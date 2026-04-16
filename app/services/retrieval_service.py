"""Qdrant vector search with permission-aware filtering and hybrid retrieval."""
import logging
from typing import Dict, List, Optional, Tuple

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchText,
    PointStruct,
    VectorParams,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

_qdrant_client: Optional[AsyncQdrantClient] = None

# RRF constant — higher k = flatter score distribution
_RRF_K = 60


def get_qdrant_client() -> AsyncQdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
            check_compatibility=False,
        )
    return _qdrant_client


async def init_qdrant_collection():
    """Create the Qdrant collection if it does not exist."""
    client = get_qdrant_client()
    collections = await client.get_collections()
    existing = [c.name for c in collections.collections]

    if settings.QDRANT_COLLECTION not in existing:
        await client.create_collection(
            collection_name=settings.QDRANT_COLLECTION,
            vectors_config=VectorParams(
                size=settings.QDRANT_VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )
        logger.info(f"Created Qdrant collection '{settings.QDRANT_COLLECTION}'")
    else:
        logger.info(f"Qdrant collection '{settings.QDRANT_COLLECTION}' already exists.")

    # Ensure full-text index exists on the content field (idempotent)
    try:
        await client.create_payload_index(
            collection_name=settings.QDRANT_COLLECTION,
            field_name="content",
            field_schema="text",
        )
        logger.info("Qdrant full-text index on 'content' ready.")
    except Exception as exc:
        # Index may already exist — Qdrant raises an error but that's fine
        logger.debug("Payload index creation skipped (may already exist): %s", exc)


async def upsert_chunks_to_qdrant(points: List[dict]):
    """Batch-upsert chunk vectors into Qdrant."""
    client = get_qdrant_client()
    qdrant_points = [
        PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
        for p in points
    ]
    await client.upsert(collection_name=settings.QDRANT_COLLECTION, points=qdrant_points)


async def delete_document_vectors(document_id: str):
    """Remove all vectors for a given document from Qdrant."""
    client = get_qdrant_client()
    await client.delete(
        collection_name=settings.QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="document_id", match=MatchAny(any=[document_id]))]
        ),
    )


def _build_permission_filter(accessible_document_ids: Optional[List[str]]) -> Optional[Filter]:
    if accessible_document_ids is None:
        return None
    return Filter(
        must=[FieldCondition(key="document_id", match=MatchAny(any=accessible_document_ids))]
    )


def _hit_to_dict(hit, score: float) -> dict:
    return {
        "score": score,
        "content": hit.payload.get("content", ""),
        "document_id": hit.payload.get("document_id"),
        "document_version_id": hit.payload.get("document_version_id"),
        "document_title": hit.payload.get("document_title"),
        "file_name": hit.payload.get("file_name"),
        "page_number": hit.payload.get("page_number"),
        "section_title": hit.payload.get("section_title"),
        "chunk_index": hit.payload.get("chunk_index"),
        "qdrant_point_id": str(hit.id),
    }


def _rrf_merge(
    ranked_lists: List[List[Tuple[str, dict]]],
    k: int = _RRF_K,
    total: int = None,
) -> List[dict]:
    """
    Reciprocal Rank Fusion.
    Each element of ranked_lists is a list of (point_id, chunk_dict) pairs
    already ordered by descending relevance for that source.
    Returns a deduplicated list sorted by RRF score (best first).
    """
    scores: Dict[str, float] = {}
    chunks: Dict[str, dict] = {}

    for ranked in ranked_lists:
        for rank, (pid, chunk) in enumerate(ranked, start=1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
            if pid not in chunks:
                chunks[pid] = chunk

    ordered = sorted(scores.keys(), key=lambda pid: scores[pid], reverse=True)
    if total:
        ordered = ordered[:total]

    result = []
    for pid in ordered:
        c = dict(chunks[pid])
        c["score"] = scores[pid]
        result.append(c)
    return result


async def search_chunks(
    query_embedding: List[float],
    accessible_document_ids: Optional[List[str]],
    top_k: int = None,
) -> List[dict]:
    """
    Dense semantic retrieval with optional permission filter.
    Returns a list of dicts: {score, content, metadata...}
    """
    client = get_qdrant_client()
    top_k = top_k or settings.RETRIEVAL_TOP_K

    if accessible_document_ids is not None and not accessible_document_ids:
        return []

    query_filter = _build_permission_filter(accessible_document_ids)

    response = await client.query_points(
        collection_name=settings.QDRANT_COLLECTION,
        query=query_embedding,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )

    return [_hit_to_dict(hit, hit.score) for hit in response.points]


async def search_chunks_hybrid(
    query_texts: List[str],
    query_embeddings: List[List[float]],
    accessible_document_ids: Optional[List[str]],
    top_k: int = None,
) -> List[dict]:
    """
    Hybrid retrieval: dense vector search + full-text keyword search, merged
    with Reciprocal Rank Fusion (RRF) across all sub-queries.

    query_texts      — one text string per sub-query (used for keyword search)
    query_embeddings — one embedding per sub-query (used for dense search)
    """
    client = get_qdrant_client()
    top_k = top_k or settings.RETRIEVAL_TOP_K
    fetch_k = top_k * 2  # over-fetch before RRF trim

    if accessible_document_ids is not None and not accessible_document_ids:
        return []

    perm_filter = _build_permission_filter(accessible_document_ids)
    all_ranked: List[List[Tuple[str, dict]]] = []

    for query_text, query_embedding in zip(query_texts, query_embeddings):
        # ── Dense leg ─────────────────────────────────────────────────────
        dense_resp = await client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=query_embedding,
            query_filter=perm_filter,
            limit=fetch_k,
            with_payload=True,
        )
        dense_ranked: List[Tuple[str, dict]] = [
            (str(hit.id), _hit_to_dict(hit, hit.score))
            for hit in dense_resp.points
        ]

        # ── Keyword leg ───────────────────────────────────────────────────
        kw_conditions = [FieldCondition(key="content", match=MatchText(text=query_text))]
        if perm_filter:
            kw_conditions.extend(perm_filter.must or [])

        kw_filter = Filter(must=kw_conditions)

        keyword_points, _ = await client.scroll(
            collection_name=settings.QDRANT_COLLECTION,
            scroll_filter=kw_filter,
            limit=fetch_k,
            with_payload=True,
            with_vectors=False,
        )
        # Keyword results have no relevance score; assign uniform score=1.0
        keyword_ranked: List[Tuple[str, dict]] = [
            (str(pt.id), _hit_to_dict(pt, 1.0))
            for pt in keyword_points
        ]

        all_ranked.append(dense_ranked)
        if keyword_ranked:
            all_ranked.append(keyword_ranked)

    return _rrf_merge(all_ranked, total=top_k)
