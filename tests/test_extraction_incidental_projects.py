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


@pytest.mark.asyncio
async def test_extracts_founder_and_ceo_project_from_pronoun_sentence(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = await extract_claims(
        "Alex Doe has 20-plus years of startup experience. "
        "She was the founder and CEO of WidgetCo before advising student teams."
    )

    claims = {(claim.subject_text, claim.predicate, claim.object_text) for claim in result.claims}
    assert ("Alex Doe", "founded", "WidgetCo") in claims


@pytest.mark.asyncio
async def test_extracts_dean_and_school_claims_from_leadership_bio(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = await extract_claims(
        "Avery Stone is Dean of North Valley School of Management and the "
        "Earl Parker Professor of Business Administration."
    )

    claims = {(claim.subject_text, claim.predicate, claim.object_text) for claim in result.claims}
    assert ("Avery Stone", "current_title", "Dean") in claims
    assert ("Avery Stone", "employed_by", "North Valley School of Management") in claims
    assert (
        "Avery Stone",
        "current_title",
        "Earl Parker Professor of Business Administration",
    ) in claims


@pytest.mark.asyncio
async def test_extracts_faculty_director_affiliation(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = await extract_claims(
        "Mina Patel, a faculty director at River Center for Digital Strategy, "
        "teaches entrepreneurship."
    )

    claims = {(claim.subject_text, claim.predicate, claim.object_text) for claim in result.claims}
    assert ("Mina Patel", "current_title", "Faculty Director") in claims
    assert ("Mina Patel", "affiliated_with", "River Center for Digital Strategy") in claims
