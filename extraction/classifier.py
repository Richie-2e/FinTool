"""
classifier.py
Stage 2 — Page classification and table tagging.
Keyword-based; no ML required.

Financial statement detection uses two independent filters, both must pass:
  1. Title check       — a statement keyword must appear on a heading-shaped
                          line anywhere on the page (see _detect_statement_title).
  2. Numeric density   — page must contain >= _MIN_FINANCIAL_NUMBERS financial-
                         looking numbers (Indian comma-grouped, e.g. 68,468, or
                         bare 2-decimal values, e.g. 805.56; percentages excluded).

Narrative sections (notes, mda) are detected on the full page body without
a numeric density requirement, since accounting-policy pages have no tables.

Classified statement pages also receive a deterministic `score` (see
_score_statement_page) used by extraction.llm.page_selector to pick the best
page per statement type when more than one page qualifies.

This design mirrors the PoC classifier (find_statement_pages in
llm_poc_experiment.py) which achieved zero false positives on OFSS FY2024-25
by using the same two-filter approach.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from extraction.parser import DoclingTable


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class PageClassification:
    page_no: int        # 1-indexed
    statement_type: str # "balance_sheet"|"income_statement"|"cash_flow"|"notes"|"mda"|"other"
    section_type: str   # "standalone"|"consolidated"|"unknown"
    score: float = 0.0  # page-selection ranking signal (0.0 for non-statement pages)


# ---------------------------------------------------------------------------
# Filter constants — numeric density (Issue 1)
# ---------------------------------------------------------------------------

# A genuine financial statement page contains dense numeric content. This
# pattern matches two forms financial values take in Indian annual reports:
#   1. Comma-grouped values, optionally with decimals: 68,468  or  22,706.17
#   2. Bare 2-decimal values with no comma (small figures, e.g. 805.56) —
#      needed for companies whose figures never exceed 999 and so never
#      produce a comma-grouped number. The negative lookahead on "%" excludes
#      percentages (e.g. "5.20%"), which are common in narrative/MD&A prose
#      and would otherwise be a false-positive source once bare decimals are
#      allowed.
_NUM_RE = re.compile(
    r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
    r"|"
    r"\d+\.\d{2}(?!\s*%)"
)
_MIN_FINANCIAL_NUMBERS: int = 5  # unchanged — no evidence in the benchmark that it needs to move


# ---------------------------------------------------------------------------
# Heading-shape constants (Issue 2)
# ---------------------------------------------------------------------------
# Mirrors text_chunker._looks_like_heading()'s logic. Duplicated here rather
# than imported: text_chunker.py already imports PageClassification from this
# module, so importing the other way would create a circular import. The
# predicate is small, stable, and unlikely to drift independently.

_HEADING_MAX_LEN: int = 120          # a heading is a short line, not a paragraph
_HEADING_MAX_WORDS: int = 14         # same rationale, measured in words
_HEADING_MAX_DIGIT_RATIO: float = 0.3  # more than 30% digits -> table row, not a heading

# Indian annual reports number each note ("24.", "25.", ...) on its own line
# immediately before the note's heading (e.g. "25." / "Revenue from
# Operations"). Those sub-headings can otherwise look exactly like a genuine
# statement title. Primary statement titles are always the first thing on
# their page — nothing precedes them — so excluding headings immediately
# preceded by a bare small integer cleanly filters out note sub-headings
# without excluding real titles. Range chosen to cover realistic note counts
# (verified against a 56-note document in this benchmark).
_NOTE_NUMBER_MIN: int = 1
_NOTE_NUMBER_MAX: int = 60

# If a heading-shaped line is (approximately) just the keyword itself, treat
# it as a strong title match (heading_quality=2); if the keyword appears on a
# heading-shaped line with more surrounding text, treat it as a weaker match
# (heading_quality=1). This slack (in characters) separates the two.
_TITLE_EXACT_MATCH_SLACK: int = 10


# ---------------------------------------------------------------------------
# Page-scoring constants (Issue 3)
# ---------------------------------------------------------------------------
# Weights are hand-calibrated against the 4-document benchmark, not learned.
# Each is a named constant so future recalibration doesn't require touching
# the scoring logic itself.

_WEIGHT_HEADING: float = 3.0             # strongest signal: title IS the statement name
_WEIGHT_NUMERIC_DENSITY: float = 4.0     # distinguishes a data table from a mention
_WEIGHT_TABLE_STRUCTURE: float = 2.0     # page is fragmented into label/value cells
_WEIGHT_TOTAL_ROWS: float = 3.0          # "Total X" rows are a strong statement signal
_WEIGHT_KEYWORD_CHECKLIST: float = 2.0   # page vocabulary matches the statement type
_WEIGHT_CURRENCY: float = 1.0            # minor bonus; nearly universal on real pages

_NUMERIC_DENSITY_CAP: int = 20   # numeric_count beyond this adds no more score
_TOTAL_ROWS_CAP: int = 8         # total-row count beyond this adds no more score

_CELL_LIKE_MAX_LEN: int = 20            # max chars for a line to count as "cell-like"
_CELL_LIKE_MIN_NONALPHA_RATIO: float = 0.5  # majority non-alpha chars -> looks like a value cell

_TOTAL_ROW_RE = re.compile(r"^\s*total\b", re.IGNORECASE)
_CURRENCY_RE = re.compile(r"₹|in crore|in lakh|rs\.|inr", re.IGNORECASE)

# Per-statement-type vocabulary checklist for the K (keyword) signal.
_SECONDARY_KEYWORDS: dict[str, list[str]] = {
    "balance_sheet": ["assets", "liabilities", "equity"],
    "income_statement": ["revenue", "expenses", "profit", "tax"],
    "cash_flow": ["operating activities", "investing activities", "financing activities"],
}


# ---------------------------------------------------------------------------
# Keyword maps — split by detection strategy
# ---------------------------------------------------------------------------

# Financial statement keywords: checked against heading-shaped lines only
# (see _detect_statement_title), AND only when the page meets the numeric
# density threshold.
# "assets and liabilities" removed — 0 true positives, 19 false positives on
# OFSS (appears in deferred-tax notes and segment disclosures, never as a
# standalone page title).
_FINANCIAL_KEYWORDS: list[tuple[str, list[str]]] = [
    ("balance_sheet", [
        "balance sheet",
        "statement of financial position",
    ]),
    ("income_statement", [
        "profit and loss",
        "statement of profit",
        "income statement",
        "revenue from operations",
        "statement of operations",
    ]),
    ("cash_flow", [
        "cash flow statement",
        "cash flows from operating",
        "statement of cash flow",
    ]),
]

# Narrative keywords: checked against the full page body. Accounting-policy
# and MDA pages have no dense numeric content, so no density gate is applied.
_NARRATIVE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("notes", [
        "notes to",
        "notes forming part",
        "significant accounting policies",
    ]),
    ("mda", [
        "management discussion",
        "md&a",
        "management's discussion and analysis",
    ]),
]

_CONSOLIDATED_RE = re.compile(r"\bconsolidated\b", re.IGNORECASE)
_STANDALONE_RE   = re.compile(r"\bstandalone\b",   re.IGNORECASE)


# ---------------------------------------------------------------------------
# Internal helpers — heading shape / title detection (Issue 2)
# ---------------------------------------------------------------------------

def _is_heading_shaped(line: str) -> bool:
    """Heuristic: short, starts with capital, no excessive digits."""
    s = line.strip()
    if not s or len(s) > _HEADING_MAX_LEN:
        return False
    words = s.split()
    if len(words) > _HEADING_MAX_WORDS:
        return False
    digit_count = sum(1 for c in s if c.isdigit())
    if digit_count > len(s) * _HEADING_MAX_DIGIT_RATIO:
        return False
    return s[0].isupper() or s.isupper()


def _is_note_subheading(non_blank_lines: list[str], idx: int) -> bool:
    """True if the line immediately preceding `idx` is a bare note-number
    line (e.g. '25.'), marking `idx` as a Notes sub-heading rather than a
    primary statement title."""
    if idx == 0:
        return False
    prev = non_blank_lines[idx - 1].strip().rstrip(".")
    return prev.isdigit() and _NOTE_NUMBER_MIN <= int(prev) <= _NOTE_NUMBER_MAX


_FINANCIAL_KEYWORDS_BY_TYPE: dict[str, list[str]] = dict(_FINANCIAL_KEYWORDS)


def _heading_candidates(non_blank_lines: list[str]) -> list[str]:
    """Lines that are heading-shaped and not a Notes sub-heading."""
    return [
        line for i, line in enumerate(non_blank_lines)
        if _is_heading_shaped(line) and not _is_note_subheading(non_blank_lines, i)
    ]


def _best_heading_quality(candidates: list[str], keywords: list[str]) -> int:
    """
    Best (highest) heading_quality across every heading-shaped line and every
    keyword variant for a statement type — not just the first line or first
    keyword that happens to match. This matters because keyword variants
    overlap (e.g. "profit and loss" is a substring of "statement of profit
    and loss", which also separately matches the more specific "statement of
    profit"); stopping at the first match can lock in a weaker score than a
    more specific keyword on the very same line would give.
    """
    best = 0
    for line in candidates:
        low = line.strip().lower()
        for kw in keywords:
            if kw in low:
                extra = len(low) - len(kw)
                hq = 2 if extra <= _TITLE_EXACT_MATCH_SLACK else 1
                best = max(best, hq)
    return best


def _detect_statement_title(non_blank_lines: list[str]) -> tuple[str, int]:
    """
    Scan every line on the page for a financial-statement keyword — not
    restricted to a fixed leading window, since PDF text-extraction order
    does not always match visual top-to-bottom order (confirmed: a genuine
    statement title can appear well past the first several lines).

    Two passes:
      1. Keyword on a heading-shaped line, excluding Notes sub-headings —
         strong match. Once the statement type is identified this way, every
         heading-shaped line and keyword variant is checked to find the best
         possible heading_quality for that type (see _best_heading_quality).
      2. Fallback: keyword found anywhere else on the page — weak match
         (heading_quality 0). Kept so pages whose title doesn't render as a
         clean standalone line are still classified, preserving prior recall.

    Returns (statement_type, heading_quality); ("other", 0) if no match.
    """
    candidates = _heading_candidates(non_blank_lines)

    stmt_type: str | None = None
    for line in candidates:
        low = line.strip().lower()
        for candidate_type, keywords in _FINANCIAL_KEYWORDS:
            if any(kw in low for kw in keywords):
                stmt_type = candidate_type
                break
        if stmt_type:
            break

    if stmt_type is not None:
        heading_quality = _best_heading_quality(candidates, _FINANCIAL_KEYWORDS_BY_TYPE[stmt_type])
        return stmt_type, heading_quality

    for line in non_blank_lines:
        low = line.strip().lower()
        for candidate_type, keywords in _FINANCIAL_KEYWORDS:
            if any(kw in low for kw in keywords):
                return candidate_type, 0

    return "other", 0


def _classify_narrative_type(body_lower: str) -> str:
    """Match narrative section keywords against the full page body."""
    for narr_type, keywords in _NARRATIVE_KEYWORDS:
        for kw in keywords:
            if kw in body_lower:
                return narr_type
    return "other"


def _classify_section_type(page_text: str) -> str:
    """Check only the first 3 lines for standalone/consolidated header."""
    first_lines = "\n".join(page_text.splitlines()[:3])
    if _CONSOLIDATED_RE.search(first_lines):
        return "consolidated"
    if _STANDALONE_RE.search(first_lines):
        return "standalone"
    return "unknown"


# ---------------------------------------------------------------------------
# Internal helpers — page scoring (Issue 3)
# ---------------------------------------------------------------------------

def _is_cell_like(line: str) -> bool:
    """A short line that's mostly digits/currency/punctuation — how PyMuPDF
    renders an individual table cell (label and value on separate lines)."""
    s = line.strip()
    if not s or len(s) > _CELL_LIKE_MAX_LEN:
        return False
    non_alpha = sum(1 for c in s if not c.isalpha())
    return non_alpha >= len(s) * _CELL_LIKE_MIN_NONALPHA_RATIO and any(c.isdigit() for c in s)


def _score_statement_page(
    page_text: str,
    non_blank_lines: list[str],
    stmt_type: str,
    heading_quality: int,
    numeric_count: int,
) -> float:
    """
    Deterministic score combining six signals, used by page_selector to pick
    the best page when multiple pages qualify for the same statement type.

      H — heading_quality (0/1/2): is the title match strong, weak, or absent.
      N — numeric density: how much this page looks like a data table vs a
          passing mention (capped so one very dense page doesn't dominate).
      T — table structure: fraction of lines that look like individual table
          cells, independent of raw number count.
      R — "Total" row count: real statements are dense with "Total X" rows;
          narrative pages essentially never have this pattern. The single
          strongest discriminator against false positives found in practice.
      K — statement-specific vocabulary checklist (assets/liabilities/equity
          for balance sheets, etc.) — confirms broad topical match.
      C — currency-unit marker ("₹ in crore" etc.) — minor bonus.
    """
    N = min(numeric_count, _NUMERIC_DENSITY_CAP) / _NUMERIC_DENSITY_CAP

    cell_lines = sum(1 for l in non_blank_lines if _is_cell_like(l))
    T = cell_lines / max(1, len(non_blank_lines))

    total_rows = sum(1 for l in non_blank_lines if _TOTAL_ROW_RE.match(l))
    R = min(total_rows, _TOTAL_ROWS_CAP) / _TOTAL_ROWS_CAP

    body_lower = page_text.lower()
    checklist = _SECONDARY_KEYWORDS[stmt_type]
    K = sum(1 for kw in checklist if kw in body_lower) / len(checklist)

    C = 1.0 if _CURRENCY_RE.search(page_text) else 0.0

    return (
        _WEIGHT_HEADING * heading_quality
        + _WEIGHT_NUMERIC_DENSITY * N
        + _WEIGHT_TABLE_STRUCTURE * T
        + _WEIGHT_TOTAL_ROWS * R
        + _WEIGHT_KEYWORD_CHECKLIST * K
        + _WEIGHT_CURRENCY * C
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_statement_pages(pages_raw_text: list[str]) -> list[PageClassification]:
    """
    Returns one PageClassification per page for all pages (including 'other').
    page_no is 1-indexed to match DoclingTable.page_no.

    Financial statement pages (balance_sheet, income_statement, cash_flow) are
    detected using two independent filters that must both pass:
      1. A statement keyword appears on a heading-shaped line anywhere on the
         page (see _detect_statement_title).
      2. The page contains >= _MIN_FINANCIAL_NUMBERS financial-looking numbers.

    Qualifying statement pages also get a deterministic `score` (see
    _score_statement_page), used downstream to pick the best page per
    statement type when several pages qualify.

    Narrative pages (notes, mda) are detected on the full body text with no
    numeric density requirement.
    """
    result: list[PageClassification] = []
    for idx, page_text in enumerate(pages_raw_text):
        page_no = idx + 1
        non_blank = [l for l in page_text.splitlines() if l.strip()]
        body_lower = page_text.lower()
        numeric_count = len(_NUM_RE.findall(page_text))

        stmt_type = "other"
        score = 0.0

        if numeric_count >= _MIN_FINANCIAL_NUMBERS:
            stmt_type, heading_quality = _detect_statement_title(non_blank)
            if stmt_type != "other":
                score = _score_statement_page(
                    page_text, non_blank, stmt_type, heading_quality, numeric_count
                )

        if stmt_type == "other":
            stmt_type = _classify_narrative_type(body_lower)

        section_type = _classify_section_type(page_text)
        result.append(PageClassification(
            page_no=page_no,
            statement_type=stmt_type,
            section_type=section_type,
            score=score,
        ))
    return result


def tag_tables_with_classification(
    tables: list["DoclingTable"],
    page_classes: list[PageClassification],
) -> list["DoclingTable"]:
    """
    Attaches statement_type from page_classes to each DoclingTable in-place
    by matching table.page_no to the classification list.
    Also upgrades section_hint if the classification has a non-unknown section_type.
    Returns the same list.
    """
    page_map: dict[int, PageClassification] = {c.page_no: c for c in page_classes}
    for table in tables:
        pc = page_map.get(table.page_no)
        if pc is None:
            continue
        table.statement_type = pc.statement_type
        # Upgrade section_hint if we have a stronger signal from the page header
        if pc.section_type != "unknown" and table.section_hint == "unknown":
            table.section_hint = pc.section_type
    return tables
