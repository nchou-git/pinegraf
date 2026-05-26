from __future__ import annotations

import pytest

from backend.main import SourceCreate, normalize_identifier


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://tuck.dartmouth.edu/", "tuck.dartmouth.edu"),
        ("tuck.dartmouth.edu", "tuck.dartmouth.edu"),
        ("http://tuck.dartmouth.edu", "tuck.dartmouth.edu"),
        ("HTTPS://WWW.TUCK.DARTMOUTH.EDU/path?q=1#frag", "tuck.dartmouth.edu"),
        ("  www.TUCK.dartmouth.edu/anything  ", "tuck.dartmouth.edu"),
    ],
)
def test_normalize_domain_identifier_variants(raw: str, expected: str) -> None:
    assert normalize_identifier("domain", raw) == expected


def test_source_create_normalizes_domain_identifier() -> None:
    payload = SourceCreate(kind="domain", identifier="HTTPS://WWW.TUCK.DARTMOUTH.EDU/path")
    assert payload.identifier == "tuck.dartmouth.edu"


def test_source_create_rejects_empty_domain_identifier() -> None:
    with pytest.raises(ValueError):
        SourceCreate(kind="domain", identifier="  ")


def test_normalize_file_identifier_strips_whitespace_only() -> None:
    assert normalize_identifier("file", "  Alumni Upload.xlsx  ") == "Alumni Upload.xlsx"


def test_upsert_source_deduplicates_normalized_domain_identifiers(store) -> None:
    first = store.upsert_source(
        kind="domain",
        identifier="https://tuck.dartmouth.edu/",
        display_name="First",
    )
    second = store.upsert_source(
        kind="domain",
        identifier="HTTP://WWW.TUCK.DARTMOUTH.EDU/path?q=1",
        display_name="Second",
    )

    assert second.id == first.id
    assert second.identifier == "tuck.dartmouth.edu"
    assert second.display_name == "Second"
    assert store.table_counts(["sources"])["sources"] == 1
