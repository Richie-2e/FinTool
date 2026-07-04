# FinTool — Structured Build Plan
## GenAI Financial Document Analysis System — Group 8

---

## Answering Your Open Questions First

### Q1: Should chunking happen before or after structured metric extraction?

**After. Always after. They are independent parallel branches, not sequential.**

The pipeline has two completely separate output paths from the same raw text:

```
PDF → Docling Parser
         │
         ├── TABLE output → metric_extractor → resolved_metrics_df  (structured store)
         │
         └── TEXT output  → text_chunker    → FAISS index           (vector store)
```

These two branches never exchange data. The metric extractor never reads from FAISS.
The chunker never reads from the metric store.
The reason is fundamental: metrics need deterministic exact-match logic; text for RAG needs
semantic fuzzy retrieval. Mixing them would compromise both.

---

### Q2: Structured chunking or recursive chunking for financial documents?

**Recursive chunking. Here's why for your specific document type:**

Financial annual reports have this structure:
- MD&A: long paragraphs with complex multi-clause sentences
- Risk Factors: bullet lists separated by `\n` with embedded numbers
- Notes to Accounts: numbered sections with dense multi-line disclosures
- Cash Flow Commentary: short paragraphs with specific numbers embedded

Fixed-size (character/token) chunking cuts sentences mid-way — a single liquidity
disclosure like "The company's current ratio declined from 1.8 in FY23 to 1.2 in FY24,
primarily due to..." would be split, losing the causal connection.

Recursive character splitting tries separators in order: `\n\n` → `\n` → `. ` → ` `.
This means it always tries to preserve paragraph boundaries first, then sentence
boundaries, and only falls back to word-level cuts when absolutely necessary.

**Parameters:** chunk_size=800 tokens, overlap=150 tokens (see SPEC_extraction.md §5).

---

### Q3: What embedding model?

**`sentence-transformers/all-MiniLM-L6-v2`** — free, local, no API key, fast.

For your project's evaluation phase, upgrade to `BAAI/bge-base-en-v1.5` for
comparison. Both run offline on CPU without GPU.

Do not use OpenAI embeddings — they require a paid API key and add
per-query cost that makes the system expensive to demo.

---

### Q4: What does the EDGAR notebook actually produce? Do you have a benchmark table?

**The EDGAR notebook is complete code, but it has not been run yet** (it printed
a warning that `./2024_q4` path does not exist). You need to download the EDGAR data
and run it to get the actual benchmark CSV files.

Here is exactly what running it correctly will produce:

| Output File | What it Contains | How You Use It |
|---|---|---|
| `01_dataset_overview.txt` | Stats: companies, filings, year range | Mid-sem report only |
| `02_top_tags.csv` | Most common XBRL tags ranked by frequency | Validates your `PHASE1_METRICS` tag choices |
| `03_industry_distribution.csv` | Filing counts by sector | Mid-sem report only |
| `04_phase1_benchmark.csv` | Raw base metrics: one row per (company, year) | Input to computation module |
| `05_computed_metrics.csv` | **The actual benchmark table**: all ratios + risk labels for every company-year | **Validation ground truth for SEC companies** |
| `06_risk_summary.csv` | Aggregate risk distribution | Mid-sem report |
| `07_insights_report.txt` | Human-readable summary | Mid-sem report |

**Your validation workflow once you have `05_computed_metrics.csv`:**
1. Pick 3–5 SEC companies from the benchmark (e.g. Apple, Ford, JPMorgan)
2. Download their 10-K PDFs from SEC EDGAR
3. Run your extraction pipeline on those PDFs
4. Compare your `current_ratio`, `debt_to_equity`, `profit_margin` to the EDGAR benchmark
5. Acceptable tolerance: ≤ 5% relative error

**How to download EDGAR data:**
```bash
# Download 2024 Q4 dataset (contains 10-K filings for fiscal year 2024)
wget https://www.sec.gov/Archives/edgar/full-index/2024/QTR4/company.idx
# Or use the bulk dataset:
wget https://www.sec.gov/Archives/edgar/full-index/companyfacts.zip
# Easier: use the EDGAR API
pip install edgartools
```

---

## The Build Plan — Week by Week

### Phase 0 — Fix EDGAR & Get Ground Truth (1 week, ~3 days of effort)
**Goal:** Run the EDGAR notebook successfully and produce `05_computed_metrics.csv`.

1. Download 2024 Q4 EDGAR ZIP from `https://www.sec.gov/Archives/edgar/full-index/2024/QTR4/`
   - Files: `sub.txt`, `num.txt`, `tag.txt`, `pre.txt`
2. Update `DATA_DIRS` in the EDGAR notebook to point to the correct folder
3. Run all cells in order
4. Verify `05_computed_metrics.csv` has ≥ 500 company-year rows
5. Pick 3 test companies (Apple, Ford, one financial company) and manually verify
   one ratio each against their actual 10-K PDF

**Deliverable:** `05_computed_metrics.csv` saved and verified.
**This is your benchmark. You cannot validate extraction without it.**

---

### Phase 1 — Fix the Extraction Pipeline (1–2 weeks)
**Goal:** `generic_financial_pdf_extractor_v3` produces correct resolved metrics for all 4 test PDFs.

**Step 1.1 — Switch to Docling**
```bash
pip install docling
```
Write `parser.py` as specified in SPEC_extraction.md §2.
Test: run `parse_pdf("AR_TATASTEEL_2024_2025.pdf")` and print the first 3 tables.
They should be DataFrames, not garbled strings.

**Step 1.2 — Fix the year-leak bug**
In `extract_header_years_with_order()`, change:
```python
head = page_head(page_text, 28)   # current: 28 lines
# Change to:
head = page_head(page_text, 12)   # 12 lines is enough for any statement header
```
Then add a test: for each of the 4 PDFs, print the detected years per classified page.
None of the years should come from note tables (note pages rarely contain "March 2024" in the first 12 lines).

**Step 1.3 — Add Docling table extraction path**
Write `extract_from_docling_tables()` in `metric_extractor.py`.
The key insight: Docling gives you a clean DataFrame per table. You only need to:
- Detect which row is the header row (first row containing years)
- For each subsequent row, run `match_metric(label, ...)` on the first column
- For each match, read the values from the year columns

**Step 1.4 — Add the text chunking stage**
Write `text_chunker.py` using LangChain's `RecursiveCharacterTextSplitter`.
```bash
pip install langchain-text-splitters sentence-transformers faiss-cpu
```
Test: chunk one 200-page PDF → should produce ≥ 150 chunks, each with `page_no` populated.

**Step 1.5 — Run the 4 test PDFs through the full pipeline**
Run `process_many_v3()` (renamed to `ExtractionPipeline.run()`) on all 4 PDFs.
Check quality report for each: `missing_core_metrics_by_year` should be empty or near-empty.

**Deliverable:** All 4 PDFs produce `_pivot_metrics.csv` with ≥ 6 core metrics extracted per year, and `_chunks.jsonl` with ≥ 80 chunks.

---

### Phase 2 — Build the Computation Module (3–4 days)
**Goal:** `numerical_module.py` computes all ratios and risk labels from any pivot_df.

This is the simplest phase — it is pure math. All formulas are in SPEC_computation.md.

1. Create `numerical_module.py`
2. Implement `compute(doc_id, pivot_df, resolved_df) -> ComputedResult`
3. Run it on the Tata Steel pivot and manually verify `current_ratio`, `debt_to_equity`, `profit_margin`
4. Run validation against `05_computed_metrics.csv` for the 3 SEC test companies

**Deliverable:** `numerical_module.py` passes all acceptance tests in SPEC_computation.md §9.

---

### Phase 3 — Build the FastAPI Backend (1 week)
**Goal:** All 7 endpoints from SPEC_api.md are working and tested with Postman/curl.

Build in this order (each step builds on the previous):

1. `main.py` + `config.py` + DB setup (SQLAlchemy models, `db.py`)
2. `POST /upload` + `GET /status` + Celery task skeleton (even if task just sleeps 5 seconds)
3. Wire the Celery task to actually call the extraction pipeline
4. `GET /metrics/{doc_id}` — reads from DB, returns JSON
5. `GET /ratios/{doc_id}` and `GET /risks/{doc_id}`
6. `GET /explain/{doc_id}/{metric_name}` — reads `_provenance.json`
7. `POST /chat` — this is last because it needs FAISS + Claude API

**For the chat endpoint specifically:**
- Load the FAISS index from disk using the `doc_id` to find the file path
- Retrieve top-4 chunks
- Build the grounded prompt (template in SPEC_api.md §3.6)
- Call Claude API: `model="claude-sonnet-4-6"`, `max_tokens=1000`
- Parse the response and extract page citations

**Deliverable:** All 7 endpoints return correct JSON for the Tata Steel test document.

---

### Phase 4 — Build the React Frontend (1 week)
**Goal:** A working UI with 4 screens connected to the live backend.

Build screens in this order:

1. **Upload Screen** — PDF drag-drop, file type validation, polling `/status`
2. **Dashboard Screen** — KPI cards + Recharts line charts (revenue, profit, ratios)
3. **Risk Screen** — 4 risk cards with Low/Medium/High colour badges
4. **Chat Screen** — message input, responses with expandable source citations

The explainability drawer can be added to any screen — clicking a ratio value opens
a drawer with the formula and source page from `/explain`.

**Deliverable:** Full end-to-end flow from PDF upload to chat working in browser.

---

### Phase 5 — Evaluation (3–4 days)
**Goal:** Produce the three quantitative evaluation tables for your report.

**Evaluation 1 — Numerical Accuracy**
- Test set: 3 Indian company PDFs (Tata Steel, Reliance, one NBFC)
- Ground truth: manually verified values from the actual PDFs
- Metric: % of 24 metric-year pairs extracted correctly (within 1% tolerance)
- Target: ≥ 80%

**Evaluation 2 — Citation Accuracy**
- Test set: 10 questions with known answer pages
- Metric: % of RAG responses that cited the correct page number
- Target: ≥ 70%

**Evaluation 3 — Risk Classification F1**
- Test set: 5 companies with manually labeled risk levels (High/Medium/Low)
- Metric: macro F1 across 4 risk categories
- Tool: `sklearn.metrics.f1_score`
- Target: ≥ 0.75

**Evaluation 4 — RAGAS (automated RAG quality)**
```bash
pip install ragas
```
Run RAGAS faithfulness, answer relevance, and context precision on 15 Q&A pairs.

---

## Common Pitfalls to Avoid

| Pitfall | Correct Approach |
|---|---|
| Letting the LLM compute ratios | Ratios are always computed in `numerical_module.py` first. LLM only explains them. |
| Chunking financial statement tables | Tables go to metric extraction. Only narrative text goes to the chunker. |
| Using a single embedding for the whole doc | Embed chunks individually. Never embed the full document as one vector. |
| Hard-coding company names in extraction | All matching must use `CANONICAL_METRICS` patterns only. |
| Using EDGAR as a query-time data source | EDGAR data is offline — baked into formulas and thresholds. No user query ever touches EDGAR at runtime. |
| Confusing `resolved_metrics.csv` with the benchmark | `resolved_metrics.csv` is per-document extraction output. The EDGAR benchmark (`05_computed_metrics.csv`) is a fixed validation reference. |
| Building the frontend before the API is stable | Always build and test the backend endpoint first; wire the frontend to it after. |

---

## Quick Reference — File Outputs per Document

After running the full pipeline on one PDF, you should have these files:

```
extraction_outputs/{doc_id}/
├── {stem}_candidate_metrics.csv   # all raw candidates (debug)
├── {stem}_resolved_metrics.csv    # final extracted metrics (structured store)
├── {stem}_pivot_metrics.csv       # year × metric pivot (input to computation)
├── {stem}_computed_metrics.csv    # ratios + risk labels (output of computation)
├── {stem}_provenance.json         # formula + source_pages for every ratio
├── {stem}_chunks.jsonl            # text chunks for RAG
├── {stem}_faiss.index             # FAISS vector index
├── {stem}_meta.json               # company name, years, page count
├── {stem}_quality_report.json     # missing metrics, accounting issues
└── {stem}_log.txt                 # extraction log
```
