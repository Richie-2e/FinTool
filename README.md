# FinTool

**GenAI-powered financial statement analysis with source-grounded trust signals.**

## What FinTool Is

FinTool ingests annual-report-style financial PDFs and produces structured, source-grounded
financial data: extracted metrics, computed ratios, risk classifications, and a grounded
chat interface for asking questions about the document — with every value carrying an
explicit signal for whether it was structurally confirmed against the source or should be
manually reviewed.

## Problem It Solves

Financial statements are unstructured, inconsistently formatted PDFs. LLM-based extraction
is powerful but opaque — a wrong number presented with the same confidence as a right one is
worse than no number at all. FinTool's extraction pipeline treats "correct" and "confidently
presented" as two separate properties: every resolved value is validated against the
document's own structural layout before being marked trustworthy, and values that can't be
confirmed are flagged for review rather than silently guessed.

## Key Capabilities

- **Structured metric extraction** from unstructured PDF financial statements (balance sheet,
  income statement, cash flow), via a local LLM (Qwen2.5) with a six-stage validation chain
- **Deterministic ratio computation and risk classification** — ratios and risk labels are
  always computed in code, never by the LLM
- **Trust & Provenance signals** — every metric, ratio, and risk carries a `VERIFIED` /
  `NEEDS_REVIEW` state, backed by structural evidence (source page, table location) a user
  can inspect
- **Grounded chat** — ask questions about the document; answers are retrieval-grounded over
  the document's own narrative text and cite their sources

## Current Stable Baseline

HEAD `f66443e` · 238/238 automated tests passing · 54.4% reviewable-correct coverage on a
68-slot gold evaluation benchmark · 0 false acceptances.

Full current-state detail, known limitations, and the development roadmap:
[`PROJECT_STATUS.md`](PROJECT_STATUS.md).

## Architecture at a Glance

```
PDF → parse → classify → LLM extraction → validate (L2–L6) → resolved metrics
                                                                    │
                                                    ratios + risk classification
                                                                    │
                                          FastAPI backend ── React/Vite frontend
                                                    │
                                    RAG-grounded chat (narrative text only)
```

Full system design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Technology Stack

- **Backend**: FastAPI, SQLAlchemy/SQLite, Pydantic
- **Extraction**: PyMuPDF, Qwen2.5 (via Ollama), a custom L2–L6 validation chain
- **Chat**: Google Gemini, FAISS + sentence-transformers for retrieval
- **Frontend**: React, Vite, TypeScript — no framework dependencies beyond that

## Getting Started

Full setup instructions: [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) (backend) and
[`frontend/README.md`](frontend/README.md) (frontend).

## Repository Structure

```
backend/            FastAPI application — routers, models, services
extraction/          PDF parsing, LLM extraction, validation (L2–L6)
frontend/            React + Vite + TypeScript UI
benchmark/gold/v0.1/ Frozen gold evaluation set
docs/                Technical documentation
```

## Benchmark / Evaluation

`benchmark/gold/v0.1/` is a frozen, human-annotated gold evaluation set — 68 valid slots
across 5 real annual-report documents, 8 canonical metrics. Current headline result: 54.4%
reviewable-correct coverage, 35.3% fully autonomous (`VERIFIED`) coverage, 0 false
acceptances. Details: [`benchmark/gold/v0.1/README.md`](benchmark/gold/v0.1/README.md) and
[`PROJECT_STATUS.md`](PROJECT_STATUS.md).

## Documentation Index

| Document | Covers |
|---|---|
| [`PROJECT_STATUS.md`](PROJECT_STATUS.md) | Current state, limitations, known issues, roadmap |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System architecture |
| [`docs/API.md`](docs/API.md) | API contract |
| [`docs/FRONTEND_GUIDE.md`](docs/FRONTEND_GUIDE.md) | Frontend architecture |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Setup, running, testing, workflow |
| [`frontend/README.md`](frontend/README.md) | Frontend-specific setup |

## Current Development Status

See [`PROJECT_STATUS.md`](PROJECT_STATUS.md) for the authoritative, up-to-date state of the
project, including known limitations and the active development roadmap.

## Team / Contribution Workflow

```
main
  ├── backend-development    (extraction/backend work)
  └── frontend-development   (frontend/UX work)
```

Both branches originate from `main`; changes return through a reviewed pull request. See
[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for the full workflow.
