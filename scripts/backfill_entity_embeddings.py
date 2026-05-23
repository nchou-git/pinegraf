from __future__ import annotations

import argparse

from backend.config import get_settings
from backend.db.store import Store
from backend.resolution.backfill import backfill_entity_embeddings
from backend.resolution.embeddings import (
    DeterministicEmbeddingClient,
    EmbeddingClient,
    OpenAIEmbeddingClient,
)


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Backfill entity embedding columns.")
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="Database URL. Defaults to DATABASE_URL from .env.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use deterministic local embeddings instead of OpenAI.",
    )
    args = parser.parse_args(argv)

    store = Store(args.database_url)
    if args.mock or not settings.openai_api_key:
        embedding_client: EmbeddingClient = DeterministicEmbeddingClient()
    else:
        embedding_client = OpenAIEmbeddingClient(api_key=settings.openai_api_key, store=store)
    summary = backfill_entity_embeddings(store, embedding_client=embedding_client)
    print(f"Backfilled embeddings for {summary.entities_updated}/{summary.entities_seen} entities.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
