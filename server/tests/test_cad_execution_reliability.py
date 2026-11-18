"""Exercise the service and cancellation API against the test database."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from artifact_helpers import commit_item
from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.cad.execution import process_cad_execution
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import (
    CADExecution,
    CADOperationPlan,
    CADSession,
    ProcessingJob,
    Revision,
    User,
)
from modelryx.services import processing
from modelryx.services.artifact_content import current_artifact_files
from modelryx.services.storage import LocalStorageProvider
from sqlalchemy import select

client = TestClient(app)


def queued_execution():
    completed = commit_item(
        client, item_number=f"CAD-RELIABILITY-{uuid.uuid4().hex}", name="Isolated CAD edit",
        files=[("source.FCStd", b"immutable native source")],
    )
    settings = get_settings()
    with SessionLocal() as db:
        revision = db.get(Revision, uuid.UUID(completed["revision_id"]))
        revision.state = "READY"
        artifact = next(f for f in current_artifact_files(db, revision.id) if f.kind == "NATIVE")
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        workspace_key = f"cad-sessions/{uuid.uuid4()}"
        workspace = settings.storage_root / workspace_key
        workspace.mkdir(parents=True)
        source = LocalStorageProvider(settings.storage_root).path_for(artifact.storage_key)
        (workspace / "source.FCStd").write_bytes(source.read_bytes())
        session = CADSession(
            revision_id=revision.id, source_artifact_id=artifact.id, owner_id=user.id,
            worker_id="freecad-controlled", status="EXECUTING", source_sha256=artifact.sha256,
            workspace_key=workspace_key, expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(session)
        db.flush()
        plan = CADOperationPlan(
            session_id=session.id, status="CONFIRMED", operation="feature.parameter.set_value",
            target_id="Pad", parameter="length_mm", expected_value_json={"value": 10},
            new_value_json={"value": 12}, unit="mm", preview_json={}, state_token="a" * 64,
            requested_by=user.id, confirmed_by=user.id,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        db.add(plan)
        db.flush()
        job = ProcessingJob(
            revision_id=revision.id, type="CAD_SESSION_EXECUTE", state="PENDING", stage="QUEUED",
            idempotency_key=f"reliability:{uuid.uuid4()}",
        )
        db.add(job)
        db.flush()
        execution = CADExecution(
            plan_id=plan.id, processing_job_id=job.id, status="QUEUED",
            idempotency_key=f"reliability:{uuid.uuid4()}",
        )
        db.add(execution)
        db.commit()
        return job.id, execution.id, plan.id, session.id, revision.id, source


@pytest.mark.parametrize("cancel_at", ["pending", "mutation", "conversion", "verification", None])
def test_cad_execution_cancellation_closes_workflow_and_preserves_source(monkeypatch, cancel_at):
    from modelryx.cad import execution as service

    job_id, execution_id, plan_id, session_id, revision_id, source = queued_execution()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    phases = []

    def cancel():
        response = client.post(f"/api/v1/processing-jobs/{job_id}/cancel", headers=AUTH)
        assert response.status_code == 200, response.text

    def mutate(plan, native, output, report, *, timeout_seconds, cancellation_callback):
        phases.append("mutation")
        if cancel_at == "mutation":
            cancel()
        cancellation_callback()
        output.write_bytes(b"edited native result")
        report.write_text(json.dumps({
            "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "requested_postconditions_passed": True, "feature_errors": [],
            "geometry": {"all_shapes_valid": True, "solid_count": 1},
        }))

    def convert(native, package, settings, *, cancellation_callback):
        phases.append("conversion")
        if cancel_at == "conversion":
            cancel()
        cancellation_callback()
        package.with_suffix(".cad-raw-metadata.json").write_text("{}")
        package.with_suffix(".part-metadata.json").write_text("{}")

    def read_package(package, result_dir, settings):
        glb, preview = result_dir / "model.glb", result_dir / "model-preview.json"
        glb.write_bytes(b"preview")
        preview.write_text("{}")
        return glb, preview, {}, {}

    def metadata(package):
        if cancel_at == "verification":
            cancel()
        return {}, {"geometry": {"shape": {"volume_mm3": 12}}}

    monkeypatch.setattr(service, "load_worker_adapter", lambda: SimpleNamespace(
        worker_id="freecad-controlled", execute_plan=mutate,
    ))
    monkeypatch.setattr(processing, "run_converter", convert)
    monkeypatch.setattr(processing, "read_converter_package", read_package)
    monkeypatch.setattr(processing, "read_worker_metadata_sidecars", metadata)
    if cancel_at == "pending":
        cancel()
    else:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            job.state = "RUNNING"
            db.commit()
            if cancel_at is None:
                process_cad_execution(db, job, get_settings())
            else:
                with pytest.raises(processing.ProcessingCancelled) as error:
                    process_cad_execution(db, job, get_settings())
                db.rollback()
                processing.fail_job(db, db.get(ProcessingJob, job_id), error.value)

    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        execution = db.get(CADExecution, execution_id)
        plan = db.get(CADOperationPlan, plan_id)
        session = db.get(CADSession, session_id)
        assert db.get(Revision, revision_id).state == "READY"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        if cancel_at is None:
            assert job.state == execution.status == plan.status == "SUCCEEDED"
            assert session.status == "READY_TO_REVIEW"
            assert execution.result_json["validation"]["passed"] is True
        else:
            assert job.state == execution.status == "FAILED"
            assert job.stage == plan.status == session.status == "CANCELLED"
            assert execution.error_json["code"] == "CANCELLED"
            assert execution.result_json == {}
            assert session.closed_at is not None
    if cancel_at == "pending":
        assert phases == []
    if cancel_at == "mutation":
        assert phases == ["mutation"]
    if cancel_at:
        result = client.get(f"/api/v1/cad-sessions/{session_id}/result", headers=AUTH)
        assert result.status_code == 404
    else:
        late = client.post(f"/api/v1/processing-jobs/{job_id}/cancel", headers=AUTH)
        assert late.status_code == 409
