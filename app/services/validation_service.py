"""Grounding validation — LLM-as-judge that audits the answer against source chunks."""
import json
import logging
from typing import List, Optional

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def validate_grounding(
    answer: str,
    chunks: List[dict],
) -> dict:
    """
    Ask an LLM to judge whether every factual claim in *answer* is directly
    supported by the provided *chunks* (top-5 used to keep prompt short).

    Returns a dict:
      {
        "grounded": bool | None,      # None if validation itself failed
        "confidence": float | None,   # 0.0 – 1.0
        "issues": str | None,         # description of unsupported claims, or None
      }
    """
    if not answer.strip():
        return {"grounded": False, "confidence": 0.0, "issues": "Empty answer."}
    if not chunks:
        return {"grounded": False, "confidence": 0.0, "issues": "No source chunks provided."}

    # Build a compact context block (cap each excerpt to avoid huge prompts)
    context_block = "\n\n".join(
        f"[Source {i + 1}] {c.get('document_title', 'Unknown')}, "
        f"p.{c.get('page_number', '?')}:\n{c.get('content', '')[:400]}"
        for i, c in enumerate(chunks[:5])
    )

    prompt = (
        "You are a strict grounding auditor.\n\n"
        "SOURCE DOCUMENTS:\n"
        f"{context_block}\n\n"
        "ANSWER TO AUDIT:\n"
        f"{answer}\n\n"
        "Task: determine whether every factual claim in the ANSWER is directly "
        "supported by the SOURCE DOCUMENTS above.\n\n"
        "Respond with a JSON object containing:\n"
        '  "grounded": true if fully supported, false if any claim is unsupported\n'
        '  "confidence": a float 0.0–1.0 reflecting your certainty\n'
        '  "issues": null if fully grounded, otherwise a short string listing unsupported claims\n\n'
        "Return only valid JSON, nothing else."
    )

    try:
        client = _get_client()
        resp = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            max_tokens=256,
            messages=[
                {
                    "role": "system",
                    "content": "You are a strict grounding auditor. Respond only with valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content.strip())
        return {
            "grounded": bool(result.get("grounded", False)),
            "confidence": float(result.get("confidence", 0.5)),
            "issues": result.get("issues") or None,
        }
    except Exception as exc:
        logger.warning("Grounding validation failed: %s", exc)
        return {"grounded": None, "confidence": None, "issues": str(exc)}
