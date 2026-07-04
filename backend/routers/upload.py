from __future__ import annotations

import hashlib
import io
import json
import os

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from backend.config import MAX_FILE_SIZE_BYTES, UPLOAD_DIR
from backend.exceptions import APIError
from backend.models.db import Document, get_db
from backend.models.schemas import StatusResponse, UploadResponse

router = APIRouter()

_FINANCIAL_KEYWORDS = [
    "balance sheet",
    "profit and loss",
    "cash flow",
    "annual report",
    "10-k",
    "10-q",
]
_CONTENT_SCAN_PAGES = 30  # spec §3.1: first 30 pages; covers NSE/BSE reports with long covers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _doc_id(filename: str, file_size: int) -> str:
    return hashlib.md5(f"{filename}{file_size}".encode()).hexdigest()[:12]


def _has_financial_content(file_bytes: bytes) -> bool:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        pages_to_scan = min(_CONTENT_SCAN_PAGES, len(reader.pages))
        for i in range(pages_to_scan):
            text = (reader.pages[i].extract_text() or "").lower()
            if any(kw in text for kw in _FINANCIAL_KEYWORDS):
                return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------------------
# POST /upload
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=UploadResponse, status_code=202)
async def upload_pdf(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadResponse:
    # a. Extension
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise APIError(400, "File must have a .pdf extension", "INVALID_PDF")

    # Read entire file once so we can validate size + content without seeking
    file_bytes = await file.read()
    file_size  = len(file_bytes)

    # b. Size
    if file_size > MAX_FILE_SIZE_BYTES:
        raise APIError(400, "File exceeds the 50 MB limit", "FILE_TOO_LARGE")

    # c. MIME type
    if file.content_type != "application/pdf":
        raise APIError(400, "MIME type must be application/pdf", "INVALID_PDF")

    # d. Financial content
    if not _has_financial_content(file_bytes):
        raise APIError(
            400,
            "Document does not appear to be a financial report "
            "(no balance sheet / P&L / cash flow / annual report keywords found in first 30 pages)",
            "INVALID_PDF",
        )

    # Compute deterministic doc_id and persist the file
    doc_id = _doc_id(filename, file_size)
    pdf_save_path = UPLOAD_DIR / f"{doc_id}.pdf"
    pdf_save_path.write_bytes(file_bytes)

    # Insert Document row (idempotent-ish: re-upload replaces progress)
    existing = db.query(Document).filter(Document.doc_id == doc_id).first()
    if existing:
        existing.status = "processing"
        existing.progress_message = "Queued for processing..."
        existing.error_message = None
    else:
        db.add(Document(
            doc_id=doc_id,
            pdf_name=filename,
            status="processing",
            progress_message="Queued for processing...",
        ))
    db.commit()

    # Enqueue Celery task (or run inline when SYNC_MODE=true for testing)
    from backend.tasks.extraction_task import run_extraction_pipeline  # lazy to avoid Celery startup on import

    if os.getenv("SYNC_MODE", "false").lower() == "true":
        run_extraction_pipeline(doc_id, str(pdf_save_path))
    else:
        run_extraction_pipeline.delay(doc_id, str(pdf_save_path))

    return UploadResponse(
        doc_id=doc_id,
        pdf_name=filename,
        status="processing",
        message="Document accepted. Use GET /status/{doc_id} to track progress.",
    )


# ---------------------------------------------------------------------------
# GET /status/{doc_id}
# ---------------------------------------------------------------------------

@router.get("/status/{doc_id}", response_model=StatusResponse)
def get_status(doc_id: str, db: Session = Depends(get_db)) -> StatusResponse:
    doc = db.query(Document).filter(Document.doc_id == doc_id).first()
    if not doc:
        raise APIError(404, "Document not found", "DOC_NOT_FOUND", doc_id=doc_id)

    document_years: list[int] | None = None
    if doc.document_years:
        try:
            document_years = json.loads(doc.document_years)
        except (json.JSONDecodeError, TypeError):
            document_years = None

    return StatusResponse(
        doc_id=doc.doc_id,
        status=doc.status,
        progress_message=doc.progress_message,
        company_name=doc.company_name,
        document_years=document_years,
        error=doc.error_message if doc.status == "failed" else None,
    )
