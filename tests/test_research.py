from types import SimpleNamespace

from pytest_mock import MockerFixture

from backend.pipeline.research import (
    EntityExtractor,
    ExtractedFact,
    FetchedPage,
    PageExtraction,
    ProfileSynthesizer,
    SynthesizedProfile,
)


def test_entity_extractor_allows_disambiguation_warning(
    mocker: MockerFixture,
) -> None:
    parsed = PageExtraction(
        facts=[
            ExtractedFact(
                category="disambiguation_warning",
                content=(
                    "This page is about Joe Hall the musician, not the Tuck alumnus; "
                    "the biography and industry do not match."
                ),
            )
        ]
    )
    fake_parse = mocker.Mock(return_value=SimpleNamespace(output_parsed=parsed))
    fake_client = SimpleNamespace(responses=SimpleNamespace(parse=fake_parse))
    fake_openai = mocker.patch("backend.pipeline.research.OpenAI", return_value=fake_client)
    page = FetchedPage(
        url="https://example.com/joe-hall-musician",
        title="Joe Hall - Musician",
        text="Joe Hall is a touring musician whose career began in the 1970s.",
    )

    result = EntityExtractor(api_key="test-key").extract("Joe Hall", page)

    fake_openai.assert_called_once_with(api_key="test-key")
    kwargs = fake_parse.call_args.kwargs
    assert kwargs["model"] == "gpt-5.4-mini"
    system_prompt = kwargs["input"][0]["content"]
    assert "different person with the same name" in system_prompt
    assert "professional or educational context alongside the alumnus" in system_prompt
    assert (
        "When in doubt, include it with a brief context note and 'low' confidence"
        in system_prompt
    )
    assert result.current_company is None
    assert result.current_title is None
    assert result.past_companies == []
    assert result.education == []
    assert result.bio_summary == ""
    assert result.connections == []
    assert result.projects == []
    assert len(result.facts) == 1
    assert result.facts[0].category == "disambiguation_warning"
    assert result.facts[0].confidence == "low"
    assert result.facts[0].source_url == page.url


def test_profile_synthesizer_uses_gpt_5_4_and_drops_unsourced_facts(
    mocker: MockerFixture,
) -> None:
    parsed = SynthesizedProfile(
        facts=[
            ExtractedFact(
                category="career",
                content="Jane Doe is CEO of ExampleCo.",
                source_url="https://example.com/jane",
            ),
            ExtractedFact(category="career", content="Jane Doe founded a company."),
        ]
    )
    fake_parse = mocker.Mock(return_value=SimpleNamespace(output_parsed=parsed))
    fake_client = SimpleNamespace(responses=SimpleNamespace(parse=fake_parse))
    fake_openai = mocker.patch("backend.pipeline.research.OpenAI", return_value=fake_client)

    result = ProfileSynthesizer(api_key="test-key").synthesize(
        "Jane Doe",
        "T24",
        [
            PageExtraction(
                facts=[
                    ExtractedFact(
                        category="career",
                        content="Jane Doe is CEO of ExampleCo.",
                        source_url="https://example.com/jane",
                    )
                ]
            )
        ],
    )

    fake_openai.assert_called_once_with(api_key="test-key")
    kwargs = fake_parse.call_args.kwargs
    assert kwargs["model"] == "gpt-5.4"
    system_prompt = kwargs["input"][0]["content"]
    assert "Do not introduce new facts during synthesis" in system_prompt
    assert "Drop facts that don't have a source_url" in system_prompt
    assert [fact.content for fact in result.facts] == ["Jane Doe is CEO of ExampleCo."]
