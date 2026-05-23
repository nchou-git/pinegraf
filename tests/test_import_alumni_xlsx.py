from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from openpyxl import Workbook

from backend.db.models import EntityAttribute
from backend.db.store import Store


def load_import_script() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "import_alumni_xlsx.py"
    spec = importlib.util.spec_from_file_location("import_alumni_xlsx", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


IMPORT_SCRIPT = load_import_script()
SOURCE_ID = IMPORT_SCRIPT.SOURCE_ID
import_workbook = IMPORT_SCRIPT.import_workbook


def make_store(tmp_path: Path) -> Store:
    store = Store(f"sqlite:///{tmp_path / 'import.db'}")
    store.init_db()
    return store


def write_workbook(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(
        [
            "FIRST NAME",
            "LAST NAME",
            "CLASS",
            "SUMMER INTERNSHIP COMPANY NAME",
            "WEBSITE",
            "Internship location | City",
            "State",
            "Eship now?",
            "Current Employer",
            "Current Employer Website",
            "Location",
            "Previous Eship experience & Notes",
        ]
    )
    worksheet.append(
        [
            "Errik",
            "Anderson",
            2007,
            "Gyrobike",
            "gyrobike.example",
            "Hanover",
            "NH",
            "yes",
            "Compass Therapeutics",
            "compasstherapeutics.com",
            "Boston, MA",
            "First-year project founder notes",
        ]
    )
    workbook.save(path)
    workbook.close()


def test_import_workbook_creates_entities_attributes_and_profiles_idempotently(tmp_path) -> None:
    store = make_store(tmp_path)
    workbook_path = tmp_path / "alumni.xlsx"
    write_workbook(workbook_path)

    first = import_workbook(workbook_path, store)
    second = import_workbook(workbook_path, store)

    assert first.rows_imported == 1
    assert second.rows_imported == 1
    assert first.entities_touched == second.entities_touched == 1
    assert first.attributes_written == second.attributes_written == 7

    profiles = store.list_profiles()
    assert len(profiles) == 1
    assert profiles[0].name == "Errik Anderson"
    assert profiles[0].class_year == "T'07"
    assert profiles[0].current_company == "Compass Therapeutics"
    assert profiles[0].discovered_via == SOURCE_ID

    with store.session() as session:
        attributes = list(
            session.query(EntityAttribute)
            .filter(EntityAttribute.entity_id == profiles[0].entity_id)
            .order_by(EntityAttribute.attribute_name)
        )

    assert len(attributes) == 7
    assert {attribute.source for attribute in attributes} == {SOURCE_ID}
    assert {attribute.source_url for attribute in attributes} == {None}
    assert {attribute.as_of_date for attribute in attributes} == {None}
    assert {attribute.last_verified_at for attribute in attributes} == {None}
    assert {attribute.confidence for attribute in attributes} == {"high"}
    values = {attribute.attribute_name: attribute.attribute_value for attribute in attributes}
    assert values["class_year"] == "T'07"
    assert values["internship_company"] == "Gyrobike"
    assert values["internship_location"] == "Hanover, NH"
    assert values["current_employer"] == "Compass Therapeutics"
    assert values["current_employer_website"] == "compasstherapeutics.com"
    assert values["eship_notes"] == "Eship now: yes. Notes: First-year project founder notes"
