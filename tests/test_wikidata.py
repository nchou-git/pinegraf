from __future__ import annotations

from collections.abc import Iterable

from backend.db.store import Store
from backend.sources.wikidata import (
    WikidataAttribute,
    WikidataEntity,
    WikidataMatch,
    enrich_wikidata,
)


class FakeWikidataSource:
    def match_entity(self, label: str, aliases: Iterable[str]) -> WikidataMatch | None:
        del aliases
        if label == "Jane Doe":
            return WikidataMatch(qid="Q123", label=label)
        return None

    def entity_attributes(self, qid: str) -> WikidataEntity:
        assert qid == "Q123"
        return WikidataEntity(
            qid=qid,
            attributes=[
                WikidataAttribute("occupation", "founder"),
                WikidataAttribute("current_employer", "Acme"),
                WikidataAttribute("education", "Tuck School of Business"),
                WikidataAttribute("notable_for", "Gyrobike"),
                WikidataAttribute("date_of_birth", "1980-01-01"),
            ],
        )


def test_enrich_wikidata_writes_idempotent_verified_attributes(tmp_path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'wikidata.db'}")
    store.init_db()
    profile = store.upsert_profile(name="Jane Doe", class_year="T'24")

    first = enrich_wikidata(store, source=FakeWikidataSource())
    second = enrich_wikidata(store, source=FakeWikidataSource())

    attrs = store.list_entity_attributes(entity_id=profile.entity_id)
    wikidata_attrs = [attr for attr in attrs if attr.source == "wikidata:Q123"]
    assert first.entities_seen == second.entities_seen == 1
    assert first.entities_matched == second.entities_matched == 1
    assert first.attributes_written == second.attributes_written == 5
    assert len(wikidata_attrs) == 5
    assert {attr.attribute_name for attr in wikidata_attrs} == {
        "occupation",
        "current_employer",
        "education",
        "notable_for",
        "date_of_birth",
    }
    assert {attr.last_verified_at is not None for attr in wikidata_attrs} == {True}
    assert {attr.source_url for attr in wikidata_attrs} == {"https://www.wikidata.org/wiki/Q123"}
