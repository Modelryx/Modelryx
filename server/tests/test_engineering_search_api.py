import uuid
from datetime import UTC, datetime, timedelta

import pytest
from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.auth import Principal, create_access_token
from modelryx.core.db import SessionLocal
from modelryx.engineering_search.evaluation import evaluate_ranking_cases, evaluate_result_cases
from modelryx.engineering_search.interpreter import deterministic_interpret
from modelryx.engineering_search.models import AISearchRequest
from modelryx.metadata.array_quality import (
    array_geometry_unverified,
    has_unverified_array_geometry,
)
from modelryx.models import (
    AiDocument,
    AiSearchFeedback,
    AiSearchSession,
    Artifact,
    ArtifactFile,
    BomLine,
    BomViewRevision,
    Item,
    MetadataExtraction,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    User,
    WorkingVersion,
)
from sqlalchemy import literal, select
from sqlalchemy.dialects.postgresql import JSONB

client = TestClient(app)


def test_search_capability_registry_is_authenticated_and_versioned() -> None:
    unauthorized = client.get("/api/v1/search/capabilities")
    assert unauthorized.status_code == 401
    response = client.get("/api/v1/search/capabilities", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["version"] == "1"
    assert response.json()["phase"] == "2.6"
    catalog = response.json()["scenario_catalog"]
    assert catalog["version"] == "scenario-catalog/v1"
    assert len(catalog["digest"]) == 64
    assert {scenario["id"] for scenario in catalog["scenarios"]} >= {
        "MAT-VALUE",
        "MAT-PRES",
        "FEAT-PRES",
    }
    assert response.json()["similarity_search"]["methods"] == ["metadata_embedding"]
    assert response.json()["ranking"] == {
        "policy": "phase2-hybrid-v1",
        "weights": {"text": 0.3, "vector": 0.3, "metadata": 0.25, "relationship": 0.15},
        "normalization": "active_branches",
    }
    assert response.json()["conversation"] == {
        "sessions": True,
        "follow_up": True,
        "maximum_history_entries": 50,
    }


def test_ai_search_returns_interpretation_and_execution_trace_without_gateway() -> None:
    response = client.post(
        "/api/v1/ai/search",
        headers=AUTH,
        json={"query": "Find aluminum brackets smaller than 200 mm", "limit": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["interpretation"]["filters"]["material"]["family"] == "aluminum"
    assert body["interpretation"]["filters"]["geometry"]["max_dimension_mm"]["lt"] == 200
    assert body["trace"]["parser"] == "deterministic"
    assert body["trace"]["returned"] == len(body["results"])
    assert body["trace"]["catalog_version"] == "scenario-catalog/v1"
    assert len(body["trace"]["catalog_digest"]) == 64
    assert any("deterministic" in warning for warning in body["warnings"])


def test_search_feedback_records_interpretation_without_raw_query() -> None:
    search = client.post(
        "/api/v1/ai/search",
        headers=AUTH,
        json={"query": "find part has no material"},
    )
    assert search.status_code == 200, search.text
    session_id = search.json()["session_id"]
    session = client.get(f"/api/v1/ai/search/sessions/{session_id}", headers=AUTH)
    assert session.status_code == 200, session.text
    assert session.json()["history"][-1]["query"] is None

    response = client.post(
        f"/api/v1/ai/search/sessions/{session_id}/feedback",
        headers=AUTH,
        json={"category": "incorrect_interpretation"},
    )
    assert response.status_code == 201, response.text
    with SessionLocal() as db:
        feedback = db.get(AiSearchFeedback, uuid.UUID(response.json()["id"]))
        assert feedback.category == "incorrect_interpretation"
        assert feedback.interpretation_json["filters"]["material_presence"] == "missing"
        assert "query" not in feedback.interpretation_json


def test_expired_search_sessions_and_feedback_are_purged() -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        session = AiSearchSession(
            created_by=user.id,
            current_request_json={},
            history_json=[],
            updated_at=datetime.now(UTC) - timedelta(days=31),
        )
        db.add(session)
        db.flush()
        session_id = session.id
        db.add(
            AiSearchFeedback(
                session_id=session.id,
                created_by=user.id,
                category="incorrect_interpretation",
                interpretation_json={},
            )
        )
        db.commit()

    response = client.post(
        "/api/v1/ai/search", headers=AUTH, json={"query": "find parts"}
    )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        assert db.get(AiSearchSession, session_id) is None
        assert db.scalar(
            select(AiSearchFeedback).where(AiSearchFeedback.session_id == session_id)
        ) is None


def test_direct_search_rejects_invalid_relationship_clauses() -> None:
    response = client.post(
        "/api/v1/search",
        headers=AUTH,
        json={"relationships": [{"type": "contains"}]},
    )
    assert response.status_code == 422


def test_direct_search_filters_item_number_and_name_with_regex() -> None:
    token = uuid.uuid4().hex[:10]
    expected = _phase22_item(
        f"Regex Mounting Plate {token}",
        "",
        {},
        part_number=f"RGX-{token}-042",
    )
    _phase22_item(
        f"Regex Mounting Bracket {token}",
        "",
        {},
        part_number=f"OTHER-{token}-042",
    )

    response = client.post(
        "/api/v1/search",
        headers=AUTH,
        json={
            "filters": {
                "item_number_regex": rf"^RGX-{token}-\d{{3}}$",
                "name_regex": rf"mounting\s+plate\s+{token}$",
            }
        },
    )

    assert response.status_code == 200, response.text
    assert [row["item_id"] for row in response.json()["results"]] == [expected]
    reason_types = {reason["type"] for reason in response.json()["results"][0]["match_reasons"]}
    assert {"item_number_regex", "name_regex"} <= reason_types


def test_direct_search_rejects_invalid_item_regex() -> None:
    response = client.post(
        "/api/v1/search",
        headers=AUTH,
        json={"filters": {"item_number_regex": "[unterminated"}},
    )

    assert response.status_code == 422


def test_direct_search_pages_sorts_and_facets_across_all_matches() -> None:
    token = uuid.uuid4().hex[:10]
    for index in range(12):
        _phase22_item(
            f"Server paging {token} {index:02d}", "", {},
            part_number=f"PAGE-{token}-{index:02d}",
            item_type="ASSEMBLY" if index == 11 else "PART",
            cad_system="EXAMPLE_CAD" if index == 11 else "FreeCAD",
        )
    request = {
        "filters": {"item_number_regex": f"^PAGE-{token}-"},
        "sort": [{"field": "item_number", "direction": "asc"}],
        "limit": 10,
        "offset": 0,
        "include_facets": True,
    }
    first = client.post("/api/v1/search", headers=AUTH, json=request)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["total"] == 12
    assert len(body["results"]) == 10
    assert body["results"][0]["item_number"].endswith("-00")
    assert set(body["facets"]["item_types"]) == {"PART", "ASSEMBLY"}
    assert set(body["facets"]["cad_systems"]) == {"FreeCAD", "EXAMPLE_CAD"}
    assert body["facets"]["item_statuses"] == ["ACTIVE"]

    second = client.post("/api/v1/search", headers=AUTH, json={**request, "offset": 10})
    assert second.status_code == 200, second.text
    assert [row["item_number"][-2:] for row in second.json()["results"]] == ["10", "11"]

    request["filters"]["cad_systems"] = ["EXAMPLE_CAD"]
    request["filters"]["item_types"] = ["PART", "ASSEMBLY"]
    filtered = client.post("/api/v1/search", headers=AUTH, json=request)
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 1
    assert filtered.json()["results"][0]["item_number"].endswith("-11")
    assert set(filtered.json()["facets"]["cad_systems"]) == {"FreeCAD", "EXAMPLE_CAD"}
    assert filtered.json()["facets"]["item_types"] == ["ASSEMBLY"]

    request["filters"]["cad_systems"] = ["FreeCAD", "EXAMPLE_CAD"]
    request["filters"]["item_types"] = ["PART"]
    request["sort"] = [{"field": "item_number", "direction": "desc"}]
    parts = client.post("/api/v1/search", headers=AUTH, json=request)
    assert parts.status_code == 200, parts.text
    assert parts.json()["total"] == 11
    assert parts.json()["results"][0]["item_number"].endswith("-10")
    assert set(parts.json()["facets"]["item_types"]) == {"PART", "ASSEMBLY"}

    request["filters"]["cad_systems"] = []
    request["filters"]["item_types"] = ["PART", "ASSEMBLY"]
    empty = client.post("/api/v1/search", headers=AUTH, json=request)
    assert empty.status_code == 200, empty.text
    assert empty.json()["total"] == 0
    assert set(empty.json()["facets"]["cad_systems"]) == {"FreeCAD", "EXAMPLE_CAD"}


def _phase22_item(
    name: str,
    description: str,
    metadata: dict,
    filename: str | None = None,
    category: str = "Bracket",
    item_type: str = "PART",
    material: str | None = "aluminum_6061",
    hole_count: int | None = None,
    feature_counts: dict[str, int | None] | None = None,
    revision_code: str = "A",
    cad_system: str = "FreeCAD",
    native_format: str = "FCStd",
    revision_state: str = "READY",
    lifecycle_state: str = "WORKING",
    dimensions_mm: tuple[float, float, float] = (100, 80, 20),
    mass_kg: float = 0.4,
    creator_subject: str = "local-development-user",
    part_number: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> str:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == creator_subject))
        item = Item(
            part_number=part_number or f"P22-{uuid.uuid4().hex}",
            name=name,
            description=description,
            category=category,
            part_type=item_type,
            created_by=user.id,
        )
        db.add(item)
        db.flush()
        revision = Revision(
            part_id=item.id,
            revision_code=revision_code,
            cad_system=cad_system,
            native_format=native_format,
            metadata_json=metadata,
            derived_metadata_json={},
            search_text=str(metadata),
            state=revision_state,
            lifecycle_state=lifecycle_state,
            metadata_status="READY",
            created_by=user.id,
        )
        db.add(revision)
        db.flush()
        extraction = MetadataExtraction(
            revision_id=revision.id,
            schema_version="1.0",
            worker_type="test",
            worker_version="1",
            source_system="FreeCAD",
            status="READY",
            extracted_at=datetime.now(UTC),
            metadata_json=metadata,
            derived_json={"tags": metadata.get("tags", [])},
            kind="PART",
            material=material,
            bbox_x_mm=dimensions_mm[0],
            bbox_y_mm=dimensions_mm[1],
            bbox_z_mm=dimensions_mm[2],
            bbox_min_mm=min(dimensions_mm),
            bbox_mid_mm=sorted(dimensions_mm)[1],
            bbox_max_mm=max(dimensions_mm),
            volume_mm3=1000,
            surface_area_mm2=500,
            mass_kg=mass_kg,
            hole_count=hole_count,
            **{
                f"{feature}_count": count
                for feature, count in (feature_counts or {}).items()
            },
        )
        db.add(extraction)
        db.flush()
        revision.current_metadata_extraction_id = extraction.id
        item.current_revision_id = revision.id
        if created_at is not None:
            item.created_at = created_at
        if updated_at is not None:
            item.updated_at = updated_at
        if filename:
            version = WorkingVersion(
                revision_id=revision.id,
                version_number=1,
                state="COMMITTED",
                metadata_json=metadata,
                created_by=user.id,
            )
            db.add(version)
            db.flush()
            revision.current_working_version_id = version.id
            artifact = Artifact(
                artifact_type="STEP",
                name=filename,
                classification="AUTHORITATIVE",
                source="USER",
                state="READY",
                created_by=user.id,
            )
            db.add(artifact)
            db.flush()
            relation = RevisionArtifactRelation(
                revision_id=revision.id,
                artifact_id=artifact.id,
                relation_type="SPECIFICATION",
                is_primary=True,
                created_by=user.id,
            )
            db.add(relation)
            db.flush()
            db.add(
                ArtifactFile(
                    artifact_id=artifact.id,
                    reference_name="MODEL",
                    role="PRIMARY",
                    revision_id=revision.id,
                    working_version_id=version.id,
                    kind="NATIVE",
                    storage_key=f"phase22/{uuid.uuid4()}/{filename}",
                    original_filename=filename,
                    mime_type="application/octet-stream",
                    byte_size=1,
                    sha256="0" * 64,
                    state="READY",
                    required=False,
                )
            )
            snapshot = RevisionContentSnapshot(
                revision_id=revision.id,
                sequence=1,
                commit_id=uuid.uuid4(),
                created_by=user.id,
            )
            db.add(snapshot)
            db.flush()
            db.add(
                RevisionContentSnapshotArtifact(
                    snapshot_id=snapshot.id,
                    relation_id=relation.id,
                    sequence=0,
                )
            )
        db.commit()
        return str(item.id)


def test_ai_search_material_absence_paraphrase_returns_known_match() -> None:
    missing = _phase22_item("Phase22 Missing Material", "", {}, material=None)
    specified = _phase22_item("Phase22 Specified Material", "", {})

    response = client.post(
        "/api/v1/ai/search",
        headers=AUTH,
        json={"query": "find part has no material"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["interpretation"]["filters"]["material_presence"] == "missing"
    ids = {row["item_id"] for row in body["results"]}
    assert missing in ids
    assert specified not in ids


def test_ai_search_hole_absence_excludes_present_and_unknown_feature_coverage() -> None:
    absent = _phase22_item("Phase22 Confirmed Hole Free", "", {}, hole_count=0)
    present = _phase22_item("Phase22 Has Holes", "", {}, hole_count=2)
    unknown = _phase22_item("Phase22 Unknown Hole Coverage", "", {}, hole_count=None)

    response = client.post(
        "/api/v1/ai/search",
        headers=AUTH,
        json={"query": "find part has no hole"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["interpretation"]["filters"]["feature_filters"] == [
        {"feature": "hole", "presence": "absent"}
    ]
    ids = {row["item_id"] for row in body["results"]}
    assert absent in ids
    assert present not in ids
    assert unknown not in ids
    assert body["coverage"]["excluded_unknown"] >= 1
    assert "successful_extraction" in body["coverage"]["coverage_rules"]


@pytest.mark.parametrize(
    "numeric_filter",
    [
        {"geometry": {"volume_mm3": {"eq": 1000}}},
        {"geometry": {"width_mm": {"eq": 100}}},
        {"mass_kg": {"eq": 0.4}},
    ],
)
def test_numeric_geometry_search_excludes_unverified_array_measurements(numeric_filter) -> None:
    token = uuid.uuid4().hex[:10]
    good = _phase22_item(f"{token} verified", "", {}, part_number=f"ARRAY-{token}-OK")
    unknown = _phase22_item(
        f"{token} unverified", "",
        {"native": {"link_arrays": [{"suppression_summary": {
            "unknown_inspected_count": 1, "uninspected_count": 0,
        }}]}},
        part_number=f"ARRAY-{token}-UNKNOWN",
    )
    response = client.post(
        "/api/v1/search", headers=AUTH,
        json={"filters": {
            "item_number_regex": f"^ARRAY-{token}-",
            **numeric_filter,
        }},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    ids = {row["item_id"] for row in body["results"]}
    assert good in ids and unknown not in ids
    assert body["coverage"]["excluded_unknown"] >= 1
    basic = client.post(
        "/api/v1/items/search", headers=AUTH,
        json={"volume_mm3_gte": 1000, "volume_mm3_lte": 1000, "limit": 100},
    )
    assert basic.status_code == 200, basic.text
    basic_ids = {row["item"]["id"] for row in basic.json()["items"]}
    assert good in basic_ids and unknown not in basic_ids


def test_array_quality_sql_matches_python_for_native_evidence() -> None:
    cases = [
        {},
        {"native": {"link_arrays": [{"suppression_summary": {
            "unknown_inspected_count": 0, "uninspected_count": 0,
        }}]}},
        {"native": {"link_arrays": [{"suppression_summary": {
            "unknown_inspected_count": 1, "uninspected_count": 0,
        }}]}},
        {"native": {"link_arrays": [{"suppression_summary": None}]}},
        {"native": {"link_arrays": [{"suppression_summary": {}}]}},
        {"native": {"extraction_diagnostics": [
            {"code": "LINK_ARRAY_SUPPRESSED_GEOMETRY_UNVERIFIED"},
        ]}},
    ]
    with SessionLocal() as db:
        for value in cases:
            actual = db.scalar(select(array_geometry_unverified(literal(value, type_=JSONB))))
            assert actual == has_unverified_array_geometry(value), value


def test_unfiltered_search_results_omit_unverified_array_measurements() -> None:
    token = uuid.uuid4().hex[:10]
    verified = _phase22_item(f"{token} verified", "", {}, part_number=f"DISPLAY-{token}-OK")
    uncertain = _phase22_item(
        f"{token} uncertain", "",
        {"native": {"link_arrays": [{"suppression_summary": {
            "unknown_inspected_count": 1, "uninspected_count": 0,
        }}]}},
        part_number=f"DISPLAY-{token}-UNKNOWN",
    )
    response = client.post(
        "/api/v1/search", headers=AUTH,
        json={"filters": {"item_number_regex": f"^DISPLAY-{token}-"}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    rows = {row["item_id"]: row for row in body["results"]}
    assert rows[verified]["dimensions_mm"] == [100, 80, 20]
    assert rows[verified]["volume_mm3"] == 1000
    assert rows[verified]["mass_kg"] == 0.4
    for field in ("dimensions_mm", "volume_mm3", "surface_area_mm2", "mass_kg"):
        assert rows[uncertain][field] is None
    assert any(
        "Unverified array geometry measurements were omitted" in item for item in body["warnings"]
    )
    with SessionLocal() as db:
        revision = db.get(Revision, db.get(Item, uuid.UUID(uncertain)).current_revision_id)
        extraction = db.get(MetadataExtraction, revision.current_metadata_extraction_id)
        assert extraction.volume_mm3 == 1000 and extraction.mass_kg == 0.4


def test_ai_search_exact_hole_count_excludes_other_counts_and_unknown_coverage() -> None:
    one_hole = _phase22_item("Phase22 One Hole", "", {}, hole_count=1)
    four_holes = _phase22_item("Phase22 Four Holes", "", {}, hole_count=4)
    unknown = _phase22_item("Phase22 Unknown Exact Hole Count", "", {}, hole_count=None)

    response = client.post(
        "/api/v1/ai/search",
        headers=AUTH,
        json={"query": "find parts only has one hole"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["interpretation"]["filters"]["feature_count_filters"][0] == {
        "feature": "hole",
        "count": {
            "eq": 1.0, "lt": None, "lte": None, "gt": None, "gte": None,
            "target": None, "tolerance": None,
        },
    }
    ids = {row["item_id"] for row in body["results"]}
    assert one_hole in ids
    assert four_holes not in ids
    assert unknown not in ids


def test_ai_search_pocket_absence_excludes_present_and_unknown_feature_coverage() -> None:
    absent = _phase22_item(
        "Phase22 Confirmed Pocket Free", "", {}, feature_counts={"pocket": 0}
    )
    present = _phase22_item(
        "Phase22 Has Pockets", "", {}, feature_counts={"pocket": 2}
    )
    unknown = _phase22_item("Phase22 Unknown Pocket Coverage", "", {})

    response = client.post(
        "/api/v1/ai/search",
        headers=AUTH,
        json={"query": "find part has no pocket"},
    )

    assert response.status_code == 200, response.text
    ids = {row["item_id"] for row in response.json()["results"]}
    assert absent in ids
    assert present not in ids
    assert unknown not in ids


def test_p0_artifact_format_and_lifecycle_filters_execute_against_current_snapshot() -> None:
    matching = _phase22_item(
        "P0 Released STEP",
        "",
        {},
        filename="released-model.stp",
        lifecycle_state="RELEASED",
    )
    wrong_format = _phase22_item(
        "P0 Released PDF",
        "",
        {},
        filename="released-drawing.pdf",
        lifecycle_state="RELEASED",
    )
    working_step = _phase22_item(
        "P0 Working STEP", "", {}, filename="working-model.step"
    )
    response = client.post(
        "/api/v1/ai/search",
        headers=AUTH,
        json={"query": "find released STEP parts"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["interpretation"]["filters"]["artifact_format"] == "step"
    assert response.json()["interpretation"]["filters"]["lifecycle"] == "RELEASED"
    ids = {row["item_id"] for row in response.json()["results"]}
    assert matching in ids
    assert wrong_format not in ids
    assert working_step not in ids


def test_p0_self_owner_resolves_server_side() -> None:
    token = f"selfowner{uuid.uuid4().hex}"
    mine = _phase22_item(f"{token} Mine", "", {})
    other_subject = f"other-owner-{uuid.uuid4()}"
    with SessionLocal() as db:
        db.add(
            User(
                external_subject=other_subject,
                display_name="Other P0 Owner",
                role="engineer",
                is_active=True,
            )
        )
        db.commit()
    other = _phase22_item(
        f"{token} Other", "", {}, creator_subject=other_subject
    )
    response = client.post(
        "/api/v1/ai/search",
        headers=AUTH,
        json={"query": f"find my parts {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["interpretation"]["filters"]["owner"] == "$me"
    ids = {row["item_id"] for row in response.json()["results"]}
    assert mine in ids
    assert other not in ids


def test_library_user_filter_uses_unique_username_when_display_names_match() -> None:
    token = uuid.uuid4().hex
    first_subject = f"duplicate-name-a-{token}"
    second_subject = f"duplicate-name-b-{token}"
    display_name = f"Shared Library Name {token}"
    with SessionLocal() as db:
        db.add_all([
            User(
                external_subject=first_subject,
                display_name=display_name,
                role="engineer",
                is_active=True,
            ),
            User(
                external_subject=second_subject,
                display_name=display_name,
                role="engineer",
                is_active=True,
            ),
        ])
        db.commit()

    first = _phase22_item(
        f"{token} First owner", "", {}, creator_subject=first_subject
    )
    second = _phase22_item(
        f"{token} Second owner", "", {}, creator_subject=second_subject
    )

    users_response = client.get("/api/v1/me/library-users", headers=AUTH)
    assert users_response.status_code == 200, users_response.text
    matching_users = [
        row for row in users_response.json() if row["display_name"] == display_name
    ]
    assert [row["username"] for row in matching_users] == [first_subject, second_subject]

    response = client.post(
        "/api/v1/search",
        headers=AUTH,
        json={
            "entity_type": "item",
            "filters": {"owner": first_subject},
            "limit": 50,
        },
    )
    assert response.status_code == 200, response.text
    ids = {row["item_id"] for row in response.json()["results"]}
    assert first in ids
    assert second not in ids


def test_search_reliability_seeded_result_membership_metrics() -> None:
    token = f"sreval{uuid.uuid4().hex}"
    material_missing = _phase22_item(
        f"{token} Material Missing", "", {}, material=None
    )
    material_present = _phase22_item(f"{token} Material Present", "", {})
    hole_absent = _phase22_item(f"{token} Hole Absent", "", {}, hole_count=0)
    hole_present = _phase22_item(f"{token} Hole Present", "", {}, hole_count=3)
    hole_unknown = _phase22_item(f"{token} Hole Unknown", "", {}, hole_count=None)
    combined_absent = _phase22_item(
        f"{token} Combined Absent",
        "",
        {},
        hole_count=0,
        feature_counts={"pocket": 0},
    )
    combined_hole = _phase22_item(
        f"{token} Combined Hole",
        "",
        {},
        hole_count=1,
        feature_counts={"pocket": 0},
    )
    combined_pocket = _phase22_item(
        f"{token} Combined Pocket",
        "",
        {},
        hole_count=0,
        feature_counts={"pocket": 1},
    )
    width_token = f"{token}widthcase"
    width_match = _phase22_item(
        f"{width_token} Match", "", {}, dimensions_mm=(125, 80, 20)
    )
    width_low = _phase22_item(
        f"{width_token} Low", "", {}, dimensions_mm=(90, 80, 20)
    )
    width_high = _phase22_item(
        f"{width_token} High", "", {}, dimensions_mm=(175, 80, 20)
    )
    mass_match = _phase22_item(f"{token} Mass Match", "", {}, mass_kg=0.5)
    mass_high = _phase22_item(f"{token} Mass High", "", {}, mass_kg=2.0)
    category = f"category{uuid.uuid4().hex}"
    classification_match = _phase22_item(
        f"{token} Classification Match", "", {}, category=category
    )
    classification_other = _phase22_item(
        f"{token} Classification Other", "", {}, category="Other"
    )
    revision_code = f"R{uuid.uuid4().hex[:8]}"
    revision_match = _phase22_item(
        f"{token} Revision Match", "", {}, revision_code=revision_code
    )
    _phase22_item(
        f"{token} CAD Match", "", {}, cad_system="SolidWorks"
    )
    _phase22_item(f"{token} CAD Other", "", {}, cad_system="FreeCAD")
    lifecycle_match = _phase22_item(
        f"{token} Lifecycle Match", "", {}, revision_state="READY_WITH_WARNINGS"
    )
    lifecycle_other = _phase22_item(f"{token} Lifecycle Other", "", {})
    assembly_match = _phase22_item(
        f"{token} Assembly Match", "", {}, item_type="ASSEMBLY"
    )
    part_other = _phase22_item(f"{token} Part Other", "", {}, item_type="PART")
    other_owner_subject = f"result-owner-{uuid.uuid4()}"
    with SessionLocal() as db:
        db.add(
            User(
                external_subject=other_owner_subject,
                display_name="Result Corpus Other Owner",
                role="engineer",
                is_active=True,
            )
        )
        db.commit()
    owner_match = _phase22_item(f"{token} Owner Match", "", {})
    owner_other = _phase22_item(
        f"{token} Owner Other", "", {}, creator_subject=other_owner_subject
    )
    identifier = f"SR-{int(uuid.uuid4().hex[:10], 16)}"
    identifier_match = _phase22_item(
        f"{token} Identifier Match", "", {}, part_number=identifier
    )
    older = datetime(2025, 1, 1, tzinfo=UTC)
    newer = datetime(2027, 1, 1, tzinfo=UTC)
    created_token = f"{token}createdcase"
    created_match = _phase22_item(
        f"{created_token} Match", "", {}, created_at=newer
    )
    created_other = _phase22_item(
        f"{created_token} Other", "", {}, created_at=older
    )
    modified_token = f"{token}modifiedcase"
    modified_match = _phase22_item(
        f"{modified_token} Match", "", {}, updated_at=newer
    )
    modified_other = _phase22_item(
        f"{modified_token} Other", "", {}, updated_at=older
    )

    def execute(query: str) -> list[str]:
        response = client.post("/api/v1/ai/search", headers=AUTH, json={"query": query})
        assert response.status_code == 200, response.text
        return [row["item_id"] for row in response.json()["results"]]

    report = evaluate_result_cases(
        [
            {
                "id": "material-absence",
                    "query": f"find {token} material part has no material",
                "expected_included": [material_missing],
                "expected_excluded": [material_present],
                "closed_set": True,
            },
            {
                "id": "hole-absence",
                    "query": f"find {token} hole parts without holes",
                "expected_included": [hole_absent],
                "expected_excluded": [hole_present, hole_unknown],
                "closed_set": True,
            },
            {
                "id": "combined-feature-absence",
                    "query": f"find {token} combined parts with no holes and no pockets",
                "expected_included": [combined_absent],
                "expected_excluded": [combined_hole, combined_pocket],
                "closed_set": True,
            },
            {
                "id": "width-range",
                "query": f"find {width_token} parts between 100 and 150 mm wide",
                "expected_included": [width_match],
                "expected_excluded": [width_low, width_high],
                "closed_set": True,
            },
            {
                "id": "mass-upper-bound",
                "query": f"find {token} mass parts weighing less than 1 kg",
                "expected_included": [mass_match],
                "expected_excluded": [mass_high],
                "closed_set": True,
            },
            {
                "id": "classification",
                "query": f"find parts classified as {category}",
                "expected_included": [classification_match],
                "expected_excluded": [classification_other],
                "closed_set": True,
            },
            {
                "id": "revision",
                "query": f"find part revision {revision_code}",
                "expected_included": [revision_match],
                "closed_set": True,
            },
            {
                "id": "lifecycle",
                "query": f"find {token} lifecycle parts ready with warnings",
                "expected_included": [lifecycle_match],
                "expected_excluded": [lifecycle_other],
                "closed_set": True,
            },
            {
                "id": "item-type",
                "query": f"find {token} assembly assemblies",
                "expected_included": [assembly_match],
                "expected_excluded": [part_other],
                "closed_set": True,
            },
            {
                "id": "owner",
                "query": f"find {token} owner parts owned by Local Engineer",
                "expected_included": [owner_match],
                "expected_excluded": [owner_other],
                "closed_set": True,
            },
            {
                "id": "identifier",
                "query": f"find part {identifier}",
                "expected_included": [identifier_match],
                "closed_set": True,
            },
            {
                "id": "created-date",
                "query": f"find {created_token} parts created after 2026-01-01",
                "expected_included": [created_match],
                "expected_excluded": [created_other],
                "closed_set": True,
            },
            {
                "id": "modified-date",
                "query": f"find {modified_token} parts modified after 2026-01-01",
                "expected_included": [modified_match],
                "expected_excluded": [modified_other],
                "closed_set": True,
            },
        ],
        execute,
    )
    for result_case in report["cases"]:
        assert result_case["passed"], result_case
    assert report["passed"], report
    assert report["metrics"] == {
        "supported_exact_filter_precision": 1.0,
        "supported_exact_filter_recall": 1.0,
        "exclusion_correctness": 1.0,
        "false_zero_rate": 0.0,
        "unexpected_result_count": 0,
    }
    assert report["threshold_failures"] == []


def test_search_reliability_pagination_is_stable_without_duplicates_or_skips() -> None:
    category = f"Pagination-{uuid.uuid4().hex}"
    expected = []
    for sequence in range(5):
        part_number = f"PG-{uuid.uuid4().hex[:8]}-{sequence}"
        expected.append(
            (
                part_number,
                _phase22_item(
                    f"Pagination Item {sequence}",
                    "",
                    {},
                    category=category,
                    part_number=part_number,
                ),
            )
        )
    expected_ids = [item_id for _, item_id in sorted(expected)]
    actual_ids = []
    for offset in (0, 2, 4):
        response = client.post(
            "/api/v1/search",
            headers=AUTH,
            json={
                "filters": {"classification": category},
                "sort": [{"field": "item_number", "direction": "asc"}],
                "limit": 2,
                "offset": offset,
            },
        )
        assert response.status_code == 200, response.text
        actual_ids.extend(row["item_id"] for row in response.json()["results"])

    assert actual_ids == expected_ids
    assert len(actual_ids) == len(set(actual_ids))


@pytest.mark.parametrize("role", ["reader", "engineer", "reviewer", "administrator"])
def test_search_reliability_role_visibility_does_not_leak_foreign_drafts(role: str) -> None:
    category = f"Visibility-{role}-{uuid.uuid4().hex}"
    subject = f"visibility-{role}-{uuid.uuid4()}"
    foreign_subject = f"visibility-foreign-{uuid.uuid4()}"
    with SessionLocal() as db:
        db.add_all(
            [
                User(
                    external_subject=subject,
                    display_name=f"Visibility {role}",
                    role=role,
                    is_active=True,
                    auth_version=0,
                ),
                User(
                    external_subject=foreign_subject,
                    display_name="Visibility Foreign",
                    role="engineer",
                    is_active=True,
                    auth_version=0,
                ),
            ]
        )
        db.commit()
    ready = _phase22_item("Visibility Ready", "", {}, category=category)
    own_draft = _phase22_item(
        "Visibility Own Draft",
        "",
        {},
        category=category,
        revision_state="DRAFT",
        creator_subject=subject,
    )
    foreign_draft = _phase22_item(
        "Visibility Foreign Draft",
        "",
        {},
        category=category,
        revision_state="DRAFT",
        creator_subject=foreign_subject,
    )
    headers = {
        "Authorization": "Bearer "
        + create_access_token(
            Principal(subject=subject, display_name=f"Visibility {role}", role=role)
        )
    }
    response = client.post(
        "/api/v1/search",
        headers=headers,
        json={"filters": {"classification": category}, "limit": 10},
    )
    assert response.status_code == 200, response.text
    ids = {row["item_id"] for row in response.json()["results"]}
    assert ready in ids
    assert foreign_draft not in ids
    if role == "administrator":
        assert own_draft not in ids
    else:
        assert own_draft in ids


def test_phase22_ranks_weighted_text_and_reports_score_components() -> None:
    rank_token = f"rank{uuid.uuid4().hex}"
    title_match = _phase22_item(
        f"{rank_token} Gearbox Mounting Bracket", "Primary mount", {}
    )
    description_match = _phase22_item(
        f"{rank_token} Support Plate", "A gearbox mounting bracket support", {}
    )
    response = client.post(
        "/api/v1/search",
        headers=AUTH,
        json={"text": {"terms": [rank_token, "gearbox", "mounting", "bracket"]}},
    )
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    ids = [row["item_id"] for row in results]
    assert ids.index(title_match) < ids.index(description_match)
    title = next(row for row in results if row["item_id"] == title_match)
    assert title["score"] == title["scores"]["final"]
    assert title["scores"]["text"] > 0
    assert "name" in title["match_reasons"][0]["actual"]
    ranking = evaluate_ranking_cases(
        [
            {
                "id": "title-before-description",
                "query": f"{rank_token} gearbox mounting bracket",
                "judgments": {title_match: 2, description_match: 1},
                "required_top": title_match,
            }
        ],
        lambda _: ids,
    )
    assert ranking["passed"], ranking
    assert ranking["metrics"] == {
        "top_5_recall": 1.0,
        "required_top_result_accuracy": 1.0,
        "ndcg_at_10": 1.0,
    }


def test_phase22_searches_normalized_metadata_and_filenames() -> None:
    metadata_match = _phase22_item("P22 Coupling", "", {"tags": ["phase22servo"]})
    filename_match = _phase22_item("P22 Drawing", "", {}, "phase22datasheet.step")
    metadata_response = client.post(
        "/api/v1/search", headers=AUTH, json={"text": {"terms": ["phase22servo"]}}
    )
    assert metadata_response.status_code == 200, metadata_response.text
    assert metadata_match in [row["item_id"] for row in metadata_response.json()["results"]]
    filename_response = client.post(
        "/api/v1/search", headers=AUTH, json={"text": {"terms": ["phase22datasheet"]}}
    )
    assert filename_response.status_code == 200, filename_response.text
    matched = next(
        row for row in filename_response.json()["results"] if row["item_id"] == filename_match
    )
    assert any(reason["type"] == "filename" for reason in matched["match_reasons"])


def test_phase25_combines_text_and_metadata_with_normalized_policy_weights() -> None:
    category = f"P25-{uuid.uuid4().hex}"
    item_id = _phase22_item("P25 Calibrated Mount", "", {}, category=category)
    response = client.post(
        "/api/v1/search",
        headers=AUTH,
        json={
            "text": {"terms": ["calibrated", "mount"]},
            "filters": {"classification": category},
        },
    )
    assert response.status_code == 200, response.text
    result = next(row for row in response.json()["results"] if row["item_id"] == item_id)
    expected = (0.30 * result["scores"]["text"] + 0.25) / 0.55
    assert result["scores"]["metadata"] == 1
    assert result["scores"]["final"] == pytest.approx(expected)


def _add_phase23_embedding(item_id: str, values: list[float]) -> str:
    with SessionLocal() as db:
        item = db.get(Item, uuid.UUID(item_id))
        revision = db.get(Revision, item.current_revision_id)
        db.add(
            AiDocument(
                item_id=item.id,
                revision_id=revision.id,
                chunk_key="part-metadata-v1",
                title=item.name,
                content=item.description or item.name,
                metadata_json={"kind": "part-metadata"},
                embedding=values,
                embedding_model="phase23-test",
                content_sha256=uuid.uuid4().hex * 2,
                indexed_at=datetime.now(UTC),
            )
        )
        db.commit()
        return str(revision.id)


def test_phase23_similarity_uses_pgvector_threshold_and_excludes_source() -> None:
    category = f"P23-{uuid.uuid4().hex}"
    source_item = _phase22_item("P23 Similarity Source", "", {}, category=category)
    close_item = _phase22_item("P23 Close Neighbor", "", {}, category=category)
    distant_item = _phase22_item("P23 Distant Neighbor", "", {}, category=category)
    source_revision = _add_phase23_embedding(source_item, [1.0] + [0.0] * 1535)
    _add_phase23_embedding(close_item, [0.99, 0.1] + [0.0] * 1534)
    _add_phase23_embedding(distant_item, [0.0, 1.0] + [0.0] * 1534)

    response = client.post(
        "/api/v1/search",
        headers=AUTH,
        json={
            "entity_type": "part",
            "filters": {"classification": category},
            "similarity": {
                "source_item_revision_id": source_revision,
                "method": "metadata_embedding",
                "minimum_score": 0.8,
            },
        },
    )
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [row["item_id"] for row in results] == [close_item]
    assert results[0]["scores"]["vector"] > 0.99
    expected = (0.30 * results[0]["scores"]["vector"] + 0.25) / 0.55
    assert results[0]["score"] == pytest.approx(expected)
    assert results[0]["match_reasons"][-1]["type"] == "vector_similarity"


def test_phase23_ai_similarity_uses_current_revision_context() -> None:
    source_item = _phase22_item("P23 Context Source", "", {})
    neighbor_item = _phase22_item("P23 Context Neighbor", "", {})
    source_revision = _add_phase23_embedding(source_item, [0.0, 1.0] + [0.0] * 1534)
    _add_phase23_embedding(neighbor_item, [0.0, 1.0] + [0.0] * 1534)
    response = client.post(
        "/api/v1/ai/search",
        headers=AUTH,
        json={
            "query": "Find parts similar to this",
            "context": {"current_revision_id": source_revision},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["interpretation"]["similarity"]["source_item_revision_id"] == source_revision
    assert neighbor_item in [row["item_id"] for row in body["results"]]


def _phase24_revision(item_id: str) -> uuid.UUID:
    with SessionLocal() as db:
        item = db.get(Item, uuid.UUID(item_id))
        revision = db.get(Revision, item.current_revision_id)
        return revision.id


@pytest.mark.parametrize(
    ("cad_system", "part_format", "assembly_format"),
    [
        pytest.param("Drawing CAD", "DRAWING", "DRAWING", id="drawing_cad"),
        pytest.param("Assembly CAD", "PART", "ASSEMBLY", id="assembly_cad"),
        pytest.param("FreeCAD", "FCStd", "FCStd", id="freecad"),
    ],
)
def test_phase24_direct_and_recursive_where_used_search(
    cad_system: str, part_format: str, assembly_format: str
) -> None:
    leaf_item = _phase22_item(
        "P24 Leaf", "", {}, cad_system=cad_system, native_format=part_format
    )
    sub_item = _phase22_item(
        "P24 Subassembly",
        "",
        {},
        item_type="ASSEMBLY",
        cad_system=cad_system,
        native_format=assembly_format,
    )
    root_item = _phase22_item(
        "P24 Root Assembly",
        "",
        {},
        item_type="ASSEMBLY",
        cad_system=cad_system,
        native_format=assembly_format,
    )
    leaf_revision = _phase24_revision(leaf_item)
    sub_revision = _phase24_revision(sub_item)
    root_revision = _phase24_revision(root_item)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        sub_snapshot = RevisionContentSnapshot(
            revision_id=sub_revision,
            sequence=1,
            commit_id=uuid.uuid4(),
            created_by=user.id,
        )
        root_snapshot = RevisionContentSnapshot(
            revision_id=root_revision,
            sequence=1,
            commit_id=uuid.uuid4(),
            created_by=user.id,
        )
        db.add_all([sub_snapshot, root_snapshot])
        db.flush()
        sub_view = BomViewRevision(
            revision_id=sub_revision,
            snapshot_id=sub_snapshot.id,
            sequence=1,
            created_by=user.id,
        )
        root_view = BomViewRevision(
            revision_id=root_revision,
            snapshot_id=root_snapshot.id,
            sequence=1,
            created_by=user.id,
        )
        db.add_all([sub_view, root_view])
        db.flush()
        db.add_all(
            [
                BomLine(
                    bom_view_revision_id=sub_view.id,
                    parent_revision_id=sub_revision,
                    child_revision_id=leaf_revision,
                    occurrence_key=f"leaf-{uuid.uuid4().hex}",
                    quantity=3,
                    sequence=0,
                ),
                BomLine(
                    bom_view_revision_id=root_view.id,
                    parent_revision_id=root_revision,
                    child_revision_id=sub_revision,
                    occurrence_key=f"sub-{uuid.uuid4().hex}",
                    quantity=2,
                    sequence=0,
                ),
            ]
        )
        db.commit()

    where_used = client.get(
        f"/api/v1/items/{leaf_item}/revisions/{leaf_revision}/where-used",
        headers=AUTH,
    )
    assert where_used.status_code == 200, where_used.text
    usages = where_used.json()["items"]
    usages_by_parent = {row["parent_item"]["id"]: row for row in usages}
    assert set(usages_by_parent) == {root_item, sub_item}
    assert usages_by_parent[sub_item]["direct"] is True
    indirect = usages_by_parent[root_item]
    assert indirect["direct"] is False
    assert indirect["depth"] == 2
    assert indirect["total_quantity"] == 6
    assert [row["quantity"] for row in indirect["path"]] == [2, 3]
    assert indirect["path"][0]["parent_item_id"] == root_item
    assert indirect["path"][1]["parent_item_id"] == sub_item
    assert where_used.json()["warnings"] == []

    bounded = client.get(
        f"/api/v1/items/{leaf_item}/revisions/{leaf_revision}/where-used?depth=1",
        headers=AUTH,
    )
    assert bounded.status_code == 200, bounded.text
    assert [row["parent_item"]["id"] for row in bounded.json()["items"]] == [sub_item]
    assert bounded.json()["warnings"] == [
        "Where-used traversal stopped at the requested depth of 1"
    ]

    direct = client.post(
        "/api/v1/search",
        headers=AUTH,
        json={
            "entity_type": "assembly",
            "relationships": [{"type": "where_used", "target_item_id": leaf_item}],
        },
    )
    assert direct.status_code == 200, direct.text
    assert [row["item_id"] for row in direct.json()["results"]] == [sub_item]

    recursive = client.post(
        "/api/v1/search",
        headers=AUTH,
        json={
            "entity_type": "assembly",
            "relationships": [
                {
                    "type": "contains",
                    "target_item_id": leaf_item,
                    "depth": "recursive",
                    "max_depth": 10,
                }
            ],
        },
    )
    assert recursive.status_code == 200, recursive.text
    results = recursive.json()["results"]
    assert [row["item_id"] for row in results] == [sub_item, root_item]
    assert results[0]["scores"]["relationship"] == 1
    assert results[1]["scores"]["relationship"] == 0.5
    assert results[1]["match_reasons"][-1]["actual"] == 2


def test_phase24_ai_where_used_uses_current_item_context() -> None:
    target = uuid.uuid4()
    request, _, _ = deterministic_interpret(
        AISearchRequest(
            query="Show all top-level assemblies containing this bearing",
            context={"current_item_id": target},
        )
    )
    assert request.entity_type == "assembly"
    assert request.relationships[0].target_item_id == target
    assert request.relationships[0].depth == "recursive"


def test_phase26_follow_up_session_modifies_previous_validated_request() -> None:
    first = client.post(
        "/api/v1/ai/search",
        headers=AUTH,
        json={"query": "Find aluminum brackets"},
    )
    assert first.status_code == 200, first.text
    session_id = first.json()["session_id"]
    assert session_id
    assert first.json()["interpretation"]["text"]["terms"] == ["brackets"]

    material_follow_up = client.post(
        "/api/v1/ai/search",
        headers=AUTH,
        json={"query": "Only stainless steel instead", "session_id": session_id},
    )
    assert material_follow_up.status_code == 200, material_follow_up.text
    interpretation = material_follow_up.json()["interpretation"]
    assert interpretation["text"]["terms"] == ["brackets"]
    assert interpretation["filters"]["material"]["family"] == "stainless_steel"
    assert any("follow-up" in warning for warning in material_follow_up.json()["warnings"])

    size_follow_up = client.post(
        "/api/v1/ai/search",
        headers=AUTH,
        json={"query": "Smaller than 150 mm", "session_id": session_id},
    )
    assert size_follow_up.status_code == 200, size_follow_up.text
    interpretation = size_follow_up.json()["interpretation"]
    assert interpretation["filters"]["material"]["family"] == "stainless_steel"
    assert interpretation["filters"]["geometry"]["max_dimension_mm"]["lt"] == 150

    session = client.get(f"/api/v1/ai/search/sessions/{session_id}", headers=AUTH)
    assert session.status_code == 200, session.text
    assert len(session.json()["history"]) == 3
    assert session.json()["current_request"] == interpretation


def test_phase26_unknown_search_session_is_not_exposed() -> None:
    response = client.get(f"/api/v1/ai/search/sessions/{uuid.uuid4()}", headers=AUTH)
    assert response.status_code == 404


@pytest.mark.parametrize("predicate,expected", [
    ({"eq": 6}, False),
    ({"eq": 4}, True),
    ({"gt": 5, "lt": 8}, False),
    ({"gte": 4, "lte": 4}, True),
    ({"target": 6, "tolerance": 1}, False),
    ({"target": 10, "tolerance": 0.1}, True),
    ({"lt": 5}, True),
    ({"gt": 9}, True),
    ({"lte": 4}, True),
    ({"lt": 4}, False),
    ({"gte": 10}, True),
    ({"gt": 10}, False),
    ({"target": 4.5, "tolerance": 0.5}, True),
    ({"target": 7, "tolerance": 0.5}, False),
])
def test_hole_diameter_requires_one_actual_matching_hole(predicate, expected) -> None:
    token = uuid.uuid4().hex
    item_id = _phase22_item(token, "", {"features": {"holes": [
        {"diameter_mm": 4, "through": True, "count": 1},
        {"diameter_mm": 10, "through": False, "count": 1},
    ]}})
    unknown_id = _phase22_item(token, "", {})
    with SessionLocal() as db:
        revision = db.scalar(select(Revision).where(Revision.part_id == uuid.UUID(item_id)))
        extraction = db.get(MetadataExtraction, revision.current_metadata_extraction_id)
        extraction.hole_diameter_min_mm = 4
        extraction.hole_diameter_max_mm = 10
        db.commit()
    response = client.post("/api/v1/search", headers=AUTH, json={
        "text": {"terms": [token]}, "filters": {"hole_diameter_mm": predicate},
    })
    assert response.status_code == 200, response.text
    ids = {row["item_id"] for row in response.json()["results"]}
    assert (item_id in ids) is expected
    assert unknown_id not in ids
