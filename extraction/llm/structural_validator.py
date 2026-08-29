"""
extraction/llm/structural_validator.py
L6 -- deterministic structural/semantic validation, run AFTER L2-L5
(extraction/llm/candidate_validator.py, UNCHANGED and UNIMPORTED-FROM for
any logic -- only its RejectionRecord dataclass shape is reused, to keep a
single rejection-diagnostic contract rather than inventing a second one).

L2-L5 answer "is this genuinely grounded somewhere on the page, and
internally self-consistent" -- and do so correctly (confirmed: 172/172
existing tests still pass, this module changes none of that logic). They
cannot answer a different question: "does this exact value belong to this
exact row and year/column, as opposed to a different, structurally similar
row on the same page." That is L6's job. It exists because of a confirmed
live production failure (OFSS operating_cash_flow, see
scratchpad/production_review/CURRENT_SYSTEM_GROUND_TRUTH.md sec K.1/D) where
L2-L5 all correctly passed a candidate that was nonetheless the wrong row.

Design validated against real PyMuPDF find_tables() output on 4 real pages
(OFSS pp.61/62/117, TataSteel p.282) before any matching logic was written --
see the Phase B/C session's inspection output for exact row/column dumps.
Row identity is resolved primarily by VALUE + YEAR-COLUMN POSITION, not
label-text matching: label-less subtotal rows (OFSS's current_assets/
current_liabilities) are a confirmed real, correct pattern that label-text
matching would have wrongly flagged.

State model: VERIFIED | NEEDS_REVIEW, matching the taxonomy documented in
docs/ARCHITECTURE.md's "L6 structural validation" section -- no new state
enum introduced. "No structural table available" and "structurally
ambiguous" are both NEEDS_REVIEW, distinguished by verification_reason
text, per that section's own explicit design choice.

REJECTED is reserved for a narrow, high-confidence structural CONTRADICTION
only: raw_label uniquely and unambiguously identifies one row, that row's
year-column for the candidate's declared year is unambiguous, and the cell
there holds a different, real number. An L2-L5-accepted candidate is never
removed for any weaker reason than this -- every other outcome is VERIFIED
or NEEDS_REVIEW, per the explicit "must not silently destroy coverage"
requirement (and the lesson already learned from L3's total_equity
coverage-vs-fidelity tradeoff, see PRODUCTION_ROADMAP.md).
"""

from __future__ import annotations

import re
from typing import Optional

from extraction.canonical_metrics import match_metric
from extraction.metric_extractor import CandidateMetric, parse_value
from extraction.llm.candidate_validator import RejectionRecord
from extraction.structural_tables import StructuralTable

# Absolute tolerance for a numeric cell match -- deliberately NOT L5's 2%
# relative tolerance. L5 checks one already-anchored candidate against a
# small, already-grounded text window, where a false positive is unlikely;
# L6 scans every cell in a whole table, a much larger search space where a
# loose relative tolerance produces real false matches -- confirmed live
# during Phase B/C testing: 0.02 relative tolerance matched OFSS's "Profit
# before tax" (33,109) against a declared value of 33,547 (a coincidental
# ~1.3% difference between two unrelated line items), corrupting the
# competing-rows/duplicate-ambiguity logic. 0.005 covers only genuine
# half-cent rounding/float-representation noise in already-parsed figures.
_VALUE_TOL = 0.005


def _values_close(a: float, b: float) -> bool:
    return abs(a - b) < _VALUE_TOL


def _normalize_row_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _has_any_numeric_cell(row: list[str]) -> bool:
    return any(parse_value(cell) is not None for cell in row[1:])


def _competing_rows_for_metric(table: StructuralTable, metric_name: str, statement_type: str) -> list[int]:
    """Every DATA row (has at least one numeric cell -- excludes section-
    header rows, which can spuriously label-match a canonical alias despite
    holding no values at all; confirmed real: OFSS row 1, "Cash flows from
    operating activities", independently matches operating_cash_flow's own
    alias list via match_metric() the same way row 30 does, but has no
    numeric content and is not a genuine candidate row) whose own label
    independently matches (via the same canonical_metrics.match_metric() L2
    already uses) the same canonical metric name -- i.e. every structurally
    plausible row for this metric, not just the one the LLM picked."""
    rows: list[int] = []
    for ri, row in enumerate(table.rows):
        label = table.row_label(ri)
        if label and _has_any_numeric_cell(row) and match_metric(label, statement_type) == metric_name:
            rows.append(ri)
    return rows


def _find_label_row(candidate: CandidateMetric, tables: list[StructuralTable]) -> tuple[Optional[StructuralTable], Optional[int]]:
    """Does candidate.raw_label, verbatim (case/whitespace-insensitive), match
    some DATA row's own label exactly? Used only to detect label/value row
    disagreement -- not a substitute for match_metric()'s semantic matching,
    which is used separately for the competing-rows check.

    Restricted to rows with at least one numeric cell (excludes section-
    header rows) for the same reason _competing_rows_for_metric() is:
    confirmed real and load-bearing during Phase B/C testing -- OFSS's
    current_assets/current_liabilities candidates declare raw_label "Current
    assets"/"Current liabilities", which are each a SECTION HEADER's exact
    text (row 13 / row 39, no values), not the label-less subtotal row that
    actually holds the value (row 21 / row 49). Without this filter, both
    correct candidates were wrongly flagged NEEDS_REVIEW as a false
    "label/value row mismatch" -- the same false positive hit
    investing_cash_flow, whose raw_label echoed its section header ("Cash
    flows from investing activities", row 34) instead of its own row's more
    specific label. A section header can never be the row a value actually
    came from, so it must never win this lookup over a genuine data row."""
    if not candidate.raw_label:
        return None, None
    norm_label = _normalize_row_text(candidate.raw_label)
    if not norm_label:
        return None, None
    for t in tables:
        for ri, row in enumerate(t.rows):
            if _normalize_row_text(t.row_label(ri)) == norm_label and _has_any_numeric_cell(row):
                return t, ri
    return None, None


def validate_one(
    candidate: CandidateMetric, tables: list[StructuralTable]
) -> tuple[str, str, Optional[str], Optional[int], Optional[int]]:
    """
    Returns (verification_state, verification_reason, table_id, row_index, col_index).
    """
    label_table, label_row = _find_label_row(candidate, tables)

    if not tables:
        return ("NEEDS_REVIEW",
                "no structural table available for this page -- row/column identity "
                "unverifiable from structure alone", None, None, None)

    # Scan every table on the page for cells whose parsed value matches the
    # candidate's declared value.
    value_hits: list[tuple[StructuralTable, int, int]] = []
    for t in tables:
        for ri, row in enumerate(t.rows):
            for ci, cell_text in enumerate(row):
                v = parse_value(cell_text)
                if v is not None and _values_close(v, candidate.value):
                    value_hits.append((t, ri, ci))

    if not value_hits:
        # Narrow structural-CONTRADICTION check: raw_label uniquely
        # identifies a row, that row's declared-year column is unambiguous,
        # and it holds a different, real number.
        if label_row is not None:
            candidate_col = None
            for ci2, years2 in label_table.col_years.items():
                if len(years2) == 1 and candidate.year is not None and years2[0] == candidate.year:
                    candidate_col = ci2
                    break
            if candidate_col is not None:
                true_cell_text = label_table.cell(label_row, candidate_col)
                true_val = parse_value(true_cell_text) if true_cell_text else None
                if true_val is not None and not _values_close(true_val, candidate.value):
                    return ("REJECTED",
                            f"structural contradiction: row {label_row} (label matches raw_label "
                            f"verbatim), column for {candidate.year} contains {true_val!r}, not "
                            f"the declared value {candidate.value!r}",
                            label_table.table_id, label_row, candidate_col)
        return ("NEEDS_REVIEW",
                f"declared value {candidate.value!r} not found in any structural table cell "
                f"on this page", None, None, None)

    # Classify each hit by whether its column's header year cleanly matches candidate.year.
    clean_hits: list[tuple[StructuralTable, int, int]] = []
    wrong_year_hits: list[tuple[StructuralTable, int, int, int]] = []
    ambiguous_year_hits: list[tuple[StructuralTable, int, int, list[int]]] = []

    for (t, ri, ci) in value_hits:
        years = t.col_years.get(ci, [])
        if len(years) == 1:
            if candidate.year is not None and years[0] == candidate.year:
                clean_hits.append((t, ri, ci))
            else:
                wrong_year_hits.append((t, ri, ci, years[0]))
        else:
            ambiguous_year_hits.append((t, ri, ci, years))

    if not clean_hits:
        if wrong_year_hits:
            t, ri, ci, actual_year = wrong_year_hits[0]
            return ("NEEDS_REVIEW",
                    f"value found at row {ri} but in the {actual_year} column, not the "
                    f"declared {candidate.year} column", t.table_id, ri, ci)
        t, ri, ci, years = ambiguous_year_hits[0]
        return ("NEEDS_REVIEW",
                f"value found at row {ri} but that column's header maps to "
                f"{years or 'no'} year(s), not unambiguously {candidate.year}",
                t.table_id, ri, ci)

    if len(clean_hits) > 1:
        return ("NEEDS_REVIEW",
                f"declared value+year combination matches {len(clean_hits)} structurally "
                f"distinct rows on this page -- not uniquely identifiable", None, None, None)

    t, ri, ci = clean_hits[0]

    # Label/value row disagreement -- independent of the checks below.
    if label_row is not None and label_table is t and label_row != ri:
        return ("NEEDS_REVIEW",
                f"declared raw_label matches row {label_row}, but the declared value+year "
                f"matches a different row ({ri}) -- label/value row mismatch",
                t.table_id, ri, ci)

    # Subtotal-vs-final-total ambiguity: multiple rows on this table
    # independently match a canonical alias for this metric.
    this_row_matches = match_metric(t.row_label(ri), candidate.statement_type) == candidate.metric_name
    if this_row_matches:
        competing = _competing_rows_for_metric(t, candidate.metric_name, candidate.statement_type)
        if len(competing) > 1:
            last_competing = max(competing)
            if ri != last_competing:
                return ("NEEDS_REVIEW",
                        f"row {ri}'s label matches {candidate.metric_name}, but {len(competing)} "
                        f"rows on this page do (rows {competing}) -- row {ri} is not the last "
                        f"(most likely final-total) of them; likely an intermediate subtotal",
                        t.table_id, ri, ci)
            return ("VERIFIED",
                    f"row {ri} uniquely identified by value+year; column matches declared year "
                    f"{candidate.year}; positionally final among {len(competing)} candidate rows "
                    f"for this metric", t.table_id, ri, ci)

    return ("VERIFIED",
            f"row {ri} uniquely identified by value+year; column matches declared year "
            f"{candidate.year}", t.table_id, ri, ci)


def run_structural_validation(
    candidates: list[CandidateMetric],
    tables: list[StructuralTable],
) -> tuple[list[CandidateMetric], list[RejectionRecord]]:
    """
    Runs L6 over candidates that already passed L2-L5. `tables` is the flat
    union of structural tables across every page selected for this
    statement-type's LLM call (not just a candidate's own `page_no`) --
    necessary because cash_flow calls span 2 pages
    (page_selector.select_pages()) while every candidate from that call
    carries page_no = the first of the two (response_parser.py's existing,
    unmodified behavior); searching only the first page would miss a
    candidate whose true row sits on the continuation page.

    Sets verification_state/verification_reason/table_id/row_index/col_index
    on each candidate in place (Phase A/B fields, safe defaults elsewhere).
    Only candidates hitting the narrow REJECTED path are removed from the
    returned list -- every other L2-L5-accepted candidate is preserved.
    """
    kept: list[CandidateMetric] = []
    l6_rejections: list[RejectionRecord] = []

    for c in candidates:
        state, reason, table_id, row_index, col_index = validate_one(c, tables)

        if state == "REJECTED":
            l6_rejections.append(RejectionRecord(
                doc_id=c.doc_id, statement_type=c.statement_type, metric_name=c.metric_name,
                raw_label=c.raw_label, value=c.value, unit=c.unit, year=c.year, evidence=c.evidence,
                l2_passed=True, l3_passed=True, l4_passed=True, l5_passed=True, l5_degraded=False,
                rejection_reasons=[f"l6_structural_contradiction: {reason}"],
            ))
            continue

        c.verification_state = state
        c.verification_reason = reason
        c.table_id = table_id
        c.row_index = row_index
        c.col_index = col_index
        kept.append(c)

    return kept, l6_rejections
