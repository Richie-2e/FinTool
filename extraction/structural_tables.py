"""
extraction/structural_tables.py
Phase B (next-architecture review) -- PyMuPDF find_tables() as an ADDITIVE
structural representation, used ONLY for provenance/validation (L6, Phase C).

Never sent to the LLM. extraction/parser.py's pages_raw_text (the LLM-facing
text) is completely unaffected -- this module is not called from parser.py
at all. Structural extraction is scoped, on demand, to exactly the pages
extraction/llm/page_selector.py already selects for a given statement-type
LLM call, invoked from extraction/llm/llm_extractor.py -- never run over the
whole document, keeping cost bounded to pages downstream validation actually
needs. (Original design doc proposed running this inside parser.py's Stage 1
over every page; scoped instead after measuring real find_tables() cost and
confirming L6 never needs structural data for any page that wasn't already
sent to the LLM -- see NEXT_ARCHITECTURE_DESIGN_REVIEW.md's Phase B/C
implementation note for the full rationale.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from extraction.canonical_metrics import DATE_YEAR_RE, YEAR_RE
from extraction.metric_extractor import parse_value


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StructuralTable:
    table_id: str                      # f"{page_no}_t{table_index}"
    page_no: int                       # 1-indexed
    row_count: int
    col_count: int
    rows: list[list[str]]              # raw cell text; row 0 is typically the header
    col_years: dict[int, list[int]]    # column_index -> years found in that column's
                                        # header text. 0 years = none detected;
                                        # >1 years = merged/ambiguous column (confirmed
                                        # real: TataSteel p.282's 3rd column merges
                                        # "As at March 31, 2024" and "As at April 1,
                                        # 2023" into one detected column)

    def cell(self, row_index: int, col_index: int) -> Optional[str]:
        if 0 <= row_index < len(self.rows) and 0 <= col_index < len(self.rows[row_index]):
            return self.rows[row_index][col_index]
        return None

    def row_label(self, row_index: int) -> str:
        """First cell of a row, treated as its label. May be empty -- label-less
        subtotal rows are a confirmed real pattern (e.g. OFSS's current_assets/
        current_liabilities rows on p.61 have no label of their own, only a
        bare number following the last line item -- matches gold's own
        'source_has_no_explicit_label' annotation for these exact metrics)."""
        cell = self.cell(row_index, 0)
        return (cell or "").strip()


# ---------------------------------------------------------------------------
# Year extraction -- reuses canonical_metrics.py's existing regexes only
# ---------------------------------------------------------------------------

# Indian fiscal-year-range header, e.g. "2023-24" (year ending 31 March
# 2024). This project's convention -- the LLM extraction path's own `year`
# field, every gold record, and this exact document's own balance-sheet
# header style ("As at 31st March, 2024") -- reports the ENDING year of the
# fiscal year as the canonical reporting year. DATE_YEAR_RE's own "YYYY-YY"
# alternative instead captures the STARTING year, which silently produced a
# col_years value one year too early wherever this header style appears
# (confirmed real: Reliance p.92/p.133; see
# scratchpad/next_architecture_review/FOOTNOTE_AND_L6_YEAR_DIAGNOSIS.md).
# Handled locally here, not in DATE_YEAR_RE itself, since that regex has two
# other call sites (metric_extractor.py's V1 fallback path, pipeline.py's
# document-year detection) that are out of scope for this fix.
_FISCAL_YEAR_RANGE_RE = re.compile(r"\b(20\d{2})-(\d{2})\b")


def _years_in_text(text: str) -> list[int]:
    text = text or ""
    years: list[int] = []

    fiscal_spans: list[tuple[int, int]] = []
    for m in _FISCAL_YEAR_RANGE_RE.finditer(text):
        end_year = int(m.group(1)) + 1
        if end_year not in years:
            years.append(end_year)
        fiscal_spans.append(m.span())

    def _already_handled(pos: int) -> bool:
        return any(start <= pos < end for start, end in fiscal_spans)

    for m in DATE_YEAR_RE.finditer(text):
        if _already_handled(m.start()):
            continue
        for g in m.groups():
            if g:
                y = int(g)
                if 2000 <= y <= 2100 and y not in years:
                    years.append(y)
    for m in YEAR_RE.finditer(text):
        if _already_handled(m.start()):
            continue
        y = int(m.group(1))
        if 2000 <= y <= 2100 and y not in years:
            years.append(y)
    return years


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_structural_tables(page, page_no: int) -> list[StructuralTable]:
    """
    Run PyMuPDF's native find_tables() on a single already-open fitz.Page.

    Returns an empty list if no tables are detected -- a normal, expected
    outcome (not every page has a table, not every financial metric comes
    from one). Callers (structural_validator.py) treat an empty list as "no
    structural data available for this page", landing candidates in
    NEEDS_REVIEW rather than crashing or silently upgrading them.
    """
    try:
        found = page.find_tables()
    except Exception:
        # PyMuPDF's table-finder can raise on some malformed/unusual pages;
        # treated identically to "no tables found" -- never propagated as a
        # pipeline failure, since this is validation metadata, not the
        # LLM-facing extraction path.
        return []

    tables: list[StructuralTable] = []
    for idx, t in enumerate(found.tables):
        try:
            extracted = t.extract()
        except Exception:
            continue
        rows = [[("" if c is None else str(c)) for c in row] for row in extracted]

        # PyMuPDF's header can be "external" (NOT included in extract()'s own
        # row list at all -- confirmed real and load-bearing: TataSteel
        # p.282's table has header.external=True, so rows[0] there is
        # already the first DATA row, not a header) or "internal" (==
        # extract()'s own row 0 exactly -- confirmed real for every OFSS
        # table checked: header.external=False). Always derive col_years
        # from t.header.names directly; never assume rows[0] is the header,
        # since that assumption is false whenever header.external is True.
        try:
            header_texts = list(t.header.names) if t.header and t.header.names else None
        except Exception:
            header_texts = None
        if not header_texts:
            header_texts = rows[0] if rows else []

        if not rows and not header_texts:
            continue

        col_years = {ci: _years_in_text(cell) for ci, cell in enumerate(header_texts)}

        tables.append(StructuralTable(
            table_id=f"{page_no}_t{idx}",
            page_no=page_no,
            row_count=len(rows),
            col_count=max((len(r) for r in rows), default=len(header_texts)),
            rows=rows,
            col_years=col_years,
        ))
    return tables


def extract_structural_tables_for_pages(
    pdf_path: str | Path, page_numbers: list[int]
) -> dict[int, list[StructuralTable]]:
    """
    Open the PDF once, extract structural tables only for the given
    (1-indexed) page numbers. Callers pass exactly the pages already selected
    for an LLM call (page_selector.select_pages()'s output) -- structural
    extraction never runs on a page that wasn't already sent to the LLM.
    """
    import fitz  # local import, mirrors extraction/parser.py's own pattern

    result: dict[int, list[StructuralTable]] = {}
    doc = fitz.open(str(pdf_path))
    try:
        for pno in page_numbers:
            if pno < 1 or pno > doc.page_count:
                result[pno] = []
                continue
            result[pno] = extract_structural_tables(doc[pno - 1], pno)
    finally:
        doc.close()
    return result
