"""Streaming chat endpoint — the full advanced RAG flow."""
import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.chat import ChatSession
from app.models.user import User
from app.schemas.chat import ChatMessageCreate
from app.services import (
    answer_service,
    citation_service,
    embedding_service,
    query_service,
    reranking_service,
    retrieval_service,
    session_service,
    validation_service,
)
from app.utils.permissions import get_accessible_document_ids

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)


@router.post("/{session_id}/messages")
async def chat(
    session_id: uuid.UUID,
    payload: ChatMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Send a message and receive a streaming answer via Server-Sent Events.

    Events emitted:
      {"type": "token",   "content": "..."}
      {"type": "done",    "message_id": "uuid", "citations": [...], "grounding": {...}}
      {"type": "error",   "message": "..."}
    """
    # ── Verify session ownership ───────────────────────────────────────────
    sess_result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == current_user.id,
        )
    )
    session = sess_result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    user_message_content = payload.content.strip()
    if not user_message_content:
        raise HTTPException(status_code=422, detail="Message content cannot be empty")

    # ── Persist user message ──────────────────────────────────────────────
    user_msg = await session_service.add_message(db, session_id, "user", user_message_content)

    if session.title == "New Chat":
        auto_title = user_message_content[:60] + ("..." if len(user_message_content) > 60 else "")
        await session_service.update_session_title(db, session_id, auto_title)

    await db.commit()

    async def _stream() -> AsyncGenerator[str, None]:
        try:
            # ── 1. Resolve accessible documents ───────────────────────────
            accessible_ids = await get_accessible_document_ids(db, current_user)

            # ── 2. Query decomposition ─────────────────────────────────────
            # Break complex questions into focused sub-queries
            sub_queries = await query_service.decompose_query(user_message_content)

            # ── 3. HyDE embeddings (parallel) ─────────────────────────────
            # Embed a *hypothetical answer* for each sub-query — pulls the
            # embedding toward the document space rather than the question space
            sub_embeddings = list(
                await asyncio.gather(*[query_service.hyde_embed(q) for q in sub_queries])
            )

            # ── 4. Hybrid retrieval with RRF ──────────────────────────────
            # Dense vector search + full-text keyword search merged via RRF
            candidates = await retrieval_service.search_chunks_hybrid(
                query_texts=sub_queries,
                query_embeddings=sub_embeddings,
                accessible_document_ids=accessible_ids,
            )

            # ── 5. Cross-encoder reranking (skipped if RERANKER_ENABLED=false) ──
            from app.core.config import settings as _settings
            if _settings.RERANKER_ENABLED:
                reranked = await reranking_service.rerank(user_message_content, candidates)
            else:
                reranked = candidates[: _settings.RERANKER_TOP_N]

            # ── 6. Load conversation history ──────────────────────────────
            history = await session_service.get_session_history(db, session_id)
            # Exclude the user message we just saved
            if history and history[-1]["role"] == "user":
                history = history[:-1]

            # ── 7. Stream answer tokens ───────────────────────────────────
            full_answer: list[str] = []
            async for token in answer_service.stream_answer(user_message_content, reranked, history):
                full_answer.append(token)
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

            answer_text = "".join(full_answer)

            # ── 8. Grounding validation (LLM-as-judge) ────────────────────
            # Runs after streaming so it doesn't block the user-visible response
            grounding = await validation_service.validate_grounding(answer_text, reranked)

            # ── 9. Persist assistant message + citations ──────────────────
            async with db:
                assistant_msg = await session_service.add_message(
                    db, session_id, "assistant", answer_text
                )
                citations_data = citation_service.build_citations_from_chunks(reranked)
                saved_citations = await citation_service.save_citations(
                    db, assistant_msg.id, citations_data
                )
                await db.commit()

            citation_out = [
                {
                    "id": str(c.id),
                    "document_id": str(c.document_id),
                    "document_title": c.document_title,
                    "file_name": c.file_name,
                    "page_number": c.page_number,
                    "section_title": c.section_title,
                    "excerpt": c.excerpt,
                    "relevance_score": c.relevance_score,
                }
                for c in saved_citations
            ]

            yield f"data: {json.dumps({'type': 'done', 'message_id': str(assistant_msg.id), 'citations': citation_out, 'grounding': grounding})}\n\n"

        except Exception as e:
            logger.error("Chat stream error: %s", e, exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': 'An internal error occurred. Please try again.'})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
