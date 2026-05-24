from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken


@dataclass(frozen=True)
class Chunk:
    text: str
    token_count: int


def chunk_text(text: str, max_tokens: int = 512, overlap: int = 50) -> list[Chunk]:
    encoding = tiktoken.get_encoding("cl100k_base")
    sentences = _sentences(text)
    chunks: list[Chunk] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        token_count = len(encoding.encode(sentence))
        if token_count > max_tokens:
            if current:
                chunks.append(_chunk(" ".join(current), encoding))
                current = []
                current_tokens = 0
            chunks.extend(_split_long_sentence(sentence, encoding, max_tokens, overlap))
            continue
        if current and current_tokens + token_count > max_tokens:
            chunk = " ".join(current)
            chunks.append(_chunk(chunk, encoding))
            current = [_overlap_text(chunk, encoding, overlap), sentence]
            current = [part for part in current if part]
            current_tokens = len(encoding.encode(" ".join(current)))
            continue
        current.append(sentence)
        current_tokens += token_count

    if current:
        chunks.append(_chunk(" ".join(current), encoding))
    return chunks


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]


def _chunk(text: str, encoding: tiktoken.Encoding) -> Chunk:
    return Chunk(text=text, token_count=len(encoding.encode(text)))


def _split_long_sentence(
    sentence: str,
    encoding: tiktoken.Encoding,
    max_tokens: int,
    overlap: int,
) -> list[Chunk]:
    tokens = encoding.encode(sentence)
    chunks: list[Chunk] = []
    start = 0
    step = max(max_tokens - overlap, 1)
    while start < len(tokens):
        window = tokens[start : start + max_tokens]
        text = encoding.decode(window).strip()
        if text:
            chunks.append(Chunk(text=text, token_count=len(window)))
        start += step
    return chunks


def _overlap_text(text: str, encoding: tiktoken.Encoding, overlap: int) -> str:
    if overlap <= 0:
        return ""
    tokens = encoding.encode(text)
    return encoding.decode(tokens[-overlap:]).strip()
