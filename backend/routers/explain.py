from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.exceptions import APIError
from backend.models.db import ComputedMetric, ResolvedMetric, get_db
from backend.models.schemas import ExplainInput, ExplainResponse, RiskClassification
from backend.routers.metrics import (
    DEBT_THRESHOLD,
    LIQUIDITY_THRESHOLD,
    PROFITABILITY_THRESHOLD,
    _get_ready_doc,
)

router = APIRouter()

# Maps computed ratio name → (risk_type label, ComputedMetric column name, threshold string)
#
# operating_cash_flow is deliberately absent: it is a raw resolved metric, not
# a computed ratio, so it never appears as a ratio_name in provenance.json —
# metric_name lookup against prov_index always 404s before this map is
# consulted, making that branch unreachable. Cashflow risk remains visible
# via GET /risks.
_METRIC_RISK_MAP: dict[str, tuple[str, str, str]] = {
    "current_ratio":  ("liquidity",     "liquidity_risk",     LIQUIDITY_THRESHOLD),
    "debt_to_equity": ("debt",          "debt_risk",          DEBT_THRESHOLD),
    "profit_margin":  ("profitability", "profitability_risk", PROFITABILITY_THRESHOLD),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_provenance(output_dir: str, doc_id: str) -> dict[str, dict[str, dict]]:
    """
    Load provenance.json (a flat list of RatioProvenance dicts) and re-index it
    as { ratio_name: { str(year): entry_dict } } for O(1) lookup.
    Returns an empty dict if the file does not exist.
    """
    prov_path = Path(output_dir) / f"{doc_id}_provenance.json"
    if not prov_path.exists():
        return {}

    with open(prov_path, encoding="utf-8") as fh:
        entries: list[dict] = json.load(fh)

    indexed: dict[str, dict[str, dict]] = {}
    for entry in entries:
        name = entry.get("ratio_name", "")
        year = str(entry.get("year", ""))
        if name and year:
            indexed.setdefault(name, {})[year] = entry

    return indexed


def _db_metric(
    doc_id: str,
    metric_name: str,
    year: int,
    db: Session,
) -> Optional[ResolvedMetric]:
    return (
        db.query(ResolvedMetric)
        .filter(
            ResolvedMetric.doc_id    == doc_id,
            ResolvedMetric.metric_name == metric_name,
            ResolvedMetric.year      == year,
        )
        .first()
    )


def _build_input(
    metric_name: Optional[str],
    value: Optional[float],
    doc_id: str,
    year: int,
    db: Session,
) -> Optional[ExplainInput]:
    """Build one ExplainInput, enriching with unit/page_no/raw_label from the DB."""
    if not metric_name:
        return None
    row = _db_metric(doc_id, metric_name, year, db)
    return ExplainInput(
        metric    = metric_name,
        value     = value,
        unit      = row.unit           if row else None,
        page_no   = row.page_no        if row else None,
        raw_label = row.raw_label      if row else None,
        source    = row.statement_type if row else None,
    )


# ---------------------------------------------------------------------------
# GET /explain/{doc_id}/{metric_name}
# ---------------------------------------------------------------------------

@router.get("/explain/{doc_id}/{metric_name}", response_model=ExplainResponse)
def explain_metric(
    doc_id: str,
    metric_name: str,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
) -> ExplainResponse:
    doc = _get_ready_doc(doc_id, db)

    if not doc.output_dir:
        raise APIError(
            404,
            f"Provenance data not available for '{metric_name}'",
            "METRIC_NOT_FOUND",
            doc_id=doc_id,
        )

    # ── Load & index provenance ──────────────────────────────────────────────
    prov_index = _load_provenance(doc.output_dir, doc_id)

    if metric_name not in prov_index:
        raise APIError(
            404,
            f"Metric '{metric_name}' not found in provenance",
            "METRIC_NOT_FOUND",
            doc_id=doc_id,
        )

    year_entries = prov_index[metric_name]

    # Default to most recent year when year param is omitted
    resolved_year: int = year if year is not None else max(int(y) for y in year_entries)

    entry = year_entries.get(str(resolved_year))
    if entry is None:
        raise APIError(
            404,
            f"Metric '{metric_name}' has no provenance for year {resolved_year}",
            "METRIC_NOT_FOUND",
            doc_id=doc_id,
        )

    # ── Build inputs (numerator + denominator) ───────────────────────────────
    inputs: list[ExplainInput] = []

    num = _build_input(entry.get("numerator"), entry.get("numerator_val"), doc_id, resolved_year, db)
    if num:
        inputs.append(num)

    den = _build_input(entry.get("denominator"), entry.get("denominator_val"), doc_id, resolved_year, db)
    if den:
        inputs.append(den)

    # ── Risk classification (only for the 4 mapped ratios) ───────────────────
    risk_classification: Optional[RiskClassification] = None
    risk_map = _METRIC_RISK_MAP.get(metric_name)
    if risk_map:
        risk_type, risk_col, threshold_str = risk_map
        cm = (
            db.query(ComputedMetric)
            .filter(
                ComputedMetric.doc_id == doc_id,
                ComputedMetric.year   == resolved_year,
            )
            .first()
        )
        if cm:
            level = getattr(cm, risk_col, None) or "Unknown"
            risk_classification = RiskClassification(
                risk_type=risk_type,
                level=level,
                threshold_applied=threshold_str,
            )

    return ExplainResponse(
        metric_name       = metric_name,
        formula           = entry.get("formula_str", ""),
        year              = resolved_year,
        result            = entry.get("result"),
        inputs            = inputs,
        risk_classification = risk_classification,
    )
