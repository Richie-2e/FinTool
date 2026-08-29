from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from backend.models.db import ResolvedMetric

"""
backend/services/verification_service.py
Downstream trust propagation (bundled into the Phase B/C session per
explicit instruction) -- worst-case verification_state aggregation for
ratios and risks, WITHOUT modifying numerical_module.py. Ratio formulas,
risk thresholds, and compute() itself are completely untouched; this module
only reads already-persisted data (resolved_metrics.verification_state, set
by L6, and provenance.json's existing numerator/denominator metric-name
mapping, already written by numerical_module._build_provenance() and already
read by backend/routers/explain.py) and derives a state for each ratio/risk
by simple worst-case aggregation.

State ordering (worst to best): NEEDS_REVIEW > VERIFIED > None (unknown/not
checked). A ratio/risk with any NEEDS_REVIEW input is NEEDS_REVIEW. A
ratio/risk whose inputs are all VERIFIED is VERIFIED. A ratio/risk with at
least one input that was never structurally checked (state is None -- e.g.
a V1-regex-fallback-sourced metric, which never runs L6) is left as None
(unknown) rather than guessed at either direction -- consistent with "do not
invent certainty."
"""

_STATE_RANK = {"NEEDS_REVIEW": 2, "VERIFIED": 1}  # higher = more cautious; None = unranked


def _worst_state(states: list[Optional[str]]) -> Optional[str]:
    ranked = [s for s in states if s in _STATE_RANK]
    if not ranked:
        return None
    return max(ranked, key=lambda s: _STATE_RANK[s])


def load_provenance(output_dir: str, doc_id: str) -> list[dict]:
    """Same file numerical_module._build_provenance() already writes and
    backend/routers/explain.py already reads -- reused, not duplicated."""
    prov_path = Path(output_dir) / f"{doc_id}_provenance.json"
    if not prov_path.exists():
        return []
    with open(prov_path, encoding="utf-8") as fh:
        return json.load(fh)


def _metric_state_map(doc_id: str, db: Session) -> dict[tuple[str, int], Optional[str]]:
    rows = db.query(ResolvedMetric).filter(ResolvedMetric.doc_id == doc_id).all()
    return {(r.metric_name, r.year): r.verification_state for r in rows if r.year is not None}


def compute_ratio_verification_states(
    doc_id: str, output_dir: str, db: Session
) -> dict[tuple[str, int], Optional[str]]:
    """(ratio_name, year) -> worst-case verification_state of its numerator/
    denominator resolved metrics, per provenance.json's existing per-ratio
    per-year numerator/denominator metric-name records."""
    entries = load_provenance(output_dir, doc_id)
    metric_states = _metric_state_map(doc_id, db)

    result: dict[tuple[str, int], Optional[str]] = {}
    for entry in entries:
        name = entry.get("ratio_name")
        year = entry.get("year")
        if not name or year is None:
            continue
        # A provenance entry can exist with result=None (numerical_module.py
        # still records the formula/numerator/denominator names even when
        # one input never resolved -- confirmed real: OFSS profit_margin,
        # numerator net_profit never extracted). Such a ratio has no value
        # at all, so it must never be assigned a verification_state -- doing
        # so via a naive worst-case over only the inputs that DO exist would
        # silently invent a "VERIFIED" for a ratio that was never actually
        # computed (confirmed live during Phase B/C testing: profit_margin
        # showed VERIFIED while its own result was null). Left as None
        # (unknown), same as any other not-computed case.
        if entry.get("result") is None:
            result[(name, year)] = None
            continue
        inputs = [entry.get("numerator"), entry.get("denominator")]
        states = [metric_states.get((m, year)) for m in inputs if m]
        result[(name, year)] = _worst_state(states)
    return result


def compute_risk_verification_states(
    doc_id: str, output_dir: str, db: Session
) -> dict[tuple[str, int], Optional[str]]:
    """(risk_type, year) -> worst-case verification_state of the ratio/metric
    that risk category is classified from. Mirrors
    backend/routers/explain.py's _METRIC_RISK_MAP (ratio-driven risks) plus
    metrics.py's direct operating_cash_flow -> cashflow_risk mapping (a raw
    resolved metric, not a ratio)."""
    ratio_states = compute_ratio_verification_states(doc_id, output_dir, db)
    metric_states = _metric_state_map(doc_id, db)

    years = sorted({y for (_, y) in ratio_states} | {y for (_, y) in metric_states})
    result: dict[tuple[str, int], Optional[str]] = {}
    for year in years:
        liquidity     = ratio_states.get(("current_ratio", year))
        debt          = ratio_states.get(("debt_to_equity", year))
        profitability = ratio_states.get(("profit_margin", year))
        cashflow      = metric_states.get(("operating_cash_flow", year))
        result[("liquidity", year)]     = liquidity
        result[("debt", year)]          = debt
        result[("profitability", year)] = profitability
        result[("cashflow", year)]      = cashflow
        result[("overall", year)]       = _worst_state([liquidity, debt, profitability, cashflow])
    return result
