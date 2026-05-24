from __future__ import annotations

import argparse
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.db.models import AlumniProfile, Entity, EntityAlias, EntityAttribute
from backend.db.store import Store
from backend.resolution.embeddings import (
    DeterministicEmbeddingClient,
    EmbeddingClient,
    OpenAIEmbeddingClient,
)
from backend.resolution.entity_resolver import resolve_or_create

SOURCE_ID = "alumni_xlsx_v2"
DEFAULT_XLSX_PATH = Path("data/alum_data.xlsx")

ATTRIBUTE_FIELDS = (
    "class_year",
    "internship_company",
    "internship_location",
    "current_employer",
    "current_employer_website",
    "current_location",
    "eship_notes",
)

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "name": (
        "name",
        "full_name",
        "alum_name",
        "alumni_name",
        "student_name",
        "alumnus",
        "alumna",
    ),
    "first_name": ("first_name",),
    "last_name": ("last_name", "surname", "family_name"),
    "class_year": (
        "class_year",
        "class",
        "year",
        "tuck_class",
        "tuck_class_year",
        "mba_class_year",
    ),
    "internship_company": (
        "internship_company",
        "internship_employer",
        "summer_internship_company",
        "summer_internship_company_name",
        "internship",
    ),
    "internship_location": (
        "internship_location",
        "summer_internship_location",
        "internship_city",
        "internship_location_city",
    ),
    "internship_location_state": ("state", "internship_state", "internship_location_state"),
    "current_employer": (
        "current_employer",
        "current_company",
        "employer",
        "current_organization",
    ),
    "current_employer_website": (
        "current_employer_website",
        "current_company_website",
        "employer_website",
        "company_website",
    ),
    "current_location": (
        "current_location",
        "location",
        "current_city",
        "city",
    ),
    "eship_now": ("eship_now", "e_ship_now", "entrepreneurship_now"),
    "eship_notes": (
        "eship_notes",
        "e_ship_notes",
        "entrepreneurship_notes",
        "entrepreneurial_notes",
        "previous_eship_experience_notes",
        "previous_e_ship_experience_notes",
        "notes",
    ),
}

HEADER_TO_FIELD = {alias: field for field, aliases in HEADER_ALIASES.items() for alias in aliases}


@dataclass(frozen=True)
class AlumniRow:
    row_number: int
    name: str
    attributes: dict[str, str]


@dataclass(frozen=True)
class ImportSummary:
    rows_seen: int
    rows_imported: int
    rows_skipped: int
    entities_touched: int
    attributes_written: int


def import_workbook(
    path: Path,
    store: Store,
    *,
    embedding_client: EmbeddingClient | None = None,
) -> ImportSummary:
    rows = read_workbook(path)
    entity_attributes: dict[uuid.UUID, list[tuple[str, str]]] = {}
    profile_rows: dict[uuid.UUID, AlumniRow] = {}
    skipped = 0
    embedding_client = embedding_client or DeterministicEmbeddingClient()

    with store.session() as session:
        for row in rows:
            class_year = row.attributes.get("class_year", "")
            current_employer = row.attributes.get("current_employer", "")

            entity_id = _resolve_entity_for_row(
                session,
                row=row,
                class_year=class_year,
                current_employer=current_employer,
                embedding_client=embedding_client,
            )
            entity_attributes.setdefault(entity_id, [])
            profile_rows[entity_id] = row
            for attribute_name in ATTRIBUTE_FIELDS:
                value = row.attributes.get(attribute_name, "")
                if value:
                    entity_attributes[entity_id].append((attribute_name, value))

        now = datetime.now(UTC)
        attributes_written = 0
        for entity_id, attributes in entity_attributes.items():
            session.execute(
                delete(EntityAttribute).where(
                    EntityAttribute.entity_id == entity_id,
                    EntityAttribute.source == SOURCE_ID,
                )
            )
            for attribute_name, attribute_value in _dedupe_attributes(attributes):
                session.add(
                    EntityAttribute(
                        entity_id=entity_id,
                        attribute_name=attribute_name,
                        attribute_value=attribute_value,
                        source=SOURCE_ID,
                        source_url=None,
                        as_of_date=None,
                        confidence="high",
                        extracted_at=now,
                        last_verified_at=None,
                        validation_verdict="keep",
                    )
                )
                attributes_written += 1

            entity = session.get(Entity, entity_id)
            if entity is not None:
                entity.updated_at = now
            _upsert_profile(session, entity_id=entity_id, row=profile_rows[entity_id])

        session.commit()

    return ImportSummary(
        rows_seen=len(rows) + skipped,
        rows_imported=len(rows),
        rows_skipped=skipped,
        entities_touched=len(entity_attributes),
        attributes_written=attributes_written,
    )


def _resolve_entity_for_row(
    session: Session,
    *,
    row: AlumniRow,
    class_year: str,
    current_employer: str,
    embedding_client: EmbeddingClient,
) -> uuid.UUID:
    if not class_year:
        existing = _entity_from_source_alias(session, row.name)
        if existing is not None:
            return existing

    context = {"source": SOURCE_ID}
    if class_year:
        context["class_year"] = class_year
    if current_employer:
        context["current_company"] = current_employer
    return resolve_or_create(
        row.name,
        session=session,
        context=context,
        embedding_client=embedding_client,
    )


def _entity_from_source_alias(session: Session, name: str) -> uuid.UUID | None:
    alias = _normalize_alias(name)
    rows = list(
        session.execute(
            select(EntityAlias.entity_id)
            .where(
                EntityAlias.alias == alias,
                EntityAlias.source == SOURCE_ID,
            )
            .limit(2)
        ).scalars()
    )
    if len(rows) == 1:
        return rows[0]
    return None


def read_workbook(path: Path) -> list[AlumniRow]:
    if not path.exists():
        raise FileNotFoundError(f"Alumni workbook not found: {path}")

    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=True, read_only=False)
    worksheet = workbook.active
    header_row_number: int | None = None
    header_cells: list[Any] = []
    for row_number, cells in enumerate(worksheet.iter_rows(), start=1):
        if any(_cell_text(cell).strip() for cell in cells):
            header_row_number = row_number
            header_cells = list(cells)
            break

    if header_row_number is None:
        return []

    column_fields = _map_headers(header_cells)
    mapped_fields = set(column_fields.values())
    if "name" not in mapped_fields and not {"first_name", "last_name"}.issubset(mapped_fields):
        headers = ", ".join(_cell_text(cell) for cell in header_cells if _cell_text(cell))
        raise ValueError(f"No name or first/last name columns found in {path}. Headers: {headers}")

    rows: list[AlumniRow] = []
    for row_number, cells in enumerate(
        worksheet.iter_rows(min_row=header_row_number + 1),
        start=header_row_number + 1,
    ):
        if not any(_cell_text(cell).strip() for cell in cells):
            continue
        values: dict[str, str] = {}
        for index, cell in enumerate(cells):
            field = column_fields.get(index)
            if field is None:
                continue
            value = _cell_text(cell)
            if field == "current_employer_website":
                value = _website_value(cell, value)
            if field == "class_year":
                value = _normalize_class_year(value)
            if value:
                values[field] = value

        name = _row_name(values)
        if not name:
            continue
        attributes = {field: values.get(field, "") for field in ATTRIBUTE_FIELDS}
        attributes["internship_location"] = _joined_location(
            values.get("internship_location", ""),
            values.get("internship_location_state", ""),
        )
        attributes["eship_notes"] = _eship_notes(
            eship_now=values.get("eship_now", ""),
            notes=values.get("eship_notes", ""),
        )
        rows.append(
            AlumniRow(
                row_number=row_number,
                name=name,
                attributes=attributes,
            )
        )

    workbook.close()
    return rows


def _upsert_profile(session: Session, *, entity_id: uuid.UUID, row: AlumniRow) -> None:
    class_year = row.attributes.get("class_year", "")
    current_employer = row.attributes.get("current_employer", "")
    profile = session.execute(
        select(AlumniProfile).where(AlumniProfile.entity_id == entity_id)
    ).scalar_one_or_none()
    if profile is None and class_year:
        profile = session.execute(
            select(AlumniProfile).where(
                AlumniProfile.name == row.name,
                AlumniProfile.class_year == class_year,
            )
        ).scalar_one_or_none()

    if profile is None:
        session.add(
            AlumniProfile(
                name=row.name,
                entity_id=entity_id,
                class_year=class_year,
                current_company=current_employer,
                current_title="",
                past_companies=[],
                education=[],
                bio_summary="",
                discovered_via=SOURCE_ID,
            )
        )
        return

    profile.entity_id = entity_id
    profile.name = row.name
    if class_year:
        profile.class_year = class_year
    if current_employer:
        profile.current_company = current_employer
    if not profile.discovered_via or profile.discovered_via == "seed":
        profile.discovered_via = SOURCE_ID


def _row_name(values: dict[str, str]) -> str:
    explicit_name = values.pop("name", "").strip()
    if explicit_name:
        return explicit_name
    first_name = values.get("first_name", "").strip()
    last_name = values.get("last_name", "").strip()
    return re.sub(r"\s+", " ", f"{first_name} {last_name}").strip()


def _joined_location(city: str, state: str) -> str:
    cleaned_city = city.strip().strip(",")
    cleaned_state = state.strip().strip(",")
    if cleaned_city and cleaned_state:
        return f"{cleaned_city}, {cleaned_state}"
    return cleaned_city or cleaned_state


def _eship_notes(*, eship_now: str, notes: str) -> str:
    cleaned_now = eship_now.strip()
    cleaned_notes = notes.strip()
    if cleaned_now and cleaned_notes:
        return f"Eship now: {cleaned_now}. Notes: {cleaned_notes}"
    if cleaned_now:
        return f"Eship now: {cleaned_now}"
    return cleaned_notes


def _map_headers(header_cells: list[Any]) -> dict[int, str]:
    column_fields: dict[int, str] = {}
    seen_fields: set[str] = set()
    for index, cell in enumerate(header_cells):
        normalized = _normalize_header(_cell_text(cell))
        field = HEADER_TO_FIELD.get(normalized)
        if field is None or field in seen_fields:
            continue
        column_fields[index] = field
        seen_fields.add(field)
    return column_fields


def _cell_text(cell: Any) -> str:
    value = getattr(cell, "value", None)
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"\s+", " ", str(value)).strip()


def _website_value(cell: Any, value: str) -> str:
    hyperlink = getattr(cell, "hyperlink", None)
    target = str(getattr(hyperlink, "target", "") or "").strip()
    if target and (not value or "." not in value):
        return target
    return value


def _normalize_header(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower())).strip("_")


def _normalize_alias(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _normalize_class_year(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    tuck_year = re.search(r"T\s*'?\s*(\d{2})", cleaned, flags=re.IGNORECASE)
    if tuck_year:
        return f"T'{tuck_year.group(1)}"
    year = re.search(r"\b(19|20)(\d{2})\b", cleaned)
    if year:
        return f"T'{year.group(2)}"
    short_year = re.fullmatch(r"'?(\d{2})", cleaned)
    if short_year:
        return f"T'{short_year.group(1)}"
    return cleaned


def _dedupe_attributes(attributes: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    output: list[tuple[str, str]] = []
    for attribute_name, attribute_value in attributes:
        key = (attribute_name, attribute_value.casefold())
        if key in seen:
            continue
        seen.add(key)
        output.append((attribute_name, attribute_value))
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the Pinegraf alumni XLSX seed file.")
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_XLSX_PATH,
        help=f"Workbook path, default: {DEFAULT_XLSX_PATH}",
    )
    parser.add_argument(
        "--database-url",
        default=get_settings().database_url,
        help="Database URL. Defaults to DATABASE_URL from .env.",
    )
    parser.add_argument(
        "--mock-embeddings",
        action="store_true",
        help="Use deterministic local embeddings instead of OpenAI.",
    )
    args = parser.parse_args(argv)

    store = Store(args.database_url)
    settings = get_settings()
    if args.mock_embeddings or not settings.openai_api_key:
        embedding_client: EmbeddingClient = DeterministicEmbeddingClient()
    else:
        embedding_client = OpenAIEmbeddingClient(api_key=settings.openai_api_key, store=store)
    try:
        summary = import_workbook(args.path, store, embedding_client=embedding_client)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(
        "Imported "
        f"{summary.rows_imported} rows, touched {summary.entities_touched} entities, "
        f"wrote {summary.attributes_written} attributes from {SOURCE_ID}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
