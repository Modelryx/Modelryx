import json
import uuid
from pathlib import Path

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.comparison.explanations import _valid_ai_output
from modelryx.comparison.service import _transform_changed
from modelryx.core.auth import Principal, create_access_token
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import (
    ArtifactFile,
    BomLine,
    BomViewRevision,
    Item,
    Revision,
    RevisionContentSnapshot,
    User,
    WorkingVersion,
)
from sqlalchemy import select

client = TestClient(app)


def _comparison_revisions(*, state: str = "READY", owner_subject: str = "local-development-user"):
    token = uuid.uuid4().hex
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.external_subject == owner_subject))
        item = Item(
            part_number=f"PF-CMP-{token[:8]}",
            name="Comparison Housing",
            description="Phase 5 fixture",
            part_type="PART",
            created_by=owner.id,
        )
        db.add(item)
        db.flush()
        before = Revision(
            part_id=item.id,
            revision_code="A",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={"classification": {"family": "housing"}},
            derived_metadata_json={
                "geometry": {"volume_mm3": 1000.0},
                "physical": {"mass_kg": 1.0, "material": "Aluminum 6061"},
            },
            state=state,
            metadata_status="READY",
            created_by=owner.id,
        )
        db.add(before)
        db.flush()
        after = Revision(
            part_id=item.id,
            revision_code="B",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={"classification": {"family": "housing-v2"}},
            derived_metadata_json={
                "geometry": {"volume_mm3": 1100.0},
                "physical": {"mass_kg": 0.9, "material": "Aluminum 7075"},
            },
            state=state,
            metadata_status="READY",
            created_by=owner.id,
        )
        db.add(after)
        db.flush()
        before_version = WorkingVersion(
            revision_id=before.id,
            version_number=1,
            state="READY",
            source_sha256="a" * 64,
            metadata_json={},
            created_by=owner.id,
        )
        after_version = WorkingVersion(
            revision_id=after.id,
            version_number=1,
            state="READY",
            source_sha256="b" * 64,
            metadata_json={},
            created_by=owner.id,
        )
        db.add_all([before_version, after_version])
        db.flush()
        before.current_working_version_id = before_version.id
        after.current_working_version_id = after_version.id
        item.current_revision_id = after.id
        db.add_all(
            [
                ArtifactFile(
                    revision_id=before.id,
                    working_version_id=before_version.id,
                    kind="NATIVE",
                    storage_key=f"comparison/{token}/a.fcstd",
                    original_filename="housing.fcstd",
                    mime_type="application/octet-stream",
                    byte_size=100,
                    sha256="a" * 64,
                    state="VERIFIED",
                    required=True,
                ),
                ArtifactFile(
                    revision_id=after.id,
                    working_version_id=after_version.id,
                    kind="NATIVE",
                    storage_key=f"comparison/{token}/b.fcstd",
                    original_filename="housing.fcstd",
                    mime_type="application/octet-stream",
                    byte_size=110,
                    sha256="b" * 64,
                    state="VERIFIED",
                    required=True,
                ),
            ]
        )
        db.commit()
        return str(item.id), str(before.id), str(after.id)


def test_phase50_golden_contract_corpus() -> None:
    corpus = json.loads(
        (Path(__file__).parent / "comparison" / "phase50_golden.json").read_text(encoding="utf-8")
    )
    assert len(corpus) >= 5
    assert any(row.get("percent_change") is None for row in corpus)
    assert any(row.get("expected_domain_status") == "UNSUPPORTED" for row in corpus)


def test_phase54_transform_tolerance_ignores_numeric_noise() -> None:
    baseline = [0.0] * 16
    within_tolerance = list(baseline)
    within_tolerance[12] = 0.0000001
    outside_tolerance = list(baseline)
    outside_tolerance[12] = 0.01

    assert _transform_changed(baseline, within_tolerance) is False
    assert _transform_changed(baseline, outside_tolerance) is True


def test_phase56_rejects_uncited_or_unapproved_ai_claims() -> None:
    valid = {
        "summary": "One change.",
        "facts": [{"text": "Mass changed.", "change_keys": ["physical:mass"]}],
        "cause": {"text": "Recorded cause.", "source_ids": ["source-1"], "recorded": True},
        "advisory": ["Advisory: review the change."],
    }
    assert _valid_ai_output(valid, {"physical:mass"}, {"source-1"}) is True
    uncited = {**valid, "facts": [{"text": "A hole was added.", "change_keys": []}]}
    assert _valid_ai_output(uncited, {"physical:mass"}, {"source-1"}) is False
    invented_source = {
        **valid,
        "cause": {"text": "Recorded cause.", "source_ids": ["invented"], "recorded": True},
    }
    assert _valid_ai_output(invented_source, {"physical:mass"}, {"source-1"}) is False
    unlabeled_advice = {**valid, "advisory": ["Approve immediately."]}
    assert _valid_ai_output(unlabeled_advice, {"physical:mass"}, {"source-1"}) is False


def test_phase51_creates_idempotent_structured_comparison() -> None:
    item_id, before_id, after_id = _comparison_revisions()
    payload = {
        "from_revision_id": before_id,
        "to_revision_id": after_id,
        "domains": ["METADATA", "PHYSICAL_PROPERTIES", "FILES", "GEOMETRY"],
    }
    created = client.post(
        f"/api/v1/items/{item_id}/revision-comparisons",
        headers={**AUTH, "X-Request-ID": "phase51-create"},
        json=payload,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "COMPLETED"
    assert body["comparison_engine_version"] == "phase5-v6"
    domains = {row["domain"]: row for row in body["domains"]}
    assert domains["GEOMETRY"]["status"] == "COMPLETED"
    assert domains["GEOMETRY"]["result"]["comparison_level"] == "METADATA_METRICS"
    assert domains["GEOMETRY"]["result"]["alignment"] == {
        "method": "CAD_COORDINATE_SYSTEM",
        "confidence": 1.0,
    }
    assert domains["GEOMETRY"]["result"]["added_volume_mm3"] is None
    assert domains["GEOMETRY"]["result"]["limitations"]
    assert domains["FILES"]["changes"][0]["provenance"] == "EXACT_HASH"
    physical = domains["PHYSICAL_PROPERTIES"]["changes"]
    volume = next(row for row in physical if row["property_path"] == "geometry.volume_mm3")
    assert volume["unit"] == "mm3"
    assert volume["delta"] == 100.0
    assert volume["percent_change"] == 10.0
    assert any(row["significance"] == "MAJOR" for row in physical)

    repeated = client.post(
        f"/api/v1/items/{item_id}/revision-comparisons", headers=AUTH, json=payload
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == body["id"]
    detail = client.get(f"/api/v1/revision-comparisons/{body['id']}", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json() == repeated.json()


def test_comparison_omits_unverified_array_measurements() -> None:
    item_id, before_id, after_id = _comparison_revisions()
    with SessionLocal() as db:
        before = db.get(Revision, uuid.UUID(before_id))
        before.derived_metadata_json = {
            **before.derived_metadata_json,
            "native": {"link_arrays": [{"suppression_summary": {
                "unknown_inspected_count": 1, "uninspected_count": 0,
            }}]},
        }
        db.commit()
    response = client.post(
        f"/api/v1/items/{item_id}/revision-comparisons", headers=AUTH,
        json={"from_revision_id": before_id, "to_revision_id": after_id,
              "domains": ["GEOMETRY", "PHYSICAL_PROPERTIES", "METADATA"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    domains = {row["domain"]: row for row in body["domains"]}
    assert body["status"] == "PARTIAL"
    assert domains["GEOMETRY"]["status"] == "UNSUPPORTED"
    assert domains["GEOMETRY"]["result"]["geometry_changed"] is None
    assert domains["GEOMETRY"]["changes"] == []
    assert domains["PHYSICAL_PROPERTIES"]["status"] == "UNSUPPORTED"
    assert domains["PHYSICAL_PROPERTIES"]["changes"] == []
    assert domains["METADATA"]["warnings"]
    assert not any(row["property_path"].startswith("geometry.")
                   for row in domains["METADATA"]["changes"])


def test_phase51_compare_previous_and_reauthorizes_both_revisions() -> None:
    item_id, before_id, after_id = _comparison_revisions()
    previous = client.post(
        f"/api/v1/items/{item_id}/revisions/{after_id}/compare-previous",
        headers=AUTH,
        json={"domains": ["METADATA", "FILES"]},
    )
    assert previous.status_code == 201, previous.text
    assert previous.json()["from_revision_id"] == before_id

    with SessionLocal() as db:
        owner = User(
            external_subject=f"comparison-owner-{uuid.uuid4()}",
            display_name="Comparison Owner",
            role="engineer",
        )
        reader = User(
            external_subject=f"comparison-reader-{uuid.uuid4()}",
            display_name="Comparison Reader",
            role="reader",
        )
        db.add_all([owner, reader])
        db.commit()
        owner_subject = owner.external_subject
        reader_subject = reader.external_subject
    hidden_item, hidden_before, hidden_after = _comparison_revisions(
        state="DRAFT", owner_subject=owner_subject
    )
    hidden = client.post(
        f"/api/v1/items/{hidden_item}/revision-comparisons",
        headers=AUTH,
        json={
            "from_revision_id": hidden_before,
            "to_revision_id": hidden_after,
            "domains": ["METADATA"],
        },
    )
    assert hidden.status_code == 201
    reader_auth = {
        "Authorization": "Bearer "
        + create_access_token(
            Principal(subject=reader_subject, display_name="Comparison Reader", role="reader")
        )
    }
    denied = client.get(f"/api/v1/revision-comparisons/{hidden.json()['id']}", headers=reader_auth)
    assert denied.status_code == 404


def test_phase53_matches_individual_features_with_explicit_confidence() -> None:
    item_id, before_id, after_id = _comparison_revisions()
    with SessionLocal() as db:
        before = db.get(Revision, uuid.UUID(before_id))
        after = db.get(Revision, uuid.UUID(after_id))
        before.derived_metadata_json = {
            **before.derived_metadata_json,
            "features": {
                "source": "history",
                "summary": {"pocket_count": 1, "fillet_count": 1},
                "items": [
                    {
                        "persistent_id": "Pocket001",
                        "name": "Main pocket",
                        "type": "POCKET",
                        "properties": {"Length": 10.0},
                    },
                    {"type": "FILLET", "properties": {"Radius": 1.0}},
                ],
            },
        }
        after.derived_metadata_json = {
            **after.derived_metadata_json,
            "features": {
                "source": "history",
                "summary": {"pocket_count": 1, "fillet_count": 1, "hole_count": 1},
                "items": [
                    {
                        "persistent_id": "Pocket001",
                        "name": "Main pocket",
                        "type": "POCKET",
                        "properties": {"Length": 12.0},
                    },
                    {"type": "FILLET", "properties": {"Radius": 2.0}},
                    {
                        "persistent_id": "Hole001",
                        "name": "Mounting hole",
                        "type": "HOLE",
                        "properties": {"Diameter": 6.0, "Through": True},
                    },
                ],
            },
        }
        db.commit()
    response = client.post(
        f"/api/v1/items/{item_id}/revision-comparisons",
        headers=AUTH,
        json={
            "from_revision_id": before_id,
            "to_revision_id": after_id,
            "domains": ["FEATURES"],
        },
    )
    assert response.status_code == 201, response.text
    domain = response.json()["domains"][0]
    assert domain["status"] == "COMPLETED"
    assert domain["result"]["comparison_level"] == "INDIVIDUAL"
    assert domain["result"]["added_count"] == 1
    assert domain["result"]["modified_count"] == 2
    matches = {row["method"]: row["confidence"] for row in domain["result"]["matches"]}
    assert matches["PERSISTENT_ID"] == 1.0
    assert matches["TYPE_ONLY"] == 0.65
    assert domain["result"]["limitations"]
    added = next(row for row in domain["changes"] if row["change_type"] == "ADDED")
    assert added["metadata"]["feature_type"] == "HOLE"
    possible = next(row for row in domain["changes"] if row["confidence"] == 0.65)
    assert possible["provenance"] == "MATCH_TYPE_ONLY"


def test_phase54_compares_authoritative_bom_occurrences() -> None:
    item_id, before_id, after_id = _comparison_revisions()
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        before = db.get(Revision, uuid.UUID(before_id))
        after = db.get(Revision, uuid.UUID(after_id))
        component = Item(
            part_number=f"PF-COMP-{uuid.uuid4().hex[:8]}",
            name="Comparison component",
            part_type="PART",
            created_by=owner.id,
        )
        added_component = Item(
            part_number=f"PF-ADD-{uuid.uuid4().hex[:8]}",
            name="Added component",
            part_type="PART",
            created_by=owner.id,
        )
        renamed_component = Item(
            part_number=f"PF-RENAMED-{uuid.uuid4().hex[:8]}",
            name="Renamed component",
            part_type="PART",
            created_by=owner.id,
        )
        repeated_component = Item(
            part_number=f"PF-REPEATED-{uuid.uuid4().hex[:8]}",
            name="Repeated renamed component",
            part_type="PART",
            created_by=owner.id,
        )
        db.add_all([component, added_component, renamed_component, repeated_component])
        db.flush()
        component_a = Revision(
            part_id=component.id,
            revision_code="A",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={},
            state="READY",
            metadata_status="READY",
            created_by=owner.id,
        )
        component_b = Revision(
            part_id=component.id,
            revision_code="B",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={},
            state="READY",
            metadata_status="READY",
            created_by=owner.id,
        )
        added_revision = Revision(
            part_id=added_component.id,
            revision_code="A",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={},
            state="READY",
            metadata_status="READY",
            created_by=owner.id,
        )
        renamed_a = Revision(
            part_id=renamed_component.id,
            revision_code="A",
            cad_system="Example CAD",
            native_format="IPT",
            metadata_json={},
            state="READY",
            metadata_status="READY",
            created_by=owner.id,
        )
        renamed_b = Revision(
            part_id=renamed_component.id,
            revision_code="B",
            cad_system="Example CAD",
            native_format="IPT",
            metadata_json={},
            state="READY",
            metadata_status="READY",
            created_by=owner.id,
        )
        repeated_a = Revision(
            part_id=repeated_component.id,
            revision_code="A",
            cad_system="Example CAD",
            native_format="DWG",
            metadata_json={},
            state="READY",
            metadata_status="READY",
            created_by=owner.id,
        )
        repeated_b = Revision(
            part_id=repeated_component.id,
            revision_code="B",
            cad_system="Example CAD",
            native_format="DWG",
            metadata_json={},
            state="READY",
            metadata_status="READY",
            created_by=owner.id,
        )
        db.add_all(
            [
                component_a,
                component_b,
                added_revision,
                renamed_a,
                renamed_b,
                repeated_a,
                repeated_b,
            ]
        )
        db.flush()
        before_snapshot = RevisionContentSnapshot(
            revision_id=before.id,
            sequence=1,
            commit_id=uuid.uuid4(),
            created_by=owner.id,
        )
        after_snapshot = RevisionContentSnapshot(
            revision_id=after.id,
            sequence=1,
            commit_id=uuid.uuid4(),
            created_by=owner.id,
        )
        db.add_all([before_snapshot, after_snapshot])
        db.flush()
        before_view = BomViewRevision(
            revision_id=before.id,
            snapshot_id=before_snapshot.id,
            sequence=1,
            created_by=owner.id,
        )
        after_view = BomViewRevision(
            revision_id=after.id,
            snapshot_id=after_snapshot.id,
            sequence=1,
            created_by=owner.id,
        )
        db.add_all([before_view, after_view])
        db.flush()
        db.add_all(
            [
                BomLine(
                    bom_view_revision_id=before_view.id,
                    parent_revision_id=before.id,
                    child_revision_id=component_a.id,
                    occurrence_key="mount-1",
                    quantity=1,
                    properties_json={
                        "native_bom_rows": [
                            {"view_type": "STRUCTURED", "item_number": "10"}
                        ]
                    },
                    sequence=0,
                ),
                BomLine(
                    bom_view_revision_id=before_view.id,
                    parent_revision_id=before.id,
                    child_revision_id=component_a.id,
                    occurrence_key="removed-1",
                    quantity=1,
                    sequence=1,
                ),
                BomLine(
                    bom_view_revision_id=before_view.id,
                    parent_revision_id=before.id,
                    child_revision_id=renamed_a.id,
                    occurrence_key="legacy-channel-1",
                    reference_designator="LegacyChannel:1",
                    source_reference="old/LegacyChannel.ipt",
                    quantity=1,
                    sequence=2,
                ),
                BomLine(
                    bom_view_revision_id=before_view.id,
                    parent_revision_id=before.id,
                    child_revision_id=repeated_a.id,
                    occurrence_key="legacy-repeated-left",
                    reference_designator="RepeatedComponent",
                    quantity=1,
                    properties_json={"occurrence_ids": ["model/left/10"]},
                    sequence=3,
                ),
                BomLine(
                    bom_view_revision_id=before_view.id,
                    parent_revision_id=before.id,
                    child_revision_id=repeated_a.id,
                    occurrence_key="legacy-repeated-right",
                    reference_designator="RepeatedComponent",
                    quantity=1,
                    properties_json={"occurrence_ids": ["model/right/20"]},
                    sequence=4,
                ),
                BomLine(
                    bom_view_revision_id=after_view.id,
                    parent_revision_id=after.id,
                    child_revision_id=component_b.id,
                    occurrence_key="mount-1",
                    quantity=2,
                    properties_json={
                        "native_bom_rows": [
                            {"view_type": "STRUCTURED", "item_number": "20"}
                        ]
                    },
                    sequence=0,
                ),
                BomLine(
                    bom_view_revision_id=after_view.id,
                    parent_revision_id=after.id,
                    child_revision_id=added_revision.id,
                    occurrence_key="added-1",
                    quantity=1,
                    sequence=1,
                ),
                BomLine(
                    bom_view_revision_id=after_view.id,
                    parent_revision_id=after.id,
                    child_revision_id=renamed_b.id,
                    occurrence_key="channel-1",
                    reference_designator="Channel:1",
                    source_reference="new/Channel.ipt",
                    quantity=1,
                    sequence=2,
                ),
                BomLine(
                    bom_view_revision_id=after_view.id,
                    parent_revision_id=after.id,
                    child_revision_id=repeated_b.id,
                    occurrence_key="renamed-repeated-a",
                    reference_designator="RepeatedComponent",
                    quantity=1,
                    properties_json={"occurrence_ids": ["model/left/10"]},
                    sequence=3,
                ),
                BomLine(
                    bom_view_revision_id=after_view.id,
                    parent_revision_id=after.id,
                    child_revision_id=repeated_b.id,
                    occurrence_key="renamed-repeated-b",
                    reference_designator="RepeatedComponent",
                    quantity=1,
                    properties_json={"occurrence_ids": ["model/right/20"]},
                    sequence=4,
                ),
            ]
        )
        db.commit()

    response = client.post(
        f"/api/v1/items/{item_id}/revision-comparisons",
        headers=AUTH,
        json={
            "from_revision_id": before_id,
            "to_revision_id": after_id,
            "domains": ["BOM"],
        },
    )
    assert response.status_code == 201, response.text
    domain = response.json()["domains"][0]
    assert domain["status"] == "COMPLETED"
    assert domain["result"]["comparison_level"] == "AUTHORITATIVE_OCCURRENCES"
    assert domain["result"]["added_count"] == 1
    assert domain["result"]["removed_count"] == 1
    assert domain["result"]["moved_count"] == 3
    # Counts are non-exclusive: both occurrences advance revisions, while the
    # renamed Channel also moves and changes its reference designator.
    assert domain["result"]["replaced_count"] == 4
    assert domain["result"]["modified_count"] == 2
    change_types = {row["change_type"] for row in domain["changes"]}
    assert {"ADDED", "REMOVED", "REPLACED", "MODIFIED"} <= change_types
    quantity = next(
        row for row in domain["changes"] if row["property_path"].endswith(".quantity")
    )
    assert quantity["provenance"] == "MATCH_OCCURRENCE_KEY"
    assert quantity["metadata"]["hierarchy_path"] == "mount-1"
    native_bom = next(
        row for row in domain["changes"] if row["property_path"].endswith(".properties")
    )
    assert native_bom["old_value"]["native_bom_rows"][0]["item_number"] == "10"
    assert native_bom["new_value"]["native_bom_rows"][0]["item_number"] == "20"
    renamed = next(
        row for row in domain["result"]["matches"] if row["method"] == "COMPONENT_ONLY"
    )
    assert renamed["from_occurrence_key"] == "legacy-channel-1"
    assert renamed["to_occurrence_key"] == "channel-1"
    renamed_reference = next(
        row
        for row in domain["changes"]
        if row["property_path"] == "bom.channel-1.reference_designator"
    )
    assert renamed_reference["old_value"] == "LegacyChannel:1"
    assert renamed_reference["new_value"] == "Channel:1"
    assert renamed_reference["provenance"] == "MATCH_COMPONENT_ONLY"
    native_matches = [
        row
        for row in domain["result"]["matches"]
        if row["method"] == "NATIVE_OCCURRENCE_IDS"
    ]
    assert {
        (row["from_occurrence_key"], row["to_occurrence_key"])
        for row in native_matches
    } == {
        ("legacy-repeated-left", "renamed-repeated-a"),
        ("legacy-repeated-right", "renamed-repeated-b"),
    }


def test_phase55_compares_drawing_file_text_and_structured_entities() -> None:
    item_id, before_id, after_id = _comparison_revisions()
    with SessionLocal() as db:
        before = db.get(Revision, uuid.UUID(before_id))
        after = db.get(Revision, uuid.UUID(after_id))
        before_artifact = ArtifactFile(
            revision_id=before.id,
            working_version_id=before.current_working_version_id,
            kind="DRAWING",
            storage_key=f"comparison/{uuid.uuid4()}/drawing-a.pdf",
            original_filename="housing.pdf",
            mime_type="application/pdf",
            byte_size=1000,
            sha256="f" * 64,
            state="VERIFIED",
            required=False,
        )
        after_artifact = ArtifactFile(
            revision_id=after.id,
            working_version_id=after.current_working_version_id,
            kind="DRAWING",
            storage_key=f"comparison/{uuid.uuid4()}/drawing-b.pdf",
            original_filename="housing.pdf",
            mime_type="application/pdf",
            byte_size=1100,
            sha256="0" * 64,
            state="VERIFIED",
            required=False,
        )
        db.add_all([before_artifact, after_artifact])
        db.flush()
        before.derived_metadata_json = {
            **before.derived_metadata_json,
            "drawings": [
                {
                    "stable_id": "main-drawing",
                    "artifact_id": str(before_artifact.id),
                    "filename": "housing.pdf",
                    "page_count": 1,
                    "worker_contract_version": "drawing-v1",
                    "sections": [
                        {
                            "stable_id": "notes",
                            "kind": "NOTES",
                            "page": 1,
                            "text": "REMOVE ALL BURRS",
                        },
                        {
                            "stable_id": "revision-table",
                            "kind": "REVISION_TABLE",
                            "page": 1,
                            "text": "REV A INITIAL RELEASE",
                        },
                    ],
                    "entities": [
                        {
                            "stable_id": "tol-1",
                            "type": "TOLERANCE",
                            "page": 1,
                            "value": 0.2,
                            "unit": "mm",
                        }
                    ],
                }
            ],
        }
        after.derived_metadata_json = {
            **after.derived_metadata_json,
            "drawings": [
                {
                    "stable_id": "main-drawing",
                    "artifact_id": str(after_artifact.id),
                    "filename": "housing.pdf",
                    "page_count": 2,
                    "worker_contract_version": "drawing-v1",
                    "sections": [
                        {
                            "stable_id": "notes",
                            "kind": "NOTES",
                            "page": 1,
                            "text": " REMOVE   ALL\nBURRS ",
                        },
                        {
                            "stable_id": "revision-table",
                            "kind": "REVISION_TABLE",
                            "page": 1,
                            "text": "REV B TIGHTEN TOLERANCE",
                        },
                    ],
                    "entities": [
                        {
                            "stable_id": "tol-1",
                            "type": "TOLERANCE",
                            "page": 1,
                            "value": 0.1,
                            "unit": "mm",
                        }
                    ],
                }
            ],
        }
        db.commit()

    response = client.post(
        f"/api/v1/items/{item_id}/revision-comparisons",
        headers=AUTH,
        json={
            "from_revision_id": before_id,
            "to_revision_id": after_id,
            "domains": ["DRAWING"],
        },
    )
    assert response.status_code == 201, response.text
    domain = response.json()["domains"][0]
    assert domain["status"] == "COMPLETED"
    assert domain["result"]["comparison_level"] == "STRUCTURED"
    assert domain["result"]["file_change_count"] == 3
    assert domain["result"]["text_change_count"] == 1
    assert domain["result"]["structured_change_count"] == 1
    assert domain["result"]["capabilities"]["visual"] is False
    assert domain["result"]["limitations"] == ["Visual page diff is not implemented"]
    assert not any("sections.notes" in row["property_path"] for row in domain["changes"])
    tolerance = next(
        row for row in domain["changes"] if row["property_path"].endswith("entities.tol-1")
    )
    assert tolerance["significance"] == "MAJOR"
    assert tolerance["unit"] == "mm"
    assert tolerance["metadata"]["page"] == 1
    assert tolerance["metadata"]["worker_contract_version"] == "drawing-v1"


def test_phase56_persists_citation_safe_deterministic_explanation() -> None:
    item_id, before_id, after_id = _comparison_revisions()
    comparison = client.post(
        f"/api/v1/items/{item_id}/revision-comparisons",
        headers=AUTH,
        json={
            "from_revision_id": before_id,
            "to_revision_id": after_id,
            "domains": ["METADATA", "PHYSICAL_PROPERTIES", "DRAWING"],
        },
    )
    assert comparison.status_code == 201, comparison.text
    comparison_id = comparison.json()["id"]

    app.dependency_overrides[get_settings] = lambda: Settings(
        ai_gateway_base_url=None,
        ai_chat_model="",
        ai_embedding_model="",
    )
    try:
        explained = client.post(
            f"/api/v1/revision-comparisons/{comparison_id}/explanations",
            headers=AUTH,
            json={"level": "ENGINEERING"},
        )
        assert explained.status_code == 201, explained.text
        body = explained.json()

        repeated = client.post(
            f"/api/v1/revision-comparisons/{comparison_id}/explanations",
            headers=AUTH,
            json={"level": "ENGINEERING"},
        )
        assert repeated.status_code == 201
        assert repeated.json()["id"] == body["id"]
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert body["comparison_id"] == comparison_id
    assert body["mode"] == "DETERMINISTIC"
    assert body["provider"] == "deterministic"
    assert body["facts"]
    valid_keys = {
        change["change_key"]
        for domain in comparison.json()["domains"]
        if domain["status"] in {"COMPLETED", "UNCHANGED"}
        for change in domain["changes"]
    }
    assert set(body["cited_change_keys"]) <= valid_keys
    assert all(set(fact["change_keys"]) <= valid_keys for fact in body["facts"])
    assert body["cause"]["recorded"] is False
    assert body["cause"]["source_ids"] == []
    assert any("DRAWING domain is unsupported" in warning for warning in body["warnings"])
    assert any("deterministic summary used" in warning for warning in body["warnings"])
