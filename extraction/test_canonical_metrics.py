"""
extraction/test_canonical_metrics.py
Regression tests for canonical_metrics.py's match_metric(), focused on the
operating_cash_flow canonical-pattern addition ("^cash from operating
activities$"), added to unblock OFSS's real, evidenced candidate that was
previously rejected at L2 with raw_label_mismatch (see
extraction_outputs/3840dcfd02e0/3840dcfd02e0_validator_rejections.csv).

Scope is deliberately narrow: this only exercises match_metric() directly.
No validator (L4/L5), prompt, or RAG-layer behavior is touched or tested here.
"""

from __future__ import annotations

import unittest

from extraction.canonical_metrics import match_metric


class TestOperatingCashFlowOFSSCase(unittest.TestCase):
    """The real raw_label that was previously rejected for OFSS."""

    def test_ofss_real_raw_label_now_matches(self):
        # Real raw_label observed for both OFSS 2024 and 2025 candidates.
        self.assertEqual(
            match_metric("Cash from operating activities", "cash_flow"),
            "operating_cash_flow",
        )

    def test_ofss_raw_label_matches_without_statement_type_filter(self):
        self.assertEqual(
            match_metric("Cash from operating activities"),
            "operating_cash_flow",
        )

    def test_case_and_whitespace_insensitive(self):
        # match_metric lowercases and collapses whitespace before matching.
        self.assertEqual(
            match_metric("  CASH   FROM  Operating   Activities  ", "cash_flow"),
            "operating_cash_flow",
        )


class TestOperatingCashFlowExistingLabelsStillMatch(unittest.TestCase):
    """
    Non-regression: every pre-existing pattern's real or representative
    label must still resolve to operating_cash_flow after the new pattern
    is added to the same list.
    """

    def test_tatasteel_real_raw_label_still_matches(self):
        # Real accepted raw_label for TataSteel 2024/2025 (resolved_metrics).
        self.assertEqual(
            match_metric("Net cash provided by operating activities", "cash_flow"),
            "operating_cash_flow",
        )

    def test_net_cash_from_operating_activities_still_matches(self):
        self.assertEqual(
            match_metric("Net cash from operating activities", "cash_flow"),
            "operating_cash_flow",
        )

    def test_net_cash_generated_from_operating_activities_still_matches(self):
        self.assertEqual(
            match_metric("Net cash generated from operating activities", "cash_flow"),
            "operating_cash_flow",
        )

    def test_net_cash_flow_from_operating_activities_still_matches(self):
        self.assertEqual(
            match_metric("Net cash flow from operating activities", "cash_flow"),
            "operating_cash_flow",
        )

    def test_cash_flows_from_operating_activities_still_matches(self):
        self.assertEqual(
            match_metric("Cash flows from operating activities", "cash_flow"),
            "operating_cash_flow",
        )

    def test_net_cash_from_used_in_operating_activities_still_matches(self):
        self.assertEqual(
            match_metric("Net cash from/(used in) operating activities", "cash_flow"),
            "operating_cash_flow",
        )

    def test_net_cash_generated_from_used_in_operating_activities_still_matches(self):
        self.assertEqual(
            match_metric(
                "Net cash generated from /(used in) operating activities", "cash_flow"
            ),
            "operating_cash_flow",
        )


class TestLeadingNumberedPrefixLICHSGFINCase(unittest.TestCase):
    """
    The real raw_label observed for LICHSGFIN's revenue line, which was
    previously rejected at L2 with raw_label_mismatch because the leading
    "(1) " line-item prefix Indian income statements use was not stripped
    before matching (see the C1 offline replay experiment).
    """

    def test_lichsgfin_real_raw_label_now_matches(self):
        self.assertEqual(
            match_metric("(1) REVENUE FROM OPERATIONS", "income_statement"),
            "revenue",
        )

    def test_lichsgfin_raw_label_matches_without_statement_type_filter(self):
        self.assertEqual(
            match_metric("(1) REVENUE FROM OPERATIONS"),
            "revenue",
        )

    def test_multi_digit_leading_prefix_still_matches(self):
        # The prefix regex must not be limited to single-digit line numbers.
        self.assertEqual(
            match_metric("(10) Net profit", "income_statement"),
            "net_profit",
        )

    def test_leading_prefix_and_trailing_note_ref_both_strip(self):
        # Leading numbered prefix and trailing single-letter note reference
        # are independent normalisations; both must apply in combination.
        self.assertEqual(
            match_metric("(1) Total equity (a)", "balance_sheet"),
            "total_equity",
        )


class TestLeadingNumberedPrefixExistingLabelsStillMatch(unittest.TestCase):
    """
    Non-regression: pre-existing labels with no leading numbered prefix must
    still resolve exactly as before.
    """

    def test_plain_revenue_from_operations_still_matches(self):
        self.assertEqual(
            match_metric("Revenue from operations", "income_statement"),
            "revenue",
        )

    def test_plain_total_assets_still_matches(self):
        self.assertEqual(
            match_metric("Total assets", "balance_sheet"),
            "total_assets",
        )

    def test_plain_net_profit_still_matches(self):
        self.assertEqual(
            match_metric("Net profit", "income_statement"),
            "net_profit",
        )

    def test_ofss_operating_cash_flow_label_still_matches(self):
        # Guards against interaction with the operating_cash_flow pattern
        # added in the previous canonical_metrics.py change.
        self.assertEqual(
            match_metric("Cash from operating activities", "cash_flow"),
            "operating_cash_flow",
        )


class TestLeadingNumberedPrefixNegativeCases(unittest.TestCase):
    """
    The new prefix-stripping must not widen matching: an unrelated numbered
    label must still return no match (or its own genuinely-excluded result),
    never fall through to an unrelated canonical metric such as revenue.
    """

    def test_numbered_unrelated_label_still_returns_none(self):
        self.assertIsNone(
            match_metric("(2) Depreciation and amortisation", "income_statement")
        )

    def test_numbered_excluded_metric_margin_still_returns_none(self):
        # "Net profit margin" is explicitly excluded for net_profit; the
        # exclude check must still fire after the new prefix is stripped,
        # and the label must not incorrectly fall through to revenue or any
        # other canonical metric.
        self.assertIsNone(
            match_metric("(1) Net profit margin", "income_statement")
        )

    def test_numbered_total_liabilities_and_equity_still_returns_none(self):
        self.assertIsNone(
            match_metric("(1) Total liabilities and equity", "balance_sheet")
        )

    def test_bare_number_in_middle_of_label_not_stripped(self):
        # The prefix regex is anchored at the start of the string -- a
        # number appearing elsewhere in the label (not a leading prefix)
        # must not be treated as a line-item number.
        self.assertIsNone(
            match_metric("Total Income (1+2)", "income_statement")
        )


class TestOperatingCashFlowNegativeCases(unittest.TestCase):
    """
    The new pattern must not widen matching to sibling cash-flow metrics or
    to superficially similar "cash from X activities" phrasings.
    """

    def test_investing_cash_flow_label_does_not_match_operating(self):
        self.assertEqual(
            match_metric("Net cash from investing activities", "cash_flow"),
            "investing_cash_flow",
        )

    def test_financing_cash_flow_label_does_not_match_operating(self):
        self.assertEqual(
            match_metric("Net cash from financing activities", "cash_flow"),
            "financing_cash_flow",
        )

    def test_cash_from_investing_activities_adversarial_case(self):
        # Same "cash from ___ activities" shape as the new operating_cash_flow
        # pattern, but for investing -- investing_cash_flow has no bare
        # "cash from X activities" pattern of its own, so this must resolve
        # to no match at all, and specifically must NOT fall through to
        # operating_cash_flow's new pattern.
        self.assertIsNone(match_metric("Cash from investing activities", "cash_flow"))

    def test_cash_from_financing_activities_adversarial_case(self):
        self.assertIsNone(match_metric("Cash from financing activities", "cash_flow"))

    def test_unrelated_label_still_returns_none(self):
        self.assertIsNone(match_metric("Depreciation and amortisation", "cash_flow"))


if __name__ == "__main__":
    unittest.main()
