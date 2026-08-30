# Development Guide

Setup, running, testing, and workflow instructions for developers working on FinTool. For
what the system does and how it's built, see `docs/ARCHITECTURE.md`, `docs/API.md`, and
`docs/FRONTEND_GUIDE.md` — this document is procedural, not explanatory.

## 1. Prerequisites

- **Python 3.9.x**
- **Node.js 20+** and npm (developed against Node 25.2.1 / npm 11.6.2; any reasonably
  current Node/npm should work)
- **[Ollama](https://ollama.com/)**, running locally, serving the `qwen2.5:3b` model
- **A Gemini API key** (for the chat endpoint — free tier is sufficient)

## 2. Backend Environment Setup

From the repository root:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3. Environment Variables

```bash
cp .env.example .env
```

Edit `.env` and set at minimum your Gemini API key. The default `DATABASE_URL`
(`sqlite:///./fintool.db`) and `SYNC_MODE` settings work out of the box for local
development.

## 4. Frontend Setup

See `frontend/README.md` for the full guide. Short version:

```bash
cd frontend
npm install
cp .env.example .env   # set VITE_API_BASE_URL, e.g. http://localhost:8000
```

## 5. Ollama / Model Requirements

```bash
ollama pull qwen2.5:3b
```

Ollama must be running (`ollama serve`, or the Ollama desktop app) before you upload a
document — extraction calls it directly on `localhost:11434`. Verify it's ready with:

```bash
curl -s http://localhost:11434/api/tags
```

## 6. Running the Backend

```bash
source venv/bin/activate
SYNC_MODE=true uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

`SYNC_MODE=true` runs the extraction pipeline inline within the upload request — the
supported local-development mode (see "Current limitations" in `PROJECT_STATUS.md`
regarding the intended-but-unexercised Celery/Redis path).

## 7. Running the Frontend

```bash
cd frontend
npm run dev
```

Opens at `http://localhost:5173` by default; `backend/main.py`'s CORS configuration already
allow-lists this origin.

## 8. Running Tests

```bash
./venv/bin/python -m pytest -q backend/ extraction/ test_numerical_module.py
```

Expected result: **238 passed, 0 failed, 0 skipped, 0 errors**.

Note: a bare `pytest -q` run from the repository root will fail to collect — an unrelated
research script under `scratchpad/` matches pytest's default `*_test.py` discovery pattern.
Always scope the test invocation to `backend/ extraction/ test_numerical_module.py` as shown
above.

## 9. Running the Frozen Gold Benchmark

`benchmark/gold/v0.1/` contains the frozen, human-annotated gold evaluation set — 68 valid
gold slots across 5 real annual-report documents (`gold_records.jsonl`, `manifest.json`,
`README.md`). It is versioned and hash-verified; do not modify it without a deliberate,
separately-reviewed decision.

The current headline result, last measured live against commit `f66443e`, is recorded in
`PROJECT_STATUS.md`'s "Current benchmark baseline" section (54.4% reviewable-correct
coverage, 0 false acceptances). **The scoring/runner tooling used to produce that result is
currently local research tooling, not yet part of this repository** — reproducing it end-to-
end requires the source PDFs for the 5 gold documents and a small pipeline-invocation script
that isn't committed yet. Promoting that tooling into the repository (e.g. under a
`scripts/` or `benchmark/` directory) is a known follow-up, not yet done — see the
development roadmap in `PROJECT_STATUS.md`.

## 10. Repository Structure / Where Things Live

```
backend/            FastAPI application — routers, models, services, background tasks
extraction/          PDF parsing, LLM metric extraction, L2–L6 validation
frontend/            React + Vite + TypeScript UI
benchmark/gold/v0.1/ Frozen gold evaluation set
docs/                Technical documentation (this file, ARCHITECTURE, API, FRONTEND_GUIDE)
requirements.txt     Backend Python dependencies
.env.example         Backend environment variable template
```

## 11. Development Workflow

Standard branch → commit → test → pull request flow. Run the test suite (§8) before opening
a PR; there is no CI configured yet, so this is currently a manual step.

## 12. Intended Branch Workflow

```
main
  ├── backend-development    (extraction/backend work)
  └── frontend-development   (frontend/UX work)
```

Both branch from `main`. Changes return to `main` through a pull request, reviewed by the
other developer. Keep backend/extraction and frontend/UX changes on separate branches so
neither person's in-flight work blocks the other's.
