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

import httpx
import openai

from backend.exceptions import APIError
from backend.services import rag_service
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


def _status_error(cls, code: int, message: str = "error"):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(code, request=request)
    return cls(message, response=response, body=None)


def _ok(text="The answer.", status="completed"):
    return SimpleNamespace(output_text=text, status=status)


_PATCH_KEY = patch("backend.services.rag_service.OPENAI_API_KEY", "test-key")
_PATCH_CLIENT = "backend.services.rag_service.OpenAI"


class TestCallLLM(unittest.TestCase):
    """
    Unit tests for call_llm() against the OpenAI Responses API. All OpenAI
    calls are mocked -- none of these tests hit the real API. Retry/backoff
    for transient failures is the SDK's job (max_retries on the client), so
    these tests assert the client is configured with a bounded policy and
    that call_llm() maps the SDK's *final* exceptions correctly, without a
    second application-level retry loop.
    """

    def _run(self, mock_client_cls, result=None, error=None, history=None):
        mock_client = MagicMock()
        if error is not None:
            mock_client.responses.create.side_effect = error
        else:
            mock_client.responses.create.return_value = result
        mock_client_cls.return_value = mock_client
        with _PATCH_KEY:
            return mock_client, call_llm("prompt", conversation_history=history)

    @patch(_PATCH_CLIENT)
    def test_success_returns_output_text_with_one_call(self, mock_client_cls):
        mock_client, result = self._run(mock_client_cls, result=_ok("The answer."))
        self.assertEqual(result, "The answer.")
        self.assertEqual(mock_client.responses.create.call_count, 1)

    @patch(_PATCH_CLIENT)
    def test_client_has_bounded_timeout_and_retries(self, mock_client_cls):
        self._run(mock_client_cls, result=_ok())
        kwargs = mock_client_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertGreater(kwargs["timeout"], 0)
        self.assertLessEqual(kwargs["timeout"], 120)
        self.assertGreaterEqual(kwargs["max_retries"], 0)
        self.assertLessEqual(kwargs["max_retries"], 3)

    @patch(_PATCH_CLIENT)
    def test_request_uses_configured_model_and_reasoning_none(self, mock_client_cls):
        mock_client, _ = self._run(mock_client_cls, result=_ok())
        kwargs = mock_client.responses.create.call_args.kwargs
        self.assertEqual(kwargs["model"], rag_service.LLM_MODEL)
        self.assertFalse(kwargs["model"].lower().startswith("gemini"))
        self.assertEqual(kwargs["reasoning"], {"effort": "none"})
        self.assertGreater(kwargs["max_output_tokens"], 0)

    @patch(_PATCH_CLIENT)
    def test_history_mapped_to_turns_with_prompt_last(self, mock_client_cls):
        history = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
        mock_client, _ = self._run(mock_client_cls, result=_ok(), history=history)
        self.assertEqual(
            mock_client.responses.create.call_args.kwargs["input"],
            [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "prompt"},
            ],
        )

    @patch(_PATCH_CLIENT)
    def test_no_history_sends_only_prompt(self, mock_client_cls):
        mock_client, _ = self._run(mock_client_cls, result=_ok())
        self.assertEqual(
            mock_client.responses.create.call_args.kwargs["input"],
            [{"role": "user", "content": "prompt"}],
        )

    @patch(_PATCH_CLIENT)
    def test_exhausted_503_maps_to_llm_unavailable_without_extra_retry(self, mock_client_cls):
        err = _status_error(openai.InternalServerError, 503, "overloaded")
        mock_client = MagicMock()
        mock_client.responses.create.side_effect = err
        mock_client_cls.return_value = mock_client
        with _PATCH_KEY, self.assertRaises(APIError) as ctx:
            call_llm("prompt")
        # SDK owns retries; call_llm() itself calls create() exactly once.
        self.assertEqual(mock_client.responses.create.call_count, 1)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.error_code, "LLM_UNAVAILABLE")
        self.assertIn("503", ctx.exception.detail)

    @patch(_PATCH_CLIENT)
    def test_exhausted_429_maps_to_llm_unavailable(self, mock_client_cls):
        err = _status_error(openai.RateLimitError, 429, "rate limited")
        with _PATCH_KEY, self.assertRaises(APIError) as ctx:
            mock_client_cls.return_value = MagicMock(**{"responses.create.side_effect": err})
            call_llm("prompt")
        self.assertEqual(ctx.exception.error_code, "LLM_UNAVAILABLE")

    @patch(_PATCH_CLIENT)
    def test_timeout_and_connection_errors_map_to_llm_unavailable(self, mock_client_cls):
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        for err in (openai.APITimeoutError(request=request), openai.APIConnectionError(request=request)):
            mock_client_cls.return_value = MagicMock(**{"responses.create.side_effect": err})
            with _PATCH_KEY, self.assertRaises(APIError) as ctx:
                call_llm("prompt")
            self.assertEqual(ctx.exception.status_code, 503)
            self.assertEqual(ctx.exception.error_code, "LLM_UNAVAILABLE")

    @patch(_PATCH_CLIENT)
    def test_auth_and_permission_errors_raise_runtimeerror_without_key_echo(self, mock_client_cls):
        for cls, code in ((openai.AuthenticationError, 401), (openai.PermissionDeniedError, 403)):
            err = _status_error(cls, code, "Incorrect API key provided: sk-SECRETFRAGMENT")
            mock_client = MagicMock(**{"responses.create.side_effect": err})
            mock_client_cls.return_value = mock_client
            with _PATCH_KEY, self.assertRaises(RuntimeError) as ctx:
                call_llm("prompt")
            self.assertEqual(mock_client.responses.create.call_count, 1)
            self.assertIn(str(code), str(ctx.exception))
            self.assertNotIn("SECRETFRAGMENT", str(ctx.exception))
            self.assertNotIsInstance(ctx.exception, APIError)

    @patch(_PATCH_CLIENT)
    def test_400_and_404_are_permanent_runtimeerrors(self, mock_client_cls):
        for cls, code in ((openai.BadRequestError, 400), (openai.NotFoundError, 404)):
            err = _status_error(cls, code, "bad request")
            mock_client_cls.return_value = MagicMock(**{"responses.create.side_effect": err})
            with _PATCH_KEY, self.assertRaises(RuntimeError) as ctx:
                call_llm("prompt")
            self.assertIn(str(code), str(ctx.exception))

    @patch(_PATCH_CLIENT)
    def test_empty_output_raises_instead_of_returning_empty_string(self, mock_client_cls):
        for bad in (_ok(""), _ok("   \n"), _ok(None)):
            mock_client_cls.return_value = MagicMock(**{"responses.create.return_value": bad})
            with _PATCH_KEY, self.assertRaises(APIError) as ctx:
                call_llm("prompt")
            self.assertEqual(ctx.exception.error_code, "LLM_EMPTY_RESPONSE")

    @patch(_PATCH_CLIENT)
    def test_incomplete_response_raises_even_with_partial_text(self, mock_client_cls):
        mock_client_cls.return_value = MagicMock(
            **{"responses.create.return_value": _ok("Partial ans", status="incomplete")}
        )
        with _PATCH_KEY, self.assertRaises(APIError) as ctx:
            call_llm("prompt")
        self.assertEqual(ctx.exception.error_code, "LLM_EMPTY_RESPONSE")

    @patch("backend.services.rag_service.OPENAI_API_KEY", "")
    @patch(_PATCH_CLIENT)
    def test_missing_api_key_raises_before_any_call(self, mock_client_cls):
        with self.assertRaises(RuntimeError) as ctx:
            call_llm("prompt")
        self.assertEqual(str(ctx.exception), "OPENAI_API_KEY is not configured.")
        mock_client_cls.assert_not_called()


class TestLLMModelConfig(unittest.TestCase):
    def test_gemini_model_name_never_reaches_openai(self):
        from backend import config
        with patch.dict("os.environ", {"LLM_MODEL": "gemini-flash-latest"}):
            self.assertEqual(config._resolve_llm_model(), "gpt-6-luna")
        with patch.dict("os.environ", {"LLM_MODEL": ""}):
            self.assertEqual(config._resolve_llm_model(), "gpt-6-luna")
        with patch.dict("os.environ", {"LLM_MODEL": "gpt-x"}):
            self.assertEqual(config._resolve_llm_model(), "gpt-x")


if __name__ == "__main__":
    unittest.main()
