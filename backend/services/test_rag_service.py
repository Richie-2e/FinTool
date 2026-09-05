"""
backend/services/test_rag_service.py
Unit tests for rag_service.py's build_grounded_prompt(), focused on FV2-7
(the RESOLVED METRICS section).

Uses stdlib unittest and lightweight SimpleNamespace fakes instead of real
SQLAlchemy rows or a live DB -- build_grounded_prompt only ever reads
attributes off its row arguments (getattr for ComputedMetric,
.metric_name/.value/.year/.unit/.page_no for ResolvedMetric), so a fake
object with the right attributes is a faithful, dependency-free substitute.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from google.genai import errors as genai_errors

from backend.exceptions import APIError
from backend.services.rag_service import build_grounded_prompt, call_llm


def _resolved_row(metric_name, value, year, unit="INR million", page_no=1):
    return SimpleNamespace(metric_name=metric_name, value=value, year=year, unit=unit, page_no=page_no)


def _computed_row(year, **ratios):
    defaults = {
        "current_ratio": None, "cash_ratio": None, "debt_to_equity": None,
        "debt_ratio": None, "interest_coverage": None, "profit_margin": None,
        "operating_margin": None, "gross_margin": None, "asset_turnover": None,
        "ocf_to_revenue": None, "free_cash_flow": None, "yoy_revenue_growth": None,
        "yoy_profit_growth": None, "total_debt": None,
        "liquidity_risk": None, "debt_risk": None, "profitability_risk": None,
        "cashflow_risk": None, "overall_risk": None,
    }
    defaults.update(ratios)
    return SimpleNamespace(year=year, **defaults)


class TestResolvedMetricsSection(unittest.TestCase):

    def test_grouped_by_statement_income_then_balance_then_cash_flow(self):
        # Real OFSS-shaped data (post-FV2-2 resolved_metrics.csv), deliberately
        # passed in database/insertion order (capex, cash_and_equivalents,
        # current_assets, ... -- not statement order) to prove the function
        # itself does the regrouping, not an artifact of input order.
        rows = [
            _resolved_row("capex", -352.0, 2025, page_no=117),
            _resolved_row("cash_and_equivalents", 12142.0, 2025, page_no=61),
            _resolved_row("current_assets", 79458.0, 2025, page_no=61),
            _resolved_row("current_liabilities", 11509.0, 2025, page_no=61),
            _resolved_row("interest_expense", 281.0, 2024, page_no=62),
            _resolved_row("interest_expense", 5.0, 2025, page_no=62),
            _resolved_row("investing_cash_flow", -24526.0, 2025, page_no=117),
            _resolved_row("revenue", 63730.0, 2024, page_no=62),
            _resolved_row("revenue", 68468.0, 2025, page_no=62),
            _resolved_row("total_equity", 83624.0, 2025, page_no=61),
        ]
        prompt = build_grounded_prompt("q", [], [], rows, None)

        income_idx = prompt.index("Income Statement:")
        balance_idx = prompt.index("Balance Sheet:")
        cashflow_idx = prompt.index("Cash Flow:")
        self.assertLess(income_idx, balance_idx)
        self.assertLess(balance_idx, cashflow_idx)

    def test_same_metric_years_are_adjacent_not_scattered(self):
        rows = [
            _resolved_row("interest_expense", 281.0, 2024),
            _resolved_row("revenue", 63730.0, 2024),
            _resolved_row("revenue", 68468.0, 2025),
            _resolved_row("interest_expense", 5.0, 2025),
        ]
        prompt = build_grounded_prompt("q", [], [], rows, None)
        lines = [l for l in prompt.splitlines() if "Revenue (" in l or "Interest Expense (" in l]
        # Revenue 2024 and 2025 must be consecutive lines, not separated by
        # Interest Expense -- this is the whole point of metric-outer/year-inner
        # grouping (see rag_service.py's _RAW_METRIC_LABELS docstring).
        self.assertEqual(lines[0].split("(")[0].strip(), "Revenue")
        self.assertEqual(lines[1].split("(")[0].strip(), "Revenue")

    def test_missing_metrics_are_omitted_not_shown_as_unavailable(self):
        rows = [_resolved_row("revenue", 68468.0, 2025)]
        prompt = build_grounded_prompt("q", [], [], rows, None)
        self.assertNotIn("Net Profit", prompt)
        self.assertNotIn("not available", prompt.split("RESOLVED METRICS")[1].split("COMPUTED METRICS")[0])

    def test_group_header_omitted_when_group_has_no_data(self):
        # Only a cash-flow metric present -- Income Statement / Balance Sheet
        # headers must not appear at all.
        rows = [_resolved_row("capex", -352.0, 2025)]
        prompt = build_grounded_prompt("q", [], [], rows, None)
        self.assertNotIn("Income Statement:", prompt)
        self.assertNotIn("Balance Sheet:", prompt)
        self.assertIn("Cash Flow:", prompt)

    def test_empty_resolved_metrics_shows_fallback_text(self):
        prompt = build_grounded_prompt("q", [], [], [], None)
        self.assertIn("No resolved metrics available.", prompt)

    def test_value_formatting_comma_grouped_two_decimals(self):
        rows = [_resolved_row("investing_cash_flow", -24526.0, 2025, unit="INR million", page_no=117)]
        prompt = build_grounded_prompt("q", [], [], rows, None)
        self.assertIn("Investing Cash Flow (2025): -24,526.00 INR million (page 117)", prompt)

    def test_null_unit_omits_unit_suffix(self):
        rows = [_resolved_row("revenue", 100.0, 2025, unit=None, page_no=5)]
        prompt = build_grounded_prompt("q", [], [], rows, None)
        self.assertIn("Revenue (2025): 100.00 (page 5)", prompt)
        self.assertNotIn("None", prompt)

    def test_null_page_no_omits_page_suffix(self):
        rows = [_resolved_row("revenue", 100.0, 2025, unit="INR million", page_no=None)]
        prompt = build_grounded_prompt("q", [], [], rows, None)
        self.assertIn("Revenue (2025): 100.00 INR million\n", prompt)
        self.assertNotIn("(page None)", prompt)

    def test_row_with_null_value_is_skipped(self):
        rows = [_resolved_row("revenue", None, 2025)]
        prompt = build_grounded_prompt("q", [], [], rows, None)
        self.assertNotIn("Revenue", prompt)

    def test_row_with_null_year_is_skipped(self):
        rows = [_resolved_row("revenue", 100.0, None)]
        prompt = build_grounded_prompt("q", [], [], rows, None)
        self.assertNotIn("Revenue", prompt)

    def test_resolved_section_appears_before_computed_section(self):
        rows = [_resolved_row("revenue", 100.0, 2025)]
        prompt = build_grounded_prompt("q", [], [_computed_row(2025, current_ratio=1.5)], rows, None)
        self.assertLess(prompt.index("RESOLVED METRICS"), prompt.index("COMPUTED METRICS"))

    def test_computed_metrics_section_unchanged_by_new_signature(self):
        # Non-regression: ratios and risk labels still render exactly as
        # before, now that build_grounded_prompt takes an extra parameter.
        computed = [_computed_row(2025, current_ratio=6.903988183161005, liquidity_risk="Low", overall_risk="Low")]
        prompt = build_grounded_prompt("q", [], computed, [], None)
        self.assertIn("Current Ratio (2025): 6.9040", prompt)
        self.assertIn("Liquidity Risk (2025): Low", prompt)


def _client_error(code: int, message: str = "client error") -> genai_errors.ClientError:
    return genai_errors.ClientError(code, {"error": {"code": code, "message": message, "status": "ERROR"}})


def _server_error(code: int = 503, message: str = "high demand") -> genai_errors.ServerError:
    return genai_errors.ServerError(code, {"error": {"code": code, "message": message, "status": "UNAVAILABLE"}})


class TestCallLLMRetry(unittest.TestCase):
    """
    Unit tests for call_llm()'s bounded retry/backoff on transient Gemini
    failures (503/5xx, 429) vs. immediate failure on permanent ones
    (400/401/403/404). All Gemini calls are mocked -- none of these tests hit
    the real API. time.sleep is mocked so tests don't actually wait.
    """

    @patch("backend.services.rag_service.GEMINI_API_KEY", "test-key")
    @patch("backend.services.rag_service.time.sleep")
    @patch("backend.services.rag_service.genai.Client")
    def test_immediate_success_makes_exactly_one_call(self, mock_client_cls, mock_sleep):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = SimpleNamespace(text="The answer.")
        mock_client_cls.return_value = mock_client

        result = call_llm("prompt")

        self.assertEqual(result, "The answer.")
        self.assertEqual(mock_client.models.generate_content.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("backend.services.rag_service.GEMINI_API_KEY", "test-key")
    @patch("backend.services.rag_service.time.sleep")
    @patch("backend.services.rag_service.genai.Client")
    def test_503_then_success_retries_and_returns_answer(self, mock_client_cls, mock_sleep):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            _server_error(503, "high demand"),
            SimpleNamespace(text="Recovered answer."),
        ]
        mock_client_cls.return_value = mock_client

        result = call_llm("prompt")

        self.assertEqual(result, "Recovered answer.")
        self.assertEqual(mock_client.models.generate_content.call_count, 2)
        mock_sleep.assert_called_once()  # one wait, between attempt 1 and attempt 2

    @patch("backend.services.rag_service.GEMINI_API_KEY", "test-key")
    @patch("backend.services.rag_service.time.sleep")
    @patch("backend.services.rag_service.genai.Client")
    def test_repeated_503_stops_at_max_attempts_no_infinite_loop(self, mock_client_cls, mock_sleep):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            _server_error(503), _server_error(503), _server_error(503),
        ]
        mock_client_cls.return_value = mock_client

        with self.assertRaises(APIError) as ctx:
            call_llm("prompt")

        # Exactly 3 attempts (the configured max) -- not 4, not infinite.
        self.assertEqual(mock_client.models.generate_content.call_count, 3)
        # Waits happen only *between* attempts (1->2, 2->3), never after the
        # final failed attempt.
        self.assertEqual(mock_sleep.call_count, 2)
        # The original transient Gemini failure must remain identifiable,
        # and must be classified distinctly from a generic internal error.
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.error_code, "LLM_UNAVAILABLE")
        self.assertIn("503", ctx.exception.detail)

    @patch("backend.services.rag_service.GEMINI_API_KEY", "test-key")
    @patch("backend.services.rag_service.time.sleep")
    @patch("backend.services.rag_service.genai.Client")
    def test_401_is_not_retried(self, mock_client_cls, mock_sleep):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [_client_error(401, "invalid key")]
        mock_client_cls.return_value = mock_client

        with self.assertRaises(RuntimeError) as ctx:
            call_llm("prompt")

        self.assertEqual(mock_client.models.generate_content.call_count, 1)
        mock_sleep.assert_not_called()
        self.assertIn("401", str(ctx.exception))

    @patch("backend.services.rag_service.GEMINI_API_KEY", "test-key")
    @patch("backend.services.rag_service.time.sleep")
    @patch("backend.services.rag_service.genai.Client")
    def test_403_is_not_retried(self, mock_client_cls, mock_sleep):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [_client_error(403, "forbidden")]
        mock_client_cls.return_value = mock_client

        with self.assertRaises(RuntimeError) as ctx:
            call_llm("prompt")

        self.assertEqual(mock_client.models.generate_content.call_count, 1)
        mock_sleep.assert_not_called()
        self.assertIn("403", str(ctx.exception))

    @patch("backend.services.rag_service.GEMINI_API_KEY", "test-key")
    @patch("backend.services.rag_service.time.sleep")
    @patch("backend.services.rag_service.genai.Client")
    def test_404_is_not_retried(self, mock_client_cls, mock_sleep):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [_client_error(404, "not found")]
        mock_client_cls.return_value = mock_client

        with self.assertRaises(RuntimeError) as ctx:
            call_llm("prompt")

        self.assertEqual(mock_client.models.generate_content.call_count, 1)
        mock_sleep.assert_not_called()
        self.assertIn("404", str(ctx.exception))

    @patch("backend.services.rag_service.GEMINI_API_KEY", "test-key")
    @patch("backend.services.rag_service.time.sleep")
    @patch("backend.services.rag_service.genai.Client")
    def test_429_is_retried_per_policy(self, mock_client_cls, mock_sleep):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            _client_error(429, "rate limited"),
            SimpleNamespace(text="Recovered after rate limit."),
        ]
        mock_client_cls.return_value = mock_client

        result = call_llm("prompt")

        self.assertEqual(result, "Recovered after rate limit.")
        self.assertEqual(mock_client.models.generate_content.call_count, 2)
        mock_sleep.assert_called_once()

    @patch("backend.services.rag_service.GEMINI_API_KEY", "")
    @patch("backend.services.rag_service.time.sleep")
    @patch("backend.services.rag_service.genai.Client")
    def test_missing_api_key_still_raises_immediately_unchanged(self, mock_client_cls, mock_sleep):
        # Non-regression: the pre-existing missing-key guard must still fire
        # before any Gemini call is attempted, unaffected by the retry loop.
        with self.assertRaises(RuntimeError) as ctx:
            call_llm("prompt")

        self.assertIn("GEMINI_API_KEY is not set", str(ctx.exception))
        mock_client_cls.assert_not_called()
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
