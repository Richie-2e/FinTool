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
