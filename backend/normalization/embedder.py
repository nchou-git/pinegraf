from __future__ import annotations

from openai import AsyncOpenAI

from backend.config import get_settings

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
BATCH_SIZE = 100


async def embed_chunks(chunks: list[str]) -> list[list[float]]:
    if not chunks:
        return []
    settings = get_settings()
    if settings.use_mock_embeddings or not settings.openai_api_key:
        return [[0.0] * EMBEDDING_DIMENSIONS for _ in chunks]

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start : start + BATCH_SIZE]
        response = await client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        vectors.extend([item.embedding for item in response.data])
    return vectors
