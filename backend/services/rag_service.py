from __future__ import annotations

from pathlib import Path
from typing import Optional

from google import genai
from google.genai import errors as genai_errors

from backend.config import EMBED_MODEL, GEMINI_API_KEY, LLM_MODEL
from extraction.text_chunker import TextChunk, load_faiss_index, search_chunks

# ---------------------------------------------------------------------------
# Embedding model singleton — loaded once, reused on every request
# ---------------------------------------------------------------------------

_embed_model = None


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(EMBED_MODEL)
    return _embed_model


# ---------------------------------------------------------------------------
# build_rag_context
# ---------------------------------------------------------------------------

def build_rag_context(doc_id: str, output_dir: str, question: str) -> dict:
    """
    Load the FAISS index, embed the question, retrieve top-4 chunks.
    Returns {"chunks": [...], "sources": [...]} ready for the chat router.
    """
    index, chunks = load_faiss_index(Path(output_dir), doc_id)
    embed_model   = _get_embed_model()
    relevant      = search_chunks(question, index, chunks, embed_model, k=4)

    sources = [
        {
            "page_no":      c.page_no,
            "section_type": c.section_type,
            "snippet":      c.text[:200],
        }
        for c in relevant
    ]
    return {"chunks": relevant, "sources": sources}


# ---------------------------------------------------------------------------
# build_grounded_prompt
# ---------------------------------------------------------------------------

_RATIO_LABELS: list[tuple[str, str]] = [
    ("current_ratio",      "Current Ratio"),
    ("cash_ratio",         "Cash Ratio"),
    ("debt_to_equity",     "Debt-to-Equity"),
    ("debt_ratio",         "Debt Ratio"),
    ("interest_coverage",  "Interest Coverage"),
    ("profit_margin",      "Profit Margin"),
    ("operating_margin",   "Operating Margin"),
    ("gross_margin",       "Gross Margin"),
    ("asset_turnover",     "Asset Turnover"),
    ("ocf_to_revenue",     "OCF-to-Revenue"),
    ("free_cash_flow",     "Free Cash Flow (Cr)"),
    ("yoy_revenue_growth", "YoY Revenue Growth"),
    ("yoy_profit_growth",  "YoY Profit Growth"),
    ("total_debt",         "Total Debt (Cr)"),
]

_RISK_LABELS: list[tuple[str, str]] = [
    ("liquidity_risk",     "Liquidity Risk"),
    ("debt_risk",          "Debt Risk"),
    ("profitability_risk", "Profitability Risk"),
    ("cashflow_risk",      "Cashflow Risk"),
    ("overall_risk",       "Overall Risk"),
]

# FV2-7: raw resolved-metric labels, grouped by statement (Income Statement ->
# Balance Sheet -> Cash Flow) rather than database/insertion order. Within
# each group, metrics are listed in standard statement presentation order
# (e.g. Assets before Liabilities before Equity). This groups each metric's
# own years on adjacent lines, which matters for the dominant real question
# pattern observed in this project's chat evaluation (same-metric,
# cross-year comparisons -- e.g. "revenue 2025 vs 2024") -- unlike
# COMPUTED_METRICS below, which is deliberately left at its existing
# year-outer nesting: ratios have no natural "statement" to group by, and
# that section's format is unchanged, working, and out of scope here.
_RAW_METRIC_LABELS: dict[str, list[tuple[str, str]]] = {
    "Income Statement": [
        ("revenue",           "Revenue"),
        ("gross_profit",      "Gross Profit"),
        ("operating_profit",  "Operating Profit"),
        ("net_profit",        "Net Profit"),
        ("interest_expense",  "Interest Expense"),
    ],
    "Balance Sheet": [
        ("total_assets",          "Total Assets"),
        ("current_assets",        "Current Assets"),
        ("cash_and_equivalents",  "Cash and Cash Equivalents"),
        ("total_liabilities",     "Total Liabilities"),
        ("current_liabilities",   "Current Liabilities"),
        ("long_term_debt",        "Long-Term Debt"),
        ("short_term_debt",       "Short-Term Debt"),
        ("total_equity",          "Total Equity"),
    ],
    "Cash Flow": [
        ("operating_cash_flow",  "Operating Cash Flow"),
        ("investing_cash_flow",  "Investing Cash Flow"),
        ("financing_cash_flow",  "Financing Cash Flow"),
        ("capex",                "Capital Expenditure"),
    ],
}


def build_grounded_prompt(
    question: str,
    chunks: list[TextChunk],
    computed_metrics_rows: list,   # list of ComputedMetric ORM objects (all years)
    resolved_metrics_rows: list,   # list of ResolvedMetric ORM objects (all years) -- FV2-7
    doc_meta,                      # Document ORM object
    ratio_verification_states: Optional[dict[tuple[str, int], Optional[str]]] = None,
    # Downstream trust propagation: (ratio_name, year) -> "VERIFIED" |
    # "NEEDS_REVIEW" | None, from backend/services/verification_service.py.
    # Optional and default None for backward compatibility -- omitted,
    # behavior (including the exact rendered COMPUTED METRICS text) is
    # identical to before Phase B/C.
) -> str:
    """
    Assemble the grounded prompt per SPEC_api.md §3.6.
    computed_metrics_rows and resolved_metrics_rows should be all years.
    """
    # ── RESOLVED METRICS section (FV2-7) ─────────────────────────────────────
    # ResolvedMetric is one row per (metric_name, year) -- unlike ComputedMetric,
    # which is one row per year with a column per ratio. Index by metric name
    # first so each metric's years can be grouped and sorted together.
    # Downstream trust propagation: getattr(..., default=None) rather than
    # direct attribute access, since resolved_metrics_rows may be plain
    # fixtures (e.g. test_rag_service.py's SimpleNamespace rows) that predate
    # Phase A/B's verification_state/verification_reason columns -- absent
    # is treated identically to "not checked" (no caveat appended), not an error.
    by_metric_year: dict[str, dict[int, tuple[float, Optional[str], Optional[int], Optional[str], Optional[str]]]] = {}
    for row in resolved_metrics_rows:
        if row.year is None or row.value is None:
            continue
        by_metric_year.setdefault(row.metric_name, {})[row.year] = (
            row.value, row.unit, row.page_no,
            getattr(row, "verification_state", None),
            getattr(row, "verification_reason", None),
        )

    raw_lines: list[str] = []
    for group_label, group_metrics in _RAW_METRIC_LABELS.items():
        group_lines: list[str] = []
        for attr, label in group_metrics:
            years = by_metric_year.get(attr, {})
            for yr in sorted(years):
                value, unit, page_no, verification_state, verification_reason = years[yr]
                unit_suffix = f" {unit}" if unit else ""
                page_suffix = f" (page {page_no})" if page_no is not None else ""
                # Caveat only NEEDS_REVIEW figures -- VERIFIED and "not
                # checked" (None, e.g. V1-fallback-sourced metrics) render
                # exactly as before Phase B/C, so every existing
                # build_grounded_prompt test stays byte-identical.
                review_suffix = (
                    f" [NEEDS_REVIEW: {verification_reason}]"
                    if verification_state == "NEEDS_REVIEW" else ""
                )
                group_lines.append(
                    f"  {label} ({yr}): {value:,.2f}{unit_suffix}{page_suffix}{review_suffix}"
                )
        if group_lines:
            raw_lines.append(f"{group_label}:")
            raw_lines.extend(group_lines)

    resolved_section = (
        "\n".join(raw_lines)
        if raw_lines
        else "  No resolved metrics available."
    )

    # ── COMPUTED METRICS section ─────────────────────────────────────────────
    metric_lines: list[str] = []
    for row in computed_metrics_rows:
        yr = row.year
        for attr, label in _RATIO_LABELS:
            val = getattr(row, attr, None)
            if val is not None:
                state = (ratio_verification_states or {}).get((attr, yr))
                review_suffix = " [NEEDS_REVIEW]" if state == "NEEDS_REVIEW" else ""
                metric_lines.append(f"  {label} ({yr}): {val:.4f}{review_suffix}")
        for attr, label in _RISK_LABELS:
            val = getattr(row, attr, None)
            if val:
                metric_lines.append(f"  {label} ({yr}): {val}")

    metrics_section = (
        "\n".join(metric_lines)
        if metric_lines
        else "  No computed metrics available."
    )

    # ── RETRIEVED DOCUMENT SECTIONS ──────────────────────────────────────────
    doc_lines: list[str] = []
    for chunk in chunks:
        # Trim chunk text to keep the prompt compact
        excerpt = chunk.text.replace("\n", " ").strip()[:500]
        doc_lines.append(f'  [Page {chunk.page_no}] "{excerpt}"')

    docs_section = (
        "\n".join(doc_lines)
        if doc_lines
        else "  No relevant document sections retrieved."
    )

    # ── Assemble ─────────────────────────────────────────────────────────────
    return (
        "You are a financial analyst assistant. You must only use the information provided below.\n"
        "Do not use any outside knowledge. Do not invent numbers.\n"
        "\n"
        "RESOLVED METRICS (extracted directly from the financial statements — authoritative):\n"
        f"{resolved_section}\n"
        "\n"
        "COMPUTED METRICS (authoritative — do not contradict these):\n"
        f"{metrics_section}\n"
        "\n"
        "RETRIEVED DOCUMENT SECTIONS:\n"
        f"{docs_section}\n"
        "\n"
        f"USER QUESTION: {question}\n"
        "\n"
        "INSTRUCTIONS:\n"
        "- Answer in 3-5 sentences.\n"
        "- Reference specific page numbers when citing document text.\n"
        "- State ratio values explicitly where relevant.\n"
        "- Do not say \"I think\" or \"I believe\". State facts from the sources above.\n"
        "- If the answer cannot be found in the sources above, say "
        "\"This information is not available in the uploaded document.\"\n"
        "- Any figure above tagged [NEEDS_REVIEW] has not been confirmed to come from the "
        "correct row/year -- if your answer uses one, say so explicitly (e.g. \"this figure "
        "is flagged for review and may not be fully verified\") instead of stating it as a "
        "plain established fact.\n"
    )


# ---------------------------------------------------------------------------
# call_llm
# ---------------------------------------------------------------------------

def call_llm(
    prompt: str,
    conversation_history: list[dict] | None = None,
) -> str:
    """Call the Gemini API with the grounded prompt. Returns the response text."""
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to your .env file or environment."
        )
    client = genai.Client(api_key=GEMINI_API_KEY)

    # Anthropic-style {"role": "user"/"assistant", "content": ...} turns,
    # translated to Gemini's {"role": "user"/"model", "parts": [{"text": ...}]}.
    contents: list[dict] = [
        {
            "role": "model" if turn["role"] == "assistant" else "user",
            "parts": [{"text": turn["content"]}],
        }
        for turn in (conversation_history or [])
    ]
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    try:
        response = client.models.generate_content(
            model=LLM_MODEL,
            contents=contents,
        )
    except genai_errors.ClientError as exc:
        raise RuntimeError(
            f"GEMINI_API_KEY is invalid or expired, or the request was rejected "
            f"({exc.code}): {exc.message}"
        )
    except genai_errors.APIError as exc:
        raise RuntimeError(f"Gemini API error ({exc.code}): {exc.message}")
    return response.text
