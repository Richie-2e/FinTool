# FinTool Project Status

Last updated: end of first frontend integration milestone (see `docs/ARCHITECTURE.md` for
system design, `docs/API.md` for the endpoint contract, `docs/FRONTEND_GUIDE.md` for frontend
internals).

## Completed work

- **Extraction V2** — PyMuPDF-based parsing, LLM metric extraction (Qwen2.5 via Ollama) with
  an L2/L4/L5 candidate-validation pipeline, benchmarked and validated across a 4-document
  corpus (JioFin, TataSteel, LICHSGFIN, Reliance). A full failure taxonomy was produced for
  every rejected candidate, a targeted regex/normalization fix (M1–M4) was implemented and
  validated with zero regressions, and a company-name-detection defect was fixed. **Frozen**
  as of this fix — no further changes without an explicit decision to reopen it.
- **Chat provider migration** — moved from Anthropic (dead API key, hard-blocked `/chat`) to
  Google Gemini (`gemini-flash-latest`, free tier), verified with a real end-to-end request
  returning a grounded, cited answer. RAG retrieval, prompt construction, and the `/chat` API
  contract were preserved exactly.
- **`/explain` verification and fix** — audited against its intended contract; found and fixed
  one dead code path (`operating_cash_flow` was listed as risk-classifiable but could never be
  reached, since it has no provenance entry — see `docs/API.md`).
- **Full backend integration verification** — a real document was pushed through the entire
  real-user flow (`/upload` → `/status` → DB → `/metrics` → `/ratios` → `/chat` → `/explain`)
  with cross-endpoint consistency confirmed (identical `doc_id`/values across every response).
- **Frontend** — a React/Vite/TypeScript app covering all 6 workflow endpoints was built,
  verified to build cleanly, and verified end-to-end against the real backend (CORS preflight
  from the real frontend origin, and every component's exact request replayed against the live
  API). See `docs/FRONTEND_GUIDE.md`.

## Current limitations

- **Extraction completeness is partial, by design.** The L2/L4/L5 validator is a safety net —
  it rejects candidates it cannot ground, rather than accepting anything. Across the 4-document
  benchmark corpus, a meaningful fraction of candidates are still rejected. Root-cause
  categories (from the failure taxonomy, not yet all addressed):
  - Statements that don't have a line item the schema expects (largest category — the LLM
    substitutes or mislabels a nearby line). Architectural, deferred to V2.1.
  - LLM-hallucinated note references or wrong-row value extraction — the validator is
    *correctly* rejecting these; not something to "fix" by loosening validation.
  - A handful of empty-evidence extractions — a prompt-engineering problem, deferred.
- **A real, confirmed data-accuracy defect exists in at least one extracted value**:
  TataSteel's `total_equity` for year 2025 was pulled from the wrong column of a multi-year
  comparative balance sheet (the value is real page text, just the wrong year's column). This
  passed L5 value-grounding because the value *does* appear on the cited page — L5 doesn't yet
  disambiguate which column a value belongs to. Documented as a V2.1 backlog item below;
  Extraction V2 remains frozen and this has not been fixed.
- **Only 2 of 4 benchmark documents have been fully processed through the live backend + DB
  path** (JioFin, TataSteel via this session's work; LICHSGFIN was processed in an earlier
  session). Reliance has only ever been run through the standalone extraction pipeline script,
  never through `/upload` → DB.
- **Celery/Redis (the intended production async task queue) is unexercised.** `redis-server`
  isn't installed in this development environment; all verification has used `SYNC_MODE=true`
  (inline pipeline execution within the request). This is fine for local dev/demo but is a real
  gap before genuine production deployment.
- **No automated test suite for the backend or frontend.** Only `extraction/llm/` has unit
  tests (81 tests, covering the candidate validator). Backend routers, DB models, RAG service,
  and the entire frontend have been verified manually/via live request replay in this session,
  not via a repeatable automated suite.
- **`backend/services/{compute_service,explain_service,pipeline_service}.py` are empty (0-byte)
  stub files.** The original spec (`docs/SPEC_api.md`) describes these as a service-layer
  wrapper; in practice the routers call `numerical_module`/`rag_service`/`extraction.pipeline`
  directly and work correctly without them. This is a harmless spec/implementation divergence,
  not a functional gap — worth reconciling the docs or deleting the stubs, but not urgent.

## Known issues

- **Severe cold-start latency on this development machine**, specifically for the first
  RAG-touching request (either `/upload`'s Stage 4, or the first `/chat` call) in a freshly
  started backend process — observed taking 20–50 minutes in this environment. Root cause:
  this project directory sits under `~/Desktop` with iCloud Drive's "Desktop & Documents"
  sync enabled, which adds severe per-file latency for the `transformers` package's hundreds
  of small files. This is an environment/OS configuration issue, not a code defect. Fix
  options (not applied — would require the project owner's decision): disable iCloud Desktop
  sync, or move the project off `~/Desktop`. Subsequent requests within the same warm process
  are fast (seconds).
- **`GET /explain/{doc_id}/{metric}}` 404s for several computed ratios** (`yoy_revenue_growth`,
  `yoy_profit_growth`, `total_debt`) because `numerical_module.compute()` doesn't write
  provenance entries for them. Expected/documented behavior, not a bug — see `docs/API.md`.
- **`/chat`'s `metrics_used` extraction is a best-effort heuristic** — it only detects ratio
  mentions matching a hardcoded phrase list (`backend/routers/chat.py::_MENTION_MAP`); if the
  model phrases a ratio differently, it's silently omitted from `metrics_used` even though the
  answer discusses it. Cosmetic, not a grounding defect (the underlying answer is still
  correctly grounded).
- **`POST /upload`'s response body says `"status": "processing"` even when `SYNC_MODE=true`
  has already fully finished processing by the time the response returns** — the field is set
  before the blocking pipeline call, not re-read after. Callers must poll `/status` for the
  real state.

## Development roadmap

**Near-term (V2.1 — extraction, currently frozen, requires explicit re-opening):**
1. Comparative-table year/column disambiguation in L5 value-grounding (fixes the TataSteel
   `total_equity` defect and likely similar latent cases).
2. Schema-aware handling for statements missing an expected line item (largest remaining
   rejection category).
3. Prompt-level fix for empty-evidence extractions.

**Near-term (backend/frontend):**
4. **Risk Identification module** (upcoming) — per explicit direction, this must be introduced
   as **new endpoint(s)**, not by modifying `/risks` or any other existing response schema.
   `/risks` already exists and is fully functional but has no frontend UI yet — worth
   evaluating whether the new module extends that endpoint's *consumers* (a new frontend
   screen) versus needing genuinely new backend surface area.
5. Process the remaining benchmark document (Reliance) through the live `/upload` → DB path
   for full corpus parity.
6. Add a frontend screen for `/risks` (backend already supports it, unused today).

**Medium-term (production hardening — deliberately deferred until the above and a working demo
are solid):**
7. Stand up Celery/Redis for real async processing (currently `SYNC_MODE` only).
8. Automated test coverage: backend (routers, DB, RAG service) and frontend (component tests).
9. Address the iCloud/Desktop-sync cold-start issue at the environment level.
10. Reconcile or remove the empty `backend/services/*.py` stub files against `docs/SPEC_api.md`.
11. OpenAPI-schema-based codegen for `frontend/src/api/types.ts` instead of hand-maintained
    mirroring, to remove drift risk as the API surface grows.

**Explicitly not planned unless requirements change:**
- Further LLM hallucination-reduction work — the validator is correctly rejecting these; no
  fix is recommended.
- Any change to existing endpoint request/response schemas — the current API contract is
  frozen (see `docs/API.md`); new functionality must arrive as new endpoints.
