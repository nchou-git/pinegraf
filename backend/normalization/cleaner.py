from __future__ import annotations

import re
from html import unescape

import trafilatura
from langdetect import LangDetectException, detect


def clean_html(raw_bytes: bytes) -> tuple[str, str | None]:
    html = raw_bytes.decode("utf-8", errors="replace")
    metadata = trafilatura.extract_metadata(html)
    title = metadata.title if metadata is not None else None
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_formatting=False,
        include_images=False,
        include_links=False,
        include_tables=False,
    )
    cleaned = extracted or _basic_html_to_text(html)
    return _normalize_whitespace(cleaned), title


def detect_language(text: str) -> str | None:
    if not text.strip():
        return None
    try:
        return detect(text)
    except LangDetectException:
        return None


def _basic_html_to_text(html: str) -> str:
    without_scripts = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    without_tags = re.sub(r"(?s)<[^>]+>", " ", without_scripts)
    return unescape(without_tags)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
