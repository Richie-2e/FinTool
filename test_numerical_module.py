"""
test_numerical_module.py
Unit tests for numerical_module.py's ratio computation, focused on the
scope-consistency guard (_scope_guard/_metric_scope) added to close a
confirmed bug: build_resolved_metrics_df() resolves each canonical metric
independently, so a ratio could silently combine values pulled from
different reporting scopes (e.g. Jio Financial Services' asset_turnover,
which divided consolidated revenue by standalone total_assets, producing
0.081405 instead of the correct consolidated ~0.0153).

Scope is deliberately narrow: only numerical_module.compute()/
_compute_year() and the scope guard itself. No extraction, validation, or
RAG-layer behavior is touched or tested here. Broad end-to-end/integration
tests are intentionally out of scope for this file.
"""

from __future__ import annotations

import unittest

import pandas as pd

from numerical_module import compute


def _resolved_row(metric_name, value, year, section_type, page_no=1, doc_id="d1"):
    return {
        "doc_id": doc_id,
        "metric_name": metric_name,
        "value": value,
        "unit": "INR crore",
        "year": year,
        "page_no": page_no,
        "raw_label": metric_name,
        "statement_type": "balance_sheet",
        "section_type": section_type,
        "confidence": "high",
        "source": "llm_extraction",
    }


def _pivot_row(year, **metrics):
    row = {"year": year}
    row.update(metrics)
    return row


class TestScopeConsistentRatioComputes(unittest.TestCase):
    """Item 1: valid same-scope inputs -> ratio computed."""

    def test_same_scope_inputs_current_ratio_computed(self):
        resolved_df = pd.DataFrame([
            _resolved_row("current_assets", 1200.0, 2024, "standalone"),
            _resolved_row("current_liabilities", 800.0, 2024, "standalone"),
        ])
        pivot_df = pd.DataFrame([
            _pivot_row(2024, current_assets=1200.0, current_liabilities=800.0),
        ])
        result = compute("d1", pivot_df, resolved_df)
        yr = result.by_year[0]
        self.assertAlmostEqual(yr.current_ratio, 1.5)
        self.assertFalse(any("scope" in w for w in result.warnings))
        prov = next(p for p in result.provenance if p.ratio_name == "current_ratio")
        self.assertIsNone(prov.scope_warning)


class TestScopeMismatchBlocksRatio(unittest.TestCase):
    """Item 2: different-scope inputs -> ratio unavailable/rejected."""

    def test_different_scope_inputs_current_ratio_unavailable(self):
        resolved_df = pd.DataFrame([
            _resolved_row("current_assets", 1200.0, 2024, "standalone"),
            _resolved_row("current_liabilities", 800.0, 2024, "consolidated"),
        ])
        pivot_df = pd.DataFrame([
            _pivot_row(2024, current_assets=1200.0, current_liabilities=800.0),
        ])
        result = compute("d1", pivot_df, resolved_df)
        yr = result.by_year[0]
        self.assertIsNone(yr.current_ratio)
        self.assertTrue(any("current_ratio" in w and "scope" in w for w in result.warnings))
        prov = next(p for p in result.provenance if p.ratio_name == "current_ratio")
        self.assertIsNotNone(prov.scope_warning)
        self.assertIn("standalone", prov.scope_warning)
        self.assertIn("consolidated", prov.scope_warning)

    def test_ambiguous_unknown_scope_does_not_falsely_block(self):
        # "unknown" is the classifier/LLM's own sentinel for "could not
        # tell" -- it must not be treated as a real, comparable third
        # scope. A known scope paired with an "unknown" one must compute
        # normally, exactly as it did before this guard existed.
        resolved_df = pd.DataFrame([
            _resolved_row("current_assets", 1200.0, 2024, "standalone"),
            _resolved_row("current_liabilities", 800.0, 2024, "unknown"),
        ])
        pivot_df = pd.DataFrame([
            _pivot_row(2024, current_assets=1200.0, current_liabilities=800.0),
        ])
        result = compute("d1", pivot_df, resolved_df)
        yr = result.by_year[0]
        self.assertAlmostEqual(yr.current_ratio, 1.5)


class TestMissingInputUnaffectedByScopeGuard(unittest.TestCase):
    """Item 3: missing input -> existing unavailable behavior preserved."""

    def test_missing_input_still_yields_none_without_scope_warning(self):
        resolved_df = pd.DataFrame([
            _resolved_row("current_assets", 1200.0, 2024, "standalone"),
        ])
        pivot_df = pd.DataFrame([
            _pivot_row(2024, current_assets=1200.0),
        ])
        result = compute("d1", pivot_df, resolved_df)
        yr = result.by_year[0]
        self.assertIsNone(yr.current_ratio)
        prov = next(p for p in result.provenance if p.ratio_name == "current_ratio")
        self.assertIsNone(prov.scope_warning)
        self.assertFalse(any("scope" in w for w in result.warnings))


class TestMultiInputRatioRequiresAllInputsConsistent(unittest.TestCase):
    """Item 4: multiple-input ratio -> all required inputs must satisfy the scope rule."""

    def test_debt_to_equity_three_inputs_one_mismatched_blocks_ratio(self):
        # long_term_debt and short_term_debt agree (standalone), but
        # total_equity is consolidated -- a mismatch even though two of
        # the three inputs already agree with each other.
        resolved_df = pd.DataFrame([
            _resolved_row("long_term_debt", 1000.0, 2024, "standalone"),
            _resolved_row("short_term_debt", 500.0, 2024, "standalone"),
            _resolved_row("total_equity", 2000.0, 2024, "consolidated"),
        ])
        pivot_df = pd.DataFrame([
            _pivot_row(2024, long_term_debt=1000.0, short_term_debt=500.0, total_equity=2000.0),
        ])
        result = compute("d1", pivot_df, resolved_df)
        yr = result.by_year[0]
        self.assertIsNone(yr.debt_to_equity)
        # total_debt (an intermediate sum, not itself a scope-guarded ratio
        # in this milestone) is unaffected -- only debt_to_equity is blocked.
        self.assertEqual(yr.total_debt, 1500.0)

    def test_debt_to_equity_all_three_inputs_same_scope_computed(self):
        resolved_df = pd.DataFrame([
            _resolved_row("long_term_debt", 1000.0, 2024, "standalone"),
            _resolved_row("short_term_debt", 500.0, 2024, "standalone"),
            _resolved_row("total_equity", 2000.0, 2024, "standalone"),
        ])
        pivot_df = pd.DataFrame([
            _pivot_row(2024, long_term_debt=1000.0, short_term_debt=500.0, total_equity=2000.0),
        ])
        result = compute("d1", pivot_df, resolved_df)
        yr = result.by_year[0]
        self.assertAlmostEqual(yr.debt_to_equity, 0.75)


class TestYoYRatioUnaffectedByScopeGuard(unittest.TestCase):
    """Item 5: two-year YoY ratio with valid same-scope yearly inputs -> computed."""

    def test_two_year_yoy_revenue_growth_computed_normally(self):
        resolved_df = pd.DataFrame([
            _resolved_row("revenue", 1000.0, 2023, "standalone"),
            _resolved_row("revenue", 1200.0, 2024, "standalone"),
        ])
        pivot_df = pd.DataFrame([
            _pivot_row(2023, revenue=1000.0),
            _pivot_row(2024, revenue=1200.0),
        ])
        result = compute("d1", pivot_df, resolved_df)
        yr_2024 = result.by_year[1]
        self.assertAlmostEqual(yr_2024.yoy_revenue_growth, 0.2)

    def test_yoy_ratio_not_blocked_by_cross_year_scope_difference(self):
        # A YoY formula compares ONE metric_name against itself across two
        # years, never two different metrics within one year -- the
        # cross-metric scope-mixing bug this milestone targets cannot occur
        # here by construction. Deliberately uses DIFFERENT scopes across
        # the two years to prove no (out-of-scope) cross-year requirement
        # was introduced, per the milestone's explicit "don't over-require"
        # instruction.
        resolved_df = pd.DataFrame([
            _resolved_row("revenue", 1000.0, 2023, "standalone"),
            _resolved_row("revenue", 1200.0, 2024, "consolidated"),
        ])
        pivot_df = pd.DataFrame([
            _pivot_row(2023, revenue=1000.0),
            _pivot_row(2024, revenue=1200.0),
        ])
        result = compute("d1", pivot_df, resolved_df)
        yr_2024 = result.by_year[1]
        self.assertAlmostEqual(yr_2024.yoy_revenue_growth, 0.2)


class TestJioAssetTurnoverRegression(unittest.TestCase):
    """
    Item 6: direct regression reproduction of the confirmed Jio Financial
    Services bug. Real values from extraction_outputs/57bd82972001/
    57bd82972001_resolved_metrics.csv (revenue=2042.91 consolidated,
    page 111; total_assets=25095.53 standalone, page 83). Before this
    milestone, asset_turnover computed as 0.081405 (revenue / standalone
    total_assets); the correct consolidated value would be ~0.0153. It
    must now be None, not either of those numbers.
    """

    def test_jiofin_asset_turnover_scope_mismatch_suppressed(self):
        resolved_df = pd.DataFrame([
            _resolved_row("revenue", 2042.91, 2025, "consolidated", page_no=111),
            _resolved_row("total_assets", 25095.53, 2025, "standalone", page_no=83),
        ])
        pivot_df = pd.DataFrame([
            _pivot_row(2025, revenue=2042.91, total_assets=25095.53),
        ])
        result = compute("57bd82972001", pivot_df, resolved_df)
        yr = result.by_year[0]
        # Suppressed entirely (None) is the intended, safer behavior --
        # not the pre-fix buggy value (0.081405), and the guard makes no
        # attempt to compute the "correct consolidated" value either.
        self.assertIsNone(yr.asset_turnover)
        prov = next(p for p in result.provenance if p.ratio_name == "asset_turnover")
        self.assertIn("consolidated", prov.scope_warning)
        self.assertIn("standalone", prov.scope_warning)


class TestDeterministicFormulasUnchanged(unittest.TestCase):
    """
    Item 7: confirms the guard is purely additive -- same-scope inputs
    across the full ratio set still produce exactly the formulas documented
    in _compute_year() (same values as the module's own __main__ smoke-test
    data), with zero warnings.
    """

    def test_full_ratio_set_same_scope_matches_expected_formulas(self):
        section = "standalone"
        metrics = {
            "revenue": 2000.0, "gross_profit": 600.0, "operating_profit": 300.0,
            "net_profit": 200.0, "interest_expense": 50.0,
            "total_assets": 5000.0, "current_assets": 1200.0,
            "cash_and_equivalents": 400.0, "total_liabilities": 3000.0,
            "current_liabilities": 800.0, "long_term_debt": 1000.0,
            "short_term_debt": 500.0, "total_equity": 2000.0,
            "operating_cash_flow": 250.0, "investing_cash_flow": -100.0,
            "financing_cash_flow": -80.0, "capex": 120.0,
        }
        resolved_df = pd.DataFrame([
            _resolved_row(name, value, 2023, section) for name, value in metrics.items()
        ])
        pivot_df = pd.DataFrame([_pivot_row(2023, **metrics)])

        result = compute("d1", pivot_df, resolved_df)
        yr = result.by_year[0]

        self.assertAlmostEqual(yr.current_ratio, 1200.0 / 800.0)
        self.assertAlmostEqual(yr.cash_ratio, 400.0 / 800.0)
        self.assertAlmostEqual(yr.debt_to_equity, 1500.0 / 2000.0)
        self.assertAlmostEqual(yr.debt_ratio, 3000.0 / 5000.0)
        self.assertAlmostEqual(yr.interest_coverage, 300.0 / 50.0)
        self.assertAlmostEqual(yr.profit_margin, 200.0 / 2000.0)
        self.assertAlmostEqual(yr.operating_margin, 300.0 / 2000.0)
        self.assertAlmostEqual(yr.gross_margin, 600.0 / 2000.0)
        self.assertAlmostEqual(yr.asset_turnover, 2000.0 / 5000.0)
        self.assertAlmostEqual(yr.ocf_to_revenue, 250.0 / 2000.0)
        self.assertAlmostEqual(yr.free_cash_flow, 250.0 - 120.0)
        self.assertEqual(result.warnings, [])


if __name__ == "__main__":
    unittest.main()
