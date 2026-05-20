from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.db.store import Store  # noqa: E402
from backend.pipeline.crawler import ProgressEvent  # noqa: E402
from backend.pipeline.parser import (  # noqa: E402
    MockExtractionClient,
    MockSynthesisClient,
    MockValidationClient,
    OpenAIExtractionClient,
    OpenAISynthesisClient,
    OpenAIValidationClient,
    Parser,
)


def main() -> int:
    settings = get_settings()
    store = Store(settings.database_url)
    store.init_db()

    if settings.use_mock_extract:
        extractor = MockExtractionClient()
        validator = MockValidationClient()
        synthesizer = MockSynthesisClient()
    else:
        extractor = OpenAIExtractionClient(api_key=settings.openai_api_key, model="gpt-5.4-mini")
        validator = OpenAIValidationClient(api_key=settings.openai_api_key, model="gpt-5.4-mini")
        synthesizer = OpenAISynthesisClient(api_key=settings.openai_api_key, model="gpt-5.4")

    parser = Parser(
        store=store,
        extractor=extractor,
        validator=validator,
        synthesizer=synthesizer,
    )

    def emit(event: ProgressEvent) -> None:
        print(json.dumps({"kind": event.kind, **event.data}, default=str))

    parser.run(emit, force=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
