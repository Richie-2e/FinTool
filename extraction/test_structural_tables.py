"""
extraction/test_structural_tables.py
Unit + live-PDF tests for structural_tables.py (Phase B).

The live tests open the actual corpus PDFs and run PyMuPDF's real
find_tables() -- skipped gracefully (not failed) if a given PDF isn't
present in uploads/, so the suite stays runnable in environments without the
full corpus, matching this project's existing test philosophy of using real
captured data without hard-requiring the source files at import time.

Run with:
    python -m unittest extraction.test_structural_tables -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

from extraction.structural_tables import (
    StructuralTable,
    _years_in_text,
    extract_structural_tables_for_pages,
)

_UPLOADS = Path(__file__).resolve().parent.parent / "uploads"


class TestYearsInText(unittest.TestCase):
    def test_single_year_header(self):
        self.assertEqual(_years_in_text("Year ended\nMarch 31, 2025"), [2025])

    def test_merged_two_year_header(self):
        # Real TataSteel p.282 artifact -- PyMuPDF merged 2 visual columns
        # into one detected column's header text.
        self.assertEqual(
            _years_in_text("As at March 31, 2024 (Restated) As at April 1, 2023 (Restated)"),
            [2024, 2023],
        )

    def test_no_year(self):
        self.assertEqual(_years_in_text("Notes"), [])

    def test_empty_string(self):
        self.assertEqual(_years_in_text(""), [])


class TestFiscalYearRangeHeader(unittest.TestCase):
    """Indian fiscal-year-range headers ("2023-24") report the ENDING year
    as the canonical reporting year throughout this project -- confirmed
    real and load-bearing on Reliance p.92/p.133 (see
    scratchpad/next_architecture_review/FOOTNOTE_AND_L6_YEAR_DIAGNOSIS.md).
    Before this fix, col_years mapped "2023-24" -> [2023] (the starting
    year), one year too early."""

    def test_fiscal_year_range_returns_ending_year(self):
        self.assertEqual(_years_in_text("2023-24"), [2024])

    def test_fiscal_year_range_prior_year(self):
        self.assertEqual(_years_in_text("2022-23"), [2023])

    def test_fy_prefix_still_unaffected(self):
        self.assertEqual(_years_in_text("FY2024"), [2024])

    def test_month_year_still_unaffected(self):
        self.assertEqual(_years_in_text("31 March 2024"), [2024])

    def test_bare_year_still_unaffected(self):
        self.assertEqual(_years_in_text("2024"), [2024])

    def test_fiscal_range_with_trailing_annotation_no_duplicate(self):
        self.assertEqual(_years_in_text("2023-24 (Audited)"), [2024])

    def test_full_four_digit_year_range_falls_back_to_bare_years(self):
        # "YYYY-YYYY" (both years spelled out in full) does not match the
        # 2-digit fiscal-range pattern; existing bare-4-digit-year
        # detection (YEAR_RE) already picks up both years independently,
        # unaffected by this fix.
        self.assertEqual(_years_in_text("2023-2024"), [2023, 2024])

    def test_mixed_header_fiscal_range_and_standalone_year(self):
        # A fiscal-range cell alongside an unrelated standalone year
        # mention elsewhere in the same text -- both must be captured
        # correctly (ending year for the range, the year itself for the
        # standalone mention), with no double-count of the range's
        # already-handled span.
        self.assertEqual(
            _years_in_text("2023-24\nRestated figures as per 2025 disclosure"),
            [2024, 2025],
        )


class TestStructuralTableHelpers(unittest.TestCase):
    def setUp(self):
        self.table = StructuralTable(
            table_id="t0", page_no=1, row_count=2, col_count=2,
            rows=[["", "2025"], ["Revenue", "100"]],
            col_years={0: [], 1: [2025]},
        )

    def test_cell_in_bounds(self):
        self.assertEqual(self.table.cell(1, 1), "100")

    def test_cell_out_of_bounds_returns_none(self):
        self.assertIsNone(self.table.cell(5, 5))

    def test_row_label_empty_row0(self):
        self.assertEqual(self.table.row_label(0), "")

    def test_row_label_normal_row(self):
        self.assertEqual(self.table.row_label(1), "Revenue")


@unittest.skipUnless((_UPLOADS / "3840dcfd02e0.pdf").exists(), "OFSS corpus PDF not present")
class TestLiveOFSSExtraction(unittest.TestCase):
    """Live find_tables() against the real OFSS PDF -- confirms the module
    actually works against a real corpus document, not just fixtures."""

    def test_cashflow_page_117_structure(self):
        result = extract_structural_tables_for_pages(
            _UPLOADS / "3840dcfd02e0.pdf", [117]
        )
        tables = result[117]
        self.assertEqual(len(tables), 1)
        t = tables[0]
        self.assertEqual(t.row_count, 42)
        self.assertEqual(t.col_years[1], [2025])
        self.assertEqual(t.col_years[2], [2024])
        # The two competing operating_cash_flow rows, exact real content.
        self.assertEqual(t.row_label(30), "Cash from operating activities")
        self.assertEqual(t.cell(30, 1), "33,547")
        self.assertEqual(t.row_label(32), "Net cash provided by operating activities")
        self.assertEqual(t.cell(32, 1), "21,989")

    def test_pages_with_no_table_return_empty_list_not_error(self):
        # A narrative-only page is a normal, expected "no table" case.
        result = extract_structural_tables_for_pages(_UPLOADS / "3840dcfd02e0.pdf", [1])
        self.assertIn(1, result)
        self.assertIsInstance(result[1], list)  # empty or not -- must not raise

    def test_out_of_range_page_returns_empty_list_not_error(self):
        result = extract_structural_tables_for_pages(_UPLOADS / "3840dcfd02e0.pdf", [99999])
        self.assertEqual(result[99999], [])


@unittest.skipUnless((_UPLOADS / "e36efb6efe4f.pdf").exists(), "TataSteel corpus PDF not present")
class TestLiveTataSteelExtraction(unittest.TestCase):
    def test_balance_sheet_page_282_merged_column_artifact(self):
        result = extract_structural_tables_for_pages(
            _UPLOADS / "e36efb6efe4f.pdf", [282]
        )
        tables = result[282]
        self.assertEqual(len(tables), 1)
        t = tables[0]
        # Confirms the real, load-bearing artifact this session's fixtures
        # and L6 design depend on: 3 detected columns for a visually
        # 4-column table, with 2 years merged into column 2's header.
        self.assertEqual(t.col_count, 3)
        self.assertEqual(sorted(t.col_years[2]), [2023, 2024])


@unittest.skipUnless((_UPLOADS / "c4187424880a.pdf").exists(), "Reliance corpus PDF not present")
class TestLiveRelianceFiscalYearRangeRegression(unittest.TestCase):
    """Regression coverage for the Reliance p.92/p.133 fiscal-year-range bug
    (see scratchpad/next_architecture_review/FOOTNOTE_AND_L6_YEAR_DIAGNOSIS.md
    section 2). Before this fix, both pages' "2023-24"/"2022-23" headers
    mapped to col_years {3: [2023], 4: [2022]} -- one year too early. Also
    confirms p.89's differently-styled header ("As at 31st March, 2024"),
    which was never affected by the bug, still resolves correctly."""

    def test_cash_flow_page_92_year_columns_corrected(self):
        result = extract_structural_tables_for_pages(_UPLOADS / "c4187424880a.pdf", [92])
        t = result[92][0]
        self.assertEqual(t.col_years, {0: [], 1: [], 2: [], 3: [2024], 4: [2023]})
        # The operating_cash_flow row itself, exact real content.
        self.assertEqual(t.cell(23, 3), "73,998")
        self.assertEqual(t.cell(23, 4), "55,340")

    def test_income_statement_page_133_year_columns_corrected(self):
        result = extract_structural_tables_for_pages(_UPLOADS / "c4187424880a.pdf", [133])
        for t in result[133]:
            self.assertEqual(t.col_years, {0: [], 1: [], 2: [], 3: [2024], 4: [2023]})

    def test_balance_sheet_page_89_unaffected_by_this_fix(self):
        # Uses "As at 31st March, 2024" style headers, already correct
        # before this fix -- must remain byte-identical after it.
        result = extract_structural_tables_for_pages(_UPLOADS / "c4187424880a.pdf", [89])
        for t in result[89]:
            self.assertEqual(t.col_years, {0: [], 1: [], 2: [], 3: [2024], 4: [2023]})


if __name__ == "__main__":
    unittest.main()
