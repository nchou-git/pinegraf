from types import SimpleNamespace

from pytest_mock import MockerFixture

from backend.pipeline.research import (
    AttributionDrop,
    AttributionValidationResult,
    AttributionValidator,
    EntityExtractor,
    ExtractedConnection,
    ExtractedFact,
    ExtractedProject,
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
    assert "Only include a project if the page EXPLICITLY states" in system_prompt
    assert "relationship between two OTHER people" in system_prompt
    assert "Each fact must be a claim about THIS alumnus personally" in system_prompt
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


def test_entity_extractor_does_not_attribute_third_party_project_to_slaughter(
    mocker: MockerFixture,
) -> None:
    fake_parse = mocker.Mock(return_value=SimpleNamespace(output_parsed=PageExtraction()))
    fake_client = SimpleNamespace(responses=SimpleNamespace(parse=fake_parse))
    mocker.patch("backend.pipeline.research.OpenAI", return_value=fake_client)
    page = FetchedPage(
        url="https://example.com/slaughter-faculty",
        title="Matthew J. Slaughter Faculty Page",
        text=(
            "Slaughter's faculty page archive mentions the Gyrobike first-year project, "
            "which Errik Anderson and Daniella Reichstetter worked on as classmates."
        ),
    )

    result = EntityExtractor(api_key="test-key").extract("Matthew J. Slaughter", page)

    kwargs = fake_parse.call_args.kwargs
    assert "Matthew J. Slaughter" in kwargs["input"][1]["content"]
    system_prompt = kwargs["input"][0]["content"]
    assert "if the page says 'X and Y worked on project Z'" in system_prompt
    assert not any("gyrobike" in project.name.lower() for project in result.projects)
    connection_names = {connection.name for connection in result.connections}
    assert "Errik Anderson" not in connection_names
    assert "Daniella Reichstetter" not in connection_names


def test_entity_extractor_keeps_project_directly_attributed_to_errik(
    mocker: MockerFixture,
) -> None:
    parsed = PageExtraction(
        connections=[
            ExtractedConnection(
                name="Daniella Reichstetter",
                context="Errik worked on Gyrobike with Daniella.",
                relationship_type="project collaborator",
            )
        ],
        projects=[
            ExtractedProject(
                name="Gyrobike",
                description="Errik worked on Gyrobike with Daniella.",
            )
        ],
    )
    fake_parse = mocker.Mock(return_value=SimpleNamespace(output_parsed=parsed))
    fake_client = SimpleNamespace(responses=SimpleNamespace(parse=fake_parse))
    mocker.patch("backend.pipeline.research.OpenAI", return_value=fake_client)
    page = FetchedPage(
        url="https://example.com/errik-gyrobike",
        title="Errik B. Anderson Project Archive",
        text="Errik worked on Gyrobike with Daniella.",
    )

    result = EntityExtractor(api_key="test-key").extract("Errik B. Anderson", page)

    assert any(project.name == "Gyrobike" for project in result.projects)
    assert "Errik" in result.projects[0].description
    assert any(connection.name == "Daniella Reichstetter" for connection in result.connections)
    assert result.projects[0].source_url == page.url
    assert result.connections[0].source_url == page.url


def test_attribution_validator_drops_misattributed_project(
    mocker: MockerFixture,
) -> None:
    parsed = AttributionValidationResult(
        keep_project_indices=[],
        keep_connection_indices=[],
        keep_fact_indices=[],
        drops=[
            AttributionDrop(
                category="projects",
                index=0,
                item="Gyrobike",
                reason="The source attributes Gyrobike to Errik and Daniella, not Slaughter.",
            )
        ],
    )
    fake_parse = mocker.Mock(return_value=SimpleNamespace(output_parsed=parsed))
    fake_client = SimpleNamespace(responses=SimpleNamespace(parse=fake_parse))
    fake_openai = mocker.patch("backend.pipeline.research.OpenAI", return_value=fake_client)
    extraction = PageExtraction(
        projects=[
            ExtractedProject(
                name="Gyrobike",
                description="Tuck first-year project incorrectly attributed to Slaughter.",
                source_url="https://example.com/slaughter-faculty",
            )
        ]
    )
    page_text = (
        "Slaughter's faculty page archive mentions the Gyrobike first-year project, "
        "which Errik Anderson and Daniella Reichstetter worked on as classmates."
    )

    result = AttributionValidator(api_key="test-key").validate(
        "Matthew J. Slaughter",
        page_text,
        extraction,
    )

    fake_openai.assert_called_once_with(api_key="test-key")
    kwargs = fake_parse.call_args.kwargs
    assert kwargs["model"] == "gpt-5.4-mini"
    assert "keep_project_indices" in kwargs["input"][0]["content"]
    assert "Matthew J. Slaughter" in kwargs["input"][1]["content"]
    assert result.projects == []


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
