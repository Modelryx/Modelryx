import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from artifact_helpers import commit_item
from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.auth import Principal, create_access_token
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import (
    Artifact,
    ArtifactFile,
    Part,
    ProcessingJob,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    User,
    WorkerHeartbeat,
)
from modelryx.services.processing import (
    ProcessingCancelled,
    check_processing_cancelled,
    fail_job,
    process_job,
    read_converter_package,
    read_worker_metadata_sidecars,
    recover_abandoned_jobs,
)
from sqlalchemy import select

client = TestClient(app)

def test_running_freecad_job_can_be_cancelled_at_safe_boundary() -> None:
    completed = commit_item(
        client,
        item_number=f"CANCEL-RUNNING-{uuid.uuid4().hex}",
        name="Running cancellation test",
        files=[("cancel.FCStd", b"cancel")],
    )
    revision_id = uuid.UUID(completed["revision_id"])
    with SessionLocal() as db:
        job = db.scalar(
            select(ProcessingJob).where(ProcessingJob.revision_id == revision_id)
        )
        job.state = "RUNNING"
        job.stage = "EXPORTING_GEOMETRY"
        job.attempt_count = 1
        job.started_at = datetime.now(UTC)
        db.commit()
        job_id = job.id

    requested = client.post(f"/api/v1/processing-jobs/{job_id}/cancel", headers=AUTH)
    assert requested.status_code == 200, requested.text
    assert requested.json()["state"] == "RUNNING"
    assert requested.json()["stage"] == "CANCELLING"

    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        with pytest.raises(ProcessingCancelled):
            check_processing_cancelled(db, job)
        fail_job(db, job, ProcessingCancelled("Processing was cancelled by an authorized user"))
        assert job.state == "FAILED"
        assert job.stage == "CANCELLED"
        assert job.diagnostics_json["errors"][0]["code"] == "CANCELLED"


def test_abandoned_job_recovery_respects_live_worker_heartbeat() -> None:
    completed = commit_item(
        client,
        item_number=f"RECOVERY-{uuid.uuid4().hex}",
        name="Recovery test",
        files=[("recovery.FCStd", b"recovery")],
    )
    revision_id = uuid.UUID(completed["revision_id"])
    now = datetime.now(UTC)
    settings = get_settings().model_copy(
        update={"processing_stale_seconds": 60, "worker_heartbeat_timeout_seconds": 30}
    )
    with SessionLocal() as db:
        abandoned = db.scalar(
            select(ProcessingJob).where(ProcessingJob.revision_id == revision_id)
        )
        abandoned.state = "RUNNING"
        abandoned.stage = "EXPORTING_GEOMETRY"
        abandoned.attempt_count = 1
        abandoned.started_at = now - timedelta(minutes=10)
        abandoned.diagnostics_json = {
            "files": {"source": {"status": "PROCESSING", "stage": "EXPORTING_GEOMETRY"}}
        }
        live = ProcessingJob(
            revision_id=revision_id,
            snapshot_id=abandoned.snapshot_id,
            type="FCSTD_TO_MODELRYX",
            state="RUNNING",
            stage="EXPORTING_GEOMETRY",
            attempt_count=1,
            started_at=now - timedelta(minutes=10),
            idempotency_key=f"live:{uuid.uuid4()}",
        )
        exhausted = ProcessingJob(
            revision_id=revision_id,
            snapshot_id=abandoned.snapshot_id,
            type="FCSTD_TO_MODELRYX",
            state="RUNNING",
            stage="OPENING_DOCUMENT",
            attempt_count=3,
            started_at=now - timedelta(minutes=10),
            idempotency_key=f"exhausted:{uuid.uuid4()}",
        )
        db.add_all([live, exhausted])
        db.flush()
        db.add(
            WorkerHeartbeat(
                worker_type="FREECAD",
                machine_name="live-test-worker",
                worker_version="test",
                adapter_version="test",
                compatibility_state="COMPATIBLE",
                capabilities_json={},
                status="BUSY",
                current_job_id=live.id,
                last_heartbeat_at=now,
            )
        )
        db.commit()

        result = recover_abandoned_jobs(db, settings)
        db.commit()

        assert result.requeued == 1
        assert result.failed == 1
        assert abandoned.state == "PENDING"
        assert abandoned.stage == "QUEUED"
        assert abandoned.started_at is None
        assert abandoned.diagnostics_json["files"]["source"]["stage"] == (
            "RECOVERED_AFTER_WORKER_LOSS"
        )
        assert live.state == "RUNNING"
        assert exhausted.state == "FAILED"
        assert exhausted.diagnostics_json["errors"][0]["code"] == "WORKER_LEASE_EXPIRED"


def test_converter_package_rejects_invalid_glb(tmp_path: Path) -> None:
    package_path = tmp_path / "bad.pfg"
    with zipfile.ZipFile(package_path, "w") as package:
        package.writestr(
            "manifest.json",
            json.dumps({"format": "Modelryx Graphics", "version": 1}),
        )
        package.writestr("model.glb", b"not-a-glb")
        package.writestr("model-preview.json", json.dumps({"schemaVersion": 1, "items": []}))
    try:
        read_converter_package(package_path, tmp_path, get_settings())
    except RuntimeError as error:
        assert "invalid GLB" in str(error)
    else:
        raise AssertionError("invalid GLB package was accepted")


def test_converter_package_rejects_invalid_part_metadata(tmp_path: Path) -> None:
    package_path = tmp_path / "bad-metadata.pfg"
    package_path.with_suffix(".cad-raw-metadata.json").write_text("{}")
    package_path.with_suffix(".part-metadata.json").write_text(
        json.dumps({"schema": "wrong", "schema_version": "1.0"})
    )
    with pytest.raises(RuntimeError, match="invalid PartMetadata"):
        read_worker_metadata_sidecars(package_path)


def test_item_creator_or_administrator_can_control_processing_jobs() -> None:
    completed = commit_item(
        client,
        item_number=f"PROCESS-CONTROL-{uuid.uuid4().hex}",
        name="Processing control test",
        files=[("control.FCStd", b"processing control")],
    )
    revision_id = completed["revision_id"]

    jobs = client.get(f"/api/v1/revisions/{revision_id}/processing-jobs", headers=AUTH)
    assert jobs.status_code == 200, jobs.text
    pending = next(job for job in jobs.json() if job["state"] == "PENDING")
    assert pending["can_control"] is True

    cancelled = client.post(
        f"/api/v1/processing-jobs/{pending['id']}/cancel", headers=AUTH
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "FAILED"
    assert cancelled.json()["stage"] == "CANCELLED"

    retried = client.post(
        f"/api/v1/processing-jobs/{pending['id']}/retry", headers=AUTH
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["state"] == "PENDING"
    assert retried.json()["can_control"] is True


def test_non_administrator_processing_control_is_limited_to_item_creator() -> None:
    completed = commit_item(
        client,
        item_number=f"PROCESS-OWNER-{uuid.uuid4().hex}",
        name="Processing ownership test",
        files=[("owner.FCStd", b"processing ownership")],
    )
    with SessionLocal() as db:
        owner = User(
            external_subject=f"owner-{uuid.uuid4().hex}",
            display_name="Item Owner",
            role="engineer",
            is_active=True,
            auth_version=0,
        )
        other = User(
            external_subject=f"other-{uuid.uuid4().hex}",
            display_name="Other Designer",
            role="engineer",
            is_active=True,
            auth_version=0,
        )
        db.add_all([owner, other])
        db.flush()
        revision = db.get(Revision, uuid.UUID(completed["revision_id"]))
        db.get(Part, revision.part_id).created_by = owner.id
        owner_subject = owner.external_subject
        other_subject = other.external_subject
        db.commit()

    def headers(subject: str, name: str) -> dict[str, str]:
        token = create_access_token(
            Principal(subject=subject, display_name=name, role="engineer")
        )
        return {"Authorization": f"Bearer {token}"}

    owner_jobs = client.get(
        f"/api/v1/revisions/{completed['revision_id']}/processing-jobs",
        headers=headers(owner_subject, "Item Owner"),
    )
    assert owner_jobs.status_code == 200, owner_jobs.text
    pending = next(job for job in owner_jobs.json() if job["state"] == "PENDING")
    assert pending["can_control"] is True
    owner_global_jobs = client.get(
        "/api/v1/processing-jobs?state=ongoing",
        headers=headers(owner_subject, "Item Owner"),
    )
    assert owner_global_jobs.status_code == 200, owner_global_jobs.text
    assert any(job["id"] == pending["id"] for job in owner_global_jobs.json())

    other_jobs = client.get(
        f"/api/v1/revisions/{completed['revision_id']}/processing-jobs",
        headers=headers(other_subject, "Other Designer"),
    )
    assert other_jobs.status_code == 200, other_jobs.text
    assert all(job["can_control"] is False for job in other_jobs.json())
    other_global_jobs = client.get(
        "/api/v1/processing-jobs?state=all",
        headers=headers(other_subject, "Other Designer"),
    )
    assert other_global_jobs.status_code == 200, other_global_jobs.text
    assert all(job["id"] != pending["id"] for job in other_global_jobs.json())
    denied = client.post(
        f"/api/v1/processing-jobs/{pending['id']}/cancel",
        headers=headers(other_subject, "Other Designer"),
    )
    assert denied.status_code == 403


def test_freecad_job_stores_outputs_and_preview_builds_pfg(monkeypatch) -> None:
    content = f"test FCStd content {uuid.uuid4()}".encode()
    completed = commit_item(
        client,
        item_number=f"PREVIEW-{uuid.uuid4().hex}",
        name="Preview test",
        files=[("preview.FCStd", content)],
    )
    revision_id = completed["revision_id"]

    reprocess = client.post(f"/api/v1/revisions/{revision_id}/preview/reprocess", headers=AUTH)
    assert reprocess.status_code == 202
    assert reprocess.json()["type"] == "FCSTD_TO_MODELRYX"

    def fake_converter(
        source: Path,
        output: Path,
        settings,
        progress_callback=None,
        cancellation_callback=None,
    ) -> None:
        assert source.read_bytes() == content
        if cancellation_callback:
            cancellation_callback()
        if progress_callback:
            progress_callback("EXPORTING_GEOMETRY", 55, "Exporting geometry.")
        bom = {"schemaVersion": 1, "items": [{"name": "Body"}]}
        with zipfile.ZipFile(output, "w") as package:
            package.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "format": "Modelryx Graphics",
                        "version": 1,
                        "model": "model.glb",
                        "modelPreview": "model-preview.json",
                    }
                ),
            )
            package.writestr(
                "model.glb", b"glTF" + (2).to_bytes(4, "little") + (12).to_bytes(4, "little")
            )
            package.writestr("model-preview.json", json.dumps(bom))
            package.writestr(
                "diagnostics.json",
                json.dumps(
                    {
                        "warnings": ["Open shell has no volume."],
                        "statistics": {"triangles": 0},
                    }
                ),
            )
        output.with_suffix(".cad-raw-metadata.json").write_text(
            json.dumps({"document_label": "Preview"})
        )
        output.with_suffix(".part-metadata.json").write_text(
            json.dumps(
                {
                    "schema": "modelryx.part-metadata",
                    "schema_version": "1.0",
                    "source": {
                        "system": "freecad",
                        "system_version": "1.0",
                        "worker": "modelryx-freecad-worker",
                        "worker_version": "test",
                        "extracted_at": "2026-08-21T12:00:00Z",
                    },
                    "identity": {"name": "Preview", "kind": "part"},
                    "geometry": {
                        "bounding_box": {"x_mm": 10, "y_mm": 10, "z_mm": 10},
                        "shape": {"volume_mm3": 1000, "surface_area_mm2": 600},
                        "topology": {"solid_count": 1},
                    },
                    "assembly": {"is_assembly": False},
                    "quality": {
                        "geometry": "complete",
                        "physical": "unavailable",
                        "features": "unsupported",
                        "assembly": "not_applicable",
                        "warnings": [],
                    },
                }
            )
        )

    monkeypatch.setattr("modelryx.services.processing.run_converter", fake_converter)
    def fake_remote(source, workspace, output, settings, job, document_kind, cancel, report):
        assert source.is_relative_to(workspace)
        assert document_kind == "part"
        fake_converter(source, output, settings, report, cancel)

    monkeypatch.setattr("modelryx.services.freecad_remote.convert_remote", fake_remote)
    monkeypatch.setattr(get_settings(), "freecad_worker_url", "http://worker")
    with SessionLocal() as db:
        job = db.scalar(
            select(ProcessingJob).where(ProcessingJob.revision_id == uuid.UUID(revision_id))
        )
        job.state = "RUNNING"
        job.attempt_count = 1
        db.commit()
        process_job(db, job, get_settings())
        revision = db.get(Revision, uuid.UUID(revision_id))
        assert revision.state == "READY_WITH_WARNINGS"
        assert revision.derived_metadata_json["items"][0]["name"] == "Body"
        assert job.stage == "COMPLETED"
        assert job.progress_percent == 100
        assert job.diagnostics_json["statistics"]["triangles"] == 0
        assert {row["status"] for row in job.diagnostics_json["files"].values()} == {"WARNING"}
        assert {row["stage"] for row in job.diagnostics_json["files"].values()} == {
            "VALIDATED_WITH_WARNINGS"
        }
        assert "Open shell" in next(iter(job.diagnostics_json["files"].values()))["message"]
        snapshots = list(
            db.scalars(
                select(RevisionContentSnapshot)
                .where(RevisionContentSnapshot.revision_id == revision.id)
                .order_by(RevisionContentSnapshot.sequence)
            )
        )
        assert len(snapshots) == 1
        assert job.snapshot_id == snapshots[0].id
        relations = list(
            db.scalars(
                select(RevisionArtifactRelation)
                .join(
                    RevisionContentSnapshotArtifact,
                    RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id,
                )
                .where(RevisionContentSnapshotArtifact.snapshot_id == snapshots[-1].id)
            )
        )
        containers = [db.get(Artifact, relation.artifact_id) for relation in relations]
        authoritative = next(
            container for container in containers if container.classification == "AUTHORITATIVE"
        )
        derived = [container for container in containers if container.classification == "DERIVED"]
        assert {container.artifact_type for container in derived} == {
            "GLB",
            "METADATA",
            "OTHER",
        }
        assert all(container.derived_from_artifact_id == authoritative.id for container in derived)
        assert all(container.generator == "modelryx-cad-worker" for container in derived)
        assert all(
            container.generator_version == get_settings().converter_version for container in derived
        )
        assert all(
            db.scalar(select(ArtifactFile.id).where(ArtifactFile.artifact_id == container.id))
            for container in derived
        )

    published = client.get(f"/api/v1/item-revisions/{revision_id}/artifacts", headers=AUTH)
    assert published.status_code == 200, published.text
    assert len(published.json()) == 4
    assert (
        sum(entry["artifact"]["classification"] == "AUTHORITATIVE" for entry in published.json())
        == 1
    )
    current_metadata = client.get(f"/api/v1/revisions/{revision_id}/metadata", headers=AUTH)
    assert current_metadata.status_code == 200, current_metadata.text
    assert current_metadata.json()["current"]["metadata"]["identity"]["name"] == "Preview"
    assert current_metadata.json()["current"]["raw_metadata_url"]

    cached_request = client.post(f"/api/v1/revisions/{revision_id}/preview/reprocess", headers=AUTH)
    assert cached_request.status_code == 202

    def converter_must_not_run(*_args, **_kwargs) -> None:
        raise AssertionError("converter ran despite a matching successful cache entry")

    monkeypatch.setattr("modelryx.services.processing.run_converter", converter_must_not_run)
    with SessionLocal() as db:
        cached_job = db.get(ProcessingJob, uuid.UUID(cached_request.json()["id"]))
        cached_job.state = "RUNNING"
        cached_job.attempt_count = 1
        db.commit()
        process_job(db, cached_job, get_settings())
        assert cached_job.state == "SUCCEEDED"
        assert cached_job.diagnostics_json["cache"]["hit"] is True
        assert (
            db.scalar(
                select(RevisionContentSnapshot.sequence)
                .where(RevisionContentSnapshot.revision_id == uuid.UUID(revision_id))
                .order_by(RevisionContentSnapshot.sequence.desc())
            )
            == 1
        )

    jobs = client.get(f"/api/v1/revisions/{revision_id}/processing-jobs", headers=AUTH)
    assert jobs.status_code == 200
    assert jobs.json()[0]["state"] == "SUCCEEDED"
    assert {row["status"] for row in jobs.json()[0]["files"]} == {"WARNING"}
    assert {row["stage"] for row in jobs.json()[0]["files"]} == {
        "CACHE_REUSED_WITH_WARNINGS"
    }

    search = client.post("/api/v1/parts/search", headers=AUTH, json={"query": "Body"})
    assert search.status_code == 200
    assert any(item["part"]["id"] == completed["item_id"] for item in search.json()["items"])

    preview = client.get(f"/api/v1/revisions/{revision_id}/preview", headers=AUTH)
    assert preview.status_code == 200
    preview_path = Path(get_settings().storage_root) / f"test-{uuid.uuid4().hex}.pfg"
    preview_path.write_bytes(preview.content)
    try:
        with zipfile.ZipFile(preview_path) as package:
            assert package.read("model.glb")[:4] == b"glTF"
            assert json.loads(package.read("model-preview.json"))["schemaVersion"] == 1
            assert json.loads(package.read("manifest.json"))["format"] == "Modelryx Graphics"
    finally:
        preview_path.unlink(missing_ok=True)


@pytest.mark.parametrize("job_type", ["EXTERNAL_CAD_TO_MODELRYX", "STEP_TO_MODELRYX"])
def test_failed_regeneration_and_retry_preserve_library_and_published_outputs(tmp_path, job_type):
    import httpx
    from modelryx.services.artifact_content import (
        current_artifact_files,
        latest_content_snapshot_id,
    )
    from modelryx.services.processing import publish_worker_outputs, store_output
    from modelryx.services.storage import LocalStorageProvider

    number = f"PRESERVE-PUBLISHED-{uuid.uuid4().hex}"
    completed = commit_item(client, item_number=number, name="Keep published part",
                            files=[("part.step", b"native source")])
    revision_id = uuid.UUID(completed["revision_id"])
    with SessionLocal() as db:
        revision = db.get(Revision, revision_id)
        original_job = db.scalar(
            select(ProcessingJob).where(ProcessingJob.revision_id == revision_id)
        )
        original_job.state = "SUCCEEDED"
        snapshot = db.get(RevisionContentSnapshot, latest_content_snapshot_id(db, revision_id))
        preview = tmp_path / "model.glb"
        preview.write_bytes(b"last successful preview")
        output = store_output(
            db, LocalStorageProvider(get_settings().storage_root), revision, snapshot,
            kind="GLB", filename="model.glb", mime_type="model/gltf-binary", source=preview,
        )
        publish_worker_outputs(db, revision, snapshot, [output])
        revision.state = "READY"
        revision.metadata_status = "READY"
        revision.derived_metadata_json = {"last_successful_metadata": True}
        db.commit()
        before = {f.id: f.sha256 for f in current_artifact_files(db, revision_id)}
        snapshot_id = latest_content_snapshot_id(db, revision_id)

    regeneration = client.post(f"/api/v1/revisions/{revision_id}/preview/reprocess", headers=AUTH)
    assert regeneration.status_code == 202, regeneration.text
    job_id = uuid.UUID(regeneration.json()["id"])
    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        job.type, job.state, job.stage, job.attempt_count = job_type, "RUNNING", "VALIDATING", 3
        db.commit()
        fail_job(db, job, httpx.ConnectError("connection refused"))
        revision = db.get(Revision, revision_id)
        assert job.state == "FAILED"
        assert revision.state == "READY_WITH_WARNINGS"
        assert revision.metadata_status == "READY"
        assert revision.derived_metadata_json == {"last_successful_metadata": True}
        assert latest_content_snapshot_id(db, revision_id) == snapshot_id
        assert {f.id: f.sha256 for f in current_artifact_files(db, revision_id)} == before

    library = client.get("/api/v1/items", headers=AUTH, params={"item_number_regex": number})
    assert library.status_code == 200, library.text
    assert number in library.text
    retried = client.post(f"/api/v1/processing-jobs/{job_id}/retry", headers=AUTH)
    assert retried.status_code == 202, retried.text
    with SessionLocal() as db:
        assert db.get(Revision, revision_id).state == "READY_WITH_WARNINGS"


def test_failed_first_processing_does_not_mark_unprocessed_content_ready():
    import httpx

    completed = commit_item(client, item_number=f"FIRST-FAIL-{uuid.uuid4().hex}",
                            name="No published preview", files=[("part.step", b"source")])
    revision_id = uuid.UUID(completed["revision_id"])
    with SessionLocal() as db:
        revision = db.get(Revision, revision_id)
        revision.state = "PROCESSING"
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.revision_id == revision_id))
        job.state, job.attempt_count = "RUNNING", 3
        db.commit()
        fail_job(db, job, httpx.ConnectError("connection refused"))
        assert revision.state == "FAILED"
