from __future__ import annotations

from collections.abc import Sequence


def vector_values(vector: object) -> Sequence[float]:
    if vector is None:
        return []
    if hasattr(vector, "tolist"):
        values = vector.tolist()
        return values if isinstance(values, list) else list(values)
    return vector if isinstance(vector, list) else list(vector)


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = sum(value * value for value in left[:size]) ** 0.5
    right_norm = sum(value * value for value in right[:size]) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
