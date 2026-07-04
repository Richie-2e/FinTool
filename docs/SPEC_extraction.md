# SPEC_extraction.md
## FinTool — Extraction Pipeline Specification
**Version:** 1.0 | **Status:** Draft | **Owners:** Group 8

---

## 0. Current State & What Needs to Change

### What `generic_financial_pdf_extractor_v3` already does well
- `CANONICAL_METRICS` dictionary with patterns and exclude-lists — **keep this**
- Page classification into `balance_sheet / income_statement / cash_flow` — **keep this**
- Candidate → resolved → pivot pipeline with a quality report — **keep this**
- `add_derived_totals_if_possible()` fallback — **keep this**
- Accounting consistency checks (assets = equity + liabilities) — **keep this**

### What is broken / missing and must be fixed
1. **Text extraction layer is too fragile.** `pdfplumber` / `pdftotext` lose layout on multi-column annual reports and scanned pages. **Replace with Docling as the primary parser.**
2. **Year detection leaks.** `extract_header_years_with_order()` sometimes picks up years from note tables. The fix is already described in the v3 docstring but not fully enforced; tighten the header window to top-12 lines only.
3. **No text-chunk output.** The extractor produces structured metric CSVs but never outputs text chunks for the RAG pipeline. **Add a text-chunking stage as a second output path.**
4. **Missing metrics vs EDGAR.** The extractor's `CANONICAL_METRICS` covers Indian company label patterns. The EDGAR notebook's `PHASE1_METRICS` maps XBRL tags for SEC filings. These two must share the same canonical metric names so validation works.
5. **No `doc_id` propagation into the database.** The `doc_id` hash from `make_doc_id()` must be stored in every output row so the API can join on it.

---

## 1. Module Overview

```
extraction/
├── parser.py          # Stage 1 – raw page/table extraction (Docling)
├── classifier.py      # Stage 2 – page-to-statement-type classification (keep v3 logic)
├── metric_extractor.py # Stage 3 – table rows → CANONICAL_METRICS (keep + fix v3 logic)
├── text_chunker.py    # Stage 4 – raw text → chunks → FAISS (NEW)
├── pipeline.py        # Orchestrator – runs stages 1-4, saves all outputs
└── canonical_metrics.py  # Shared metric schema (merged from v3 + EDGAR notebook)
```

---

## 2. Stage 1 — Document Parsing (Docling replaces pdftotext)

### Why Docling
`pdftotext -layout` loses table structure for multi-column Indian annual reports.
Docling (IBM Research, 2024) exports a structured document object with:
- Tables as `TableItem` objects (rows + cells, no layout heuristics needed)
- Text blocks tagged by role (heading, paragraph, list, footer)
- Page-level bounding boxes so every piece of text carries `page_no`
- Native support for scanned PDFs via built-in OCR (EasyOCR / Tesseract)
- XBRL/HTML input support for SEC filings

### Installation
```bash
pip install docling
# Optional for GPU-accelerated OCR:
pip install docling[ocr]
```

### Interface Contract

```python
# parser.py

from docling.document_converter import DocumentConverter

def parse_pdf(pdf_path: str | Path) -> DoclingParseResult:
    """
    Input:  PDF file path (annual report, balance sheet, P&L, cash flow)
    Output: DoclingParseResult
    """

@dataclass
class DoclingParseResult:
    doc_id: str                    # md5 hash of filename+size (carry forward from v3)
    pdf_name: str
    page_count: int
    pages_raw_text: list[str]      # one string per page (for classifier + chunker)
    tables: list[DoclingTable]     # structured table objects

@dataclass
class DoclingTable:
    page_no: int                   # 1-indexed
    df: pd.DataFrame               # rows × cols as extracted by Docling
    caption: str                   # heading text above the table, if any
    section_hint: str              # "standalone" | "consolidated" | "unknown"
```

### Fallback Chain
```
Docling (primary)
  └─ if ImportError or parse fails → pdfplumber tables + pdftotext text (v3 behaviour)
  └─ if still fails → pypdf text-only (no tables)
```
Log which parser was used in `_log.txt` so debugging is easy.

---

## 3. Stage 2 — Page Classification

**No change from v3.** Keep `classify_statement_pages()` exactly as-is.

The only addition: after classification, also tag each `DoclingTable` with its
statement type by matching the table's `page_no` to the classified pages list.

---

## 4. Stage 3 — Metric Extraction

### 4a. From Docling Tables (primary path — replaces line-by-line parsing)

```python
# metric_extractor.py

def extract_from_docling_tables(
    tables: list[DoclingTable],
    page_classifications: list[PageClassification],
    document_years: list[int],
) -> list[CandidateMetric]:
    """
    Input:  list of DoclingTable + page classifications
    Output: list of CandidateMetric

    For each table whose page is classified as a financial statement:
    1. Detect the year columns from the table header row
    2. For each data row, run match_metric(label, statement_type, section_type)
    3. If matched, create one CandidateMetric per year column
    """
```

**Year column detection from table headers (improved):**
- Look at the first 1–2 rows of the DataFrame
- Apply `DATE_YEAR_RE` and `YEAR_RE` only to those header rows
- Never apply year detection to data rows (this is the leak fix)

### 4b. Fallback — Line-by-Line Parsing (kept from v3)
If Docling is unavailable or a table has < 2 columns, fall back to
`extract_candidates_from_page()` from v3 on the raw `pages_raw_text`.

### 4c. Output Schema — `CandidateMetric`

```python
@dataclass
class CandidateMetric:
    doc_id:         str
    metric_name:    str          # canonical name from CANONICAL_METRICS
    raw_label:      str          # original text label from the document
    value:          float        # in reporting unit (crore / USD millions)
    unit:           str          # "crore" | "lakh" | "million" | "USD" | "as_reported"
    year:           int | None
    page_no:        int | None
    statement_type: str          # "balance_sheet" | "income_statement" | "cash_flow"
    section_type:   str          # "standalone" | "consolidated" | "unknown"
    confidence:     str          # "high" | "medium" | "low"
    source:         str          # "docling_table" | "line_parse_fallback"
```

### 4d. Resolution & Output DataFrames

Keep the v3 resolution logic exactly:
- `choose_best_candidate_per_metric_year()` — keep
- `add_derived_totals_if_possible()` — keep
- `run_accounting_checks()` — keep

**Outputs saved per document:**
```
{stem}_candidate_metrics.csv   # all candidates before resolution
{stem}_resolved_metrics.csv    # one row per metric+year after resolution
{stem}_pivot_metrics.csv       # year × metric pivot (used by computation module)
{stem}_meta.json
{stem}_quality_report.json
{stem}_log.txt
```

**Column contract for `resolved_metrics.csv`** (these column names are fixed — other modules depend on them):
```
doc_id | metric_name | value | unit | year | page_no | raw_label |
statement_type | section_type | confidence | source
```

---

## 5. Stage 4 — Text Chunking (NEW — feeds the RAG pipeline)

This stage runs on `pages_raw_text` in parallel with metric extraction.
It does NOT use the financial statement pages — it uses the full document text
excluding pages that are pure number tables.

### Chunking Strategy: Recursive Character Text Splitter

**Decision: Use recursive splitting, not fixed-size splitting.**

Reasons specific to financial documents:
- MD&A sections have long paragraphs → recursive splitter respects paragraph boundaries
- Risk factor sections use bullet lists → recursive splitter handles list structure
- Fixed-size chunking would cut sentences mid-way, hurting retrieval precision
- Financial documents have repetitive boilerplate at section boundaries — overlapping chunks help the retrieval model bridge these

**Parameters:**
```python
CHUNK_SIZE    = 800    # tokens (≈ 600 words) — large enough for full context
CHUNK_OVERLAP = 150    # tokens — enough to catch cross-sentence references
SEPARATORS    = ["\n\n", "\n", ". ", " "]  # try in order
```

Why 800 and not 500? Financial sentences often carry multiple numbers and qualifiers.
A 500-token chunk frequently cuts a disclosure mid-sentence.

### Chunk Schema

```python
@dataclass
class TextChunk:
    chunk_id:       str       # f"{doc_id}_{page_no}_{chunk_index}"
    doc_id:         str
    text:           str
    page_no:        int
    section_title:  str       # nearest heading above this chunk, or ""
    section_type:   str       # "mda" | "risk_factors" | "notes" | "other"
    char_start:     int       # character offset in page text
    char_end:       int
```

### Section Classification for Chunks

Use keyword matching on the nearest heading above each chunk:

```python
SECTION_KEYWORDS = {
    "mda":          ["management discussion", "md&a", "management's discussion"],
    "risk_factors": ["risk factor", "risks and concerns", "key risks"],
    "notes":        ["notes to", "notes forming part", "significant accounting"],
    "liquidity":    ["liquidity", "capital resources"],
    "other":        [],   # default
}
```

### Embedding Model

**Recommendation: `sentence-transformers/all-MiniLM-L6-v2`**

- Free, runs locally, no API key required
- 384-dimensional embeddings — small enough that FAISS searches run in < 100ms
- Strong on financial English (fine-tuned on diverse English text including business)
- 256-token context window — fits most financial sentences

**Alternative if you need longer context:** `BAAI/bge-base-en-v1.5` (768-dim, 512 tokens, better for long disclosures)

```python
# text_chunker.py
from sentence_transformers import SentenceTransformer
import faiss, numpy as np

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

def build_faiss_index(chunks: list[TextChunk]) -> tuple[faiss.IndexFlatIP, list[TextChunk]]:
    """
    Input:  list of TextChunk
    Output: (faiss_index, ordered_chunks_list)
    Saves:  {doc_id}_faiss.index  and  {doc_id}_chunks.jsonl
    """
```

### Output Files per Document
```
{stem}_chunks.jsonl        # one JSON object per line, TextChunk schema
{stem}_faiss.index         # FAISS flat index (cosine similarity via IP + normalized vecs)
```

---

## 6. Pipeline Orchestrator

```python
# pipeline.py

class ExtractionPipeline:
    def run(self, pdf_path: Path, output_dir: Path) -> PipelineResult:
        # Stage 1: parse
        parse_result = parse_pdf(pdf_path)

        # Stage 2: classify
        page_classes = classify_statement_pages(parse_result.pages_raw_text)

        # Stage 3a: metric extraction (Docling tables primary)
        candidates = extract_from_docling_tables(parse_result.tables, page_classes, ...)
        # Stage 3b: fallback if candidates < 3
        if len(candidates) < 3:
            candidates += extract_candidates_from_pages(parse_result.pages_raw_text, ...)

        resolved_df, quality = build_resolved_metrics_df(candidates)
        pivot_df = add_basic_ratios(pivot_metrics(resolved_df))

        # Stage 4: chunking (independent of stage 3)
        chunks = chunk_document(parse_result.pages_raw_text, page_classes)
        faiss_index = build_faiss_index(chunks)

        # Save all outputs
        save_outputs(parse_result.doc_id, resolved_df, pivot_df, quality, chunks, output_dir)

        return PipelineResult(doc_id=..., pivot_df=..., quality=..., chunks=...)
```

---

## 7. Canonical Metrics Registry (shared with EDGAR notebook)

The extraction module and EDGAR module must share **the same canonical metric names**.

Current mismatch:
- v3 extractor uses: `revenue, net_profit, current_assets, current_liabilities, total_assets, total_equity, total_liabilities, operating_cash_flow, investing_cash_flow, financing_cash_flow, borrowings, trade_receivables, trade_payables, ...`
- EDGAR notebook uses: `revenue, operating_profit, net_profit, gross_profit, total_expenses, interest_expense, total_assets, current_assets, cash, total_liabilities, current_liabilities, long_term_debt, short_term_debt, total_equity, operating_cash_flow, investing_cash_flow, financing_cash_flow, capex`

**Merged canonical set (Phase 1 — use these names everywhere):**

| Canonical Name | Statement | EDGAR XBRL Tag (primary) | v3 Pattern (primary) |
|---|---|---|---|
| `revenue` | income | `Revenues` | `^revenue from operations$` |
| `gross_profit` | income | `GrossProfit` | — |
| `operating_profit` | income | `OperatingIncomeLoss` | — |
| `net_profit` | income | `NetIncomeLoss` | `^profit for the year$` |
| `interest_expense` | income | `InterestExpense` | — |
| `total_assets` | balance | `Assets` | `^total assets$` |
| `current_assets` | balance | `AssetsCurrent` | `^total current assets$` |
| `cash_and_equivalents` | balance | `CashAndCashEquivalentsAtCarryingValue` | `^cash and cash equivalents$` |
| `total_liabilities` | balance | `Liabilities` | `^total liabilities$` |
| `current_liabilities` | balance | `LiabilitiesCurrent` | `^total current liabilities$` |
| `long_term_debt` | balance | `LongTermDebt` | `^borrowings$` (non-current) |
| `short_term_debt` | balance | `ShortTermBorrowings` | `^borrowings$` (current) |
| `total_equity` | balance | `StockholdersEquity` | `^total equity$` |
| `operating_cash_flow` | cashflow | `NetCashProvidedByUsedInOperatingActivities` | `^net cash from operating activities$` |
| `investing_cash_flow` | cashflow | `NetCashProvidedByUsedInInvestingActivities` | `^net cash from investing activities$` |
| `financing_cash_flow` | cashflow | `NetCashProvidedByUsedInFinancingActivities` | `^net cash from financing activities$` |
| `capex` | cashflow | `PaymentsToAcquirePropertyPlantAndEquipment` | — |

---

## 8. Acceptance Tests

Before marking the extraction module complete, all of the following must pass:

| Test | Pass Criterion |
|---|---|
| Tata Steel AR 2024 | `revenue`, `net_profit`, `total_assets`, `current_ratio` extracted for both 2023 and 2024 |
| Reliance AR 2024 | Consolidated section preferred over standalone when both present |
| JioFin AR 2025 | At least 6/8 core metrics extracted for each year |
| Year leak test | No year from note tables appears as a column year in the pivot |
| Accounting check | `total_assets ≈ total_equity + total_liabilities` within 3% for all companies |
| Chunk count | Each 100-page AR produces ≥ 80 text chunks |
| Chunk metadata | Every chunk has `doc_id`, `page_no`, `section_type` populated |
| FAISS roundtrip | Query "liquidity risk" retrieves a chunk from MD&A or risk factors section |

---

## 9. What NOT to Do

- **Do not chunk financial statement tables.** Table rows are metric candidates, not text for RAG. The chunker only processes narrative text.
- **Do not use LangChain's `UnstructuredPDFLoader`.** It mangles table structure.
- **Do not run embedding on resolved_metrics.** Embeddings are for text chunks only. Metrics go into the structured store.
- **Do not hard-code company names.** The `CANONICAL_METRICS` patterns must work on any Indian company's annual report.
