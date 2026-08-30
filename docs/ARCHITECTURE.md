# FinTool Architecture

## System overview

```
┌─────────────┐      HTTP (JSON / multipart)      ┌──────────────────────┐
│  Frontend    │ ───────────────────────────────▶ │  FastAPI backend      │
│  (React/Vite)│ ◀─────────────────────────────── │  (backend/)           │
└─────────────┘                                    └──────────┬───────────┘
                                                                │
                     ┌──────────────────────────────────────────┼───────────────────────┐
                     │                                          │                        │
                     ▼                                          ▼                        ▼
          ┌─────────────────────┐                  ┌─────────────────────┐   ┌──────────────────┐
          │ Extraction pipeline  │                  │ numerical_module.py  │   │ SQLite (fintool.db)│
          │ (extraction/)        │─── writes CSV/  │ (ratios + provenance) │   │ documents          │
          │ parse→classify→      │    JSON to →     │                       │   │ resolved_metrics   │
          │ extract→validate→    │  extraction_     │                       │   │ computed_metrics   │
          │ chunk+index           │  outputs/{doc_id}│                       │   │                    │
          └─────────────────────┘                  └──────────┬────────────┘   └──────────┬─────────┘
                                                                 │                          │
                                                                 ▼                          │
                                                        provenance.json ◀────── read by ────┘
                                                        (per-ratio formula +
                                                         numerator/denominator)
                                                                 ▲
                                                                 │ read by GET /explain
                     ┌───────────────────────────────────────────┘
                     │
          ┌──────────┴───────────┐        ┌────────────────────┐
          │ FAISS index +          │◀──────│ Gemini API          │
          │ chunks.jsonl           │  RAG  │ (google-genai SDK)   │
          │ (per doc_id)           │ retrieval + generation       │
          └────────────────────────┘       └────────────────────┘
                     ▲
                     │ read by POST /chat
```

## Components

### 1. Extraction pipeline (`extraction/`) — **frozen**

Runs entirely offline from the API layer's perspective — `backend/tasks/extraction_task.py`
invokes it as one blocking call (or a Celery task). Four stages, orchestrated by
`extraction/pipeline.py::ExtractionPipeline.run()`:

1. **Parse** (`extraction/parser.py`) — PyMuPDF extracts per-page raw text + tables. Assigns
   the document's `doc_id` (or accepts one passed in from the caller, so the API layer and
   the pipeline always agree on the id).
2. **Classify** (`extraction/classifier.py`) — tags each page with a `statement_type`
   (`balance_sheet` / `income_statement` / `cash_flow` / `other`) and `section_type`
   (`standalone` / `consolidated` / `unknown`).
3. **Extract metrics** (`extraction/llm/`) — sends classified statement pages to a local LLM
   (Qwen2.5, via Ollama, `temperature=0`) to extract candidate metric values, then validates
   every candidate through `extraction/llm/candidate_validator.py`'s L2 (canonical-label
   match) → L4 (evidence text grounding) → L5 (value grounding) pipeline, followed by **L6
   structural validation** (see below). Falls back to a regex-based V1 extractor if the LLM
   returns fewer than 3 candidates — the V1 path never runs L6, so its candidates always carry
   `verification_state = null`.
4. **RAG chunking + indexing** (`extraction/text_chunker.py`) — splits *narrative* pages only
   (MD&A, notes, risk factors — explicitly **excludes** financial statement pages, which
   belong to metric extraction, not RAG) into ~800-char chunks, embeds them with
   `sentence-transformers/all-MiniLM-L6-v2`, and builds a FAISS `IndexFlatIP` index.

Outputs land in `extraction_outputs/{doc_id}/`: `*_resolved_metrics.csv`,
`*_pivot_metrics.csv`, `*_validator_rejections.csv`, `*_quality_report.json`, `*_meta.json`,
`*_chunks.jsonl`, `*_faiss.index`.

**The L2-L5 validated-extraction baseline is frozen** — no changes without an explicit
decision to reopen it. **L6 structural validation (below) is a separately-approved, additive
milestone on top of that frozen baseline**, not a reopening of it; see `PROJECT_STATUS.md`
for the current baseline and change-control state.

#### L6 structural validation

`extraction/llm/structural_validator.py` (using structural tables extracted by
`extraction/structural_tables.py`, a PyMuPDF `find_tables()` wrapper scoped to exactly the
pages already selected for the calling statement-type's LLM call — never the whole document)
runs after every L2-L5-accepted candidate, to answer a question L2-L5 cannot: *does this exact
value belong to this exact row and year/column*, as opposed to a different, structurally
similar row on the same page. Two deterministic checks:

1. **Row identity** — the candidate's declared value is searched for across every cell of
   every structural table on the page; if it's found in more than one structurally distinct
   row, or the row implied by `raw_label` disagrees with the row implied by the value, that's
   an identity ambiguity.
2. **Column/year identity** — each matching cell's column is mapped back to a year via the
   table's own header row; if the candidate's declared year doesn't match the column the value
   was actually found in, that's a year mismatch.

State model — **`VERIFIED` / `NEEDS_REVIEW` / `REJECTED`** (no new state introduced beyond
what L2-L5 already used):
- `VERIFIED` — value+year uniquely and unambiguously located in a structural table, cell
  matches.
- `NEEDS_REVIEW` — L2-L5 passed but L6 could not reach certainty (no structural table
  available, ambiguous row/column match, or label/value row disagreement). **This is the
  default landing state when structural confirmation isn't possible** — an L2-L5 pass is
  never silently downgraded to `REJECTED` merely because L6 cannot establish sufficient
  evidence.
- `REJECTED` — reserved for one narrow case: `raw_label` uniquely identifies a row, that row's
  column for the declared year is unambiguous, and the cell holds a *different*, real number —
  an actual structural contradiction, not merely an inability to confirm. Rejected candidates
  are removed before persistence.
- A resolved metric's `verification_state` is `null` ("not checked") when no structural table
  was ever consulted for it at all (the V1 fallback path).

Full field-level API contract: `docs/API.md`'s "Trust / Verification Semantics" section.

### 2. Ratio computation (`numerical_module.py`)

Takes the pipeline's `resolved_df`/`pivot_df`, computes 14 ratios per year (current ratio,
debt-to-equity, profit margin, etc.), classifies 4 risk categories against hardcoded
thresholds, and writes two more files per document: `{doc_id}_computed_metrics.csv` and
`{doc_id}_provenance.json` — the latter records, per ratio per year, the formula string, the
numerator/denominator metric names, their resolved values, and the final result. This is the
file `GET /explain` reads.

### 3. Backend API (`backend/`)

FastAPI app (`backend/main.py`), 4 routers:

| Router | Endpoints | Backed by |
|---|---|---|
| `upload.py` | `POST /upload`, `GET /status/{doc_id}` | Validates + persists the PDF, triggers the pipeline, tracks `Document` row status |
| `metrics.py` | `GET /metrics`, `GET /ratios`, `GET /risks` | Reads `resolved_metrics`/`computed_metrics` DB tables + `quality_report.json`; `ratios`/`risks` additionally call `backend/services/verification_service.py` to attach `verification_states` |
| `chat.py` | `POST /chat` | `rag_service.py` (FAISS retrieval + Gemini call); also calls `verification_service.py` so the prompt can caveat `NEEDS_REVIEW` figures |
| `explain.py` | `GET /explain/{doc_id}/{metric}` | Reads `provenance.json` + `resolved_metrics` DB rows; calls `verification_service.py` for the ratio's own worst-case `verification_state` |

`backend/services/verification_service.py` reads `resolved_metrics.verification_state` (set by
L6) plus each document's `provenance.json` (numerator/denominator metric names per ratio/year,
already written by `numerical_module._build_provenance()`) and derives a worst-case
`verification_state` per ratio/risk — `NEEDS_REVIEW` if any input is `NEEDS_REVIEW`, `VERIFIED`
only if every input is `VERIFIED`, `null` if the ratio/risk itself isn't computable or none of
its inputs were structurally checked. It does not modify `numerical_module.py` or any ratio
formula.

`backend/services/{compute_service,explain_service,pipeline_service}.py` are present but
currently empty (0 bytes) — the routers call `numerical_module`/`rag_service`/
`extraction.pipeline` directly instead. This is a harmless divergence from an earlier
service-layer design, not a functional gap; these three files are not active runtime code.

Persistence: SQLite (`fintool.db`), 3 tables (`documents`, `resolved_metrics`,
`computed_metrics`) — see `backend/models/db.py`. `resolved_metrics` carries, per row, the
original extraction fields (`metric_name`, `value`, `unit`, `year`, `page_no`, `raw_label`,
`statement_type`, `section_type`, `confidence`) plus the provenance/verification columns
`evidence`, `table_id`, `row_index`, `col_index`, `verification_state`, `verification_reason`
(all nullable; populated by L6 when a structural table was available, `null` otherwise).
`SYNC_MODE=true` runs the pipeline inline within the request instead of via Celery/Redis (used
for local dev; Celery/Redis is the intended production path but is currently unexercised in
this environment).

Full endpoint contract: `docs/API.md`.

### 4. Frontend (`frontend/`)

React + Vite + TypeScript, talks to the backend exclusively over HTTP through
`src/api/client.ts`. See `docs/FRONTEND_GUIDE.md` for its internal architecture.

## Request flow: full document lifecycle

```
1. Browser → POST /upload (multipart PDF)
     backend/routers/upload.py validates → inserts Document(status="processing")
       → run_extraction_pipeline(doc_id, path)   [inline if SYNC_MODE, else Celery task]
           → ExtractionPipeline.run()             [4 stages, see above]
           → numerical_module.compute()           [ratios + provenance.json]
           → bulk-insert ResolvedMetric + ComputedMetric rows
           → Document.status = "ready"
     ← 202 {doc_id, pdf_name, status: "processing", message}

2. Browser polls GET /status/{doc_id} every 3s
     ← 200 {status: "ready", company_name, document_years, ...}

3. Browser → GET /metrics/{doc_id}
     reads ResolvedMetric rows from DB + quality_report.json from disk
     ← 200 {metrics: [...], quality_report: {...}}

4. Browser → GET /ratios/{doc_id}
     reads ComputedMetric rows from DB
     ← 200 {ratios: [...]}

5. Browser → GET /explain/{doc_id}/{ratio}?year=
     reads provenance.json (indexed by ratio_name→year) + cross-references
     ResolvedMetric rows in DB for the numerator/denominator's page_no/raw_label
     ← 200 {formula, result, inputs: [...], risk_classification}

6. Browser → POST /chat {doc_id, question, conversation_history}
     rag_service.build_rag_context(): loads FAISS index + chunks.jsonl for doc_id,
       embeds the question, retrieves top-4 chunks
     rag_service.build_grounded_prompt(): assembles computed ratios (from DB) +
       retrieved chunk text + the question into one prompt
     rag_service.call_llm(): sends the prompt to Gemini (gemini-flash-latest)
     chat.py post-processes: scans the answer for ratio-name mentions
       (metrics_used), flags missing key ratios (warning)
     ← 200 {answer, sources: [...], metrics_used: [...], warning}
```

## Key architectural decisions (and why)

- **Ratios are always computed in `numerical_module.py`, never by the LLM.** The chat prompt
  includes computed ratios as "authoritative" numbers the model must not contradict — the LLM
  only explains/discusses them, never calculates them itself.
- **Financial statement pages never enter the RAG index.** Tables are extracted
  deterministically by the LLM-extraction + validator pipeline; only narrative text (MD&A,
  notes, risk factors) is chunked and embedded. This is why `/chat`'s `sources` always cite
  narrative pages, never balance-sheet/income-statement pages.
- **`doc_id` is deterministic** (`md5(filename+size)[:12]`), not random — re-uploading the
  same file always resolves to the same id and overwrites prior results, rather than
  accumulating duplicate documents.
- **The validator (L2–L6) is a safety net, not a completeness guarantee.** It rejects
  candidates it cannot ground in source text/value or confirm structurally — a rejected
  candidate means "not shown," not "wrong." This is why `/metrics` responses often have fewer
  rows than statements actually contain values for. See `docs/API.md`'s "Trust / Verification
  Semantics" for what `VERIFIED`/`NEEDS_REVIEW`/`REJECTED`/`null` each mean.
- **Provenance is ratio-scoped, not metric-scoped.** Only the 11 computed ratios have
  `provenance.json` entries; raw resolved metrics do not. This is why `/explain` 404s for raw
  metric names — by design, not a bug (see `docs/API.md`).
