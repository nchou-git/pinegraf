from __future__ import annotations

import argparse
import sys

from backend.config import get_settings
from backend.db.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show the latest stored source snapshot.")
    parser.add_argument("url")
    args = parser.parse_args(argv)

    store = Store(get_settings().database_url)
    raw_page = store.get_latest_raw_page_by_url(args.url)
    if raw_page is None:
        print(f"No snapshot stored for {args.url}", file=sys.stderr)
        return 1

    preview = " ".join(raw_page.page_text.split())[:500]
    print(f"URL: {raw_page.source_url}")
    print(f"Retrieved at: {raw_page.fetched_at.isoformat()}")
    print(f"HTTP status: {raw_page.http_status}")
    print(f"SHA-256: {raw_page.content_sha256}")
    print(f"Compressed HTML available: {raw_page.raw_html_gz is not None}")
    print(f"Text preview: {preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
