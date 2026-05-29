from __future__ import annotations

import json
import re
from html import unescape
from typing import Any

import trafilatura
from langdetect import LangDetectException, detect


def clean_html(raw_bytes: bytes) -> tuple[str, str | None]:
    html = raw_bytes.decode("utf-8", errors="replace")
    metadata = trafilatura.extract_metadata(html)
    title = _normalize_text_field(metadata.title) if metadata is not None else None
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_formatting=False,
        include_images=False,
        include_links=False,
        include_tables=False,
        favor_precision=True,
        deduplicate=True,
    )
    cleaned = extracted or _basic_html_to_text(html)
    cleaned = _strip_related_content_tail(cleaned)
    return _normalize_whitespace(cleaned), title


def clean_pdl_person(raw_bytes: bytes) -> tuple[str, str | None]:
    try:
        payload = json.loads(raw_bytes.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "", None
    if not isinstance(payload, dict):
        return "", None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return "", None
    full_name = _pdl_full_name(data)
    if not full_name:
        return "", None

    sentences: list[str] = []
    job_title = _pdl_str(data.get("job_title"))
    job_company = _pdl_str(data.get("job_company_name"))
    if job_title and job_company:
        sentences.append(f"{full_name} is {job_title} at {job_company}.")
    elif job_title:
        sentences.append(f"{full_name} is {job_title}.")
    elif job_company:
        sentences.append(f"{full_name} works at {job_company}.")

    location = (
        _pdl_str(data.get("location_name"))
        or _pdl_str(data.get("location_locality"))
        or _pdl_str(data.get("location_region"))
    )
    if location:
        sentences.append(f"{full_name} is based in {location}.")

    for education_sentence in _pdl_education(data, full_name):
        sentences.append(education_sentence)
    for experience_sentence in _pdl_experience(data, full_name):
        sentences.append(experience_sentence)

    return _normalize_whitespace("\n".join(sentences)), full_name


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
    return re.sub(r"\s+", " ", _normalize_text_field(text) or "").strip()


def _normalize_text_field(text: str | None) -> str | None:
    if text is None:
        return None
    return text.replace("\x00", "")


def _strip_related_content_tail(text: str) -> str:
    matches = list(re.finditer(r"\bread\s+more\b", text, re.IGNORECASE))
    if len(matches) >= 3:
        return text[: matches[0].start()].rstrip()
    return text


def _pdl_str(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return _normalize_text_field(text) or ""


def _pdl_full_name(data: dict[str, Any]) -> str | None:
    full_name = _pdl_str(data.get("full_name"))
    if full_name:
        return _titlecase_name(full_name)
    parts = [
        _pdl_str(data.get("first_name")),
        _pdl_str(data.get("middle_name")),
        _pdl_str(data.get("last_name")),
    ]
    name = " ".join(part for part in parts if part)
    return _titlecase_name(name) if name else None


def _titlecase_name(value: str) -> str:
    return " ".join(part[:1].upper() + part[1:].lower() for part in value.split())


def _pdl_education(data: dict[str, Any], full_name: str) -> list[str]:
    output: list[str] = []
    education = data.get("education")
    if not isinstance(education, list):
        return output
    for item in education:
        if not isinstance(item, dict):
            continue
        school = item.get("school")
        school_name = _pdl_str(school.get("name")) if isinstance(school, dict) else ""
        if not school_name:
            continue
        degree = _degree_from_education(item)
        end_year = _year(item.get("end_date"))
        if degree and end_year:
            output.append(f"{full_name} earned a {degree} from {school_name} in {end_year}.")
        elif degree:
            output.append(f"{full_name} earned a {degree} from {school_name}.")
        elif end_year:
            output.append(f"{full_name} studied at {school_name} until {end_year}.")
        else:
            output.append(f"{full_name} studied at {school_name}.")
    return output


def _degree_from_education(item: dict[str, Any]) -> str:
    degrees = item.get("degrees")
    if isinstance(degrees, list) and degrees:
        degree = _pdl_str(degrees[0])
        if degree:
            return degree
    majors = item.get("majors")
    if isinstance(majors, list) and majors:
        return _pdl_str(majors[0])
    return _pdl_str(majors)


def _pdl_experience(data: dict[str, Any], full_name: str) -> list[str]:
    output: list[str] = []
    experience = data.get("experience")
    if not isinstance(experience, list):
        return output
    for item in experience[:10]:
        if not isinstance(item, dict):
            continue
        company = item.get("company")
        title = item.get("title")
        company_name = _pdl_str(company.get("name")) if isinstance(company, dict) else ""
        title_name = _pdl_str(title.get("name")) if isinstance(title, dict) else ""
        if not company_name:
            continue
        start = _year(item.get("start_date"))
        end = _year(item.get("end_date")) or "present"
        if title_name and start:
            output.append(f"{full_name} was {title_name} at {company_name} from {start} to {end}.")
        elif title_name:
            output.append(f"{full_name} was {title_name} at {company_name}.")
        elif start:
            output.append(f"{full_name} worked at {company_name} from {start} to {end}.")
        else:
            output.append(f"{full_name} worked at {company_name}.")
    return output


def _year(value: object) -> str:
    text = _pdl_str(value)
    match = re.match(r"^(\d{4})", text)
    return match.group(1) if match else ""
