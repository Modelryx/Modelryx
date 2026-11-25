import hashlib
import os
import time
import uuid

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import StagingFile
from modelryx.services.reconcile import reconcile

client = TestClient(app)


def test_operational_status_requires_authentication() -> None:
    assert client.get("/api/v1/operations/status").status_code == 401


def test_operational_status_reports_capacity_and_queues() -> None:
    response = client.get("/api/v1/operations/status", headers=AUTH)

    assert response.status_code == 200
    status = response.json()
    assert status["status"] in {"ok", "attention"}
    assert isinstance(status["processing_jobs"], dict)
    assert isinstance(status["revisions"], dict)
    assert status["active_staging_sessions"] >= 0
    assert status["verified_artifact_bytes"] >= 0
    assert isinstance(status["artifacts"], dict)
    assert isinstance(status["artifact_files"], dict)
    assert status["storage_free_bytes"] > 0
    assert isinstance(status["bundle_jobs"], dict)
    assert status["queue_depth"] >= 0
    assert status["checkout_notification_health"] in {"HEALTHY", "WARNING", "DEGRADED"}
    assert status["checkout_notification_consecutive_failures"] >= 0
    assert status["checkout_notification_last_examined"] >= 0
    assert status["checkout_notification_last_created"] >= 0
    assert status["pending_approval_requests"] >= 0
    assert status["overdue_approval_requests"] >= 0
    assert status["oldest_pending_approval_age_seconds"] >= 0
    assert "STEP" in [worker["worker_type"] for worker in status["workers"]]
    assert next(
        worker for worker in status["workers"] if worker["worker_type"] == "STEP"
    )["configured_url"]


def test_worker_heartbeat_is_authenticated_and_reported() -> None:
    payload = {
        "worker_type": "GENERAL",
        "machine_name": "freecad-test-host",
        "worker_version": "1.2.3",
        "application_version": "FreeCAD 1.1.3",
        "adapter_version": "2.0.0",
        "compatibility_state": "COMPATIBLE",
        "capabilities": {"step_export": True, "pdf_export": True},
        "status": "BUSY",
        "current_job_id": str(uuid.uuid4()),
    }
    denied = client.put("/api/v1/workers/heartbeat", json=payload)
    assert denied.status_code == 401

    accepted = client.put(
        "/api/v1/workers/heartbeat",
        headers={"X-Modelryx-Worker-Key": get_settings().internal_worker_key},
        json=payload,
    )
    assert accepted.status_code == 204, accepted.text

    status = client.get("/api/v1/operations/status", headers=AUTH).json()
    freecad = next(worker for worker in status["workers"] if worker["worker_type"] == "GENERAL")
    assert freecad["availability"] == "ONLINE"
    assert freecad["machine_name"] == "freecad-test-host"
    assert freecad["worker_version"] == "1.2.3"
    assert freecad["application_version"] == "FreeCAD 1.1.3"
    assert freecad["adapter_version"] == "2.0.0"
    assert freecad["compatibility_state"] == "COMPATIBLE"
    assert freecad["capabilities"]["step_export"] is True
    assert freecad["status"] == "BUSY"
    assert freecad["current_job"] is True

    preflight = client.get(
        "/api/v1/workers/preflight?worker_type=FREECAD", headers=AUTH
    )
    assert preflight.status_code == 200
    assert preflight.json()["available"] is False
    assert preflight.json()["compatible"] is False


def test_external_worker_heartbeat_alone_cannot_verify_endpoint() -> None:
    payload = {
        "worker_type": "EXAMPLE_CAD",
        "machine_name": "incompatible-example_cad-host",
        "worker_version": "1.0.0",
        "application_version": "Example CAD 2016",
        "compatibility_state": "INCOMPATIBLE",
        "capabilities": {"pdf_export": False},
        "status": "DEGRADED",
        "detail": "Example CAD version is outside the supported range.",
    }
    accepted = client.put(
        "/api/v1/workers/heartbeat",
        headers={"X-Modelryx-Worker-Key": get_settings().internal_worker_key},
        json=payload,
    )
    assert accepted.status_code == 204
    preflight = client.get(
        "/api/v1/workers/preflight?worker_type=EXAMPLE_CAD", headers=AUTH
    )
    assert preflight.status_code == 200
    # Heartbeats alone do not verify external native endpoints. Registration and
    # an authenticated capability probe are required, even for a live heartbeat.
    assert preflight.json()["available"] is False
    assert preflight.json()["compatible"] is False


def test_metrics_and_audit_are_available_to_administrator() -> None:
    metrics = client.get("/api/v1/operations/metrics", headers=AUTH)
    assert metrics.status_code == 200
    assert "modelryx_ai_search_interpretations" in metrics.text
    assert "modelryx_ai_search_warnings" in metrics.text
    assert "modelryx_ai_search_zero_results" in metrics.text
    assert "modelryx_processing_jobs" in metrics.text
    assert "modelryx_verified_artifact_bytes" in metrics.text
    assert "modelryx_artifacts" in metrics.text
    assert "modelryx_artifact_files" in metrics.text
    assert "modelryx_staging_sessions" in metrics.text
    assert "modelryx_checkout_locks" in metrics.text
    assert "modelryx_checkout_notification_health" in metrics.text
    assert "modelryx_checkout_notification_consecutive_failures" in metrics.text
    assert "modelryx_checkout_notification_last_examined" in metrics.text
    assert "modelryx_checkout_notification_last_created" in metrics.text
    assert "modelryx_approval_requests_pending" in metrics.text
    assert "modelryx_approval_requests_overdue" in metrics.text
    assert "modelryx_approval_oldest_pending_age_seconds" in metrics.text
    assert "modelryx_engineering_search_queries" in metrics.text
    assert "modelryx_engineering_search_zero_results" in metrics.text
    assert "modelryx_engineering_search_fallbacks" in metrics.text
    assert "modelryx_engineering_search_feedback" in metrics.text
    assert "modelryx_engineering_search_duration_milliseconds_bucket" in metrics.text
    audit = client.get("/api/v1/operations/audit?limit=5", headers=AUTH)
    assert audit.status_code == 200
    assert isinstance(audit.json(), list)


def test_reconciliation_preserves_staging_files_and_deletes_only_orphans(
    tmp_path, monkeypatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "orphan_grace_seconds", 0)
    content = b"active staged content"
    created = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "files": [
                {
                    "logical_path": "active.step",
                    "filename": "active.step",
                    "mime_type": "application/octet-stream",
                    "byte_size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    staged = created.json()["files"][0]
    uploaded = client.put(
        staged["upload_url"],
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert uploaded.status_code == 200, uploaded.text

    with SessionLocal() as db:
        staged_row = db.get(StagingFile, uuid.UUID(staged["id"]))
        staged_path = tmp_path / staged_row.storage_key
    orphan_path = tmp_path / "unreferenced" / "orphan.bin"
    orphan_path.parent.mkdir(parents=True)
    orphan_path.write_bytes(b"orphan")
    old = time.time() - 10
    os.utime(staged_path, (old, old))
    os.utime(orphan_path, (old, old))

    result = reconcile(delete=True)

    assert staged_path.is_file()
    assert not orphan_path.exists()
    assert result.removed == 1


def test_embedded_freecad_heartbeat_is_not_an_available_cad_server():
    response = client.put(
        "/api/v1/workers/heartbeat",
        headers={"X-Modelryx-Worker-Key": get_settings().internal_worker_key},
        json={"worker_type": "FREECAD", "machine_name": "retired-embedded",
              "worker_version": "old", "compatibility_state": "COMPATIBLE"},
    )
    assert response.status_code == 204
    status = client.get("/api/v1/operations/status", headers=AUTH).json()
    assert all(w["worker_type"] != "FREECAD" for w in status["workers"])
