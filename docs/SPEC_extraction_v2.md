# FinTool Extraction V2 — Architecture, Specification & Migration Plan

**Version:** 2.0  
**Date:** 2026-06-13  
**Status:** Pre-implementation specification  
**Author:** Senior AI Systems Architect review

---

## Table of Contents

1. Phase 1 — Current Architecture Review
2. Phase 2 — Parser Investigation & Recommendation
3. Phase 3 — Extraction V2 Architecture
4. Phase 4 — LLM Strategy
5. Phase 5 — System Compatibility Analysis
6. Phase 6 — File-by-File Impact Analysis
7. Phase 7 — Specification, Migration Plan & Risk Analysis

---

## Phase 1 — Current Architecture Review

### 1.1 Current Architecture Summary

FinTool V1 is a four-stage extraction pipeline feeding a FastAPI backend:

```
Stage 1: parse_pdf()
         Docling → pdfplumber → pypdf fallback chain
         Output: DoclingParseResult (pages_raw_text[], tables[])

Stage 2: classify_statement_pages()
         Keyword matching → page tags
         Output: PageClassification[] (statement_type, section_type per page)

Stage 3: extract_from_docling_tables() / extract_candidates_from_page()
         DataFrame column scanning → regex label matching → CandidateMetric[]
         Resolution: build_resolved_metrics_df() → dedup + priority
         Pivot: pivot_metrics() → year × metric DataFrame
         Output: resolved_df, pivot_df, quality_report

Stage 4: chunk_document() + build_faiss_index()
         RecursiveCharacterTextSplitter → MiniLM embeddings → FAISS IndexFlatIP
         Output: chunks.jsonl + {doc_id}_faiss.index

Downstream:
         numerical_module.compute(pivot_df) → 13 ratios + 4 risk labels
         FastAPI routers serve the results via 7 endpoints
         RAG chain: FAISS retrieval → Claude API → chat answer
```

### 1.2 Architectural Strengths

| Strength | Detail |
|---|---|
| Clean stage separation | Each of the 4 stages has a single responsibility and a well-defined output contract |
| Strong downstream contracts | `resolved_df` and `pivot_df` have an exact column schema; `numerical_module.py` is pure math and entirely decoupled from extraction |
| Solid canonical schema | `canonical_metrics.py` defines 17 metrics with XBRL tags, patterns, and exclude lists — a complete extraction target specification |
| Working RAG pipeline | 709 chunks indexed, FAISS search working, grounded prompt builder functional |
| Robust resolution logic | Priority-based deduplication (consolidated > standalone, high > low confidence) handles multi-section documents |
| Accounting checks | `run_accounting_checks()` catches mixed consolidated/standalone extraction errors |
| Derived totals | `add_derived_totals_if_possible()` reconstructs missing balance sheet totals |
| FastAPI backend | All 7 endpoints correctly implemented against spec; Pydantic schema validation complete |

### 1.3 Architectural Weaknesses

**Critical weakness: the extraction layer is fundamentally brittle.**

The entire structured extraction path (Stage 3) is built on two assumptions:

1. The parser provides clean DataFrames with year-headers in the first 1–2 rows.
2. Row labels in those DataFrames exactly match one of the regex patterns in `canonical_metrics.py`.

Both assumptions fail in practice for Indian Annual Reports:

- Indian ARs use complex multi-column layouts with merged cells, ₹ unit headers spanning multiple rows, page headers interspersed in table rows, and section-jump rows ("See accompanying notes") that break the row-label assumption.
- Label variants are unlimited: "Revenue from Operations", "Turnover", "Net Sales", "Gross Revenue", "Income from Operations", "Value of Sales & Services" — no regex library can anticipate every company's phrasing.
- `pdfplumber` treats PDF navigation tabs ("CORPORATE OVERVIEW | STATUTORY REPORTS | FINANCIAL STATEMENTS") as table rows, polluting every extracted DataFrame.
- The fallback `extract_candidates_from_page()` was designed for simple text — it cannot parse two-column layouts where "Particulars" and values are separated by 12+ whitespace characters in the PDF text stream.

**Secondary weakness: no insight extraction.**

Narrative pages (MDA, notes, risk factors) are chunked and stored in FAISS but never read for financial insights. The RAG system retrieves them for chat but no structured insight is ever extracted from them.

### 1.4 Technical Debt

| # | Item | Impact |
|---|---|---|
| 1 | Three empty service files (`pipeline_service.py`, `compute_service.py`, `explain_service.py`) | Low — structural |
| 2 | SYNC_MODE blocks HTTP thread for 150–270 seconds | High — production blocking |
| 3 | Deprecated `@app.on_event("startup")` | Low — deprecation warning |
| 4 | `yoy_asset_growth` computed but not persisted | Medium — missing field |
| 5 | Output file naming: CSV uses stem, FAISS uses doc_id | Medium — data integrity risk |
| 6 | Company name detection reads BSE boilerplate | Low — already fixed in code |
| 7 | `doc_id` collision: md5(filename + filesize)[:12] | Low probability, zero handling |
| 8 | No test suite | High — no acceptance validation |
| 9 | Docling cache folder names hardcoded | Medium — silent fallback risk |
| 10 | SQLite only | Medium — no production migration path |

### 1.5 Bottlenecks Preventing Reliable Extraction

**Bottleneck 1 — Parser chain not activating correctly.**  
Docling models are cached and imports succeed. However, the actual PDF conversion throws an exception for JioFin (caught and swallowed), falling to pdfplumber. pdfplumber also fails silently for JioFin (likely an encoding or stream issue), falling to pypdf. pypdf returns text only — zero tables. Result: Stage 3 primary path receives 0 DataFrames.

**Bottleneck 2 — Line-parse fallback cannot handle Indian AR layout.**  
`extract_candidates_from_page()` relies on `re.split(r"\s{12,}", line)` to separate labels from values in text. Indian AR PDFs rendered via pypdf compress or expand whitespace inconsistently. The 12-space threshold is a guess that fails on most real pages.

**Bottleneck 3 — Regex label matching has fixed vocabulary.**  
`match_metric()` matches normalized labels against 40–50 regex patterns. Any label phrasing outside those patterns silently returns `None`. There is no way for the extractor to understand that "Net earnings attributable to shareholders" means `net_profit`.

**Bottleneck 4 — Consolidated vs standalone mixing.**  
Without clean table structure, the extractor cannot reliably determine which section a value belongs to, producing accounting-check failures (79% relative error on JioFin).

### 1.6 Why the Current Architecture Cannot Scale

- Every new company format requires a developer to identify new label variants and add regex patterns. This is O(companies) maintenance.
- Indian ARs vary by filing year: the same company may use different label phrasing in FY2023 vs FY2024.
- Multi-language documents (some notes sections include Hindi) are entirely unparseable by the current regex approach.
- Table structure variations (notes-style tables with sub-rows, hierarchical indent, multi-row merged headers) are not handled.
- The architecture has no learning mechanism — errors in production cannot be used to improve future extractions without developer intervention.

---

## Phase 2 — Parser Investigation & Recommendation

### 2.1 Docling Root-Cause Report

**Finding:** Docling is installed (v2.69.1), models are cached in the correct directory (both `docling-project--docling-layout-heron` and `docling-project--docling-models` exist), and all imports succeed. The `_docling_models_cached()` check returns `True`. However, the actual `converter.convert()` call throws a runtime exception for the JioFin PDF that is caught by the generic `except Exception` handler in `parse_pdf()`, silently falling to pdfplumber.

**Root cause categories:**

| Category | Assessment |
|---|---|
| Environmental | Possible — Python 3.9.6 on macOS with LibreSSL instead of OpenSSL. Docling's ML dependencies (torch, transformers) are primarily tested on 3.10/3.11 |
| Configuration | Not the issue — cache check passes, pipeline_opts are valid |
| Missing models | Not the issue — both model folders confirmed present |
| Code integration | Partial — the `except Exception: pass` swallows the real error; no logging means the actual exception is invisible |
| Architectural limitation | Fundamental — Docling processes 139 pages with full ML layout detection; this takes 150+ seconds and may OOM on Python 3.9 with macOS memory pressure |

**Exact diagnosis:** The `except Exception as e: print(f"[parser] Docling failed ({e})")` in `parse_pdf()` at line 300 of parser.py is the critical code path. The exception message IS printed, but in the actual run log shown in status_2.md, only `parser=pypdf` appears — meaning either:
1. The log output was truncated, or
2. Docling activated but then pdfplumber was selected for a different reason

**Definitive test needed:** Add `import traceback; traceback.print_exc()` inside the Docling except block to capture the full stack trace.

**Recommendation on Docling:**  
**Retain as optional enhancement, do not rely on as primary parser.** The combination of Python 3.9 compatibility uncertainty, 150+ second processing time, and silent failure mode makes Docling unsuitable as the primary parser for V2. With the LLM handling semantic understanding, Docling's primary value proposition (ML table structure) is partially superseded.

### 2.2 Parser Comparison Matrix

| Criterion | Docling | PyMuPDF (fitz) | Marker | LlamaParse |
|---|---|---|---|---|
| **Reliability** | Medium — silent failures on some PDFs | High — C++ binding, stable | Low — GPU-dependent | High — managed cloud |
| **Financial report suitability** | High — built for structured documents | High — demonstrated 44 table pages on JioFin | Medium — optimized for academic papers | Very high — purpose-built |
| **Table extraction quality** | Very high (when working) | Medium — bounding box only, no semantic merging | High (markdown) | Very high |
| **Markdown/text quality** | High | Medium — raw text, good layout preservation | Very high | Very high |
| **Setup complexity** | High — 500MB models, ML pipeline | Zero — already installed (v1.26.5) | High — GPU setup | Zero — cloud API |
| **Maintenance burden** | High — model updates, Python version sensitivity | Low — stable C library | Medium | Low — managed |
| **Offline capable** | Yes | Yes | Yes (with GPU) | No |
| **Speed (139-page PDF)** | 150–270s | 2–5s | 30–60s (GPU) | 5–10s (API call) |
| **Indian AR compatibility** | Medium | High — handles CJK, Unicode, complex encodings | Unknown | High |
| **Student project suitable** | Marginal | Yes | No (GPU) | No (paid API) |

### 2.3 Recommended Parser Strategy for V2

**Primary: PyMuPDF (fitz)**

PyMuPDF is already installed (v1.26.5), demonstrated finding 44 financial-statement pages with tables in JioFin, extracts both text and table bounding boxes, is orders of magnitude faster than Docling, and is a stable C library with no ML dependencies.

**Parser chain for V2:**
```
PyMuPDF  →  pypdf  (fallback for encrypted/damaged PDFs)
(primary)    (text-only fallback)
```

Docling is retained as an optional flag (`USE_DOCLING=true` in config) for cases where ML-quality table structure is required and processing time is acceptable.

**Key change in how parser output is used:**  
In V1, the parser had to produce structured DataFrames for metric_extractor to scan. In V2, the parser only needs to produce **clean text** (per-page raw text + per-page table text formatted as plain text or markdown). The LLM handles all semantic interpretation. This dramatically lowers the bar for what a "good" parser needs to deliver.

---

## Phase 3 — Extraction V2 Architecture

### 3.1 Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        FINTOOL EXTRACTION V2                               │
│                                                                            │
│  PDF File                                                                  │
│     │                                                                      │
│     ▼                                                                      │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  parser.py  [MODIFIED]                                               │  │
│  │  PyMuPDF primary  →  pypdf fallback  (→  Docling optional)          │  │
│  │                                                                      │  │
│  │  New output fields added to DoclingParseResult:                      │  │
│  │    pages_table_text: list[str]   ← tables formatted as plain text   │  │
│  │    pages_raw_text:   list[str]   ← unchanged                        │  │
│  └──────────────────────────────┬───────────────────────────────────────┘  │
│                                 │                                          │
│                                 ▼                                          │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  classifier.py  [KEEP — unchanged]                                   │  │
│  │  Keyword classification → PageClassification[] per page             │  │
│  │  balance_sheet / income_statement / cash_flow / notes / mda / other │  │
│  └────────────────────┬────────────────────────┬────────────────────────┘  │
│                       │                        │                           │
│            Financial Pages               Narrative Pages                   │
│          (balance_sheet,                (notes, mda, other)                │
│           income_stmt,                                                     │
│           cash_flow)                                                       │
│                       │                        │                           │
│       ┌───────────────┘                ┌───────┘                           │
│       │                                │                                   │
│       ▼                                ▼                                   │
│  ┌─────────────────────────┐   ┌───────────────────────────────────────┐  │
│  │  llm_extractor.py [NEW] │   │  insight_extractor.py [NEW]          │  │
│  │                         │   │                                       │  │
│  │  Per statement group:   │   │  Per narrative section (MDA/notes):  │  │
│  │  1. Assemble context    │   │  1. Assemble section text             │  │
│  │     (page text +        │   │  2. Send structured JSON prompt       │  │
│  │      table text)        │   │  3. Extract:                          │  │
│  │  2. Build JSON prompt   │   │     · business_risks[]                │  │
│  │  3. Call Ollama/Qwen2.5 │   │     · management_outlook              │  │
│  │  4. Parse JSON          │   │     · strategic_initiatives[]         │  │
│  │  5. Retry on failure    │   │     · regulatory_concerns[]           │  │
│  │  6. Fallback → regex    │   │     · key_financial_insights[]        │  │
│  └──────────┬──────────────┘   │     · forward_looking_statements[]   │  │
│             │                  └──────────────────┬────────────────────┘  │
│             ▼                                     │                        │
│  ┌─────────────────────────┐                      │                        │
│  │  validation.py [NEW]    │                      │                        │
│  │                         │                      ▼                        │
│  │  · JSON schema check    │           ┌───────────────────────────────┐  │
│  │    (Pydantic)           │           │  text_chunker.py [KEEP]       │  │
│  │  · Accounting checks    │           │                               │  │
│  │  · Range/sanity         │           │  Narrative text → chunks      │  │
│  │  · Unit normalization   │           │  Insight JSON → insight chunks│  │
│  │  → CandidateMetric[]    │           │  MiniLM embed → FAISS index   │  │
│  └──────────┬──────────────┘           └───────────────────────────────┘  │
│             │                                                              │
│             ▼                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  metric_extractor.py  [MODIFIED — resolution layer only]             │  │
│  │                                                                      │  │
│  │  KEPT:   build_resolved_metrics_df()  → dedup + priority resolution  │  │
│  │          pivot_metrics()              → year × metric DataFrame      │  │
│  │          add_derived_totals()         → balance sheet derivations    │  │
│  │          run_accounting_checks()      → identity verification        │  │
│  │          CandidateMetric (dataclass)  → unchanged data contract      │  │
│  │          parse_value()               → number parsing utility        │  │
│  │                                                                      │  │
│  │  REMOVED: extract_from_docling_tables()   → replaced by LLM         │  │
│  │           extract_candidates_from_page()  → retained as fallback    │  │
│  └──────────┬───────────────────────────────────────────────────────────┘  │
│             │                                                              │
│             ▼                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  pipeline.py  [MODIFIED — orchestrator]                              │  │
│  │                                                                      │  │
│  │  Stage 1: parse_pdf()             → DoclingParseResult               │  │
│  │  Stage 2: classify_pages()        → PageClassification[]             │  │
│  │  Stage 3a: llm_extractor.run()    → CandidateMetric[]                │  │
│  │  Stage 3b: insight_extractor.run()→ InsightResult (new)             │  │
│  │  Stage 3c: resolve + pivot        → resolved_df, pivot_df            │  │
│  │  Stage 4: chunk + index           → FAISS + insight chunks           │  │
│  │  Output:  all files, meta, logs   → extraction_outputs/{doc_id}/     │  │
│  └──────────┬───────────────────────────────────────────────────────────┘  │
│             │                                                              │
│  ┌──────────┼──────────────────────────────────────────────────────────┐  │
│  │          │              UNCHANGED DOWNSTREAM                         │  │
│  │          ▼                                                           │  │
│  │  ┌────────────────┐  ┌──────────────────┐  ┌──────────────────────┐ │  │
│  │  │ numerical_     │  │  fintool.db       │  │  FAISS index        │ │  │
│  │  │ module.py      │  │  documents        │  │  narrative chunks   │ │  │
│  │  │ (UNCHANGED)    │  │  resolved_metrics │  │  + insight chunks   │ │  │
│  │  │ 13 ratios      │  │  computed_metrics │  │  (new)              │ │  │
│  │  │ 4 risk labels  │  │  (UNCHANGED)      │  └──────────────────────┘ │  │
│  │  └────────────────┘  └──────────────────┘                            │  │
│  │                                                                       │  │
│  │  FastAPI backend, all 7 endpoints → UNCHANGED                        │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Component Responsibilities

**parser.py** — Raw document access layer  
- Converts PDF bytes to per-page text strings and per-page table text strings
- Does NOT interpret content — returns raw strings only
- Table text is formatted as plain-text markdown-style tables for LLM readability
- PyMuPDF extracts text with `page.get_text("text")` and tables with `page.find_tables()`; each table is serialized to a pipe-delimited text representation

**classifier.py** — Page routing layer  
- Assigns each page a statement_type and section_type using keyword matching
- Routes financial pages to the LLM structured extraction path
- Routes narrative pages to the insight extraction + chunking path
- No changes required

**llm_extractor.py** — Structured financial extraction  
- Groups financial pages by statement_type (balance_sheet pages together, income_statement pages together, etc.)
- Assembles a prompt context from page text + table text for each group
- Sends a single structured JSON prompt to the Ollama LLM asking for all metrics in that statement type
- Parses and validates the JSON response
- Retries up to 3 times on malformed output with error context
- Falls back to the regex path (`extract_candidates_from_page`) if all LLM attempts fail
- Returns: `list[CandidateMetric]` with `source="llm"` and `confidence="high"`

**insight_extractor.py** — Unstructured narrative insight extraction  
- Processes MDA, notes, and risk-factor pages
- Sends structured JSON prompts asking for categorized insights
- Returns: `InsightResult` dataclass with typed lists for each insight category
- Insights are persisted as `{doc_id}_insights.json` and also chunked into FAISS for enhanced RAG retrieval

**validation.py** — Output integrity layer  
- Applies Pydantic schema validation to raw LLM JSON output before conversion to CandidateMetric
- Runs accounting identity checks on extracted values before they enter the resolution pipeline
- Applies unit normalization (crore/lakh/million detection from context)
- Filters hallucinated metrics (values outside plausible range for the detected unit)
- Returns validated `list[CandidateMetric]` or raises `ExtractionValidationError`

**metric_extractor.py** — Resolution and pivot layer (retained, scope reduced)  
- `build_resolved_metrics_df()`: unchanged — deduplication and priority resolution
- `pivot_metrics()`: unchanged — produces the year × metric DataFrame for numerical_module.py
- `add_derived_totals_if_possible()`: unchanged
- `run_accounting_checks()`: unchanged
- `CandidateMetric` dataclass: unchanged — this is the integration contract between LLM output and downstream

**text_chunker.py** — RAG index builder (unchanged)  
- Existing chunking and FAISS index logic untouched
- Insight chunks added as a new chunk category with `section_type="insight"`

**pipeline.py** — Orchestrator (modified)  
- Calls new stages in order
- File naming standardized to `{doc_id}_*` throughout (fixes existing inconsistency)
- Insight JSON added as a new output artifact

---

## Phase 4 — LLM Strategy

### 4.1 Model Comparison

| Criterion | Qwen2.5-7B-Instruct | Qwen2.5-14B-Instruct | Llama 3.1-8B-Instruct | Mistral-7B-Instruct |
|---|---|---|---|---|
| **Structured JSON generation** | Excellent — explicit JSON mode, trained with structured output | Excellent — same base, more capable | Good — Meta instruction tuning | Fair — inconsistent on complex schemas |
| **Financial reasoning** | Very high — strong numeric comprehension | Excellent | Good | Fair |
| **Multilingual** | Excellent — Chinese + English training; handles Hindi, Marathi in notes | Excellent | Limited | Limited |
| **Ollama availability** | `qwen2.5:7b-instruct` | `qwen2.5:14b-instruct` | `llama3.1:8b-instruct` | `mistral:7b-instruct` |
| **RAM (4-bit quantized)** | ~6GB | ~10GB | ~6GB | ~5GB |
| **VRAM (GPU offload)** | ~8GB | ~14GB | ~8GB | ~6GB |
| **Student project suitable** | Yes — runs on 16GB RAM MacBook | Marginal — needs 16GB+ | Yes | Yes |
| **Instruction following** | Excellent | Excellent | Good | Fair |
| **Context window** | 32K tokens | 32K tokens | 128K tokens | 32K tokens |
| **Extraction benchmark (approx.)** | High (93%+ on structured tasks) | Very high | Medium-high (88%) | Medium (80%) |

### 4.2 Recommendation

**Primary: Qwen2.5-7B-Instruct via Ollama**

Justification:
1. Qwen2.5's JSON mode is explicitly trained and reliable. Financial extraction requires strict JSON schema adherence; hallucinated or malformed JSON is a failure mode, not just a quality issue.
2. Strong multilingual handling is essential for Indian ARs, which frequently contain transliterated terms, Hindi company names, and mixed-script notes.
3. 7B quantized runs within 16GB RAM on a student MacBook without GPU.
4. Available today: `ollama pull qwen2.5:7b-instruct`

**Fallback: Qwen2.5-3B-Instruct**  
For deployments with less than 12GB RAM. Acceptable for extraction with somewhat higher retry rates.

**Upgrade path: Qwen2.5-14B-Instruct**  
If hardware allows. Significant improvement on complex multi-column balance sheet extraction.

**Do not use Llama 3.1 for extraction** unless Qwen2.5 is unavailable. Llama 3.1-8B produces higher rates of malformed JSON on deeply nested extraction schemas.

### 4.3 Ollama Deployment Specification

```
Service:  ollama serve
Port:     11434 (default)
Model:    qwen2.5:7b-instruct
Options:
  num_ctx: 8192        (sufficient for 3–5 financial pages)
  temperature: 0.0     (deterministic — extraction must be reproducible)
  top_p: 1.0
  format: json         (Ollama JSON mode — enforces valid JSON output)
```

The LLM is called synchronously within the extraction pipeline. Because extraction already runs in a background task (or SYNC_MODE), the blocking Ollama call does not affect the HTTP server. Expected processing time: 15–45 seconds per document for the full LLM extraction phase (all statement groups combined), far less than Docling's 150–270 seconds.

---

## Phase 5 — System Compatibility Analysis

### 5.1 What Changes vs What Stays

**The fundamental integration contract is `CandidateMetric → resolved_df → pivot_df`.**

V2's LLM extractor converts its JSON output to `CandidateMetric` objects before passing them to `build_resolved_metrics_df()`. Everything from that point downstream is unchanged.

### 5.2 Component-by-Component Compatibility

**numerical_module.py — UNCHANGED**  
Receives `pivot_df` with the exact same schema: rows = years, columns = the 17 canonical metric names from `canonical_metrics.py`. V2 extraction produces the same `pivot_df` through the same `pivot_metrics()` function. No changes to `numerical_module.py` are required.

**Backend APIs — UNCHANGED**  
All 7 FastAPI endpoints (`/upload`, `/status`, `/metrics`, `/ratios`, `/risks`, `/explain`, `/chat`) are unchanged. The `extraction_task.py` still calls `ExtractionPipeline().run()` — only the internals of that pipeline change.

**Database schema — UNCHANGED**  
`documents`, `resolved_metrics`, and `computed_metrics` tables are unchanged. Data inserted into them comes from the same `resolved_df` and `numerical_module` output objects.

**MiniLM embeddings — UNCHANGED**  
`text_chunker.py` is unchanged. The SentenceTransformer model and embedding process are identical. New insight chunks are added as a new chunk category but use the same embedding model and FAISS structure.

**FAISS infrastructure — UNCHANGED**  
`{doc_id}_faiss.index` and `{doc_id}_chunks.jsonl` files are produced by the same code. Insight chunks extend the JSONL with `section_type="insight"` entries. The RAG query path in `rag_service.py` is unchanged — it retrieves the top-4 chunks regardless of type, which now includes insights.

**RAG infrastructure — ENHANCED, not broken**  
The existing `build_grounded_prompt()` in `rag_service.py` already handles arbitrary retrieved chunks. Adding insight chunks to the FAISS index means chat queries about "management outlook" or "business risks" now retrieve relevant structured content. No code changes needed in `rag_service.py`.

### 5.3 New Integration Points

| New Component | Integrates With | Contract |
|---|---|---|
| `llm_extractor.py` | `metric_extractor.py` | Returns `list[CandidateMetric]` — same type as regex extractor |
| `validation.py` | `llm_extractor.py` | Validates LLM JSON → raises or passes through |
| `insight_extractor.py` | `text_chunker.py` | `InsightResult` converted to `TextChunk` list for FAISS |
| `ollama_client.py` | `llm_extractor.py`, `insight_extractor.py` | HTTP REST client to Ollama API |

---

## Phase 6 — File-by-File Impact Analysis

### 6.1 Existing Files

#### `extraction/parser.py` — MODIFY

**What changes:**
- Add `_parse_with_pymupdf()` as the new primary parser
- PyMuPDF extracts: (a) per-page text via `page.get_text("text")`, (b) per-page tables via `page.find_tables()`, serializing each table to pipe-delimited plain text
- Add `pages_table_text: list[str]` field to `DoclingParseResult` — one string per page containing all tables on that page formatted for LLM consumption
- Move Docling to an optional mode, activated only when `USE_DOCLING=true` config is set
- Remove the `_DOCLING_LAYOUT_FOLDER` / `_DOCLING_TABLE_FOLDER` cache-check logic (no longer the primary path)
- Retain pypdf as final fallback

**What stays:**
- `DoclingParseResult` dataclass — same fields, one new field added
- `DoclingTable` dataclass — retained for Docling optional mode
- `_make_doc_id()` — unchanged
- `parse_pdf()` public interface — unchanged signature

**Justification:** PyMuPDF demonstrated 44 financial pages with tables on JioFin in seconds. The LLM consumes text, so per-page plain-text table representation is sufficient; full DataFrame structure is no longer required.

---

#### `extraction/classifier.py` — KEEP

**No changes required.**

The keyword-based page classifier correctly identifies statement types. It is fast, deterministic, and accurate enough. The LLM does not replace page routing — it replaces value extraction within already-routed pages.

---

#### `extraction/metric_extractor.py` — MODIFY (scope reduction)

**What stays (critical):**
- `CandidateMetric` dataclass — the core integration contract; unchanged
- `parse_value()` — still used by validation.py for unit normalization
- `build_resolved_metrics_df()` — unchanged; LLM output flows through this
- `pivot_metrics()` — unchanged
- `add_derived_totals_if_possible()` — unchanged
- `run_accounting_checks()` — unchanged
- `build_resolved_metrics_df()`, `pivot_metrics()` are called from pipeline.py exactly as before

**What changes:**
- `extract_from_docling_tables()` — demoted from primary to Docling-optional mode
- `extract_candidates_from_page()` — demoted to LLM fallback (called when Ollama is unavailable or all retries exhausted); retained as the last-resort path
- `detect_year_columns()`, `_find_data_start_row()` — retained for Docling-optional mode and line-parse fallback

**Justification:** The resolution and pivot logic is correct and well-tested. Only the extraction path changes. Retaining the regex path as a fallback preserves the existing behavior when the LLM is unavailable.

---

#### `extraction/canonical_metrics.py` — KEEP

**No changes required.**

`CANONICAL_METRICS` becomes the authoritative schema for LLM extraction prompts. Instead of using the regex patterns for matching, `llm_extractor.py` uses the metric keys as the JSON schema field names that the LLM must populate. The `XBRL_tags` field can be added to prompts as additional aliases. The existing patterns and exclude lists are still used by the regex fallback path.

---

#### `extraction/text_chunker.py` — KEEP

**No changes required to core chunking logic.**

One new integration: `pipeline.py` passes insight chunks (from `insight_extractor.py`) to `build_faiss_index()` as additional `TextChunk` objects with `section_type="insight"`. No changes to `text_chunker.py` itself are needed; this is orchestrated in `pipeline.py`.

---

#### `extraction/pipeline.py` — MODIFY

**What changes:**
- Stage 3 updated: calls `llm_extractor.run()` instead of `extract_from_docling_tables()`
- New Stage 3b: calls `insight_extractor.run()` on narrative pages
- File naming standardized: all output files use `{doc_id}_*` prefix (fixes existing inconsistency)
- Insight JSON saved as `{doc_id}_insights.json`
- Insight chunks fed to `build_faiss_index()` alongside narrative chunks
- Stage logging updated

**What stays:**
- `PipelineResult` dataclass — add `insights` field
- `ExtractionPipeline.run()` public interface — unchanged; called from `extraction_task.py`
- Stages 1, 2, 4 (parse, classify, RAG) — unchanged

**Justification:** The orchestrator must be updated to wire the new components. All other callers (`extraction_task.py`) remain unchanged because `ExtractionPipeline().run()` signature is preserved.

---

### 6.2 New Files to Create

#### `extraction/llm_extractor.py` — NEW

**Purpose:** Primary structured metric extraction via LLM.

**Responsibilities:**
- Group financial pages by statement type (all balance sheet pages, all income statement pages, all cash flow pages — each group sent as one prompt)
- Build the extraction prompt: system prompt with metric schema + user message with page context
- Call Ollama via `ollama_client.py`
- Parse returned JSON: `{metric_name: {value: float, year: int, unit: str, section: str, confidence: str}}`
- Retry logic: up to 3 attempts, on failure inject the error message into the next prompt
- Convert parsed output to `list[CandidateMetric]` via `validation.py`
- On total failure: log and call `extract_candidates_from_page()` regex fallback
- Return: `list[CandidateMetric]` with `source="llm"`

**Prompt design principles:**
- System prompt defines the extraction task, the expected JSON schema, and examples
- User message includes the statement type, all page texts for that type, and explicit instructions for consolidated vs standalone disambiguation
- The 17 canonical metric keys from `canonical_metrics.py` form the required JSON fields
- Temperature=0.0 enforces determinism

---

#### `extraction/insight_extractor.py` — NEW

**Purpose:** Unstructured financial insight extraction from narrative sections.

**Responsibilities:**
- Process MDA, notes, and risk-factor pages in batches (by section)
- Build insight extraction prompts — one prompt per section type
- Call Ollama and parse the structured JSON response
- Return `InsightResult` dataclass

**InsightResult schema:**
```
InsightResult:
  doc_id: str
  business_risks: list[str]         ← each is a concise 1-2 sentence risk statement
  management_outlook: str           ← management's stated view on future performance
  strategic_initiatives: list[str]  ← identified strategic priorities
  regulatory_concerns: list[str]    ← compliance, regulatory, or legal items flagged
  key_financial_insights: list[str] ← important numeric context from narrative
  forward_looking_statements: list[str] ← any forward-looking guidance
  source_pages: list[int]           ← page numbers that contributed
```

**Integration with RAG:** Each list element is converted to a `TextChunk` with `section_type="insight"` and stored in FAISS. This means `/chat` queries about business risks will retrieve LLM-extracted, structured risk statements — not raw paragraphs.

---

#### `extraction/validation.py` — NEW

**Purpose:** Validate and sanitize LLM output before it enters the extraction pipeline.

**Responsibilities:**
- Pydantic model for the expected LLM JSON structure
- Validate each extracted value: is it a number? Is it within a plausible range?
- Unit normalization: detect "crore", "lakh", "million" from page context and normalize
- Accounting pre-check: flag if assets ≠ equity + liabilities before entering resolution
- Hallucination filter: reject values that are clearly year numbers (2024, 2025) appearing as metric values
- Conversion: validated LLM output → `list[CandidateMetric]`
- On validation failure: raise `ExtractionValidationError` so `llm_extractor.py` can retry

---

#### `extraction/schema_definitions.py` — NEW

**Purpose:** Centralized Pydantic models for all V2 data contracts.

**Contents:**
- `LLMMetricValue` — single metric value from LLM (value, year, unit, section, confidence)
- `LLMExtractionResponse` — full LLM response per statement type
- `InsightResult` — narrative insight extraction output
- `ExtractionValidationError` — raised when LLM output fails validation

This separates schema definition from logic, enabling clean imports and testability.

---

#### `extraction/ollama_client.py` — NEW

**Purpose:** Wrapper for the Ollama REST API.

**Responsibilities:**
- Configures endpoint from `OLLAMA_BASE_URL` env var (default: `http://localhost:11434`)
- Configures model from `OLLAMA_MODEL` env var (default: `qwen2.5:7b-instruct`)
- Single `generate(prompt: str, system: str) -> str` method
- Enforces `format="json"` in the request
- Handles connection errors gracefully (raises `OllamaUnavailableError`)
- Timeout: 120 seconds per call (financial statements can be long prompts)

---

### 6.3 Files to Remove

**None at this stage.**

The regex extraction functions in `metric_extractor.py` are retained as the fallback path. Removing them before the LLM path is validated would eliminate the safety net. Post-validation, `extract_from_docling_tables()` can be removed in a cleanup pass.

---

## Phase 7 — Specification, Migration Plan & Risk Analysis

### 7.1 Updated Extraction Specification

**Input contract:** PDF file path  
**Output contract:** `PipelineResult` — unchanged dataclass, `insights` field added

**Stage 1 — Parsing**  
- Primary parser: PyMuPDF  
- Output: `DoclingParseResult` with `pages_raw_text[]` and `pages_table_text[]`  
- Fallback: pypdf (text-only)  
- Optional: Docling (activated by `USE_DOCLING=true`)  
- Failure mode: raises `RuntimeError` only if all parsers fail

**Stage 2 — Classification**  
- Unchanged keyword-based classifier  
- Output: `PageClassification[]` — one per page  
- Routes financial pages → structured path; narrative pages → insight path

**Stage 3a — LLM Structured Extraction**  
- Groups pages by statement type  
- One Ollama call per statement type (3 calls maximum: balance_sheet, income_statement, cash_flow)  
- Returns `list[CandidateMetric]` with `source="llm"`  
- Retry: up to 3 attempts per statement type  
- Fallback: `extract_candidates_from_page()` regex path on total LLM failure  
- Validation: `validation.py` before conversion to CandidateMetric  
- Resolution: `build_resolved_metrics_df()` → `pivot_metrics()` — unchanged

**Stage 3b — LLM Insight Extraction**  
- Processes MDA and notes sections  
- One Ollama call per narrative section type  
- Returns `InsightResult`  
- Persisted as `{doc_id}_insights.json`  
- Insight strings converted to `TextChunk[]` for FAISS

**Stage 4 — RAG Indexing**  
- Unchanged: `chunk_document()` + `build_faiss_index()`  
- Enhanced: insight chunks added to FAISS index

**Output artifacts:**
```
extraction_outputs/{doc_id}/
  {doc_id}_resolved_metrics.csv    ← renamed from stem-based (bug fix)
  {doc_id}_pivot_metrics.csv       ← renamed from stem-based (bug fix)
  {doc_id}_quality_report.json     ← unchanged schema
  {doc_id}_insights.json           ← NEW
  {doc_id}_meta.json               ← unchanged schema
  {doc_id}_faiss.index             ← unchanged
  {doc_id}_chunks.jsonl            ← unchanged; insight chunks appended
  {doc_id}_log.txt                 ← unchanged
```

**Ollama configuration (new env vars):**
```
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
OLLAMA_TIMEOUT_SECONDS=120
LLM_EXTRACTION_MAX_RETRIES=3
USE_DOCLING=false
```

---

### 7.2 Migration Roadmap

#### Sprint 1 — Foundation (Days 1–3)

**Goal:** Replace parser, establish Ollama connectivity.

Tasks:
1. Add `_parse_with_pymupdf()` to `parser.py` — set as primary, move Docling to optional
2. Add `pages_table_text` field to `DoclingParseResult`
3. Create `extraction/ollama_client.py` — basic HTTP wrapper + health check
4. Create `extraction/schema_definitions.py` — all Pydantic models
5. Add Ollama env vars to `backend/config.py`
6. Verify end-to-end: `parse_pdf()` → correct text and table text for JioFin

**Acceptance criteria:**
- PyMuPDF parses JioFin in < 10 seconds
- `pages_table_text[82]` contains the balance sheet table text (page 83 is financial)
- Ollama health check returns 200 with Qwen2.5-7B loaded

---

#### Sprint 2 — LLM Structured Extraction (Days 4–8)

**Goal:** Replace regex extraction with LLM extraction; validate against JioFin.

Tasks:
1. Create `extraction/validation.py` — Pydantic validation + CandidateMetric conversion
2. Create `extraction/llm_extractor.py` — prompt builder, Ollama call, retry logic, fallback
3. Update `pipeline.py` Stage 3a to call `llm_extractor.run()` instead of `extract_from_docling_tables()`
4. Fix output file naming inconsistency in `pipeline.py` (stem → doc_id prefix)
5. Run full pipeline on JioFin; measure metric coverage vs V1 (target: ≥ 14/17 metrics)
6. Run accounting check: target total_assets ≈ equity + liabilities within 3%

**Acceptance criteria:**
- All 8 core metrics present for both 2024 and 2025 in JioFin
- At least one non-null ratio computed by `numerical_module.py`
- No accounting check warnings (or < 3% relative error)
- Retry logic triggers and succeeds on at least one malformed response test

---

#### Sprint 3 — Insight Extraction (Days 9–12)

**Goal:** Add structured insight extraction from narrative sections.

Tasks:
1. Create `extraction/insight_extractor.py` — prompt builder, InsightResult, FAISS integration
2. Update `pipeline.py` Stage 3b to call `insight_extractor.run()`
3. Update `text_chunker.py` call in `pipeline.py` to include insight chunks
4. Persist `{doc_id}_insights.json`
5. Verify: `/chat` query "What are the main business risks?" retrieves insight chunks

**Acceptance criteria:**
- `{doc_id}_insights.json` contains ≥ 3 business risks for JioFin
- FAISS index includes insight chunks (total chunk count increases)
- Chat query about risks retrieves at least one insight chunk in top-4 results

---

#### Sprint 4 — Validation & Multi-Document Testing (Days 13–16)

**Goal:** Verify extraction quality across multiple documents; fix edge cases.

Tasks:
1. Run pipeline on 3 additional test PDFs (Tata Steel, Reliance, one more)
2. Compare extracted values against EDGAR benchmark (edgar.ipynb)
3. Tune prompts based on failure modes
4. Implement `yoy_asset_growth` in DB schema and `RatioItem` schema
5. Write extraction acceptance tests

**Acceptance criteria:**
- ≤ 5% extraction error on EDGAR-verified metrics for 3 companies
- Test suite covers: parse, classify, LLM extract, validate, resolve, pivot, compute

---

#### Sprint 5 — Cleanup & Production Prep (Days 17–20)

**Goal:** Remove V1 debt, prepare for Phase 4 (frontend).

Tasks:
1. Fill or delete empty service files
2. Fix deprecated `@app.on_event("startup")` → `@app.lifespan`
3. Implement Redis + Celery (remove SYNC_MODE)
4. Update ANTHROPIC_API_KEY with real key
5. Update CORS for production domain

---

### 7.3 Implementation Order (dependency-ordered)

```
1. schema_definitions.py          (no dependencies)
2. ollama_client.py               (no dependencies)
3. parser.py [PyMuPDF]            (no new dependencies)
4. validation.py                  (depends on schema_definitions)
5. llm_extractor.py               (depends on ollama_client, validation, metric_extractor)
6. insight_extractor.py           (depends on ollama_client, schema_definitions)
7. pipeline.py [modified]         (depends on all above)
8. text_chunker.py [insight path] (depends on insight_extractor)
9. backend/config.py [new vars]   (independent)
10. test suite                    (depends on all above)
```

---

### 7.4 Risk Analysis

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| Ollama unavailable in deployment | High | Low | OllamaUnavailableError falls back to regex path; document Ollama setup requirement |
| LLM hallucination of metric values | High | Medium | validation.py range checks; accounting identity check; retry with error feedback |
| Qwen2.5 malformed JSON output | Medium | Low | `format=json` Ollama mode enforces valid JSON; retry loop handles remaining edge cases |
| LLM extraction slower than expected | Medium | Low | 3 Ollama calls × 15–30s each = 45–90s; total pipeline remains under 5 minutes |
| Prompt token limit exceeded (very long PDFs) | Medium | Medium | Truncate page context to 4000 tokens per statement group; process pages in chunks if needed |
| Insight extraction produces low-quality output | Low | Medium | Insights are additive only; failure doesn't break metrics path; insights stored as-is for RAG |
| PyMuPDF table formatting fails for complex merged-cell tables | Medium | Medium | LLM is more tolerant of imperfect table text than regex; validation catches numerical errors |
| Migration breaks existing JioFin DB record | Low | Low | Re-process by re-uploading; or update DB record directly |
| V1 regex fallback returns wrong values when LLM unavailable | Medium | Low | Existing behavior — no regression vs V1 |

---

### 7.5 Estimated Development Effort

| Deliverable | Effort |
|---|---|
| `parser.py` PyMuPDF integration | 0.5 day |
| `ollama_client.py` | 0.5 day |
| `schema_definitions.py` | 0.5 day |
| `validation.py` | 1 day |
| `llm_extractor.py` (including prompt engineering) | 2 days |
| `insight_extractor.py` | 1.5 days |
| `pipeline.py` updates | 0.5 day |
| Prompt tuning across 3 documents | 2 days |
| Test suite | 2 days |
| **Total (V2 extraction layer)** | **10.5 days** |
| Frontend (Phase 4, unchanged estimate) | 5 days |
| EDGAR validation | 1 day |
| Redis + Celery + production prep | 2 days |
| **Total to production-ready** | **~19 days** |

---

## Appendix A — LLM Prompt Design Reference

### Structured Extraction Prompt (balance_sheet example)

**System:**
```
You are a financial data extraction engine. Extract specific financial metrics from 
Indian Annual Report balance sheet pages and return them as valid JSON only.

You must extract ALL of the following metrics where present. If a metric is not found, 
omit it from the response.

Required metric fields (use exactly these keys):
- total_assets
- current_assets
- cash_and_equivalents
- total_liabilities
- current_liabilities
- long_term_debt
- short_term_debt
- total_equity

For each metric found, return:
{
  "metric_name": {
    "value": <number>,
    "year": <4-digit year as integer>,
    "unit": "<crore|lakh|million|as_reported>",
    "section": "<consolidated|standalone|unknown>",
    "confidence": "<high|medium|low>"
  }
}

If a metric appears for multiple years, return each as a separate entry:
{
  "total_assets_2025": {...},
  "total_assets_2024": {...}
}

Rules:
- Numbers in parentheses are negative: (4,521) = -4521
- Remove commas from numbers: 1,89,483 = 189483
- Look for "Consolidated" or "Standalone" in page headers to determine section
- Prefer consolidated figures over standalone when both exist
- Return ONLY valid JSON. No explanation, no markdown.
```

**User:**
```
BALANCE SHEET PAGES FROM ANNUAL REPORT

Page 83:
[raw text of page 83]

TABLES ON PAGE 83:
| Particulars | As at 31st March, 2025 | As at 31st March, 2024 |
| FINANCIAL ASSETS | | |
| Cash and cash equivalents | 352.32 | 1.00 |
| ...
```

### Insight Extraction Prompt (MDA example)

**System:**
```
You are a financial analyst. Extract structured insights from the Management Discussion 
and Analysis section of an Indian Annual Report.

Return exactly this JSON structure:
{
  "business_risks": ["<risk statement>", ...],
  "management_outlook": "<overall outlook summary>",
  "strategic_initiatives": ["<initiative>", ...],
  "regulatory_concerns": ["<concern>", ...],
  "key_financial_insights": ["<insight with number if available>", ...],
  "forward_looking_statements": ["<statement>", ...]
}

Each list item should be a complete, standalone sentence. Maximum 10 items per list.
Return ONLY valid JSON.
```

---

## Appendix B — V2 Configuration Reference

```env
# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
OLLAMA_TIMEOUT_SECONDS=120
LLM_EXTRACTION_MAX_RETRIES=3

# Parser
USE_DOCLING=false

# Existing (unchanged)
ANTHROPIC_API_KEY=<real key>
DATABASE_URL=sqlite:///./fintool.db
UPLOAD_DIR=uploads
EXTRACTION_OUTPUT_DIR=extraction_outputs
MAX_FILE_SIZE_MB=50
SYNC_MODE=true
```

---

*End of Extraction V2 Architecture Specification*
