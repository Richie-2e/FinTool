"""
metric_extractor.py
Stage 3 — Metric extraction.

Primary path:  DoclingTable DataFrames  → CandidateMetric list
Fallback path: raw page text (line-by-line) → CandidateMetric list

Resolution, pivot, derived totals, and accounting checks are all here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd

from extraction.canonical_metrics import (
    CANONICAL_METRICS,
    DATE_YEAR_RE,
    YEAR_RE,
    SECTION_KEYWORDS,
    match_metric,
)
from extraction.parser import DoclingTable
from extraction.classifier import PageClassification


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CORE_METRIC_SET = [
    "revenue", "net_profit", "total_assets", "current_assets",
    "current_liabilities", "total_equity", "total_liabilities",
    "operating_cash_flow",
]

FINANCIAL_STATEMENT_TYPES = {"balance_sheet", "income_statement", "cash_flow"}

# For detecting reporting unit from page headers
_UNIT_HINTS: dict[str, list[str]] = {
    "crore":   ["in crore", "(₹ in crore)", "(rs. in crore)", "(₹ crore)", " crore)"],
    "lakh":    ["in lakh", "(₹ in lakh)", "(rs. in lakh)"],
    "million": ["in million", "usd million", "$ million"],
}

# Prose phrases that indicate a line is not a table row
_BAD_PROSE_PHRASES = [
    "higher by", "as compared", "compared to", "previous year",
    "during the year", "the company", "the group", "consists of",
    "management discussion", "integrated report", "annual report",
    "sensitivity", "assumption", "quoted", "unquoted",
]

_NUMBER_RE = re.compile(r"\(?[-−]?\d[\d,]*(?:\.\d+)?\)?")


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class CandidateMetric:
    doc_id:         str
    metric_name:    str
    raw_label:      str
    value:          float
    unit:           str
    year:           Optional[int]
    page_no:        Optional[int]
    statement_type: str
    section_type:   str
    confidence:     str   # "high" | "medium" | "low" | "derived"
    source:         str   # "docling_table" | "line_parse_fallback"
    evidence:       str = ""   # verbatim source snippet (LLM path only); default keeps all other call sites valid
    # Phase B/C (structural provenance + L6) -- all optional, default None,
    # so no existing call site needs to change. Populated by
    # extraction/llm/structural_validator.py::run_structural_validation()
    # when structural table data is available; left None otherwise (e.g. the
    # V1 regex fallback path, which never runs L6).
    verification_state:  Optional[str] = None   # "VERIFIED" | "NEEDS_REVIEW" | None (not checked)
    verification_reason: Optional[str] = None
    table_id:  Optional[str] = None
    row_index: Optional[int] = None
    col_index: Optional[int] = None


# ---------------------------------------------------------------------------
# Number parsing
# ---------------------------------------------------------------------------

def parse_value(raw: str) -> Optional[float]:
    """
    Parse an Indian financial number string to float.
    - Parentheses → negative: "(4,521)" → -4521.0
    - Commas stripped: "1,89,483" → 189483.0
    - Dashes / em-dashes / nil / na → None
    - Decimal points preserved: "42.5" → 42.5
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s in {"", "-", "--", "—", "–", "na", "n/a", "nil", "Nil", "NA", "N/A"}:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = s.replace("−", "-").replace("–", "-")   # Unicode minus → ASCII
    s = re.sub(r"[₹$€£]", "", s)                # strip currency symbols
    s = re.sub(r"[Rr][Ss]\.?", "", s)           # strip Rs / Rs. prefix
    s = s.replace(",", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return None
    val = float(s)
    return -val if negative else val


def _detect_unit(page_text: str) -> str:
    low = (page_text or "").lower()
    for unit, hints in _UNIT_HINTS.items():
        if any(h in low for h in hints):
            return unit
    return "as_reported"


def _normalize_label(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip().lower())
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"\[[^\]]+\]", "", text)  # strip footnote refs like [1]
    # Strip leading bullet/numbering ONLY when followed by a non-letter delimiter
    # e.g. "1. Total" → "Total",  "a) Revenue" → "Revenue"
    # but NOT "current" → "urrent"
    text = re.sub(r"^[\(\)ivxabc\d]+[.)\s]\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Year detection helpers (header rows only — fixes year-leak bug)
# ---------------------------------------------------------------------------

def _years_from_cell(cell_text: str) -> list[int]:
    """Extract all valid years (2000-2100) from a single header cell."""
    years: list[int] = []
    for m in DATE_YEAR_RE.finditer(str(cell_text)):
        for g in m.groups():
            if g:
                y = int(g)
                if 2000 <= y <= 2100 and y not in years:
                    years.append(y)
    for m in YEAR_RE.finditer(str(cell_text)):
        y = int(m.group(1))
        if 2000 <= y <= 2100 and y not in years:
            years.append(y)
    return years


def detect_year_columns(df: pd.DataFrame) -> list[tuple[int, int]]:
    """
    Scan ONLY the first 2 rows of df (header rows) to find year columns.
    Returns list of (year, col_index) ordered by col_index.
    Col 0 (label column) is always skipped.

    This is the fix for the year-leak bug: we never inspect data rows for years.
    """
    if df.empty or len(df.columns) < 2:
        return []

    year_cols: list[tuple[int, int]] = []
    seen_years: set[int] = set()

    n_header = min(2, len(df))
    for col_idx in range(1, len(df.columns)):
        for row_idx in range(n_header):
            cell = str(df.iloc[row_idx, col_idx]).strip()
            for y in _years_from_cell(cell):
                if y not in seen_years:
                    year_cols.append((y, col_idx))
                    seen_years.add(y)
                    break  # one year per column; move to next col

    year_cols.sort(key=lambda t: t[1])
    return year_cols


def _find_data_start_row(df: pd.DataFrame, year_col_indices: list[int]) -> int:
    """
    Return the first row index that does NOT contain year patterns in
    the numeric columns. Typically 1 or 2.
    """
    if not year_col_indices:
        return 1
    for row_idx in range(min(3, len(df))):
        row_has_year = any(
            bool(_years_from_cell(str(df.iloc[row_idx, ci])))
            for ci in year_col_indices
        )
        row_has_unit_hint = any(
            kw in str(df.iloc[row_idx, ci]).lower()
            for ci in year_col_indices
            for kw in ("crore", "lakh", "million", "₹", "rs.", "note", "%")
        )
        if not row_has_year and not row_has_unit_hint:
            return row_idx
    return min(3, len(df))


# ---------------------------------------------------------------------------
# Function 1 (public alias — used by pipeline.py)
# detect_year_columns is already defined above
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Function 2 — Primary extraction from Docling tables
# ---------------------------------------------------------------------------

def extract_from_docling_tables(
    tables: list[DoclingTable],
    page_classifications: list[PageClassification],
    doc_id: str,
) -> list[CandidateMetric]:
    """
    For each table whose page is classified as a financial statement:
      1. Detect year columns from header rows only
      2. Skip header rows; iterate data rows
      3. Match row label against CANONICAL_METRICS
      4. Emit one CandidateMetric per (metric, year) pair
    """
    page_map: dict[int, PageClassification] = {c.page_no: c for c in page_classifications}
    candidates: list[CandidateMetric] = []

    for table in tables:
        pc = page_map.get(table.page_no)
        stmt_type = getattr(table, "statement_type", None) or (pc.statement_type if pc else "other")
        if stmt_type not in FINANCIAL_STATEMENT_TYPES:
            continue

        section_type = table.section_hint if table.section_hint != "unknown" else (
            pc.section_type if pc else "unknown"
        )

        df = table.df
        if df.empty or len(df.columns) < 2:
            continue

        year_cols = detect_year_columns(df)
        if not year_cols:
            continue

        year_col_indices = [ci for _, ci in year_cols]
        data_start = _find_data_start_row(df, year_col_indices)

        # Detect unit from page text (use table caption as hint first)
        unit = _detect_unit(table.caption)
        if unit == "as_reported" and pc:
            # will be improved by pipeline.py passing page text; best effort here
            unit = "crore"  # default for Indian ARs

        for row_idx in range(data_start, len(df)):
            row = df.iloc[row_idx]

            # Label = first non-empty cell in the row
            label_raw = ""
            for ci in range(len(df.columns)):
                cell = str(row.iloc[ci]).strip()
                if cell and cell.lower() not in {"nan", "none", ""}:
                    label_raw = cell
                    break
            if not label_raw:
                continue

            label_norm = _normalize_label(label_raw)
            metric_name = match_metric(label_norm, stmt_type)
            if not metric_name:
                continue

            for year, col_idx in year_cols:
                if col_idx >= len(row):
                    continue
                raw_val = str(row.iloc[col_idx]).strip()
                value = parse_value(raw_val)
                if value is None:
                    continue

                candidates.append(CandidateMetric(
                    doc_id=doc_id,
                    metric_name=metric_name,
                    raw_label=label_raw,
                    value=value,
                    unit=unit,
                    year=year,
                    page_no=table.page_no,
                    statement_type=stmt_type,
                    section_type=section_type,
                    confidence="high",
                    source="docling_table",
                ))

    return candidates


# ---------------------------------------------------------------------------
# Function 3 — Fallback line-by-line extraction
# ---------------------------------------------------------------------------

def extract_candidates_from_page(
    page_text: str,
    page_no: int,
    statement_type: str,
    section_type: str,
    doc_id: str,
    document_years: Optional[list[int]] = None,
) -> list[CandidateMetric]:
    """
    Line-by-line fallback parser. Used when Docling tables are unavailable
    or a table has < 2 columns.
    Confidence = "low", source = "line_parse_fallback".
    """
    if statement_type not in FINANCIAL_STATEMENT_TYPES:
        return []

    document_years = document_years or []
    unit = _detect_unit(page_text)
    candidates: list[CandidateMetric] = []

    # Detect years from header region (top 12 lines only — year-leak fix)
    header = "\n".join(page_text.splitlines()[:12])
    page_years: list[int] = []
    for m in DATE_YEAR_RE.finditer(header):
        for g in m.groups():
            if g:
                y = int(g)
                if 2000 <= y <= 2100 and y not in page_years:
                    page_years.append(y)
    for m in YEAR_RE.finditer(header):
        y = int(m.group(1))
        if 2000 <= y <= 2100 and y not in page_years:
            page_years.append(y)

    if not page_years and document_years:
        page_years = document_years[:2]
    page_years = page_years[:2]

    for line in page_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) > 200:
            continue
        if any(p in line.lower() for p in _BAD_PROSE_PHRASES):
            continue

        # For wide balance-sheet lines, split on large whitespace gaps
        segments = [line]
        if statement_type == "balance_sheet":
            chunks = [c for c in re.split(r"\s{12,}", line) if c.strip()]
            if len(chunks) >= 2:
                segments = chunks

        for segment in segments:
            numbers_raw = _NUMBER_RE.findall(segment)
            values = [parse_value(x) for x in numbers_raw]
            values = [v for v in values if v is not None]
            # drop stray year-like numbers from value list
            values = [v for v in values if not (2000 <= abs(v) <= 2100)]
            values = values[:2]
            if not values:
                continue

            label_raw = _NUMBER_RE.sub(" ", segment).strip()
            label_norm = _normalize_label(label_raw)
            if not label_norm:
                continue

            metric_name = match_metric(label_norm, statement_type)
            if not metric_name:
                continue

            target_years = (page_years[:len(values)] if page_years
                            else [None] * len(values))
            if len(target_years) < len(values):
                target_years += [None] * (len(values) - len(target_years))

            for year, val in zip(target_years, values):
                candidates.append(CandidateMetric(
                    doc_id=doc_id,
                    metric_name=metric_name,
                    raw_label=label_raw,
                    value=val,
                    unit=unit,
                    year=year,
                    page_no=page_no,
                    statement_type=statement_type,
                    section_type=section_type,
                    confidence="low",
                    source="line_parse_fallback",
                ))

    return candidates


# ---------------------------------------------------------------------------
# Function 4 — Resolution
# ---------------------------------------------------------------------------

_SECTION_RANK = {"consolidated": 2, "unknown": 1, "standalone": 0}
_CONF_RANK    = {"high": 3, "medium": 2, "low": 1, "derived": 0}
_SOURCE_RANK  = {"docling_table": 1, "line_parse_fallback": 0}


def build_resolved_metrics_df(
    candidates: list[CandidateMetric],
) -> tuple[pd.DataFrame, dict]:
    """
    Deduplicate candidates to one row per (metric_name, year).

    Priority (descending):
      1. consolidated > standalone (section_type rank)
      2. high > medium > low confidence
      3. docling_table > line_parse_fallback
      4. first found (stable sort)

    Returns (resolved_df, quality_report) where resolved_df has the exact
    column contract required by numerical_module.py.
    """
    quality: dict = {
        "candidate_rows": len(candidates),
        "resolved_rows": 0,
        "missing_core_metrics_by_year": {},
        "issues": [],
    }

    if not candidates:
        quality["issues"].append("No candidate metrics extracted.")
        empty = pd.DataFrame(columns=[
            "doc_id", "metric_name", "value", "unit", "year", "page_no",
            "raw_label", "statement_type", "section_type", "confidence", "source",
            "evidence", "verification_state", "verification_reason",
            "table_id", "row_index", "col_index",
        ])
        return empty, quality

    df = pd.DataFrame([asdict(c) for c in candidates])
    df = df.dropna(subset=["year"])
    df = df[(df["year"] >= 2000) & (df["year"] <= 2100)]

    if df.empty:
        quality["issues"].append("All candidates had invalid or missing years.")
        return df, quality

    df["year"] = df["year"].astype(int)

    # Sort by priority (descending = best first)
    df["_sec_rank"]  = df["section_type"].map(_SECTION_RANK).fillna(1)
    df["_conf_rank"] = df["confidence"].map(_CONF_RANK).fillna(0)
    df["_src_rank"]  = df["source"].map(_SOURCE_RANK).fillna(0)
    # Prefer labels starting with "total " (more definitive)
    df["_lbl_rank"]  = df["raw_label"].str.lower().str.startswith("total ").astype(int)

    df = df.sort_values(
        ["metric_name", "year", "_sec_rank", "_conf_rank", "_src_rank", "_lbl_rank"],
        ascending=[True, True, False, False, False, False],
    )
    df = df.drop_duplicates(subset=["metric_name", "year"], keep="first")
    df = df.drop(columns=["_sec_rank", "_conf_rank", "_src_rank", "_lbl_rank"])

    # Apply derived totals and accounting checks
    df = add_derived_totals_if_possible(df)

    # Enforce exact column contract
    # "evidence" added (Phase A, next-architecture review): the verbatim
    # grounding snippet CandidateMetric already carries -- previously computed
    # and validated by L4/L5, then dropped here before reaching persistence.
    # Purely additive to the contract; no existing column removed or reordered.
    # Phase B/C: verification_state/verification_reason/table_id/row_index/
    # col_index added -- populated by L6 (structural_validator.py) when
    # structural table data was available for a candidate's page, else left
    # None (e.g. V1 regex-fallback candidates, which never run L6). Purely
    # additive; no existing column removed or reordered.
    col_order = [
        "doc_id", "metric_name", "value", "unit", "year", "page_no",
        "raw_label", "statement_type", "section_type", "confidence", "source",
        "evidence", "verification_state", "verification_reason",
        "table_id", "row_index", "col_index",
    ]
    resolved_df = df[col_order].reset_index(drop=True)
    quality["resolved_rows"] = len(resolved_df)

    # Missing core metrics by year
    for year in sorted(resolved_df["year"].unique()):
        yr_metrics = set(resolved_df[resolved_df["year"] == year]["metric_name"])
        missing = [m for m in CORE_METRIC_SET if m not in yr_metrics]
        if missing:
            quality["missing_core_metrics_by_year"][str(int(year))] = missing

    # Accounting checks
    quality["issues"].extend(run_accounting_checks(resolved_df))

    return resolved_df, quality


# ---------------------------------------------------------------------------
# Function 5 — Pivot
# ---------------------------------------------------------------------------

def pivot_metrics(resolved_df: pd.DataFrame) -> pd.DataFrame:
    """
    Reshape resolved_df to: rows = years, columns = canonical metric names.
    Missing metrics are NaN.
    """
    if resolved_df.empty:
        return pd.DataFrame()

    pivot = resolved_df.pivot_table(
        index="year",
        columns="metric_name",
        values="value",
        aggfunc="first",
    )
    pivot.columns.name = None
    pivot = pivot.sort_index()

    # Ensure all 17 canonical metrics are present (NaN if missing)
    for metric in CANONICAL_METRICS:
        if metric not in pivot.columns:
            pivot[metric] = float("nan")

    return pivot.reset_index()


# ---------------------------------------------------------------------------
# Function 6 — Derived totals
# ---------------------------------------------------------------------------

def add_derived_totals_if_possible(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill in derivable totals that are missing.
    Rules (per spec):
      - total_liabilities missing + current_liabilities + long_term_debt exist
        → total_liabilities = current_liabilities + long_term_debt
      - total_assets missing + total_equity + total_liabilities exist
        → total_assets = total_equity + total_liabilities
      - gross_profit: do NOT derive (different concept from operating_profit)
    """
    if df.empty:
        return df.copy()

    out = df.copy()
    years = sorted(out["year"].dropna().unique())
    additions: list[dict] = []

    def _get(sub: pd.DataFrame, metric: str) -> Optional[float]:
        rows = sub[sub["metric_name"] == metric]
        if rows.empty:
            return None
        v = rows.iloc[0]["value"]
        return None if (v is None or (isinstance(v, float) and np.isnan(v))) else float(v)

    def _base(sub: pd.DataFrame, metric: str) -> dict:
        rows = sub[sub["metric_name"] == metric]
        if rows.empty:
            return {}
        return rows.iloc[0].to_dict()

    for year in years:
        sub = out[out["year"] == year]

        # Rule 1: total_liabilities = current_liabilities + long_term_debt (+ short_term_debt)
        if _get(sub, "total_liabilities") is None:
            cl = _get(sub, "current_liabilities")
            ltd = _get(sub, "long_term_debt")
            std = _get(sub, "short_term_debt") or 0.0
            if cl is not None and ltd is not None:
                base = _base(sub, "current_liabilities")
                additions.append({
                    **base,
                    "metric_name": "total_liabilities",
                    "value": cl + ltd + std,
                    "raw_label": "derived(cl+ltd+std)",
                    "statement_type": "balance_sheet",
                    "confidence": "derived",
                    "source": "line_parse_fallback",
                    "year": year,
                })

        # Rule 2: total_assets = total_equity + total_liabilities
        if _get(sub, "total_assets") is None:
            eq = _get(sub, "total_equity")
            liab = _get(sub, "total_liabilities")
            if eq is not None and liab is not None:
                base = _base(sub, "total_equity")
                additions.append({
                    **base,
                    "metric_name": "total_assets",
                    "value": eq + liab,
                    "raw_label": "derived(equity+liab)",
                    "statement_type": "balance_sheet",
                    "confidence": "derived",
                    "source": "line_parse_fallback",
                    "year": year,
                })

    if additions:
        out = pd.concat([out, pd.DataFrame(additions)], ignore_index=True)

    return out


# ---------------------------------------------------------------------------
# Function 7 — Accounting checks
# ---------------------------------------------------------------------------

def run_accounting_checks(resolved_df: pd.DataFrame) -> list[str]:
    """
    Returns a list of warning strings for failed sanity checks.
    Checks per year:
      - total_assets ≈ total_equity + total_liabilities (within 3%)
      - revenue > net_profit (sanity check)
    """
    warnings: list[str] = []
    if resolved_df.empty:
        return warnings

    for year in sorted(resolved_df["year"].dropna().unique()):
        sub = resolved_df[resolved_df["year"] == year]
        metrics = {r["metric_name"]: r["value"] for _, r in sub.iterrows()}

        assets = metrics.get("total_assets")
        equity = metrics.get("total_equity")
        liab   = metrics.get("total_liabilities")

        if all(v is not None for v in (assets, equity, liab)):
            try:
                rhs = float(equity) + float(liab)
                lhs = float(assets)
                denom = max(abs(lhs), abs(rhs), 1.0)
                rel_err = abs(lhs - rhs) / denom
                if rel_err > 0.03:
                    warnings.append(
                        f"Year {int(year)}: total_assets ({lhs:.0f}) ≠ "
                        f"total_equity + total_liabilities ({rhs:.0f}) — "
                        f"relative error {rel_err:.2%}"
                    )
            except (TypeError, ValueError):
                pass

        revenue    = metrics.get("revenue")
        net_profit = metrics.get("net_profit")

        if revenue is not None and net_profit is not None:
            try:
                r, p = float(revenue), float(net_profit)
                if abs(r) > 0 and abs(p) > abs(r):
                    warnings.append(
                        f"Year {int(year)}: net_profit ({p:.0f}) exceeds revenue ({r:.0f}) — "
                        "possible extraction error"
                    )
            except (TypeError, ValueError):
                pass

    return warnings
