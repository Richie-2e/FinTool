"""
extraction/llm/test_response_parser.py
Focused tests for parse_response()'s evidence-cap behavior (widened from
200 to 350 chars, see EVIDENCE_PRESERVATION_EXPERIMENT_REPORT.md and
FINAL_EVIDENCE_CAP_CONFIRMATION.md for the empirical validation). raw_label
remains capped at 200 -- untouched by this change, tested here to confirm
that explicitly.
"""

from __future__ import annotations

import unittest

from extraction.llm.response_parser import parse_response, _EVIDENCE_MAX_CHARS


def _raw_result(evidence, raw_label="Total assets"):
    return {
        "metrics": [
            {
                "canonical_name": "total_assets",
                "raw_label": raw_label,
                "value": 100.0,
                "unit": "INR crore",
                "year": 2024,
                "confidence": "high",
                "section_type": "consolidated",
                "evidence": evidence,
            }
        ]
    }


class TestEvidenceCap(unittest.TestCase):
    def test_cap_is_350(self):
        self.assertEqual(_EVIDENCE_MAX_CHARS, 350)

    def test_evidence_under_350_preserved_in_full(self):
        ev = "x" * 300
        candidates = parse_response(_raw_result(ev), "balance_sheet", "doc1", 1, "consolidated")
        self.assertEqual(candidates[0].evidence, ev)

    def test_evidence_exactly_350_preserved_in_full(self):
        ev = "x" * 350
        candidates = parse_response(_raw_result(ev), "balance_sheet", "doc1", 1, "consolidated")
        self.assertEqual(candidates[0].evidence, ev)
        self.assertEqual(len(candidates[0].evidence), 350)

    def test_evidence_over_350_truncated_at_350(self):
        ev = "x" * 500
        candidates = parse_response(_raw_result(ev), "balance_sheet", "doc1", 1, "consolidated")
        self.assertEqual(len(candidates[0].evidence), 350)
        self.assertEqual(candidates[0].evidence, "x" * 350)

    def test_evidence_between_200_and_350_no_longer_truncated(self):
        # The exact regression this change targets: evidence in the 201-350
        # range used to be silently cut to 200 chars; it must now survive
        # in full.
        ev = "y" * 265
        candidates = parse_response(_raw_result(ev), "balance_sheet", "doc1", 1, "consolidated")
        self.assertEqual(candidates[0].evidence, ev)

    def test_raw_label_cap_unchanged_at_200(self):
        label = "z" * 300
        candidates = parse_response(_raw_result("short evidence", raw_label=label),
                                     "balance_sheet", "doc1", 1, "consolidated")
        self.assertEqual(len(candidates[0].raw_label), 200)


if __name__ == "__main__":
    unittest.main()
