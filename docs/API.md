# FinTool Backend API

**Status: stable, additive-only by convention.** Existing fields are not
removed or renamed without a deliberate, explicit decision — the frontend
(`frontend/src/api/types.ts`) and any other consumer depend on them exactly
as written. New functionality (e.g. a future Risk Identification module)
should be added as **new** endpoints, not by changing these. Response shapes
have grown additively as the system evolved — most recently, the
Trust/Verification fields described below — so this document reflects the
current shapes, not a historical snapshot; keep it in sync when a schema
changes.

Request/response examples below are adapted from live verification runs
against the benchmark corpus, extended with the Trust/Verification fields
each endpoint now returns (field names/semantics confirmed directly against
`backend/models/schemas.py` and `extraction/llm/structural_validator.py`,
not assumed).

Base URL: configurable, no default baked into the backend. Locally this is
typically `http://localhost:8000` (`uvicorn backend.main:app --port 8000`).

Source of truth for types: `backend/models/schemas.py` (Pydantic) and
`frontend/src/api/types.ts` (TypeScript, generated to mirror it 1:1).

---

## Trust / Verification Semantics

Every resolved metric carries a `verification_state`; every computed ratio
and risk classification carries a `verification_states` map keyed by
ratio/risk name. Both are produced by L6 structural validation
(`extraction/llm/structural_validator.py::validate_one()`) and, for ratios/
risks, aggregated by `backend/services/verification_service.py`. The
possible values, exactly as the current implementation produces them:

- **`"VERIFIED"`** — the candidate's row, column, and year were uniquely and
  unambiguously located in the document's own structural table (via PyMuPDF
  `find_tables()`), and the cell there matches the reported value. This is a
  **structural self-consistency check**, not independent fact-checking
  against any source outside the document itself.
- **`"NEEDS_REVIEW"`** — L2–L5 passed, but L6 could not establish sufficient
  structural consistency to reach `VERIFIED`: no structural table was
  available for the page, the value/year combination matched more than one
  row, the matched row's column mapped to a different or ambiguous year, or
  the candidate's label and its value landed on different rows.
  `NEEDS_REVIEW` is not a claim that the value is wrong — only that the
  automated structural check could not confirm it with certainty.
- **`"REJECTED"`** (never appears in an API response) — reserved for one
  narrow case: the candidate's `raw_label` uniquely identifies exactly one
  structural row, that row's column for the declared year is unambiguous,
  and the cell there holds a *different*, real number — an actual
  structural contradiction, not merely an inability to confirm. A rejected
  candidate is removed before persistence, so its absence from `/metrics`
  *is* the signal.
- **`null`** — no structural check was performed for this value at all
  (e.g. the candidate came from the V1 regex-fallback extractor, which
  never runs L6). `null` means "not checked," never "VERIFIED" and never
  "confirmed wrong."

**What `VERIFIED` does NOT mean**: FinTool has not independently proven that
a reported financial figure is factually true. `VERIFIED` means the value
is *structurally self-consistent* with what the source PDF's own table
layout shows for that row/column/year. Do not present `VERIFIED` to a user
as "confirmed correct" or "fact-checked" — present it as "passed FinTool's
structural consistency check."

---

## Error format

Every error response (any non-2xx) has this shape, defined in
`backend/exceptions.py` / `backend/models/schemas.py::ErrorResponse`:

```json
{
  "detail": "Human-readable message",
  "error_code": "MACHINE_READABLE_CODE",
  "doc_id": "57bd82972001"
}
```

`doc_id` is `null` when the error isn't associated with a specific document
(e.g. a malformed request). `error_code` values in use: `INVALID_PDF`,
`FILE_TOO_LARGE`, `DOC_NOT_FOUND`, `DOC_PROCESSING`, `DOC_FAILED`,
`METRIC_NOT_FOUND`, `INTERNAL_ERROR`.

---

## `GET /health`

Liveness check. No auth, no params.

**Response 200:**
```json
{"status": "ok"}
```

---

## `POST /upload`

Accepts a PDF, validates it, persists it, and triggers the extraction
pipeline (synchronously if `SYNC_MODE=true`, otherwise via Celery).

**Request:** `multipart/form-data`, one field:
| Field | Type | Required |
|---|---|---|
| `file` | PDF file | yes |

Validation (`backend/routers/upload.py`), in order:
1. Filename must end in `.pdf`
2. File size ≤ `MAX_FILE_SIZE_BYTES` (default 50 MB)
3. MIME type must be `application/pdf`
4. First 30 pages must contain at least one of: "balance sheet", "profit and loss",
   "cash flow", "annual report", "10-K", "10-Q" (case-insensitive)

`doc_id` is deterministic: `md5(filename + file_size_bytes)[:12]`. Re-uploading
the same file (same name + size) always maps to the same `doc_id` and
re-triggers full reprocessing — there is no "already processed, skip" cache.

**Response 202:**
```json
{
  "doc_id": "57bd82972001",
  "pdf_name": "AR_JIOFIN_2024_2025.pdf",
  "status": "processing",
  "message": "Document accepted. Use GET /status/{doc_id} to track progress."
}
```

Note: in `SYNC_MODE`, the HTTP response only returns *after* the full
pipeline finishes, but the JSON body's `status` field still reads
`"processing"` (set before the blocking call, not re-read after) — poll
`/status` for the real terminal state, don't trust this field's value in
`SYNC_MODE`.

**Error responses:**
- `400 INVALID_PDF` — wrong extension, wrong MIME type, or no financial-report keywords found
- `400 FILE_TOO_LARGE` — exceeds `MAX_FILE_SIZE_BYTES`

---

## `GET /status/{doc_id}`

Poll this until `status` is `"ready"` or `"failed"`.

**Response 200:**
```json
{
  "doc_id": "57bd82972001",
  "status": "ready",
  "progress_message": "Complete.",
  "company_name": "Jio Financial Services Limited",
  "document_years": [2024, 2025],
  "error": null
}
```

`status` is one of `"processing"`, `"ready"`, `"failed"`. `error` is only
populated when `status == "failed"`.

**Error responses:**
- `404 DOC_NOT_FOUND` — no document with this `doc_id`

---

## `GET /metrics/{doc_id}`

Every resolved (LLM-extracted, L2–L6-validated) raw metric row. See "Trust /
Verification Semantics" above for `evidence`/`table_id`/`row_index`/
`col_index`/`verification_state`/`verification_reason`.

**Response 200** (truncated to 2 of 8 rows for brevity):
```json
{
  "doc_id": "57bd82972001",
  "company_name": "Jio Financial Services Limited",
  "document_years": [2024, 2025],
  "metrics": [
    {
      "metric_name": "net_profit",
      "year": 2024,
      "value": 1604.55,
      "unit": "₹ in crore",
      "page_no": 111,
      "raw_label": "Profit for the Year (A)",
      "confidence": "high",
      "statement_type": "income_statement",
      "section_type": "consolidated",
      "evidence": "Profit for the Year (A)\n1,604.55\n1,612.59",
      "table_id": "111_t0",
      "row_index": 12,
      "col_index": 2,
      "verification_state": "VERIFIED",
      "verification_reason": "row 12 uniquely identified by value+year; column matches declared year 2024"
    },
    {
      "metric_name": "total_assets",
      "year": 2025,
      "value": 25095.53,
      "unit": "INR crore",
      "page_no": 83,
      "raw_label": "Total Assets",
      "confidence": "high",
      "statement_type": "balance_sheet",
      "section_type": "standalone",
      "evidence": "Total Assets\n25,095.53\n24,473.83",
      "table_id": null,
      "row_index": null,
      "col_index": null,
      "verification_state": "NEEDS_REVIEW",
      "verification_reason": "no structural table available for this page -- row/column identity unverifiable from structure alone"
    }
  ],
  "quality_report": {
    "candidate_rows": 8,
    "resolved_rows": 8,
    "missing_core_metrics_by_year": {
      "2024": ["revenue", "total_assets", "current_assets", "current_liabilities", "total_equity", "total_liabilities", "operating_cash_flow"],
      "2025": ["current_liabilities", "operating_cash_flow"]
    },
    "issues": []
  }
}
```

`quality_report` is `null` if the quality report file is missing or
unreadable — this is non-fatal, callers should handle its absence.

**Error responses:**
- `404 DOC_NOT_FOUND`
- `400 DOC_PROCESSING` — document exists but hasn't finished processing
- `400 DOC_FAILED` — document processing failed

---

## `GET /ratios/{doc_id}`

14 computed ratios per year, from `numerical_module.compute()`. Any ratio
whose numerator/denominator wasn't resolved is `null` — this is expected,
not an error. Each `RatioItem` also carries `verification_states`, a map
with all 14 ratio names as keys (`backend/services/verification_service.py`
always populates every key, not just computable ratios). A ratio's value is
`"VERIFIED"` only if every one of its numerator/denominator resolved
metrics is itself `VERIFIED`; `"NEEDS_REVIEW"` if any input is
`NEEDS_REVIEW`; `null` if the ratio itself is `null`/not computable, or if
none of its inputs were structurally checked. See "Trust / Verification
Semantics" above.

**Response 200:**
```json
{
  "doc_id": "57bd82972001",
  "company_name": "Jio Financial Services Limited",
  "ratios": [
    {
      "year": 2024,
      "current_ratio": null, "cash_ratio": null, "debt_to_equity": null,
      "debt_ratio": null, "interest_coverage": null, "profit_margin": null,
      "operating_margin": null, "gross_margin": null, "asset_turnover": null,
      "ocf_to_revenue": null, "free_cash_flow": null,
      "yoy_revenue_growth": null, "yoy_profit_growth": null, "total_debt": null,
      "verification_states": {
        "current_ratio": null, "cash_ratio": null, "debt_to_equity": null,
        "debt_ratio": null, "interest_coverage": null, "profit_margin": null,
        "operating_margin": null, "gross_margin": null, "asset_turnover": null,
        "ocf_to_revenue": null, "free_cash_flow": null,
        "yoy_revenue_growth": null, "yoy_profit_growth": null, "total_debt": null
      }
    },
    {
      "year": 2025,
      "current_ratio": null, "cash_ratio": null, "debt_to_equity": null,
      "debt_ratio": 0.0043860400637085566, "interest_coverage": null,
      "profit_margin": 0.7893592962979279, "operating_margin": null,
      "gross_margin": null, "asset_turnover": 0.08140533393795629,
      "ocf_to_revenue": null, "free_cash_flow": null,
      "yoy_revenue_growth": null, "yoy_profit_growth": 0.005010750677760097,
      "total_debt": null,
      "verification_states": {
        "current_ratio": null, "cash_ratio": null, "debt_to_equity": null,
        "debt_ratio": "VERIFIED", "interest_coverage": null,
        "profit_margin": "NEEDS_REVIEW", "operating_margin": null,
        "gross_margin": null, "asset_turnover": "VERIFIED",
        "ocf_to_revenue": null, "free_cash_flow": null,
        "yoy_revenue_growth": null, "yoy_profit_growth": null,
        "total_debt": null
      }
    }
  ]
}
```

**Error responses:** same as `/metrics` (`DOC_NOT_FOUND`, `DOC_PROCESSING`, `DOC_FAILED`).

---

## `GET /risks/{doc_id}`

4 risk classifications (liquidity, debt, profitability, cashflow) plus an
overall rating, per year. **Not currently used by the frontend** — it exists
and works, but no UI consumes it yet (candidate for a future frontend
addition, not new backend work). Each `RiskItem` also carries
`verification_states`, a 5-key map (`liquidity`, `debt`, `profitability`,
`cashflow`, `overall`) mirroring `RatioItem`'s pattern — see "Trust /
Verification Semantics" above.

**Response 200** (one year shown):
```json
{
  "doc_id": "e36efb6efe4f",
  "company_name": "Tata Steel Limited",
  "risks": [
    {
      "year": 2025,
      "liquidity_risk": "High",
      "liquidity_ratio_used": "current_ratio",
      "liquidity_ratio_value": 0.6185959264282922,
      "liquidity_threshold": "< 1.0 = High, 1.0-1.5 = Medium, >= 1.5 = Low",
      "debt_risk": "Unknown",
      "debt_ratio_used": "debt_to_equity",
      "debt_ratio_value": null,
      "debt_threshold": "< 1.0 = Low, 1.0-2.0 = Medium, > 2.0 = High",
      "profitability_risk": "Unknown",
      "profitability_ratio_used": "profit_margin",
      "profitability_ratio_value": null,
      "profitability_threshold": "< 0 = High, 0-5% = Medium, >= 5% = Low",
      "cashflow_risk": "Low",
      "cashflow_value": 23879.91,
      "cashflow_threshold": "< 0 = High, 0-100 Cr = Medium, >= 100 Cr = Low",
      "overall_risk": "High",
      "verification_states": {
        "liquidity": "VERIFIED",
        "debt": null,
        "profitability": null,
        "cashflow": "VERIFIED",
        "overall": "VERIFIED"
      }
    }
  ]
}
```

A risk level of `"Unknown"` means the underlying ratio was `null` (not
resolvable), not that risk is actually unknown/neutral.

**Error responses:** same as `/metrics`.

---

## `POST /chat`

Grounded Q&A over the document: retrieves relevant RAG chunks (FAISS +
sentence-transformers), assembles a prompt with computed metrics + retrieved
text, and calls Gemini (`gemini-flash-latest` via `google-genai`).

**Request body:**
```json
{
  "doc_id": "57bd82972001",
  "question": "What was the net profit in 2025?",
  "conversation_history": []
}
```
`conversation_history` is a list of `{"role": "user"|"assistant", "content": "..."}`
turns — pass prior turns back on follow-up questions for multi-turn context.

**Response 200:**
```json
{
  "answer": "According to Page 59, standalone Profit After Tax (PAT) increased by ₹166.44 crore (representing a 44% YoY increase)... the exact absolute net profit amount for 2025 is not provided. Therefore, this information is not available in the uploaded document.",
  "sources": [
    {
      "page_no": 59,
      "section_type": "other",
      "snippet": "Profit Before Tax (PBT) increased by ₹132.39 crore, reflecting (25% YoY increase). Profit After Tax (PAT) increased by ₹166.44 crore, marking (44% YoY increase)..."
    }
  ],
  "metrics_used": [
    {"metric": "profit_margin", "year": 2025, "value": 0.7893592962979279}
  ],
  "warning": "current_ratio not available for 2024; debt_to_equity not available for 2024; ..."
}
```

Notes:
- `sources` are always narrative/RAG chunks — never financial-statement
  table pages, which are excluded from the RAG index by design (see
  ARCHITECTURE.md).
- `metrics_used` is populated by scanning the answer text for hardcoded
  ratio-name phrases (`backend/routers/chat.py::_MENTION_MAP`). It's a
  best-effort heuristic — if the model phrases a ratio name differently than
  the hardcoded phrase list expects, it won't be detected. Not a bug, a
  known imprecision.
- `warning` lists any of 5 key ratios (`current_ratio`, `debt_to_equity`,
  `profit_margin`, `free_cash_flow`, `operating_margin`) that are `null` for
  any year — surfaces extraction gaps to the user/caller.
- The model is instructed to answer only from the provided context and
  explicitly decline rather than fabricate — confirmed in live testing (see
  PROJECT_STATUS.md).

**Error responses:**
- `404 DOC_NOT_FOUND`
- `400 DOC_PROCESSING` / `400 DOC_FAILED`
- `400 DOC_FAILED` — "RAG index not built for this document" (FAISS index missing)
- `500 INTERNAL_ERROR` — Gemini API failure (invalid/expired key, quota exceeded, etc.) surfaces here with the underlying message in `detail`

---

## `GET /explain/{doc_id}/{metric_name}`

Formula, source-grounded inputs, and risk classification for one **computed
ratio**. Only ratios with a `provenance.json` entry are explainable — raw
resolved metrics (e.g. `net_profit`, `revenue`) are not, since they have no
formula to explain. Explainable ratio names: `current_ratio`, `cash_ratio`,
`debt_to_equity`, `debt_ratio`, `interest_coverage`, `profit_margin`,
`operating_margin`, `gross_margin`, `asset_turnover`, `ocf_to_revenue`,
`free_cash_flow`. (`yoy_revenue_growth`, `yoy_profit_growth`, `total_debt`
are computed but not tracked in provenance, so they 404 here too — expected,
not a bug.)

**Path params:** `doc_id`, `metric_name`
**Query params:** `year` (optional — defaults to the most recent year with provenance for this metric)

**Response 200:**
```json
{
  "metric_name": "profit_margin",
  "formula": "net_profit / revenue",
  "year": 2025,
  "result": 0.7893592962979279,
  "inputs": [
    {
      "metric": "net_profit",
      "value": 1612.59,
      "unit": "₹ in crore",
      "page_no": 111,
      "raw_label": "Profit for the Year (A)",
      "source": "income_statement",
      "evidence": "Profit for the Year (A)\n1,604.55\n1,612.59",
      "verification_state": "VERIFIED",
      "verification_reason": "row 12 uniquely identified by value+year; column matches declared year 2025",
      "table_id": "111_t0",
      "row_index": 12,
      "col_index": 1
    },
    {
      "metric": "revenue",
      "value": 2042.91,
      "unit": "₹ in crore",
      "page_no": 111,
      "raw_label": "Revenue from Operations",
      "source": "income_statement",
      "evidence": null,
      "verification_state": "NEEDS_REVIEW",
      "verification_reason": "no structural table available for this page -- row/column identity unverifiable from structure alone",
      "table_id": null,
      "row_index": null,
      "col_index": null
    }
  ],
  "risk_classification": {
    "risk_type": "profitability",
    "level": "Low",
    "threshold_applied": "< 0 = High, 0-5% = Medium, >= 5% = Low"
  },
  "verification_state": "NEEDS_REVIEW"
}
```
`verification_state` at the top level is the ratio's own worst-case state
across its inputs (`backend/services/verification_service.py`) — `null` if
the ratio itself has no `result` (never computed), independent of whether
any individual input resolved.

`risk_classification` is `null` for ratios outside the 3 risk-mapped metrics
(`current_ratio`, `debt_to_equity`, `profit_margin`) — e.g. `free_cash_flow`
always returns `risk_classification: null`.

**Error responses:**
- `404 DOC_NOT_FOUND`
- `404 METRIC_NOT_FOUND` — metric not in provenance, or no provenance for the requested year
