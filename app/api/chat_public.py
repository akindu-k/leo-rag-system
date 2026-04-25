"""Stateless public chat endpoint — no auth, no session management.
Used by the mobile app. History is passed in the request body by the client.
"""
import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.schemas.chat import PublicChatRequest
from app.services import (
    answer_service,
    query_service,
    reranking_service,
    retrieval_service,
)
from app.core.config import settings

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)


@router.post("")
async def public_chat(payload: PublicChatRequest):
    """
    Stateless RAG chat — no auth, no session required.

    Send a message and receive a streaming answer via Server-Sent Events.

    Request body:
      {
        "content": "Your question here",
        "history": [
          {"role": "user", "content": "previous question"},
          {"role": "assistant", "content": "previous answer"}
        ]
      }

    SSE events:
      {"type": "token",   "content": "..."}
      {"type": "done",    "citations": [...], "grounding": {...}}
      {"type": "error",   "message": "..."}
    """
    user_message = payload.content.strip()
    if not user_message:
        async def _empty():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Message content cannot be empty.'})}\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")

    history = [{"role": m.role, "content": m.content} for m in payload.history]

    async def _stream() -> AsyncGenerator[str, None]:
        try:
            # ── 1. Query decomposition ─────────────────────────────────────
            sub_queries = await query_service.decompose_query(user_message)

            # ── 2. HyDE embeddings (parallel) ─────────────────────────────
            sub_embeddings = list(
                await asyncio.gather(*[query_service.hyde_embed(q) for q in sub_queries])
            )

            # ── 3. Hybrid retrieval — all documents accessible (no auth) ──
            candidates = await retrieval_service.search_chunks_hybrid(
                query_texts=sub_queries,
                query_embeddings=sub_embeddings,
                accessible_document_ids=None,  # None = no filter = all documents
            )

            # ── 4. Rerank ─────────────────────────────────────────────────
            if settings.RERANKER_ENABLED:
                reranked = await reranking_service.rerank(user_message, candidates)
            else:
                reranked = candidates[: settings.RERANKER_TOP_N]

            # ── 5. Stream answer ──────────────────────────────────────────
            full_answer: list[str] = []
            async for token in answer_service.stream_answer(user_message, reranked, history):
                full_answer.append(token)
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

            answer_text = "".join(full_answer)

            # ── 6. Grounding validation ───────────────────────────────────
            from app.services import validation_service
            grounding = await validation_service.validate_grounding(answer_text, reranked)

            # ── 7. Build citations (no DB save — returned directly) ───────
            citations = [
                {
                    "document_title": c.get("document_title"),
                    "file_name": c.get("file_name"),
                    "page_number": c.get("page_number"),
                    "section_title": c.get("section_title"),
                    "excerpt": c.get("content", "")[:300],
                    "relevance_score": c.get("rerank_score") or c.get("score"),
                }
                for c in reranked
            ]

            yield f"data: {json.dumps({'type': 'done', 'citations': citations, 'grounding': grounding})}\n\n"

        except Exception as e:
            logger.error("Public chat stream error: %s", e, exc_info=True)
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
