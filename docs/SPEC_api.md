# SPEC_api.md
## FinTool — Backend API Specification
**Version:** 1.0 | **Status:** Draft | **Owners:** Group 8

---

## 0. Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI (Python 3.11+) |
| Task queue | Celery + Redis (async PDF pipeline) |
| Database | SQLite (dev) → PostgreSQL (prod) |
| ORM | SQLAlchemy 2.0 |
| Validation | Pydantic v2 |
| LLM | Anthropic Claude API (`claude-sonnet-4-6`) | 
| Vector store | FAISS (file-based, loaded on demand per doc_id) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` |

---

## 1. Project Structure

```
backend/
├── main.py              # FastAPI app, CORS, router registration
├── routers/
│   ├── upload.py        # POST /upload
│   ├── metrics.py       # GET /metrics, GET /ratios, GET /risks
│   ├── chat.py          # POST /chat
│   └── explain.py       # GET /explain
├── services/
│   ├── pipeline_service.py   # triggers extraction pipeline as Celery task
│   ├── compute_service.py    # wraps numerical_module.compute()
│   ├── rag_service.py        # RAG chain: FAISS retrieval + LLM prompt
│   └── explain_service.py    # reads provenance.json, formats explanation
├── models/
│   ├── db.py            # SQLAlchemy table definitions
│   └── schemas.py       # Pydantic request/response models
├── tasks/
│   └── extraction_task.py    # Celery task wrapping the full pipeline
└── config.py            # env vars, paths, model names
```

---

## 2. Database Schema

### Table: `documents`
```sql
CREATE TABLE documents (
    doc_id          TEXT PRIMARY KEY,          -- md5 hash from extraction pipeline
    pdf_name        TEXT NOT NULL,
    company_name    TEXT,
    page_count      INTEGER,
    document_years  TEXT,                      -- JSON array e.g. [2023, 2024]
    upload_time     DATETIME DEFAULT NOW(),
    status          TEXT DEFAULT 'processing', -- 'processing' | 'ready' | 'failed'
    error_message   TEXT,
    output_dir      TEXT                       -- path to extraction_outputs/{doc_id}/
);
```

### Table: `resolved_metrics`
```sql
CREATE TABLE resolved_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT REFERENCES documents(doc_id),
    metric_name     TEXT NOT NULL,
    value           REAL,
    unit            TEXT,
    year            INTEGER,
    page_no         INTEGER,
    raw_label       TEXT,
    statement_type  TEXT,
    section_type    TEXT,
    confidence      TEXT
);
```

### Table: `computed_metrics`
```sql
CREATE TABLE computed_metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id              TEXT REFERENCES documents(doc_id),
    year                INTEGER,
    -- ratios (all REAL, nullable)
    current_ratio       REAL,
    cash_ratio          REAL,
    debt_to_equity      REAL,
    debt_ratio          REAL,
    interest_coverage   REAL,
    profit_margin       REAL,
    operating_margin    REAL,
    gross_margin        REAL,
    asset_turnover      REAL,
    ocf_to_revenue      REAL,
    free_cash_flow      REAL,
    yoy_revenue_growth  REAL,
    yoy_profit_growth   REAL,
    -- risk labels
    liquidity_risk      TEXT,
    debt_risk           TEXT,
    profitability_risk  TEXT,
    cashflow_risk       TEXT,
    overall_risk        TEXT
);
```

---

## 3. API Endpoints

### 3.1 `POST /upload`

**Purpose:** Accept a PDF, start the extraction pipeline asynchronously.

**Request:**
```
Content-Type: multipart/form-data
Field: file  (PDF file, max 50MB)
```

**Validation (return 400 if fails):**
- File extension must be `.pdf`
- File size must be ≤ 50MB
- MIME type must be `application/pdf`
- First 30 pages must contain at least one of: "balance sheet", "profit and loss", "cash flow", "annual report", "10-K", "10-Q"
-In some cases the actual annual report of a company downloaded from NSE OR BSE websites starts after 10 to 20 initial pages.


**Response 202 (Accepted):**
```json
{
  "doc_id": "91c97158cf24",
  "pdf_name": "AR_TATASTEEL_2024_2025.pdf",
  "status": "processing",
  "message": "Document accepted. Use GET /status/{doc_id} to track progress."
}
```

**What happens after acceptance:**
1. PDF saved to `uploads/{doc_id}.pdf`
2. Celery task `run_extraction_pipeline.delay(doc_id, pdf_path)` enqueued
3. `documents` table row inserted with `status='processing'`

---

### 3.2 `GET /status/{doc_id}`

**Purpose:** Poll pipeline progress (use this until status = 'ready').

**Response 200:**
```json
{
  "doc_id": "91c97158cf24",
  "status": "processing",       // "processing" | "ready" | "failed"
  "progress_message": "Stage 2/4: Classifying statement pages...",
  "company_name": null,         // populated when ready
  "document_years": null,       // populated when ready
  "error": null                 // populated only if failed
}
```

**Frontend polling strategy:** Poll every 3 seconds until status != 'processing'.

---

### 3.3 `GET /metrics/{doc_id}`

**Purpose:** Return the resolved metrics store — all extracted base numbers.

**Response 200:**
```json
{
  "doc_id": "91c97158cf24",
  "company_name": "Tata Steel Limited",
  "document_years": [2024, 2025],
  "metrics": [
    {
      "metric_name": "revenue",
      "year": 2024,
      "value": 189483.12,
      "unit": "crore",
      "page_no": 187,
      "raw_label": "Revenue from Operations",
      "confidence": "high",
      "statement_type": "income_statement",
      "section_type": "standalone"
    }
    // ... one entry per metric+year
  ],
  "quality_report": {
    "candidate_rows": 120,
    "resolved_rows": 34,
    "missing_core_metrics_by_year": {
      "2024": [],
      "2025": ["interest_expense"]
    },
    "issues": []
  }
}
```

---

### 3.4 `GET /ratios/{doc_id}`

**Purpose:** Return all computed financial ratios for all years.

**Response 200:**
```json
{
  "doc_id": "91c97158cf24",
  "company_name": "Tata Steel Limited",
  "ratios": [
    {
      "year": 2024,
      "current_ratio": 1.23,
      "cash_ratio": 0.31,
      "debt_to_equity": 1.87,
      "debt_ratio": 0.62,
      "interest_coverage": 3.4,
      "profit_margin": 0.042,
      "operating_margin": 0.091,
      "gross_margin": 0.19,
      "asset_turnover": 0.71,
      "ocf_to_revenue": 0.11,
      "free_cash_flow": 8420.5,
      "yoy_revenue_growth": 0.032,
      "yoy_profit_growth": -0.12,
      "total_debt": 78234.0
    }
    // ... one entry per year
  ]
}
```

---

### 3.5 `GET /risks/{doc_id}`

**Purpose:** Return risk classifications with explanations.

**Response 200:**
```json
{
  "doc_id": "91c97158cf24",
  "company_name": "Tata Steel Limited",
  "risks": [
    {
      "year": 2024,
      "liquidity_risk": "Medium",
      "liquidity_ratio_used": "current_ratio",
      "liquidity_ratio_value": 1.23,
      "liquidity_threshold": "< 1.0 = High, 1.0-1.5 = Medium, >= 1.5 = Low",

      "debt_risk": "High",
      "debt_ratio_used": "debt_to_equity",
      "debt_ratio_value": 1.87,
      "debt_threshold": "< 1.0 = Low, 1.0-2.0 = Medium, > 2.0 = High",

      "profitability_risk": "Medium",
      "profitability_ratio_used": "profit_margin",
      "profitability_ratio_value": 0.042,
      "profitability_threshold": "< 0 = High, 0-5% = Medium, >= 5% = Low",

      "cashflow_risk": "Low",
      "cashflow_value": 18200.0,
      "cashflow_threshold": "< 0 = High, 0-100 Cr = Medium, >= 100 Cr = Low",

      "overall_risk": "High"
    }
  ]
}
```

---

### 3.6 `POST /chat`

**Purpose:** RAG-powered Q&A grounded in the uploaded document.

**Request:**
```json
{
  "doc_id": "91c97158cf24",
  "question": "What is the liquidity position of the company and how has it changed?",
  "conversation_history": []   // optional, list of {role, content} for multi-turn
}
```

**How the RAG chain works (internal, not exposed in response):**
1. Embed the question using `all-MiniLM-L6-v2`
2. Retrieve top-4 text chunks from the doc's FAISS index (cosine similarity)
3. Fetch the computed metrics for the most recent year from the database
4. Build a **grounded prompt** (see below)
5. Call Claude API with the prompt . 
6. Extract the response and the page citations

**Grounded Prompt Template (internal):**
```
You are a financial analyst assistant. You must only use the information provided below.
Do not use any outside knowledge. Do not invent numbers.

COMPUTED METRICS (authoritative — do not contradict these):
  Current Ratio (2024): 1.23  [page 187]
  Current Ratio (2025): 1.31  [page 203]
  Liquidity Risk (2024): Medium
  Liquidity Risk (2025): Medium

RETRIEVED DOCUMENT SECTIONS:
  [Page 34] "The company maintained adequate liquidity buffers throughout FY25..."
  [Page 61] "Current assets grew by 12% driven by higher trade receivables..."

USER QUESTION: What is the liquidity position of the company and how has it changed?

INSTRUCTIONS:
- Answer in 3-5 sentences.
- Reference specific page numbers when citing document text.
- State the current ratio values explicitly.
- Do not say "I think" or "I believe". State facts from the sources above.
- If the answer cannot be found in the sources above, say "This information is not available in the uploaded document."
```

**Response 200:**
```json
{
  "answer": "The company maintained a medium liquidity position in both FY24 and FY25, with the current ratio improving slightly from 1.23 to 1.31. According to page 34, the company maintained adequate liquidity buffers throughout FY25. The improvement in current ratio was primarily driven by a 12% growth in current assets (page 61), even as current liabilities also increased. This suggests a stable but not yet comfortable liquidity position.",
  "sources": [
    { "page_no": 34, "section_type": "mda", "snippet": "maintained adequate liquidity buffers..." },
    { "page_no": 61, "section_type": "mda", "snippet": "Current assets grew by 12%..." }
  ],
  "metrics_used": [
    { "metric": "current_ratio", "year": 2024, "value": 1.23 },
    { "metric": "current_ratio", "year": 2025, "value": 1.31 }
  ],
  "warning": null    // or "operating_cash_flow not available for 2024" if metric missing
}
```

**Error 404:** `{ "detail": "doc_id not found or document not ready" }`

---

### 3.7 `GET /explain/{doc_id}/{metric_name}`

**Purpose:** Return the full provenance for a single metric — formula, source values, page number.

**Path params:** `doc_id`, `metric_name` (e.g. `current_ratio`)
**Query param:** `year` (optional, defaults to most recent)

**Response 200:**
```json
{
  "metric_name": "current_ratio",
  "year": 2024,
  "formula": "current_assets / current_liabilities",
  "result": 1.23,
  "inputs": [
    {
      "metric": "current_assets",
      "value": 45230.0,
      "unit": "crore",
      "page_no": 187,
      "raw_label": "Total Current Assets",
      "source": "Balance Sheet"
    },
    {
      "metric": "current_liabilities",
      "value": 36780.0,
      "unit": "crore",
      "page_no": 187,
      "raw_label": "Total Current Liabilities",
      "source": "Balance Sheet"
    }
  ],
  "risk_classification": {
    "risk_type": "liquidity",
    "level": "Medium",
    "threshold_applied": "1.0 <= current_ratio < 1.5 → Medium"
  }
}
```

---

## 4. Error Response Format (all endpoints)

```json
{
  "detail": "Human-readable error message",
  "error_code": "DOC_NOT_FOUND",   // machine-readable code
  "doc_id": "91c97158cf24"         // if applicable
}
```

Standard error codes:
- `DOC_NOT_FOUND` — doc_id does not exist
- `DOC_PROCESSING` — pipeline not yet complete
- `DOC_FAILED` — extraction pipeline failed (check error_message in /status)
- `METRIC_NOT_FOUND` — requested metric has no value for the given year
- `INVALID_PDF` — uploaded file is not a supported financial document
- `FILE_TOO_LARGE` — PDF exceeds 50MB

---

## 5. CORS Configuration

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],   # React dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 6. Celery Task: `run_extraction_pipeline`

```python
# tasks/extraction_task.py

@celery_app.task(bind=True, max_retries=1)
def run_extraction_pipeline(self, doc_id: str, pdf_path: str):
    """
    Runs all 4 stages of the extraction pipeline + computation module.
    Updates documents.status at each stage.
    On success: status = 'ready'
    On failure: status = 'failed', error_message = str(exc)
    """
    try:
        update_status(doc_id, "processing", "Stage 1/4: Parsing document...")
        parse_result = parse_pdf(pdf_path)

        update_status(doc_id, "processing", "Stage 2/4: Classifying pages...")
        page_classes = classify_statement_pages(parse_result.pages_raw_text)

        update_status(doc_id, "processing", "Stage 3/4: Extracting metrics...")
        candidates = extract_metrics(parse_result, page_classes)
        resolved_df, quality = build_resolved_metrics_df(candidates)
        pivot_df = add_basic_ratios(pivot_metrics(resolved_df))

        update_status(doc_id, "processing", "Stage 4/4: Building RAG index...")
        chunks = chunk_document(parse_result.pages_raw_text, page_classes)
        build_faiss_index(chunks, output_dir)

        # Computation module
        computed = compute(doc_id, pivot_df, resolved_df)

        # Persist to DB
        save_to_db(doc_id, parse_result.doc_meta, resolved_df, computed)
        update_status(doc_id, "ready")

    except Exception as exc:
        update_status(doc_id, "failed", error_message=str(exc))
        raise
```

---

## 7. Acceptance Tests

| Test | Pass Criterion |
|---|---|
| Upload valid PDF | Returns 202, doc_id is a 12-char hex string |
| Upload non-PDF | Returns 400, `INVALID_PDF` error code |
| Upload PDF > 50MB | Returns 400, `FILE_TOO_LARGE` error code |
| Poll /status during processing | Returns `"processing"` with progress message |
| Poll /status after completion | Returns `"ready"`, company_name and years populated |
| GET /metrics before ready | Returns 400 with `DOC_PROCESSING` |
| GET /metrics after ready | Returns all resolved metrics with correct schema |
| GET /ratios | `current_ratio` matches manual calculation within 0.01 |
| GET /risks | All four risk fields present, values in {High, Medium, Low, Unknown} |
| POST /chat | Response has `answer`, `sources` (list with page_no), `metrics_used` |
| POST /chat — metric absent | `warning` field is populated, answer handles gracefully |
| GET /explain/current_ratio | `inputs` array has exactly 2 entries: current_assets, current_liabilities |
| GET /explain — unknown metric | Returns 404 with `METRIC_NOT_FOUND` |
