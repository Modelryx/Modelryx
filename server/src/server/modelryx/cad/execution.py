import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.cad.worker_adapter import load_worker_adapter
from modelryx.core.audit import record_audit
from modelryx.core.config import Settings
from modelryx.models import (
    ArtifactFile,
    CADExecution,
    CADOperationPlan,
    CADSession,
    ProcessingJob,
)
from modelryx.services.storage import LocalStorageProvider


def _workspace(settings: Settings, session: CADSession) -> Path:
    root = (settings.storage_root.resolve() / "cad-sessions").resolve()
    value = (settings.storage_root.resolve() / session.workspace_key).resolve()
    if not value.is_relative_to(root) or value == root:
        raise RuntimeError("CAD execution workspace is outside the configured session root")
    return value


def _comparison(before: dict, after: dict) -> dict:
    def facts(value: dict) -> dict:
        geometry = value.get("geometry", {}) if isinstance(value.get("geometry"), dict) else {}
        shape = geometry.get("shape", {}) if isinstance(geometry.get("shape"), dict) else {}
        topology = (
            geometry.get("topology", {}) if isinstance(geometry.get("topology"), dict) else {}
        )
        physical = value.get("physical", {}) if isinstance(value.get("physical"), dict) else {}
        features = value.get("features", {}) if isinstance(value.get("features"), dict) else {}
        feature_items = features.get("items", []) if isinstance(features.get("items"), list) else []
        return {
            "material": (
                physical.get("material", {}).get("name")
                if isinstance(physical.get("material"), dict)
                else physical.get("material")
            ),
            "volume_mm3": shape.get("volume_mm3"),
            "surface_area_mm2": shape.get("surface_area_mm2"),
            "solid_count": topology.get("solid_count"),
            "face_count": topology.get("face_count"),
            "edge_count": topology.get("edge_count"),
            "features": [
                {"id": item.get("persistent_id"), "type": item.get("type")}
                for item in feature_items
                if isinstance(item, dict) and item.get("persistent_id")
            ],
        }

    before_facts = facts(before)
    after_facts = facts(after)
    return {
        "before": before_facts,
        "after": after_facts,
        "changed": {
            key: {"before": before_facts[key], "after": after_facts[key]}
            for key in before_facts
            if before_facts[key] != after_facts[key]
        },
    }


def validate_native_report(report: dict, output_sha256: str) -> None:
    if report.get("output_sha256") != output_sha256:
        raise RuntimeError("CAD result checksum verification failed")
    if report.get("feature_errors"):
        raise RuntimeError("CAD result contains native feature errors")
    if report.get("requested_postconditions_passed") is not True:
        raise RuntimeError("CAD requested-value postconditions were not verified")
    geometry = report.get("geometry", {})
    solid_count = geometry.get("solid_count") if isinstance(geometry, dict) else None
    if (
        not isinstance(geometry, dict) or geometry.get("all_shapes_valid") is not True
        or type(solid_count) is not int or solid_count < 1
    ):
        raise RuntimeError("CAD result geometry verification failed")


def fail_cad_execution(
    db: Session, job: ProcessingJob, *, cancelled: bool = False
) -> None:
    """Close the isolated edit workflow without changing the authoritative revision."""
    execution = db.scalar(select(CADExecution).where(CADExecution.processing_job_id == job.id))
    plan = db.get(CADOperationPlan, execution.plan_id) if execution else None
    session = db.get(CADSession, plan.session_id) if plan else None
    error = {
        "code": "CANCELLED" if cancelled else "CAD_EXECUTION_FAILED",
        "stage": job.stage,
        "message": job.error_detail,
    }
    if execution is not None:
        # The existing execution contract represents cancellation as a failed execution.
        execution.status = "FAILED"
        execution.error_json = error
        execution.completed_at = datetime.now(UTC)
        execution.result_json = {}
    if plan is not None:
        plan.status = "CANCELLED" if cancelled else "FAILED"
    if session is not None:
        session.status = "CANCELLED" if cancelled else "FAILED"
        session.error_json = error
        session.closed_at = datetime.now(UTC)
        session.lock_version += 1


def process_cad_execution(db: Session, job: ProcessingJob, settings: Settings) -> None:
    execution = db.scalar(
        select(CADExecution).where(CADExecution.processing_job_id == job.id).with_for_update()
    )
    if execution is None:
        raise RuntimeError("CAD execution is not linked to this processing job")
    plan = db.get(CADOperationPlan, execution.plan_id)
    session = db.get(CADSession, plan.session_id) if plan else None
    if plan is None or session is None:
        raise RuntimeError("CAD execution plan or session is unavailable")
    if execution.status == "SUCCEEDED":
        job.state = "SUCCEEDED"
        job.stage = "COMPLETED"
        job.progress_percent = 100
        job.finished_at = datetime.now(UTC)
        db.commit()
        return
    if execution.status not in {"QUEUED", "RUNNING"} or plan.status not in {
        "CONFIRMED",
        "EXECUTING",
    }:
        raise RuntimeError("CAD execution is not in an executable state")
    workspace = _workspace(settings, session)
    sources = [path for path in workspace.glob("source.*") if path.is_file()]
    if len(sources) != 1:
        raise RuntimeError("CAD execution workspace must contain exactly one native source")
    source = sources[0]
    storage = LocalStorageProvider(settings.storage_root)
    artifact = db.get(ArtifactFile, session.source_artifact_id)
    if artifact is None:
        raise RuntimeError("Authoritative CAD source artifact is unavailable")
    from modelryx.services.processing import (
        check_processing_cancelled,
        read_converter_package,
        read_worker_metadata_sidecars,
        run_converter,
        sha256_file,
        update_stage,
    )

    check_processing_cancelled(db, job)
    authoritative = storage.path_for(artifact.storage_key)
    if (
        not source.is_file()
        or sha256_file(source) != session.source_sha256
        or sha256_file(authoritative) != session.source_sha256
    ):
        raise RuntimeError("CAD source immutability check failed")
    execution.status = "RUNNING"
    execution.started_at = datetime.now(UTC)
    plan.status = "EXECUTING"
    session.status = "EXECUTING"
    db.commit()
    result_dir = workspace / f"execution-{execution.id}"
    result_dir.mkdir(exist_ok=True)
    plan_path = result_dir / "plan.json"
    output = result_dir / f"result{source.suffix}"
    report_path = result_dir / "native-report.json"
    package = result_dir / "result.pfg"
    plan_path.write_text(
        json.dumps(
            {
                "operation": plan.operation,
                "target_id": plan.target_id,
                "parameter": plan.parameter,
                "expected_value": plan.expected_value_json["value"],
                "new_value": plan.new_value_json["value"],
                "source_sha256": session.source_sha256,
                **({"unit": plan.unit} if plan.operation == "sketch.constraint.set_value" else {}),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    update_stage(db, job, "CAD_MUTATING", 20)
    adapter = load_worker_adapter()
    if adapter.worker_id != session.worker_id:
        raise RuntimeError(
            f"CAD session targets worker {session.worker_id!r}, not {adapter.worker_id!r}"
        )
    adapter.execute_plan(
        plan_path,
        source,
        output,
        report_path,
        timeout_seconds=settings.converter_timeout_seconds,
        cancellation_callback=lambda: check_processing_cancelled(db, job),
    )
    check_processing_cancelled(db, job)
    if not output.is_file() or output.stat().st_size > settings.cad_session_max_bytes:
        raise RuntimeError("CAD result is missing or exceeds the configured size limit")
    session.status = "VERIFYING"
    db.commit()
    update_stage(db, job, "CAD_CONVERTING", 50)
    run_converter(
        output, package, settings,
        cancellation_callback=lambda: check_processing_cancelled(db, job),
    )
    check_processing_cancelled(db, job)
    update_stage(db, job, "CAD_VERIFYING", 75)
    glb_path, preview_path, _, diagnostics = read_converter_package(package, result_dir, settings)
    _, part_metadata = read_worker_metadata_sidecars(package)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    validate_native_report(report, sha256_file(output))
    if (
        sha256_file(source) != session.source_sha256
        or sha256_file(authoritative) != artifact.sha256
    ):
        raise RuntimeError("CAD source changed during execution")
    from modelryx.models import Revision

    source_revision = db.get(Revision, session.revision_id)
    before = (
        (source_revision.derived_metadata_json or {}).get("partMetadata", {})
        if source_revision else {}
    )
    comparison = _comparison(before, part_metadata)
    # Serialize the final cancellation check with the cancellation endpoint. Once this
    # lock is released, the job is terminal and a late request receives a conflict.
    db.scalar(select(ProcessingJob).where(ProcessingJob.id == job.id).with_for_update())
    check_processing_cancelled(db, job)
    execution.result_json = {
        "source_sha256": session.source_sha256,
        "output_sha256": report["output_sha256"],
        "artifacts": {
            "native": str(output.relative_to(workspace)).replace("\\", "/"),
            "glb": str(glb_path.relative_to(workspace)).replace("\\", "/"),
            "preview": str(preview_path.relative_to(workspace)).replace("\\", "/"),
            "raw_metadata": str(
                package.with_suffix(".cad-raw-metadata.json").relative_to(workspace)
            ).replace("\\", "/"),
            "part_metadata": str(
                package.with_suffix(".part-metadata.json").relative_to(workspace)
            ).replace("\\", "/"),
        },
        "artifact_sha256": {
            "native": sha256_file(output),
            "glb": sha256_file(glb_path),
            "preview": sha256_file(preview_path),
            "raw_metadata": sha256_file(package.with_suffix(".cad-raw-metadata.json")),
            "part_metadata": sha256_file(package.with_suffix(".part-metadata.json")),
        },
        "postconditions": report,
        "comparison": comparison,
        "validation": {
            "engine": "cad-postconditions-v1",
            "passed": True,
            "checks": ["recompute", "feature_errors", "solid_valid", "requested_value"],
        },
        "diagnostics": diagnostics,
    }
    execution.status = "SUCCEEDED"
    execution.completed_at = datetime.now(UTC)
    plan.status = "SUCCEEDED"
    session.status = "READY_TO_REVIEW"
    session.lock_version += 1
    job.state = "SUCCEEDED"
    job.stage = "COMPLETED"
    job.progress_percent = 100
    job.finished_at = datetime.now(UTC)
    record_audit(
        db,
        actor_id=None,
        action="cad_execution.verified",
        entity_type="cad_execution",
        entity_id=execution.id,
        request_id=f"job:{job.id}",
        details={
            "session_id": str(session.id),
            "source_sha256": session.source_sha256,
            "output_sha256": report["output_sha256"],
        },
    )
    db.commit()
