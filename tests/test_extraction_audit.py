from __future__ import annotations

from backend.db.store import Store
from backend.pipeline.extraction_audit import bucket_for_page, run_extraction_audit


def make_store(tmp_path) -> Store:
    store = Store(f"sqlite:///{tmp_path / 'audit.db'}")
    store.init_db()
    return store


def test_run_extraction_audit_writes_diff_summary(tmp_path) -> None:
    store = make_store(tmp_path)
    rich_text = "Errik Anderson T'07 and Daniella Reichstetter T'07 worked on Gyrobike. " * 90
    medium_text = "Jane Doe T'24 works at Acme. " * 50
    sparse_text = "Short profile."
    for name, url, text in [
        ("Errik Anderson", "https://example.com/rich", rich_text),
        ("Jane Doe", "https://example.com/medium", medium_text),
        ("Pat Person", "https://example.com/sparse", sparse_text),
    ]:
        store.save_raw_page(alum_name=name, source_url=url, page_title=name, page_text=text)

    result = run_extraction_audit(store, sample_size=3, use_mock_extract=True)
    latest = store.latest_audit_run()

    assert latest is not None
    assert latest.sample_size == 3
    assert result["id"] == latest.id
    assert len(latest.diff_summary["per_page"]) == 3
    assert latest.diff_summary["global_jaccard"] == 1.0
    assert {row["bucket"] for row in latest.diff_summary["per_page"]} == {
        "rich",
        "medium",
        "sparse",
    }
    assert bucket_for_page(store.list_raw_pages()[0]) == "rich"
