"""
numerical_module.py
FinTool — Computation & Risk Classification Module

Design principle: The LLM never computes a number. This module is pure
deterministic Python. Same input → same output, always.

Entry point:
    result = compute(doc_id, pivot_df, resolved_df, output_dir)
"""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RISK_ORDER: dict[str, int] = {"High": 3, "Medium": 2, "Low": 1, "Unknown": 0}

# Threshold for cash-flow risk — ₹100 Cr for Indian ARs, $1M USD for SEC filings.
# The caller can override via the `ocf_threshold` parameter of `compute()`.
DEFAULT_OCF_THRESHOLD = 100.0   # ₹ Crore (Indian default)

# Canonical metric columns expected from pivot_df (all float).
CANONICAL_COLS = [
    "revenue",
    "gross_profit",
    "operating_profit",
    "net_profit",
    "interest_expense",
    "total_assets",
    "current_assets",
    "cash_and_equivalents",
    "total_liabilities",
    "current_liabilities",
    "long_term_debt",
    "short_term_debt",
    "total_equity",
    "operating_cash_flow",
    "investing_cash_flow",
    "financing_cash_flow",
    "capex",
]


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RatioProvenance:
    ratio_name:      str
    formula_str:     str
    numerator:       str
    denominator:     Optional[str]
    numerator_val:   Optional[float]
    denominator_val: Optional[float]
    result:          Optional[float]
    year:            int
    source_pages:    list[int]


@dataclass
class YearResult:
    doc_id:             str
    year:               int

    # ── Base metrics (pass-through from pivot_df) ─────────────────────────
    revenue:            Optional[float] = None
    gross_profit:       Optional[float] = None
    operating_profit:   Optional[float] = None
    net_profit:         Optional[float] = None
    interest_expense:   Optional[float] = None
    total_assets:       Optional[float] = None
    current_assets:     Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    total_liabilities:  Optional[float] = None
    current_liabilities: Optional[float] = None
    long_term_debt:     Optional[float] = None
    short_term_debt:    Optional[float] = None
    total_equity:       Optional[float] = None
    operating_cash_flow:  Optional[float] = None
    investing_cash_flow:  Optional[float] = None
    financing_cash_flow:  Optional[float] = None
    capex:              Optional[float] = None

    # ── Computed ratios ───────────────────────────────────────────────────
    total_debt:         Optional[float] = None
    current_ratio:      Optional[float] = None
    cash_ratio:         Optional[float] = None
    debt_to_equity:     Optional[float] = None
    debt_ratio:         Optional[float] = None
    interest_coverage:  Optional[float] = None
    profit_margin:      Optional[float] = None
    operating_margin:   Optional[float] = None
    gross_margin:       Optional[float] = None
    asset_turnover:     Optional[float] = None
    ocf_to_revenue:     Optional[float] = None
    free_cash_flow:     Optional[float] = None
    yoy_revenue_growth: Optional[float] = None
    yoy_profit_growth:  Optional[float] = None
    yoy_asset_growth:   Optional[float] = None

    # ── Risk classifications ──────────────────────────────────────────────
    liquidity_risk:     str = "Unknown"
    debt_risk:          str = "Unknown"
    profitability_risk: str = "Unknown"
    cashflow_risk:      str = "Unknown"
    overall_risk:       str = "Unknown"


@dataclass
class ComputedResult:
    doc_id:   str
    by_year:  list[YearResult]
    as_df:    pd.DataFrame
    warnings: list[str]
    provenance: list[RatioProvenance] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Safe arithmetic helpers
# ---------------------------------------------------------------------------

def _nan(v: Optional[float]) -> bool:
    """Return True if v is None or NaN."""
    return v is None or (isinstance(v, float) and math.isnan(v))


def _safe_div(numerator: Optional[float], denominator: Optional[float],
               label: str, warn_list: list[str]) -> Optional[float]:
    """Divide numerator / denominator; return None on zero/NaN denominator."""
    if _nan(numerator) or _nan(denominator):
        return None
    if denominator == 0:
        warn_list.append(f"{label}: denominator is 0, result set to NaN")
        return None
    return numerator / denominator


def _coerce(v) -> Optional[float]:
    """Convert a pandas scalar to float | None, treating NaN as None."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Risk classifiers  (spec §4)
# ---------------------------------------------------------------------------

def classify_liquidity(current_ratio: Optional[float]) -> str:
    if _nan(current_ratio):
        return "Unknown"
    if current_ratio < 1.0:   # type: ignore[operator]
        return "High"
    if current_ratio < 1.5:
        return "Medium"
    return "Low"


def classify_debt(debt_to_equity: Optional[float]) -> str:
    if _nan(debt_to_equity):
        return "Unknown"
    if debt_to_equity > 2.0:  # type: ignore[operator]
        return "High"
    if debt_to_equity > 1.0:
        return "Medium"
    return "Low"


def classify_profitability(profit_margin: Optional[float]) -> str:
    if _nan(profit_margin):
        return "Unknown"
    if profit_margin < 0:        # type: ignore[operator]
        return "High"
    if profit_margin < 0.05:
        return "Medium"
    return "Low"


def classify_cashflow(operating_cash_flow: Optional[float],
                      threshold: float = DEFAULT_OCF_THRESHOLD) -> str:
    if _nan(operating_cash_flow):
        return "Unknown"
    if operating_cash_flow < 0:               # type: ignore[operator]
        return "High"
    if operating_cash_flow < threshold:
        return "Medium"
    return "Low"


def compute_overall_risk(liquidity: str, debt: str,
                         profitability: str, cashflow: str) -> str:
    return max(
        [liquidity, debt, profitability, cashflow],
        key=lambda x: RISK_ORDER.get(x, 0),
    )


# ---------------------------------------------------------------------------
# Provenance builder
# ---------------------------------------------------------------------------

def _build_provenance(
    ratio_name: str,
    formula_str: str,
    numerator_name: str,
    denominator_name: Optional[str],
    numerator_val: Optional[float],
    denominator_val: Optional[float],
    result: Optional[float],
    year: int,
    resolved_df: pd.DataFrame,
    metrics_used: list[str],
) -> RatioProvenance:
    """Collect page_no values from resolved_df for every metric used in the ratio."""
    pages: list[int] = []
    if not resolved_df.empty and "metric_name" in resolved_df.columns:
        mask = resolved_df["metric_name"].isin(metrics_used)
        if "year" in resolved_df.columns:
            mask &= resolved_df["year"] == year
        page_col = "page_no" if "page_no" in resolved_df.columns else None
        if page_col:
            raw_pages = resolved_df.loc[mask, page_col].dropna().tolist()
            pages = sorted({int(p) for p in raw_pages if not _nan(float(p))})

    return RatioProvenance(
        ratio_name=ratio_name,
        formula_str=formula_str,
        numerator=numerator_name,
        denominator=denominator_name,
        numerator_val=numerator_val,
        denominator_val=denominator_val,
        result=result,
        year=year,
        source_pages=pages,
    )


# ---------------------------------------------------------------------------
# Per-year computation
# ---------------------------------------------------------------------------

def _compute_year(
    doc_id: str,
    year: int,
    row: dict[str, Optional[float]],
    prev_row: Optional[dict[str, Optional[float]]],
    resolved_df: pd.DataFrame,
    warn_list: list[str],
    prov_list: list[RatioProvenance],
    ocf_threshold: float,
) -> YearResult:

    def g(col: str) -> Optional[float]:
        return row.get(col)

    def prov(ratio_name, formula_str, num_name, den_name,
             num_val, den_val, result, metrics_used):
        prov_list.append(_build_provenance(
            ratio_name, formula_str, num_name, den_name,
            num_val, den_val, result, year, resolved_df, metrics_used,
        ))

    yr = YearResult(doc_id=doc_id, year=year)

    # ── Pass-through base metrics ─────────────────────────────────────────
    for col in CANONICAL_COLS:
        setattr(yr, col, g(col))

    # ── 3b. total_debt = long_term_debt + short_term_debt  ────────────────
    ltd = g("long_term_debt") or 0.0
    std = g("short_term_debt") or 0.0
    yr.total_debt = ltd + std if not (_nan(g("long_term_debt")) and _nan(g("short_term_debt"))) else None

    # ── 3a. Liquidity ─────────────────────────────────────────────────────
    ca  = g("current_assets")
    cl  = g("current_liabilities")
    ce  = g("cash_and_equivalents")

    yr.current_ratio = _safe_div(ca, cl, "current_ratio", warn_list)
    prov("current_ratio", "current_assets / current_liabilities",
         "current_assets", "current_liabilities", ca, cl, yr.current_ratio,
         ["current_assets", "current_liabilities"])

    yr.cash_ratio = _safe_div(ce, cl, "cash_ratio", warn_list)
    prov("cash_ratio", "cash_and_equivalents / current_liabilities",
         "cash_and_equivalents", "current_liabilities", ce, cl, yr.cash_ratio,
         ["cash_and_equivalents", "current_liabilities"])

    # ── 3b. Leverage ──────────────────────────────────────────────────────
    te  = g("total_equity")
    tl  = g("total_liabilities")
    ta  = g("total_assets")
    op  = g("operating_profit")
    ie  = g("interest_expense")

    yr.debt_to_equity = _safe_div(yr.total_debt, te, "debt_to_equity", warn_list)
    prov("debt_to_equity", "total_debt / total_equity",
         "total_debt", "total_equity", yr.total_debt, te, yr.debt_to_equity,
         ["long_term_debt", "short_term_debt", "total_equity"])

    yr.debt_ratio = _safe_div(tl, ta, "debt_ratio", warn_list)
    prov("debt_ratio", "total_liabilities / total_assets",
         "total_liabilities", "total_assets", tl, ta, yr.debt_ratio,
         ["total_liabilities", "total_assets"])

    yr.interest_coverage = _safe_div(op, ie, "interest_coverage", warn_list)
    if _nan(ie):
        warn_list.append(f"interest_coverage NaN for {year}: interest_expense missing")
    prov("interest_coverage", "operating_profit / interest_expense",
         "operating_profit", "interest_expense", op, ie, yr.interest_coverage,
         ["operating_profit", "interest_expense"])

    # ── 3c. Profitability ─────────────────────────────────────────────────
    rev = g("revenue")
    np_ = g("net_profit")
    gp  = g("gross_profit")

    if not _nan(rev) and rev == 0:
        warn_list.append(f"Revenue is 0 for {year}; margin ratios set to NaN")

    yr.profit_margin    = _safe_div(np_, rev, "profit_margin",    warn_list)
    yr.operating_margin = _safe_div(op,  rev, "operating_margin", warn_list)
    yr.gross_margin     = _safe_div(gp,  rev, "gross_margin",     warn_list)
    yr.asset_turnover   = _safe_div(rev, ta,  "asset_turnover",   warn_list)

    prov("profit_margin",    "net_profit / revenue",
         "net_profit", "revenue", np_, rev, yr.profit_margin,
         ["net_profit", "revenue"])
    prov("operating_margin", "operating_profit / revenue",
         "operating_profit", "revenue", op, rev, yr.operating_margin,
         ["operating_profit", "revenue"])
    prov("gross_margin",     "gross_profit / revenue",
         "gross_profit", "revenue", gp, rev, yr.gross_margin,
         ["gross_profit", "revenue"])
    prov("asset_turnover",   "revenue / total_assets",
         "revenue", "total_assets", rev, ta, yr.asset_turnover,
         ["revenue", "total_assets"])

    # ── 3d. Cash Flow ─────────────────────────────────────────────────────
    ocf  = g("operating_cash_flow")
    capex = g("capex")

    yr.ocf_to_revenue = _safe_div(ocf, rev, "ocf_to_revenue", warn_list)
    prov("ocf_to_revenue", "operating_cash_flow / revenue",
         "operating_cash_flow", "revenue", ocf, rev, yr.ocf_to_revenue,
         ["operating_cash_flow", "revenue"])

    # free_cash_flow = ocf - abs(capex); if capex NaN treat as 0
    if not _nan(ocf):
        capex_abs = abs(capex) if not _nan(capex) else 0.0
        yr.free_cash_flow = ocf - capex_abs  # type: ignore[operator]
    prov("free_cash_flow", "operating_cash_flow - abs(capex)",
         "operating_cash_flow", "capex", ocf, capex, yr.free_cash_flow,
         ["operating_cash_flow", "capex"])

    # ── 3e. YoY Growth (requires previous year) ───────────────────────────
    if prev_row is not None:
        def _yoy(curr_val, prev_val, name) -> Optional[float]:
            if _nan(curr_val) or _nan(prev_val) or prev_val == 0:
                return None
            return (curr_val - prev_val) / abs(prev_val)  # type: ignore[operator]

        yr.yoy_revenue_growth = _yoy(rev,          prev_row.get("revenue"),       "revenue")
        yr.yoy_profit_growth  = _yoy(np_,          prev_row.get("net_profit"),    "net_profit")
        yr.yoy_asset_growth   = _yoy(ta,            prev_row.get("total_assets"),  "total_assets")

    # ── Risk classifications ──────────────────────────────────────────────
    yr.liquidity_risk     = classify_liquidity(yr.current_ratio)
    yr.debt_risk          = classify_debt(yr.debt_to_equity)
    yr.profitability_risk = classify_profitability(yr.profit_margin)
    yr.cashflow_risk      = classify_cashflow(ocf, threshold=ocf_threshold)
    yr.overall_risk       = compute_overall_risk(
        yr.liquidity_risk, yr.debt_risk,
        yr.profitability_risk, yr.cashflow_risk,
    )

    return yr


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute(
    doc_id: str,
    pivot_df: pd.DataFrame,
    resolved_df: pd.DataFrame,
    output_dir: Optional[Path] = None,
    ocf_threshold: float = DEFAULT_OCF_THRESHOLD,
) -> ComputedResult:
    """
    Main entry point.

    Parameters
    ----------
    doc_id        : Document identifier (md5 hash from extraction pipeline).
    pivot_df      : Year × metric table from the extraction pipeline.
                    Columns: 'year' (int) + the 17 CANONICAL_COLS (float | NaN).
    resolved_df   : Full resolved metrics table (for provenance page_no lookup).
    output_dir    : If provided, saves _computed_metrics.csv and _provenance.json here.
    ocf_threshold : Operating cash-flow threshold for Medium/Low boundary.
                    Default 100 (₹ Crore, Indian ARs).  Use 1_000_000 for USD/SEC.

    Returns
    -------
    ComputedResult
    """
    warn_list: list[str] = []
    prov_list: list[RatioProvenance] = []
    year_results: list[YearResult] = []

    if pivot_df is None or pivot_df.empty:
        warn_list.append("pivot_df is empty — returning empty ComputedResult")
        empty_df = pd.DataFrame()
        return ComputedResult(doc_id=doc_id, by_year=[], as_df=empty_df,
                               warnings=warn_list, provenance=[])

    # Ensure 'year' column exists and coerce to int
    if "year" not in pivot_df.columns:
        warn_list.append("pivot_df has no 'year' column — cannot compute")
        return ComputedResult(doc_id=doc_id, by_year=[], as_df=pd.DataFrame(),
                               warnings=warn_list, provenance=[])

    df = pivot_df.copy()
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df = df.dropna(subset=["year"])
    df["year"] = df["year"].astype(int)
    df = df.sort_values("year").reset_index(drop=True)

    # Ensure all canonical columns exist (fill with NaN if absent)
    for col in CANONICAL_COLS:
        if col not in df.columns:
            df[col] = np.nan

    # Build list of per-year metric dicts (coerce all values to float | None)
    rows: list[dict[str, Optional[float]]] = []
    for _, raw_row in df.iterrows():
        row_dict: dict[str, Optional[float]] = {}
        for col in CANONICAL_COLS:
            row_dict[col] = _coerce(raw_row.get(col))
        rows.append(row_dict)

    years = df["year"].tolist()

    for i, (year, row) in enumerate(zip(years, rows)):
        prev_row = rows[i - 1] if i > 0 else None
        yr = _compute_year(
            doc_id=doc_id,
            year=year,
            row=row,
            prev_row=prev_row,
            resolved_df=resolved_df if resolved_df is not None else pd.DataFrame(),
            warn_list=warn_list,
            prov_list=prov_list,
            ocf_threshold=ocf_threshold,
        )
        year_results.append(yr)

    # Build output DataFrame
    records = [asdict(yr) for yr in year_results]
    result_df = pd.DataFrame(records)

    result = ComputedResult(
        doc_id=doc_id,
        by_year=year_results,
        as_df=result_df,
        warnings=warn_list,
        provenance=prov_list,
    )

    # Optionally persist outputs
    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Derive stem from doc_id (matches pipeline convention)
        csv_path  = output_dir / f"{doc_id}_computed_metrics.csv"
        prov_path = output_dir / f"{doc_id}_provenance.json"

        result_df.to_csv(csv_path, index=False)

        prov_dicts = []
        for p in prov_list:
            d = asdict(p)
            # Convert None → null-friendly values for JSON
            for k, v in d.items():
                if isinstance(v, float) and math.isnan(v):
                    d[k] = None
            prov_dicts.append(d)

        with open(prov_path, "w", encoding="utf-8") as fh:
            json.dump(prov_dicts, fh, indent=2, default=str)

    return result


# ---------------------------------------------------------------------------
# Convenience: load from saved CSVs (used by the API layer)
# ---------------------------------------------------------------------------

def load_from_csv(doc_id: str, output_dir: Path) -> Optional[pd.DataFrame]:
    """
    Re-load a previously saved computed_metrics CSV as a DataFrame.
    Returns None if the file does not exist.
    """
    path = Path(output_dir) / f"{doc_id}_computed_metrics.csv"
    if not path.exists():
        return None
    return pd.read_csv(path)


def load_provenance(doc_id: str, output_dir: Path) -> Optional[list[dict]]:
    """Re-load a previously saved provenance JSON. Returns None if missing."""
    path = Path(output_dir) / f"{doc_id}_provenance.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== numerical_module smoke test ===")

    # Build a minimal synthetic pivot_df for quick validation
    synthetic = pd.DataFrame([
        {
            "year": 2023,
            "revenue": 2000.0, "gross_profit": 600.0, "operating_profit": 300.0,
            "net_profit": 200.0, "interest_expense": 50.0,
            "total_assets": 5000.0, "current_assets": 1200.0,
            "cash_and_equivalents": 400.0, "total_liabilities": 3000.0,
            "current_liabilities": 800.0, "long_term_debt": 1000.0,
            "short_term_debt": 500.0, "total_equity": 2000.0,
            "operating_cash_flow": 250.0, "investing_cash_flow": -100.0,
            "financing_cash_flow": -80.0, "capex": 120.0,
        },
        {
            "year": 2024,
            "revenue": 2400.0, "gross_profit": 750.0, "operating_profit": 380.0,
            "net_profit": 260.0, "interest_expense": 55.0,
            "total_assets": 5500.0, "current_assets": 1400.0,
            "cash_and_equivalents": 500.0, "total_liabilities": 3200.0,
            "current_liabilities": 900.0, "long_term_debt": 950.0,
            "short_term_debt": 480.0, "total_equity": 2300.0,
            "operating_cash_flow": 310.0, "investing_cash_flow": -150.0,
            "financing_cash_flow": -90.0, "capex": 150.0,
        },
    ])

    result = compute("TEST_DOC", synthetic, pd.DataFrame())

    for yr in result.by_year:
        print(f"\n--- Year {yr.year} ---")
        print(f"  current_ratio:    {yr.current_ratio:.3f}" if yr.current_ratio else "  current_ratio:    N/A")
        print(f"  debt_to_equity:   {yr.debt_to_equity:.3f}" if yr.debt_to_equity else "  debt_to_equity:   N/A")
        print(f"  profit_margin:    {yr.profit_margin:.3f}" if yr.profit_margin else "  profit_margin:    N/A")
        print(f"  free_cash_flow:   {yr.free_cash_flow:.1f}" if yr.free_cash_flow else "  free_cash_flow:   N/A")
        print(f"  yoy_revenue_growth: {yr.yoy_revenue_growth:.3f}" if yr.yoy_revenue_growth else "  yoy_revenue_growth: N/A")
        print(f"  liquidity_risk:   {yr.liquidity_risk}")
        print(f"  debt_risk:        {yr.debt_risk}")
        print(f"  profitability_risk: {yr.profitability_risk}")
        print(f"  cashflow_risk:    {yr.cashflow_risk}")
        print(f"  overall_risk:     {yr.overall_risk}")

    if result.warnings:
        print("\nWarnings:")
        for w in result.warnings:
            print(f"  ! {w}")

    print(f"\nProvenance entries: {len(result.provenance)}")
    print("OK")
