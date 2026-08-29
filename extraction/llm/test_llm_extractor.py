"""
extraction/llm/test_llm_extractor.py
Focused unit tests for the cash_flow decomposition fallback's pure merge
logic (_merge_fallback_pages, _values_close), added alongside the fallback
implementation (see CASH_FLOW_FALLBACK_POLICY_EXPERIMENT.md for the
validated policy this implements). No Ollama call, no live PDF -- exercises
the merge function directly against constructed CandidateMetric objects,
mirroring the exact policy already validated in
decomposition_experiment/reprocess_arm_c.py::merge_pages.
"""

from __future__ import annotations

import unittest

from extraction.llm.llm_extractor import _merge_fallback_pages, _values_close
from extraction.metric_extractor import CandidateMetric


def _cm(metric_name, value, year, verification_state=None, page_no=1):
    return CandidateMetric(
        doc_id="test", metric_name=metric_name, raw_label="x", value=value,
        unit="INR crore", year=year, page_no=page_no, statement_type="cash_flow",
        section_type="unknown", confidence="high", source="llm_extraction",
        verification_state=verification_state,
    )


class TestValuesClose(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(_values_close(100.0, 100.0))

    def test_within_tolerance(self):
        self.assertTrue(_values_close(100.0, 100.5))

    def test_outside_tolerance(self):
        self.assertFalse(_values_close(100.0, 110.0))

    def test_none_is_never_close(self):
        self.assertFalse(_values_close(None, 100.0))
        self.assertFalse(_values_close(100.0, None))

    def test_zero_denominator_not_close_unless_exact(self):
        self.assertFalse(_values_close(1.0, 0.0))
        self.assertTrue(_values_close(0.0, 0.0))


class TestMergeFallbackPages(unittest.TestCase):
    def test_no_overlap_keeps_both(self):
        page1 = ([_cm("operating_cash_flow", 100.0, 2024, "VERIFIED")], [])
        page2 = ([_cm("financing_cash_flow", -50.0, 2024, "VERIFIED")], [])
        merged, diags = _merge_fallback_pages([page1, page2])
        self.assertEqual(len(merged), 2)
        self.assertEqual(diags, [])

    def test_matching_duplicate_prefers_verified(self):
        page1 = ([_cm("operating_cash_flow", 100.0, 2024, "NEEDS_REVIEW")], [])
        page2 = ([_cm("operating_cash_flow", 100.0, 2024, "VERIFIED")], [])
        merged, _ = _merge_fallback_pages([page1, page2])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].verification_state, "VERIFIED")

    def test_matching_duplicate_first_verified_not_overwritten_by_needs_review(self):
        page1 = ([_cm("operating_cash_flow", 100.0, 2024, "VERIFIED")], [])
        page2 = ([_cm("operating_cash_flow", 100.0, 2024, "NEEDS_REVIEW")], [])
        merged, _ = _merge_fallback_pages([page1, page2])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].verification_state, "VERIFIED")

    def test_conflicting_value_keeps_first_seen_drops_second(self):
        # Documented, deterministic behavior for a case never observed in
        # the validated corpus (0 conflicts across all 5 documents).
        page1 = ([_cm("operating_cash_flow", 100.0, 2024, "VERIFIED")], [])
        page2 = ([_cm("operating_cash_flow", 999.0, 2024, "VERIFIED")], [])
        merged, _ = _merge_fallback_pages([page1, page2])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].value, 100.0)

    def test_diagnostics_concatenated_across_pages(self):
        from extraction.llm.candidate_validator import RejectionRecord
        r1 = RejectionRecord(doc_id="test", metric_name="a", raw_label="x", value=1.0,
                              unit="INR crore", year=2024, statement_type="cash_flow",
                              evidence="", l2_passed=False, l3_passed=True, l4_passed=True,
                              l5_passed=True, l5_degraded=False,
                              rejection_reasons=["raw_label_mismatch"])
        r2 = RejectionRecord(doc_id="test", metric_name="b", raw_label="y", value=2.0,
                              unit="INR crore", year=2024, statement_type="cash_flow",
                              evidence="", l2_passed=False, l3_passed=True, l4_passed=True,
                              l5_passed=True, l5_degraded=False,
                              rejection_reasons=["raw_label_mismatch"])
        merged, diags = _merge_fallback_pages([([], [r1]), ([], [r2])])
        self.assertEqual(merged, [])
        self.assertEqual(len(diags), 2)

    def test_empty_input_returns_empty(self):
        merged, diags = _merge_fallback_pages([])
        self.assertEqual(merged, [])
        self.assertEqual(diags, [])


if __name__ == "__main__":
    unittest.main()
