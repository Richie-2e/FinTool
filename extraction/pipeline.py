"""
pipeline.py
Orchestrator — runs all 4 extraction stages in order and saves all outputs.

Stage 1: parse_pdf             → DoclingParseResult
Stage 2: classify_statement_pages + tag_tables → page_classes
Stage 3: extract_with_llm → resolved_df, pivot_df  (V2 primary path)
         extract_candidates_from_page → fallback if LLM returns < 3 candidates
Stage 4: chunk_document + build_faiss_index → chunks
"""

from __future__ import annotations

import os

# Must be set before any transformers import (triggered lazily by Stage 4's
# sentence-transformers and by Docling's fallback path): transformers
# auto-probes for TensorFlow, and the TensorFlow build installed on this
# machine deadlocks/crashes in its bundled protobuf/abseil init. This project
# only uses the PyTorch backend, so disabling the TF probe avoids the bug
# entirely with no functional change.
os.environ.setdefault("USE_TF", "0")

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from extraction.canonical_metrics import DATE_YEAR_RE, YEAR_RE
from extraction.classifier import classify_statement_pages, tag_tables_with_classification
from extraction.metric_extractor import (
    add_derived_totals_if_possible,
    build_resolved_metrics_df,
    extract_candidates_from_page,
    extract_from_docling_tables,
    pivot_metrics,
    run_accounting_checks,
)
from extraction.llm import extract_with_llm
from extraction.parser import parse_pdf
from extraction.text_chunker import TextChunk, build_faiss_index, chunk_document


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    doc_id:      str
    pdf_name:    str
    pivot_df:    pd.DataFrame
    resolved_df: pd.DataFrame
    quality:     dict
    chunks:      list[TextChunk]
    output_dir:  Path
    meta:        dict


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------

class ExtractionPipeline:

    def update_status(self, msg: str) -> None:
        print(msg, flush=True)

    def run(
        self,
        pdf_path: Path,
        output_dir: Optional[Path] = None,
        doc_id: Optional[str] = None,
    ) -> PipelineResult:
        """
        doc_id : optional external identifier (e.g. assigned by the upload
            endpoint). When supplied, it is the single authoritative id for
            every stage of this run — parsing, LLM extraction, chunking, and
            FAISS indexing all use it as-is. When omitted (standalone / CLI
            usage), the parser self-derives one, matching prior behavior.
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        t0 = time.time()
        log_lines: list[str] = []

        def _log(msg: str) -> None:
            self.update_status(msg)
            elapsed = time.time() - t0
            log_lines.append(f"[{elapsed:6.1f}s] {msg}")

        # ── Stage 1: Parse ───────────────────────────────────────────────────
        _log("Stage 1/4: Parsing document...")
        parse_result = parse_pdf(pdf_path, doc_id=doc_id)
        doc_id = parse_result.doc_id
        stem   = pdf_path.stem

        if output_dir is None:
            output_dir = Path("extraction_outputs") / doc_id
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        _log(f"  parser={parse_result.parser_used} | pages={parse_result.page_count} | "
             f"tables={len(parse_result.tables)}")

        # ── Stage 2: Classify ────────────────────────────────────────────────
        _log("Stage 2/4: Classifying statement pages...")
        page_classes = classify_statement_pages(parse_result.pages_raw_text)
        tag_tables_with_classification(parse_result.tables, page_classes)

        non_other = sum(1 for pc in page_classes if pc.statement_type != "other")
        _log(f"  {non_other} financial statement pages detected")

        # ── Stage 3: Metric extraction ───────────────────────────────────────
        _log("Stage 3/4: Extracting metrics...")
        pages = {i + 1: text for i, text in enumerate(parse_result.pages_raw_text)}
        candidates = extract_with_llm(pages, page_classes, doc_id)
        _log(f"  LLM extraction candidates: {len(candidates)}")

        if len(candidates) < 3:
            _log("  LLM candidates < 3 — running V1 line-parse fallback on all pages...")
            doc_years = _detect_doc_years(parse_result.pages_raw_text)
            class_map = {pc.page_no: pc for pc in page_classes}

            for idx, page_text in enumerate(parse_result.pages_raw_text):
                page_no = idx + 1
                pc = class_map.get(page_no)
                if pc is None:
                    continue
                fb = extract_candidates_from_page(
                    page_text,
                    page_no,
                    pc.statement_type,
                    pc.section_type,
                    doc_id,
                    doc_years,
                )
                candidates.extend(fb)
            _log(f"  After fallback: {len(candidates)} total candidates")

        resolved_df, quality = build_resolved_metrics_df(candidates)
        pivot_df = pivot_metrics(resolved_df)

        # Accounting checks (already run inside build_resolved_metrics_df;
        # call again so the result is explicit in quality)
        warnings = run_accounting_checks(resolved_df)
        quality["accounting_warnings"] = warnings

        years_in_doc = (
            sorted(resolved_df["year"].dropna().unique().astype(int).tolist())
            if not resolved_df.empty and "year" in resolved_df.columns
            else []
        )
        _log(f"  Resolved {quality['resolved_rows']} metric rows | "
             f"years: {years_in_doc} | accounting warnings: {len(warnings)}")

        # ── Stage 4: RAG chunking ────────────────────────────────────────────
        _log("Stage 4/4: Building RAG index...")
        chunks = chunk_document(parse_result.pages_raw_text, page_classes, doc_id)
        build_faiss_index(chunks, output_dir, doc_id)
        _log(f"  {len(chunks)} text chunks indexed")

        # ── Meta ─────────────────────────────────────────────────────────────
        company_name = _detect_company_name(parse_result.pages_raw_text)
        meta: dict = {
            "doc_id":      doc_id,
            "pdf_name":    parse_result.pdf_name,
            "page_count":  parse_result.page_count,
            "parser_used": parse_result.parser_used,
            "company_name": company_name,
            "years":       years_in_doc,
        }

        # ── Save outputs ─────────────────────────────────────────────────────
        resolved_df.to_csv(output_dir / f"{stem}_resolved_metrics.csv", index=False)
        pivot_df.to_csv(output_dir / f"{stem}_pivot_metrics.csv", index=False)

        with open(output_dir / f"{stem}_quality_report.json", "w", encoding="utf-8") as fh:
            json.dump(quality, fh, indent=2, default=str)

        with open(output_dir / f"{stem}_meta.json", "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, default=str)

        _log(f"Done. Outputs → {output_dir}")
        with open(output_dir / f"{stem}_log.txt", "w", encoding="utf-8") as fh:
            fh.write("\n".join(log_lines) + "\n")

        return PipelineResult(
            doc_id=doc_id,
            pdf_name=parse_result.pdf_name,
            pivot_df=pivot_df,
            resolved_df=resolved_df,
            quality=quality,
            chunks=chunks,
            output_dir=output_dir,
            meta=meta,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _detect_doc_years(pages_raw_text: list[str], n_pages: int = 5) -> list[int]:
    """Scan the first n_pages (top-12 lines each) for 4-digit years."""
    years: list[int] = []
    for page_text in pages_raw_text[:n_pages]:
        header = "\n".join(page_text.splitlines()[:12])
        for m in DATE_YEAR_RE.finditer(header):
            for g in m.groups():
                if g:
                    y = int(g)
                    if 2000 <= y <= 2100 and y not in years:
                        years.append(y)
        for m in YEAR_RE.finditer(header):
            y = int(m.group(1))
            if 2000 <= y <= 2100 and y not in years:
                years.append(y)
    return years[:4]


def _detect_company_name(pages_raw_text: list[str]) -> str:
    """
    Scan the first 3 pages for a company-name-like line.
    Prefer short lines (5-80 chars) that are mostly alphabetic and title/upper-case.
    Skip lines that look like dates, addresses, or BSE/NSE boilerplate.
    """
    import re
    _SKIP_RE = re.compile(
        r"(bse|nse|bombay stock|national stock|dalal|phiroze|sebi|"
        r"limited liability|cin\s*:|gstin|pan\s*:|website|www\.|"
        r"\d{1,2}[\s,/]\d{1,2}[\s,/]\d{2,4}|"  # dates
        r"dear|subject|ref\s*:|mumbai|chennai|delhi|kolkata|hyderabad|"
        r"august|january|february|march|april|may|june|july|september|"
        r"october|november|december)",
        re.IGNORECASE,
    )

    for page_text in pages_raw_text[:3]:
        for line in page_text.splitlines():
            line = line.strip()
            if not (5 < len(line) <= 80):
                continue
            if _SKIP_RE.search(line):
                continue
            alpha_ratio = sum(1 for c in line if c.isalpha()) / len(line)
            if alpha_ratio < 0.5:
                continue
            # Prefer lines that are title-case or ALL CAPS
            if line.istitle() or line.isupper() or "Limited" in line or "Ltd" in line:
                return line
    # Last resort: return first non-trivial line of page 1
    for line in pages_raw_text[0].splitlines():
        line = line.strip()
        if len(line) > 5:
            return line
    return ""
