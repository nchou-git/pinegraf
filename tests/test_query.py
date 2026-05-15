from backend.pipeline.query import MockQueryClient


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

    response = client.answer_question("Who works at Acme?", profiles)

    assert "John Cena" in response.answer
