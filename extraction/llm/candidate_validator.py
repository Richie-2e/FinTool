"""
extraction/llm/candidate_validator.py
Deterministic, non-LLM post-extraction validation for LLM-based metric candidates.

Stage 1: normalization utilities and evidence-fragment parsing.
Stage 2: ordered-subsequence fragment search -- the core of L4 (evidence
grounding).
Stage 3: grounded-region construction and the public check_evidence_grounding()
entry point, completing L4.
Stage 4: value grounding (L5) -- check_value_grounding(). Reuses
metric_extractor's _NUMBER_RE / parse_value() rather than duplicating number
parsing. Does not modify L4; searches a minimal, explicitly-bounded
extension past the L4-defined grounded_region rather than changing L4's own
boundary definition.
Stage 4.5: label verbatim-fidelity grounding (L3) -- check_label_grounding().
Reuses L4's own fragment-split/classify/ordered-search machinery to verify
that raw_label itself (not just evidence) is genuine source text. Closes a
confirmed gap: L2 only checks raw_label MAPS to the claimed metric_name, and
L4 grounds evidence, a separate field -- neither verifies raw_label is
verbatim-present in the source, which let a fabricated-but-internally-
consistent raw_label (Jio Financial Services' "Current assets", which never
appears on the source page; the real line is "Total Non-financial assets")
pass every existing check.
Stage 5: orchestration -- validate_candidates(), the single entry point
llm_extractor.py calls. Evaluates L2 (raw-label mapping, via
canonical_metrics.match_metric()), L3 (raw-label grounding), L4 (evidence
grounding), and L5 (value grounding) for every candidate, unconditionally
(no short-circuit), so rejected candidates always carry complete per-check
diagnostics.

No Ollama or network dependency; deterministic; stdlib-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from extraction.canonical_metrics import match_metric
from extraction.metric_extractor import CandidateMetric

from extraction.metric_extractor import _NUMBER_RE, parse_value

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

# Matches a literal two-character backslash-n sequence (observed in real LLM
# output, where the model emits "\n" as literal text rather than a properly
# JSON-decoded newline), plus genuine newline variants. Used both to split
# evidence into fragments and, collapsed to a space, to normalize text for
# comparison.
_NEWLINE_RE = re.compile(r"\r\n|\r|\n|\\n")

# Strips a thousands-separator comma that sits directly between two digits
# (e.g. "2,509.44" -> "2509.44") without touching commas elsewhere (e.g.
# "Property, Plant and Equipment" is left unchanged).
_DIGIT_COMMA_RE = re.compile(r"(?<=\d),(?=\d)")

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_for_grounding(s: str) -> str:
    """
    Normalize text for evidence-grounding comparisons.

    Applied identically to both evidence fragments and the source text_block
    so comparisons operate on the same representation. Deliberately
    conservative: only whitespace/newline collapsing, case-folding, and
    digit-adjacent comma stripping. Does not strip other punctuation or
    words -- aggressive normalization risks collapsing genuinely distinct
    line items into the same string.
    """
    s = _NEWLINE_RE.sub(" ", s)
    s = s.lower()
    s = _DIGIT_COMMA_RE.sub("", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


# ---------------------------------------------------------------------------
# Evidence layout normalization
#
# Fixes a specific, benchmark-confirmed defect: for cash_flow candidates,
# the LLM sometimes emits `evidence` as a single line fusing the label and
# its trailing value(s) with a colon or plain whitespace (e.g.
# "Net cash from/(used in) operating activities: 23,879.91") instead of the
# newline-separated, multi-fragment layout it reliably produces for
# balance_sheet/income_statement. Because the fused string then classifies
# as one "text" fragment, L4's ordered search requires the ENTIRE string --
# including the trailing number, in whatever exact formatting the model
# chose -- to match verbatim in the source text_block, which is far more
# brittle than L5's own tolerant, source-derived value check. Splitting the
# label from its trailing value restores the intended division of labor:
# L4 verifies the label, L5 (independently, from the real text_block) finds
# the value. See the frozen design review for the full rationale,
# including why the decimal-or-comma safeguard below is not loosened to a
# bare digit-count rule, and why a hyphenated fiscal-year range (e.g.
# "2023-24") or a glued reference code (e.g. "F95") must never be
# misread as -- or absorbed into -- the trailing value.
# ---------------------------------------------------------------------------

# The gap that may sit between two adjacent numeric tokens already
# identified as part of the same trailing run (e.g. the ", " in
# "(2,314.03), 27,324.93", or the plain " " in "5,569.74 7,212.64").
# Deliberately narrow: this only judges the gap BETWEEN two already-matched
# _NUMBER_RE tokens, never characters inside a number.
_RUN_GAP_RE = re.compile(r"[,\s]+")


def _looks_like_financial_figure(token: str) -> bool:
    """
    True if `token` (a _NUMBER_RE match) contains a decimal point or a
    thousands-comma. Every real fused trailing value observed in the
    benchmark corpus (23,879.91; (2,314.03); 27,324.93; 9,280.88; 5,569.74;
    7,212.64; 73.35; 37.45; 17,413.47; 7,345.57) satisfies this; every real
    bare note/section reference observed (24, 95, (A), (1)) does not.
    Deliberately conservative and NOT loosened to a digit-count rule: an
    integer-only fused value (not observed in this corpus) would be a
    false negative, but a false negative only leaves a candidate exactly as
    rejected as it is today -- never a new false accept. A digit-count
    rule would reopen a worse false-positive risk (a bare 4-digit fiscal
    year like "2025" would qualify), per the design review.
    """
    return "." in token or "," in token


def _is_glued_to_preceding_content(s: str, start: int) -> bool:
    """
    True if treating the _NUMBER_RE match starting at `start` as a
    standalone value would be wrong because it is actually a fragment of
    something else glued directly (no separator) to what precedes it:
    a note/section reference glued to a letter (the "95" inside "F95"), or
    the second half of a hyphenated fiscal-year range glued to a digit
    (the "-24" inside "2023-24" -- a bare sign _NUMBER_RE itself would
    otherwise consume as a genuine negative sign). A token preceded by
    nothing (string start) or by a genuine separator (whitespace, comma,
    colon, '(') is never glued.
    """
    if start == 0:
        return False
    prev = s[start - 1]
    if prev.isalnum():
        return True
    if prev in "-−" and start - 1 > 0 and s[start - 2].isalnum():
        return True
    return False


def _normalize_evidence_layout(evidence: str) -> str:
    """
    Insert a single newline at a fused label/value boundary in a raw
    evidence string, so the unchanged _split_evidence_fragments /
    _classify_fragment / L4 / L5 pipeline can treat the label and its
    trailing value(s) as separate fragments -- exactly as it already does
    for evidence the LLM formats with real newlines.

    Rule: if the string ends in a run of one or more _NUMBER_RE tokens
    (adjacent through nothing but a comma/whitespace gap), at least one of
    which contains '.' or ',', preceded by text containing at least one
    letter, a single newline is inserted at that boundary. At most one
    trailing colon (plus surrounding whitespace) immediately before the
    boundary is dropped rather than preserved: confirmed against real
    source text (TataSteel p.286) that this colon is a model-inserted
    artifact, not verbatim source content -- dropping it is safe either
    way, since a fragment without it still matches as a prefix of the
    source line whether or not the source itself has a colon there.

    Never fires (returns the input unchanged) if: the boundary is already
    newline-separated (idempotent by construction); no qualifying trailing
    run exists; the run would consume the whole string; the remaining
    prefix has no letters; or a token only reaches the boundary by being
    glued to preceding letters/digits (see _is_glued_to_preceding_content).
    Never deletes or reorders any character other than the one
    narrowly-scoped colon substitution described above.
    """
    stripped = evidence.rstrip()
    if not stripped:
        return evidence

    candidates = [
        m for m in _NUMBER_RE.finditer(stripped)
        if not _is_glued_to_preceding_content(stripped, m.start())
    ]
    if not candidates or candidates[-1].end() != len(stripped):
        return evidence

    # Extend backward through remaining candidates while each pair is
    # adjacent through nothing but a comma/whitespace gap.
    run_start_idx = len(candidates) - 1
    for i in range(len(candidates) - 2, -1, -1):
        gap = stripped[candidates[i].end():candidates[run_start_idx].start()]
        if gap and _RUN_GAP_RE.fullmatch(gap):
            run_start_idx = i
        else:
            break

    run = candidates[run_start_idx:]
    if not any(_looks_like_financial_figure(m.group()) for m in run):
        return evidence

    run_start = run[0].start()
    if run_start == 0:
        return evidence

    prefix = stripped[:run_start]
    prefix_trimmed = prefix.rstrip()
    if prefix_trimmed.endswith(":"):
        prefix_trimmed = prefix_trimmed[:-1].rstrip()

    if not prefix_trimmed or not any(c.isalpha() for c in prefix_trimmed):
        return evidence

    separator_zone = stripped[len(prefix_trimmed):run_start]
    if _NEWLINE_RE.search(separator_zone):
        return evidence

    trailing_ws = evidence[len(stripped):]
    return prefix_trimmed + "\n" + stripped[run_start:] + trailing_ws


# ---------------------------------------------------------------------------
# Evidence-fragment parsing
# ---------------------------------------------------------------------------

def _split_evidence_fragments(evidence: str) -> list[str]:
    """
    Split a raw evidence string into line-fragments.

    Splits on the same newline variants _normalize_for_grounding collapses
    (including the literal backslash-n artifact), BEFORE normalization --
    normalization would otherwise destroy the split points by collapsing
    them to spaces first. Each fragment is stripped; empty fragments are
    dropped.
    """
    fragments = _NEWLINE_RE.split(evidence)
    return [f.strip() for f in fragments if f.strip()]


# A fragment, after normalization, that contains only digits and common
# numeric punctuation (parentheses/minus for negatives, comma, decimal
# point, percent sign, whitespace) and at least one digit.
_NUMERIC_FRAGMENT_RE = re.compile(r"^[()\-−\d,.\s%]+$")


def _classify_fragment(fragment: str) -> Literal["text", "numeric", "junk"]:
    """
    Classify an evidence fragment as "text", "numeric", or "junk".

    Only "text" fragments participate in the ordered-subsequence search
    (added in a later stage) -- a short numeric fragment (e.g. a note
    reference number) must not be allowed to anchor/advance the search
    cursor, since it can match spuriously early in the page and block a
    later, more distinctive text fragment from being found in its correct
    position. Numeric fragments are grounded later via value-in-region
    checks (L5), not via this ordered search.
    """
    normalized = _normalize_for_grounding(fragment)
    if not normalized:
        return "junk"
    if _NUMERIC_FRAGMENT_RE.match(normalized) and any(c.isdigit() for c in normalized):
        return "numeric"
    if any(c.isalpha() for c in normalized):
        return "text"
    return "junk"


# ---------------------------------------------------------------------------
# Ordered-subsequence fragment search
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FragmentMatch:
    """One text fragment's matched position within a normalized text_block."""
    fragment: str   # original (unnormalized) fragment text
    start: int      # start index in normalized_text_block
    end: int        # end index in normalized_text_block (exclusive)


@dataclass(frozen=True)
class _OrderedSearchResult:
    """
    Result of an ordered-subsequence search over a normalized text_block.

    succeeded is True only if every fragment in the input list was found,
    in order, with no backward jumps. On failure, `matches` holds whatever
    fragments matched before the failure (possibly empty), and
    `failed_fragment` names the fragment that could not be found from the
    current cursor position onward -- or is None if the input fragment list
    was empty to begin with (a distinct case from "a fragment failed").
    """
    succeeded: bool
    matches: list[_FragmentMatch] = field(default_factory=list)
    failed_fragment: Optional[str] = None


def _is_word_boundary_char(ch: Optional[str]) -> bool:
    """
    True if `ch` is a valid word-boundary character: absent (string start/
    end) or neither alphanumeric nor a hyphen. Hyphen is deliberately
    excluded from "boundary-safe" (unlike a standard regex \\b, which would
    treat it as one) because the real collision this guards against is a
    hyphenated compound like "non-current assets" containing "current
    assets" as a substring -- the character before that inner match is '-',
    which a plain alnum/non-alnum check alone would wrongly accept as a
    boundary.
    """
    return ch is None or not (ch.isalnum() or ch == "-")


def _ordered_fragment_search(
    text_fragments: list[str],
    normalized_text_block: str,
) -> _OrderedSearchResult:
    """
    Search for `text_fragments` (raw, unnormalized "text"-classified
    fragments, in their original evidence order) within an already-normalized
    `normalized_text_block`, requiring each fragment to be found at or after
    the position the previous one ended at, AND to occur at a genuine word
    boundary (see _is_word_boundary_char) -- not merely as a substring
    inside a larger word or hyphenated compound (RC-1: "current assets"
    matching inside "non-current assets" would otherwise anchor evidence at
    the wrong location; confirmed on real OFSS and Reliance balance sheets).
    A candidate match that fails the boundary check is not treated as a
    failure of the whole search -- the search resumes one character later,
    looking for a later, valid occurrence of the same fragment.

    This is deliberately NOT a contiguous/windowed match: gaps between
    fragments are unbounded (the frozen L4 design explicitly drops a hard
    locality constraint -- see the LICHSGFIN current_assets case, a genuine,
    correct extraction with a 410-character gap between its two evidence
    fragments). What is enforced is ORDER: a fragment can only match at or
    after the previous fragment's end, never before it. This is what would
    catch a model quoting fragments out of their true reading order.

    `normalized_text_block` is expected to already be normalized by the
    caller (via _normalize_for_grounding), once per batch of candidates that
    share the same source page(s) -- not re-normalized here on every call,
    per the frozen design's performance note.

    Each `fragment` in `text_fragments` is normalized internally before
    searching, reusing _normalize_for_grounding (Stage 1) rather than
    duplicating normalization logic.
    """
    cursor = 0
    matches: list[_FragmentMatch] = []

    for fragment in text_fragments:
        norm_fragment = _normalize_for_grounding(fragment)
        if not norm_fragment:
            # Defensive: _classify_fragment should already exclude fragments
            # that normalize to empty; skip rather than fail on one.
            continue

        search_from = cursor
        idx = None
        while True:
            candidate_idx = normalized_text_block.find(norm_fragment, search_from)
            if candidate_idx == -1:
                break
            end = candidate_idx + len(norm_fragment)
            before = normalized_text_block[candidate_idx - 1] if candidate_idx > 0 else None
            after = normalized_text_block[end] if end < len(normalized_text_block) else None
            if _is_word_boundary_char(before) and _is_word_boundary_char(after):
                idx = candidate_idx
                break
            search_from = candidate_idx + 1

        if idx is None:
            return _OrderedSearchResult(
                succeeded=False,
                matches=matches,
                failed_fragment=fragment,
            )

        end = idx + len(norm_fragment)
        matches.append(_FragmentMatch(fragment=fragment, start=idx, end=end))
        cursor = end

    if not matches:
        # Empty input, or every fragment normalized to empty -- no anchor
        # was ever established. Distinct from "a specific fragment failed".
        return _OrderedSearchResult(succeeded=False, matches=[], failed_fragment=None)

    return _OrderedSearchResult(succeeded=True, matches=matches, failed_fragment=None)


# ---------------------------------------------------------------------------
# Grounded-region construction and the public L4 entry point
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceGroundingResult:
    """Result of the public L4 check, check_evidence_grounding()."""
    passed: bool
    grounded_region: Optional[str]      # normalized substring of text_block spanning matched text fragments
    matched_fragments: list[str] = field(default_factory=list)   # text fragments matched, in order
    failed_fragment: Optional[str] = None                        # first text fragment that failed to match, if any
    reason: str = ""


def _construct_grounded_region(
    search_result: _OrderedSearchResult,
    normalized_text_block: str,
) -> Optional[str]:
    """
    Build the grounded region from an already-completed _OrderedSearchResult.

    Reuses the search result's match positions directly -- does NOT
    re-search or recompute fragment positions. The region spans from the
    start of the FIRST matched fragment to the end of the LAST matched
    fragment. Per the frozen design, this span is intentionally not bounded
    by any separate locality constraint: ordering (already enforced by
    _ordered_fragment_search) is what keeps this a plausible reading-order
    span of the page, not a fixed character/line window.

    Returns None if the search did not succeed (nothing to construct).
    """
    if not search_result.succeeded or not search_result.matches:
        return None
    start = search_result.matches[0].start
    end = search_result.matches[-1].end
    return normalized_text_block[start:end]


def check_evidence_grounding(evidence: str, text_block: str) -> EvidenceGroundingResult:
    """
    Public L4 entry point: is `evidence` genuinely grounded in `text_block`?

    Orchestrates, within L4 only (no L5, no multi-candidate/L2 orchestration
    -- that is a later stage):
      1. Normalize a fused label/value layout, if present (
         _normalize_evidence_layout), then split `evidence` into fragments
         (Stage 1: _split_evidence_fragments).
      2. Classify each fragment; keep only "text" fragments as the ordered-
         search anchors (Stage 1: _classify_fragment). Numeric fragments are
         not required to match here -- grounding the candidate's declared
         value is L5's job, scoped to the region this function returns.
      3. Normalize `text_block` once (Stage 1: _normalize_for_grounding).
      4. Run the ordered-subsequence search (Stage 2: _ordered_fragment_search).
      5. Construct the grounded region from the search result (Stage 3:
         _construct_grounded_region).

    Parameters
    ----------
    evidence : str
        The candidate's raw evidence string (CandidateMetric.evidence).
    text_block : str
        The same page text passed to the LLM for this call, unnormalized.
        Callers validating multiple candidates from the same call should
        still only normalize it once per batch by calling
        _normalize_for_grounding() themselves and reusing the result --
        this function normalizes it internally for a single-candidate call,
        matching the multi-candidate reuse pattern is an orchestration-stage
        concern, not this function's.

    Returns
    -------
    EvidenceGroundingResult
    """
    fragments = _split_evidence_fragments(_normalize_evidence_layout(evidence))
    text_fragments = [f for f in fragments if _classify_fragment(f) == "text"]

    normalized_text_block = _normalize_for_grounding(text_block)
    search_result = _ordered_fragment_search(text_fragments, normalized_text_block)

    if not search_result.succeeded:
        reason = (
            f"fragment_not_found_in_order: {search_result.failed_fragment!r}"
            if search_result.failed_fragment is not None
            else "no_text_anchor_in_evidence"
        )
        return EvidenceGroundingResult(
            passed=False,
            grounded_region=None,
            matched_fragments=[m.fragment for m in search_result.matches],
            failed_fragment=search_result.failed_fragment,
            reason=reason,
        )

    grounded_region = _construct_grounded_region(search_result, normalized_text_block)
    return EvidenceGroundingResult(
        passed=True,
        grounded_region=grounded_region,
        matched_fragments=[m.fragment for m in search_result.matches],
        failed_fragment=None,
        reason="grounded",
    )


# ---------------------------------------------------------------------------
# Label verbatim-fidelity grounding (L3)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LabelGroundingResult:
    """Result of the public L3 check, check_label_grounding()."""
    passed: bool
    reason: str = ""


def check_label_grounding(raw_label: str, text_block: str) -> LabelGroundingResult:
    """
    Public L3 entry point: does `raw_label` itself appear verbatim in
    `text_block`?

    Reuses the exact L4 pipeline stages (fragment split -> text-fragment
    classification -> ordered-subsequence search over the normalized
    text_block) rather than a separate matching strategy, so it inherits
    L4's word-boundary guarantee for free -- "current assets" cannot
    falsely anchor inside "non-current assets" here either (see RC-1 in
    _ordered_fragment_search's docstring). `_normalize_evidence_layout` is
    NOT applied: that step exists solely to un-fuse a label glued to its
    own trailing value in an `evidence` string (a cash_flow-specific
    pattern); raw_label is a label-only field with no such pattern to
    undo.

    A raw_label that yields no "text"-classified fragment at all (empty,
    or purely numeric) cannot establish any anchor and is rejected --
    consistent with L4's own no_text_anchor_in_evidence case for an
    equivalent input.

    Parameters
    ----------
    raw_label : str
        The candidate's declared label (CandidateMetric.raw_label).
    text_block : str
        The same page text passed to the LLM for this call (identical
        input check_evidence_grounding searches), unnormalized.

    Returns
    -------
    LabelGroundingResult
    """
    fragments = _split_evidence_fragments(raw_label)
    text_fragments = [f for f in fragments if _classify_fragment(f) == "text"]

    normalized_text_block = _normalize_for_grounding(text_block)
    search_result = _ordered_fragment_search(text_fragments, normalized_text_block)

    if not search_result.succeeded:
        reason = (
            f"fragment_not_found_in_order: {search_result.failed_fragment!r}"
            if search_result.failed_fragment is not None
            else "no_text_anchor_in_raw_label"
        )
        return LabelGroundingResult(passed=False, reason=reason)

    return LabelGroundingResult(passed=True, reason="grounded")


# ---------------------------------------------------------------------------
# Value grounding (L5)
# ---------------------------------------------------------------------------

# L4's grounded_region ends at the last matched TEXT fragment (by design --
# see check_evidence_grounding). Real benchmark data shows the declared
# value normally starts just 1 character past that boundary (a single
# collapsed whitespace/newline): confirmed on LICHSGFIN current_assets,
# LICHSGFIN total_equity, and JioFin total_assets. A case with an
# intervening note-reference number and page cross-reference (TataSteel
# revenue: "Revenue from operations \n24\nF94\n1,32,516.66") measured an
# 8-character gap. This constant covers that worst observed case with
# margin, while staying far short of the 300+ character gaps measured
# between genuinely DIFFERENT line items on the same page (the LICHSGFIN
# current_assets non-contiguous fragment gap itself) -- i.e. wide enough
# for "the next number after this label", nowhere near wide enough to
# reach an unrelated row. This does not change L4's own grounded_region
# definition; it is an additional, minimal search-window extension local
# to this function only.
_VALUE_SEARCH_TRAILING_EXTENSION = 40

# Relative-error tolerance for a numeric match, matching the tolerance used
# during the earlier L2/L4/L5 design-review audit.
_VALUE_RELATIVE_TOLERANCE = 0.02


@dataclass(frozen=True)
class ValueGroundingResult:
    """Result of the public L5 check, check_value_grounding()."""
    passed: bool
    degraded: bool                       # True if L4 had failed and this fell back to the full text_block
    matched_number: Optional[float] = None
    sign_mismatch: bool = False          # True if magnitude matched but sign did not
    reason: str = ""


def _numbers_in(text: str) -> list[float]:
    """
    Extract and parse every number-shaped substring of `text`.

    Reuses metric_extractor's existing _NUMBER_RE / parse_value() rather
    than duplicating number-parsing logic, per the frozen design.
    """
    numbers: list[float] = []
    for m in _NUMBER_RE.finditer(text):
        parsed = parse_value(m.group())
        if parsed is not None:
            numbers.append(parsed)
    return numbers


def _evidence_self_confirms_value(evidence: str, value: float) -> bool:
    """
    FV2-2: True if `evidence` (the candidate's full raw evidence string, not
    just its "text"-classified fragments) itself contains a number matching
    `value` (exact or within _VALUE_RELATIVE_TOLERANCE).

    This is deliberately checked against the model's OWN reported evidence,
    not the source text_block -- it answers "did the model's own quote
    already agree with what it declared?", which is a different and
    narrower question than "does this value appear anywhere on the page?".
    A "numeric" fragment in evidence is excluded from L4's ordered
    text-fragment search (see _classify_fragment) but is not discarded here;
    reusing it is what lets FV2-2 avoid guessing a wider fixed window.

    Measured live against real benchmark data (see FRAMEWORK_V2_IMPLEMENTATION_PLAN.md's
    FV2-2 entry): every genuine window-blocked OFSS candidate
    (current_assets, current_liabilities, long_term_debt) has its declared
    value quoted verbatim in its own evidence; every known fabrication
    (LICHSGFIN's cross-row total_liabilities, OFSS's hallucinated
    gross_profit) does not. This is the gate that lets check_value_grounding
    search further without needing an arbitrarily larger fixed constant.
    """
    for n in _numbers_in(_normalize_for_grounding(evidence)):
        if n == value or (n != 0 and abs(n - value) / abs(n) < _VALUE_RELATIVE_TOLERANCE):
            return True
    return False


def check_value_grounding(
    value: float,
    grounded_region: Optional[str],
    text_block: str,
    evidence: Optional[str] = None,
) -> ValueGroundingResult:
    """
    Public L5 entry point: is `value` grounded near the L4-verified region?

    If `grounded_region` is provided (L4 passed), the search space is the
    region itself plus a trailing extension into the surrounding normalized
    text_block. That extension is _VALUE_SEARCH_TRAILING_EXTENSION by
    default -- calibrated against real benchmark data to cover "the next
    number after the last grounded label" without reaching into an
    unrelated row -- UNLESS `evidence` is supplied and
    _evidence_self_confirms_value(evidence, value) is True, in which case
    the search space extends all the way to the end of the (already
    length-capped, per-call) normalized text_block instead (FV2-2). A flat
    wider constant was measured to be unsafe: OFSS's current_liabilities
    needs a 339-character window, but LICHSGFIN's frozen cross-row
    total_liabilities fabrication starts incorrectly passing at 349 --
    a 10-character margin, too fragile to be a real fix. Gating the wider
    search on the model's own evidence already agreeing with its declared
    value avoids picking any such constant at all: fabricated candidates
    generally don't self-confirm (their own quoted number doesn't match
    what they declared either), so they never reach the wider search.
    L4's own grounded_region definition is not changed by any of this --
    the extension is local to this function's own search window only.

    If `grounded_region` is None (L4 failed), falls back to searching the
    full normalized text_block, for diagnostic purposes only
    (`degraded=True`). Callers must NOT treat a degraded pass as
    acceptance -- only a non-degraded pass should count; that decision
    belongs to the orchestrator (a later stage), not this function.

    Parameters
    ----------
    value : float
        The candidate's declared value (CandidateMetric.value).
    grounded_region : str or None
        EvidenceGroundingResult.grounded_region from check_evidence_grounding(),
        or None if L4 failed.
    text_block : str
        The same page text passed to the LLM for this call, unnormalized.
    evidence : str or None
        The candidate's full raw evidence string (CandidateMetric.evidence).
        Optional and defaults to None for backward compatibility with
        existing callers/tests; when omitted, behavior is identical to
        before FV2-2 (fixed _VALUE_SEARCH_TRAILING_EXTENSION window only).

    Returns
    -------
    ValueGroundingResult
    """
    if grounded_region is None:
        search_space = _normalize_for_grounding(text_block)
        degraded = True
    else:
        normalized_text_block = _normalize_for_grounding(text_block)
        region_pos = normalized_text_block.find(grounded_region)
        if region_pos == -1:
            # Defensive: grounded_region was derived from this same
            # text_block by L4 via a substring slice, so this should not
            # happen in practice. Fall back to the region alone (no
            # extension possible without knowing where it sits).
            search_space = grounded_region
        else:
            region_end = region_pos + len(grounded_region)
            if evidence and _evidence_self_confirms_value(evidence, value):
                extension_end = len(normalized_text_block)
            else:
                extension_end = region_end + _VALUE_SEARCH_TRAILING_EXTENSION
            search_space = normalized_text_block[region_pos:extension_end]
        degraded = False

    candidates = _numbers_in(search_space)

    for n in candidates:
        if n == value or (n != 0 and abs(n - value) / abs(n) < _VALUE_RELATIVE_TOLERANCE):
            return ValueGroundingResult(
                passed=True, degraded=degraded, matched_number=n, reason="value_grounded"
            )

    for n in candidates:
        if n != 0 and abs(n) == abs(value):
            return ValueGroundingResult(
                passed=False, degraded=degraded, matched_number=n,
                sign_mismatch=True, reason="sign_mismatch",
            )

    return ValueGroundingResult(
        passed=False, degraded=degraded, matched_number=None, reason="value_not_grounded"
    )


# ---------------------------------------------------------------------------
# Orchestration -- L2 + L4 + L5 over a batch of candidates
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RejectionRecord:
    """
    Per-candidate diagnostic record, produced for every candidate
    (accepted or not) so rejected ones carry complete per-check results,
    not just the first failure. Field set matches the frozen
    validator_rejections.csv column contract agreed earlier
    (doc_id, statement_type, metric_name, raw_label, value, unit, year,
    evidence, rejection_reason) plus per-check pass/fail booleans for
    finer-grained debugging. l3_passed was added when L3 (raw-label
    verbatim-fidelity grounding) was introduced; it is additive to the
    existing column contract, not a replacement for any existing field.
    """
    doc_id: str
    statement_type: str
    metric_name: str
    raw_label: str
    value: float
    unit: str
    year: Optional[int]
    evidence: str
    l2_passed: bool
    l3_passed: bool          # False if raw_label is not verbatim-findable in text_block
    l4_passed: bool
    l5_passed: bool          # False if L5 failed OR was degraded; a degraded pass never counts
    l5_degraded: bool        # True iff L4 failed and L5 fell back to a full-text_block diagnostic check
    rejection_reasons: list[str] = field(default_factory=list)


def validate_candidates(
    candidates: list[CandidateMetric],
    text_block: str,
) -> tuple[list[CandidateMetric], list[RejectionRecord]]:
    """
    Orchestrate L2 (raw-label mapping), L3 (raw-label grounding), L4
    (evidence grounding), and L5 (value grounding) over a batch of
    candidates that share the same source text_block (i.e. one call per
    extract_with_llm() statement-type loop iteration, mirroring how
    call_ollama()/parse_response() are already invoked once per statement
    type).

    Each candidate is evaluated against all four checks unconditionally --
    no short-circuit on an earlier failure -- so every RejectionRecord
    carries complete diagnostics, not just the first failure reason. This
    was essential during the earlier benchmark investigation (distinguishing
    e.g. "hint-echoed raw_label but genuinely correct value" from "wrong on
    every axis") and is preserved here for the same reason.

    L2 reuses canonical_metrics.match_metric() directly, per the reuse-first
    decision made when L2 was designed -- no separate raw-label-matching
    function exists or is duplicated here. L3, L4, and L5 reuse their own
    public entry points (check_label_grounding, check_evidence_grounding,
    check_value_grounding) exactly as implemented in Stages 4.5, 3, and 4;
    none of their behavior is touched by this function.

    A candidate is accepted only if:
        l2_passed AND l3_result.passed AND l4_result.passed AND
        l5_result.passed AND NOT l5_result.degraded
    (a degraded L5 result means L4 failed and L5 fell back to a full-page
    diagnostic-only check -- it never counts toward acceptance, per
    check_value_grounding's own contract from Stage 4).

    Parameters
    ----------
    candidates : list[CandidateMetric]
        Candidates from a single extract_with_llm() statement-type call
        (i.e. sharing one text_block).
    text_block : str
        The same page text passed to the LLM for this call.

    Returns
    -------
    (accepted, diagnostics)
        accepted    : list[CandidateMetric] -- only candidates passing all
                      three checks, in their original relative order.
        diagnostics : list[RejectionRecord] -- one entry per input
                      candidate, accepted or not (accepted candidates have
                      rejection_reasons=[]).
    """
    accepted: list[CandidateMetric] = []
    diagnostics: list[RejectionRecord] = []

    for candidate in candidates:
        l2_passed = match_metric(candidate.raw_label, candidate.statement_type) == candidate.metric_name

        l3_result = check_label_grounding(candidate.raw_label, text_block)

        l4_result = check_evidence_grounding(candidate.evidence, text_block)
        l5_result = check_value_grounding(candidate.value, l4_result.grounded_region, text_block, candidate.evidence)

        l5_effective_passed = l5_result.passed and not l5_result.degraded

        reasons: list[str] = []
        if not l2_passed:
            reasons.append("raw_label_mismatch")
        if not l3_result.passed:
            reasons.append(f"raw_label_not_grounded:{l3_result.reason}")
        if not l4_result.passed:
            reasons.append(f"evidence_not_grounded:{l4_result.reason}")
        elif not l5_effective_passed:
            # Only report a value-grounding reason when L4 actually passed --
            # if L4 failed, l5_degraded already explains why L5 couldn't
            # meaningfully evaluate, and the L4 reason above is the real cause.
            reasons.append(f"value_not_grounded:{l5_result.reason}")

        is_accepted = l2_passed and l3_result.passed and l4_result.passed and l5_effective_passed

        diagnostics.append(RejectionRecord(
            doc_id=candidate.doc_id,
            statement_type=candidate.statement_type,
            metric_name=candidate.metric_name,
            raw_label=candidate.raw_label,
            value=candidate.value,
            unit=candidate.unit,
            year=candidate.year,
            evidence=candidate.evidence,
            l2_passed=l2_passed,
            l3_passed=l3_result.passed,
            l4_passed=l4_result.passed,
            l5_passed=l5_effective_passed,
            l5_degraded=l5_result.degraded,
            rejection_reasons=reasons,
        ))

        if is_accepted:
            accepted.append(candidate)

    return accepted, diagnostics
