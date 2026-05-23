from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.db.models import Entity, EntityAlias, EntityAttribute
from backend.db.store import Store

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
WIKIDATA_ENTITY_URL = "https://www.wikidata.org/wiki"


@dataclass(frozen=True)
class WikidataMatch:
    qid: str
    label: str


@dataclass(frozen=True)
class WikidataAttribute:
    attribute_name: str
    attribute_value: str


@dataclass(frozen=True)
class WikidataEntity:
    qid: str
    attributes: list[WikidataAttribute] = field(default_factory=list)


class WikidataSource(Protocol):
    def match_entity(self, label: str, aliases: Iterable[str]) -> WikidataMatch | None:
        raise NotImplementedError

    def entity_attributes(self, qid: str) -> WikidataEntity:
        raise NotImplementedError


class SparqlWikidataSource:
    def __init__(
        self,
        *,
        endpoint: str = SPARQL_ENDPOINT,
        sleep: Callable[[float], None] = time.sleep,
        min_interval_seconds: float = 0.1,
    ) -> None:
        self.endpoint = endpoint
        self.sleep = sleep
        self.min_interval_seconds = min_interval_seconds
        self._last_request = 0.0
        self.client = httpx.Client(
            headers={"Accept": "application/sparql-results+json", "User-Agent": "Pinegraf/0.1"},
            timeout=30.0,
        )

    def close(self) -> None:
        self.client.close()

    def match_entity(self, label: str, aliases: Iterable[str]) -> WikidataMatch | None:
        labels = [label, *aliases]
        values = " ".join(f'"{_sparql_string(value)}"@en' for value in labels if value.strip())
        if not values:
            return None
        query = f"""
        SELECT ?item ?itemLabel WHERE {{
          VALUES ?candidateLabel {{ {values} }}
          {{
            ?item rdfs:label ?candidateLabel.
          }} UNION {{
            ?item skos:altLabel ?candidateLabel.
          }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }}
        LIMIT 1
        """
        rows = self._sparql(query)
        if not rows:
            return None
        item = rows[0]["item"]["value"].rsplit("/", 1)[-1]
        item_label = rows[0].get("itemLabel", {}).get("value", label)
        return WikidataMatch(qid=item, label=item_label)

    def entity_attributes(self, qid: str) -> WikidataEntity:
        query = f"""
        SELECT ?dob ?occupationLabel ?employerLabel ?educationLabel ?notableLabel WHERE {{
          OPTIONAL {{ wd:{qid} wdt:P569 ?dob. }}
          OPTIONAL {{ wd:{qid} wdt:P106 ?occupation. }}
          OPTIONAL {{ wd:{qid} wdt:P108 ?employer. }}
          OPTIONAL {{ wd:{qid} wdt:P69 ?education. }}
          OPTIONAL {{ wd:{qid} wdt:P800 ?notable. }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }}
        LIMIT 100
        """
        rows = self._sparql(query)
        attrs: list[WikidataAttribute] = []
        for row in rows:
            if "dob" in row:
                attrs.append(
                    WikidataAttribute("date_of_birth", row["dob"]["value"].split("T", 1)[0])
                )
            for sparql_key, attr_name in [
                ("occupationLabel", "occupation"),
                ("employerLabel", "current_employer"),
                ("educationLabel", "education"),
                ("notableLabel", "notable_for"),
            ]:
                value = row.get(sparql_key, {}).get("value", "").strip()
                if value:
                    attrs.append(WikidataAttribute(attr_name, value))
        return WikidataEntity(qid=qid, attributes=_dedupe_attrs(attrs))

    def _sparql(self, query: str) -> list[dict[str, dict[str, str]]]:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval_seconds:
            self.sleep(self.min_interval_seconds - elapsed)
        response = self.client.get(self.endpoint, params={"query": query, "format": "json"})
        self._last_request = time.monotonic()
        response.raise_for_status()
        payload = response.json()
        bindings = payload.get("results", {}).get("bindings", [])
        return bindings if isinstance(bindings, list) else []


@dataclass(frozen=True)
class EnrichmentSummary:
    entities_seen: int
    entities_matched: int
    attributes_written: int


def enrich_wikidata(
    store: Store,
    *,
    source: WikidataSource,
    limit: int | None = None,
) -> EnrichmentSummary:
    with store.session() as session:
        entities = list(session.execute(select(Entity).order_by(Entity.created_at.asc())).scalars())
        if limit is not None:
            entities = entities[:limit]
        matched = 0
        written = 0
        for entity in entities:
            aliases = list(
                session.execute(
                    select(EntityAlias.alias)
                    .where(EntityAlias.entity_id == entity.id)
                    .order_by(EntityAlias.id.asc())
                ).scalars()
            )
            match = source.match_entity(entity.canonical_name, aliases)
            if match is None:
                continue
            matched += 1
            wikidata_entity = source.entity_attributes(match.qid)
            written += _write_attributes(session, entity.id, wikidata_entity)
        session.commit()
    return EnrichmentSummary(
        entities_seen=len(entities),
        entities_matched=matched,
        attributes_written=written,
    )


def _write_attributes(session: Session, entity_id: uuid.UUID, entity: WikidataEntity) -> int:
    source_id = f"wikidata:{entity.qid}"
    session.execute(
        delete(EntityAttribute).where(
            EntityAttribute.entity_id == entity_id,
            EntityAttribute.source == source_id,
        )
    )
    now = datetime.now(UTC)
    written = 0
    for attr in entity.attributes:
        if not attr.attribute_value.strip():
            continue
        session.add(
            EntityAttribute(
                entity_id=entity_id,
                attribute_name=attr.attribute_name,
                attribute_value=attr.attribute_value.strip(),
                source=source_id,
                source_url=f"{WIKIDATA_ENTITY_URL}/{entity.qid}",
                as_of_date=None,
                confidence="medium",
                extracted_at=now,
                last_verified_at=now,
                validation_verdict="keep",
            )
        )
        written += 1
    return written


def _dedupe_attrs(attrs: list[WikidataAttribute]) -> list[WikidataAttribute]:
    seen: set[tuple[str, str]] = set()
    output: list[WikidataAttribute] = []
    for attr in attrs:
        key = (attr.attribute_name, attr.attribute_value.casefold())
        if key in seen:
            continue
        seen.add(key)
        output.append(attr)
    return output


def _sparql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
