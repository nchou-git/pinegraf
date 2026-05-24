from __future__ import annotations

PREDICATES = (
    "employed_by",
    "studied_at",
    "founded",
    "partnered_with",
    "mentored_by",
    "worked_on_project",
    "located_in",
    "affiliated_with",
    "related_to",
)

OBJECT_TYPES = (
    "person",
    "org",
    "project",
    "place",
    "event",
    "attribute_value",
    "date",
)

SYSTEM_PROMPT = f"""
You extract source-linked knowledge graph claims from text.

Return strict JSON only:
{{
  "claims": [
    {{
      "subject_text": "canonical subject mention",
      "predicate": "one allowed predicate",
      "object_text": "canonical object mention or value",
      "object_type": "person|org|project|place|event|attribute_value|date",
      "qualifiers": {{}},
      "confidence_internal": 0.0,
      "raw_quote": "exact supporting span from the source",
      "span_start": 0,
      "span_end": 0
    }}
  ]
}}

Allowed predicates are fixed: {", ".join(PREDICATES)}.
New predicates require a code change. Do not invent predicates.
Only emit claims explicitly supported by the text. raw_quote must be an exact
span from the source text. Prefer fewer high-quality claims over speculation.
""".strip()

USER_PROMPT_TEMPLATE = """
Extract claims from this chunk:

{chunk_text}
""".strip()


def user_prompt(chunk_text: str) -> str:
    return USER_PROMPT_TEMPLATE.format(chunk_text=chunk_text)
