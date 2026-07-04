"""
classifier.py
Stage 2 — Page classification and table tagging.
Keyword-based; no ML required.
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
# Keyword maps — ordered by priority (first match wins within a page)
# ---------------------------------------------------------------------------

_STATEMENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("balance_sheet", [
        "balance sheet",
        "assets and liabilities",
        "statement of financial position",
    ]),
    ("income_statement", [
        "profit and loss",
        "statement of profit",
        "income statement",
        "revenue from operations",
    ]),
    ("cash_flow", [
        "cash flow statement",
        "cash flows from operating",
        "statement of cash flow",
    ]),
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

def _classify_statement_type(text_lower: str) -> str:
    for stmt_type, keywords in _STATEMENT_KEYWORDS:
        for kw in keywords:
            if kw in text_lower:
                return stmt_type
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
    """
    result: list[PageClassification] = []
    for idx, page_text in enumerate(pages_raw_text):
        page_no = idx + 1
        text_lower = page_text.lower()
        stmt_type = _classify_statement_type(text_lower)
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
