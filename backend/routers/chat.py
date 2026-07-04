from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.exceptions import APIError
from backend.models.db import ComputedMetric, get_db
from backend.models.schemas import ChatRequest, ChatResponse, MetricUsed, SourceItem
from backend.routers.metrics import _get_ready_doc
from backend.services.rag_service import build_grounded_prompt, build_rag_context, call_claude

router = APIRouter()

# Canonical ratio attribute names present on ComputedMetric rows
_ALL_RATIO_ATTRS: list[str] = [
    "current_ratio",
    "cash_ratio",
    "debt_to_equity",
    "debt_ratio",
    "interest_coverage",
    "profit_margin",
    "operating_margin",
    "gross_margin",
    "asset_turnover",
    "ocf_to_revenue",
    "free_cash_flow",
    "yoy_revenue_growth",
    "yoy_profit_growth",
    "total_debt",
]

# Maps lowercase phrases that might appear in the answer text to canonical attr names
_MENTION_MAP: dict[str, str] = {
    "current ratio":      "current_ratio",
    "cash ratio":         "cash_ratio",
    "debt to equity":     "debt_to_equity",
    "debt-to-equity":     "debt_to_equity",
    "debt ratio":         "debt_ratio",
    "interest coverage":  "interest_coverage",
    "profit margin":      "profit_margin",
    "net margin":         "profit_margin",
    "operating margin":   "operating_margin",
    "gross margin":       "gross_margin",
    "asset turnover":     "asset_turnover",
    "ocf to revenue":     "ocf_to_revenue",
    "free cash flow":     "free_cash_flow",
    "revenue growth":     "yoy_revenue_growth",
    "profit growth":      "yoy_profit_growth",
    "total debt":         "total_debt",
    "liquidity":          "current_ratio",
    "leverage":           "debt_to_equity",
    "solvency":           "debt_ratio",
    "profitability":      "profit_margin",
}

# Key metrics whose absence is worth surfacing as a warning
_KEY_METRICS: list[str] = [
    "current_ratio",
    "debt_to_equity",
    "profit_margin",
    "free_cash_flow",
    "operating_margin",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_metrics_used(
    answer: str,
    question: str,
    computed_rows: list,
) -> list[MetricUsed]:
    """
    Scan answer + question for ratio name mentions; emit MetricUsed entries
    for every (metric, year) pair found in computed_rows.
    """
    combined = (answer + " " + question).lower()

    # Collect canonical attr names that are mentioned
    mentioned: set[str] = set()
    for phrase, attr in _MENTION_MAP.items():
        if phrase in combined:
            mentioned.add(attr)

    if not mentioned:
        return []

    used: list[MetricUsed] = []
    for row in computed_rows:
        for attr in mentioned:
            val = getattr(row, attr, None)
            used.append(MetricUsed(metric=attr, year=row.year, value=val))

    return used


def _build_warning(computed_rows: list) -> Optional[str]:
    """Return a warning string if any key metric is None for any year."""
    missing: list[str] = []
    for row in computed_rows:
        for attr in _KEY_METRICS:
            if getattr(row, attr, None) is None:
                missing.append(f"{attr} not available for {row.year}")
    return "; ".join(missing) if missing else None


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=ChatResponse)
def post_chat(req: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    doc_id = req.doc_id
    doc    = _get_ready_doc(doc_id, db)

    # Guard: FAISS index must exist
    if not doc.output_dir:
        raise APIError(400, "RAG index not built for this document", "DOC_FAILED", doc_id=doc_id)

    index_path = Path(doc.output_dir) / f"{doc_id}_faiss.index"
    if not index_path.exists():
        raise APIError(400, "RAG index not built for this document", "DOC_FAILED", doc_id=doc_id)

    # Retrieve top-4 relevant chunks
    rag_context = build_rag_context(doc_id, doc.output_dir, req.question)

    # Fetch all computed metric rows for this document (all years, ascending)
    computed_rows = (
        db.query(ComputedMetric)
        .filter(ComputedMetric.doc_id == doc_id)
        .order_by(ComputedMetric.year)
        .all()
    )

    # Build grounded prompt and call Claude
    prompt = build_grounded_prompt(
        req.question,
        rag_context["chunks"],
        computed_rows,
        doc,
    )

    history = [{"role": t.role, "content": t.content} for t in req.conversation_history]
    answer  = call_claude(prompt, conversation_history=history)

    # Post-process
    sources      = [SourceItem(**s) for s in rag_context["sources"]]
    metrics_used = _extract_metrics_used(answer, req.question, computed_rows)
    warning      = _build_warning(computed_rows)

    return ChatResponse(
        answer=answer,
        sources=sources,
        metrics_used=metrics_used,
        warning=warning,
    )
