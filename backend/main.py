from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import OUTPUT_DIR, UPLOAD_DIR
from backend.exceptions import APIError
from backend.models.db import create_tables
from backend.routers import chat, explain, metrics, upload

app = FastAPI(title="FinTool API", version="1.0")

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Exception handler — converts APIError → spec-compliant flat JSON
# ---------------------------------------------------------------------------

@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail":     exc.detail,
            "error_code": exc.error_code,
            "doc_id":     exc.doc_id,
        },
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "detail":     str(exc),
            "error_code": "INTERNAL_ERROR",
            "doc_id":     None,
        },
    )


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup() -> None:
    create_tables()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(upload.router)
app.include_router(metrics.router)
app.include_router(chat.router)
app.include_router(explain.router)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
