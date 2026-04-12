"""Cross-encoder reranking using sentence-transformers."""
import asyncio
import logging
from typing import List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Lazy-loaded model — only downloaded / loaded once on first use
_reranker = None
_reranker_lock = asyncio.Lock()


def _load_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        logger.info(f"Loading reranker model '{settings.RERANKER_MODEL}'...")
        _reranker = CrossEncoder(settings.RERANKER_MODEL)
        logger.info("Reranker model loaded.")
    return _reranker


async def rerank(query: str, chunks: List[dict], top_n: Optional[int] = None) -> List[dict]:
    """
    Rerank chunks using a cross-encoder.
    Returns top_n chunks sorted by reranker score (descending).
    """
    if not chunks:
        return []

    top_n = top_n or settings.RERANKER_TOP_N

    async with _reranker_lock:
        loop = asyncio.get_event_loop()
        model = await loop.run_in_executor(None, _load_reranker)

    pairs = [[query, chunk["content"]] for chunk in chunks]

    loop = asyncio.get_event_loop()
    scores: List[float] = await loop.run_in_executor(None, lambda: model.predict(pairs).tolist())

    for chunk, score in zip(chunks, scores):
        chunk["rerank_score"] = score

    reranked = sorted(chunks, key=lambda c: c["rerank_score"], reverse=True)
    return reranked[:top_n]
