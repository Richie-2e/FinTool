"""
canonical_metrics.py
Shared metric schema for the FinTool extraction pipeline.
Used by metric_extractor.py (Indian AR labels) and the EDGAR module (XBRL tags).
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Canonical metric registry
# 17 Phase-1 metrics; keys are the exact names used throughout the pipeline.
# ---------------------------------------------------------------------------

CANONICAL_METRICS: dict[str, dict] = {
    # ── Income Statement ────────────────────────────────────────────────────
    "revenue": {
        "statement": "income_statement",
        "patterns": [
            r"^revenue from operations$",
            r"^revenue$",
            r"^turnover$",
            r"^sales$",
            r"^net sales$",
            r"^income from operations$",
            r"^net revenue$",
            r"^total revenue$",
            r"^value of sales(?: & services)?(?: \(revenue\))?$",
        ],
        "xbrl_tags": [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
        ],
        "exclude": [
            r"segment",
            r"ebitda",
            r"margin",
            r"deliveries",
            r"higher by",
            r"previous year",
            r"operations stood at",
            r"turnover at",
            r"%",
        ],
    },
    "gross_profit": {
        "statement": "income_statement",
        "patterns": [
            r"^gross profit$",
            r"^gross profit/\(loss\)$",
        ],
        "xbrl_tags": ["GrossProfit"],
        "exclude": [r"margin", r"%", r"higher by", r"previous year"],
    },
    "operating_profit": {
        "statement": "income_statement",
        "patterns": [
            r"^operating profit$",
            r"^profit from operations$",
            r"^profit before finance costs and depreciation$",
            r"^ebit$",
            r"^earnings before interest and tax$",
            r"^profit before interest, depreciation and tax$",
            r"^profit before interest and tax$",
            r"^operating income$",
        ],
        "xbrl_tags": ["OperatingIncomeLoss"],
        "exclude": [r"margin", r"%", r"higher by", r"previous year", r"segment"],
    },
    "net_profit": {
        "statement": "income_statement",
        "patterns": [
            r"^profit for the year$",
            r"^profit for the period$",
            r"^profit after tax$",
            r"^net profit$",
            r"^net income$",
            r"^profit /\(loss\) for the year$",
            r"^profit \(loss\) for the year$",
            r"^loss for the year$",
            r"^profit after tax for the year$",
            r"^net profit attributable to:?$",
        ],
        "xbrl_tags": ["NetIncomeLoss"],
        "exclude": [r"margin", r"net profit margin", r"%", r"higher by", r"previous year"],
    },
    "interest_expense": {
        "statement": "income_statement",
        "patterns": [
            r"^finance costs$",
            r"^interest expense$",
            r"^finance charges$",
            r"^interest and finance charges$",
            r"^interest cost$",
            r"^borrowing costs$",
            r"^finance cost$",
        ],
        "xbrl_tags": ["InterestExpense", "InterestAndDebtExpense"],
        "exclude": [r"capitalised", r"capitalized", r"%", r"higher by"],
    },
    # ── Balance Sheet ───────────────────────────────────────────────────────
    "total_assets": {
        "statement": "balance_sheet",
        "patterns": [
            r"^total assets$",
        ],
        "xbrl_tags": ["Assets"],
        "exclude": [r"and liabilities", r"liabilities and equity", r"net"],
    },
    "current_assets": {
        "statement": "balance_sheet",
        "patterns": [
            r"^total current assets$",
            r"^current assets$",
        ],
        "xbrl_tags": ["AssetsCurrent"],
        "exclude": [r"other current assets", r"charge on the company", r"pari-passu"],
    },
    "cash_and_equivalents": {
        "statement": "balance_sheet",
        "patterns": [
            r"^cash and cash equivalents$",
            r"^cash and bank balances$",
            r"^cash and cash equivalent$",
            r"^cash & cash equivalents$",
        ],
        "xbrl_tags": ["CashAndCashEquivalentsAtCarryingValue"],
        "exclude": [
            r"net increase",
            r"decrease",
            r"movement",
            r"beginning of the year",
            r"end of the year",
            r"closing cash",
            r"opening cash",
        ],
    },
    "total_liabilities": {
        "statement": "balance_sheet",
        "patterns": [
            r"^total liabilities$",
        ],
        "xbrl_tags": ["Liabilities"],
        "exclude": [r"and equity", r"liabilities and equity"],
    },
    "current_liabilities": {
        "statement": "balance_sheet",
        "patterns": [
            r"^total current liabilities$",
            r"^current liabilities$",
        ],
        "xbrl_tags": ["LiabilitiesCurrent"],
        "exclude": [r"other current liabilities"],
    },
    "long_term_debt": {
        "statement": "balance_sheet",
        "patterns": [
            r"^non-current borrowings$",
            r"^long.?term borrowings$",
            r"^long.?term debt$",
            r"^borrowings$",  # matched when in non-current section; ambiguous cases resolved in extractor
        ],
        "xbrl_tags": ["LongTermDebt", "LongTermDebtNoncurrent"],
        "exclude": [
            r"proceeds from",
            r"repayment",
            r"foreign currency borrowings",
            r"borrowings include",
            r"current maturities",
        ],
    },
    "short_term_debt": {
        "statement": "balance_sheet",
        "patterns": [
            r"^short.?term borrowings$",
            r"^current borrowings$",
            r"^current maturities of long.?term debt$",
            r"^current maturities of borrowings$",
            r"^working capital loans$",
        ],
        "xbrl_tags": ["ShortTermBorrowings"],
        "exclude": [
            r"proceeds from",
            r"repayment",
        ],
    },
    "total_equity": {
        "statement": "balance_sheet",
        "patterns": [
            r"^total equity$",
            r"^equity attributable to owners",
            r"^total equity attributable to owners",
            r"^total shareholders.? equity$",
            r"^shareholders.? equity$",
        ],
        "xbrl_tags": ["StockholdersEquity"],
        "exclude": [r"liabilities and equity"],
    },
    # ── Cash Flow ───────────────────────────────────────────────────────────
    "operating_cash_flow": {
        "statement": "cash_flow",
        "patterns": [
            r"^net cash from operating activities$",
            r"^net cash generated from operating activities$",
            r"^net cash flow from operating activities$",
            r"^cash flows from operating activities$",
            r"^net cash from/\(used in\) operating activities$",
            r"^net cash generated from(?: /\(used in\))? operating activities$",
            r"^net cash provided by operating activities$",
            r"^cash from operating activities$",
        ],
        "xbrl_tags": ["NetCashProvidedByUsedInOperatingActivities"],
        "exclude": [],
    },
    "investing_cash_flow": {
        "statement": "cash_flow",
        "patterns": [
            r"^net cash from investing activities$",
            r"^net cash from/\(used in\) investing activities$",
            r"^net cash generated from /\(used in\) investing activities$",
            r"^net cash used in investing activities$",
            r"^cash flows from investing activities$",
            r"^net cash inflow\s*/\s*\(used in\) investing activities$",
        ],
        "xbrl_tags": ["NetCashProvidedByUsedInInvestingActivities"],
        "exclude": [],
    },
    "financing_cash_flow": {
        "statement": "cash_flow",
        "patterns": [
            r"^net cash from financing activities$",
            r"^net cash from/\(used in\) financing activities$",
            r"^net cash generated from(?: /\(used in\))? financing activities$",
            r"^net cash used in financing activities$",
            r"^cash flows from financing activities$",
        ],
        "xbrl_tags": ["NetCashProvidedByUsedInFinancingActivities"],
        "exclude": [],
    },
    "capex": {
        "statement": "cash_flow",
        "patterns": [
            r"^purchase of property, plant and equipment$",
            r"^purchase of property, plant & equipment$",
            r"^acquisition of property, plant and equipment$",
            r"^capital expenditure$",
            r"^purchase of fixed assets$",
            r"^additions to property, plant and equipment$",
            r"^payment for purchase of property, plant and equipment$",
        ],
        "xbrl_tags": ["PaymentsToAcquirePropertyPlantAndEquipment"],
        "exclude": [r"proceeds from", r"sale of", r"disposal"],
    },
}


# ---------------------------------------------------------------------------
# Section keywords (used by text_chunker.py)
# ---------------------------------------------------------------------------

SECTION_KEYWORDS: dict[str, list[str]] = {
    "mda":          ["management discussion", "md&a", "management's discussion"],
    "risk_factors": ["risk factor", "risks and concerns", "key risks"],
    "notes":        ["notes to", "notes forming part", "significant accounting"],
    "liquidity":    ["liquidity", "capital resources"],
    "other":        [],
}


# ---------------------------------------------------------------------------
# Year regexes
# ---------------------------------------------------------------------------

# Matches date-style year references: "March 2024", "31 March 2024", "FY2024", "2023-24"
DATE_YEAR_RE = re.compile(
    r"""
    (?:
        (?:\d{1,2}\s+)?                              # optional day "31 "
        (?:january|february|march|april|may|june|    # full month names
           july|august|september|october|november|december|
           jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)  # abbreviated
        \s+(20\d{2})                                 # year group 1
    |
        fy\s*(20\d{2})                               # FY2024 / FY 2024  group 2
    |
        (20\d{2})-\d{2}\b                            # 2023-24             group 3
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Matches bare 4-digit years: 2020–2029
YEAR_RE = re.compile(r"\b(20\d{2})\b")


# ---------------------------------------------------------------------------
# match_metric
# ---------------------------------------------------------------------------

# A trailing single-letter parenthetical note/column reference, e.g. the
# "(A)" / "(B)" / "(C)" suffixes Indian cash-flow statements append to
# sub-total lines ("Net Cash generated from Financing Activities (C)").
# Deliberately narrow: exactly one letter between the parens, anchored at
# the end of the string, so multi-word parentheticals like "(loss)" or
# "(used in)" are never touched.
_TRAILING_NOTE_REF_RE = re.compile(r"\s*\([a-z]\)$")

# A leading numbered line-item prefix, e.g. the "(1)" / "(3)" / "(10)" line
# numbers Indian income statements prefix each row with ("(1) Revenue from
# operations"). Anchored at the start of the string so it only strips a
# genuine leading prefix, never a number appearing elsewhere in the label.
_LEADING_NUMBERED_PREFIX_RE = re.compile(r"^\(\d+\)\s*")

# A trailing footnote-reference marker such as "*", "#", "†", "‡" (or a run
# of them), e.g. "Net Cash Flow from Operating Activities *" -- the label
# text itself is genuine and verbatim-present on the source page (L3 reads
# raw_label unmodified and is unaffected by this normalisation); only this
# function's own alias lookup fails to see past the trailing symbol.
# Deliberately narrow: only whitespace + footnote-style punctuation,
# anchored at the end of the string -- never touches a mid-label character
# or strips general punctuation.
_TRAILING_FOOTNOTE_MARKER_RE = re.compile(r"\s*[*#†‡]+$")


def match_metric(label: str, statement_type: str | None = None) -> str | None:
    """
    Returns the canonical metric name for `label`, or None if no match.

    Steps:
      1. Normalise label (strip, collapse whitespace, lowercase).
      2. Strip a leading numbered line-item prefix, if present.
      3. Strip a trailing single-letter note reference, if present.
      4. Strip a trailing footnote-reference marker, if present.
      5. For each candidate metric (filtered by statement_type when given):
         a. If any exclude pattern matches → skip this metric.
         b. If any pattern matches → return the metric name.
    """
    if not label:
        return None

    normalised = re.sub(r"\s+", " ", label.strip().lower())
    normalised = _LEADING_NUMBERED_PREFIX_RE.sub("", normalised)
    normalised = _TRAILING_NOTE_REF_RE.sub("", normalised)
    normalised = _TRAILING_FOOTNOTE_MARKER_RE.sub("", normalised)

    for metric_name, spec in CANONICAL_METRICS.items():
        if statement_type and spec["statement"] != statement_type:
            continue

        # Check exclude patterns first
        excluded = any(
            re.search(pat, normalised, re.IGNORECASE)
            for pat in spec["exclude"]
        )
        if excluded:
            continue

        # Check match patterns
        for pat in spec["patterns"]:
            if re.search(pat, normalised, re.IGNORECASE):
                return metric_name

    return None


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_cases = [
        ("Revenue from operations",     "income_statement",  "revenue"),
        ("Total assets",                "balance_sheet",     "total_assets"),
        ("Net cash from operating activities", "cash_flow",  "operating_cash_flow"),
        ("Finance costs",               "income_statement",  "interest_expense"),
        ("Purchase of property, plant and equipment", "cash_flow", "capex"),
        ("Total equity",                "balance_sheet",     "total_equity"),
        ("Net profit",                  "income_statement",  "net_profit"),
        ("Cash and cash equivalents",   "balance_sheet",     "cash_and_equivalents"),
        # Should NOT match (excluded)
        ("Net profit margin",           "income_statement",  None),
        ("Total liabilities and equity", "balance_sheet",    None),
    ]

    all_pass = True
    for label, stmt, expected in test_cases:
        result = match_metric(label, stmt)
        status = "PASS" if result == expected else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  [{status}]  '{label}' -> {result!r}  (expected {expected!r})")

    print()
    print("All tests passed." if all_pass else "Some tests FAILED.")
