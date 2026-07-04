"""
llm_poc_experiment.py
---------------------
Isolated proof-of-concept: LLM-based financial metric extraction.

Pipeline:
  OFSS Annual Report PDF
    → PyMuPDF (text extraction)
    → page classifier (keyword-based, no ML)
    → Qwen2.5:3b via Ollama REST API
    → structured JSON with confidence + evidence
    → coverage / latency report

Zero imports from existing extraction/ module.
Does NOT modify any existing files.

Run:
  python extraction/llm_poc_experiment.py
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import fitz          # PyMuPDF
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PDF_PATH = Path("uploads/AR_26628_OFSS_2024_2025_A_27062025192147.pdf")
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5:3b"
OUTPUT_PATH = Path("extraction_outputs/llm_poc_results.json")

# All 17 canonical metric names from canonical_metrics.py
CANONICAL_NAMES = [
    "revenue", "gross_profit", "operating_profit", "net_profit",
    "interest_expense", "total_assets", "current_assets",
    "cash_and_equivalents", "total_liabilities", "current_liabilities",
    "long_term_debt", "short_term_debt", "total_equity",
    "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
    "capex",
]

# Statement types and their detection keywords (lowercased)
STATEMENT_KEYWORDS: dict[str, list[str]] = {
    "balance_sheet":      ["balance sheet", "assets and liabilities", "financial position"],
    "income_statement":   ["profit and loss", "statement of profit", "income statement",
                           "revenue from operations", "statement of operations"],
    "cash_flow":          ["cash flow statement", "cash flows from operating",
                           "statement of cash flows"],
}

# Maximum characters sent per LLM call (keeps within 3b context limit)
MAX_CHARS_PER_CALL = 6000

# ---------------------------------------------------------------------------
# Step 1 — Extract text from PDF
# ---------------------------------------------------------------------------

def extract_pages(pdf_path: Path) -> dict[int, str]:
    """Returns {page_no (1-indexed): raw_text}."""
    doc = fitz.open(str(pdf_path))
    pages: dict[int, str] = {}
    for i, page in enumerate(doc, start=1):
        pages[i] = page.get_text("text")
    doc.close()
    print(f"[PDF] Extracted {len(pages)} pages from {pdf_path.name}")
    return pages


# ---------------------------------------------------------------------------
# Step 2 — Find financial statement pages
# ---------------------------------------------------------------------------

def find_statement_pages(pages: dict[int, str]) -> dict[str, list[int]]:
    """
    Returns {statement_type: [page_numbers]} for pages where a financial
    statement TITLE appears in the first 8 lines AND the page has dense
    numeric content (>= 5 numbers of the form XX,XXX).

    Checks the page title area only — prevents false positives from pages
    that merely mention keywords in body text (auditor notes, MDA, etc.).
    """
    num_re = re.compile(r"\d{1,3}(?:,\d{3})+")  # e.g. 68,468 or 1,89,483

    # Tighter title-level keywords — must appear near top of page
    TITLE_KEYWORDS: dict[str, list[str]] = {
        "balance_sheet":    ["balance sheet", "statement of financial position"],
        "income_statement": ["statement of profit and loss", "income statement",
                             "profit and loss account", "statement of operations"],
        "cash_flow":        ["cash flow statement", "statement of cash flows",
                             "cash flows from operating"],
    }

    found: dict[str, list[int]] = {k: [] for k in TITLE_KEYWORDS}

    for page_no, text in pages.items():
        # Only look at first 8 lines for the statement title
        first_lines = "\n".join(text.splitlines()[:8]).lower()
        numeric_count = len(num_re.findall(text))

        for stmt_type, keywords in TITLE_KEYWORDS.items():
            if any(kw in first_lines for kw in keywords) and numeric_count >= 5:
                found[stmt_type].append(page_no)

    for stmt, pgs in found.items():
        print(f"[Classify] {stmt}: pages {pgs}")
    return found


def get_page_block(pages: dict[int, str], page_nos: list[int], max_chars: int) -> str:
    """
    Concatenates text from the given pages, up to max_chars total.
    Takes the first occurrence page + up to 2 following pages to capture
    multi-page tables.
    """
    if not page_nos:
        return ""
    # Use first hit + next 2 pages
    start = page_nos[0]
    candidates = [start, start + 1, start + 2]
    block = ""
    for pno in candidates:
        if pno in pages:
            block += f"\n--- Page {pno} ---\n" + pages[pno]
        if len(block) >= max_chars:
            break
    return block[:max_chars]


# ---------------------------------------------------------------------------
# Step 3 — Build extraction prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a financial data extraction engine for Indian annual reports.

Rules:
1. Extract ONLY values that are explicitly present in the provided text.
2. Do NOT calculate, infer, or guess any value.
3. Numbers may use Indian formatting: 1,89,483 means 189483. Convert to plain float.
4. Negative values are shown in parentheses: (4,521) = -4521.
5. Dashes (—, -) in value cells mean zero or not applicable; use null.
6. Map each extracted item to the closest canonical_name from the list provided.
7. If a metric appears for multiple years, extract all of them as separate entries.
8. Output ONLY a valid JSON object. No explanation text outside the JSON.
9. IMPORTANT — Indian Balance Sheet format: The word "TOTAL" appears TWICE.
   First TOTAL = Total Assets (sum of all assets). Second TOTAL = Total Equity + Liabilities.
   These two totals are always equal (balance sheet identity). Do NOT treat the second
   TOTAL as "total_liabilities". Extract "total_assets" from the first TOTAL only.
   For total_liabilities: add non-current liabilities subtotal + current liabilities subtotal.
10. Keep evidence snippets short (under 100 characters).

Output schema:
{
  "metrics": [
    {
      "canonical_name": "<name from canonical list>",
      "raw_label": "<exact label as it appears in text>",
      "value": <float or null>,
      "unit": "<INR Crore / INR Lakh / USD Million / etc>",
      "year": <integer YYYY or null>,
      "confidence": "<high|medium|low>",
      "section_type": "<consolidated|standalone|unknown>",
      "evidence": "<verbatim line(s) from text where value was found>"
    }
  ],
  "notes": "<any important observations about the data>"
}

Confidence guide:
- high: label clearly matches a canonical metric, value is unambiguous, year is clear
- medium: label is close but not exact, or year needed inference
- low: value or label was ambiguous
"""


def build_user_prompt(statement_type: str, text_block: str) -> str:
    canonical_list = ", ".join(CANONICAL_NAMES)
    return f"""Statement type: {statement_type}

Canonical metric names you may use (use ONLY these):
{canonical_list}

--- DOCUMENT TEXT ---
{text_block}
--- END OF TEXT ---

Extract all financial metrics from the text above and return valid JSON."""


# ---------------------------------------------------------------------------
# JSON truncation recovery
# ---------------------------------------------------------------------------

def _recover_partial_json(text: str) -> dict | None:
    """
    Attempt to salvage complete metric objects from a truncated JSON string.
    Finds all complete {...} metric blocks and wraps them in a valid response.
    """
    # Find all complete JSON objects inside the metrics array
    objects = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                candidate = text[start:i+1]
                try:
                    obj = json.loads(candidate)
                    # Only keep objects that look like metric entries
                    if "canonical_name" in obj:
                        objects.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1

    if objects:
        return {"metrics": objects, "notes": "recovered from truncated output"}
    return None


# ---------------------------------------------------------------------------
# Step 4 — Call Ollama
# ---------------------------------------------------------------------------

def call_ollama(statement_type: str, text_block: str) -> tuple[dict | None, float, bool]:
    """
    Returns (parsed_result, latency_seconds, json_valid).
    parsed_result is None if the call failed or JSON was invalid.
    """
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": build_user_prompt(statement_type, text_block)},
        ],
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": 2500,
        },
    }

    t0 = time.perf_counter()
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=180)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        print(f"  [ERROR] Cannot connect to Ollama at {OLLAMA_URL}. Is it running?")
        return None, 0.0, False
    except requests.exceptions.Timeout:
        print(f"  [ERROR] Ollama request timed out after 180s")
        return None, 180.0, False
    except requests.exceptions.HTTPError as e:
        print(f"  [ERROR] Ollama HTTP error: {e}")
        return None, 0.0, False

    latency = time.perf_counter() - t0
    raw_content = resp.json()["message"]["content"]

    # Attempt JSON parse — model may still wrap in markdown fences
    clean = raw_content.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\n?", "", clean)
        clean = re.sub(r"\n?```$", "", clean)

    try:
        parsed = json.loads(clean)
        return parsed, latency, True
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON parse failed: {e} — attempting partial recovery")
        # Try to salvage complete metric objects from truncated JSON
        recovered = _recover_partial_json(clean)
        if recovered:
            print(f"  [WARN] Recovered {len(recovered.get('metrics', []))} metrics from truncated output")
            return recovered, latency, False
        print(f"  [WARN] Raw content (first 300 chars): {raw_content[:300]}")
        return None, latency, False


# ---------------------------------------------------------------------------
# Step 5 — Normalise & validate extracted metrics
# ---------------------------------------------------------------------------

def normalise_metrics(raw_result: dict, statement_type: str) -> list[dict]:
    """
    Validates and normalises metric entries from the LLM response.
    Filters out entries with unknown canonical names.
    """
    metrics = raw_result.get("metrics", [])
    normalised = []
    for m in metrics:
        cname = m.get("canonical_name", "").strip().lower().replace(" ", "_")
        if cname not in CANONICAL_NAMES:
            continue  # LLM invented a name — discard

        value = m.get("value")
        if isinstance(value, str):
            # Try to parse if LLM returned a string
            value = _parse_number(value)

        normalised.append({
            "canonical_name": cname,
            "raw_label":      m.get("raw_label", ""),
            "value":          value,
            "unit":           m.get("unit", ""),
            "year":           m.get("year"),
            "confidence":     m.get("confidence", "low"),
            "section_type":   m.get("section_type", "unknown"),
            "evidence":       m.get("evidence", "")[:300],
            "statement_type": statement_type,
        })
    return normalised


def _parse_number(s: str) -> float | None:
    """Parse Indian-formatted number strings returned as text by the LLM."""
    if not s:
        return None
    s = s.strip()
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = s.replace(",", "").replace(" ", "")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Step 6 — Coverage & quality report
# ---------------------------------------------------------------------------

def compute_report(all_metrics: list[dict], call_stats: list[dict]) -> dict:
    """Summarises extraction results."""
    found_names = {m["canonical_name"] for m in all_metrics}
    missing = [n for n in CANONICAL_NAMES if n not in found_names]
    confidence_dist = {"high": 0, "medium": 0, "low": 0}
    for m in all_metrics:
        confidence_dist[m.get("confidence", "low")] = (
            confidence_dist.get(m.get("confidence", "low"), 0) + 1
        )

    total_latency = sum(s["latency_s"] for s in call_stats)

    return {
        "model": MODEL,
        "pdf": str(PDF_PATH),
        "total_metrics_extracted": len(all_metrics),
        "unique_canonical_names_found": len(found_names),
        "canonical_coverage": f"{len(found_names)}/17",
        "coverage_pct": round(len(found_names) / 17 * 100, 1),
        "missing_metrics": missing,
        "confidence_distribution": confidence_dist,
        "call_stats": call_stats,
        "total_latency_s": round(total_latency, 2),
        "avg_latency_per_call_s": round(total_latency / max(len(call_stats), 1), 2),
    }


# ---------------------------------------------------------------------------
# Main experiment
# ---------------------------------------------------------------------------

def run_experiment() -> dict:
    print("=" * 60)
    print(f"LLM PoC Experiment — {MODEL}")
    print("=" * 60)

    if not PDF_PATH.exists():
        raise FileNotFoundError(f"PDF not found: {PDF_PATH}")

    # Step 1: extract text
    pages = extract_pages(PDF_PATH)

    # Step 2: find statement pages
    statement_pages = find_statement_pages(pages)

    all_metrics: list[dict] = []
    call_stats: list[dict] = []

    # Step 3-4: call LLM for each statement type
    for stmt_type, page_nos in statement_pages.items():
        if not page_nos:
            print(f"[Skip] No pages found for {stmt_type}")
            continue

        text_block = get_page_block(pages, page_nos, MAX_CHARS_PER_CALL)
        char_count = len(text_block)
        print(f"\n[LLM] Calling model for {stmt_type} ({char_count} chars) ...")

        result, latency, json_ok = call_ollama(stmt_type, text_block)

        stat = {
            "statement_type": stmt_type,
            "pages_used": page_nos[:3],
            "chars_sent": char_count,
            "latency_s": round(latency, 2),
            "json_valid": json_ok,
            "metrics_returned": 0,
        }

        if result and json_ok:
            metrics = normalise_metrics(result, stmt_type)
            stat["metrics_returned"] = len(metrics)
            all_metrics.extend(metrics)
            print(f"  → {len(metrics)} metrics extracted in {latency:.1f}s")
        else:
            print(f"  → Failed (latency: {latency:.1f}s)")

        call_stats.append(stat)

    # Step 5: build report
    report = compute_report(all_metrics, call_stats)

    output = {
        "report": report,
        "metrics": all_metrics,
    }

    # Save results
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    # Print summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    print(f"Coverage     : {report['canonical_coverage']} ({report['coverage_pct']}%)")
    print(f"Total metrics: {report['total_metrics_extracted']}")
    print(f"Confidence   : {report['confidence_distribution']}")
    print(f"Total latency: {report['total_latency_s']}s")
    print(f"Avg/call     : {report['avg_latency_per_call_s']}s")
    print(f"Missing      : {report['missing_metrics']}")
    print(f"\nFull results saved → {OUTPUT_PATH}")

    # Print extracted metrics table
    if all_metrics:
        print("\nEXTRACTED METRICS:")
        print(f"  {'Metric':<25} {'Year':<6} {'Value':<15} {'Conf':<8} {'Section'}")
        print("  " + "-" * 70)
        for m in sorted(all_metrics, key=lambda x: (x["canonical_name"], x.get("year") or 0)):
            val_str = f"{m['value']:,.0f}" if m["value"] is not None else "null"
            print(f"  {m['canonical_name']:<25} {str(m.get('year') or ''):<6} "
                  f"{val_str:<15} {m['confidence']:<8} {m['section_type']}")

    return output


if __name__ == "__main__":
    run_experiment()
