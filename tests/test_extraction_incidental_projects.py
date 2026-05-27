from __future__ import annotations

import pytest

from backend.extraction.cascading_extractor import extract_claims


@pytest.mark.asyncio
async def test_extracts_worked_on_project_from_passing_bio_mentions(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = await extract_claims(
        "Jordan Lee is a lecturer whose earlier work included building CampusCart, "
        "advising student teams, and mentoring founders."
    )

    claims = {(claim.subject_text, claim.predicate, claim.object_text) for claim in result.claims}
    assert ("Jordan Lee", "worked_on_project", "CampusCart") in claims


@pytest.mark.asyncio
async def test_extracts_founded_venture_from_incidental_alumni_note(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = await extract_claims(
        "Miguel Ortiz founded Oakline Analytics after co-creating BrightPath as a student."
    )

    claims = {(claim.subject_text, claim.predicate, claim.object_text) for claim in result.claims}
    assert ("Miguel Ortiz", "founded", "Oakline Analytics") in claims
    assert ("Miguel Ortiz", "worked_on_project", "BrightPath") in claims
