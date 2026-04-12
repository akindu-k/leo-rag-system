"""Qdrant vector search with permission-aware filtering."""
import logging
import uuid
from typing import List, Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchAny,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

_qdrant_client: Optional[AsyncQdrantClient] = None


def get_qdrant_client() -> AsyncQdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = AsyncQdrantClient(url=settings.QDRANT_URL, check_compatibility=False)
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

    query_filter = None
    if accessible_document_ids is not None:
        # Empty list → user has access to nothing
        if not accessible_document_ids:
            return []
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="document_id",
                    match=MatchAny(any=accessible_document_ids),
                )
            ]
        )

    # qdrant-client 1.7+ replaced client.search() with client.query_points()
    response = await client.query_points(
        collection_name=settings.QDRANT_COLLECTION,
        query=query_embedding,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )
    results = response.points

    return [
        {
            "score": hit.score,
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
        for hit in results
    ]
