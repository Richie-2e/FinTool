"""
parser.py
Stage 1 — Raw document parsing.
Tries PyMuPDF first, then Docling, then pdfplumber, then pypdf (text-only).
OCR via Docling's built-in pipeline is available as an optional fallback path.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

# Docling layout + table models are cached here on first run (~500 MB download).
# If either folder is missing we skip Docling to avoid blocking indefinitely.
_DOCLING_CACHE = Path.home() / ".cache" / "docling" / "models"
_DOCLING_LAYOUT_FOLDER  = "docling-project--docling-layout-heron"
_DOCLING_TABLE_FOLDER   = "docling-project--docling-models"


def _docling_models_cached() -> bool:
    return (
        (_DOCLING_CACHE / _DOCLING_LAYOUT_FOLDER).exists()
        and (_DOCLING_CACHE / _DOCLING_TABLE_FOLDER).exists()
    )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DoclingTable:
    page_no: int          # 1-indexed
    df: pd.DataFrame      # rows × cols as extracted
    caption: str          # heading text above the table, or ""
    section_hint: str     # "standalone" | "consolidated" | "unknown"
    statement_type: str = "other"  # set by classifier.tag_tables_with_classification


@dataclass
class DoclingParseResult:
    doc_id: str                    # caller-supplied id, or self-derived: first 12 hex chars of md5(filename + file_size)
    pdf_name: str
    page_count: int
    pages_raw_text: list[str]      # one string per page, index 0 = page 1
    tables: list[DoclingTable]
    parser_used: str               # "pymupdf" | "docling" | "docling_ocr" | "pdfplumber" | "pypdf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_doc_id(pdf_path: Path) -> str:
    stat = pdf_path.stat()
    raw = f"{pdf_path.name}{stat.st_size}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _resolve_doc_id(pdf_path: Path, doc_id: Optional[str]) -> str:
    """Use the caller-supplied doc_id when given; only self-derive for
    standalone/CLI usage where no external identity exists yet."""
    return doc_id if doc_id is not None else _make_doc_id(pdf_path)


_STANDALONE_RE = re.compile(r"\bstandalone\b", re.IGNORECASE)
_CONSOLIDATED_RE = re.compile(r"\bconsolidated\b", re.IGNORECASE)


def _infer_section_hint(text: str) -> str:
    has_consol = bool(_CONSOLIDATED_RE.search(text))
    has_standalone = bool(_STANDALONE_RE.search(text))
    if has_consol and not has_standalone:
        return "consolidated"
    if has_standalone and not has_consol:
        return "standalone"
    return "unknown"


# ---------------------------------------------------------------------------
# Parser 1 — PyMuPDF (primary)
# ---------------------------------------------------------------------------

def _parse_with_pymupdf(pdf_path: Path, doc_id: Optional[str] = None) -> DoclingParseResult:
    import fitz  # PyMuPDF

    doc = fitz.open(str(pdf_path))
    page_count = doc.page_count
    pages_raw_text = [page.get_text("text") for page in doc]
    doc.close()

    # No structured table extraction here: Stage 3 (extract_with_llm) consumes
    # pages_raw_text only, so `tables` is left empty, matching the pypdf path.
    tables: list[DoclingTable] = []

    print(f"[parser] Used: pymupdf | pages: {page_count} | tables: 0")

    return DoclingParseResult(
        doc_id=_resolve_doc_id(pdf_path, doc_id),
        pdf_name=pdf_path.name,
        page_count=page_count,
        pages_raw_text=pages_raw_text,
        tables=tables,
        parser_used="pymupdf",
    )


# ---------------------------------------------------------------------------
# Parser 2 — Docling (fallback 1)
# ---------------------------------------------------------------------------

def _parse_with_docling(
    pdf_path: Path, use_ocr: bool = False, doc_id: Optional[str] = None
) -> DoclingParseResult:
    from docling.document_converter import DocumentConverter, PdfFormatOption  # type: ignore
    from docling.datamodel.base_models import InputFormat  # type: ignore
    from docling.datamodel.pipeline_options import PdfPipelineOptions  # type: ignore
    from docling_core.types.doc import DocItemLabel, TableItem  # type: ignore

    pipeline_opts = PdfPipelineOptions()
    pipeline_opts.do_ocr = use_ocr
    pipeline_opts.do_table_structure = True

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)
        }
    )

    # Indian ARs often exceed 300 pages of boilerplate before/after financials.
    # ML layout+table detection on every page takes hours on large PDFs.
    # Limit Docling to the last _DOCLING_PAGE_WINDOW pages for large documents —
    # financials (standalone + consolidated) are almost always in the latter half.
    _DOCLING_PAGE_WINDOW = 180
    page_range_kwarg: dict = {}
    try:
        import pypdfium2 as _pdfium  # type: ignore
        _pdoc = _pdfium.PdfDocument(str(pdf_path))
        _total = len(_pdoc)
        _pdoc.close()
        if _total > _DOCLING_PAGE_WINDOW:
            _start = _total - _DOCLING_PAGE_WINDOW + 1
            page_range_kwarg = {"page_range": (_start, _total)}
            print(f"[parser] Large PDF ({_total} pages) — Docling restricted to pages "
                  f"{_start}-{_total}")
    except Exception:
        pass  # pypdfium2 unavailable; process all pages

    result = converter.convert(str(pdf_path), **page_range_kwarg)
    doc = result.document

    # ── per-page raw text ──────────────────────────────────────────────────
    # Collect text items grouped by page_no (1-indexed → list index 0-based)
    page_count = doc.num_pages
    page_texts: dict[int, list[str]] = {i: [] for i in range(1, page_count + 1)}

    for item, _ in doc.iterate_items():
        if not hasattr(item, "text"):
            continue
        if not item.prov:
            continue
        pno = item.prov[0].page_no
        if pno in page_texts:
            page_texts[pno].append(item.text)

    pages_raw_text: list[str] = [
        "\n".join(page_texts.get(i, [])) for i in range(1, page_count + 1)
    ]

    # ── tables ────────────────────────────────────────────────────────────
    tables: list[DoclingTable] = []
    for item, _ in doc.iterate_items():
        if not isinstance(item, TableItem):
            continue
        if not item.prov:
            continue

        try:
            df = item.export_to_dataframe()
        except Exception:
            continue

        if df.empty:
            continue

        pno = item.prov[0].page_no

        # Caption: use TableItem.caption_text if available
        try:
            caption = item.caption_text(doc)
        except Exception:
            caption = ""
        caption = caption or ""

        # section_hint: check caption + a window of text on the same page
        context = caption + " " + pages_raw_text[pno - 1][:500]
        hint = _infer_section_hint(context)

        tables.append(DoclingTable(page_no=pno, df=df, caption=caption, section_hint=hint))

    parser_label = "docling_ocr" if use_ocr else "docling"
    print(f"[parser] Used: {parser_label} | pages: {page_count} | tables: {len(tables)}")

    return DoclingParseResult(
        doc_id=_resolve_doc_id(pdf_path, doc_id),
        pdf_name=pdf_path.name,
        page_count=page_count,
        pages_raw_text=pages_raw_text,
        tables=tables,
        parser_used=parser_label,
    )


# ---------------------------------------------------------------------------
# Parser 3 — pdfplumber (fallback 2)
# ---------------------------------------------------------------------------

def _parse_with_pdfplumber(pdf_path: Path, doc_id: Optional[str] = None) -> DoclingParseResult:
    import pdfplumber  # type: ignore

    pages_raw_text: list[str] = []
    tables: list[DoclingTable] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        page_count = len(pdf.pages)
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages_raw_text.append(text)

            for raw_table in page.extract_tables() or []:
                if not raw_table:
                    continue
                # First row as header when it looks like one, else use col indices
                try:
                    header = raw_table[0]
                    rows = raw_table[1:]
                    if all(isinstance(h, str) for h in header):
                        df = pd.DataFrame(rows, columns=header)
                    else:
                        df = pd.DataFrame(raw_table)
                except Exception:
                    df = pd.DataFrame(raw_table)

                if df.empty:
                    continue

                hint = _infer_section_hint(text[:500])
                tables.append(DoclingTable(page_no=pno, df=df, caption="", section_hint=hint))

    print(f"[parser] Used: pdfplumber | pages: {page_count} | tables: {len(tables)}")

    return DoclingParseResult(
        doc_id=_resolve_doc_id(pdf_path, doc_id),
        pdf_name=pdf_path.name,
        page_count=page_count,
        pages_raw_text=pages_raw_text,
        tables=tables,
        parser_used="pdfplumber",
    )


# ---------------------------------------------------------------------------
# Parser 4 — pypdf (fallback 3, text-only)
# ---------------------------------------------------------------------------

def _parse_with_pypdf(pdf_path: Path, doc_id: Optional[str] = None) -> DoclingParseResult:
    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(str(pdf_path))
    page_count = len(reader.pages)
    pages_raw_text = [p.extract_text() or "" for p in reader.pages]

    print(f"[parser] Used: pypdf (text-only) | pages: {page_count} | tables: 0")

    return DoclingParseResult(
        doc_id=_resolve_doc_id(pdf_path, doc_id),
        pdf_name=pdf_path.name,
        page_count=page_count,
        pages_raw_text=pages_raw_text,
        tables=[],
        parser_used="pypdf",
    )


# ---------------------------------------------------------------------------
# Parser 2b — Docling with OCR (scanned PDFs)
# ---------------------------------------------------------------------------
# Called automatically when Docling succeeds but yields no text at all
# (likely a scanned document).  Can also be triggered explicitly.

def _parse_with_docling_ocr(pdf_path: Path, doc_id: Optional[str] = None) -> DoclingParseResult:
    return _parse_with_docling(pdf_path, use_ocr=True, doc_id=doc_id)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_pdf(pdf_path: str | Path, doc_id: Optional[str] = None) -> DoclingParseResult:
    """
    Parse a PDF through the fallback chain:
      1. PyMuPDF (primary)
      2. Docling (if PyMuPDF is unavailable/fails, or returns blank pages)
      3. Docling with OCR (if Docling returned blank pages — scanned doc)
      4. pdfplumber
      5. pypdf (text-only)

    doc_id : optional external identifier (e.g. assigned by the upload
        endpoint). When supplied, every parser in the chain uses it as-is
        instead of self-deriving one — the parser never recomputes or
        overwrites an externally supplied identity. When omitted (standalone
        / CLI usage), behavior is unchanged: each parser self-derives doc_id
        from the file's name + size.

    Raises RuntimeError only if all parsers fail.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # ── PyMuPDF (primary) ───────────────────────────────────────────────────
    try:
        result = _parse_with_pymupdf(pdf_path, doc_id=doc_id)
        total_text = sum(len(t) for t in result.pages_raw_text)
        if result.page_count > 0 and total_text == 0:
            print("[parser] PyMuPDF returned empty text — falling back to Docling")
        else:
            return result
    except ImportError:
        print("[parser] PyMuPDF not available — falling back to Docling")
    except Exception as e:
        print(f"[parser] PyMuPDF failed ({e}) — falling back to Docling")

    # ── Docling (fallback 1) ────────────────────────────────────────────────
    if not _docling_models_cached():
        print("[parser] Docling models not cached — falling back to pdfplumber. "
              "Run `docling-tools models download` once to enable Docling.")
    else:
        try:
            result = _parse_with_docling(pdf_path, use_ocr=False, doc_id=doc_id)
            total_text = sum(len(t) for t in result.pages_raw_text)
            if result.page_count > 0 and total_text == 0:
                print("[parser] Docling returned empty text — retrying with OCR")
                try:
                    return _parse_with_docling_ocr(pdf_path, doc_id=doc_id)
                except Exception as ocr_err:
                    print(f"[parser] Docling OCR failed: {ocr_err}")
            else:
                return result
        except ImportError:
            print("[parser] Docling not available — falling back to pdfplumber")
        except Exception as e:
            print(f"[parser] Docling failed ({e}) — falling back to pdfplumber")

    # ── pdfplumber (fallback 2) ────────────────────────────────────────────
    try:
        return _parse_with_pdfplumber(pdf_path, doc_id=doc_id)
    except Exception as e:
        print(f"[parser] pdfplumber failed ({e}) — falling back to pypdf")

    # ── pypdf (fallback 3, text-only) ─────────────────────────────────────
    try:
        return _parse_with_pypdf(pdf_path, doc_id=doc_id)
    except Exception as e:
        raise RuntimeError(f"All parsers failed for {pdf_path}: {e}") from e
