"""
extraction/llm/response_parser.py
Converts the raw JSON dict returned by Ollama into CandidateMetric objects.

Responsibilities:
  - Validate that canonical_name is in the known metric registry
  - Handle values that the LLM returns as strings instead of floats
  - Validate year is in [2000, 2100]; drop metrics with invalid years
  - Set source = "llm_extraction" on every metric
  - Prefer section_type from the page classifier over the LLM's guess
  - Reuse parse_value() from metric_extractor rather than duplicating it

No Ollama or network dependency — this module is unit-testable in isolation
by passing any dict that matches the LLM output schema.
"""

from __future__ import annotations

from typing import Optional

from extraction.canonical_metrics import CANONICAL_METRICS
from extraction.metric_extractor import CandidateMetric, parse_value

# Confidence values the LLM is instructed to use
_VALID_CONFIDENCE = {"high", "medium", "low"}

# Section types the LLM is instructed to use
_VALID_SECTION_TYPES = {"consolidated", "standalone", "unknown"}


def parse_response(
    raw_result: dict,
    stmt_type: str,
    doc_id: str,
    page_no: Optional[int],
    section_type: str,
) -> list[CandidateMetric]:
    """
    Convert the parsed LLM JSON response into a list of CandidateMetric objects.

    Parameters
    ----------
    raw_result : dict
        The parsed JSON dict from ollama_client.call_ollama().
        Expected structure: {"metrics": [...], "notes": "..."}
    stmt_type : str
        The statement type this call was made for. Used to scope
        canonical name validation and fill CandidateMetric.statement_type.
    doc_id : str
        Document identifier, forwarded to CandidateMetric.
    page_no : int or None
        The primary page number for this call. Used as CandidateMetric.page_no.
    section_type : str
        Section type from the page classifier ("consolidated" | "standalone" |
        "unknown"). Takes priority over whatever the LLM reported.

    Returns
    -------
    list[CandidateMetric]
        One CandidateMetric per valid metric entry in the LLM response.
        Invalid entries (unknown canonical name, missing value, bad year) are
        silently dropped — extraction continues rather than raising.
    """
    raw_metrics: list[dict] = raw_result.get("metrics", [])
    candidates: list[CandidateMetric] = []

    for entry in raw_metrics:
        canonical_name = _normalise_name(entry.get("canonical_name", ""))
        if canonical_name not in CANONICAL_METRICS:
            continue  # LLM invented a name or returned a typo

        value = _coerce_value(entry.get("value"))
        if value is None:
            continue  # null metrics have no use downstream

        year = _coerce_year(entry.get("year"))
        if year is None:
            continue  # year is required for resolution and pivot

        confidence = entry.get("confidence", "low")
        if confidence not in _VALID_CONFIDENCE:
            confidence = "low"

        # Use the classifier's section_type — it reads the actual page header.
        # The LLM's section_type guess is ignored; it's only present in the
        # output schema so the model has context, not because we trust it.
        llm_section = entry.get("section_type", "unknown")
        if llm_section not in _VALID_SECTION_TYPES:
            llm_section = "unknown"
        resolved_section = section_type if section_type != "unknown" else llm_section

        candidates.append(CandidateMetric(
            doc_id=doc_id,
            metric_name=canonical_name,
            raw_label=str(entry.get("raw_label", ""))[:200],
            value=value,
            unit=str(entry.get("unit", ""))[:50],
            year=year,
            page_no=page_no,
            statement_type=stmt_type,
            section_type=resolved_section,
            confidence=confidence,
            source="llm_extraction",
            evidence=str(entry.get("evidence", ""))[:200],
        ))

    return candidates


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _normalise_name(raw: str) -> str:
    return raw.strip().lower().replace(" ", "_")


def _coerce_value(raw) -> Optional[float]:
    """
    Handle three cases:
    1. LLM returned a Python float/int (expected, most common)
    2. LLM returned a string (Indian-formatted number like "68,468")
    3. LLM returned null / None
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        # Reuse the production parse_value() — handles parentheses, commas,
        # dashes, currency symbols, etc.
        return parse_value(raw)
    return None


def _coerce_year(raw) -> Optional[int]:
    """Return a valid 4-digit year or None."""
    if raw is None:
        return None
    try:
        y = int(raw)
        return y if 2000 <= y <= 2100 else None
    except (TypeError, ValueError):
        return None
