from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import pandas as pd
from celery import Celery

from backend.config import OUTPUT_DIR, REDIS_URL
from backend.models.db import ComputedMetric, Document, ResolvedMetric, SessionLocal

# ---------------------------------------------------------------------------
# Celery app
# ---------------------------------------------------------------------------

celery_app = Celery(
    "fintool",
    broker=REDIS_URL,
    backend=REDIS_URL,
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
)


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

def update_doc_status(
    doc_id: str,
    status: str,
    progress_message: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Open a fresh session, update Document row, commit, close."""
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.doc_id == doc_id).first()
        if doc:
            doc.status = status
            if progress_message is not None:
                doc.progress_message = progress_message
            if error_message is not None:
                doc.error_message = error_message
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# NaN-safe coercion helpers
# ---------------------------------------------------------------------------

def _f(row: pd.Series, col: str) -> Optional[float]:
    """Return float value from row[col], or None if missing/NaN."""
    val = row.get(col)
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _i(row: pd.Series, col: str) -> Optional[int]:
    """Return int value from row[col], or None if missing/NaN."""
    val = row.get(col)
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def _s(row: pd.Series, col: str) -> Optional[str]:
    """Return str value from row[col], or None if missing/NaN/empty."""
    val = row.get(col)
    if val is None:
        return None
    try:
        if isinstance(val, float) and math.isnan(val):
            return None
    except TypeError:
        pass
    s = str(val).strip()
    return s if s else None


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=1)
def run_extraction_pipeline(self, doc_id: str, pdf_path: str) -> None:  # noqa: ANN001
    try:
        # ── Stage 1: Run extraction pipeline ────────────────────────────────
        update_doc_status(doc_id, "processing", "Stage 1/4: Parsing document...")

        from extraction.pipeline import ExtractionPipeline

        result = ExtractionPipeline().run(
            Path(pdf_path),
            output_dir=OUTPUT_DIR / doc_id,
        )

        # ── Stage 2: Compute ratios ──────────────────────────────────────────
        update_doc_status(doc_id, "processing", "Stage 2/4: Computing ratios...")

        from numerical_module import compute

        computed = compute(
            doc_id,
            result.pivot_df,
            result.resolved_df,
            output_dir=OUTPUT_DIR / doc_id,
        )

        # ── Stage 3: Persist to DB ───────────────────────────────────────────
        update_doc_status(doc_id, "processing", "Stage 3/4: Saving to database...")

        db = SessionLocal()
        try:
            # Update Document metadata
            doc = db.query(Document).filter(Document.doc_id == doc_id).first()
            if doc:
                doc.company_name    = result.meta.get("company_name")
                doc.page_count      = result.meta.get("page_count")
                doc.document_years  = json.dumps(result.meta.get("years", []))
                doc.output_dir      = str(result.output_dir)

            # Clear any previous rows for this doc (idempotent re-processing)
            db.query(ResolvedMetric).filter(ResolvedMetric.doc_id == doc_id).delete()
            db.query(ComputedMetric).filter(ComputedMetric.doc_id == doc_id).delete()

            # Bulk-insert resolved metrics
            if not result.resolved_df.empty:
                resolved_rows = []
                for _, row in result.resolved_df.iterrows():
                    resolved_rows.append(ResolvedMetric(
                        doc_id         = doc_id,
                        metric_name    = _s(row, "metric_name"),
                        value          = _f(row, "value"),
                        unit           = _s(row, "unit"),
                        year           = _i(row, "year"),
                        page_no        = _i(row, "page_no"),
                        raw_label      = _s(row, "raw_label"),
                        statement_type = _s(row, "statement_type"),
                        section_type   = _s(row, "section_type"),
                        confidence     = _s(row, "confidence"),
                    ))
                db.add_all(resolved_rows)

            # Bulk-insert computed metrics (one row per year)
            if not computed.as_df.empty:
                computed_rows = []
                for _, row in computed.as_df.iterrows():
                    computed_rows.append(ComputedMetric(
                        doc_id             = doc_id,
                        year               = _i(row, "year"),
                        current_ratio      = _f(row, "current_ratio"),
                        cash_ratio         = _f(row, "cash_ratio"),
                        debt_to_equity     = _f(row, "debt_to_equity"),
                        debt_ratio         = _f(row, "debt_ratio"),
                        interest_coverage  = _f(row, "interest_coverage"),
                        profit_margin      = _f(row, "profit_margin"),
                        operating_margin   = _f(row, "operating_margin"),
                        gross_margin       = _f(row, "gross_margin"),
                        asset_turnover     = _f(row, "asset_turnover"),
                        ocf_to_revenue     = _f(row, "ocf_to_revenue"),
                        free_cash_flow     = _f(row, "free_cash_flow"),
                        yoy_revenue_growth = _f(row, "yoy_revenue_growth"),
                        yoy_profit_growth  = _f(row, "yoy_profit_growth"),
                        total_debt         = _f(row, "total_debt"),
                        liquidity_risk     = _s(row, "liquidity_risk"),
                        debt_risk          = _s(row, "debt_risk"),
                        profitability_risk = _s(row, "profitability_risk"),
                        cashflow_risk      = _s(row, "cashflow_risk"),
                        overall_risk       = _s(row, "overall_risk"),
                    ))
                db.add_all(computed_rows)

            db.commit()
        finally:
            db.close()

        # ── Stage 4: Done ────────────────────────────────────────────────────
        update_doc_status(doc_id, "ready", "Complete.")

    except Exception as exc:
        update_doc_status(doc_id, "failed", error_message=str(exc))
        raise
