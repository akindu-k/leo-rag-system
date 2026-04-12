"""Semantic-aware chunking: structure-first, then token-size enforcement."""
import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from app.core.config import settings
from app.services.parsing_service import ParsedDocument, ParsedPage

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    content: str
    chunk_index: int
    page_number: Optional[int]
    section_title: Optional[str]
    token_count: int


def _count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text.split())  # rough fallback


def _split_by_headings(text: str) -> List[tuple[Optional[str], str]]:
    """Split text on markdown-style headings. Returns [(title, body), ...]."""
    sections: List[tuple[Optional[str], str]] = []
    # Match # Heading or ## Heading patterns
    pattern = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    last_end = 0
    last_title = None

    for match in pattern.finditer(text):
        body = text[last_end:match.start()].strip()
        if body:
            sections.append((last_title, body))
        last_title = match.group(2).strip()
        last_end = match.end()

    remaining = text[last_end:].strip()
    if remaining:
        sections.append((last_title, remaining))

    if not sections:
        sections.append((None, text))

    return sections


def _split_into_token_chunks(text: str, title: Optional[str], page_number: Optional[int],
                              max_tokens: int, overlap: int) -> List[dict]:
    """Split a block of text into token-sized chunks with overlap."""
    words = text.split()
    if not words:
        return []

    chunks = []
    start = 0
    while start < len(words):
        # Grab a window of words and check token count
        end = min(start + max_tokens * 2, len(words))  # rough upper bound
        window = words[start:end]
        window_text = " ".join(window)
        tokens = _count_tokens(window_text)

        if tokens <= max_tokens:
            chunks.append({
                "content": window_text,
                "page_number": page_number,
                "section_title": title,
                "token_count": tokens,
            })
            break

        # Binary search for the right end
        lo, hi = start, end
        while lo < hi - 1:
            mid = (lo + hi) // 2
            candidate = " ".join(words[start:mid])
            if _count_tokens(candidate) <= max_tokens:
                lo = mid
            else:
                hi = mid

        chunk_words = words[start:lo]
        chunk_text = " ".join(chunk_words)
        chunks.append({
            "content": chunk_text,
            "page_number": page_number,
            "section_title": title,
            "token_count": _count_tokens(chunk_text),
        })

        # Move forward with overlap
        overlap_words = max(0, lo - overlap)
        start = overlap_words if overlap_words > start else lo

    return chunks


def chunk_document(parsed: ParsedDocument) -> List[Chunk]:
    """
    Chunking pipeline:
    1. Split each page by headings/structure.
    2. Enforce token-size limits with overlap.
    3. Tag every chunk with metadata.
    """
    max_tokens = settings.CHUNK_SIZE
    overlap = settings.CHUNK_OVERLAP
    raw_chunks: List[dict] = []

    for page in parsed.pages:
        sections = _split_by_headings(page.text)
        for title, body in sections:
            if not body.strip():
                continue
            token_count = _count_tokens(body)
            if token_count <= max_tokens:
                raw_chunks.append({
                    "content": body,
                    "page_number": page.page_number,
                    "section_title": title,
                    "token_count": token_count,
                })
            else:
                sub_chunks = _split_into_token_chunks(body, title, page.page_number, max_tokens, overlap)
                raw_chunks.extend(sub_chunks)

    return [
        Chunk(
            content=rc["content"],
            chunk_index=i,
            page_number=rc["page_number"],
            section_title=rc["section_title"],
            token_count=rc["token_count"],
        )
        for i, rc in enumerate(raw_chunks)
        if rc["content"].strip()
    ]
