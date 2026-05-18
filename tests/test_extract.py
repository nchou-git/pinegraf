from types import SimpleNamespace

from pydantic import BaseModel
from pytest_mock import MockerFixture

from backend.pipeline.extract import MockExtractClient, OpenAIExtractClient
from backend.pipeline.search import SearchResult


def test_mock_extract_returns_structured_profile() -> None:
    client = MockExtractClient()
    results = [
        SearchResult(
            title="John Cena - LinkedIn",
            link="https://example.com/john-cena",
            snippet="John Cena (T95) is currently a Senior Manager at Acme Corp.",
        )
    ]

    profile = client.extract_profile("John Cena", results)

    assert profile.name == "John Cena"
    assert profile.current_company == "Acme Corp"
    assert profile.current_title == "Senior Manager"
    assert "Beta Inc" in profile.past_companies


def test_openai_extract_uses_responses_api_with_pydantic_format(
    mocker: MockerFixture,
) -> None:
    parsed_profile = SimpleNamespace(
        current_company="Waystar Royco",
        past_companies=None,
    )
    fake_response = SimpleNamespace(output_parsed=parsed_profile)
    fake_parse = mocker.Mock(return_value=fake_response)
    fake_client = SimpleNamespace(responses=SimpleNamespace(parse=fake_parse))
    fake_openai = mocker.patch("openai.OpenAI", return_value=fake_client)
    results = [
        SearchResult(
            title="Jane Doe - Company Bio",
            link="https://example.com/jane-doe",
            snippet="Jane Doe is Chief Strategy Officer at Waystar Royco.",
        )
    ]

    client = OpenAIExtractClient(api_key="test-key")
    profile = client.extract_profile("Jane Doe", results)

    fake_openai.assert_called_once_with(api_key="test-key")
    fake_parse.assert_called_once()
    kwargs = fake_parse.call_args.kwargs
    assert kwargs["model"] == "gpt-5.5"
    assert issubclass(kwargs["text_format"], BaseModel)
    assert "Jane Doe" in str(kwargs["input"])
    assert "Waystar Royco" in str(kwargs["input"])
    assert profile.name == "Jane Doe"
    assert profile.current_company == "Waystar Royco"
    assert profile.current_title == ""
    assert profile.past_companies == []
