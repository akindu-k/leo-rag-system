"""Build and persist citations from retrieved chunks."""
import uuid
import logging
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import AnswerCitation

logger = logging.getLogger(__name__)


def build_citations_from_chunks(chunks: List[dict]) -> List[dict]:
    """Convert reranked chunks into citation dicts (before DB save)."""
    seen = set()
    citations = []
    for chunk in chunks:
        doc_id = chunk.get("document_id")
        page = chunk.get("page_number")
        dedup_key = (doc_id, page, chunk.get("chunk_index"))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        citations.append({
            "document_id": doc_id,
            "document_version_id": chunk.get("document_version_id"),
            "document_title": chunk.get("document_title"),
            "file_name": chunk.get("file_name"),
            "page_number": page,
            "section_title": chunk.get("section_title"),
            "excerpt": chunk.get("content", "")[:400],
            "relevance_score": chunk.get("rerank_score") or chunk.get("score"),
        })
    return citations


async def save_citations(
    db: AsyncSession,
    message_id: uuid.UUID,
    citations: List[dict],
) -> List[AnswerCitation]:
    """Persist citations linked to an assistant message."""
    records = []
    for c in citations:
        record = AnswerCitation(
            id=uuid.uuid4(),
            message_id=message_id,
            document_id=uuid.UUID(c["document_id"]) if c.get("document_id") else None,
            document_version_id=uuid.UUID(c["document_version_id"]) if c.get("document_version_id") else None,
            document_title=c.get("document_title"),
            file_name=c.get("file_name"),
            page_number=c.get("page_number"),
            section_title=c.get("section_title"),
            excerpt=c.get("excerpt"),
            relevance_score=c.get("relevance_score"),
        )
        records.append(record)

    db.add_all(records)
    await db.flush()
    return records
