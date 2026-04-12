"""Streaming chat endpoint — the core RAG flow."""
import uuid
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.chat import ChatMessageCreate
from app.services import (
    embedding_service,
    retrieval_service,
    reranking_service,
    answer_service,
    citation_service,
    session_service,
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
    Send a message and receive a streaming answer (SSE).
    The response is a text/event-stream with JSON events:
      - {"type": "token", "content": "..."}
      - {"type": "done", "message_id": "uuid", "citations": [...]}
      - {"type": "error", "message": "..."}
    """
    # ── Verify session belongs to user ────────────────────────────────────
    from sqlalchemy import select
    from app.models.chat import ChatSession

    sess_result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
    )
    session = sess_result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    user_message_content = payload.content.strip()
    if not user_message_content:
        raise HTTPException(status_code=422, detail="Message content cannot be empty")

    # ── Save user message ─────────────────────────────────────────────────
    user_msg = await session_service.add_message(db, session_id, "user", user_message_content)

    # ── Auto-title session on first message ───────────────────────────────
    if session.title == "New Chat":
        auto_title = user_message_content[:60] + ("..." if len(user_message_content) > 60 else "")
        await session_service.update_session_title(db, session_id, auto_title)

    await db.commit()

    async def _stream() -> AsyncGenerator[str, None]:
        try:
            # ── 1. Resolve accessible documents ───────────────────────────
            accessible_ids = await get_accessible_document_ids(db, current_user)

            # ── 2. Embed query ─────────────────────────────────────────────
            query_embedding = await embedding_service.embed_query(user_message_content)

            # ── 3. Retrieve candidates ────────────────────────────────────
            candidates = await retrieval_service.search_chunks(
                query_embedding=query_embedding,
                accessible_document_ids=accessible_ids,
                top_k=None,
            )

            # ── 4. Rerank ─────────────────────────────────────────────────
            reranked = await reranking_service.rerank(user_message_content, candidates)

            # ── 5. Load history ───────────────────────────────────────────
            history = await session_service.get_session_history(db, session_id)
            # Exclude the message we just added (it's the last one)
            history = history[:-1] if history and history[-1]["role"] == "user" else history

            # ── 6. Stream answer ──────────────────────────────────────────
            full_answer = []
            async for token in answer_service.stream_answer(user_message_content, reranked, history):
                full_answer.append(token)
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

            answer_text = "".join(full_answer)

            # ── 7. Save assistant message + citations ─────────────────────
            async with db:
                assistant_msg = await session_service.add_message(db, session_id, "assistant", answer_text)
                citations_data = citation_service.build_citations_from_chunks(reranked)
                saved_citations = await citation_service.save_citations(db, assistant_msg.id, citations_data)
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

            yield f"data: {json.dumps({'type': 'done', 'message_id': str(assistant_msg.id), 'citations': citation_out})}\n\n"

        except Exception as e:
            logger.error(f"Chat stream error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': 'An internal error occurred. Please try again.'})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable Nginx buffering
            "Connection": "keep-alive",
        },
    )
