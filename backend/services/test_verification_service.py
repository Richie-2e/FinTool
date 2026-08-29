"""
backend/services/test_verification_service.py
Unit tests for verification_service.py's worst-case verification_state
aggregation. Uses a real in-memory SQLite session (matching db.py's own
SQLAlchemy setup) rather than mocking the ORM, and a temp provenance.json
file (the same format numerical_module._build_provenance() writes and
backend/routers/explain.py already reads).

Run with:
    python -m unittest backend.services.test_verification_service -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.db import Base, ResolvedMetric
from backend.services.verification_service import (
    compute_ratio_verification_states,
    compute_risk_verification_states,
)


class _VerificationServiceTestBase(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.output_dir = self.tmpdir.name

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def _add_metric(self, name: str, year: int, state, value=100.0):
        self.db.add(ResolvedMetric(
            doc_id="doc1", metric_name=name, value=value, year=year,
            verification_state=state,
        ))
        self.db.commit()

    def _write_provenance(self, entries: list[dict]):
        path = Path(self.output_dir) / "doc1_provenance.json"
        path.write_text(json.dumps(entries))


class TestComputeRatioVerificationStates(_VerificationServiceTestBase):
    def test_both_inputs_verified_yields_verified(self):
        self._add_metric("revenue", 2025, "VERIFIED")
        self._add_metric("net_profit", 2025, "VERIFIED")
        self._write_provenance([{
            "ratio_name": "profit_margin", "numerator": "net_profit",
            "denominator": "revenue", "result": 0.5, "year": 2025,
        }])
        states = compute_ratio_verification_states("doc1", self.output_dir, self.db)
        self.assertEqual(states[("profit_margin", 2025)], "VERIFIED")

    def test_one_input_needs_review_yields_needs_review(self):
        self._add_metric("revenue", 2025, "VERIFIED")
        self._add_metric("operating_cash_flow", 2025, "NEEDS_REVIEW")
        self._write_provenance([{
            "ratio_name": "ocf_to_revenue", "numerator": "operating_cash_flow",
            "denominator": "revenue", "result": 0.4, "year": 2025,
        }])
        states = compute_ratio_verification_states("doc1", self.output_dir, self.db)
        self.assertEqual(states[("ocf_to_revenue", 2025)], "NEEDS_REVIEW")

    def test_null_result_never_yields_verified_even_if_one_input_exists(self):
        # Regression: confirmed live during Phase B/C testing -- OFSS
        # profit_margin had a provenance entry (numerator net_profit,
        # denominator revenue) with result=None because net_profit was
        # never resolved at all. Before the fix, this silently produced
        # "VERIFIED" (aggregating over only the inputs that DO exist,
        # dropping the missing one instead of treating it as blocking).
        self._add_metric("revenue", 2025, "VERIFIED")
        # net_profit deliberately NOT added -- it was never resolved.
        self._write_provenance([{
            "ratio_name": "profit_margin", "numerator": "net_profit",
            "denominator": "revenue", "result": None, "year": 2025,
        }])
        states = compute_ratio_verification_states("doc1", self.output_dir, self.db)
        self.assertIsNone(states[("profit_margin", 2025)])

    def test_no_provenance_file_yields_empty_dict(self):
        states = compute_ratio_verification_states("doc1", self.output_dir, self.db)
        self.assertEqual(states, {})

    def test_neither_input_checked_yields_none_not_verified(self):
        self._add_metric("revenue", 2025, None)
        self._add_metric("net_profit", 2025, None)
        self._write_provenance([{
            "ratio_name": "profit_margin", "numerator": "net_profit",
            "denominator": "revenue", "result": 0.5, "year": 2025,
        }])
        states = compute_ratio_verification_states("doc1", self.output_dir, self.db)
        self.assertIsNone(states[("profit_margin", 2025)])


class TestComputeRiskVerificationStates(_VerificationServiceTestBase):
    def test_cashflow_risk_mirrors_operating_cash_flow_metric_state_directly(self):
        # cashflow_risk is driven by a raw resolved metric, not a ratio --
        # no provenance entry needed for this one.
        self._add_metric("operating_cash_flow", 2025, "NEEDS_REVIEW")
        states = compute_risk_verification_states("doc1", self.output_dir, self.db)
        self.assertEqual(states[("cashflow", 2025)], "NEEDS_REVIEW")

    def test_overall_is_worst_case_across_all_four_categories(self):
        self._add_metric("operating_cash_flow", 2025, "NEEDS_REVIEW")
        self._add_metric("current_assets", 2025, "VERIFIED")
        self._add_metric("current_liabilities", 2025, "VERIFIED")
        self._write_provenance([{
            "ratio_name": "current_ratio", "numerator": "current_assets",
            "denominator": "current_liabilities", "result": 2.0, "year": 2025,
        }])
        states = compute_risk_verification_states("doc1", self.output_dir, self.db)
        self.assertEqual(states[("liquidity", 2025)], "VERIFIED")
        self.assertEqual(states[("cashflow", 2025)], "NEEDS_REVIEW")
        self.assertEqual(states[("overall", 2025)], "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()
