# SPEC_computation.md
## FinTool — Computation & Risk Classification Module Specification
**Version:** 1.0 | **Status:** Draft | **Owners:** Group 8

---

## 0. Design Principle

> **The LLM never computes a number. The LLM only explains numbers that this module has already computed.**

This module is pure deterministic Python. Same input → same output, always.
Every formula, every threshold, every risk label is explicit, auditable, and sourced
from standard financial analysis practice (cross-validated against EDGAR benchmark data).

---

## 1. What this module does (and does not do)

**Does:**
- Takes `pivot_df` (year × metric table) from the extraction module
- Computes all derived financial ratios
- Classifies four risk dimensions: Liquidity, Debt, Profitability, Cash Flow
- Computes YoY growth rates
- Returns a `ComputedResult` with full provenance for every number

**Does NOT:**
- Call any LLM or external API
- Generate explanations or natural language
- Access the internet
- Read the original PDF

---

## 2. Input Contract

```python
# Input: pivot_df from extraction pipeline
# Schema: one row per year, columns = canonical metric names

pivot_df columns (all float, NaN if not extracted):
    year                   int
    revenue                float   # ₹ Crore or USD millions
    gross_profit           float
    operating_profit       float
    net_profit             float
    interest_expense       float
    total_assets           float
    current_assets         float
    cash_and_equivalents   float
    total_liabilities      float
    current_liabilities    float
    long_term_debt         float
    short_term_debt        float
    total_equity           float
    operating_cash_flow    float
    investing_cash_flow    float
    financing_cash_flow    float
    capex                  float
```

The `doc_id` is passed as a separate argument (not a column).

---

## 3. Computed Ratios — Full Formula Registry

All formulas here are sourced from the EDGAR notebook's `compute_derived_metrics()`.
They are reproduced here as the authoritative spec so the computation module
can be built directly from this document.

### 3a. Liquidity Ratios

```
current_ratio     = current_assets / current_liabilities
cash_ratio        = cash_and_equivalents / current_liabilities
```

Guard: if denominator == 0 or NaN → output NaN (never divide by zero).

### 3b. Leverage / Debt Ratios

```
total_debt        = long_term_debt + short_term_debt   (use 0 if either is NaN)
debt_to_equity    = total_debt / total_equity
debt_ratio        = total_liabilities / total_assets
interest_coverage = operating_profit / interest_expense
```

### 3c. Profitability Ratios

```
profit_margin     = net_profit / revenue
operating_margin  = operating_profit / revenue
gross_margin      = gross_profit / revenue
asset_turnover    = revenue / total_assets
```

### 3d. Cash Flow Metrics

```
ocf_to_revenue    = operating_cash_flow / revenue
free_cash_flow    = operating_cash_flow - abs(capex)
```

### 3e. Year-over-Year Growth (requires ≥ 2 years of data)

```
yoy_revenue_growth  = (revenue[year] - revenue[year-1]) / abs(revenue[year-1])
yoy_profit_growth   = (net_profit[year] - net_profit[year-1]) / abs(net_profit[year-1])
yoy_asset_growth    = (total_assets[year] - total_assets[year-1]) / abs(total_assets[year-1])
```

Guard: if prior year value is 0 or NaN → output NaN.

---

## 4. Risk Classification Rules

These thresholds are fixed. They are not configurable at runtime.
They were calibrated against EDGAR benchmark data and standard financial textbook practice.

### 4a. Liquidity Risk (from `current_ratio`)

| Current Ratio | Risk Level |
|---|---|
| < 1.0 | **High** — current liabilities exceed current assets; default risk |
| 1.0 – 1.5 | **Medium** — tight but manageable |
| ≥ 1.5 | **Low** — comfortable liquidity buffer |
| NaN | **Unknown** |

```python
def classify_liquidity(current_ratio: float | None) -> str:
    if current_ratio is None or np.isnan(current_ratio): return "Unknown"
    if current_ratio < 1.0:  return "High"
    if current_ratio < 1.5:  return "Medium"
    return "Low"
```

### 4b. Debt Risk (from `debt_to_equity`)

| Debt-to-Equity | Risk Level |
|---|---|
| > 2.0 | **High** — heavily leveraged |
| 1.0 – 2.0 | **Medium** — moderate leverage |
| < 1.0 | **Low** — conservatively financed |
| NaN | **Unknown** |

```python
def classify_debt(debt_to_equity: float | None) -> str:
    if debt_to_equity is None or np.isnan(debt_to_equity): return "Unknown"
    if debt_to_equity > 2.0: return "High"
    if debt_to_equity > 1.0: return "Medium"
    return "Low"
```

### 4c. Profitability Risk (from `profit_margin`)

| Profit Margin | Risk Level |
|---|---|
| < 0 | **High** — company is making a net loss |
| 0 – 5% | **Medium** — thin margins, vulnerable to shocks |
| ≥ 5% | **Low** — healthy profitability |
| NaN | **Unknown** |

```python
def classify_profitability(profit_margin: float | None) -> str:
    if profit_margin is None or np.isnan(profit_margin): return "Unknown"
    if profit_margin < 0:    return "High"
    if profit_margin < 0.05: return "Medium"
    return "Low"
```

### 4d. Cash Flow Risk (from `operating_cash_flow`)

| Operating Cash Flow | Risk Level |
|---|---|
| < 0 | **High** — burning cash from operations |
| 0 – small positive | **Medium** — barely cash-generating |
| Comfortably positive | **Low** — healthy operational cash generation |
| NaN | **Unknown** |

```python
def classify_cashflow(operating_cash_flow: float | None) -> str:
    if operating_cash_flow is None or np.isnan(operating_cash_flow): return "Unknown"
    if operating_cash_flow < 0:   return "High"
    if operating_cash_flow < 100: return "Medium"   # < ₹100 Cr is marginal for Indian cos
    return "Low"
```

**Note:** The threshold of 100 crore is for Indian company reports. For SEC filings (USD),
replace with `1_000_000` (1 million USD) as in the EDGAR notebook.

### 4e. Overall Risk (worst of four)

```python
RISK_ORDER = {"High": 3, "Medium": 2, "Low": 1, "Unknown": 0}

def compute_overall_risk(liquidity, debt, profitability, cashflow) -> str:
    return max([liquidity, debt, profitability, cashflow],
               key=lambda x: RISK_ORDER.get(x, 0))
```

---

## 5. Output Contract

```python
@dataclass
class YearResult:
    doc_id:             str
    year:               int
    # Base metrics (pass-through from pivot_df)
    revenue:            float | None
    net_profit:         float | None
    # ... (all canonical metrics)

    # Computed ratios
    current_ratio:          float | None
    cash_ratio:             float | None
    debt_to_equity:         float | None
    debt_ratio:             float | None
    interest_coverage:      float | None
    profit_margin:          float | None
    operating_margin:       float | None
    gross_margin:           float | None
    asset_turnover:         float | None
    ocf_to_revenue:         float | None
    free_cash_flow:         float | None
    total_debt:             float | None
    yoy_revenue_growth:     float | None    # NaN for earliest year
    yoy_profit_growth:      float | None
    yoy_asset_growth:       float | None

    # Risk classifications
    liquidity_risk:         str    # "High" | "Medium" | "Low" | "Unknown"
    debt_risk:              str
    profitability_risk:     str
    cashflow_risk:          str
    overall_risk:           str

@dataclass
class ComputedResult:
    doc_id:     str
    by_year:    list[YearResult]       # one entry per year in the pivot
    as_df:      pd.DataFrame           # same data as a DataFrame (for API use)
    warnings:   list[str]              # e.g. "interest_coverage NaN for 2024: interest_expense missing"
```

**Output file saved by this module:**
```
{stem}_computed_metrics.csv    # ComputedResult.as_df
```
This is equivalent to `05_computed_metrics.csv` from the EDGAR notebook — same schema.

---

## 6. Provenance Metadata (for Explainability Layer)

Every computed ratio must carry its provenance so the explainability layer
can show the formula and source values in the UI.

```python
@dataclass
class RatioProvenance:
    ratio_name:     str      # e.g. "current_ratio"
    formula_str:    str      # e.g. "current_assets / current_liabilities"
    numerator:      str      # canonical metric name
    denominator:    str      # canonical metric name (or None for absolute metrics)
    numerator_val:  float | None
    denominator_val:float | None
    result:         float | None
    year:           int
    source_pages:   list[int]    # page_no from resolved_metrics_df for each input
```

This dict is serialized to `{stem}_provenance.json` for the API to serve.

---

## 7. Module Interface

```python
# numerical_module.py

def compute(
    doc_id: str,
    pivot_df: pd.DataFrame,
    resolved_df: pd.DataFrame,    # needed for source_pages provenance
    output_dir: Path | None = None,
) -> ComputedResult:
    """
    Main entry point.
    Takes the pivot table from the extraction module.
    Returns ComputedResult with all ratios, risks, and provenance.
    Optionally saves CSV to output_dir.
    """
```

---

## 8. Edge Cases and Guards

| Situation | Behaviour |
|---|---|
| Only 1 year of data | YoY growth fields all NaN; no error raised |
| `current_liabilities` == 0 | `current_ratio` = NaN, log warning |
| `total_equity` is negative | `debt_to_equity` computed normally; negative equity signals insolvency — the risk classifier correctly returns "High" |
| All metrics NaN for a year | Year still appears in output with all ratios NaN and all risks "Unknown" |
| Revenue = 0 | Margin ratios = NaN; log warning |
| `capex` is NaN | `free_cash_flow` = `operating_cash_flow` (treat capex as 0) |

---

## 9. Acceptance Tests

| Test | Pass Criterion |
|---|---|
| Tata Steel 2024 | `current_ratio` matches manual calculation ±0.01 |
| Reliance 2024 | `debt_to_equity` in expected range (< 1.5 for Reliance) |
| Loss-making company | `profitability_risk` = "High", `overall_risk` = "High" |
| Zero-division guard | Passing `current_liabilities=0` returns `current_ratio=NaN`, no exception |
| YoY growth | With 3 years of data, year[0] has NaN growth, years [1] and [2] have values |
| Provenance roundtrip | `provenance.json` contains `source_pages` list for every ratio |
| EDGAR validation | Computed ratios for 5 SEC companies match EDGAR `05_computed_metrics.csv` within 5% |
