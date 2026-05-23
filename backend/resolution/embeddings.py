from __future__ import annotations

import hashlib
import math
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol

from openai import OpenAI
from sqlalchemy.orm import Session

from backend.db.models import LLMUsage
from backend.db.store import Store
from backend.pricing import estimate_llm_dollars

EMBEDDING_DIMENSIONS = 1536
EMBEDDING_MODEL = "text-embedding-3-small"


class EmbeddingClient(Protocol):
    def embed_text(
        self,
        text: str,
        *,
        purpose: str = "entity_embedding",
        entity_id: uuid.UUID | str | None = None,
    ) -> list[float]:
        raise NotImplementedError


class OpenAIEmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str,
        store: Store,
        model: str = EMBEDDING_MODEL,
    ) -> None:
        self.client = OpenAI(api_key=api_key, max_retries=0)
        self.store = store
        self.model = model

    def embed_text(
        self,
        text: str,
        *,
        purpose: str = "entity_embedding",
        entity_id: uuid.UUID | str | None = None,
    ) -> list[float]:
        response = self.client.embeddings.create(model=self.model, input=text or " ")
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        self.store.record_llm_usage(
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            dollars=estimate_llm_dollars(
                self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=0,
            ),
            purpose=purpose,
            entity_id=entity_id,
        )
        return list(response.data[0].embedding)


class OpenAISessionEmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str,
        session: Session,
        model: str = EMBEDDING_MODEL,
    ) -> None:
        self.client = OpenAI(api_key=api_key, max_retries=0)
        self.session = session
        self.model = model

    def embed_text(
        self,
        text: str,
        *,
        purpose: str = "entity_embedding",
        entity_id: uuid.UUID | str | None = None,
    ) -> list[float]:
        response = self.client.embeddings.create(model=self.model, input=text or " ")
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        self.session.add(
            LLMUsage(
                ts=datetime.now(UTC),
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=0,
                dollars=estimate_llm_dollars(
                    self.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=0,
                ),
                purpose=purpose,
                entity_id=uuid.UUID(str(entity_id)) if entity_id is not None else None,
            )
        )
        return list(response.data[0].embedding)


class DeterministicEmbeddingClient:
    def embed_text(
        self,
        text: str,
        *,
        purpose: str = "entity_embedding",
        entity_id: uuid.UUID | str | None = None,
    ) -> list[float]:
        del entity_id
        features = (
            _text_features(text)
            if "context" in purpose or ":" in text
            else _name_features(text)
            if "name" in purpose
            else _name_features(text)
            if _looks_like_name(text)
            else _text_features(text)
        )
        return _features_to_unit_vector(features)


def context_text(context: dict[str, object] | None) -> str:
    if not context:
        return ""
    parts: list[str] = []
    for key in sorted(context):
        value = context[key]
        if value is None:
            continue
        cleaned = str(value).strip()
        if cleaned:
            parts.append(f"{key}: {cleaned}")
    return "\n".join(parts)


def cosine_similarity(left: Iterable[float] | None, right: Iterable[float] | None) -> float:
    if left is None or right is None:
        return 0.0
    left_values = list(left)
    right_values = list(right)
    if not left_values or not right_values or len(left_values) != len(right_values):
        return 0.0
    dot = sum(a * b for a, b in zip(left_values, right_values, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _looks_like_name(text: str) -> bool:
    normalized = _tokens(text)
    return len(normalized) <= 6


def _name_features(text: str) -> dict[str, float]:
    tokens = _tokens(text)
    if not tokens:
        return {}
    features: dict[str, float] = {}
    has_class_marker = bool(re.search(r"\b(?:t|th|d)'?\d{2}\b", text.lower()))
    name_tokens = [
        token
        for token in tokens
        if not has_class_marker
        or (not re.fullmatch(r"[td]?\d{2,4}", token) and token not in {"t", "th", "d"})
    ]
    if not name_tokens:
        name_tokens = tokens
    first = name_tokens[0]
    last = name_tokens[-1]
    if len(first) == 1:
        features[f"initial:{first}"] = 2.0
    else:
        features[f"first:{first}"] = 2.0
        features[f"initial:{first[0]}"] = 2.0
    features[f"last:{last}"] = 3.0
    for token in name_tokens[1:-1]:
        if len(token) == 1:
            features[f"middle_initial:{token}"] = 0.2
        else:
            features[f"middle:{token}"] = 0.2
    return features


def _text_features(text: str) -> dict[str, float]:
    features: dict[str, float] = {}
    for raw_line in text.splitlines():
        if ":" in raw_line:
            key, value = raw_line.split(":", 1)
            key = key.strip().lower()
            for token in _tokens(value):
                weight = 4.0 if key == "class_year" else 1.0
                features[f"{key}:{token}"] = features.get(f"{key}:{token}", 0.0) + weight
        else:
            for token in _tokens(raw_line):
                features[f"text:{token}"] = features.get(f"text:{token}", 0.0) + 1.0
    return features


def _tokens(text: str) -> list[str]:
    normalized = text.lower().replace("'", "")
    return re.findall(r"[a-z]+|\d{1,4}", normalized)


def _features_to_unit_vector(features: dict[str, float]) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for feature, weight in features.items():
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        for offset in (0, 8, 16):
            index = int.from_bytes(digest[offset : offset + 8], "big") % EMBEDDING_DIMENSIONS
            vector[index] += weight / 3.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]
