"""
text_chunker.py
Stage 4 — Narrative text chunking + FAISS index for the RAG pipeline.

RULE: Financial statement pages (balance_sheet, income_statement, cash_flow)
are EXCLUDED. Only narrative pages (mda, notes, risk_factors, other) are chunked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np

from extraction.canonical_metrics import SECTION_KEYWORDS
from extraction.classifier import PageClassification


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHUNK_SIZE    = 800    # characters (≈130–160 words per chunk)
CHUNK_OVERLAP = 150    # characters — enough to bridge cross-sentence context
SEPARATORS    = ["\n\n", "\n", ". ", " "]

EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM        = 384  # MiniLM-L6-v2 output dimension

# Pages with these types go to metric_extractor, NOT here
_FINANCIAL_TYPES = {"balance_sheet", "income_statement", "cash_flow"}


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class TextChunk:
    chunk_id:      str    # f"{doc_id}_{page_no}_{chunk_index}"
    doc_id:        str
    text:          str
    page_no:       int
    section_title: str    # nearest heading above this chunk, or ""
    section_type:  str    # "mda" | "risk_factors" | "notes" | "liquidity" | "other"
    char_start:    int    # character offset in the concatenated narrative text
    char_end:      int


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------

def _classify_section(heading: str) -> str:
    """Map a heading string to a section_type via SECTION_KEYWORDS."""
    h = heading.lower()
    for section, keywords in SECTION_KEYWORDS.items():
        if section == "other":
            continue
        if any(kw in h for kw in keywords):
            return section
    return "other"


def _looks_like_heading(line: str) -> bool:
    """Heuristic: short, starts with capital, no excessive digits."""
    s = line.strip()
    if not s or len(s) > 120:
        return False
    words = s.split()
    if len(words) > 14:
        return False
    digit_count = sum(1 for c in s if c.isdigit())
    if digit_count > len(s) * 0.3:  # more than 30% digits → table row, not heading
        return False
    return s[0].isupper() or s.isupper()


def _extract_heading_from_text(text: str) -> str:
    """Return the first heading-like line from a text block, or ''."""
    for line in text.splitlines():
        if _looks_like_heading(line):
            return line.strip()
    return ""


# ---------------------------------------------------------------------------
# Function 1 — chunk_document
# ---------------------------------------------------------------------------

def chunk_document(
    pages_raw_text: list[str],
    page_classifications: list[PageClassification],
    doc_id: str,
) -> list[TextChunk]:
    """
    Split narrative pages into TextChunk objects.

    Financial statement pages (balance_sheet, income_statement, cash_flow)
    are skipped — they belong to metric_extractor.

    Steps:
      1. Filter to narrative pages only
      2. Concatenate with tracked page boundaries
      3. Split with RecursiveCharacterTextSplitter
      4. Assign page_no, section_type, section_title to each chunk
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter  # lazy import

    # Build page classification map
    class_map: dict[int, str] = {c.page_no: c.statement_type for c in page_classifications}

    # Collect narrative pages with their original page numbers
    narrative_pages: list[tuple[int, str]] = []
    for idx, page_text in enumerate(pages_raw_text):
        page_no = idx + 1
        stmt_type = class_map.get(page_no, "other")
        if stmt_type in _FINANCIAL_TYPES:
            continue  # skip — goes to metric_extractor
        if not page_text.strip():
            continue
        narrative_pages.append((page_no, page_text))

    if not narrative_pages:
        return []

    # Build concatenated text with cumulative page-boundary tracking
    page_boundaries: list[tuple[int, int, int]] = []  # (page_no, char_start, char_end)
    parts: list[str] = []
    offset = 0
    for page_no, text in narrative_pages:
        start = offset
        parts.append(text)
        offset += len(text)
        page_boundaries.append((page_no, start, offset))
        parts.append("\n")   # separator between pages
        offset += 1

    full_text = "".join(parts)

    # Build a running section-heading tracker for the full text
    # Scan line by line, updating current_heading whenever we see a heading-like line
    heading_events: list[tuple[int, str]] = []  # (char_offset, heading_text)
    pos = 0
    for line in full_text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if _looks_like_heading(stripped):
            heading_events.append((pos, stripped.strip()))
        pos += len(line)

    def _heading_at(char_pos: int) -> str:
        """Return the most recent heading before char_pos."""
        result = ""
        for hpos, htext in heading_events:
            if hpos <= char_pos:
                result = htext
            else:
                break
        return result

    def _page_no_at(char_pos: int) -> int:
        """Return the page_no that contains char_pos."""
        for pno, start, end in page_boundaries:
            if start <= char_pos < end:
                return pno
        return page_boundaries[-1][0] if page_boundaries else 1

    # Split
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=SEPARATORS,
        length_function=len,
    )
    raw_chunks = splitter.split_text(full_text)

    # Build TextChunk objects — track char position with a running search pointer
    chunks: list[TextChunk] = []
    search_from = 0
    for i, chunk_text in enumerate(raw_chunks):
        # Find this chunk's start position in full_text
        pos = full_text.find(chunk_text, search_from)
        if pos == -1:
            # fallback: try from beginning (shouldn't happen normally)
            pos = full_text.find(chunk_text, 0)
        char_start = max(pos, 0)
        char_end   = char_start + len(chunk_text)

        # Advance search pointer (overlap means we don't move past char_end)
        if pos >= 0:
            search_from = max(search_from, char_start + max(1, CHUNK_SIZE - CHUNK_OVERLAP))

        page_no       = _page_no_at(char_start)
        heading       = _heading_at(char_start)
        section_type  = _classify_section(heading) if heading else _classify_section(chunk_text[:200])

        # If section_type is still "other", check the page-level classification
        if section_type == "other":
            page_stmt = class_map.get(page_no, "other")
            if page_stmt in ("mda", "notes", "risk_factors", "liquidity"):
                section_type = page_stmt

        chunks.append(TextChunk(
            chunk_id=f"{doc_id}_{page_no}_{i}",
            doc_id=doc_id,
            text=chunk_text,
            page_no=page_no,
            section_title=heading,
            section_type=section_type,
            char_start=char_start,
            char_end=char_end,
        ))

    return chunks


# ---------------------------------------------------------------------------
# Function 2 — build_faiss_index
# ---------------------------------------------------------------------------

def build_faiss_index(
    chunks: list[TextChunk],
    output_dir: Path,
    doc_id: str,
) -> tuple:
    """
    Embed chunks, build a FAISS IndexFlatIP (cosine via normalized IP), save to disk.
    Returns (faiss_index, chunks).
    """
    import faiss                                          # lazy import
    from sentence_transformers import SentenceTransformer

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    model = SentenceTransformer(EMBED_MODEL_NAME)
    texts = [c.text for c in chunks]

    # normalize_embeddings=True → L2-normalized → dot product = cosine similarity
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    embeddings = embeddings.astype(np.float32)

    index = faiss.IndexFlatIP(EMBED_DIM)
    index.add(embeddings)

    # Save index
    index_path = output_dir / f"{doc_id}_faiss.index"
    faiss.write_index(index, str(index_path))

    # Save chunks
    chunks_path = output_dir / f"{doc_id}_chunks.jsonl"
    with open(chunks_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")

    print(f"[chunker] Saved {len(chunks)} chunks → {chunks_path.name}")
    print(f"[chunker] Saved FAISS index → {index_path.name}")

    return index, chunks


# ---------------------------------------------------------------------------
# Function 3 — load_faiss_index
# ---------------------------------------------------------------------------

def load_faiss_index(
    output_dir: Path,
    doc_id: str,
) -> tuple:
    """
    Load FAISS index and chunks list from disk.
    Returns (faiss_index, list[TextChunk]).
    """
    import faiss

    output_dir = Path(output_dir)
    index_path  = output_dir / f"{doc_id}_faiss.index"
    chunks_path = output_dir / f"{doc_id}_chunks.jsonl"

    if not index_path.exists():
        raise FileNotFoundError(f"FAISS index not found: {index_path}")
    if not chunks_path.exists():
        raise FileNotFoundError(f"Chunks file not found: {chunks_path}")

    index = faiss.read_index(str(index_path))

    chunks: list[TextChunk] = []
    with open(chunks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(TextChunk(**json.loads(line)))

    return index, chunks


# ---------------------------------------------------------------------------
# Function 4 — search_chunks
# ---------------------------------------------------------------------------

def search_chunks(
    query: str,
    index,
    chunks: list[TextChunk],
    embed_model,
    k: int = 4,
) -> list[TextChunk]:
    """
    Embed and L2-normalize the query, search the FAISS index, return top-k chunks.
    embed_model must be a loaded SentenceTransformer instance.
    """
    import numpy as np

    query_emb = embed_model.encode([query], normalize_embeddings=True)
    query_emb = query_emb.astype(np.float32)

    distances, indices = index.search(query_emb, k)
    return [chunks[i] for i in indices[0] if 0 <= i < len(chunks)]
