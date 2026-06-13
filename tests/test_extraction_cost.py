from __future__ import annotations

from backend.config import get_settings
from backend.extraction.cost import MODEL_PRICING_USD_PER_1M, estimate_cost


def test_extraction_model_defaults_to_gpt_5_4_mini(monkeypatch) -> None:
    monkeypatch.delenv("EXTRACTION_MODEL", raising=False)
    get_settings.cache_clear()

    assert get_settings().extraction_model == "gpt-5.4-mini"


def test_cost_uses_published_gpt_5_4_mini_pricing_and_fallback() -> None:
    assert MODEL_PRICING_USD_PER_1M["gpt-5.4-mini"] == {
        "input": 0.75,
        "output": 4.50,
    }
    assert estimate_cost("gpt-5.4-mini", 1_000_000, 1_000_000) == 5.25
    assert estimate_cost("unknown-model", 1_000_000, 1_000_000) == 5.25
