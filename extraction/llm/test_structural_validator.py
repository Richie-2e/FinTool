"""
extraction/llm/test_structural_validator.py
Unit tests for structural_validator.py (L6).

Fixtures are REAL PyMuPDF find_tables() output, captured live during the
Phase B/C design-validation pass against the actual corpus PDFs (OFSS
uploads/3840dcfd02e0.pdf p.117 cash flow statement; TataSteel
uploads/e36efb6efe4f.pdf p.282 balance sheet) -- not synthetic examples,
matching this project's existing testing convention
(extraction/llm/test_candidate_validator.py's own stated practice). Embedded
as static StructuralTable literals so the suite stays fast and isolated,
without requiring the source PDFs or PyMuPDF to run.

Run with:
    python -m unittest extraction.llm.test_structural_validator -v
"""

from __future__ import annotations

import unittest

from extraction.metric_extractor import CandidateMetric
from extraction.structural_tables import StructuralTable
from extraction.llm.structural_validator import validate_one, run_structural_validation


def _candidate(**overrides) -> CandidateMetric:
    defaults = dict(
        doc_id="test_doc", metric_name="revenue", raw_label="Revenue from operations",
        value=68468.0, unit="INR million", year=2025, page_no=62,
        statement_type="income_statement", section_type="consolidated",
        confidence="high", source="llm_extraction", evidence="",
    )
    defaults.update(overrides)
    return CandidateMetric(**defaults)


# ---------------------------------------------------------------------------
# Real fixture: OFSS p.117 cash flow statement (42 rows), verbatim from a
# live find_tables() call against uploads/3840dcfd02e0.pdf during Phase B/C.
# ---------------------------------------------------------------------------

_OFSS_CASHFLOW_ROWS: list[list[str]] = [
    ["", "Year ended\nMarch 31, 2025", "Year ended\nMarch 31, 2024"],
    ["Cash flows from operating activities", "", ""],
    ["Profit before tax", "33,109", "30,223"],
    ["Adjustments to reconcile profit before tax to cash (used in) provided by operating\nactivities:", "", ""],
    ["Depreciation and amortization", "691", "743"],
    ["(Profit) loss on sale of fixed assets", "(4)", "2"],
    ["Impairment loss (reversed) recognized on contract assets", "(533)", "616"],
    ["Impairment loss recognized on other financial assets", "11", "6"],
    ["Bad debts", "535", "85"],
    ["Finance income", "(3,157)", "(3,317)"],
    ["Employee stock compensation expense", "1,244", "950"],
    ["(Gain) on lease modification", "–", "*–"],
    ["Effect of exchange rate changes in cash and cash equivalents", "(807)", "(130)"],
    ["Effect of exchange rate changes in assets and liabilities", "603", "43"],
    ["Finance cost", "5", "281"],
    ["Deferred rent", "35", "–"],
    ["", "31,732", "29,502"],
    ["", "", ""],
    ["Movements in operating assets and liabilities", "", ""],
    ["(Increase) in other non-current assets", "(51)", "(38)"],
    ["Decrease (increase) in trade receivables", "1,603", "(3,031)"],
    ["Decrease (increase) in other current financial assets", "1,401", "(1,207)"],
    ["(Increase) in other current assets", "(492)", "(795)"],
    ["Increase in non-current financial liabilities", "1", "2"],
    ["(Decrease) increase in other non-current liabilities", "(181)", "107"],
    ["Increase in non-current provisions", "298", "311"],
    ["(Decrease) increase in trade payables", "(338)", "625"],
    ["(Decrease) increase in other current financial liabilities", "(37)", "230"],
    ["(Decrease) increase in other current liabilities", "(665)", "144"],
    ["Increase in current provisions", "276", "114"],
    ["Cash from operating activities", "33,547", "25,964"],
    ["Payment of domestic and foreign taxes, net of refunds", "(11,558)", "(8,057)"],
    ["Net cash provided by operating activities", "21,989", "17,907"],
    ["", "", ""],
    ["Cash flows from investing activities", "", ""],
    ["Purchase of property, plant and equipment", "(352)", "(301)"],
    ["Proceeds from sale of property, plant and equipment", "6", "*–"],
    ["(Placement) refund of deposits for premises and others", "(15)", "36"],
    ["Bank fixed deposits having maturity of more than three months matured", "21,700", "35,220"],
    ["Bank fixed deposits having maturity of more than three months booked", "(48,064)", "(21,466)"],
    ["Interest received", "2,199", "2,491"],
    ["Net cash (used in) provided by investing activities", "(24,526)", "15,980"],
]

_OFSS_CASHFLOW_TABLE = StructuralTable(
    table_id="117_t0", page_no=117,
    row_count=len(_OFSS_CASHFLOW_ROWS), col_count=3,
    rows=_OFSS_CASHFLOW_ROWS,
    col_years={0: [], 1: [2025], 2: [2024]},
)

# ---------------------------------------------------------------------------
# Real fixture: OFSS p.62 income statement (trimmed to the rows needed),
# also verbatim from a live find_tables() call.
# ---------------------------------------------------------------------------

_OFSS_INCOME_ROWS: list[list[str]] = [
    ["", "Notes", "Year ended\nMarch 31, 2025", "Year ended\nMarch 31, 2024"],
    ["Revenue from operations", "17", "68,468", "63,730"],
    ["Other income", "18", "3,042", "3,422"],
    ["Total income", "", "71,510", "67,152"],
]

_OFSS_INCOME_TABLE = StructuralTable(
    table_id="62_t0", page_no=62,
    row_count=len(_OFSS_INCOME_ROWS), col_count=4,
    rows=_OFSS_INCOME_ROWS,
    col_years={0: [], 1: [], 2: [2025], 3: [2024]},
)

# ---------------------------------------------------------------------------
# Real fixture: TataSteel p.282 balance sheet (trimmed to rows 0-34),
# verbatim from a live find_tables() call against uploads/e36efb6efe4f.pdf.
#
# IMPORTANT, confirmed only by actually running find_tables() live (this is
# exactly the kind of assumption the Phase B/C task instructions warned
# against making blindly): this table's header.external is True, meaning
# PyMuPDF's extract() does NOT include the header row in its row list at all
# -- row 0 of `rows` below is already the first DATA row ("Assets", garbled
# to "ssets" by PyMuPDF's own text extraction), not a header. The header
# ("Note Page" / "As at March 31, 2025" / "As at March 31, 2024 (Restated)
# As at April 1, 2023 (Restated)") exists only in the table object's
# separate .header.names property, reflected here in col_years, never as a
# row. structural_tables.py's extract_structural_tables() derives col_years
# from t.header.names directly for exactly this reason -- see its docstring.
#
# PyMuPDF detected only 3 columns for a visually 4-column comparative table
# (2025 / 2024-restated / April-1-2023-restated) -- column 2's header and
# every cell in it fuse two years/values into one string. Real, confirmed
# artifact.
# ---------------------------------------------------------------------------

_TATASTEEL_ROWS: list[list[str]] = [["", "", ""] for _ in range(35)]  # 0-34, no header row
_TATASTEEL_ROWS[34] = ["Total equity", "1,26,731.94", "1,41,229.47 1,39,009.95"]

_TATASTEEL_TABLE = StructuralTable(
    table_id="282_t0", page_no=282,
    row_count=len(_TATASTEEL_ROWS), col_count=3,
    rows=_TATASTEEL_ROWS,
    col_years={0: [], 1: [2025], 2: [2024, 2023]},
)


class TestCorrectRowCorrectYear(unittest.TestCase):
    """1. Correct row + correct year -> VERIFIED."""

    def test_ofss_revenue_2025_verified(self):
        c = _candidate(metric_name="revenue", raw_label="Revenue from operations",
                        value=68468.0, year=2025, statement_type="income_statement")
        state, reason, table_id, row, col = validate_one(c, [_OFSS_INCOME_TABLE])
        self.assertEqual(state, "VERIFIED")
        self.assertEqual(row, 1)
        self.assertEqual(col, 2)

    def test_ofss_capex_2025_verified_no_competing_rows(self):
        c = _candidate(metric_name="capex", raw_label="Purchase of property, plant and equipment",
                        value=-352.0, year=2025, statement_type="cash_flow")
        state, reason, table_id, row, col = validate_one(c, [_OFSS_CASHFLOW_TABLE])
        self.assertEqual(state, "VERIFIED")
        self.assertEqual(row, 35)


class TestCorrectRowWrongYear(unittest.TestCase):
    """2. Correct row + wrong year -> NEEDS_REVIEW."""

    def test_value_in_2025_column_declared_as_2024(self):
        # Real production case: OFSS operating_cash_flow candidate declared
        # year=2024 with value=21989 -- 21,989 is genuinely row 32's value,
        # but for 2025, not 2024 (whose real value is 17,907).
        c = _candidate(metric_name="operating_cash_flow", raw_label="Cash from operating activities",
                        value=21989.0, year=2024, statement_type="cash_flow")
        state, reason, table_id, row, col = validate_one(c, [_OFSS_CASHFLOW_TABLE])
        self.assertEqual(state, "NEEDS_REVIEW")
        self.assertIn("2025", reason)
        self.assertIn("not the declared 2024", reason)
        self.assertEqual(row, 32)


class TestWrongRowInternallyConsistent(unittest.TestCase):
    """3. Wrong row + internally consistent value -> NEEDS_REVIEW."""

    def test_ofss_pretax_subtotal_self_consistent_but_not_final(self):
        # Real production case: label, evidence, and value all agree with
        # each other -- but on the pre-tax subtotal row, not the final total.
        c = _candidate(metric_name="operating_cash_flow", raw_label="Cash from operating activities",
                        value=33547.0, year=2025, statement_type="cash_flow")
        state, reason, table_id, row, col = validate_one(c, [_OFSS_CASHFLOW_TABLE])
        self.assertEqual(state, "NEEDS_REVIEW")
        self.assertEqual(row, 30)
        self.assertIn("not the last", reason)
        self.assertIn("intermediate subtotal", reason)


class TestLabelValueDifferentRows(unittest.TestCase):
    """4. Label/value from different rows -> NEEDS_REVIEW."""

    def test_label_matches_one_row_value_matches_another(self):
        c = CandidateMetric(
            doc_id="test_doc", metric_name="operating_cash_flow",
            raw_label="Cash from operating activities",  # matches row 30
            value=17907.0,  # only appears at row 32 (2024 column) -- forces
                             # a genuine label/value-row disagreement rather
                             # than a wrong-year case (see class 2 above,
                             # which already covers the wrong-year variant)
            unit="INR million", year=2024, page_no=117,
            statement_type="cash_flow", section_type="consolidated",
            confidence="high", source="llm_extraction", evidence="",
        )
        state, reason, table_id, row, col = validate_one(c, [_OFSS_CASHFLOW_TABLE])
        self.assertEqual(state, "NEEDS_REVIEW")
        self.assertIn("label/value row mismatch", reason)
        self.assertEqual(row, 32)


class TestSectionHeaderDoesNotFalsePositiveAsLabelRow(unittest.TestCase):
    """Regression: a candidate whose raw_label happens to equal a SECTION
    HEADER's exact text (no values of its own), while its real value lives
    in a different, label-less subtotal row, must not be flagged as a
    label/value row mismatch. Confirmed live during Phase B/C testing: OFSS
    current_assets (raw_label "Current assets" == row 13's section header;
    real value at label-less row 21) and current_liabilities (raw_label
    "Current liabilities" == row 39's section header; real value at
    label-less row 49) were both wrongly downgraded to NEEDS_REVIEW by this
    exact bug before the fix."""

    def test_ofss_current_assets_label_less_subtotal_is_verified(self):
        c = _candidate(metric_name="current_assets", raw_label="Current assets",
                        value=79458.0, year=2025, statement_type="balance_sheet",
                        page_no=61)
        table = StructuralTable(
            table_id="61_t0", page_no=61, row_count=22, col_count=4,
            rows=[
                ["", "Notes", "March 31, 2025", "March 31, 2024"],   # 0 header
                ["ASSETS", "", "", ""],                              # 1
                ["Non-current assets", "", "", ""],                  # 2
                ["", "", "", ""], ["", "", "", ""], ["", "", "", ""],  # 3-5 filler
                ["", "", "", ""], ["", "", "", ""], ["", "", "", ""],  # 6-8 filler
                ["", "", "", ""], ["", "", "", ""], ["", "", "", ""],  # 9-11 filler
                ["", "", "21,892", "22,843"],                        # 12
                ["Current assets", "", "", ""],                      # 13 -- section header, no values
                ["Financial assets", "", "", ""],                    # 14
                ["Trade receivables", "8", "11,837", "13,193"],      # 15
                ["Cash and cash equivalents", "9 (a)", "12,142", "34,833"],  # 16
                ["Other bank balances", "9 (b)", "47,372", "20,549"],  # 17
                ["Other financial assets", "7", "3,599", "4,323"],   # 18
                ["Income tax assets (net)", "", "619", "280"],       # 19
                ["Other current assets", "10", "3,889", "3,336"],    # 20
                ["", "", "79,458", "76,514"],                        # 21 -- label-less subtotal, real value
            ],
            col_years={0: [], 1: [], 2: [2025], 3: [2024]},
        )
        state, reason, table_id, row, col = validate_one(c, [table])
        self.assertEqual(state, "VERIFIED")
        self.assertEqual(row, 21)


class TestNoStructuralTable(unittest.TestCase):
    """5. No table structure available -> appropriate unverifiable state."""

    def test_empty_tables_list_yields_needs_review(self):
        c = _candidate()
        state, reason, table_id, row, col = validate_one(c, [])
        self.assertEqual(state, "NEEDS_REVIEW")
        self.assertIn("no structural table available", reason)
        self.assertIsNone(table_id)
        self.assertIsNone(row)
        self.assertIsNone(col)


class TestDuplicateAmbiguousRows(unittest.TestCase):
    """6. Genuine duplicate/ambiguous rows -> NEEDS_REVIEW, not arbitrary selection."""

    def test_identical_value_in_two_structurally_distinct_rows_same_year(self):
        rows = [
            ["", "Year ended March 31, 2025", "Year ended March 31, 2024"],
            ["Row A", "1,000", "900"],
            ["Row B", "1,000", "800"],  # same 2025 value as Row A -- genuine duplicate
        ]
        table = StructuralTable(
            table_id="synthetic_t0", page_no=999, row_count=3, col_count=3,
            rows=rows, col_years={0: [], 1: [2025], 2: [2024]},
        )
        c = _candidate(metric_name="revenue", raw_label="Row A", value=1000.0,
                        year=2025, statement_type="income_statement", page_no=999)
        state, reason, table_id, row, col = validate_one(c, [table])
        self.assertEqual(state, "NEEDS_REVIEW")
        self.assertIn("not uniquely identifiable", reason)


class TestNoUsableStructureForThisMetricDoesNotOverReject(unittest.TestCase):
    """7. A table exists on the page, but doesn't contain this metric's row
    -> still NEEDS_REVIEW gracefully, never a crash, never a false REJECTED
    (raw_label doesn't uniquely+unambiguously identify a row here, so the
    narrow structural-contradiction path correctly does not fire)."""

    def test_value_not_present_anywhere_in_an_unrelated_table(self):
        c = _candidate(metric_name="revenue", raw_label="Revenue from operations",
                        value=999999.0, year=2025, statement_type="income_statement",
                        page_no=117)
        state, reason, table_id, row, col = validate_one(c, [_OFSS_CASHFLOW_TABLE])
        self.assertEqual(state, "NEEDS_REVIEW")
        self.assertIn("not found in any structural table cell", reason)


class TestTataSteelWrongYearColumnRegression(unittest.TestCase):
    """Regression fixture: TataSteel total_equity, the documented wrong-
    column production defect (PROJECT_STATUS.md / CURRENT_SYSTEM_GROUND_TRUTH.md).
    PyMuPDF merged 2 visual columns into 1 detected column; the production
    value (139,009.95) is the April-1-2023 figure fused into that merged
    cell, declared as year=2025. Because raw_label ("Total equity") matches
    row 34 exactly and unambiguously, and the true 2025 column is
    unambiguous and contains a different real number (126,731.94), this is
    exactly the narrow, high-confidence contradiction case L6 is designed to
    catch -- REJECTED, not silently VERIFIED and not merely NEEDS_REVIEW,
    since we have positive structural proof of the correct value, not just
    an inability to confirm."""

    def test_production_defect_value_is_rejected_not_verified(self):
        c = _candidate(metric_name="total_equity", raw_label="Total equity",
                        value=139009.95, year=2025, statement_type="balance_sheet",
                        page_no=282)
        state, reason, table_id, row, col = validate_one(c, [_TATASTEEL_TABLE])
        self.assertEqual(state, "REJECTED")
        self.assertIn("126731.94", reason)
        self.assertEqual(row, 34)
        self.assertEqual(col, 1)

    def test_correct_value_at_correct_column_is_verified(self):
        c = _candidate(metric_name="total_equity", raw_label="Total equity",
                        value=126731.94, year=2025, statement_type="balance_sheet",
                        page_no=282)
        state, reason, table_id, row, col = validate_one(c, [_TATASTEEL_TABLE])
        self.assertEqual(state, "VERIFIED")
        self.assertEqual(row, 34)


class TestRunStructuralValidationOrchestration(unittest.TestCase):
    """run_structural_validation(): REJECTED candidates are removed from the
    kept list and turned into RejectionRecords; everything else is kept with
    verification_state/reason/table_id/row_index/col_index set."""

    def test_mixed_batch_verified_and_rejected(self):
        good = _candidate(metric_name="revenue", raw_label="Revenue from operations",
                           value=68468.0, year=2025, statement_type="income_statement",
                           page_no=282)
        bad = _candidate(metric_name="total_equity", raw_label="Total equity",
                          value=139009.95, year=2025, statement_type="balance_sheet",
                          page_no=282)
        kept, rejections = run_structural_validation(
            [good, bad], [_TATASTEEL_TABLE]
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].metric_name, "revenue")
        self.assertEqual(kept[0].verification_state, "NEEDS_REVIEW")  # revenue not on this page
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0].metric_name, "total_equity")
        self.assertIn("l6_structural_contradiction", rejections[0].rejection_reasons[0])

    def test_no_l2_l5_accepted_candidate_disappears_without_a_rejection_record(self):
        # Every candidate that isn't REJECTED must be preserved -- the
        # explicit "must not silently destroy coverage" requirement.
        candidates = [
            _candidate(metric_name="revenue", value=68468.0, year=2025,
                       statement_type="income_statement", page_no=62,
                       raw_label="Revenue from operations"),
            _candidate(metric_name="operating_cash_flow", value=33547.0, year=2025,
                       statement_type="cash_flow", page_no=117,
                       raw_label="Cash from operating activities"),
        ]
        tables = [_OFSS_INCOME_TABLE, _OFSS_CASHFLOW_TABLE]
        kept, rejections = run_structural_validation(candidates, tables)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(rejections), 0)
        states = {c.metric_name: c.verification_state for c in kept}
        self.assertEqual(states["revenue"], "VERIFIED")
        self.assertEqual(states["operating_cash_flow"], "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()
