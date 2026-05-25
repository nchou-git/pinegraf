from __future__ import annotations

import re

CLASS_YEAR_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:(?:Tuck\s+)?T\s*['’]?\s*|class\s+of\s+['’]?\s*|Tuck\s+['’]|['’])?"
    r"(\d{2}|\d{4})"
    r"(?!\d)",
    re.IGNORECASE,
)


def normalize_class_year(text: str | None) -> int | None:
    if not text:
        return None
    match = CLASS_YEAR_RE.search(text.strip())
    if not match:
        return None
    raw = match.group(1)
    value = int(raw)
    if len(raw) == 4:
        return value if value >= 1900 else None
    if value >= 50:
        return 1900 + value
    return 2000 + value


def expand_class_year_synonyms(question: str) -> list[str]:
    variants = [question]
    for match in CLASS_YEAR_RE.finditer(question):
        year = normalize_class_year(match.group(0))
        if year is None:
            continue
        short = f"T'{year % 100:02d}"
        long = f"class of {year}"
        surface = match.group(0)
        if short not in question:
            variants.append(question.replace(surface, short))
        if long not in question.casefold():
            variants.append(question.replace(surface, long))
        variants.append(question.replace(surface, str(year)))
    return list(dict.fromkeys(variants))
