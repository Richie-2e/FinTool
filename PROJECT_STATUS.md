# FinTool Project Status

Last updated: 2026-08-29, at commit `f66443e3ac9fff29b0b910697d7243b15278e2b1`.
See `docs/ARCHITECTURE.md` for system design, `docs/API.md` for the endpoint contract,
`docs/FRONTEND_GUIDE.md` for frontend internals, and `docs/DEVELOPMENT.md` for setup and
day-to-day development instructions.

This document is the authoritative, current-state description of the project. If another
document appears to disagree with a number or claim here, this file is correct.

## Completed work

- **Extraction pipeline (L2–L6)** — PyMuPDF-based parsing, LLM metric extraction
  (Qwen2.5 via Ollama) validated through a six-stage chain: L2 (canonical-label matching),
  L4 (evidence-text grounding), L5 (value grounding), and **L6 (structural validation)** —
  a deterministic check that cross-references each candidate's row, column, and year against
  the source PDF's own structural tables (via PyMuPDF `find_tables()`), producing a
  `VERIFIED` / `NEEDS_REVIEW` / `REJECTED` trust state for every resolved value. Several
  targeted extraction-correctness fixes are included: trailing footnote-marker label
  matching, reliable multi-year comparative-row extraction, and a widened evidence-capture
  window (350 characters).
- **Trust & Provenance UX** — every resolved metric, computed ratio, and risk classification
  now exposes its verification state, supporting evidence, and structural location
  (`table_id`/`row_index`/`col_index`) end-to-end, from extraction through the API
  (`GET /metrics`, `GET /ratios`, `GET /risks`, `GET /explain`) to the frontend
  (`VerificationBadge`, expandable evidence detail on `MetricsTable`, `RatiosTable`, and
  `ExplainPanel`).
- **Chat provider** — Gemini (`gemini-flash-latest`), RAG-grounded over narrative document
  text (financial-statement tables are explicitly excluded from the RAG index), with
  `NEEDS_REVIEW` figures caveated in the model's own answers rather than presented as
  confidently established.
- **Gold evaluation benchmark (v0.1)** — a frozen, human-annotated, hash-verified ground
  truth (`benchmark/gold/v0.1/`) covering 5 real annual-report documents, 8 canonical
  metrics, up to 2 years each. Established and independently reproduced with a fresh,
  live end-to-end pipeline run against this exact commit — see "Current benchmark
  baseline" below.
- **Documentation synchronization** — `docs/API.md`, `docs/ARCHITECTURE.md`, and
  `docs/FRONTEND_GUIDE.md` are kept in sync with the implementation described above.

## Current benchmark baseline

Measured by running the current pipeline live against all 5 gold-benchmark source PDFs and
scoring the result against `benchmark/gold/v0.1/gold_records.jsonl`, at commit
`f66443e3ac9fff29b0b910697d7243b15278e2b1`:

| Metric | Value |
|---|---:|
| Gold-valid slots | 68 |
| VERIFIED-correct | 24 |
| NEEDS_REVIEW-correct | 13 |
| NEEDS_REVIEW-incorrect | 2 |
| MISSING | 29 |
| **Reviewable-correct coverage** | **54.4% (37/68)** |
| VERIFIED coverage | 35.3% (24/68) |
| False acceptances | **0** |

**Reviewable-correct coverage** (54.4%) is the correct headline figure: the fraction of gold
slots where the pipeline's answer is either autonomously trusted (`VERIFIED`) or correct and
flagged for review (`NEEDS_REVIEW`). **False acceptances remain 0** — no gold slot has ever
been marked `VERIFIED` while actually wrong, across every measurement of this baseline.

Two gold slots are currently known-incorrect (flagged `NEEDS_REVIEW`, not silently wrong):
- **OFSS — `operating_cash_flow` — 2025**
- **LICHSGFIN — `total_equity` — 2025**

## Current limitations

- **Extraction completeness is partial, by design.** The L2–L6 validator is a safety net —
  it declines to resolve a value it cannot ground and structurally confirm, rather than
  guessing. 29 of 68 gold slots are currently unresolved (`MISSING`); the largest known
  cause is candidate-generation omission (the LLM never proposes a value for that slot at
  all), not validator over-rejection.
- **The two known-incorrect slots above are not yet fixed.** Both are correctly flagged
  `NEEDS_REVIEW`, not presented as trustworthy — no user-facing false-acceptance risk, but a
  known accuracy gap.
- **No automated frontend test suite.** `backend/` and `extraction/` have 238 automated unit
  tests; frontend verification is currently manual, plus type-checking (`tsc -b`), a
  production build, and linting (`oxlint`) — no Vitest/Testing Library setup exists yet.
- **Three backend service-layer files are present but empty**: `backend/services/{compute_service,explain_service,pipeline_service}.py`
  are 0 bytes. Routers call `numerical_module`, `rag_service`, and `extraction.pipeline`
  directly instead. This is a harmless divergence from an earlier service-layer design, not
  a functional gap.
- **Celery/Redis (the intended production async task queue) is unexercised.** All current
  development and testing uses `SYNC_MODE=true` (the pipeline runs inline within the
  request).
- **A deterministic (non-LLM) extraction approach has been feasibility-tested but is not
  integrated into production.** An isolated research pilot found it could correctly resolve
  every gold slot it was eligible for, with zero false acceptances — but it exists only as a
  standalone experiment today, not as a code path the live pipeline uses.

## Known issues

- **`GET /explain/{doc_id}/{metric}` 404s for a few computed ratios**
  (`yoy_revenue_growth`, `yoy_profit_growth`, `total_debt`) because `numerical_module.compute()`
  does not write provenance entries for them. Expected, documented behavior — see `docs/API.md`.
- **`POST /chat`'s `metrics_used` field is a best-effort heuristic** — it detects ratio
  mentions in the model's answer via a hardcoded phrase list
  (`backend/routers/chat.py::_MENTION_MAP`); a differently-phrased mention can be missed.
  Cosmetic — the underlying answer remains correctly grounded regardless.
- **`POST /upload`'s response body reports `"status": "processing"` even when
  `SYNC_MODE=true` has already finished processing by the time the response returns** — the
  field is set before the blocking pipeline call, not re-read after. Callers should poll
  `GET /status/{doc_id}` for the authoritative state.

## Development roadmap

**Near-term:**
1. Establish this baseline on GitHub as the team's shared `main`, with separate backend and
   frontend development branches (see `docs/DEVELOPMENT.md`).
2. Shadow-mode integration of the deterministic-extraction pilot — run it alongside the live
   LLM path in a read-only, non-serving mode to validate it against real uploads before any
   production-facing change.
3. Reduce the 29 `MISSING` gold slots, starting with the largest known failure cluster
   (candidate-generation omission).

**Backend/frontend:**
4. Frontend consumption of the existing `GET /risks/{doc_id}` endpoint (implemented, not yet
   wired into any UI).
5. Automated frontend test coverage.

**Medium-term:**
6. Stand up Celery/Redis for genuine asynchronous processing.
7. Reconcile or remove the three empty `backend/services/*.py` stub files.
8. OpenAPI-schema-based code generation for `frontend/src/api/types.ts`, to remove the
   manual-sync risk between it and `backend/models/schemas.py`.

## Explicitly not planned

- **Further LLM hallucination-reduction prompt tuning** — the L2–L6 validator already
  correctly rejects or flags these cases; no change is planned without new evidence that a
  specific fix is safe.
- **Changes to existing endpoint request/response shapes** — the API contract grows
  additively only (see `docs/API.md`); new functionality arrives as new endpoints.
