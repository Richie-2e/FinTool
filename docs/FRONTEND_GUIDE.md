# Frontend Guide

For setup/install instructions, see `frontend/README.md` — this document is
about the architecture and how to extend it, for a developer picking up
frontend work.

## Stack

React + Vite + TypeScript. No router library, no chart library, no state
management library, no CSS framework. This was a deliberate choice, not an
oversight: the app is a thin verification/demo layer over 6 backend
endpoints, and the workflow is strictly linear (upload → wait → view
results), so none of those categories of dependency currently earn their
complexity. If the app grows multiple independent pages with deep-linkable
URLs, `react-router` becomes justified — evaluate that when it actually
happens, don't add it preemptively.

## Folder structure

```
frontend/
  .env.example              # template for VITE_API_BASE_URL
  src/
    main.tsx                 # entry point — mounts <App /> into #root
    App.tsx                  # workflow state machine (see below)
    index.css                # minimal global styles, no framework
    vite-env.d.ts            # TS typing for import.meta.env.VITE_API_BASE_URL
    api/
      client.ts              # ALL fetch() calls live here — nowhere else
      types.ts                # TS interfaces mirroring backend Pydantic models
    components/
      UploadForm.tsx
      StatusPoller.tsx
      MetricsTable.tsx
      RatiosTable.tsx
      ChatPanel.tsx
      ExplainPanel.tsx
```

## Architectural principle: components own their own data

Every component in `components/` calls exactly one `api/client.ts` function
(in a `useEffect` for GET-on-mount, or an event handler for POST-on-submit)
and renders its own loading/error state locally. `App.tsx` does **not**
fetch anything and holds **no** fetched data — it only tracks *which stage
of the workflow is currently active* (`view: "upload" | "processing" |
"results"`) and the identifiers needed to move between stages (`docId`,
`pdfName`, `companyName`, which results tab is active, which metric is being
explained).

This means: if you need to add a new screen that shows backend data, you
almost never need to touch `App.tsx` beyond wiring in the new
component/tab. You do **not** need to lift state up, add a global store, or
touch any other component.

## `App.tsx` — the workflow state machine

```
view: "upload" -----(UploadForm calls onUploaded)----> "processing"
"processing" -----(StatusPoller calls onReady when status:"ready")----> "results"
"results": tabs { metrics | ratios | chat }, plus an optional ExplainPanel
           overlay triggered from a RatiosTable cell click
```

State held in `App.tsx`:
| State | Purpose |
|---|---|
| `view` | which of the 3 stages is rendered |
| `docId`, `pdfName`, `companyName` | identify the current document, shown in the header |
| `activeTab` | which results tab (`metrics`/`ratios`/`chat`) |
| `explainTarget` | `{ metricName, year } \| null` — when set, renders `<ExplainPanel />` |

"Upload a different document" resets all of this back to the `upload` view.

## `api/client.ts` — the only file that talks to the backend

One function per endpoint, all going through a shared `request<T>()` helper
that:
1. Prefixes every path with `API_BASE_URL` (read once from
   `import.meta.env.VITE_API_BASE_URL` — throws at module load if unset, so
   misconfiguration fails immediately and loudly rather than as a confusing
   runtime network error on first click)
2. Parses the JSON body
3. On a non-2xx response, parses the backend's `ErrorResponse` shape and
   throws a typed `ApiError` (`status`, `errorCode`, `docId`, `message`)

Functions: `uploadDocument`, `getStatus`, `getMetrics`, `getRatios`,
`postChat`, `getExplain`. **No component calls `fetch()` directly** — this
is the boundary to preserve. If you add a 7th endpoint, add a 7th function
here, not an inline `fetch()` in a component.

## `api/types.ts` — kept in sync with the backend by hand

These interfaces mirror `backend/models/schemas.py` field-for-field
(same names, same optionality — `Optional[X] = None` in Pydantic becomes
`X | null` in TypeScript). **There is no code generation step** — if a
Pydantic model in `schemas.py` changes, the matching interface here must be
updated manually. If this project grows, introducing an OpenAPI-schema-based
codegen step (FastAPI already emits an OpenAPI spec at `/openapi.json`)
would remove this manual-sync risk — noted as a reasonable future
improvement, not currently implemented.

## Component reference

| Component | Endpoint | Responsibility |
|---|---|---|
| `UploadForm` | `POST /upload` | File picker, submit, surfaces validation errors from the backend (wrong file type, not a financial report, etc.) |
| `StatusPoller` | `GET /status/{doc_id}` (polled every 3s) | Polls until `ready`/`failed`; guards against double-firing `onReady` under React StrictMode's dev-mode double-invoke |
| `MetricsTable` | `GET /metrics/{doc_id}` | Read-only table of every resolved metric row. **Not** wired to Explain — see below |
| `RatiosTable` | `GET /ratios/{doc_id}` | Year × ratio grid; non-null cells are clickable, opening `ExplainPanel` |
| `ChatPanel` | `POST /chat` | Free-text Q&A with running conversation history, renders cited sources and any warning |
| `ExplainPanel` | `GET /explain/{doc_id}/{metric}?year=` | Side panel showing formula, inputs, risk classification for one ratio/year |

**Why `MetricsTable` isn't clickable-to-Explain**: `/explain` only has
provenance data for *computed ratios*, not raw resolved metrics (confirmed
by testing — see `docs/API.md`). Wiring raw-metric rows to Explain would
mostly produce 404s. If the backend is later extended to track provenance
for raw metrics too, this would be a reasonable enhancement — don't add it
speculatively before that backend support exists.

## How to add a new screen

1. Add a function to `api/client.ts` for the new endpoint (follow the
   existing pattern — one function, uses `request<T>()`).
2. Add the matching TypeScript interface(s) to `api/types.ts`, mirroring the
   backend's Pydantic model exactly.
3. Add a new component in `components/` that calls that function in a
   `useEffect` (or handler) and renders loading/error/data states — copy the
   shape of `MetricsTable.tsx` or `RatiosTable.tsx` as a template.
4. Wire it into `App.tsx` — usually just a new tab or a new `view` state.

Do not add a global store, do not lift fetched data into `App.tsx`, do not
call `fetch()` outside `api/client.ts`.

## Known frontend-side limitations (not backend defects)

- `metrics_used` in the Chat response can miss ratio mentions if the model's
  phrasing doesn't exactly match `chat.py`'s hardcoded phrase list — this is
  backend behavior the frontend just renders as-is.
- The Upload/Status flow does not currently show a progress bar or estimated
  time — first-time processing on a cold backend process can take a very
  long time (see `PROJECT_STATUS.md` and `frontend/README.md`
  Troubleshooting) and the UI only shows "Processing…" with the backend's
  `progress_message`. A future improvement could surface pipeline-stage
  detail if the backend starts exposing it.
- No automated frontend tests exist yet (no Vitest/Testing Library setup).
  Verification so far has been manual + wire-level request replay (see
  `PROJECT_STATUS.md`).
