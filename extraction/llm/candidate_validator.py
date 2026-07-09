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
Stage 5: orchestration -- validate_candidates(), the single entry point
llm_extractor.py calls. Evaluates L2 (raw-label, via
canonical_metrics.match_metric()), L4, and L5 for every candidate,
unconditionally (no short-circuit), so rejected candidates always carry
complete per-check diagnostics.

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


def _ordered_fragment_search(
    text_fragments: list[str],
    normalized_text_block: str,
) -> _OrderedSearchResult:
    """
    Search for `text_fragments` (raw, unnormalized "text"-classified
    fragments, in their original evidence order) within an already-normalized
    `normalized_text_block`, requiring each fragment to be found at or after
    the position the previous one ended at.

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

        idx = normalized_text_block.find(norm_fragment, cursor)
        if idx == -1:
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
      1. Split `evidence` into fragments (Stage 1: _split_evidence_fragments).
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
    fragments = _split_evidence_fragments(evidence)
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


def check_value_grounding(
    value: float,
    grounded_region: Optional[str],
    text_block: str,
) -> ValueGroundingResult:
    """
    Public L5 entry point: is `value` grounded near the L4-verified region?

    If `grounded_region` is provided (L4 passed), the search space is the
    region itself plus a small, fixed trailing extension into the
    surrounding normalized text_block (_VALUE_SEARCH_TRAILING_EXTENSION),
    calibrated against real benchmark data to cover "the next number after
    the last grounded label" without reaching into an unrelated row. L4's
    own grounded_region definition is not changed by this -- the extension
    is local to this function's own search window only.

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
            extension_end = (
                region_pos + len(grounded_region) + _VALUE_SEARCH_TRAILING_EXTENSION
            )
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
    finer-grained debugging.
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
    l4_passed: bool
    l5_passed: bool          # False if L5 failed OR was degraded; a degraded pass never counts
    l5_degraded: bool        # True iff L4 failed and L5 fell back to a full-text_block diagnostic check
    rejection_reasons: list[str] = field(default_factory=list)


def validate_candidates(
    candidates: list[CandidateMetric],
    text_block: str,
) -> tuple[list[CandidateMetric], list[RejectionRecord]]:
    """
    Orchestrate L2 (raw-label), L4 (evidence grounding), and L5 (value
    grounding) over a batch of candidates that share the same source
    text_block (i.e. one call per extract_with_llm() statement-type loop
    iteration, mirroring how call_ollama()/parse_response() are already
    invoked once per statement type).

    Each candidate is evaluated against all three checks unconditionally --
    no short-circuit on an earlier failure -- so every RejectionRecord
    carries complete diagnostics, not just the first failure reason. This
    was essential during the earlier benchmark investigation (distinguishing
    e.g. "hint-echoed raw_label but genuinely correct value" from "wrong on
    every axis") and is preserved here for the same reason.

    L2 reuses canonical_metrics.match_metric() directly, per the reuse-first
    decision made when L2 was designed -- no separate raw-label-matching
    function exists or is duplicated here. L4 and L5 reuse their own public
    entry points (check_evidence_grounding, check_value_grounding) exactly
    as implemented in Stages 3 and 4; neither's behavior is touched by this
    function.

    A candidate is accepted only if:
        l2_passed AND l4_result.passed AND l5_result.passed AND NOT l5_result.degraded
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

        l4_result = check_evidence_grounding(candidate.evidence, text_block)
        l5_result = check_value_grounding(candidate.value, l4_result.grounded_region, text_block)

        l5_effective_passed = l5_result.passed and not l5_result.degraded

        reasons: list[str] = []
        if not l2_passed:
            reasons.append("raw_label_mismatch")
        if not l4_result.passed:
            reasons.append(f"evidence_not_grounded:{l4_result.reason}")
        elif not l5_effective_passed:
            # Only report a value-grounding reason when L4 actually passed --
            # if L4 failed, l5_degraded already explains why L5 couldn't
            # meaningfully evaluate, and the L4 reason above is the real cause.
            reasons.append(f"value_not_grounded:{l5_result.reason}")

        is_accepted = l2_passed and l4_result.passed and l5_effective_passed

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
            l4_passed=l4_result.passed,
            l5_passed=l5_effective_passed,
            l5_degraded=l5_result.degraded,
            rejection_reasons=reasons,
        ))

        if is_accepted:
            accepted.append(candidate)

    return accepted, diagnostics
