from backend.db.store import Store


def test_store_upsert_and_list(tmp_path) -> None:
    db_file = tmp_path / "test.db"
    store = Store(f"sqlite:///{db_file}")
    store.init_db()

    store.upsert_profile(
        name="John Cena",
        class_year="T95",
        current_company="Acme Corp",
        current_title="Senior Manager",
        past_companies=["Beta Inc"],
    )
    profiles = store.list_profiles()

    assert len(profiles) == 1
    assert profiles[0].name == "John Cena"
