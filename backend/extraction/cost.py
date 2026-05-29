from __future__ import annotations

MODEL_PRICING_USD_PER_1M = {
    "gpt-5.5": {"input": 5.00, "output": 30.00},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING_USD_PER_1M.get(model, MODEL_PRICING_USD_PER_1M["gpt-5.5"])
    input_cost = input_tokens * pricing["input"] / 1_000_000
    output_cost = output_tokens * pricing["output"] / 1_000_000
    return round(input_cost + output_cost, 6)
