from __future__ import annotations

from openai import AsyncOpenAI

from backend.config import get_settings

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536

async def embed_text(text: str) -> list[float]:
    return (await embed_texts([text]))[0]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    settings = get_settings()
    if settings.use_mock_embeddings or not settings.openai_api_key:
        return [_deterministic_vector(text) for text in texts]

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


def _deterministic_vector(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for index, char in enumerate(text.casefold().encode("utf-8")):
        vector[(index + char) % EMBEDDING_DIMENSIONS] += 1.0
    norm = sum(value * value for value in vector) ** 0.5
    if norm == 0:
        return vector
    return [value / norm for value in vector]
