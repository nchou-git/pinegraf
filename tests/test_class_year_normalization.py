from __future__ import annotations

import pytest

from backend.class_year import expand_class_year_synonyms, normalize_class_year
from backend.extraction.extractor import ExtractedClaim
from backend.extraction.runner import normalize_extracted_claim


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("T'17", 2017),
        ("T '17", 2017),
        ("T17", 2017),
        ("T 17", 2017),
        ("Class of 2017", 2017),
        ("class of '17", 2017),
        ("'17", 2017),
        ("Tuck '17", 2017),
        ("T'05", 2005),
        ("'99", 1999),
        ("T 1972", 1972),
        ("class of '95", 1995),
        ("class of '23", 2023),
    ],
)
def test_normalize_class_year_surface_forms(surface: str, expected: int) -> None:
    assert normalize_class_year(surface) == expected


@pytest.mark.parametrize("surface", ["", "not a year", "class of nope", "Tuck class"])
def test_normalize_class_year_rejects_garbage(surface: str) -> None:
    assert normalize_class_year(surface) is None


def test_extraction_guard_normalizes_class_year_object_text() -> None:
    claim = ExtractedClaim(
        subject_text="Nathaniel Chou",
        predicate="class_year",
        object_text="T '17",
        object_type="date",
        raw_quote="Nathaniel Chou T '17",
    )

    normalized = normalize_extracted_claim(claim)

    assert normalized.object_text == "2017"
    assert normalized.object_type == "attribute_value"
    assert normalized.raw_quote == "Nathaniel Chou T '17"


def test_expand_class_year_synonyms() -> None:
    assert expand_class_year_synonyms("T'17 alumni") == [
        "T'17 alumni",
        "class of 2017 alumni",
        "2017 alumni",
    ]
