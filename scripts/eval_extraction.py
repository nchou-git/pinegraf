from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from alembic.config import Config

from alembic import command
from backend.db.models import AlumniProfile
from backend.db.store import Store
from backend.pipeline.crawler import Crawler
from backend.pipeline.page_fetcher import FixturePageFetcher
from backend.pipeline.parser import (
    MockExtractionClient,
    MockSynthesisClient,
    MockValidationClient,
    Parser,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_SET_PATH = REPO_ROOT / "tests" / "eval" / "golden_set.json"
FIXTURES_DIR = REPO_ROOT / "tests" / "eval" / "fixtures"
RESULTS_PATH = REPO_ROOT / "eval_results.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Pinegraf extraction golden eval.")
    parser.add_argument("--min-f1", type=float, default=0.6)
    args = parser.parse_args(argv)

    golden_set = _load_golden_set(GOLDEN_SET_PATH)
    with TemporaryDirectory() as tmp_dir:
        store = _migrated_store(Path(tmp_dir) / "eval.db")
        _run_pipeline(store, golden_set)
        results = _score(store, golden_set)

    _print_table(results)
    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    failed = [
        name for name, metric in results["per_attribute"].items() if metric["f1"] < args.min_f1
    ]
    if failed:
        print(
            f"Required attribute F1 below {args.min_f1}: {', '.join(sorted(failed))}",
            file=sys.stderr,
        )
        return 1
    return 0


def _load_golden_set(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _migrated_store(db_path: Path) -> Store:
    database_url = f"sqlite:///{db_path}"
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
    return Store(database_url)


def _run_pipeline(store: Store, golden_set: dict[str, Any]) -> None:
    fetcher = FixturePageFetcher(FIXTURES_DIR)
    seeds = [
        {
            "name": entry["entity_name"],
            "class_year": entry.get("class_year", ""),
            "urls": [fixture_url_for_name(entry["entity_name"])],
        }
        for entry in golden_set["entries"]
    ]
    try:
        crawler = Crawler(store=store, fetcher=fetcher)
        crawler.run(seeds, lambda event: None)
    finally:
        fetcher.close()

    parser = Parser(
        store=store,
        extractor=MockExtractionClient(),
        validator=MockValidationClient(),
        synthesizer=MockSynthesisClient(),
    )
    parser.run(lambda event: None)


def _score(store: Store, golden_set: dict[str, Any]) -> dict[str, Any]:
    profiles = {
        (profile.name, profile.class_year): profile
        for profile in store.list_profiles()
        if profile.entity_id is not None
    }
    counters: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    entity_results = []

    for entry in golden_set["entries"]:
        profile = profiles.get((entry["entity_name"], entry.get("class_year", "")))
        actual = _actual_attributes(store, profile)
        expected = entry["expected"]
        missing: dict[str, list[str]] = {}

        for attribute_name, expected_value in expected.items():
            expected_set = _value_set(expected_value)
            actual_set = _value_set(actual.get(attribute_name, []))
            counters[attribute_name]["tp"] += len(expected_set & actual_set)
            counters[attribute_name]["fp"] += len(actual_set - expected_set)
            counters[attribute_name]["fn"] += len(expected_set - actual_set)
            if not expected_set.issubset(actual_set):
                missing[attribute_name] = sorted(expected_set - actual_set)

        entity_results.append(
            {
                "entity_name": entry["entity_name"],
                "class_year": entry.get("class_year", ""),
                "hit": not missing,
                "missing": missing,
            }
        )

    per_attribute = {
        name: _metrics(values["tp"], values["fp"], values["fn"])
        for name, values in sorted(counters.items())
    }
    hit_count = sum(1 for result in entity_results if result["hit"])
    return {
        "version": golden_set["version"],
        "per_attribute": per_attribute,
        "entity_hit_rate": hit_count / len(entity_results) if entity_results else 0.0,
        "entity_results": entity_results,
    }


def _actual_attributes(
    store: Store,
    profile: AlumniProfile | None,
) -> dict[str, list[str]]:
    if profile is None or profile.entity_id is None:
        return {}
    values: dict[str, list[str]] = defaultdict(list)
    for attribute in store.list_entity_attributes(entity_id=profile.entity_id, verdicts=("keep",)):
        values[attribute.attribute_name].append(attribute.attribute_value)
    if profile.current_company:
        values["current_company"].append(profile.current_company)
    if profile.current_title:
        values["current_title"].append(profile.current_title)
    values["past_company"].extend(profile.past_companies)
    values["education"].extend(profile.education)
    return values


def _metrics(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def _print_table(results: dict[str, Any]) -> None:
    print("Attribute          Precision  Recall  F1     TP  FP  FN")
    print("-----------------  ---------  ------  -----  --  --  --")
    for attribute_name, metric in results["per_attribute"].items():
        print(
            f"{attribute_name:<17}  "
            f"{metric['precision']:>9.2f}  "
            f"{metric['recall']:>6.2f}  "
            f"{metric['f1']:>5.2f}  "
            f"{metric['tp']:>2}  "
            f"{metric['fp']:>2}  "
            f"{metric['fn']:>2}"
        )
    print(f"\nEntity hit rate: {results['entity_hit_rate']:.2%}")
    print(f"Wrote {RESULTS_PATH.relative_to(REPO_ROOT)}")


def _value_set(value: object) -> set[str]:
    if isinstance(value, list):
        return {_normalize(item) for item in value if _normalize(item)}
    normalized = _normalize(value)
    return {normalized} if normalized else set()


def _normalize(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def fixture_url_for_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"https://fixtures.local/{slug}.html"


if __name__ == "__main__":
    raise SystemExit(main())
