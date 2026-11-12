"""Executable examples of the source-level feature traversal maintenance contract."""

import uuid
from datetime import UTC, datetime

import pytest
from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.assistant.feature_search import AssemblyFeatureSearch, validate_feature_question
from modelryx.core.auth import Principal
from modelryx.core.db import SessionLocal
from modelryx.models import (
    Artifact,
    BomLine,
    BomViewRevision,
    Item,
    MetadataExtraction,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    User,
)
from sqlalchemy import select

client = TestClient(app)
ADMIN = Principal(subject="local-development-user", display_name="Admin", role="administrator")


def _node(
    db,
    *,
    assembly=False,
    holes=0,
    fillets=0,
    quality="complete",
    item=None,
    owner=None,
    state="READY",
    code="A",
):
    owner = owner or db.scalar(
        select(User).where(
            User.external_subject == "local-development-user",
        )
    )
    if item is None:
        item = Item(
            part_number=f"000-GRAPH-{uuid.uuid4().hex}",
            name="Feature graph fixture",
            part_type="ASSEMBLY" if assembly else "PART",
            created_by=owner.id,
        )
        db.add(item)
        db.flush()
    revision = Revision(
        part_id=item.id,
        revision_code=code,
        cad_system="FreeCAD",
        native_format="FCStd",
        metadata_json={},
        derived_metadata_json={},
        state=state,
        created_by=owner.id,
    )
    db.add(revision)
    db.flush()
    item.current_revision_id = revision.id
    snapshot = RevisionContentSnapshot(
        revision_id=revision.id, sequence=1, commit_id=uuid.uuid4(), created_by=owner.id
    )
    db.add(snapshot)
    db.flush()
    extraction = MetadataExtraction(
        revision_id=revision.id,
        snapshot_id=snapshot.id,
        schema_version="1.0",
        worker_type="test",
        worker_version="1",
        source_system="FreeCAD",
        status="PARTIAL" if quality != "complete" else "READY",
        extracted_at=datetime.now(UTC),
        metadata_json={"quality": {"features": quality}},
        derived_json={},
        kind=item.part_type,
        material="feature-test-material",
        hole_count=holes,
        fillet_count=fillets,
        bbox_x_mm=1,
        bbox_y_mm=1,
        bbox_z_mm=1,
        bbox_min_mm=1,
        bbox_mid_mm=1,
        bbox_max_mm=1,
        volume_mm3=1,
        surface_area_mm2=6,
    )
    db.add(extraction)
    db.flush()
    revision.current_metadata_extraction_id = extraction.id
    if assembly:
        db.add(
            BomViewRevision(
                revision_id=revision.id, snapshot_id=snapshot.id, sequence=1, created_by=owner.id
            )
        )
    db.flush()
    return item, revision


def _line(db, parent, child, quantity, *, suppressed=False, visible=True):
    bom = db.scalar(select(BomViewRevision).where(BomViewRevision.revision_id == parent.id))
    line = BomLine(
        bom_view_revision_id=bom.id,
        parent_revision_id=parent.id,
        child_revision_id=child.id,
        occurrence_key=uuid.uuid4().hex,
        quantity=quantity,
        is_suppressed=suppressed,
        is_visible=visible,
    )
    db.add(line)
    db.flush()
    return line


def test_nested_quantities_repeated_paths_and_assembly_counts_are_not_double_counted():
    with SessionLocal() as db:
        _, part = _node(db, holes=2, fillets=1)
        _, sub = _node(db, assembly=True, holes=400, fillets=500)
        root_item, root = _node(db, assembly=True, holes=900, fillets=800)
        _line(db, sub, part, 3)
        _line(db, root, sub, 2)
        _line(db, root, part, 4)
        _line(db, root, part, 5, visible=False)
        _line(db, root, part, 100, suppressed=True)
        db.commit()
        report = AssemblyFeatureSearch(db, ADMIN).count(root.id, ["hole", "fillet"])
        assert report.totals == {"hole": 30, "fillet": 15}
        assert report.complete == {"hole": True, "fillet": True}
        assert sorted(row["quantity"] for row in report.components) == [4, 5, 6]
        assert len(report.assembly_features) == 2
        assert "not added to component totals" in report.text()

    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "How many holes and fillets are in this assembly?",
            "context": {"item": {"item_id": str(root_item.id), "revision_id": str(root.id)}},
        },
    )
    assert response.status_code == 201, response.text
    assert "30 recorded hole(s)" in response.json()["answer"]
    assert "15 recorded fillet(s)" in response.json()["answer"]


def test_discovery_follows_historical_referenced_revision_and_retains_multiple_paths():
    with SessionLocal() as db:
        part_item, old_part = _node(db, holes=2)
        _, latest_part = _node(db, item=part_item, holes=99, code="B")
        _, sub = _node(db, assembly=True)
        _, root = _node(db, assembly=True)
        sub_line = _line(db, sub, old_part, 3)
        root_line = _line(db, root, sub, 2)
        direct_line = _line(db, root, old_part, 4)
        _, suppressed_parent = _node(db, assembly=True)
        _line(db, suppressed_parent, old_part, 1, suppressed=True)
        db.commit()
        reports, paths, _ = AssemblyFeatureSearch(db, ADMIN).discover(["hole"], limit=1000)
        by_id = {report.revision.id: report for report in reports}
        assert {old_part.id, latest_part.id, sub.id, root.id} <= set(by_id)
        assert suppressed_parent.id not in by_id
        assert by_id[root.id].totals["hole"] == 20
        assert sorted(paths[root.id]) == sorted(
            [
                [str(root_line.id), str(sub_line.id)],
                [str(direct_line.id)],
            ]
        )
        assert len([report for report in reports if report.revision.id == root.id]) == 1


@pytest.mark.parametrize(
    "holes,quality,total,complete",
    [
        (None, "complete", 0, False),
        (0, "partial", 0, False),
        (0, "complete", 0, True),
        (2, "partial", 6, False),
    ],
)
def test_coverage_distinguishes_missing_partial_zero_and_confirmed_zero(
    holes, quality, total, complete
):
    with SessionLocal() as db:
        _, child = _node(db, holes=holes, quality=quality)
        _, parent = _node(db, assembly=True)
        _line(db, parent, child, 3)
        db.commit()
        report = AssemblyFeatureSearch(db, ADMIN).count(parent.id, ["hole"])
        assert report.totals["hole"] == total
        assert report.complete["hole"] is complete
        if holes is None or (holes == 0 and not complete):
            assert "unknown" in report.text()
        if total and not complete:
            assert "At least 6" in report.text()


def test_inaccessible_child_is_redacted_and_makes_totals_incomplete():
    with SessionLocal() as db:
        other = User(
            external_subject=f"private-{uuid.uuid4()}", display_name="Other", role="engineer"
        )
        db.add(other)
        db.flush()
        private_item, private = _node(db, holes=200, owner=other, state="DRAFT")
        _, visible = _node(db, holes=2)
        _, root = _node(db, assembly=True)
        _line(db, root, private, 1)
        _line(db, root, visible, 3)
        db.commit()
        reader = Principal(subject="local-development-user", display_name="Reader", role="reader")
        report = AssemblyFeatureSearch(db, reader).count(root.id, ["hole"])
        assert report.totals["hole"] == 6
        assert report.complete["hole"] is False
        assert private_item.part_number not in report.text()
        assert str(private.id) not in str(report.summary())
        assert "inaccessible" in report.text()
        reports, _, _ = AssemblyFeatureSearch(db, reader).discover(["hole"], limit=1000)
        by_id = {r.revision.id: r for r in reports}
        assert private.id not in by_id
        assert by_id[root.id].totals["hole"] == 6
        assert str(private.id) not in str(by_id[root.id].summary())


def test_cycles_limits_and_stale_bom_are_never_reported_as_complete(monkeypatch):
    with SessionLocal() as db:
        _, part = _node(db, holes=2)
        _, root = _node(db, assembly=True)
        _line(db, root, part, 1)
        _line(db, root, root, 1)
        db.commit()
        report = AssemblyFeatureSearch(db, ADMIN).count(root.id, ["hole"])
        assert report.totals["hole"] == 2
        assert not report.complete["hole"]
        assert "cycle" in report.text()
        monkeypatch.setattr("modelryx.assistant.feature_search.MAX_OCCURRENCES", 1)
        limited = AssemblyFeatureSearch(db, ADMIN).count(root.id, ["hole"])
        assert not limited.complete["hole"] and "limited" in limited.text()
        db.add(
            RevisionContentSnapshot(
                revision_id=root.id, sequence=2, commit_id=uuid.uuid4(), created_by=root.created_by
            )
        )
        db.commit()
        stale = AssemblyFeatureSearch(db, ADMIN).count(root.id, ["hole"])
        assert not stale.complete["hole"] and "BOM coverage is unavailable" in stale.text()


def test_global_and_revision_endpoints_use_feature_evidence_without_ai_documents(monkeypatch):
    # Isolate discovery candidates from unrelated positive-feature fixtures.
    with SessionLocal() as db:
        item, part = _node(db, holes=2)
        parent_item, parent = _node(db, assembly=True)
        _line(db, parent, part, 4)
        db.commit()
        target_ids = {part.id, parent.id}
        item_id, parent_item_id = item.id, parent_item.id
    original = AssemblyFeatureSearch.discover

    def discover(self, features, **kwargs):
        reports, paths, warnings = original(self, features, **{**kwargs, "limit": 1000})
        return [r for r in reports if r.revision.id in target_ids], paths, warnings

    monkeypatch.setattr(AssemblyFeatureSearch, "discover", discover)
    global_response = client.post(
        "/api/v1/ai/query", headers=AUTH, json={"question": "Find parts with holes"}
    )
    assert global_response.status_code == 200, global_response.text
    sources = global_response.json()["sources"]
    assert {row["revision_id"] for row in sources} == {str(part.id), str(parent.id)}
    parent_source = next(row for row in sources if row["revision_id"] == str(parent.id))
    assert parent_source["metadata"]["totals"]["hole"] == 8
    assert parent_source["metadata"]["discovery_paths"]
    assert str(item_id) in str(sources) and str(parent_item_id) in str(sources)
    chat = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Find parts with holes",
            "context": {"workspace": {"page": "ai"}},
        },
    )
    assert chat.status_code == 201, chat.text
    assert {row["revision_id"] for row in chat.json()["references"]} == {
        str(value) for value in target_ids
    }
    rejected = client.post(
        "/api/v1/ai/query", headers=AUTH, json={"question": "Find aluminum parts with holes"}
    )
    assert rejected.status_code == 422


@pytest.mark.parametrize(
    "worker_version,same_source,expected_complete",
    [
        (True, True, True),
        (False, True, False),
        (True, False, False),
    ],
)
def test_legacy_worker_versions_inherit_bom_only_with_proven_unchanged_source(
    worker_version,
    same_source,
    expected_complete,
):
    with SessionLocal() as db:
        _, child = _node(db, holes=2)
        _, parent = _node(db, assembly=True)
        _line(db, parent, child, 3)
        baseline = db.scalar(
            select(RevisionContentSnapshot).where(
                RevisionContentSnapshot.revision_id == parent.id,
            )
        )
        source = Artifact(
            artifact_type="CAD_ASSEMBLY",
            name="source.FCStd",
            classification="AUTHORITATIVE",
            source="USER",
            state="READY",
            created_by=parent.created_by,
        )
        db.add(source)
        db.flush()
        relation = RevisionArtifactRelation(
            revision_id=parent.id,
            artifact_id=source.id,
            relation_type="SPECIFICATION",
            is_primary=True,
            created_by=parent.created_by,
        )
        db.add(relation)
        db.flush()
        db.add(RevisionContentSnapshotArtifact(snapshot_id=baseline.id, relation_id=relation.id))
        current = RevisionContentSnapshot(
            revision_id=parent.id,
            sequence=2,
            commit_id=uuid.uuid4(),
            comment="Worker-derived Artifact publication" if worker_version else "CAD commit",
            created_by=parent.created_by,
        )
        db.add(current)
        db.flush()
        if same_source:
            db.add(RevisionContentSnapshotArtifact(snapshot_id=current.id, relation_id=relation.id))
        db.commit()
        report = AssemblyFeatureSearch(db, ADMIN).count(parent.id, ["hole"])
        assert report.complete["hole"] is expected_complete
        assert report.totals["hole"] == (6 if expected_complete else 0)
        reports, _, _ = AssemblyFeatureSearch(db, ADMIN).discover(["hole"], limit=1000)
        assert (parent.id in {r.revision.id for r in reports}) is expected_complete


def test_features_in_different_components_match_parent_without_double_counting():
    with SessionLocal() as db:
        _, holes = _node(db, holes=2, fillets=0)
        _, fillets = _node(db, holes=0, fillets=3)
        _, parent = _node(db, assembly=True)
        _line(db, parent, holes, 2)
        _line(db, parent, fillets, 4)
        db.commit()
        reports, _, _ = AssemblyFeatureSearch(db, ADMIN).discover(["hole", "fillet"], limit=1000)
        by_id = {r.revision.id: r for r in reports}
        assert parent.id in by_id and holes.id not in by_id and fillets.id not in by_id
        assert by_id[parent.id].totals == {"hole": 4, "fillet": 12}
        any_reports, _, _ = AssemblyFeatureSearch(db, ADMIN).discover(
            ["hole", "fillet"],
            limit=1000,
            match_any=True,
        )
        assert {holes.id, fillets.id, parent.id} <= {r.revision.id for r in any_reports}


def test_depth_and_discovery_limits_are_explicit(monkeypatch):
    with SessionLocal() as db:
        _, child = _node(db, holes=2)
        _, parent = _node(db, assembly=True)
        _line(db, parent, child, 3)
        db.commit()
        monkeypatch.setattr("modelryx.assistant.feature_search.MAX_DEPTH", 0)
        report = AssemblyFeatureSearch(db, ADMIN).count(parent.id, ["hole"])
        assert not report.complete["hole"] and "depth" in report.text()
        monkeypatch.setattr("modelryx.assistant.feature_search.MAX_DEPTH", 20)
        reports, _, warnings = AssemblyFeatureSearch(db, ADMIN).discover(["hole"], limit=1)
        assert len(reports) == 1 and any("truncated" in warning for warning in warnings)


def test_stale_child_extraction_is_unknown_and_does_not_seed_discovery():
    with SessionLocal() as db:
        _, child = _node(db, holes=8)
        _, parent = _node(db, assembly=True)
        _line(db, parent, child, 3)
        db.add(
            RevisionContentSnapshot(
                revision_id=child.id,
                sequence=2,
                commit_id=uuid.uuid4(),
                created_by=child.created_by,
            )
        )
        db.commit()
        report = AssemblyFeatureSearch(db, ADMIN).count(parent.id, ["hole"])
        assert report.totals["hole"] == 0 and not report.complete["hole"]
        reports, _, _ = AssemblyFeatureSearch(db, ADMIN).discover(["hole"], limit=1000)
        assert child.id not in {r.revision.id for r in reports}
        assert parent.id not in {r.revision.id for r in reports}


def test_partial_native_structure_keeps_managed_component_totals_as_lower_bounds():
    with SessionLocal() as db:
        _, child = _node(db, holes=2)
        _, parent = _node(db, assembly=True)
        _line(db, parent, child, 3)
        extraction = db.get(MetadataExtraction, parent.current_metadata_extraction_id)
        extraction.metadata_json = {"quality": {"features": "complete", "assembly": "partial"}}
        db.commit()
        report = AssemblyFeatureSearch(db, ADMIN).count(parent.id, ["hole"])
        assert report.totals["hole"] == 6 and not report.complete["hole"]
        assert "At least 6" in report.text()
        assert "structure coverage is incomplete" in report.text()


@pytest.mark.parametrize(
    "question",
    [
        "Find red parts with holes",
        "Find parts with 6 mm holes",
        "Find parts without holes",
        "Find aluminum parts with holes",
        "Find parts with holes " + "x" * 2100,
    ],
)
def test_discovery_does_not_silently_drop_unimplemented_query_constraints(question):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        validate_feature_question(question, discovery=True)
    assert error.value.status_code == 422
