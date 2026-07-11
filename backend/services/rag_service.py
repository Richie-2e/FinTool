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


def build_grounded_prompt(
    question: str,
    chunks: list[TextChunk],
    computed_metrics_rows: list,   # list of ComputedMetric ORM objects (all years)
    doc_meta,                      # Document ORM object
) -> str:
    """
    Assemble the grounded prompt per SPEC_api.md §3.6.
    computed_metrics_rows should be all years, ordered ascending.
    """
    # ── COMPUTED METRICS section ─────────────────────────────────────────────
    metric_lines: list[str] = []
    for row in computed_metrics_rows:
        yr = row.year
        for attr, label in _RATIO_LABELS:
            val = getattr(row, attr, None)
            if val is not None:
                metric_lines.append(f"  {label} ({yr}): {val:.4f}")
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
