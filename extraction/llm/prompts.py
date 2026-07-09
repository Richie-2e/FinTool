"""
extraction/llm/prompts.py
Statement-specific system prompts for LLM-based metric extraction.

Each prompt is self-contained: it includes the shared extraction rules
plus rules that apply only to that statement type.

Keeping them separate eliminates rule bleeding — the balance sheet TOTAL
rules cannot confuse cash flow extraction, and the cash flow label rules
cannot confuse balance sheet extraction. This was the root cause of the
Fix 3 regression during PoC benchmarking.

Public API
----------
    get_system_prompt(stmt_type: str) -> str
    build_user_prompt(stmt_type: str, text_block: str) -> str
"""

from __future__ import annotations

import re
from typing import Optional

from extraction.canonical_metrics import CANONICAL_METRICS


# ---------------------------------------------------------------------------
# Canonical names scoped per statement type
# ---------------------------------------------------------------------------
# Sending only the relevant subset to each LLM call reduces ambiguity.

_CANONICAL_BY_TYPE: dict[str, list[str]] = {
    "balance_sheet": [
        name for name, spec in CANONICAL_METRICS.items()
        if spec["statement"] == "balance_sheet"
    ],
    "income_statement": [
        name for name, spec in CANONICAL_METRICS.items()
        if spec["statement"] == "income_statement"
    ],
    "cash_flow": [
        name for name, spec in CANONICAL_METRICS.items()
        if spec["statement"] == "cash_flow"
    ],
}


# ---------------------------------------------------------------------------
# Synonym hints — rendered from canonical_metrics.py's regex patterns
# ---------------------------------------------------------------------------
# canonical_metrics.py remains the single source of truth for alias
# knowledge. This section only *renders* that existing data as prompt text;
# it never introduces a second alias list. The patterns are regexes (used
# for label matching elsewhere), so converting them to human-readable text
# requires a defensive cleaner: known regex syntax (anchors, escaped parens,
# the "long.?term" optional-separator construct) is converted to plain text;
# anything else is left unrendered rather than emitted as garbled regex.

_MAX_SYNONYMS_PER_METRIC = 3

# Characters that indicate unhandled regex syntax after the known
# substitutions have been applied. A pattern containing any of these post-
# cleaning is skipped rather than rendered partially cleaned.
_UNSUPPORTED_REGEX_CHARS = set("^$*+[]{}|\\")


def _clean_pattern(pattern: str) -> Optional[str]:
    """
    Convert a canonical_metrics.py regex pattern into plain text, or return
    None if it uses regex syntax this cleaner doesn't know how to render.
    """
    s = pattern
    if s.startswith("^"):
        s = s[1:]
    if s.endswith("$"):
        s = s[:-1]
    s = s.replace(r"\(", "(").replace(r"\)", ")")
    s = s.replace(".?", " ")  # optional separator, e.g. "long.?term" -> "long term"
    s = re.sub(r"\s+", " ", s).strip()

    if any(ch in _UNSUPPORTED_REGEX_CHARS for ch in s):
        return None
    if "." in s:  # stray wildcard not covered by the substitution above
        return None
    return s


def _normalize_for_compare(s: str) -> str:
    return re.sub(r"[-\s]+", " ", s).strip().lower()


def _is_identity_alias(cleaned: str, metric_name: str) -> bool:
    """True if `cleaned` is just the canonical name with underscores->spaces
    (allowing for hyphen/space variation), i.e. it adds no new information."""
    return _normalize_for_compare(cleaned) == _normalize_for_compare(
        metric_name.replace("_", " ")
    )


def _render_metric_hints(metric_name: str, patterns: list[str]) -> Optional[str]:
    """Return 'metric_name: alias1, alias2, alias3' or None if no usable
    non-identity aliases remain after cleaning."""
    aliases: list[str] = []
    seen_normalized: set[str] = set()

    for pattern in patterns:
        cleaned = _clean_pattern(pattern)
        if cleaned is None:
            continue
        if _is_identity_alias(cleaned, metric_name):
            continue
        norm = _normalize_for_compare(cleaned)
        if norm in seen_normalized:
            continue
        seen_normalized.add(norm)
        aliases.append(cleaned)
        if len(aliases) == _MAX_SYNONYMS_PER_METRIC:
            break

    if not aliases:
        return None
    return f"{metric_name}: {', '.join(aliases)}"


def _build_synonym_hint_block(stmt_type: str) -> str:
    lines: list[str] = []
    for name in _CANONICAL_BY_TYPE.get(stmt_type, []):
        hint_line = _render_metric_hints(name, CANONICAL_METRICS[name]["patterns"])
        if hint_line:
            lines.append(f"- {hint_line}")

    if not lines:
        return ""
    return (
        "Common label variants for some metrics (for matching only — "
        "always use the canonical_name from the list above, never a variant):\n"
        + "\n".join(lines)
    )


# Precomputed once at import time (same pattern as _CANONICAL_BY_TYPE) — no
# per-call cost in build_user_prompt().
_SYNONYM_HINTS_BY_TYPE: dict[str, str] = {
    stmt: _build_synonym_hint_block(stmt) for stmt in _CANONICAL_BY_TYPE
}


# ---------------------------------------------------------------------------
# JSON output schema (shared across all prompts)
# ---------------------------------------------------------------------------

_OUTPUT_SCHEMA = """\
Output schema:
{
  "metrics": [
    {
      "canonical_name": "<name from canonical list>",
      "raw_label": "<copied verbatim, character-for-character, from the DOCUMENT TEXT below -- NEVER from the canonical name list or the label-variant hints, even if one of them looks similar>",
      "value": <float or null>,
      "unit": "<INR Crore / INR Lakh / USD Million / etc>",
      "year": <integer YYYY or null>,
      "confidence": "<high|medium|low>",
      "section_type": "<consolidated|standalone|unknown>",
      "evidence": "<verbatim line(s) from text where value was found>"
    }
  ],
  "notes": "<any important observations about the data>"
}

Confidence guide:
- high: label clearly matches, value is unambiguous, year is clear
- medium: label is close but not exact, or year required inference
- low: value or label was ambiguous"""


# ---------------------------------------------------------------------------
# Shared rules (rules 1–10 appear in every prompt unchanged)
# ---------------------------------------------------------------------------

_SHARED_RULES = """\
You are a financial data extraction engine for Indian annual reports.

Rules:
1. Extract ONLY values that are explicitly present in the provided text.
2. Do NOT calculate, infer, or guess any value.
3. Numbers may use Indian formatting: 1,89,483 means 189483. Convert to plain float.
4. Negative values are shown in parentheses: (4,521) = -4521.
5. Dashes (—, -) in value cells mean zero or not applicable; use null.
6. Map each extracted item to the closest canonical_name from the list provided.
7. If a metric appears for multiple years, extract all of them as separate entries.
8. Output ONLY a valid JSON object. No explanation text outside the JSON.
9. Keep evidence snippets under 100 characters.
10. IMPORTANT — Note reference numbers: Indian financial statements include a
    "Notes" column. Note references are small standalone integers (1–50) that
    appear on their own line immediately after a row label and immediately before
    the actual financial values.
    Example: "Revenue from operations\\n17\\n68,468\\n63,730" — here 17 is the
    note reference, NOT the revenue value. Revenue values are 68,468 and 63,730.
    Rule: if you see a standalone integer ≤ 50 between a label and large
    comma-formatted numbers, skip it entirely."""


# ---------------------------------------------------------------------------
# Statement-specific rule extensions
# ---------------------------------------------------------------------------

_BALANCE_SHEET_EXTRA = """\
11. IMPORTANT — Indian Balance Sheet TOTAL rows: The word "TOTAL" appears TWICE.
    The first TOTAL = Total Assets (sum of all assets).
    The second TOTAL = Total Equity + Liabilities (balance check, same numeric value).
    Rules:
    - Extract total_assets ONLY from the first TOTAL (under the ASSETS section).
    - Do NOT use the second TOTAL for total_assets, total_liabilities, or
      current_liabilities. The second TOTAL is a balance check, not a liability.
12. IMPORTANT — total_liabilities has NO explicit label in Indian balance sheets.
    To derive it: find the standalone subtotal at the END of the liabilities section
    (after all liability line items), BEFORE the second TOTAL row.
    This subtotal = non-current liabilities subtotal + current liabilities subtotal.
    Example: non-current liabilities subtotal 6,217 + current liabilities subtotal
    11,509 → total_liabilities = 17,726.
    DO NOT use the second TOTAL row value for total_liabilities.
13. IMPORTANT — current_liabilities: find the standalone subtotal at the END of the
    "Current liabilities" section — a plain number on its own line before the TOTAL row.
    Do NOT use the TOTAL row value for current_liabilities."""


_CASH_FLOW_EXTRA = """\
11. IMPORTANT — Cash flow label disambiguation:
    "Cash from operating activities" (or "Cash generated from operations") is a
    PRE-TAX subtotal that appears in the MIDDLE of the operating activities section.
    Do NOT extract it as operating_cash_flow.
    operating_cash_flow must be the line labeled "Net cash provided by operating
    activities" or "Net cash from operating activities" — this appears AFTER the
    tax paid line item near the END of the operating section.
    Apply the same rule for investing_cash_flow and financing_cash_flow: extract only
    the final "Net cash from/used in X activities" totals, not intermediate subtotals."""


# ---------------------------------------------------------------------------
# Assembled system prompts — one per statement type
# ---------------------------------------------------------------------------

BALANCE_SHEET_PROMPT: str = (
    _SHARED_RULES
    + "\n"
    + _BALANCE_SHEET_EXTRA
    + "\n\n"
    + _OUTPUT_SCHEMA
)

INCOME_STATEMENT_PROMPT: str = (
    _SHARED_RULES
    + "\n\n"
    + _OUTPUT_SCHEMA
)

CASH_FLOW_PROMPT: str = (
    _SHARED_RULES
    + "\n"
    + _CASH_FLOW_EXTRA
    + "\n\n"
    + _OUTPUT_SCHEMA
)

_PROMPTS: dict[str, str] = {
    "balance_sheet":    BALANCE_SHEET_PROMPT,
    "income_statement": INCOME_STATEMENT_PROMPT,
    "cash_flow":        CASH_FLOW_PROMPT,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_system_prompt(stmt_type: str) -> str:
    """Return the system prompt for the given statement type."""
    prompt = _PROMPTS.get(stmt_type)
    if prompt is None:
        raise ValueError(
            f"No prompt defined for statement type '{stmt_type}'. "
            f"Valid types: {list(_PROMPTS)}"
        )
    return prompt


def build_user_prompt(stmt_type: str, text_block: str) -> str:
    """
    Build the user-turn message for a given statement type and page text.
    Includes only the canonical names relevant to that statement type, plus
    a bounded set of label-variant hints rendered from canonical_metrics.py.
    """
    canonical_list = ", ".join(_CANONICAL_BY_TYPE.get(stmt_type, []))
    hints = _SYNONYM_HINTS_BY_TYPE.get(stmt_type, "")
    hints_block = f"\n\n{hints}" if hints else ""
    return (
        f"Statement type: {stmt_type}\n\n"
        f"Canonical metric names you may use (use ONLY these):\n"
        f"{canonical_list}"
        f"{hints_block}\n\n"
        f"--- DOCUMENT TEXT ---\n"
        f"{text_block}\n"
        f"--- END OF TEXT ---\n\n"
        f"Extract all financial metrics from the text above and return valid JSON."
    )
