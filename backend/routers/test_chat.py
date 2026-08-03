"""
backend/routers/test_chat.py
Unit tests for chat.py's _extract_metrics_used(), focused on FV2-7's
extension to also recognize raw ResolvedMetric mentions (Option B from the
FV2-7 design review: keep API response metadata consistent with what the
answer actually cites).

Uses stdlib unittest and lightweight SimpleNamespace fakes -- same rationale
as backend/services/test_rag_service.py.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.routers.chat import _extract_metrics_used


def _resolved_row(metric_name, value, year):
    return SimpleNamespace(metric_name=metric_name, value=value, year=year)


def _computed_row(year, **ratios):
    defaults = {"current_ratio": None, "debt_to_equity": None, "profit_margin": None}
    defaults.update(ratios)
    return SimpleNamespace(year=year, **defaults)


class TestExtractMetricsUsed(unittest.TestCase):

    def test_recognizes_raw_metric_mention_in_answer(self):
        resolved = [_resolved_row("revenue", 68468.0, 2025)]
        used = _extract_metrics_used(
            "Revenue in 2025 was 68,468 million.", "What was revenue?", [], resolved,
        )
        self.assertEqual(len(used), 1)
        self.assertEqual(used[0].metric, "revenue")
        self.assertEqual(used[0].year, 2025)
        self.assertEqual(used[0].value, 68468.0)

    def test_still_recognizes_ratio_mention_unchanged(self):
        # Non-regression: existing ratio-mention behavior must be unaffected
        # by the new resolved_rows parameter.
        computed = [_computed_row(2025, current_ratio=6.9040)]
        used = _extract_metrics_used(
            "The current ratio was 6.90.", "What is the current ratio?", computed, [],
        )
        self.assertEqual(len(used), 1)
        self.assertEqual(used[0].metric, "current_ratio")
        self.assertEqual(used[0].value, 6.9040)

    def test_combines_ratio_and_raw_mentions_in_one_call(self):
        computed = [_computed_row(2025, current_ratio=6.9040)]
        resolved = [_resolved_row("revenue", 68468.0, 2025)]
        used = _extract_metrics_used(
            "Revenue was 68,468 and the current ratio was 6.90.",
            "How is the company doing?",
            computed, resolved,
        )
        metrics = {m.metric for m in used}
        self.assertEqual(metrics, {"revenue", "current_ratio"})

    def test_returns_empty_when_nothing_mentioned(self):
        computed = [_computed_row(2025, current_ratio=6.9040)]
        resolved = [_resolved_row("revenue", 68468.0, 2025)]
        used = _extract_metrics_used(
            "This information is not available in the uploaded document.",
            "What is the dividend per share?",
            computed, resolved,
        )
        self.assertEqual(used, [])

    def test_ignores_resolved_row_with_null_year(self):
        resolved = [_resolved_row("revenue", 68468.0, None)]
        used = _extract_metrics_used("Revenue was mentioned.", "revenue?", [], resolved)
        self.assertEqual(used, [])

    def test_raw_mention_phrase_does_not_require_ratio_mention(self):
        # A question that only touches a raw metric (no ratio phrase at all)
        # must not accidentally require both dicts to have a hit.
        resolved = [_resolved_row("total_equity", 83624.0, 2025)]
        used = _extract_metrics_used(
            "Total equity was 83,624 million.", "What is total equity?", [], resolved,
        )
        self.assertEqual(len(used), 1)
        self.assertEqual(used[0].metric, "total_equity")


if __name__ == "__main__":
    unittest.main()
