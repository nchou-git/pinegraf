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


def test_partial_crawl_state_is_requeued_on_enqueue(tmp_path) -> None:
    db_file = tmp_path / "test.db"
    store = Store(f"sqlite:///{db_file}")
    store.init_db()

    assert store.enqueue_crawl("Jane Doe", "T24", depth=0, discovered_via="seed")
    store.mark_crawl_status("Jane Doe", "partial", class_year="T24")

    assert not store.enqueue_crawl("Jane Doe", "T24", depth=0, discovered_via="seed")
    pending = store.list_pending_by_class("T24")

    assert len(pending) == 1
    assert pending[0].name == "Jane Doe"
    assert pending[0].status == "pending"
