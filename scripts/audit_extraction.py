from __future__ import annotations

import argparse
import json

from backend.config import get_settings
from backend.db.store import Store
from backend.pipeline.extraction_audit import run_extraction_audit


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run a thrifty-vs-frontier extraction audit.")
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="Database URL. Defaults to DATABASE_URL from .env.",
    )
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock extraction regardless of USE_MOCK_EXTRACT.",
    )
    parser.add_argument(
        "--max-estimated-dollars",
        type=float,
        default=5.0,
        help="Abort non-mock audits above this estimated OpenAI cost.",
    )
    args = parser.parse_args(argv)

    store = Store(args.database_url)
    result = run_extraction_audit(
        store,
        sample_size=args.sample_size,
        use_mock_extract=args.mock or settings.use_mock_extract,
        openai_api_key=settings.openai_api_key,
        max_estimated_dollars=args.max_estimated_dollars,
    )
    print(json.dumps(result["diff_summary"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
