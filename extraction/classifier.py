"""
classifier.py
Stage 2 — Page classification and table tagging.
Keyword-based; no ML required.

Financial statement detection uses two independent filters, both must pass:
  1. Title-area check  — keyword must appear in the first _TITLE_LINES lines.
  2. Numeric density   — page must contain >= _MIN_FINANCIAL_NUMBERS Indian-
                         formatted numbers (e.g. 68,468 or 1,89,483).

Narrative sections (notes, mda) are detected on the full page body without
a numeric density requirement, since accounting-policy pages have no tables.

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


# ---------------------------------------------------------------------------
# Filter constants
# ---------------------------------------------------------------------------

# Only the first _TITLE_LINES lines of a page are checked for financial
# statement keywords. Lines beyond this threshold are body/prose text where
# the same words appear in accounting policy descriptions and notes.
_TITLE_LINES: int = 8

# A genuine financial statement page contains dense numeric content.
# This pattern matches Indian-formatted numbers (e.g. 68,468 or 1,89,483).
_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+")
_MIN_FINANCIAL_NUMBERS: int = 5


# ---------------------------------------------------------------------------
# Keyword maps — split by detection strategy
# ---------------------------------------------------------------------------

# Financial statement keywords: checked in title area ONLY (first _TITLE_LINES
# lines), AND only when the page meets the numeric density threshold.
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
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_financial_type(title_lower: str) -> str:
    """Match financial statement keywords against the page title area only."""
    for stmt_type, keywords in _FINANCIAL_KEYWORDS:
        for kw in keywords:
            if kw in title_lower:
                return stmt_type
    return "other"


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
# Public API
# ---------------------------------------------------------------------------

def classify_statement_pages(pages_raw_text: list[str]) -> list[PageClassification]:
    """
    Returns one PageClassification per page for all pages (including 'other').
    page_no is 1-indexed to match DoclingTable.page_no.

    Financial statement pages (balance_sheet, income_statement, cash_flow) are
    detected using two independent filters that must both pass:
      1. A statement keyword appears in the first _TITLE_LINES lines.
      2. The page contains >= _MIN_FINANCIAL_NUMBERS Indian-formatted numbers.

    Narrative pages (notes, mda) are detected on the full body text with no
    numeric density requirement.
    """
    result: list[PageClassification] = []
    for idx, page_text in enumerate(pages_raw_text):
        page_no = idx + 1
        title_area = "\n".join(page_text.splitlines()[:_TITLE_LINES]).lower()
        body_lower  = page_text.lower()
        numeric_count = len(_NUM_RE.findall(page_text))

        # Financial statement: must pass both title-area AND density check
        if numeric_count >= _MIN_FINANCIAL_NUMBERS:
            stmt_type = _classify_financial_type(title_area)
        else:
            stmt_type = "other"

        # Narrative fallback: checked on full body, no density gate
        if stmt_type == "other":
            stmt_type = _classify_narrative_type(body_lower)

        section_type = _classify_section_type(page_text)
        result.append(PageClassification(
            page_no=page_no,
            statement_type=stmt_type,
            section_type=section_type,
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
