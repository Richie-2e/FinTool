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

from pathlib import Path
from typing import Optional

from extraction.classifier import PageClassification
from extraction.metric_extractor import CandidateMetric
from extraction.llm.candidate_validator import RejectionRecord, validate_candidates
from extraction.llm.ollama_client import call_ollama
from extraction.llm.page_selector import get_page_block, select_pages
from extraction.llm.prompts import build_user_prompt, get_system_prompt
from extraction.llm.response_parser import parse_response
from extraction.llm.structural_validator import run_structural_validation
from extraction.structural_tables import extract_structural_tables_for_pages

_STATEMENT_TYPES = ("balance_sheet", "income_statement", "cash_flow")


# ---------------------------------------------------------------------------
# cash_flow-only decomposition fallback (experimentally validated in
# scratchpad/next_architecture_review/CASH_FLOW_FALLBACK_POLICY_EXPERIMENT.md
# -- 0 false acceptances, 0 regressions of any kind across the full 5-doc
# gold corpus). Activates ONLY when the existing combined-page cash_flow
# call returns zero raw candidates (confirmed schema-abandonment signature,
# e.g. Reliance). Never touches balance_sheet/income_statement (no
# analogous lever exists for them -- see the experiment report's Section
# B), never touches the prompt, and never runs when the combined call
# already produced any raw candidate -- the existing successful-path
# behavior for cash_flow is completely unchanged in that case.
# ---------------------------------------------------------------------------

def _run_single_page_call(
    stmt_type: str,
    page_no: int,
    pages: dict[int, str],
    doc_id: str,
    section_type: str,
    pdf_path: Optional[str | Path],
) -> tuple[list[CandidateMetric], list[RejectionRecord]]:
    """One page, called and validated in isolation -- same steps as the main
    per-statement-type loop (call_ollama -> parse_response ->
    validate_candidates -> run_structural_validation), scoped to a single
    page. Used only by the cash_flow fallback below."""
    text_block = get_page_block(pages, [page_no])
    if not text_block.strip():
        return [], []

    system_prompt = get_system_prompt(stmt_type)
    user_text = build_user_prompt(stmt_type, text_block)
    result, latency, json_ok = call_ollama(system_prompt, user_text)

    print(f"  [fallback] cash_flow page {page_no}: call finished in {latency:.1f}s (json_ok={json_ok})")

    if result is None:
        return [], []

    new_candidates = parse_response(
        raw_result=result, stmt_type=stmt_type, doc_id=doc_id,
        page_no=page_no, section_type=section_type,
    )
    if not new_candidates:
        return [], []

    accepted, diagnostics = validate_candidates(new_candidates, text_block)

    page_candidates = accepted
    if pdf_path is not None and accepted:
        tables_by_page = extract_structural_tables_for_pages(pdf_path, [page_no])
        flat_tables = tables_by_page.get(page_no, [])
        page_candidates, l6_rejections = run_structural_validation(accepted, flat_tables)
        diagnostics = diagnostics + l6_rejections

    return page_candidates, diagnostics


def _values_close(a: Optional[float], b: Optional[float], tol: float = 0.01) -> bool:
    if a is None or b is None:
        return False
    if a == b:
        return True
    if b == 0:
        return False
    return abs(a - b) / abs(b) < tol


def _merge_fallback_pages(
    page_results: list[tuple[list[CandidateMetric], list[RejectionRecord]]],
) -> tuple[list[CandidateMetric], list[RejectionRecord]]:
    """Experiment-only merge policy (matches
    decomposition_experiment/reprocess_arm_c.py::merge_pages exactly, the
    policy validated in CASH_FLOW_FALLBACK_POLICY_EXPERIMENT.md): simple
    concatenation, deduped by (metric_name, year). A duplicate with a
    matching value keeps the VERIFIED copy if the two states differ. A
    duplicate with a genuinely different value keeps the first-seen page's
    candidate and drops the conflicting one -- confirmed empirically to
    never occur across the validated corpus, but handled deterministically
    rather than left undefined."""
    merged: dict[tuple[str, Optional[int]], CandidateMetric] = {}
    all_diagnostics: list[RejectionRecord] = []

    for candidates, diagnostics in page_results:
        all_diagnostics.extend(diagnostics)
        for c in candidates:
            key = (c.metric_name, c.year)
            if key not in merged:
                merged[key] = c
            else:
                existing = merged[key]
                if _values_close(existing.value, c.value):
                    if c.verification_state == "VERIFIED" and existing.verification_state != "VERIFIED":
                        merged[key] = c
                # else: conflicting value for the same (metric, year) --
                # keep the first-seen candidate, drop this one.

    return list(merged.values()), all_diagnostics


def extract_with_llm(
    pages: dict[int, str],
    page_classes: list[PageClassification],
    doc_id: str,
    pdf_path: Optional[str | Path] = None,
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
    pdf_path : str or Path, optional
        Phase B/C: when supplied, enables structural (L6) validation --
        PyMuPDF find_tables() is run, scoped to exactly the pages selected
        for each statement-type call (never the whole document), and every
        L2-L5-accepted candidate is passed through
        structural_validator.run_structural_validation() before being
        returned. When omitted (backward-compatible default), behavior is
        identical to before Phase B/C: candidates are returned with
        verification_state left at its CandidateMetric default (None).

    Returns
    -------
    (candidates, diagnostics)
        candidates  : list[CandidateMetric] -- candidates that passed
            L2/L3/L4/L5 validation AND (when pdf_path is given) did not hit
            L6's narrow structural-contradiction REJECTED path, across
            balance_sheet, income_statement, and cash_flow. May contain
            multiple entries for the same (metric_name, year) pair —
            resolution is handled downstream by build_resolved_metrics_df().
        diagnostics : list[RejectionRecord] -- one entry per candidate seen
            (accepted, L2-L5-rejected, or L6-rejected) across all three
            statement-type calls, for persistence to validator_rejections.csv
            by the caller.
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

        if stmt_type == "cash_flow" and not new_candidates and len(effective_pages) > 1:
            # Combined call produced zero raw candidates -- experimentally
            # validated fallback: call each page independently and merge.
            # See CASH_FLOW_FALLBACK_POLICY_EXPERIMENT.md. Never touched
            # when the combined call returns any raw candidate at all.
            print(f"  [fallback] cash_flow: 0 raw candidates from combined call — "
                  f"retrying pages {effective_pages} independently")
            page_results = [
                _run_single_page_call(stmt_type, pno, pages, doc_id, section_type, pdf_path)
                for pno in effective_pages
            ]
            stmt_candidates, diagnostics = _merge_fallback_pages(page_results)
            print(f"  [fallback] cash_flow: merged {len(stmt_candidates)} candidates "
                  f"from {len(effective_pages)} independent page calls")
        else:
            accepted, diagnostics = validate_candidates(new_candidates, text_block)
            rejected_count = len(new_candidates) - len(accepted)
            if rejected_count:
                print(
                    f"  [validator] {stmt_type}: rejected {rejected_count} of "
                    f"{len(new_candidates)} candidates (L2/L4/L5)"
                )

            stmt_candidates = accepted
            if pdf_path is not None and accepted:
                tables_by_page = extract_structural_tables_for_pages(pdf_path, effective_pages)
                flat_tables = [t for pno in effective_pages for t in tables_by_page.get(pno, [])]
                stmt_candidates, l6_rejections = run_structural_validation(accepted, flat_tables)
                if l6_rejections:
                    print(
                        f"  [L6] {stmt_type}: rejected {len(l6_rejections)} of "
                        f"{len(accepted)} L2-L5-accepted candidates (structural contradiction)"
                    )
                diagnostics = diagnostics + l6_rejections

        candidates.extend(stmt_candidates)
        all_diagnostics.extend(diagnostics)

    return candidates, all_diagnostics
