import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from modelryx.engineering_search.catalog_loader import (
    ScenarioCatalogError,
    active_scenario_catalog,
    load_scenario_catalog,
)
from modelryx.engineering_search.interpreter import deterministic_interpret
from modelryx.engineering_search.models import AISearchRequest

CATALOG_ROOT = (
    Path(__file__).parents[1]
    / "src"
    / "server"
    / "modelryx"
    / "engineering_search"
    / "catalog"
)


def _catalog_document() -> dict:
    return json.loads((CATALOG_ROOT / "v1" / "catalog.json").read_text(encoding="utf-8"))


def _write_catalog(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_active_catalog_is_stable_and_published() -> None:
    first = active_scenario_catalog()
    second = load_scenario_catalog()
    assert first.version == "scenario-catalog/v1"
    assert first.digest == second.digest
    assert len(first.digest) == 64
    assert {scenario["id"] for scenario in first.scenarios} == {
        "ID",
        "TXT",
        "GEO",
        "MASS",
        "CLASS",
        "REV",
        "REL",
        "SIM",
        "MAT-VALUE",
        "MAT-PRES",
        "FEAT-PRES",
        "ENT",
        "STATE",
        "CAD-FORMAT",
        "OWNER",
        "DATE",
        "SORT",
        "APR-MINE",
        "APR-STATE",
        "APR-DUE",
        "REV-WARN",
        "DATA-ARTIFACT",
        "DATA-META",
        "CHECKOUT",
    }
    assert first.public()["digest"] == first.digest
    assert {scenario["id"] for scenario in first.public()["scenarios"]} == {
        "ID",
        "TXT",
        "GEO",
        "MASS",
        "CLASS",
        "REV",
        "REL",
        "SIM",
        "MAT-VALUE",
        "MAT-PRES",
        "FEAT-PRES",
        "ENT",
        "STATE",
        "CAD-FORMAT",
        "OWNER",
        "DATE",
        "SORT",
        "APR-MINE",
        "APR-STATE",
        "APR-DUE",
        "REV-WARN",
        "DATA-ARTIFACT",
        "DATA-META",
        "CHECKOUT",
    }
    assert sum(1 + len(scenario["paraphrases"]) for scenario in first.scenarios) >= 100
    assert all(len(scenario["paraphrases"]) >= 5 for scenario in first.scenarios)
    assert all(len(scenario["negative_examples"]) >= 2 for scenario in first.scenarios)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "what do I need to review",
            {"approval_state": "PENDING", "approval_assigned_to_me": True},
        ),
        ("parts awaiting approval", {"approval_state": "PENDING"}),
        ("overdue reviews", {"approval_state": "PENDING"}),
        ("ready parts with warnings", {"status": "READY", "has_warnings": True}),
        ("parts without drawings", {"artifact_presence": "drawing", "artifact_missing": True}),
        ("parts with failed extraction", {"extraction_status": "failed"}),
        ("items checked out by me", {"checkout_owner": "$me"}),
    ],
)
def test_p1_workflow_scenarios_are_deterministic(query: str, expected: dict) -> None:
    request, _, _ = deterministic_interpret(AISearchRequest(query=query))
    actual = request.filters.model_dump(exclude_none=True)
    for key, value in expected.items():
        assert actual[key] == value
    if query == "overdue reviews":
        assert actual["approval_due_before"] is not None


def test_catalog_rejects_duplicate_ids(tmp_path: Path) -> None:
    document = _catalog_document()
    document["scenarios"].append(document["scenarios"][0])
    with pytest.raises(ScenarioCatalogError, match="duplicate scenario IDs"):
        load_scenario_catalog(
            _write_catalog(tmp_path, document), CATALOG_ROOT / "schema.json"
        )


def test_catalog_rejects_unknown_capability(tmp_path: Path) -> None:
    document = _catalog_document()
    document["scenarios"][0]["capabilities"] = ["supplier"]
    with pytest.raises(ScenarioCatalogError, match="unknown capability supplier"):
        load_scenario_catalog(
            _write_catalog(tmp_path, document), CATALOG_ROOT / "schema.json"
        )


def test_catalog_rejects_invalid_request_template(tmp_path: Path) -> None:
    document = _catalog_document()
    document["scenarios"][0]["example_request"] = {"entity_type": "document"}
    with pytest.raises(ScenarioCatalogError, match="invalid example request"):
        load_scenario_catalog(
            _write_catalog(tmp_path, document), CATALOG_ROOT / "schema.json"
        )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("find part has no material", ("missing", [])),
        ("find aluminum parts", (None, [])),
        ("find parts with no holes", (None, [("hole", "absent")])),
        ("find parts that contain threads", (None, [("thread", "present")])),
    ],
)
def test_catalog_backed_patterns_preserve_existing_behavior(
    query: str, expected: tuple[str | None, list[tuple[str, str]]]
) -> None:
    request, _, _ = deterministic_interpret(AISearchRequest(query=query))
    actual_features = [
        (feature.feature, feature.presence) for feature in request.filters.feature_filters
    ]
    assert (request.filters.material_presence, actual_features) == expected


def test_relative_dates_are_resolved_to_bounded_utc_predicates() -> None:
    before = datetime.now(UTC)
    request, _, _ = deterministic_interpret(
        AISearchRequest(query="find parts modified in the last 7 days")
    )
    after = datetime.now(UTC)
    assert request.filters.modified is not None
    assert before - timedelta(days=7) <= request.filters.modified.after <= after - timedelta(
        days=7
    )

    request, _, _ = deterministic_interpret(AISearchRequest(query="items created yesterday"))
    assert request.filters.created is not None
    assert request.filters.created.before - request.filters.created.after == timedelta(days=1)


def test_sort_limit_and_format_aliases_compose() -> None:
    request, warnings, _ = deterministic_interpret(
        AISearchRequest(query="newest 500 released STP assemblies without holes")
    )
    assert request.entity_type == "assembly"
    assert request.filters.lifecycle == "RELEASED"
    assert request.filters.artifact_format == "step"
    assert request.filters.feature_filters[0].feature == "hole"
    assert request.sort[0].field == "created"
    assert request.sort[0].direction == "desc"
    assert request.limit == 100
    assert "result limit was capped at 100" in warnings
