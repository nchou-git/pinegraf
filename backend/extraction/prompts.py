from __future__ import annotations

PREDICATES = (
    "current_title",
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

ENTITY_TYPES = ("person", "org", "project", "place", "event")

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
      "subject_type": "person|org|project|place|event",
      "predicate": "one allowed predicate",
      "object_text": "canonical object mention or value",
      "object_type": "person|org|project|place|event|attribute_value|date",
      "qualifiers": {{}},
      "raw_quote": "exact supporting span from the source",
      "span_start": 0,
      "span_end": 0
    }}
  ]
}}

Allowed predicates are fixed: {", ".join(PREDICATES)}.
New predicates require a code change. Do not invent predicates.
subject_type is required on every claim. Most subjects are persons, but
organizations, projects, and places can also be subjects (e.g. "Maine Venture
Fund invested in X", "Tuck partnered with Y"). When uncertain whether a
subject is a person or an organization, prefer org — false person typings
pollute the graph; false org typings are easily fixable by an admin.

Persons have personal names: a given name and family name. "John Smith" is a
person. "Smith Capital Partners" is an organization. "Tuck Investment Club" is
an organization. "Maine Venture Fund" is an organization. Names ending in legal
markers (LLC, Inc, Corp, Ltd, LLP, GmbH, etc.) are always organizations, never
persons.

News-article headline subjects describe events or markets, not people: "Global
Bond Selloff Halts as Fed Pauses", "Stocks Rally on Earnings", "Tech IPOs
Resume in Q3". Do not extract these as person entities. If the article body
names a real person making a real statement, extract from the body, not from
the headline.

Only emit claims explicitly supported by the text. raw_quote must be an exact
span from the source text. Prefer fewer high-quality claims over speculation.

Faculty and leadership roles:
Extract named academic and administrative roles whenever the source states them.
Use current_title for titles such as dean, professor, lecturer, faculty
director, chair, or center director. Also emit employed_by or affiliated_with
when the text names the school, center, department, or organization connected
to that role. Do not skip role claims just because the page is a directory,
faculty bio, news article, or leadership listing.

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

Headlines, titles, and framing phrases are not facts. Article titles often have
constructions like "Meet [Name]", "Introducing [Name]", "Q&A with [Name]",
"About [Name]", "A Conversation with [Name]". Extract only the name, not the
framing verb. Subject_text "Meet Courtney Bragg" is WRONG; extract "Courtney
Bragg" as subject when the surrounding sentence supports a real claim about
that person. If the only context is the headline itself, skip the claim —
headlines without supporting sentences are not enough to extract a graph fact.

Few-shot examples:

Chunk: "Meet Alex Reed T'18 — From Army Veteran to Health Tech Founder. Alex
co-founded HealthBridge in 2019."
Expected claims:
{{
  "claims": [
    {{
      "subject_text": "Alex Reed",
      "subject_type": "person",
      "predicate": "class_year",
      "object_text": "2018",
      "object_type": "attribute_value",
      "qualifiers": {{}},
      "confidence_internal": 0.95,
      "raw_quote": "Alex Reed T'18",
      "span_start": 5,
      "span_end": 19
    }},
    {{
      "subject_text": "Alex Reed",
      "subject_type": "person",
      "predicate": "founded",
      "object_text": "HealthBridge",
      "object_type": "org",
      "qualifiers": {{}},
      "confidence_internal": 0.9,
      "raw_quote": "Alex co-founded HealthBridge in 2019",
      "span_start": 58,
      "span_end": 94
    }}
  ]
}}

Chunk: "Avery Stone is Dean of North Valley School of Management and the Earl
Parker Professor of Business Administration."
Expected claims:
{{
  "claims": [
    {{
      "subject_text": "Avery Stone",
      "subject_type": "person",
      "predicate": "current_title",
      "object_text": "Dean",
      "object_type": "attribute_value",
      "qualifiers": {{}},
      "confidence_internal": 0.9,
      "raw_quote": "Avery Stone is Dean of North Valley School of Management",
      "span_start": 0,
      "span_end": 59
    }},
    {{
      "subject_text": "Avery Stone",
      "subject_type": "person",
      "predicate": "employed_by",
      "object_text": "North Valley School of Management",
      "object_type": "org",
      "qualifiers": {{}},
      "confidence_internal": 0.9,
      "raw_quote": "Dean of North Valley School of Management",
      "span_start": 15,
      "span_end": 59
    }},
    {{
      "subject_text": "Avery Stone",
      "subject_type": "person",
      "predicate": "current_title",
      "object_text": "Earl Parker Professor of Business Administration",
      "object_type": "attribute_value",
      "qualifiers": {{}},
      "confidence_internal": 0.9,
      "raw_quote": "Earl Parker Professor of Business Administration",
      "span_start": 68,
      "span_end": 113
    }}
  ]
}}

Chunk: "Mina Patel, a faculty director at River Center for Digital Strategy,
teaches entrepreneurship."
Expected claims:
{{
  "claims": [
    {{
      "subject_text": "Mina Patel",
      "subject_type": "person",
      "predicate": "current_title",
      "object_text": "Faculty Director",
      "object_type": "attribute_value",
      "qualifiers": {{}},
      "confidence_internal": 0.88,
      "raw_quote": "Mina Patel, a faculty director at River Center for Digital Strategy",
      "span_start": 0,
      "span_end": 67
    }},
    {{
      "subject_text": "Mina Patel",
      "subject_type": "person",
      "predicate": "affiliated_with",
      "object_text": "River Center for Digital Strategy",
      "object_type": "org",
      "qualifiers": {{}},
      "confidence_internal": 0.88,
      "raw_quote": "faculty director at River Center for Digital Strategy",
      "span_start": 15,
      "span_end": 67
    }}
  ]
}}

Chunk: "Jordan Lee is a lecturer whose earlier work included building CampusCart,
advising two student teams, and founding Northstar Labs."
Expected claims:
{{
  "claims": [
    {{
      "subject_text": "Jordan Lee",
      "subject_type": "person",
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
      "subject_type": "person",
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
      "subject_type": "person",
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
      "subject_type": "person",
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
      "subject_type": "person",
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
      "subject_type": "person",
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

Chunk: "Maine Venture Fund invested in HealthBridge in 2019."
Expected claims:
{{
  "claims": [
    {{
      "subject_text": "Maine Venture Fund",
      "subject_type": "org",
      "predicate": "partnered_with",
      "object_text": "HealthBridge",
      "object_type": "org",
      "qualifiers": {{"role": "investor", "year": 2019}},
      "raw_quote": "Maine Venture Fund invested in HealthBridge in 2019",
      "span_start": 0,
      "span_end": 51
    }}
  ]
}}

Chunk: "Global Bond Selloff Halts as Fed Pauses. Sam Mendoza, a portfolio
manager at Acme Capital, said the market reaction was overdone."
Expected claims:
{{
  "claims": [
    {{
      "subject_text": "Sam Mendoza",
      "subject_type": "person",
      "predicate": "employed_by",
      "object_text": "Acme Capital",
      "object_type": "org",
      "qualifiers": {{"role": "portfolio manager"}},
      "raw_quote": "Sam Mendoza, a portfolio manager at Acme Capital",
      "span_start": 41,
      "span_end": 89
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
