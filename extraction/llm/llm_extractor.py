"""
extraction/llm/llm_extractor.py
Main entry point for LLM-based metric extraction (Stage 3, V2 path).

extract_with_llm() is the direct replacement for
metric_extractor.extract_from_docling_tables(). Both accept doc_id and
page classification data; both return list[CandidateMetric].
The downstream pipeline (build_resolved_metrics_df → pivot_metrics →
numerical_module) is identical regardless of which function produced the list.

Orchestration flow per statement type:
  1. page_selector.select_pages()    — which pages to send (Fix 2 policy)
  2. page_selector.get_page_block()  — concatenate selected page text
  3. prompts.get_system_prompt()     — statement-specific system prompt
  4. prompts.build_user_prompt()     — user-turn message with scoped canonical names
  5. ollama_client.call_ollama()     — HTTP call to local Ollama service
  6. response_parser.parse_response() — JSON dict → list[CandidateMetric]
"""

from __future__ import annotations

from extraction.classifier import PageClassification
from extraction.metric_extractor import CandidateMetric
from extraction.llm.candidate_validator import RejectionRecord, validate_candidates
from extraction.llm.ollama_client import call_ollama
from extraction.llm.page_selector import get_page_block, select_pages
from extraction.llm.prompts import build_user_prompt, get_system_prompt
from extraction.llm.response_parser import parse_response

_STATEMENT_TYPES = ("balance_sheet", "income_statement", "cash_flow")


def extract_with_llm(
    pages: dict[int, str],
    page_classes: list[PageClassification],
    doc_id: str,
) -> tuple[list[CandidateMetric], list[RejectionRecord]]:
    """
    Run LLM-based extraction for all three statement types and return the
    validated candidates plus per-candidate validator diagnostics.

    Parameters
    ----------
    pages : dict[int, str]
        Mapping of page_no (1-indexed) → raw page text.
        Typically built from DoclingParseResult.pages_raw_text:
            {i + 1: text for i, text in enumerate(parse_result.pages_raw_text)}
    page_classes : list[PageClassification]
        Output of classifier.classify_statement_pages(). Used both to find
        which pages contain each statement type and to determine section_type
        (consolidated vs standalone).
    doc_id : str
        Document identifier forwarded to every CandidateMetric.

    Returns
    -------
    (candidates, diagnostics)
        candidates  : list[CandidateMetric] -- only candidates that passed
            L2/L4/L5 validation, across balance_sheet, income_statement, and
            cash_flow. May contain multiple entries for the same
            (metric_name, year) pair — resolution is handled downstream by
            build_resolved_metrics_df().
        diagnostics : list[RejectionRecord] -- one entry per candidate seen
            (accepted or not) across all three statement-type calls, for
            persistence to validator_rejections.csv by the caller.
    """
    page_class_map: dict[int, PageClassification] = {
        pc.page_no: pc for pc in page_classes
    }
    candidates: list[CandidateMetric] = []
    all_diagnostics: list[RejectionRecord] = []

    for stmt_type in _STATEMENT_TYPES:
        effective_pages = select_pages(page_classes, stmt_type)
        if not effective_pages:
            print(f"  [llm] No pages classified as {stmt_type} — skipping")
            continue

        text_block = get_page_block(pages, effective_pages)
        if not text_block.strip():
            print(f"  [llm] Empty text block for {stmt_type} — skipping")
            continue

        primary_page_no = effective_pages[0]
        pc = page_class_map.get(primary_page_no)
        section_type = pc.section_type if pc else "unknown"

        system_prompt = get_system_prompt(stmt_type)
        user_text = build_user_prompt(stmt_type, text_block)

        print(
            f"  [llm] {stmt_type}: pages {effective_pages} "
            f"({len(text_block)} chars, section={section_type})"
        )

        result, latency, json_ok = call_ollama(system_prompt, user_text)

        if result is None:
            print(f"  [llm] {stmt_type}: call failed ({latency:.1f}s)")
            continue

        new_candidates = parse_response(
            raw_result=result,
            stmt_type=stmt_type,
            doc_id=doc_id,
            page_no=primary_page_no,
            section_type=section_type,
        )

        status = "valid JSON" if json_ok else "partial recovery"
        print(
            f"  [llm] {stmt_type}: {len(new_candidates)} candidates "
            f"in {latency:.1f}s ({status})"
        )

        accepted, diagnostics = validate_candidates(new_candidates, text_block)
        rejected_count = len(new_candidates) - len(accepted)
        if rejected_count:
            print(
                f"  [validator] {stmt_type}: rejected {rejected_count} of "
                f"{len(new_candidates)} candidates (L2/L4/L5)"
            )
        candidates.extend(accepted)
        all_diagnostics.extend(diagnostics)

    return candidates, all_diagnostics
