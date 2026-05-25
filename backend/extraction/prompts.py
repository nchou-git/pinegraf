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
    "class_year",
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

Class year normalization:
When extracting a class_year attribute or any mention of an alumni class year,
normalize it to a 4-digit integer year. Accept any of these surface forms and
convert:
  "T'17", "T '17", "T17", "T 17"       -> 2017
  "Class of 2017", "class of '17"      -> 2017
  "'17", "Tuck '17"                    -> 2017
  Any 2-digit year >= 50               -> 19XX (e.g. '95 -> 1995)
  Any 2-digit year < 50                -> 20XX (e.g. '23 -> 2023)
Store the integer in the structured field. Preserve the raw surface form in
raw_quote for traceability.
""".strip()

USER_PROMPT_TEMPLATE = """
Extract claims from this chunk:

{chunk_text}
""".strip()


def user_prompt(chunk_text: str) -> str:
    return USER_PROMPT_TEMPLATE.format(chunk_text=chunk_text)
