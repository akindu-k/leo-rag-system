"""OpenAI embedding service with batched requests."""
import asyncio
import logging
from typing import List

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI = None


def get_openai_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


BATCH_SIZE = 100  # OpenAI allows up to 2048 inputs per request; 100 is safe


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a list of texts, batching to avoid rate limits."""
    if not texts:
        return []

    client = get_openai_client()
    all_embeddings: List[List[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        # Strip newlines — OpenAI recommends this
        batch = [t.replace("\n", " ") for t in batch]

        response = await client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=batch,
        )
        batch_embeddings = [item.embedding for item in response.data]
        all_embeddings.extend(batch_embeddings)

        if i + BATCH_SIZE < len(texts):
            await asyncio.sleep(0.1)  # small pause to respect rate limits

    return all_embeddings


async def embed_query(query: str) -> List[float]:
    """Embed a single query string."""
    embeddings = await embed_texts([query.replace("\n", " ")])
    return embeddings[0]
