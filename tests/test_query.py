from types import SimpleNamespace

from pytest_mock import MockerFixture

from backend.pipeline.query import MockQueryClient, OpenAIQueryClient


def test_mock_query_answers_from_profiles() -> None:
    client = MockQueryClient()
    profiles = [
        {
            "name": "John Cena",
            "current_company": "Acme Corp",
            "current_title": "Senior Manager",
            "past_companies": ["Beta Inc"],
        }
    ]

    response = client.answer_question("Who works at Acme?", {"profiles": profiles})

    assert "John Cena" in response.answer


def test_openai_query_uses_gpt_5_5(mocker: MockerFixture) -> None:
    fake_create = mocker.Mock(return_value=SimpleNamespace(output_text="Stored answer."))
    fake_client = SimpleNamespace(responses=SimpleNamespace(create=fake_create))
    fake_openai = mocker.patch("backend.pipeline.query.OpenAI", return_value=fake_client)

    response = OpenAIQueryClient(api_key="test-key").answer_question(
        "Who works at Acme?",
        {"profiles": [{"name": "John Cena", "current_company": "Acme Corp"}]},
    )

    fake_openai.assert_called_once_with(api_key="test-key")
    fake_create.assert_called_once()
    assert fake_create.call_args.kwargs["model"] == "gpt-5.5"
    assert "John Cena" in fake_create.call_args.kwargs["input"]
    assert response.answer == "Stored answer."
