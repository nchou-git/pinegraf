from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: float
    output_per_million: float


MODEL_PRICES: dict[str, ModelPrice] = {
    "text-embedding-3-small": ModelPrice(input_per_million=0.02, output_per_million=0.0),
    "gpt-5.4-mini": ModelPrice(input_per_million=0.25, output_per_million=2.00),
    "gpt-5.4": ModelPrice(input_per_million=3.00, output_per_million=15.00),
    "gpt-5.5": ModelPrice(input_per_million=5.00, output_per_million=20.00),
}


def estimate_llm_dollars(model: str, *, prompt_tokens: int, completion_tokens: int) -> float:
    price = MODEL_PRICES.get(model)
    if price is None:
        return 0.0
    return (prompt_tokens / 1_000_000) * price.input_per_million + (
        completion_tokens / 1_000_000
    ) * price.output_per_million
