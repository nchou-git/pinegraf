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

Project, product, and venture mentions:
Extract project, product, and venture affiliations even when mentioned only in
passing. Faculty bios and news articles often list ventures, products built, or
projects led in a single sentence or short list; these are high-value claims.
Use worked_on_project for individual contributions and founded for ventures a
person started. Extract the named project/product as object_text even when the
page is not primarily about that project.

Few-shot examples:

Chunk: "Jordan Lee is a lecturer whose earlier work included building CampusCart,
advising two student teams, and founding Northstar Labs."
Expected claims:
{{
  "claims": [
    {{
      "subject_text": "Jordan Lee",
      "predicate": "worked_on_project",
      "object_text": "CampusCart",
      "object_type": "project",
      "qualifiers": {{}},
      "confidence_internal": 0.86,
      "raw_quote": "Jordan Lee is a lecturer whose earlier work included building CampusCart",
      "span_start": 0,
      "span_end": 78
    }},
    {{
      "subject_text": "Jordan Lee",
      "predicate": "founded",
      "object_text": "Northstar Labs",
      "object_type": "org",
      "qualifiers": {{}},
      "confidence_internal": 0.86,
      "raw_quote": "founding Northstar Labs",
      "span_start": 111,
      "span_end": 134
    }}
  ]
}}

Chunk: "In a profile about teaching, Priya Raman T'14 mentioned that she led
HarborGrid before joining a climate venture."
Expected claims:
{{
  "claims": [
    {{
      "subject_text": "Priya Raman",
      "predicate": "class_year",
      "object_text": "2014",
      "object_type": "attribute_value",
      "qualifiers": {{}},
      "confidence_internal": 0.95,
      "raw_quote": "Priya Raman T'14",
      "span_start": 29,
      "span_end": 45
    }},
    {{
      "subject_text": "Priya Raman",
      "predicate": "worked_on_project",
      "object_text": "HarborGrid",
      "object_type": "project",
      "qualifiers": {{}},
      "confidence_internal": 0.84,
      "raw_quote": "she led HarborGrid",
      "span_start": 61,
      "span_end": 79
    }}
  ]
}}

Chunk: "The alumni note says Miguel Ortiz co-created BrightPath, later founded
Oakline Analytics, and now mentors founders."
Expected claims:
{{
  "claims": [
    {{
      "subject_text": "Miguel Ortiz",
      "predicate": "worked_on_project",
      "object_text": "BrightPath",
      "object_type": "project",
      "qualifiers": {{}},
      "confidence_internal": 0.84,
      "raw_quote": "Miguel Ortiz co-created BrightPath",
      "span_start": 21,
      "span_end": 54
    }},
    {{
      "subject_text": "Miguel Ortiz",
      "predicate": "founded",
      "object_text": "Oakline Analytics",
      "object_type": "org",
      "qualifiers": {{}},
      "confidence_internal": 0.86,
      "raw_quote": "founded Oakline Analytics",
      "span_start": 62,
      "span_end": 87
    }}
  ]
}}
""".strip()

USER_PROMPT_TEMPLATE = """
Extract claims from this chunk:

{chunk_text}
""".strip()


def user_prompt(chunk_text: str) -> str:
    return USER_PROMPT_TEMPLATE.format(chunk_text=chunk_text)
