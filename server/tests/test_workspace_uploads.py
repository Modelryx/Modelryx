import hashlib
import uuid

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import ProcessingJob
from modelryx.services.staging_workflow import claim_staging_workflow, process_staging_workflow
from sqlalchemy import select

client = TestClient(app)


def test_workspace_includes_active_upload_progress() -> None:
    content = b"durable workspace upload"
    created = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "files": [{
                "logical_path": "Assembly.iam",
                "filename": "Assembly.iam",
                "mime_type": "application/octet-stream",
                "byte_size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }],
        },
    )
    assert created.status_code == 201, created.text
    staging = created.json()

    proposed = client.put(
        f"/api/v1/staging/{staging['id']}/proposal",
        headers=AUTH,
        json={
            "expected_snapshot_version": staging["snapshot_version"],
            "operation": "CREATE_ITEM",
            "item": {
                "item_number": "ASM-DURABLE-001",
                "name": "Durable assembly",
                "item_type": "ASSEMBLY",
            },
            "revision": {
                "revision_code": "A",
                "cad_system": "Example CAD",
                "native_format": "IAM",
            },
            "artifacts": [{
                "client_artifact_id": str(uuid.uuid4()),
                "artifact_type": "CAD_ASSEMBLY",
                "name": "Durable assembly",
                "classification": "AUTHORITATIVE",
                "relation_type": "SPECIFICATION",
                "is_primary": True,
                "files": [{
                    "staged_file_id": staging["files"][0]["id"],
                    "reference_name": "MODEL",
                    "role": "PRIMARY",
                }],
            }],
            "identity_decisions": [{
                "staged_file_id": staging["files"][0]["id"],
                "decision": "CREATE_NEW",
            }],
            "bom": [],
        },
    )
    assert proposed.status_code == 200, proposed.text
    staging = proposed.json()

    before = client.get("/api/v1/me/workspace", headers=AUTH)
    assert before.status_code == 200, before.text
    upload = next(row for row in before.json()["uploads"] if row["id"] == staging["id"])
    assert upload["status"] == "PREPARING"
    assert upload["item_number"] == "ASM-DURABLE-001"
    assert upload["item_name"] == "Durable assembly"
    assert upload["primary_filename"] == "Assembly.iam"
    assert upload["file_count"] == 1
    assert upload["verified_file_count"] == 0
    assert upload["progress_percent"] == 0
    assert upload["can_resume"] is True

    uploaded = client.put(
        staging["files"][0]["upload_url"],
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert uploaded.status_code == 200, uploaded.text

    after = client.get("/api/v1/me/workspace", headers=AUTH).json()
    upload = next(row for row in after["uploads"] if row["id"] == staging["id"])
    assert upload["verified_file_count"] == 1
    assert upload["verified_bytes"] == len(content)
    assert upload["progress_percent"] == 100

    cancelled = client.delete(f"/api/v1/staging/{staging['id']}", headers=AUTH)
    assert cancelled.status_code == 204, cancelled.text
    final = client.get("/api/v1/me/workspace", headers=AUTH).json()
    assert all(row["id"] != staging["id"] for row in final["uploads"])


def test_submitted_upload_is_committed_by_durable_worker_workflow() -> None:
    content = b"asynchronous publication"
    created = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "files": [{
                "logical_path": "Async.step",
                "filename": "Async.step",
                "mime_type": "application/octet-stream",
                "byte_size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }],
        },
    ).json()
    proposed = client.put(
        f"/api/v1/staging/{created['id']}/proposal",
        headers=AUTH,
        json={
            "expected_snapshot_version": created["snapshot_version"],
            "operation": "CREATE_ITEM",
            "item": {
                "item_number": f"ASYNC-{uuid.uuid4().hex[:10]}",
                "name": "Async publication",
                "item_type": "PART",
            },
            "revision": {
                "revision_code": "A",
                "cad_system": "STEP",
                "native_format": "STEP",
            },
            "artifacts": [{
                "client_artifact_id": str(uuid.uuid4()),
                "artifact_type": "STEP",
                "name": "Async publication",
                "classification": "AUTHORITATIVE",
                "relation_type": "SPECIFICATION",
                "is_primary": True,
                "files": [{
                    "staged_file_id": created["files"][0]["id"],
                    "reference_name": "MODEL",
                    "role": "PRIMARY",
                }],
            }],
            "identity_decisions": [{
                "staged_file_id": created["files"][0]["id"],
                "decision": "CREATE_NEW",
            }],
            "bom": [],
        },
    ).json()
    uploaded = client.put(
        proposed["files"][0]["upload_url"],
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert uploaded.status_code == 200, uploaded.text

    submitted = client.post(f"/api/v1/staging/{created['id']}/submit", headers=AUTH)
    assert submitted.status_code == 202, submitted.text
    assert submitted.json()["state"] == "QUEUED"

    paused = client.post(f"/api/v1/staging/{created['id']}/pause", headers=AUTH)
    assert paused.status_code == 200, paused.text
    assert paused.json()["state"] == "PAUSED"
    with SessionLocal() as db:
        assert claim_staging_workflow(db) is None
    resumed = client.post(f"/api/v1/staging/{created['id']}/retry", headers=AUTH)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["state"] == "QUEUED"

    with SessionLocal() as db:
        claimed = claim_staging_workflow(db)
        assert claimed is not None
        assert str(claimed.id) == created["id"]
        process_staging_workflow(db, claimed.id, get_settings())

    completed = client.get(f"/api/v1/staging/{created['id']}", headers=AUTH)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["result"]["item_id"]

    workspace = client.get("/api/v1/me/workspace", headers=AUTH).json()
    task = next(row for row in workspace["uploads"] if row["id"] == created["id"])
    assert task["is_finished"] is True
    assert task["can_edit"] is False
    assert task["is_archived"] is False

    with SessionLocal() as db:
        job = db.scalar(
            select(ProcessingJob).where(
                ProcessingJob.revision_id == uuid.UUID(completed.json()["result"]["revision_id"])
            )
        )
        assert job is not None
        job.state = "FAILED"
        job.stage = "FAILED"
        job.progress_percent = 80
        job.error_detail = "Licensed CAD output validation failed"
        db.commit()

    workspace = client.get("/api/v1/me/workspace", headers=AUTH).json()
    task = next(row for row in workspace["uploads"] if row["id"] == created["id"])
    assert task["status"] == "FAILED"
    assert task["workflow_state"] == "FAILED"
    assert task["workflow_stage"] == "FAILED"
    assert task["workflow_error_detail"] == "Licensed CAD output validation failed"

    archived = client.patch(
        f"/api/v1/staging/{created['id']}/workspace",
        headers=AUTH,
        json={"action": "ARCHIVE"},
    )
    assert archived.status_code == 204, archived.text
    workspace = client.get("/api/v1/me/workspace", headers=AUTH).json()
    task = next(row for row in workspace["uploads"] if row["id"] == created["id"])
    assert task["is_archived"] is True

    removed = client.patch(
        f"/api/v1/staging/{created['id']}/workspace",
        headers=AUTH,
        json={"action": "REMOVE"},
    )
    assert removed.status_code == 204, removed.text
    workspace = client.get("/api/v1/me/workspace", headers=AUTH).json()
    assert all(row["id"] != created["id"] for row in workspace["uploads"])
