"""HyDE (Hypothetical Document Embeddings) and query decomposition."""
import json
import logging
from typing import List

from openai import AsyncOpenAI

from app.core.config import settings
from app.services import embedding_service

logger = logging.getLogger(__name__)

_client: AsyncOpenAI = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def hyde_embed(query: str) -> List[float]:
    """
    HyDE: ask the LLM to write a hypothetical answer, then embed *that*.
    Produces an embedding that is closer to the document space than a raw question.
    Falls back to a standard query embedding on any failure.
    """
    try:
        client = _get_client()
        resp = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            max_tokens=256,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a document assistant. Given a question, write a concise, "
                        "factual paragraph (2–4 sentences) that would appear verbatim in a "
                        "document answering this question. Do not reference yourself or the "
                        "question. Just write the answer as if it were document text."
                    ),
                },
                {"role": "user", "content": query},
            ],
        )
        hypothetical_doc = resp.choices[0].message.content.strip()
        logger.debug("HyDE doc (truncated): %s", hypothetical_doc[:100])
        return await embedding_service.embed_query(hypothetical_doc)
    except Exception as exc:
        logger.warning("HyDE failed, falling back to raw query embedding: %s", exc)
        return await embedding_service.embed_query(query)


async def decompose_query(query: str) -> List[str]:
    """
    Break a complex multi-part question into focused independent sub-questions.
    Returns a list; for simple questions returns [query] unchanged.
    """
    try:
        client = _get_client()
        resp = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            max_tokens=256,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a query analyst. Decompose the user's question into the "
                        "minimal set of independent, focused sub-questions whose answers "
                        "together answer the original question fully.\n"
                        "Rules:\n"
                        "- If the question is already simple and atomic, return it unchanged.\n"
                        "- Maximum 4 sub-questions.\n"
                        "- Return a JSON object with key \"questions\" whose value is a list of strings.\n"
                        'Example: {"questions": ["What is X?", "How does Y affect X?"]}'
                    ),
                },
                {"role": "user", "content": query},
            ],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        parsed = json.loads(raw)

        if isinstance(parsed, list):
            sub_queries = parsed
        elif isinstance(parsed, dict):
            # Accept {"questions": [...]} or any first list value
            sub_queries = next(
                (v for v in parsed.values() if isinstance(v, list)), [query]
            )
        else:
            sub_queries = [query]

        sub_queries = [str(q).strip() for q in sub_queries if str(q).strip()]
        if not sub_queries:
            sub_queries = [query]

        logger.debug("Decomposed into %d sub-queries: %s", len(sub_queries), sub_queries)
        return sub_queries
    except Exception as exc:
        logger.warning("Query decomposition failed, using original: %s", exc)
        return [query]
