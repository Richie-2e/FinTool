# FinTool Frontend

A minimal React + Vite + TypeScript UI for the FinTool backend. It exercises
exactly six backend endpoints — upload, status, metrics, ratios, chat,
explain — and nothing else. This is a verification/demo interface, not a
polished product: no design system, no charts, no routing library, no state
management library.

## Project structure

```
frontend/
  .env.example            # template — copy to .env and configure
  src/
    main.tsx              # entry point
    App.tsx               # workflow state machine: upload -> processing -> results
    index.css             # minimal global styles (no framework)
    api/
      client.ts           # the ONLY file that calls fetch() — every backend
                           # request goes through one function here
      types.ts             # TypeScript interfaces mirroring
                           # backend/models/schemas.py 1:1
    components/
      UploadForm.tsx       # Step 1 — POST /upload
      StatusPoller.tsx     # Step 2 — polls GET /status/{doc_id} until ready
      MetricsTable.tsx     # Step 3 — GET /metrics/{doc_id} (read-only)
      RatiosTable.tsx      # Step 4 — GET /ratios/{doc_id} (cells open Explain)
      ChatPanel.tsx        # Step 5 — POST /chat
      ExplainPanel.tsx     # Step 6 — GET /explain/{doc_id}/{metric}?year=
```

**Design principle**: components own their own data-fetching (each calls
exactly one `api/client.ts` function in a `useEffect`/handler and renders its
own loading/error state). `App.tsx` only tracks *which stage of the workflow
is active* — it holds no fetched data itself. If you need to add a new
screen, add one component here and one function in `api/client.ts`; you
should not need to touch anything else.

## Prerequisites

- Node.js 20+ and npm (developed against Node 25.2.1 / npm 11.6.2, but any
  reasonably current Node/npm should work)
- The FinTool backend running and reachable (see the root project's own
  setup — `backend/`, `requirements.txt`, `.env.example`)

## Installation

```bash
cd frontend
npm install
```

## Configuring `.env`

The frontend never hardcodes a backend URL. Every request in
`src/api/client.ts` reads `import.meta.env.VITE_API_BASE_URL`, which Vite
loads from `.env`.

```bash
cp .env.example .env
```

Then edit `.env` and set `VITE_API_BASE_URL` to match however you're
actually running the backend, e.g.:

```
VITE_API_BASE_URL=http://localhost:8000
```

If this doesn't match the backend's real host/port, every request will fail
with a network error (or a CORS error — see Troubleshooting below). The app
fails fast at startup with a clear error if `VITE_API_BASE_URL` is missing
entirely.

## Running the frontend

```bash
npm run dev
```

Vite will print a local URL (default `http://localhost:5173`). Open it in a
browser. `backend/main.py`'s CORS config already allow-lists
`http://localhost:5173`; if you run the dev server on a different port,
you'll need to add that origin to `backend/main.py`'s `CORSMiddleware`
config — the frontend does not control this.

```bash
npm run build      # type-checks (tsc -b) and produces dist/
npm run preview    # serve the production build locally
npm run lint        # oxlint
```

## Connecting to the backend

Start the backend separately (see the project root's setup — typically
`SYNC_MODE=true uvicorn backend.main:app --port 8000` for local development,
so `/upload` processes inline instead of requiring Celery/Redis). The
frontend and backend are two independent processes; the frontend only knows
about the backend through `VITE_API_BASE_URL`.

## Expected workflow

1. **Upload** — pick a PDF and submit. The backend validates it looks like a
   financial report and returns a `doc_id`.
2. **Status** — the app polls `GET /status/{doc_id}` every 3 seconds. First-time
   processing runs the full extraction + RAG-indexing pipeline and can take
   several minutes (see Troubleshooting — this can be much longer on a fresh
   backend process).
3. **Metrics** — once `status: ready`, the Metrics tab shows every resolved
   metric row (raw extracted values, one per metric/year).
4. **Ratios** — the Ratios tab shows a year × ratio grid of computed values.
   Click any non-empty cell to open the Explain panel for that ratio/year.
5. **Explain** — shows the formula, the numerator/denominator inputs (with
   source page numbers), and the risk classification where applicable. Note:
   only *computed ratios* have provenance — raw metrics on the Metrics tab
   are not explainable, which is why only Ratios cells are clickable.
6. **Chat** — ask a free-text question; answers are grounded in the
   document's RAG-indexed narrative text and cite source pages.

## Troubleshooting

- **"VITE_API_BASE_URL is not set"** — you haven't created `.env`. Run
  `cp .env.example .env`.
- **Network error / failed to fetch** — the backend isn't running, or
  `VITE_API_BASE_URL` points at the wrong host/port.
- **CORS error in the browser console** — the frontend's origin
  (`http://<host>:<port>` shown by `npm run dev`) isn't in
  `backend/main.py`'s `CORSMiddleware` `allow_origins` list. Add it there —
  this is backend configuration, not something the frontend can work around.
- **Upload/status polling seems stuck for a very long time** — on a freshly
  started backend process, the *first* request that touches the RAG
  embedding model (either the upload itself, or the first `/chat` call) can
  take a long time to complete depending on the machine — this is a backend
  cold-start characteristic (loading the sentence-transformers model), not a
  frontend bug. Subsequent requests in the same backend process are fast.
  The status panel will keep polling and eventually reflect `ready` once the
  backend finishes; it does not time out.
- **404 from Explain** — not every ratio has provenance data (e.g.
  `yoy_revenue_growth`, `total_debt` are not tracked in `provenance.json`).
  This is expected backend behavior, not a frontend defect.
- **`document does not appear to be a financial report`** — the backend
  requires financial-report keywords in the first 30 pages; use one of the
  benchmark annual reports in the project's `test/` directory.
