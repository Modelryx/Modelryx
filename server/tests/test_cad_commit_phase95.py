import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import (
    Artifact,
    ArtifactFile,
    CADExecution,
    CADOperationPlan,
    CADSession,
    Checkout,
    Item,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    User,
    WorkingVersion,
)
from modelryx.services.storage import LocalStorageProvider
from sqlalchemy import select

client = TestClient(app)


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _part_metadata() -> dict:
    return {
        "schema": "modelryx.part-metadata",
        "schema_version": "1.0",
        "source": {
            "system": "freecad",
            "system_version": "1.0",
            "worker": "modelryx-freecad-worker",
            "worker_version": "phase9-test",
            "extracted_at": "2026-08-27T12:00:00Z",
        },
        "identity": {"name": "Phase 9 result", "kind": "part"},
        "geometry": {
            "bounding_box": {"x_mm": 100, "y_mm": 80, "z_mm": 10},
            "shape": {"volume_mm3": 79000, "surface_area_mm2": 19300},
            "topology": {"solid_count": 1},
        },
        "physical": {"material": {"name": "Aluminum 7075-T6"}},
        "assembly": {"is_assembly": False},
        "quality": {
            "geometry": "complete",
            "physical": "complete",
            "features": "unsupported",
            "assembly": "not_applicable",
            "warnings": [],
        },
    }


@pytest.mark.parametrize("operation", ["NEW_VERSION", "NEW_REVISION"])
def test_verified_result_requires_second_confirmation_and_commits_once(operation: str) -> None:
    settings = get_settings()
    storage = LocalStorageProvider(settings.storage_root)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        item = Item(
            part_number=f"CAD95-{uuid.uuid4().hex}",
            name="CAD phase 9.5",
            created_by=user.id,
        )
        db.add(item)
        db.flush()
        source_revision = Revision(
            part_id=item.id,
            revision_code="A",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={"schema_version": 1},
            state="READY",
            created_by=user.id,
        )
        db.add(source_revision)
        db.flush()
        source_version = WorkingVersion(
            revision_id=source_revision.id,
            version_number=1,
            state="READY",
            metadata_json=source_revision.metadata_json,
            created_by=user.id,
        )
        db.add(source_version)
        db.flush()
        source_key = f"phase9-tests/{uuid.uuid4()}/source.FCStd"
        source_path = storage.create_upload(source_key)
        source_path.write_bytes(b"immutable-source")
        source_hash = _sha(source_path)
        source_version.source_sha256 = source_hash
        source_container = Artifact(
            artifact_type="CAD_MODEL",
            name="source.FCStd",
            classification="AUTHORITATIVE",
            source="USER",
            state="READY",
            created_by=user.id,
        )
        db.add(source_container)
        db.flush()
        source_artifact = ArtifactFile(
            artifact_id=source_container.id,
            reference_name="MODEL",
            role="PRIMARY",
            revision_id=source_revision.id,
            working_version_id=source_version.id,
            kind="NATIVE",
            storage_key=source_key,
            original_filename="source.FCStd",
            mime_type="application/octet-stream",
            byte_size=source_path.stat().st_size,
            sha256=source_hash,
            state="VERIFIED",
            required=True,
        )
        db.add(source_artifact)
        db.flush()
        source_snapshot = RevisionContentSnapshot(
            revision_id=source_revision.id,
            sequence=1,
            commit_id=uuid.uuid4(),
            created_by=user.id,
        )
        db.add(source_snapshot)
        db.flush()
        source_relation = RevisionArtifactRelation(
            revision_id=source_revision.id,
            artifact_id=source_container.id,
            relation_type="SPECIFICATION",
            sequence=0,
            is_primary=True,
            created_by=user.id,
        )
        db.add(source_relation)
        db.flush()
        db.add(
            RevisionContentSnapshotArtifact(
                snapshot_id=source_snapshot.id,
                relation_id=source_relation.id,
                sequence=0,
            )
        )
        source_revision.current_working_version_id = source_version.id
        item.current_revision_id = source_revision.id
        for code in ("B", "C"):
            later_revision = Revision(
                part_id=item.id,
                revision_code=code,
                cad_system="FreeCAD",
                native_format="FCStd",
                metadata_json={"schema_version": 1},
                state="READY",
                created_by=user.id,
            )
            db.add(later_revision)
            db.flush()
            later_version = WorkingVersion(
                revision_id=later_revision.id,
                version_number=1,
                state="READY",
                metadata_json=later_revision.metadata_json,
                created_by=user.id,
            )
            db.add(later_version)
            db.flush()
            later_revision.current_working_version_id = later_version.id
            item.current_revision_id = later_revision.id
        session_id = uuid.uuid4()
        workspace_key = f"cad-sessions/{session_id}"
        workspace = settings.storage_root.resolve() / workspace_key
        workspace.mkdir(parents=True)
        (workspace / "source.fcstd").write_bytes(b"immutable-source")
        result_dir = workspace / "execution-result"
        result_dir.mkdir()
        files = {
            "native": (result_dir / "result.fcstd", b"verified-result"),
            "glb": (result_dir / "model.glb", b"glTF-result"),
            "preview": (
                result_dir / "model-preview.json",
                json.dumps({"schemaVersion": 1, "items": []}).encode(),
            ),
            "raw_metadata": (result_dir / "cad-raw-metadata.json", b"{}"),
            "part_metadata": (
                result_dir / "part-metadata.json",
                json.dumps(_part_metadata()).encode(),
            ),
        }
        for path, content in files.values():
            path.write_bytes(content)
        session = CADSession(
            id=session_id,
            revision_id=source_revision.id,
            source_artifact_id=source_artifact.id,
            owner_id=user.id,
            worker_id="freecad-controlled",
            status="READY_TO_REVIEW",
            source_sha256=source_hash,
            workspace_key=workspace_key,
            lock_version=3,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(session)
        db.flush()
        plan = CADOperationPlan(
            session_id=session.id,
            status="SUCCEEDED",
            risk="MEDIUM",
            operation="material.set",
            target_id="document",
            parameter="material",
            expected_value_json={"value": "Aluminum 6061-T6"},
            new_value_json={"value": "Aluminum 7075-T6"},
            preview_json={},
            state_token="a" * 64,
            requested_by=user.id,
            confirmed_by=user.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(plan)
        db.flush()
        execution = CADExecution(
            plan_id=plan.id,
            status="SUCCEEDED",
            idempotency_key=f"execute-{uuid.uuid4()}",
            result_json={
                "output_sha256": _sha(files["native"][0]),
                "artifacts": {
                    key: str(path.relative_to(workspace)).replace("\\", "/")
                    for key, (path, _) in files.items()
                },
                "artifact_sha256": {key: _sha(path) for key, (path, _) in files.items()},
                "comparison": {"changed": {"material": {}}},
                "validation": {"engine": "cad-postconditions-v1", "passed": True},
            },
            completed_at=datetime.now(UTC),
        )
        db.add(execution)
        db.add(
            CADOperationPlan(
                session_id=session.id,
                status="CANCELLED",
                risk="MEDIUM",
                operation="sketch.create",
                target_id="Body",
                parameter="sketch",
                expected_value_json={"value": {}},
                new_value_json={
                    "value": {
                        "feature_id": "Modelryx_persistence_test",
                        "plane": "XY",
                        "geometry": "circle",
                        "radius_mm": 3,
                    }
                },
                preview_json={},
                state_token="b" * 64,
                requested_by=user.id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                decided_at=datetime.now(UTC),
            )
        )
        db.add(
            Checkout(
                part_id=item.id,
                revision_id=source_revision.id,
                base_snapshot_id=source_snapshot.id,
                owner_id=user.id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        db.commit()
        session_id_text = str(session.id)
        item_id = item.id
        source_revision_id = source_revision.id
        prior_current_revision_id = item.current_revision_id

    result = client.get(f"/api/v1/cad-sessions/{session_id_text}/result", headers=AUTH)
    assert result.status_code == 200
    assert result.json()["execution"]["result"]["validation"]["passed"] is True
    proposed = client.post(f"/api/v1/cad-sessions/{session_id_text}/commit-plans", headers=AUTH)
    assert proposed.status_code == 201
    assert proposed.json()["revision_code"] == "D"
    key = f"cad-commit-{uuid.uuid4()}"
    confirmed = client.post(
        f"/api/v1/cad-commit-plans/{proposed.json()['id']}/confirm",
        headers=AUTH,
        json={
            "confirmed": True,
            "operation": operation,
            "revision_code": "D",
            "description": "Material updated for the next released design.",
            "idempotency_key": key,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    replay = client.post(
        f"/api/v1/cad-commit-plans/{proposed.json()['id']}/confirm",
        headers=AUTH,
        json={
            "confirmed": True,
            "operation": operation,
            "revision_code": "D",
            "description": "Material updated for the next released design.",
            "idempotency_key": key,
        },
    )
    assert replay.json()["revision_id"] == confirmed.json()["revision_id"]
    with SessionLocal() as db:
        item = db.get(Item, item_id)
        source_revision = db.get(Revision, source_revision_id)
        created = db.get(Revision, uuid.UUID(confirmed.json()["revision_id"]))
        assert source_revision is not None
        assert created.revision_code == ("D" if operation == "NEW_REVISION" else "A")
        if operation == "NEW_REVISION":
            assert item.current_revision_id == created.id
            assert created.description == "Material updated for the next released design."
        else:
            assert created.id == source_revision.id
            assert item.current_revision_id == prior_current_revision_id
            current_snapshot = db.scalar(
                select(RevisionContentSnapshot)
                .where(RevisionContentSnapshot.revision_id == created.id)
                .order_by(RevisionContentSnapshot.sequence.desc())
            )
            assert current_snapshot.sequence == 2
        assert created.metadata_status in {"READY", "PARTIAL"}
        created_artifacts = list(
            db.scalars(select(ArtifactFile).where(ArtifactFile.revision_id == created.id))
        )
        assert len(created_artifacts) == (5 if operation == "NEW_REVISION" else 6)
        created_native = next(row for row in created_artifacts if row.kind == "NATIVE")
        assert created_native.original_filename == "source.FCStd"
        assert created_native.storage_key.endswith("/source.FCStd")
    assert source_path.read_bytes() == b"immutable-source"
