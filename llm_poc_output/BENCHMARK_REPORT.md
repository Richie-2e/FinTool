# LLM PoC Extraction Benchmark Report
## Oracle Financial Services Software Ltd — FY 2024-25
**Date:** 2026-06-21  
**Model:** qwen2.5:3b via Ollama  
**Script:** extraction/llm_poc_experiment.py  
**PDF:** AR_26628_OFSS_2024_2025_A_27062025192147.pdf (1.4 MB, 193 pages)

---

## 1. Execution Summary

| Item | Result |
|---|---|
| Execution completed | YES — exit code 0 |
| Total wall-clock time | 155.12 seconds |
| Ollama calls made | 3 (balance_sheet, income_statement, cash_flow) |
| Average latency per call | 51.7 seconds |
| JSON validity (all calls) | PASS — all 3 returned valid JSON |
| Partial recovery triggered | NO — no truncation detected by the parser |
| Output file saved | extraction_outputs/llm_poc_results.json |

---

## 2. Stage-by-Stage Log

### Stage 1 — PDF Loading (PyMuPDF)
- **Pages extracted:** 193
- **Method:** `fitz.open()` + `page.get_text("text")`
- **Status:** PASS — complete text extracted for all pages

### Stage 2 — Page Classification
| Statement Type | Pages Detected | Assessment |
|---|---|---|
| balance_sheet | 61, 129 | ✓ Correct — page 61 is the consolidated balance sheet |
| income_statement | 62, 96, 130, 162 | ✓ Correct — page 62 is consolidated P&L; pages 96/130/162 are notes with P&L references |
| cash_flow | 117, 177 | ✓ Correct — page 117 is consolidated cash flow statement |

**Classifier assessment:** The two-filter approach (title-area keyword + ≥5 Indian-formatted numbers) worked correctly. No false negatives on primary statements. Pages 96/130/162 are false positives for income_statement (they are notes pages), but these were not used because `get_page_block()` only takes the first hit.

### Stage 3 — LLM Inference (per call)
| Statement | Pages sent | Context chars | Latency | JSON valid | Metrics returned |
|---|---|---|---|---|---|
| balance_sheet | 61, 62, 63 | 6,000 | 44.1s | YES | 7 |
| income_statement | 62, 63, 64 | 6,000 | 39.3s | YES | 7 |
| cash_flow | 117, 118, 119 | 6,000 | 71.7s | YES | 9 |

**Note:** `get_page_block()` sends page N + N+1 + N+2 for every first-hit page. For balance_sheet (first hit = page 61), pages 62 and 63 were included. Page 63 is the Statement of Changes in Equity — irrelevant content that caused the share-count hallucination.

### Stage 4 — Normalisation
- **Total raw metrics returned:** 23
- **Dropped (invalid canonical name):** 0
- **Null values in output:** 4 (gross_profit, operating_profit, two null placeholders)
- **Duplicates (same canonical name + year):** 6 conflict groups

### Stage 5 — Coverage
- **Unique canonical metrics found:** 9/17 (52.9%)
- **Total extraction events (including duplicates):** 23

---

## 3. Metric Coverage Report

| Canonical Metric | Extracted | Both Years | Status |
|---|---|---|---|
| revenue | 2025 only (wrong value) | NO | FAILED |
| gross_profit | null | NO | FAILED |
| operating_profit | null | NO | FAILED |
| net_profit | 2025 only | NO | PARTIAL |
| interest_expense | 2024 + 2025 (sign wrong) | YES | PARTIAL |
| total_assets | 2024 + 2025 | YES | CORRECT |
| current_assets | — | NO | MISSING |
| cash_and_equivalents | 2025 only (wrong value) | NO | FAILED |
| total_liabilities | 2024 + 2025 (both wrong) | YES | FAILED |
| current_liabilities | — | NO | MISSING |
| long_term_debt | — | NO | MISSING |
| short_term_debt | — | NO | MISSING |
| total_equity | 2024 + 2025 (2024 wrong; hallucination) | YES | PARTIAL |
| operating_cash_flow | — | NO | MISSING |
| investing_cash_flow | — | NO | MISSING |
| financing_cash_flow | — | NO | MISSING |
| capex | — | NO | MISSING |

**Coverage: 9/17 canonical names touched (52.9%) — but only 2/17 are fully correct for both years.**

---

## 4. Accuracy Against Ground Truth

Ground truth values verified directly from OFSS FY2024-25 financial statements.

| Metric | Year | Extracted | Ground Truth | Status | Error |
|---|---|---|---|---|---|
| revenue | 2025 | 17 | 68,468 | WRONG | 100% |
| revenue | 2024 | — | 63,730 | MISSING | — |
| net_profit | 2025 | 23,796 | 23,796 | **CORRECT** | 0% |
| net_profit | 2024 | — | 22,194 | MISSING | — |
| total_assets | 2025 | 101,350 | 101,350 | **CORRECT** | 0% |
| total_assets | 2024 | 99,357 | 99,357 | **CORRECT** | 0% |
| total_equity | 2025 | 83,624 | 83,624 | **CORRECT** | 0% |
| total_equity | 2024 | 83,588 | 78,588 | WRONG | 6.4% |
| total_liabilities | 2025 | 101,350 | 17,726 | WRONG | 472% |
| total_liabilities | 2024 | 7,971 | 20,769 | WRONG | 62% |
| cash_and_equivalents | 2025 | 33,547 | 12,142 | WRONG | 176% |
| cash_and_equivalents | 2024 | — | 34,833 | MISSING | — |
| current_assets | 2025/2024 | — | 79,458 / 76,514 | MISSING | — |
| current_liabilities | 2025/2024 | — | 11,509 / 12,798 | MISSING | — |
| interest_expense | 2025 | -5 | 5 | WRONG (sign) | 200% |
| interest_expense | 2024 | -281 | 281 | WRONG (sign) | 200% |
| operating_cash_flow | 2025/2024 | — | 21,989 / 17,907 | MISSING | — |
| investing_cash_flow | 2025/2024 | — | -24,526 / 15,980 | MISSING | — |
| capex | 2025/2024 | — | -352 / -301 | MISSING | — |

**Summary:**
- Correct (< 1% error): **4 of 24 data points (16.7% overall accuracy)**
- Wrong (≥ 1% error): **7 of 24**
- Missing: **13 of 24**
- Precision of extracted values: **36.4%** (4 correct out of 11 non-missing)

---

## 5. Root Cause Analysis

### RCA-1: Note Number Extracted as Revenue Value (CRITICAL)
**Affected metric:** `revenue 2025` → extracted 17, actual 68,468

OFSS uses a "Notes" column in its financial statements. The raw PyMuPDF text for the income statement page reads:
```
Revenue from operations
17          ← note reference number
68,468      ← 2025 value
63,730      ← 2024 value
```
The model sees three numbers after the label and picks the first one (17) as the revenue value. The prompt has no rule instructing the model to skip note reference numbers (small integers 1–30 appearing between a label and large values).

**Fix required:** Add Rule 11 to the prompt: "Ignore any integer between 1 and 50 that appears immediately after a label — these are note reference numbers, not values."

---

### RCA-2: Balance Sheet Total Confusion (CRITICAL)
**Affected metric:** `total_liabilities 2025` → extracted 101,350, actual 17,726

The Indian balance sheet has two TOTAL lines — one for assets (101,350) and one as the balance-check (equity + liabilities = 101,350). Rule 9 in the prompt was designed to prevent this. It partially succeeded: `total_assets` was correctly mapped to the first TOTAL. But the model still mapped the second TOTAL to `total_liabilities`.

The deeper problem: there is no explicit "Total liabilities" row in the OFSS balance sheet. The correct value (17,726) must be derived by adding the non-current liabilities subtotal (6,217) and current liabilities subtotal (11,509). The 3b model cannot perform this two-step reasoning reliably.

**Fix required:** Extend Rule 9 to explicitly instruct: "For total_liabilities: add the non-current liabilities subtotal and the current liabilities subtotal. Do NOT use the second TOTAL row."

---

### RCA-3: Wrong Page in Context Window (HIGH)
**Affected metric:** `total_equity 2025` hallucination → 86,863,101 (actual: 83,624)

`get_page_block()` hardcodes "first detected page + next 2 pages." For balance_sheet (detected at page 61), the context included pages 61, 62, and 63. Page 63 is the Statement of Changes in Equity, which contains share counts (86,863,101 shares). The model extracted a share count as a financial metric value.

**Fix required:** Pass only classified pages to the LLM context, not arbitrary sequential pages. The page classifier already identifies which pages belong to which statement type — use that information to filter context.

---

### RCA-4: Year-Column Misalignment
**Affected metric:** `total_equity 2024` → extracted 83,588, actual 78,588

The balance sheet has two columns (2025 | 2024). PyMuPDF text serialises both columns into a flat stream. When the model reads "Total equity: 83,624 | 78,588", it correctly extracts 83,624 for 2025. But for 2024 it extracted 83,588 — likely by combining the leading digits of 83,624 (year 2025) with the last three digits of 78,588 (year 2024). This is a column boundary confusion error specific to the flat text representation.

**Fix required:** PyMuPDF table extraction (`page.find_tables()`) would provide column-aligned data. Or use the LLM's structured reasoning to match numbers to column headers explicitly in the prompt.

---

### RCA-5: Cash Flow Label Not Mapped
**Affected metrics:** `operating_cash_flow`, `investing_cash_flow`, `financing_cash_flow` — all MISSING

Page 117 explicitly contains "Net cash provided by operating activities: 21,989" (line 91 in the raw text). This is a clean, unambiguous label that matches canonical patterns. The model extracted 9 metrics from the cash_flow call but returned none of these three.

Two possible causes:
1. **Token limit reached:** `num_predict=2500` may have caused truncation before the model finished emitting all metrics. The net cash subtotals appear at lines 91–113 of a 117-line page, near the end of the context.
2. **Model confusion:** The model returned `cash_and_equivalents` (33,547 — pre-tax cash subtotal) instead of recognising it as an intermediate operating activities figure, then ran out of context/tokens before the true net cash lines.

**Fix required:** Increase `num_predict` to 4096. Add a specific rule: "operating_cash_flow is labeled 'Net cash provided/used by operating activities' — NOT 'Cash from operating activities'."

---

### RCA-6: Sign Convention Not Specified
**Affected metrics:** `interest_expense` (both years) → negative when should be positive

The model correctly identified "Finance cost: 5" and "Finance cost: 281" but returned -5 and -281 because costs are outflows. The canonical metric convention stores interest_expense as a positive absolute value. The prompt says nothing about sign conventions for expense-side metrics.

**Fix required:** Add prompt rule: "For interest_expense, always return as positive value (absolute cost)."

---

## 6. Comparison: OFSS (LLM PoC) vs JioFin (V1 Regex)

| Metric | JioFin V1 (regex, pypdf) | OFSS PoC (LLM, PyMuPDF) |
|---|---|---|
| Parser | pypdf (text-only, fell back) | PyMuPDF (primary, direct) |
| Core metrics extracted | 3/8 (37.5%) | 4/8 (50%) |
| Overall accuracy (vs ground truth) | ~20%* | 16.7% |
| Biggest failure | Parser fallback, 0 tables | Note numbers, wrong context window |
| Accounting check | 79% error (mixed sections) | 472% error (TOTAL mismap) |
| Hallucinations | None detected | 1 confirmed (share count as equity) |
| Both years extracted | Partial | Partial |

*JioFin V1 accuracy estimated from status_2.md; no formal ground truth was computed.

The LLM PoC extracted more unique canonical metrics (9 vs ~5) but introduced a new failure category: confident wrong values. The regex extractor tends to miss metrics (low recall); the LLM extractor tends to extract with confidence but incorrectly (low precision).

---

## 7. Verdict

### Is the PoC production-ready?
**NO.**

### Is it promising but incomplete?
**YES — with important caveats.**

### Is it fundamentally flawed?
**NO — the core approach is sound.**

---

### Detailed Assessment

**What works correctly:**
- PyMuPDF text extraction: fast (< 1s for 193 pages), complete, stable
- Classifier (title + numeric density): correctly identified all 3 statement pages
- JSON mode enforcement: all 3 calls returned valid JSON — zero parse failures
- Unambiguous, isolated labels (total_assets, net_profit): extracted correctly at 100% precision
- No connection failures, no timeouts, no crashes

**What works partially:**
- total_equity (2025 year correct, 2024 year confused)
- interest_expense (correct magnitude, wrong sign)
- cash_and_equivalents (12,142 is present as a second entry — resolution would select correctly)

**What fails systematically:**
- Note reference numbers interspersed in text (causes revenue = 17)
- Context window includes wrong pages (causes hallucination of share count as equity)
- total_liabilities requires implicit arithmetic — 3b model cannot reliably add two subtotals
- Cash flow net totals missed (token limit or label confusion)
- No 2024 year coverage for most metrics (single-year extraction)

**The four failures are all fixable without a model change:**
1. Note number rule (1 prompt line)
2. Context window filtering (use classifier output, not sequential pages)
3. total_liabilities instruction (1 prompt paragraph)
4. num_predict increase (1 config change)

**The approach is architecturally correct.** PyMuPDF → Ollama → structured JSON is a viable extraction path. The 3b model demonstrates sufficient instruction-following capability for unambiguous cases. The failures are engineering problems (context management, prompt gaps), not fundamental LLM limitations. Upgrading to qwen2.5:7b-instruct would additionally improve multi-column reasoning (RCA-4) and reduce token truncation pressure.

**Recommendation:** Address the four fixable failures, upgrade to qwen2.5:7b-instruct, then re-benchmark before integrating into the production pipeline.

---

*All raw results: llm_poc_output/llm_poc_results.json*  
*Execution log: llm_poc_output/run_log.txt*
