"""
extraction/llm/page_selector.py
Page selection policy for LLM-based extraction (Fix 2 logic).

Responsibilities:
  - Given classifier output, find which pages belong to each statement type
  - Apply the selection policy: highest-scoring page only for balance_sheet
    and income_statement; highest-scoring page + continuation for cash_flow
  - Concatenate selected page text up to MAX_CHARS_PER_CALL

Selection policy rationale (from PoC benchmarking Fix 2):
  - Sending multiple classified pages for the same statement type includes
    standalone statements and notes pages, which contaminate extracted values.
  - For cash_flow, the financing activities section and closing cash balance
    appear on the page immediately following the statement title page, without
    a statement title of their own. That continuation page must be included.
  - For balance_sheet and income_statement, a single well-chosen page
    contains all required data — "well-chosen" means the highest-scoring
    page per classifier.PageClassification.score (see classifier.py), not
    simply the first one classified. The classifier's title/density gates
    can still produce false positives (e.g. a narrative page that mentions
    a statement keyword, or a Notes sub-heading); picking by score rather
    than page order is what makes selection resilient to those.
"""

from __future__ import annotations

from extraction.classifier import PageClassification
from extraction.llm.config import MAX_CHARS_PER_CALL


def select_pages(
    page_classes: list[PageClassification],
    stmt_type: str,
) -> list[int]:
    """
    Return the effective page numbers to send to the LLM for `stmt_type`.

    For balance_sheet and income_statement: only the highest-scoring
    classified page (see classifier.PageClassification.score). For
    cash_flow: the highest-scoring page plus the immediately following
    page (which contains financing activities without its own title).

    Ties (equal score) are broken by page order — the earliest page wins —
    for full determinism.

    Parameters
    ----------
    page_classes : list[PageClassification]
        Full classification output from classifier.classify_statement_pages().
    stmt_type : str
        One of "balance_sheet", "income_statement", "cash_flow".

    Returns
    -------
    list[int]
        Page numbers (1-indexed) to include in the LLM context.
        Empty list if no pages of this type were classified.
    """
    matching = [pc for pc in page_classes if pc.statement_type == stmt_type]

    if not matching:
        return []

    best = max(matching, key=lambda pc: (pc.score, -pc.page_no))
    primary = best.page_no

    if stmt_type == "cash_flow":
        return [primary, primary + 1]

    return [primary]


def get_page_block(
    pages: dict[int, str],
    page_nos: list[int],
    max_chars: int = MAX_CHARS_PER_CALL,
) -> str:
    """
    Concatenate text from the given pages up to max_chars total.
    Each page is prefixed with a separator line for LLM context clarity.
    Only the explicitly provided page numbers are used — no sequential neighbors.

    Parameters
    ----------
    pages : dict[int, str]
        Mapping of page_no (1-indexed) → raw text from the PDF.
    page_nos : list[int]
        Page numbers to include, in order.
    max_chars : int
        Hard character cap on the returned block.

    Returns
    -------
    str
        Concatenated text, truncated at max_chars.
    """
    if not page_nos:
        return ""

    block = ""
    for pno in page_nos:
        if pno in pages:
            block += f"\n--- Page {pno} ---\n" + pages[pno]
        if len(block) >= max_chars:
            break

    return block[:max_chars]
