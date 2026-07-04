from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.exceptions import APIError
from backend.models.db import ComputedMetric, Document, ResolvedMetric, get_db
from backend.models.schemas import (
    MetricItem,
    MetricsResponse,
    QualityReport,
    RatioItem,
    RatiosResponse,
    RiskItem,
    RisksResponse,
)

router = APIRouter()

# Hardcoded threshold descriptions (spec §3.5)
LIQUIDITY_THRESHOLD     = "< 1.0 = High, 1.0-1.5 = Medium, >= 1.5 = Low"
DEBT_THRESHOLD          = "< 1.0 = Low, 1.0-2.0 = Medium, > 2.0 = High"
PROFITABILITY_THRESHOLD = "< 0 = High, 0-5% = Medium, >= 5% = Low"
CASHFLOW_THRESHOLD      = "< 0 = High, 0-100 Cr = Medium, >= 100 Cr = Low"


# ---------------------------------------------------------------------------
# Guard helper (shared by all three endpoints)
# ---------------------------------------------------------------------------

def _get_ready_doc(doc_id: str, db: Session) -> Document:
    doc = db.query(Document).filter(Document.doc_id == doc_id).first()
    if not doc:
        raise APIError(404, "Document not found", "DOC_NOT_FOUND", doc_id=doc_id)
    if doc.status == "processing":
        raise APIError(400, "Document is still being processed", "DOC_PROCESSING", doc_id=doc_id)
    if doc.status == "failed":
        raise APIError(400, "Document processing failed", "DOC_FAILED", doc_id=doc_id)
    return doc


def _parse_years(doc: Document) -> list[int]:
    if not doc.document_years:
        return []
    try:
        return json.loads(doc.document_years)
    except (json.JSONDecodeError, TypeError):
        return []


# ---------------------------------------------------------------------------
# GET /metrics/{doc_id}
# ---------------------------------------------------------------------------

@router.get("/metrics/{doc_id}", response_model=MetricsResponse)
def get_metrics(doc_id: str, db: Session = Depends(get_db)) -> MetricsResponse:
    doc = _get_ready_doc(doc_id, db)

    rows = (
        db.query(ResolvedMetric)
        .filter(ResolvedMetric.doc_id == doc_id)
        .order_by(ResolvedMetric.year)
        .all()
    )

    # MetricItem.year is int (required) — skip rows where year was not resolved
    metrics = [MetricItem.model_validate(r) for r in rows if r.year is not None]

    # Load quality report from the JSON file written by the pipeline
    quality_report: Optional[QualityReport] = None
    if doc.output_dir:
        quality_path = Path(doc.output_dir) / f"{doc_id}_quality_report.json"
        if quality_path.exists():
            try:
                with open(quality_path, encoding="utf-8") as fh:
                    qdata = json.load(fh)
                quality_report = QualityReport(
                    candidate_rows=qdata.get("candidate_rows", 0),
                    resolved_rows=qdata.get("resolved_rows", 0),
                    missing_core_metrics_by_year=qdata.get("missing_core_metrics_by_year", {}),
                    issues=qdata.get("issues", []),
                )
            except Exception:
                pass  # non-fatal — respond without quality_report

    return MetricsResponse(
        doc_id=doc_id,
        company_name=doc.company_name,
        document_years=_parse_years(doc),
        metrics=metrics,
        quality_report=quality_report,
    )


# ---------------------------------------------------------------------------
# GET /ratios/{doc_id}
# ---------------------------------------------------------------------------

@router.get("/ratios/{doc_id}", response_model=RatiosResponse)
def get_ratios(doc_id: str, db: Session = Depends(get_db)) -> RatiosResponse:
    doc = _get_ready_doc(doc_id, db)

    rows = (
        db.query(ComputedMetric)
        .filter(ComputedMetric.doc_id == doc_id)
        .order_by(ComputedMetric.year)
        .all()
    )

    ratios = [RatioItem.model_validate(r) for r in rows]

    return RatiosResponse(
        doc_id=doc_id,
        company_name=doc.company_name,
        ratios=ratios,
    )


# ---------------------------------------------------------------------------
# GET /risks/{doc_id}
# ---------------------------------------------------------------------------

@router.get("/risks/{doc_id}", response_model=RisksResponse)
def get_risks(doc_id: str, db: Session = Depends(get_db)) -> RisksResponse:
    doc = _get_ready_doc(doc_id, db)

    rows = (
        db.query(ComputedMetric)
        .filter(ComputedMetric.doc_id == doc_id)
        .order_by(ComputedMetric.year)
        .all()
    )

    risks: list[RiskItem] = []
    for row in rows:
        if row.year is None:
            continue

        # operating_cash_flow is not stored in computed_metrics; fetch from resolved_metrics
        ocf_row = (
            db.query(ResolvedMetric)
            .filter(
                ResolvedMetric.doc_id == doc_id,
                ResolvedMetric.metric_name == "operating_cash_flow",
                ResolvedMetric.year == row.year,
            )
            .first()
        )
        cashflow_value = ocf_row.value if ocf_row else None

        risks.append(RiskItem(
            year=row.year,

            liquidity_risk        = row.liquidity_risk     or "Unknown",
            liquidity_ratio_used  = "current_ratio",
            liquidity_ratio_value = row.current_ratio,
            liquidity_threshold   = LIQUIDITY_THRESHOLD,

            debt_risk        = row.debt_risk     or "Unknown",
            debt_ratio_used  = "debt_to_equity",
            debt_ratio_value = row.debt_to_equity,
            debt_threshold   = DEBT_THRESHOLD,

            profitability_risk        = row.profitability_risk     or "Unknown",
            profitability_ratio_used  = "profit_margin",
            profitability_ratio_value = row.profit_margin,
            profitability_threshold   = PROFITABILITY_THRESHOLD,

            cashflow_risk      = row.cashflow_risk or "Unknown",
            cashflow_value     = cashflow_value,
            cashflow_threshold = CASHFLOW_THRESHOLD,

            overall_risk = row.overall_risk or "Unknown",
        ))

    return RisksResponse(
        doc_id=doc_id,
        company_name=doc.company_name,
        risks=risks,
    )
