import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin

import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from modelryx.cad.worker_adapter import load_worker_adapter
from modelryx.core.audit import record_audit
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import SessionLocal
from modelryx.metadata import MetadataService
from modelryx.models import (
    ApplicationSetting,
    Artifact,
    ArtifactFile,
    BomLine,
    BomViewRevision,
    CADExecution,
    CADOperationPlan,
    CADSession,
    Item,
    ProcessingJob,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    WorkerHeartbeat,
)
from modelryx.services.approval_escalations import create_overdue_approval_escalations
from modelryx.services.artifact_content import (
    artifact_files_for_snapshot,
    latest_content_snapshot_id,
)
from modelryx.services.checkout_notifications import (
    create_checkout_expiry_notifications,
    record_checkout_notification_run,
)
from modelryx.services.external_workers import (
    JOB_TYPE as EXTERNAL_CAD_JOB_TYPE,
)
from modelryx.services.external_workers import (
    matching_worker,
    validate_capabilities,
)
from modelryx.services.freecad_settings import FREECAD_WORKER_KEY, configured_url
from modelryx.services.glb_thumbnail import THUMBNAIL_NAME, render_glb_thumbnail
from modelryx.services.staging_workflow import (
    claim_staging_workflow,
    fail_staging_workflow,
    process_staging_workflow,
    recover_staging_workflows,
)
from modelryx.services.storage import LocalStorageProvider

MAX_ATTEMPTS = 3
logger = logging.getLogger("modelryx.worker")
cad_worker_adapter = load_worker_adapter()


def generate_revision_thumbnail(
    glb_path: Path, directory: Path, preview_path: Path | None = None
) -> Path | None:
    """A missing thumbnail must not invalidate an otherwise usable CAD preview."""
    destination = directory / THUMBNAIL_NAME
    try:
        render_glb_thumbnail(glb_path, destination, preview_path=preview_path)
    except Exception as exc:  # Thumbnail failure must not fail a verified CAD conversion.
        logger.warning("thumbnail_generation_failed glb=%s error=%s", glb_path, exc)
        return None
    return destination


@dataclass(frozen=True)
class ProcessingFailure:
    code: str
    retryable: bool
    suggestion: str


@dataclass(frozen=True)
class AbandonedJobRecovery:
    requeued: int = 0
    failed: int = 0


class ProcessingCancelled(RuntimeError):
    """Raised at a safe boundary after an authorized cancellation request."""


def classify_processing_failure(error: Exception) -> ProcessingFailure:
    """Normalize native/runtime failures without exposing worker-specific internals."""
    message = str(error).casefold()
    if isinstance(error, ProcessingCancelled):
        return ProcessingFailure(
            "CANCELLED",
            False,
            "Start a new processing request if a preview is still required.",
        )
    if isinstance(error, (subprocess.TimeoutExpired, TimeoutError, httpx.TimeoutException)):
        return ProcessingFailure(
            "TIMEOUT",
            True,
            "The CAD operation timed out. Retry it; if it repeats, simplify the model "
            "or increase the configured conversion timeout.",
        )
    if (
        (
            isinstance(error, httpx.HTTPStatusError)
            and (error.response.status_code == 429 or error.response.status_code >= 500)
        )
        or isinstance(error, httpx.TransportError)
        or any(
            token in message
            for token in ("connection refused", "could not be reached", "temporarily unavailable")
        )
    ):
        return ProcessingFailure(
            "WORKER_UNAVAILABLE",
            True,
            "Confirm the required conversion service is running and reachable, then retry.",
        )
    if any(
        token in message for token in ("not configured", "authentication", "invalid worker key")
    ):
        return ProcessingFailure(
            "WORKER_CONFIGURATION",
            False,
            "Correct the worker endpoint or authentication configuration, then retry the stage.",
        )
    if any(token in message for token in ("permission denied", "access is denied", "read-only")):
        return ProcessingFailure(
            "SOURCE_ACCESS_DENIED",
            False,
            "Grant the worker read access to every source and write access to its processing "
            "workspace, then retry.",
        )
    if any(token in message for token in ("password protected", "encrypted document")):
        return ProcessingFailure(
            "PROTECTED_DOCUMENT",
            False,
            "Save an unencrypted native CAD copy and upload it again.",
        )
    if any(
        token in message
        for token in (
            "dll load failed",
            "cannot open shared object file",
            "undefined symbol",
            "dependent libraries",
            "shared library",
            "image not found",
        )
    ):
        return ProcessingFailure(
            "NATIVE_DEPENDENCY_MISSING",
            False,
            "Install the missing native library in the worker image and verify that it matches "
            "the supported CAD runtime.",
        )
    if any(
        token in message
        for token in ("missing reference", "referenced file", "no such file", "file not found")
    ):
        return ProcessingFailure(
            "MISSING_REFERENCE",
            False,
            "Add or relink the missing referenced document, upload the complete assembly "
            "package, and retry.",
        )
    if any(
        token in message
        for token in ("unsupported", "workbench", "unknown document type", "no module named")
    ):
        return ProcessingFailure(
            "UNSUPPORTED_CAD_FEATURE",
            False,
            "Install the required CAD application module or convert the source to a "
            "supported assembly model.",
        )
    if any(
        token in message
        for token in (
            "too large",
            "exceeds the configured",
            "max-nodes",
            "max-triangles",
            "resource limit",
        )
    ):
        return ProcessingFailure(
            "RESOURCE_LIMIT",
            False,
            "Reduce model complexity or ask an administrator to review the configured "
            "processing limits.",
        )
    memory_errors = ("out of memory", "memoryerror", "cannot allocate memory")
    if any(token in message for token in memory_errors):
        return ProcessingFailure(
            "OUT_OF_MEMORY",
            True,
            "Retry on a worker with more memory or reduce assembly and tessellation complexity.",
        )
    if any(
        token in message
        for token in ("segmentation fault", "access violation", "process exited with code -11")
    ):
        return ProcessingFailure(
            "NATIVE_PROCESS_CRASH",
            True,
            "Retry once on a healthy worker. If it repeats, quarantine the source and review the "
            "CAD worker crash diagnostic.",
        )
    if any(
        token in message
        for token in (
            "recompute failed",
            "failed to recompute",
            "topological naming",
            "broken shape",
            "invalid shape",
        )
    ):
        return ProcessingFailure(
            "INVALID_MODEL_STATE",
            False,
            "Open the document in its authoring CAD application, repair failed features and "
            "recompute errors, then save and upload a clean copy.",
        )
    if any(
        token in message
        for token in (
            "invalid freecad",
            "invalid zip",
            "bad zip",
            "corrupt",
            "failed to open",
            "invalid glb",
            "invalid partmetadata",
        )
    ):
        return ProcessingFailure(
            "INVALID_DOCUMENT",
            False,
            "Open and repair the source in its CAD application, save a clean copy, and "
            "upload it again.",
        )
    return ProcessingFailure(
        "PROCESSING_ERROR",
        True,
        "Retry the stage. If it fails again, review the worker diagnostic and repair the "
        "source document.",
    )


def report_cad_worker_heartbeat(
    db: Session,
    settings: Settings,
    *,
    status: str,
    current_job_id: uuid.UUID | None = None,
    detail: str | None = None,
) -> None:
    application_version = cad_worker_adapter.application_version()
    compatibility_state, compatibility_detail = cad_worker_adapter.compatibility()
    compatible = compatibility_state == "COMPATIBLE"
    heartbeat = {
        "worker_type": cad_worker_adapter.worker_type,
        "machine_name": (
            os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or "cad-worker"
        ),
        "worker_version": cad_worker_adapter.worker_version,
        "application_version": application_version,
        "adapter_version": cad_worker_adapter.worker_version,
        "compatibility_state": compatibility_state,
        "capabilities_json": cad_worker_adapter.capabilities(),
        "status": status if compatible else "DEGRADED",
        "current_job_id": current_job_id,
        "detail": detail or compatibility_detail,
        "last_heartbeat_at": datetime.now(UTC),
    }
    statement = insert(WorkerHeartbeat).values(**heartbeat)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[WorkerHeartbeat.worker_type, WorkerHeartbeat.machine_name],
            set_={
                key: value
                for key, value in heartbeat.items()
                if key not in {"worker_type", "machine_name"}
            },
        )
    )
    db.commit()


def report_general_worker_heartbeat(
    db: Session,
    *,
    status: str,
    current_job_id: uuid.UUID | None = None,
) -> None:
    heartbeat = {
        "worker_type": "PROCESSING",
        "machine_name": os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or "worker",
        "worker_version": "modelryx-server",
        "application_version": None,
        "adapter_version": None,
        "compatibility_state": "COMPATIBLE",
        "capabilities_json": {
            "job_types": [
                "STEP_TO_MODELRYX",
                EXTERNAL_CAD_JOB_TYPE,
                "AUTOCAD_TO_MODELRYX",
                "INVENTOR_TO_MODELRYX",
                "NX_TO_MODELRYX",
                "ASSEMBLY_SOURCE_BUNDLE",
            ]
        },
        "status": status,
        "current_job_id": current_job_id,
        "detail": None,
        "last_heartbeat_at": datetime.now(UTC),
    }
    statement = insert(WorkerHeartbeat).values(**heartbeat)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[WorkerHeartbeat.worker_type, WorkerHeartbeat.machine_name],
            set_={
                key: value
                for key, value in heartbeat.items()
                if key not in {"worker_type", "machine_name"}
            },
        )
    )
    db.commit()


def busy_heartbeat_loop(
    settings: Settings, job_id: uuid.UUID, stop: threading.Event, worker_role: str = "all"
) -> None:
    while not stop.wait(10):
        try:
            with SessionLocal() as db:
                if worker_role == "general":
                    report_general_worker_heartbeat(db, status="BUSY", current_job_id=job_id)
                else:
                    report_cad_worker_heartbeat(db, settings, status="BUSY", current_job_id=job_id)
        except Exception:
            logger.exception("cad_worker_busy_heartbeat_failed job_id=%s", job_id)


def assembly_dependency_files(
    db: Session,
    revision: Revision,
    snapshot: RevisionContentSnapshot,
) -> list[ArtifactFile]:
    # Worker publication creates a later snapshot containing the native inputs
    # plus derived preview Artifacts. The assembly BOM remains the one committed
    # on the preceding source snapshot, so reprocessing must inherit it.
    bom_view = db.scalar(
        select(BomViewRevision)
        .join(
            RevisionContentSnapshot,
            RevisionContentSnapshot.id == BomViewRevision.snapshot_id,
        )
        .where(
            BomViewRevision.revision_id == revision.id,
            RevisionContentSnapshot.sequence <= snapshot.sequence,
        )
        .order_by(RevisionContentSnapshot.sequence.desc())
        .limit(1)
    )
    if bom_view is None:
        return []
    child_revision_ids = list(
        db.scalars(
            select(BomLine.child_revision_id)
            .where(BomLine.bom_view_revision_id == bom_view.id)
            .order_by(BomLine.sequence, BomLine.id)
        )
    )
    dependencies: list[ArtifactFile] = []
    seen: set[uuid.UUID] = set()
    for child_revision_id in child_revision_ids:
        if child_revision_id in seen:
            continue
        seen.add(child_revision_id)
        child_snapshot_id = latest_content_snapshot_id(db, child_revision_id)
        files = (
            artifact_files_for_snapshot(
                db,
                child_revision_id,
                child_snapshot_id,
                artifact_types={"CAD_MODEL", "CAD_ASSEMBLY"},
            )
            if child_snapshot_id
            else []
        )
        primary = next(
            (file for file in files if file.role == "PRIMARY"),
            files[0] if files else None,
        )
        if primary is None:
            raise RuntimeError(f"assembly component {child_revision_id} has no current CAD file")
        dependencies.append(primary)
    return dependencies


def index_ai_best_effort(db: Session, revision: Revision, settings: Settings) -> None:
    """Refresh both search projections after committed publication independently."""
    revision_id = revision.id
    try:
        from modelryx.services.ai import index_revision

        chunks = index_revision(db, revision, settings)
        logger.info("ai_indexed revision_id=%s chunks=%s", revision_id, chunks)
    except Exception:
        db.rollback()
        logger.exception("ai_index_failed revision_id=%s", revision_id)

    try:
        from modelryx.knowledge.service import index_revision_knowledge

        source, changed = index_revision_knowledge(db, revision, settings)
        db.commit()
        logger.info(
            "knowledge_indexed revision_id=%s source_id=%s changed=%s",
            revision_id,
            source.id,
            changed,
        )
    except Exception:
        db.rollback()
        logger.exception("knowledge_index_failed revision_id=%s", revision_id)


def published_revision_state(metadata_status: str, warnings: list[str] | None = None) -> str:
    """Map successful publication evidence to a consistent revision lifecycle state."""
    return "READY" if metadata_status == "READY" and not warnings else "READY_WITH_WARNINGS"


def duplicate_check_best_effort(db: Session, revision: Revision, settings: Settings) -> None:
    """Create a review report after metadata is ready without blocking processing."""
    try:
        from modelryx.duplicate_detection.reasoner import GatewayDuplicateReasoner
        from modelryx.duplicate_detection.repository import DuplicateDetectionRepository
        from modelryx.duplicate_detection.service import (
            DuplicateDetectionConfig,
            DuplicateDetectionService,
        )
        from modelryx.models import User
        from modelryx.services.ai import AiGateway

        actor = db.get(User, revision.created_by)
        service = DuplicateDetectionService(
            DuplicateDetectionConfig(
                metadata_weight=settings.duplicate_metadata_weight,
                embedding_weight=settings.duplicate_embedding_weight,
                engineering_duplicate_min=settings.duplicate_engineering_min,
                likely_duplicate_min=settings.duplicate_likely_min,
                similar_min=settings.duplicate_similar_min,
            )
        )
        gateway = AiGateway(settings)
        reasoner = GatewayDuplicateReasoner(gateway) if gateway.chat_enabled else None
        check = DuplicateDetectionRepository(
            db,
            service,
            reasoner,
            ai_review_min=settings.duplicate_ai_review_min,
            ai_review_max=settings.duplicate_ai_review_max,
            max_ai_candidates=settings.duplicate_max_ai_candidates,
        ).run(
            revision,
            actor_id=revision.created_by,
            administrator=bool(actor and actor.role == "administrator"),
        )
        db.commit()
        logger.info(
            "duplicate_check_completed revision_id=%s check_id=%s candidates=%s",
            revision.id,
            check.id,
            check.candidate_count_after_filter,
        )
    except Exception:
        db.rollback()
        logger.exception("duplicate_check_failed revision_id=%s", revision.id)


def claim_job(
    db: Session, worker_role: str = "all", *, remote_freecad: bool = False
) -> ProcessingJob | None:
    now = datetime.now(UTC)
    cad_enabled = worker_role in {"all", "freecad"}
    compatibility, _ = cad_worker_adapter.compatibility() if cad_enabled else ("INCOMPATIBLE", None)
    capabilities = cad_worker_adapter.capabilities() if compatibility == "COMPATIBLE" else {}
    extensions = capabilities.get("vendor_extensions", {})
    formats = extensions.get("formats", []) if isinstance(extensions, dict) else []
    native_job_types = (
        ["FCSTD_TO_MODELRYX"] if any(str(value).casefold() == "fcstd" for value in formats) else []
    )
    session_jobs = (
        select(CADExecution.processing_job_id)
        .join(CADOperationPlan, CADOperationPlan.id == CADExecution.plan_id)
        .join(CADSession, CADSession.id == CADOperationPlan.session_id)
        .where(CADSession.worker_id == cad_worker_adapter.worker_id)
    )
    generic_job_types = (
        "STEP_TO_MODELRYX",
        EXTERNAL_CAD_JOB_TYPE,
        "AUTOCAD_TO_MODELRYX",
        "INVENTOR_TO_MODELRYX",
        "NX_TO_MODELRYX",
        "ASSEMBLY_SOURCE_BUNDLE",
    )
    eligible = []
    if worker_role in {"all", "general"}:
        eligible.append(ProcessingJob.type.in_(generic_job_types))
        if remote_freecad:
            eligible.append(ProcessingJob.type == "FCSTD_TO_MODELRYX")
    if worker_role in {"all", "freecad"} and native_job_types and not remote_freecad:
        eligible.append(ProcessingJob.type.in_(native_job_types))
    if worker_role in {"all", "freecad"} and compatibility == "COMPATIBLE":
        eligible.append(
            (ProcessingJob.type == "CAD_SESSION_EXECUTE") & ProcessingJob.id.in_(session_jobs)
        )
    if not eligible:
        return None
    job = db.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.state == "PENDING",
            ProcessingJob.available_at <= now,
            or_(*eligible),
        )
        .order_by(ProcessingJob.available_at, ProcessingJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.state = "RUNNING"
    job.attempt_count += 1
    job.started_at = now
    job.error_detail = None
    db.commit()
    db.refresh(job)
    logger.info(
        "job_claimed job_id=%s revision_id=%s type=%s attempt=%s",
        job.id,
        job.revision_id,
        job.type,
        job.attempt_count,
    )
    return job


def recover_abandoned_jobs(db: Session, settings: Settings) -> AbandonedJobRecovery:
    """Recover expired RUNNING leases while leaving live worker jobs untouched."""
    now = datetime.now(UTC)
    heartbeat_cutoff = now - timedelta(seconds=settings.worker_heartbeat_timeout_seconds)
    live_job_ids = set(
        db.scalars(
            select(WorkerHeartbeat.current_job_id).where(
                WorkerHeartbeat.status == "BUSY",
                WorkerHeartbeat.current_job_id.is_not(None),
                WorkerHeartbeat.last_heartbeat_at >= heartbeat_cutoff,
            )
        )
    )
    statement = select(ProcessingJob).where(
        ProcessingJob.state == "RUNNING",
        ProcessingJob.started_at < now - timedelta(seconds=settings.processing_stale_seconds),
    )
    if live_job_ids:
        statement = statement.where(ProcessingJob.id.notin_(live_job_ids))
    abandoned = list(db.scalars(statement.with_for_update(skip_locked=True)))
    requeued = 0
    failed = 0
    for job in abandoned:
        diagnostic = dict(job.diagnostics_json or {})
        file_rows = dict(diagnostic.get("files") or {})
        for row in file_rows.values():
            if row.get("status") in {"QUEUE", "PROCESSING"}:
                row.update(
                    status="QUEUE",
                    stage="RECOVERED_AFTER_WORKER_LOSS",
                    message="The previous worker stopped reporting; this file was queued again.",
                )
        warnings = list(diagnostic.get("warnings") or [])
        warnings.append("Recovered after the processing worker stopped reporting.")
        diagnostic.update(files=file_rows, warnings=warnings)
        job.diagnostics_json = diagnostic
        job.progress_percent = 0
        job.started_at = None
        if job.attempt_count < MAX_ATTEMPTS:
            job.state = "PENDING"
            job.stage = "QUEUED"
            job.error_detail = None
            job.available_at = now
            job.finished_at = None
            action = "processing.abandoned_requeued"
            requeued += 1
        else:
            from modelryx.models import DownloadBundle

            job.state = "FAILED"
            job.stage = "FAILED"
            job.error_detail = "Processing stopped because the worker lease expired repeatedly"
            job.finished_at = now
            diagnostic["errors"] = [
                {
                    "stage": "ABANDONED",
                    "code": "WORKER_LEASE_EXPIRED",
                    "message": job.error_detail,
                    "retryable": True,
                    "fix_suggestion": (
                        "Confirm the CAD worker is stable and connected, then retry this stage."
                    ),
                }
            ]
            job.diagnostics_json = diagnostic
            bundle = db.scalar(
                select(DownloadBundle).where(DownloadBundle.processing_job_id == job.id)
            )
            if bundle is not None:
                bundle.state = "FAILED"
                bundle.error_detail = job.error_detail
            action = "processing.abandoned_failed"
            failed += 1
        record_audit(
            db,
            actor_id=None,
            action=action,
            entity_type="revision",
            entity_id=job.revision_id,
            request_id=f"recovery:{job.id}",
            details={"job_id": str(job.id), "attempt_count": job.attempt_count},
        )
    return AbandonedJobRecovery(requeued=requeued, failed=failed)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run_converter(
    source: Path,
    output_pfg: Path,
    settings: Settings,
    progress_callback: Callable[[str, int, str], None] | None = None,
    cancellation_callback: Callable[[], None] | None = None,
) -> None:
    cad_worker_adapter.convert(
        source,
        output_pfg,
        timeout_seconds=settings.converter_timeout_seconds,
        max_nodes=settings.max_preview_nodes,
        max_triangles=settings.max_preview_triangles,
        progress_callback=progress_callback,
        cancellation_callback=cancellation_callback,
    )


def run_step_processing_job(
    source: Path,
    output_directory: Path,
    settings: Settings,
) -> dict:
    """Submit a STEP document and download the worker's complete output set."""
    if not settings.internal_worker_key:
        raise RuntimeError("STEP worker authentication is not configured")
    base_url = settings.step_worker_url.rstrip("/") + "/"
    headers = {
        "X-Modelryx-Worker-Key": settings.internal_worker_key,
        "Idempotency-Key": f"step:{sha256_file(source)}",
    }
    timeout = httpx.Timeout(settings.converter_timeout_seconds)
    with httpx.Client(timeout=timeout) as client, source.open("rb") as stream:
        created = client.post(
            urljoin(base_url, "api/v1/jobs"),
            headers=headers,
            files={"source": (source.name, stream, "model/step")},
        )
        if not created.is_success:
            detail = created.text.strip()[:2000]
            raise RuntimeError(
                f"STEP worker rejected {source.name}: HTTP {created.status_code}"
                + (f": {detail}" if detail else "")
            )
        created.raise_for_status()
        record = created.json()
        status_url = urljoin(base_url, record["links"]["self"].lstrip("/"))
        deadline = time.monotonic() + settings.converter_timeout_seconds
        while True:
            state = str(record.get("state", "")).upper()
            if state == "READY":
                output_directory.mkdir(parents=True, exist_ok=True)
                for link_name, filename in {
                    "glb": "model.glb",
                    "metadata": "part-metadata.json",
                    "preview": "model-preview.json",
                    "raw_metadata": "step-raw-metadata.json",
                }.items():
                    output = client.get(
                        urljoin(base_url, record["links"][link_name].lstrip("/")),
                        headers=headers,
                    )
                    output.raise_for_status()
                    (output_directory / filename).write_bytes(output.content)
                return record
            if state == "FAILED":
                raise RuntimeError(str(record.get("error") or "STEP processing failed"))
            if state == "CANCELLED":
                raise ProcessingCancelled(
                    str(record.get("error") or "STEP conversion was cancelled")
                )
            if time.monotonic() >= deadline:
                raise RuntimeError("STEP processing timed out")
            time.sleep(max(0.1, settings.worker_poll_seconds))
            status = client.get(status_url, headers=headers)
            status.raise_for_status()
            record = status.json()


def licensed_output_filename(role: str, cad_system: str, index: int) -> str:
    """Return a safe local filename that preserves the output's meaningful suffix."""
    names = {
        "native-snapshot": f"{cad_system}.snapshot.json",
        "part-metadata": "part-metadata.json",
        "model-preview": "model-preview.json",
        "xref-dependencies": "xref-dependencies.json",
        "step": "model.step",
        "glb": "model.glb",
        "drawing-pdf": "drawing.pdf",
        "component-geometry": "component-meshes.zip",
        "drawing-representations": "drawing-representations.zip",
    }
    return names.get(role, f"output-{index}")


def prepare_licensed_source_upload(
    storage: LocalStorageProvider,
    source_artifact: ArtifactFile,
    source_files: list[ArtifactFile],
    workspace: Path,
    cad_system: str,
    force_package: bool = False,
) -> tuple[Path, str]:
    """Create the native upload, preserving Inventor model and drawing paths."""
    source_path = storage.path_for(source_artifact.storage_key)
    if not source_path.is_file():
        raise RuntimeError("verified native source is unavailable")
    is_inventor_package = cad_system == "inventor" and (
        Path(source_artifact.original_filename).suffix.casefold() in {".iam", ".ipt"}
    )
    is_autocad_package = cad_system == "autocad" and (
        Path(source_artifact.original_filename).suffix.casefold() in {".dwg", ".dxf"}
    )
    is_nx_package = (
        cad_system == "siemens_nx"
        and Path(source_artifact.original_filename).suffix.casefold() == ".prt"
    )
    external_package = force_package or cad_system not in {"inventor", "autocad", "siemens_nx"}
    if len(source_files) <= 1 or not (
        is_inventor_package or is_autocad_package or is_nx_package or external_package
    ):
        return source_path, source_artifact.original_filename

    package_path = workspace / f"{cad_system}-package.zip"
    used: dict[str, str] = {}
    root_path: str | None = None
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for artifact in source_files:
            candidate = (
                str(artifact.reference_name or artifact.original_filename)
                .replace("\\", "/")
                .strip("/")
            )
            if candidate.upper() in {"MODEL", "PRIMARY", "SECONDARY", "AUXILIARY"}:
                candidate = Path(artifact.original_filename).name
            logical_path = PurePosixPath(candidate)
            if logical_path.is_absolute() or ".." in logical_path.parts or not logical_path.name:
                raise RuntimeError(f"assembly contains unsafe component path {candidate}")
            archive_name = logical_path.as_posix()
            normalized = archive_name.casefold()
            prior_hash = used.get(normalized)
            if prior_hash is not None:
                if prior_hash != artifact.sha256:
                    raise RuntimeError(
                        f"assembly contains different component files at {archive_name}"
                    )
                continue
            stored_path = storage.path_for(artifact.storage_key)
            if not stored_path.is_file():
                raise RuntimeError(f"verified assembly component is unavailable: {archive_name}")
            package.write(stored_path, archive_name)
            used[normalized] = artifact.sha256
            if artifact.id == source_artifact.id:
                root_path = archive_name
    if root_path is None:
        raise RuntimeError("verified native assembly root is missing from its package")
    return package_path, root_path


def validate_model_preview(preview: object) -> dict:
    """Validate the CAD-worker-owned viewer navigation contract."""
    if not isinstance(preview, dict) or preview.get("schemaVersion") != 1:
        raise RuntimeError("CAD worker produced an invalid model preview schema")
    root_id = preview.get("rootOccurrenceId")
    items = preview.get("items")
    if not isinstance(root_id, str) or not root_id or not isinstance(items, list):
        raise RuntimeError("CAD worker produced an invalid model preview document")
    occurrence_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("CAD worker produced an invalid model preview item")
        occurrence_id = item.get("occurrenceId")
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise RuntimeError("CAD worker preview item is missing an occurrence ID")
        if occurrence_id in occurrence_ids:
            raise RuntimeError("CAD worker preview contains duplicate occurrence IDs")
        occurrence_ids.add(occurrence_id)
        render_ids = item.get("renderOccurrenceIds")
        if render_ids is not None and (
            not isinstance(render_ids, list)
            or not render_ids
            or any(not isinstance(value, str) or not value for value in render_ids)
        ):
            raise RuntimeError("CAD worker preview contains invalid render occurrence IDs")
    if root_id not in occurrence_ids:
        raise RuntimeError("CAD worker preview root does not exist in its item list")
    for item in items:
        parent_id = item.get("parentOccurrenceId")
        if parent_id is not None and parent_id not in occurrence_ids:
            raise RuntimeError("CAD worker preview contains an unknown parent occurrence")
    return preview


def update_stage(db: Session, job: ProcessingJob, stage: str, progress: int) -> None:
    job.stage = stage
    job.progress_percent = progress
    db.commit()


def check_processing_cancelled(db: Session, job: ProcessingJob) -> None:
    db.refresh(job, attribute_names=["diagnostics_json"])
    cancellation = (job.diagnostics_json or {}).get("cancellation") or {}
    if cancellation.get("requested") is True:
        raise ProcessingCancelled("Processing was cancelled by an authorized user")


def update_file_stages(
    db: Session,
    job: ProcessingJob,
    files: list[ArtifactFile],
    status: str,
    *,
    message: str | None = None,
    stage: str | None = None,
) -> None:
    diagnostics = dict(job.diagnostics_json or {})
    rows = dict(diagnostics.get("files") or {})
    for file in files:
        row = {
            "logical_path": file.reference_name or file.original_filename,
            "status": status,
        }
        if message:
            row["message"] = message
        if stage:
            row["stage"] = stage
        rows[str(file.id)] = row
    diagnostics["files"] = rows
    job.diagnostics_json = diagnostics
    db.commit()


def materialize_cad_workspace(
    storage: LocalStorageProvider,
    source_artifact: ArtifactFile,
    dependencies: list[ArtifactFile],
    work_directory: Path,
) -> Path:
    """Copy a native package without flattening its logical directory structure."""
    copied_paths: dict[str, str] = {}
    converter_source: Path | None = None
    for artifact in [source_artifact, *dependencies]:
        reference_name = str(artifact.reference_name or "").replace("\\", "/")
        # reference_name may be a semantic role such as MODEL rather than a file path.
        # Preserve it only when it identifies a native CAD document; otherwise
        # materialize the stored source under its original filename and extension.
        logical_name = (
            reference_name
            if PurePosixPath(reference_name).suffix
            else Path(artifact.original_filename).name
        )
        logical_path = PurePosixPath(logical_name.replace("\\", "/"))
        if logical_path.is_absolute() or ".." in logical_path.parts or not logical_path.name:
            raise RuntimeError(f"assembly contains unsafe component path {logical_name}")
        normalized = logical_path.as_posix().casefold()
        prior_hash = copied_paths.get(normalized)
        if prior_hash is not None and prior_hash != artifact.sha256:
            raise RuntimeError(
                f"assembly contains different component files at {logical_path.as_posix()}"
            )
        destination = work_directory.joinpath(*logical_path.parts)
        if prior_hash is None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(storage.path_for(artifact.storage_key), destination)
            copied_paths[normalized] = artifact.sha256
        if artifact.id == source_artifact.id:
            converter_source = destination
    if converter_source is None:
        raise RuntimeError("verified native artifact is missing")
    return converter_source


# Compatibility alias for integrations importing the old helper name.
materialize_freecad_workspace = materialize_cad_workspace


def read_converter_package(
    package_path: Path, destination: Path, settings: Settings
) -> tuple[Path, Path, dict, dict]:
    if package_path.stat().st_size > settings.max_preview_package_bytes:
        raise RuntimeError("converter package exceeds the configured size limit")
    with zipfile.ZipFile(package_path) as package:
        members = {item.filename: item for item in package.infolist()}
        missing = {"manifest.json", "model.glb", "model-preview.json"} - members.keys()
        if missing:
            raise RuntimeError(f"converter package is missing: {', '.join(sorted(missing))}")
        if members["model.glb"].file_size > settings.max_preview_glb_bytes:
            raise RuntimeError("converted GLB exceeds the configured size limit")
        if members["model-preview.json"].file_size > settings.max_preview_bom_bytes:
            raise RuntimeError("converted model preview exceeds the configured size limit")
        glb_path = destination / "model.glb"
        bom_path = destination / "model-preview.json"
        glb_path.write_bytes(package.read("model.glb"))
        bom_path.write_bytes(package.read("model-preview.json"))
        manifest = json.loads(package.read("manifest.json"))
        diagnostics = (
            json.loads(package.read("diagnostics.json"))
            if "diagnostics.json" in members
            else {"warnings": [], "statistics": {}}
        )
    glb = glb_path.read_bytes()
    if (
        len(glb) < 12
        or glb[:4] != b"glTF"
        or int.from_bytes(glb[4:8], "little") != 2
        or int.from_bytes(glb[8:12], "little") != len(glb)
    ):
        raise RuntimeError("converter produced an invalid GLB 2.0 header")
    bom = json.loads(bom_path.read_text(encoding="utf-8"))
    if bom.get("schemaVersion") != 1 or not isinstance(bom.get("items"), list):
        raise RuntimeError("converter produced an invalid BOM document")
    if (
        manifest.get("format") != "Modelryx Graphics"
        or manifest.get("version") != 1
        or manifest.get("model") != "model.glb"
        or manifest.get("modelPreview") != "model-preview.json"
    ):
        raise RuntimeError("converter produced an invalid package manifest")
    return glb_path, bom_path, bom, diagnostics


def read_worker_metadata_sidecars(package_path: Path) -> tuple[dict, dict]:
    raw_path = package_path.with_suffix(".cad-raw-metadata.json")
    part_path = package_path.with_suffix(".part-metadata.json")
    if not raw_path.is_file() or not part_path.is_file():
        raise RuntimeError("converter did not produce CAD metadata sidecars")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    part = json.loads(part_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("converter produced invalid CAD raw metadata")
    if not isinstance(part, dict):
        raise RuntimeError("converter produced an invalid PartMetadata document")
    try:
        from modelryx.metadata.engineering_validator import validate_engineering_metadata
        from modelryx.metadata.validator import validate_part_metadata

        validate_part_metadata(part)
        validate_engineering_metadata(part)
    except ValueError as exc:
        raise RuntimeError(f"converter produced invalid PartMetadata: {exc}") from exc
    return raw, part


def read_cad_drawing_previews(
    package_path: Path, destination: Path, settings: Settings
) -> list[Path]:
    """Extract validated optional TechDraw PNG representations from a PFG."""
    results: list[Path] = []
    with zipfile.ZipFile(package_path) as package:
        members = {item.filename: item for item in package.infolist()}
        try:
            manifest = json.loads(package.read("manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise RuntimeError("converter produced an invalid package manifest") from exc
        drawings = manifest.get("drawings", [])
        if not isinstance(drawings, list):
            raise RuntimeError("converter produced an invalid drawing preview manifest")
        for entry in drawings[:100]:
            name = entry.get("file") if isinstance(entry, dict) else None
            path = PurePosixPath(str(name or ""))
            if (
                len(path.parts) != 2
                or path.parts[0] != "drawings"
                or path.suffix.casefold() != ".png"
                or path.name != str(name).rsplit("/", 1)[-1]
                or str(path) not in members
            ):
                raise RuntimeError("converter produced an unsafe drawing preview path")
            if members[str(path)].file_size > settings.max_image_preview_bytes:
                raise RuntimeError("converted drawing preview exceeds the configured size limit")
            output = destination / path.name
            output.write_bytes(package.read(str(path)))
            if not output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("converter produced an invalid drawing preview image")
            results.append(output)
    return results


def read_licensed_drawing_representations(
    package_path: Path, destination: Path, settings: Settings
) -> tuple[dict, list[Path]]:
    """Validate and extract a licensed worker's related drawing bundle."""
    extracted: list[Path] = []
    with zipfile.ZipFile(package_path) as package:
        members = {item.filename: item for item in package.infolist()}
        try:
            manifest = json.loads(package.read("manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise RuntimeError("licensed CAD worker produced an invalid drawing manifest") from exc
        drawings = manifest.get("drawings", [])
        if not isinstance(drawings, list):
            raise RuntimeError("licensed CAD worker produced an invalid drawing manifest")
        normalized_drawings: list[dict] = []
        for index, entry in enumerate(drawings[:100], start=1):
            if not isinstance(entry, dict):
                raise RuntimeError("licensed CAD worker produced an invalid drawing entry")
            member_name = str(entry.get("file") or "")
            member_path = PurePosixPath(member_name)
            if (
                len(member_path.parts) != 1
                or member_path.suffix.casefold() != ".pdf"
                or member_name not in members
            ):
                raise RuntimeError("licensed CAD worker produced an unsafe drawing path")
            if members[member_name].file_size > settings.max_pdf_preview_bytes:
                raise RuntimeError("licensed CAD drawing exceeds the configured size limit")
            source = str(entry.get("source") or member_path.stem).replace("\\", "/")
            source_name = Path(source).stem
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", source_name).strip("-._")[:80]
            output = destination / f"drawing-{index:04d}-{safe_name or 'drawing'}.pdf"
            output.write_bytes(package.read(member_name))
            if not output.read_bytes().startswith(b"%PDF-"):
                raise RuntimeError("licensed CAD worker produced an invalid drawing PDF")
            extracted.append(output)
            normalized_drawings.append({**entry, "file": output.name, "source": source})
        return {**manifest, "drawings": normalized_drawings}, extracted


def merge_related_drawing_metadata(part_metadata: dict, manifest: dict) -> dict:
    """Add related drawing facts to otherwise model-centric PartMetadata."""
    drawings = manifest.get("drawings", [])
    if not isinstance(drawings, list) or not drawings:
        return part_metadata
    normalized = part_metadata.setdefault("drawings", [])
    native = part_metadata.setdefault("native", {})
    if not isinstance(normalized, list) or not isinstance(native, dict):
        raise RuntimeError("licensed CAD worker produced incompatible drawing metadata")
    sheets = native.setdefault("drawing_sheets", [])
    if not isinstance(sheets, list):
        raise RuntimeError("licensed CAD worker produced incompatible drawing metadata")
    for index, drawing in enumerate(drawings, start=1):
        if not isinstance(drawing, dict):
            continue
        source = str(drawing.get("source") or drawing.get("file") or f"drawing-{index}")
        drawing_sheets = drawing.get("sheets", [])
        if not isinstance(drawing_sheets, list):
            drawing_sheets = []
        stable_id = "inventor-related-" + hashlib.sha256(source.encode()).hexdigest()[:24]
        record = {
            "stable_id": stable_id,
            "filename": str(drawing.get("file") or f"drawing-{index:04d}.pdf"),
            "mime_type": "application/pdf",
            "worker_contract_version": "inventor-related-drawing-v1",
            "sections": [],
            "entities": [],
        }
        if drawing_sheets:
            record["page_count"] = len(drawing_sheets)
        normalized.append(record)
        for sheet_index, sheet in enumerate(drawing_sheets, start=1):
            if isinstance(sheet, dict):
                sheets.append(
                    {
                        **sheet,
                        "stable_id": str(
                            sheet.get("id")
                            or sheet.get("stable_id")
                            or f"{stable_id}-sheet-{sheet_index}"
                        ),
                        "source_path": source,
                        "drawing_name": str(drawing.get("displayName") or Path(source).stem),
                    }
                )
    return part_metadata


def is_cad_drawing_preview(db: Session, artifact_file: ArtifactFile) -> bool:
    if artifact_file.kind != "IMAGE" or artifact_file.mime_type != "image/png":
        return False
    container = (
        db.get(Artifact, artifact_file.artifact_id)
        if artifact_file.artifact_id is not None
        else None
    )
    return bool(
        container is not None
        and container.classification == "DERIVED"
        and bool(container.generator)
    )


def store_output(
    db: Session,
    storage: LocalStorageProvider,
    revision: Revision,
    source_snapshot: RevisionContentSnapshot,
    *,
    kind: str,
    filename: str,
    mime_type: str,
    source: Path,
    generator: str | None = None,
    generator_version: str | None = None,
) -> ArtifactFile:
    source_files = artifact_files_for_snapshot(
        db,
        revision.id,
        source_snapshot.id,
        artifact_types={"CAD_MODEL", "CAD_ASSEMBLY", "DRAWING", "STEP"},
    )
    source_file = next(
        (file for file in source_files if file.role == "PRIMARY"),
        source_files[0] if source_files else None,
    )
    if source_file is None:
        raise RuntimeError("verified native artifact is missing")
    source_container = (
        db.get(Artifact, source_file.artifact_id) if source_file.artifact_id else None
    )
    if source_container is None:
        source_container = Artifact(
            artifact_type="CAD_ASSEMBLY" if len(source_files) > 1 else "CAD_MODEL",
            name=source_file.original_filename,
            classification="AUTHORITATIVE",
            source="USER",
            state="READY",
            created_by=revision.created_by,
        )
        db.add(source_container)
        db.flush()
        source_file.artifact_id = source_container.id
        source_file.reference_name = source_file.reference_name or "MODEL"
        source_file.role = source_file.role or "PRIMARY"
        db.add(
            RevisionArtifactRelation(
                revision_id=revision.id,
                artifact_id=source_container.id,
                relation_type="SPECIFICATION",
                sequence=0,
                is_primary=True,
                created_by=revision.created_by,
            )
        )
        db.flush()

    container = Artifact(
        artifact_type={
            "GLB": "GLB",
            "PDF": "PDF",
            "IMAGE": "IMAGE",
            "METADATA": "METADATA",
            "OTHER": "OTHER",
            "BUNDLE": "OTHER",
        }.get(kind, "METADATA"),
        name=filename,
        classification="DERIVED",
        source="WORKER",
        derived_from_artifact_id=source_container.id,
        generator=generator or cad_worker_adapter.generator,
        generator_version=generator_version,
        state="READY",
        created_by=revision.created_by,
    )
    db.add(container)
    db.flush()
    relation = RevisionArtifactRelation(
        revision_id=revision.id,
        artifact_id=container.id,
        relation_type=(
            "REPRESENTATION" if kind in {"GLB", "PDF", "IMAGE", "BUNDLE"} else "ANALYSIS"
        ),
        sequence={
            "GLB": 100,
            "PDF": 105,
            "IMAGE": 107,
            "METADATA": 110,
            "OTHER": 120,
            "BUNDLE": 130,
        }.get(kind, 199),
        is_primary=False,
        created_by=revision.created_by,
    )
    db.add(relation)
    db.flush()
    artifact_id = uuid.uuid4()
    storage_key = (
        f"parts/{revision.part_id}/revisions/{revision.id}/artifacts/{artifact_id}/{filename}"
    )
    destination = storage.create_upload(storage_key)
    temporary = destination.with_suffix(destination.suffix + ".processing")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)
    artifact = ArtifactFile(
        id=artifact_id,
        artifact_id=container.id,
        reference_name={
            "GLB": "MODEL",
            "PDF": "DRAWING",
            "IMAGE": "PRESENTATION",
            "METADATA": "PREVIEW",
            "OTHER": "RAW_METADATA",
            "BUNDLE": "BUNDLE",
        }.get(kind, "CONTENT"),
        role="PRIMARY",
        revision_id=revision.id,
        kind=kind,
        storage_key=storage_key,
        original_filename=filename,
        mime_type=mime_type,
        byte_size=destination.stat().st_size,
        sha256=sha256_file(destination),
        state="VERIFIED",
        required=False,
    )
    db.add(artifact)
    return artifact


def publish_worker_outputs(
    db: Session,
    revision: Revision,
    source_snapshot: RevisionContentSnapshot,
    outputs: list[ArtifactFile],
    *,
    retain_derived: bool = False,
    replace_derived_from_artifact_id: uuid.UUID | None = None,
) -> RevisionContentSnapshot | None:
    """Refresh derived associations without creating or changing a CAD source version."""
    # CAD commits lock the same Revision row. Serialize the baseline check and
    # derived replacement with commits and other worker publications.
    db.scalar(select(Revision.id).where(Revision.id == revision.id).with_for_update())
    if latest_content_snapshot_id(db, revision.id) != source_snapshot.id:
        return None
    existing = list(
        db.execute(
            select(
                RevisionContentSnapshotArtifact.relation_id,
                RevisionContentSnapshotArtifact.sequence,
                Artifact.classification,
                Artifact.derived_from_artifact_id,
            )
            .join(
                RevisionArtifactRelation,
                RevisionArtifactRelation.id == RevisionContentSnapshotArtifact.relation_id,
            )
            .join(Artifact, Artifact.id == RevisionArtifactRelation.artifact_id)
            .where(RevisionContentSnapshotArtifact.snapshot_id == source_snapshot.id)
        ).all()
    )
    replaced_ids = [
        relation_id
        for relation_id, _, classification, derived_from in existing
        if classification == "DERIVED"
        and (
            not retain_derived
            or (
                replace_derived_from_artifact_id is not None
                and derived_from == replace_derived_from_artifact_id
            )
        )
    ]
    artifact_ids = {output.artifact_id for output in outputs if output.artifact_id}
    output_relation_ids = list(
        db.scalars(
            select(RevisionArtifactRelation.id)
            .join(Artifact, Artifact.id == RevisionArtifactRelation.artifact_id)
            .where(
                RevisionArtifactRelation.revision_id == revision.id,
                RevisionArtifactRelation.artifact_id.in_(artifact_ids),
                Artifact.classification == "DERIVED",
            )
            .order_by(RevisionArtifactRelation.sequence, RevisionArtifactRelation.id)
        )
    )
    if len(output_relation_ids) != len(artifact_ids) or len(artifact_ids) != len(outputs):
        raise RuntimeError("Worker publication requires revision-scoped derived artifacts")
    if replaced_ids:
        db.execute(
            delete(RevisionContentSnapshotArtifact).where(
                RevisionContentSnapshotArtifact.snapshot_id == source_snapshot.id,
                RevisionContentSnapshotArtifact.relation_id.in_(replaced_ids),
            )
        )
    retained_ids = {row[0] for row in existing} - set(replaced_ids)
    sequence = max((row[1] for row in existing), default=-1) + 1
    for relation_id in output_relation_ids:
        if relation_id not in retained_ids:
            db.add(
                RevisionContentSnapshotArtifact(
                    snapshot_id=source_snapshot.id,
                    relation_id=relation_id,
                    sequence=sequence,
                )
            )
            sequence += 1
    return source_snapshot


def process_step_job(db: Session, job: ProcessingJob, settings: Settings) -> None:
    update_stage(db, job, "INSPECTING", 10)
    revision = db.get(Revision, job.revision_id)
    source_snapshot = db.get(RevisionContentSnapshot, job.snapshot_id)
    source_files = (
        artifact_files_for_snapshot(db, revision.id, source_snapshot.id, artifact_types={"STEP"})
        if revision is not None and source_snapshot is not None
        else []
    )
    source_artifact = next(
        (file for file in source_files if file.role == "PRIMARY"),
        source_files[0] if source_files else None,
    )
    if revision is None or source_snapshot is None or source_artifact is None:
        raise RuntimeError("verified STEP artifact is missing")
    storage = LocalStorageProvider(settings.storage_root)
    source = storage.path_for(source_artifact.storage_key)
    if not source.is_file():
        raise RuntimeError("verified STEP source is unavailable")
    with tempfile.TemporaryDirectory(prefix=f"modelryx-step-{job.id}-") as directory:
        output_glb = Path(directory) / "model.glb"
        preview_path = Path(directory) / "model-preview.json"
        metadata_path = Path(directory) / "part-metadata.json"
        raw_metadata_path = Path(directory) / "step-raw-metadata.json"
        update_stage(db, job, "CONVERTING", 35)
        worker_result = run_step_processing_job(source, Path(directory), settings)
        worker_version = str(worker_result.get("worker_version") or "").strip()
        if not worker_version:
            raise RuntimeError("STEP worker did not report its version")
        job.converter_version = worker_version
        job.cache_key = hashlib.sha256(
            f"{source_artifact.sha256}:step:{worker_version}".encode()
        ).hexdigest()
        db.commit()
        update_stage(db, job, "VALIDATING", 70)
        glb_size = output_glb.stat().st_size
        with output_glb.open("rb") as model:
            glb_header = model.read(12)
        if (
            glb_size < 12
            or glb_header[:4] != b"glTF"
            or int.from_bytes(glb_header[4:8], "little") != 2
            or int.from_bytes(glb_header[8:12], "little") != glb_size
        ):
            raise RuntimeError("STEP converter produced an invalid GLB 2.0 file")
        try:
            part_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            from modelryx.metadata.engineering_validator import validate_engineering_metadata
            from modelryx.metadata.validator import validate_part_metadata

            validate_part_metadata(part_metadata)
            validate_engineering_metadata(part_metadata)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"STEP converter produced invalid metadata: {exc}") from exc
        try:
            preview = json.loads(preview_path.read_text(encoding="utf-8"))
            raw_metadata = json.loads(raw_metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"STEP worker produced invalid output metadata: {exc}") from exc
        if not isinstance(preview, dict) or not isinstance(raw_metadata, dict):
            raise RuntimeError("STEP worker produced invalid output metadata")
        update_stage(db, job, "RENDERING_PREVIEW", 78)
        thumbnail = generate_revision_thumbnail(output_glb, Path(directory), preview_path)
        update_stage(db, job, "PUBLISHING", 85)
        outputs = [
            store_output(
                db,
                storage,
                revision,
                source_snapshot,
                kind="GLB",
                filename="model.glb",
                mime_type="model/gltf-binary",
                source=output_glb,
                generator="modelryx-step-worker",
                generator_version=worker_version,
            ),
            store_output(
                db,
                storage,
                revision,
                source_snapshot,
                kind="METADATA",
                filename="model-preview.json",
                mime_type="application/json",
                source=preview_path,
                generator="modelryx-step-worker",
                generator_version=worker_version,
            ),
            store_output(
                db,
                storage,
                revision,
                source_snapshot,
                kind="METADATA",
                filename="part-metadata.json",
                mime_type="application/json",
                source=metadata_path,
                generator="modelryx-step-worker",
                generator_version=worker_version,
            ),
        ]
        raw_artifact = store_output(
            db,
            storage,
            revision,
            source_snapshot,
            kind="OTHER",
            filename="step-raw-metadata.json",
            mime_type="application/json",
            source=raw_metadata_path,
            generator="modelryx-step-worker",
            generator_version=worker_version,
        )
        outputs.append(raw_artifact)
        if thumbnail is not None:
            outputs.append(
                store_output(
                    db,
                    storage,
                    revision,
                    source_snapshot,
                    kind="IMAGE",
                    filename=THUMBNAIL_NAME,
                    mime_type="image/png",
                    source=thumbnail,
                    generator="modelryx-step-worker",
                    generator_version=worker_version,
                )
            )
        published_snapshot = publish_worker_outputs(db, revision, source_snapshot, outputs)
        if published_snapshot is None:
            raise RuntimeError("revision content changed before CAD publication")
        job.snapshot_id = published_snapshot.id
        revision.state = "READY"
        revision.derived_metadata_json = {**preview, "partMetadata": part_metadata}
        extraction = MetadataService(db).ingest(
            revision=revision,
            snapshot=published_snapshot,
            raw_artifact=raw_artifact,
            metadata=part_metadata,
        )
        if published_snapshot is not None:
            revision.state = published_revision_state(
                extraction.status, list(part_metadata["quality"]["warnings"])
            )
        revision.search_text = json.dumps(
            {"metadata": revision.metadata_json, "derived": revision.derived_metadata_json},
            sort_keys=True,
        )
        job.state = "SUCCEEDED"
        job.stage = "COMPLETED"
        job.progress_percent = 100
        job.diagnostics_json = {"warnings": [], "statistics": {"glb_bytes": glb_size}}
        job.finished_at = datetime.now(UTC)
        record_audit(
            db,
            actor_id=None,
            action="processing.succeeded",
            entity_type="revision",
            entity_id=revision.id,
            request_id=f"job:{job.id}",
            details={"job_id": str(job.id), "type": job.type},
        )
        db.commit()
        index_ai_best_effort(db, revision, settings)
        duplicate_check_best_effort(db, revision, settings)


def licensed_cad_system_candidates(job_type: str) -> tuple[str, ...]:
    """Return licensed CAD systems in dispatch priority order for a job type."""
    if job_type == "AUTOCAD_TO_MODELRYX":
        # Inventor can open standard DWG/DXF documents, so it is a useful fallback
        # when a deployment has no dedicated AutoCAD worker configured.
        return ("autocad", "inventor")
    if job_type == "NX_TO_MODELRYX":
        return ("siemens_nx",)
    return ("inventor",)


def _configured_cad_server_url(db: Session, settings: Settings, cad_system: str) -> str | None:
    setting_key = f"cad.{cad_system}.server_url"
    override = db.get(ApplicationSetting, setting_key)
    if override is not None:
        return override.value or None
    return None


def preferred_cad_outputs(
    cad_system: str, document_kind: str, has_related_drawings: bool,
    supported_roles: list[str] | None = None,
) -> list[str]:
    outputs = [] if cad_system == "siemens_nx" and document_kind == "drawing" else ["glb"]
    if document_kind == "drawing":
        outputs.append("drawing-pdf")
    elif has_related_drawings:
        outputs.append("drawing-representations")
    if document_kind == "assembly" and cad_system != "siemens_nx":
        outputs.append("component-geometry")
    return (
        outputs if supported_roles is None
        else [role for role in outputs if role in supported_roles]
    )


def process_licensed_cad_job(db: Session, job: ProcessingJob, settings: Settings) -> None:
    """Dispatch native processing to a standalone licensed-CAD server and publish its result."""
    external = job.type == EXTERNAL_CAD_JOB_TYPE
    external_worker = None
    if external:
        revision = db.get(Revision, job.revision_id)
        snapshot = db.get(RevisionContentSnapshot, job.snapshot_id)
        files = (
            artifact_files_for_snapshot(
                db,
                revision.id,
                snapshot.id,
                artifact_types={"CAD_MODEL", "CAD_ASSEMBLY", "DRAWING", "CAD_DRAWING"},
            )
            if revision and snapshot
            else []
        )
        primary = next((value for value in files if value.role == "PRIMARY"), None)
        if primary is None:
            raise RuntimeError("verified native artifact is missing")
        external_worker = matching_worker(db, primary.original_filename, revision.cad_system)
        if external_worker is None:
            raise RuntimeError("No unique enabled external worker matches the source")
        configured_servers = [
            (external_worker["capabilities"]["cadSystem"], external_worker["url"])
        ]
    else:
        configured_servers = [
            (candidate, server_url)
            for candidate in licensed_cad_system_candidates(job.type)
            if (server_url := _configured_cad_server_url(db, settings, candidate))
        ]
    if not configured_servers:
        if job.type == "AUTOCAD_TO_MODELRYX":
            raise RuntimeError("AutoCAD and fallback Inventor server URLs are not configured")
        raise RuntimeError("The required CAD server URL is not configured")
    cad_system, server_url = configured_servers[0]
    if not settings.internal_worker_key:
        raise RuntimeError("the internal worker key is not configured")

    update_stage(db, job, "INSPECTING", 10)
    revision = db.get(Revision, job.revision_id)
    source_snapshot = db.get(RevisionContentSnapshot, job.snapshot_id)
    all_source_files = (
        artifact_files_for_snapshot(db, revision.id, source_snapshot.id)
        if revision is not None and source_snapshot is not None
        else []
    )
    allowed_extensions = (
        set(external_worker["capabilities"]["sourceExtensions"])
        if external_worker is not None
        else {".dwg", ".dxf"}
        if cad_system == "autocad"
        else {".prt"}
        if cad_system == "siemens_nx"
        else {".ipt", ".iam", ".idw", ".ipn", ".dwg", ".dxf"}
    )
    if external:
        # A worker advertises root formats, not every possible reference or support
        # file. Send the entire authoritative artifact containing the chosen root.
        source_files = [
            value for value in all_source_files if value.artifact_id == primary.artifact_id
        ]
    else:
        source_files = [
            value
            for value in all_source_files
            if Path(value.original_filename).suffix.casefold() in allowed_extensions
        ]
    source_artifact = next(
        (value for value in source_files if value.role == "PRIMARY"),
        source_files[0] if source_files else None,
    )
    if revision is None or source_snapshot is None or source_artifact is None:
        raise RuntimeError("verified native artifact is missing")
    storage = LocalStorageProvider(settings.storage_root)
    if external:
        for source_file in source_files:
            stored = storage.path_for(source_file.storage_key)
            if (
                not stored.is_file()
                or stored.stat().st_size != source_file.byte_size
                or sha256_file(stored).casefold() != source_file.sha256.casefold()
            ):
                raise RuntimeError("Verified native source bytes no longer match the snapshot")
    extension = source_artifact.original_filename.rsplit(".", 1)[-1].casefold()
    document_kind = {
        "iam": "assembly",
        "idw": "drawing",
        "ipn": "presentation",
        "dwg": "drawing" if revision.native_format.casefold() == "drawing" else "model",
    }.get(extension, "part" if cad_system in {"inventor", "siemens_nx"} else "model")
    if cad_system == "siemens_nx" and not external:
        from modelryx.services.licensed_cad_contracts import nx_document_kind

        item = db.get(Item, revision.part_id)
        source_container = (
            db.get(Artifact, source_artifact.artifact_id) if source_artifact.artifact_id else None
        )
        document_kind = nx_document_kind(
            source_container.artifact_type if source_container is not None else None,
            item.part_type if item is not None else None,
        )
    if external_worker is not None:
        item = db.get(Item, revision.part_id)
        source_container = (
            db.get(Artifact, source_artifact.artifact_id) if source_artifact.artifact_id else None
        )
        if source_container is not None and source_container.artifact_type in {
            "DRAWING",
            "CAD_DRAWING",
        }:
            document_kind = "drawing"
        else:
            document_kind = (
                "assembly" if item is not None and item.part_type == "ASSEMBLY" else "part"
            )
        supported_kinds = external_worker["capabilities"]["documentKinds"]
        if (
            document_kind == "part"
            and document_kind not in supported_kinds
            and "model" in supported_kinds
        ):
            document_kind = "model"
        if document_kind not in supported_kinds:
            raise RuntimeError("External worker does not support this document kind")
    remote_job_id = uuid.uuid5(job.id, f"attempt:{max(job.attempt_count, 1)}")
    headers = {"X-Modelryx-Worker-Key": settings.internal_worker_key}
    base_url = server_url.rstrip("/")
    with tempfile.TemporaryDirectory(prefix=f"modelryx-{cad_system}-{job.id}-") as directory:
        workspace = Path(directory)
        source_path, root_path = prepare_licensed_source_upload(
            storage, source_artifact, source_files, workspace, cad_system, force_package=external
        )
        preferred_outputs = preferred_cad_outputs(
            cad_system,
            document_kind,
            any(
                Path(value.original_filename).suffix.casefold() in {".idw", ".dwg", ".dxf"}
                for value in source_files
                if value.id != source_artifact.id
            ),
            external_worker["capabilities"]["outputRoles"] if external_worker else None,
        )
        required_outputs = ["native-snapshot", "part-metadata", "model-preview"]
        if cad_system == "autocad" and not external:
            required_outputs.append("xref-dependencies")
        request_payload = {
            "contractVersion": "1.0",
            "jobId": str(remote_job_id),
            "attempt": max(job.attempt_count, 1),
            "cadSystem": cad_system,
            "documentKind": document_kind,
            "source": {
                "snapshotId": str(source_snapshot.id),
                "sha256": sha256_file(source_path),
                "size": source_path.stat().st_size,
                "rootPath": root_path,
            },
            "configuration": None,
            "requiredOutputs": required_outputs,
            "preferredOutputs": preferred_outputs,
            "expiresAt": (
                datetime.now(UTC) + timedelta(seconds=settings.converter_timeout_seconds)
            ).isoformat(),
        }
        request_path = workspace / "request.json"
        request_path.write_text(json.dumps(request_payload), encoding="utf-8")
        update_stage(db, job, "DISPATCHING", 20)
        with httpx.Client(timeout=httpx.Timeout(60.0), follow_redirects=False) as client:
            if external_worker is not None:
                capability_response = client.get(f"{base_url}/api/v1/capabilities", headers=headers)
                capability_response.raise_for_status()
                capabilities = validate_capabilities(capability_response.json())
                if capabilities != external_worker["capabilities"]:
                    raise RuntimeError(
                        "External worker capabilities changed; an administrator must re-register it"
                    )
                readiness = client.get(f"{base_url}/ready")
                readiness.raise_for_status()
            if cad_system == "siemens_nx" and not external:
                capabilities_response = client.get(
                    f"{base_url}/api/v1/capabilities", headers=headers
                )
                capabilities_response.raise_for_status()
                capabilities = capabilities_response.json()
                if capabilities.get("cadSystem") != "siemens_nx":
                    raise RuntimeError("The configured NX worker reported an unexpected CAD system")
                readiness = client.get(f"{base_url}/ready", headers=headers)
                production_ready = (
                    readiness.is_success
                    and capabilities.get("nativeProcessingQualified") is True
                    and document_kind in (capabilities.get("documentKinds") or [])
                    and {"metadata-v1", "model-preview-v1"}.issubset(
                        capabilities.get("capabilities") or []
                    )
                )
                if not production_ready:
                    raise RuntimeError("NX worker is not production-ready")
            with request_path.open("rb") as request_stream, source_path.open("rb") as source_stream:
                response = client.post(
                    f"{base_url}/api/v1/jobs",
                    headers=headers,
                    files={
                        "request": ("request.json", request_stream, "application/json"),
                        "source": (
                            source_path.name
                            if external and len(source_files) > 1
                            else source_artifact.original_filename,
                            source_stream,
                            "application/zip"
                            if external and len(source_files) > 1
                            else source_artifact.mime_type,
                        ),
                    },
                )
            response.raise_for_status()
            deadline = time.monotonic() + settings.converter_timeout_seconds
            remote: dict = response.json()
            while str(remote.get("state", "")).casefold() not in {
                "readytopublish",
                "partial",
                "failed",
                "cancelled",
                "expired",
            }:
                if time.monotonic() >= deadline:
                    try:
                        client.post(
                            f"{base_url}/api/v1/jobs/{remote_job_id}/cancel",
                            headers=headers,
                        )
                    except httpx.RequestError:
                        logger.warning(
                            "Could not cancel timed-out %s worker job %s",
                            cad_system,
                            remote_job_id,
                        )
                    raise RuntimeError(f"{cad_system.title()} server job timed out")
                time.sleep(1)
                try:
                    check_processing_cancelled(db, job)
                except ProcessingCancelled:
                    try:
                        client.post(
                            f"{base_url}/api/v1/jobs/{remote_job_id}/cancel",
                            headers=headers,
                        )
                    except httpx.RequestError:
                        logger.warning(
                            "Could not propagate cancellation to %s worker job %s",
                            cad_system,
                            remote_job_id,
                        )
                    raise
                response = client.get(f"{base_url}/api/v1/jobs/{remote_job_id}", headers=headers)
                response.raise_for_status()
                remote = response.json()
                job.stage = str(remote.get("stage") or "REMOTE_PROCESSING").upper()[:32]
                job.progress_percent = min(80, max(20, int(remote.get("progress") or 20)))
                db.commit()
            state = str(remote.get("state", "")).casefold()
            if state == "cancelled":
                raise ProcessingCancelled(f"{cad_system.title()} server job was cancelled")
            if state == "expired":
                raise TimeoutError(f"{cad_system.title()} server job expired")
            if state not in {"readytopublish", "partial"}:
                error = remote.get("error") or {}
                raise RuntimeError(
                    f"{cad_system.title()} server failed during "
                    f"{error.get('stage') or remote.get('stage')}: "
                    f"{error.get('message') or state}"
                )
            result = remote.get("result") or {}
            if external:
                from modelryx_worker_sdk import validate_result

                validate_result(
                    request_payload,
                    result,
                    allowed_outputs={
                        "native-snapshot": ("application/json", settings.max_preview_bom_bytes),
                        "part-metadata": ("application/json", settings.max_preview_bom_bytes),
                        "model-preview": ("application/json", settings.max_preview_bom_bytes),
                        "glb": ("model/gltf-binary", settings.max_preview_glb_bytes),
                        "drawing-pdf": ("application/pdf", settings.max_pdf_preview_bytes),
                    },
                )
            if cad_system == "siemens_nx" and not external:
                from modelryx.services.licensed_cad_contracts import validate_nx_worker_result

                validate_nx_worker_result(request_payload, result)
            manifests = result.get("outputs") or []
            downloaded: dict[str, tuple[Path, dict]] = {}
            update_stage(db, job, "VALIDATING", 82)
            for manifest in manifests:
                role = str(manifest.get("role") or "")
                download_url = str(manifest.get("downloadUrl") or "")
                if not role or not download_url:
                    raise RuntimeError("licensed CAD server returned an invalid output manifest")
                output_path = workspace / licensed_output_filename(
                    role, cad_system, len(downloaded)
                )
                if cad_system == "siemens_nx" and not external:
                    limit = (
                        settings.max_preview_glb_bytes
                        if role == "glb"
                        else settings.max_pdf_preview_bytes
                        if role == "drawing-pdf"
                        else settings.max_preview_bom_bytes
                        if role in {"native-snapshot", "part-metadata", "model-preview"}
                        else settings.max_preview_package_bytes
                    )
                    if manifest["size"] > limit:
                        raise RuntimeError(
                            f"CAD worker output {role} exceeds the configured size limit"
                        )
                received = 0
                with client.stream(
                    "GET", urljoin(f"{base_url}/", download_url.lstrip("/")), headers=headers
                ) as output_response:
                    output_response.raise_for_status()
                    with output_path.open("wb") as output_stream:
                        for chunk in output_response.iter_bytes():
                            received += len(chunk)
                            if (external or cad_system == "siemens_nx") and received > manifest[
                                "size"
                            ]:
                                raise RuntimeError(
                                    f"CAD worker output {role} exceeds its declared size"
                                )
                            output_stream.write(chunk)
                if output_path.stat().st_size != int(manifest.get("size") or -1):
                    raise RuntimeError(f"licensed CAD output size mismatch for {role}")
                if sha256_file(output_path) != str(manifest.get("sha256") or "").casefold():
                    raise RuntimeError(f"licensed CAD output hash mismatch for {role}")
                downloaded[role] = (output_path, manifest)

        required_roles = {"part-metadata", "native-snapshot", "model-preview"}
        if cad_system == "autocad" and not external:
            required_roles.add("xref-dependencies")
        if missing_roles := required_roles - downloaded.keys():
            raise RuntimeError(
                "licensed CAD server omitted required outputs: " + ", ".join(sorted(missing_roles))
            )
        # Inventor workers before the model-preview contract fix emitted usable
        # reference geometry only as `reference-glb`. Accept it as the primary
        # preview so rolling upgrades do not publish a snapshot without a GLB.
        if "glb" not in downloaded and "reference-glb" in downloaded:
            downloaded["glb"] = downloaded["reference-glb"]
        metadata_path = downloaded["part-metadata"][0]
        part_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if cad_system == "siemens_nx" and not external:
            from modelryx.services.licensed_cad_contracts import validate_nx_worker_metadata

            validate_nx_worker_metadata(
                request_payload,
                json.loads(downloaded["native-snapshot"][0].read_text(encoding="utf-8")),
                part_metadata,
            )
        drawing_manifest: dict | None = None
        drawing_paths: list[Path] = []
        if "drawing-representations" in downloaded:
            drawing_manifest, drawing_paths = read_licensed_drawing_representations(
                downloaded["drawing-representations"][0], workspace, settings
            )
            merge_related_drawing_metadata(part_metadata, drawing_manifest)
            metadata_path.write_text(json.dumps(part_metadata, indent=2), encoding="utf-8")
        from modelryx.metadata.engineering_validator import validate_engineering_metadata
        from modelryx.metadata.validator import validate_part_metadata

        validate_part_metadata(part_metadata)
        validate_engineering_metadata(part_metadata)

        glb_entry = downloaded.get("glb")
        preview_path = downloaded["model-preview"][0]
        preview_document = validate_model_preview(
            json.loads(preview_path.read_text(encoding="utf-8"))
        )
        thumbnail = None
        if glb_entry is not None:
            update_stage(db, job, "RENDERING_PREVIEW", 85)
            thumbnail = generate_revision_thumbnail(glb_entry[0], workspace, preview_path)
        update_stage(db, job, "PUBLISHING", 90)
        generator = f"modelryx-{cad_system}-server"
        generator_version = str(result.get("adapterVersion") or "1.0")
        outputs: list[ArtifactFile] = []
        if glb_entry is not None:
            outputs.append(
                store_output(
                    db,
                    storage,
                    revision,
                    source_snapshot,
                    kind="GLB",
                    filename="model.glb",
                    mime_type="model/gltf-binary",
                    source=glb_entry[0],
                    generator=generator,
                    generator_version=generator_version,
                )
            )
        if thumbnail is not None:
            outputs.append(
                store_output(
                    db,
                    storage,
                    revision,
                    source_snapshot,
                    kind="IMAGE",
                    filename=THUMBNAIL_NAME,
                    mime_type="image/png",
                    source=thumbnail,
                    generator=generator,
                    generator_version=generator_version,
                )
            )
        outputs.append(
            store_output(
                db,
                storage,
                revision,
                source_snapshot,
                kind="METADATA",
                filename="model-preview.json",
                mime_type="application/json",
                source=preview_path,
                generator=generator,
                generator_version=generator_version,
            )
        )
        metadata_artifact = store_output(
            db,
            storage,
            revision,
            source_snapshot,
            kind="METADATA",
            filename="part-metadata.json",
            mime_type="application/json",
            source=metadata_path,
            generator=generator,
            generator_version=generator_version,
        )
        outputs.append(metadata_artifact)
        native_path, native_manifest = downloaded["native-snapshot"]
        raw_artifact = store_output(
            db,
            storage,
            revision,
            source_snapshot,
            kind="OTHER",
            filename=f"{cad_system}.snapshot.json",
            mime_type=str(native_manifest.get("mediaType") or "application/json"),
            source=native_path,
            generator=generator,
            generator_version=generator_version,
        )
        outputs.append(raw_artifact)
        if cad_system == "siemens_nx" and not external:
            # Retain translator/license/conversion provenance with immutable output lineage.
            result_manifest_path = workspace / "siemens_nx.result-manifest.json"
            result_manifest_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            outputs.append(
                store_output(
                    db,
                    storage,
                    revision,
                    source_snapshot,
                    kind="OTHER",
                    filename=result_manifest_path.name,
                    mime_type="application/json",
                    source=result_manifest_path,
                    generator=generator,
                    generator_version=generator_version,
                )
            )
        for role, filename, kind in (
            ("step", "model.step", "OTHER"),
            ("drawing-pdf", "drawing.pdf", "PDF"),
            ("component-geometry", "component-meshes.zip", "BUNDLE"),
            ("xref-dependencies", "xref-dependencies.json", "OTHER"),
        ):
            if role in downloaded:
                path, manifest = downloaded[role]
                outputs.append(
                    store_output(
                        db,
                        storage,
                        revision,
                        source_snapshot,
                        kind=kind,
                        filename=filename,
                        mime_type=str(manifest.get("mediaType") or "application/octet-stream"),
                        source=path,
                        generator=generator,
                        generator_version=generator_version,
                    )
                )
        if drawing_manifest is not None:
            drawing_manifest_path = workspace / "drawing-previews.json"
            drawing_manifest_path.write_text(
                json.dumps(drawing_manifest, indent=2), encoding="utf-8"
            )
            outputs.append(
                store_output(
                    db,
                    storage,
                    revision,
                    source_snapshot,
                    kind="METADATA",
                    filename="drawing-previews.json",
                    mime_type="application/json",
                    source=drawing_manifest_path,
                    generator=generator,
                    generator_version=generator_version,
                )
            )
            for drawing_path in drawing_paths:
                outputs.append(
                    store_output(
                        db,
                        storage,
                        revision,
                        source_snapshot,
                        kind="PDF",
                        filename=drawing_path.name,
                        mime_type="application/pdf",
                        source=drawing_path,
                        generator=generator,
                        generator_version=generator_version,
                    )
                )
        published_snapshot = publish_worker_outputs(db, revision, source_snapshot, outputs)
        if published_snapshot is None:
            raise RuntimeError("revision content changed before licensed CAD publication")
        job.snapshot_id = published_snapshot.id
        revision.derived_metadata_json = {**preview_document, "partMetadata": part_metadata}
        extraction = MetadataService(db).ingest(
            revision=revision,
            snapshot=published_snapshot,
            raw_artifact=raw_artifact,
            metadata=part_metadata,
        )
        worker_warnings = list(result.get("warnings") or [])
        if external and document_kind == "assembly":
            from modelryx.services.bom_cycles import unresolved_native_occurrences

            components = (part_metadata.get("assembly") or {}).get("components") or []
            if components and unresolved_native_occurrences(db, revision.id, components):
                worker_warnings.append(
                    "The worker found assembly components without managed BOM relationships. "
                    "Review the native assembly metadata and resolve the BOM."
                )
            bom_view = db.scalar(
                select(BomViewRevision.id)
                .where(BomViewRevision.revision_id == revision.id)
                .order_by(BomViewRevision.sequence.desc())
                .limit(1)
            )
            if bom_view is not None and any(
                (line.properties_json or {}).get("native_placement") == "unavailable"
                for line in db.scalars(
                    select(BomLine).where(BomLine.bom_view_revision_id == bom_view)
                )
            ):
                worker_warnings.append(
                    "One or more managed BOM relationships use an identity transform because "
                    "native placement was unavailable."
                )
        revision.state = published_revision_state(
            extraction.status,
            worker_warnings + list(part_metadata["quality"]["warnings"]),
        )
        revision.search_text = json.dumps(
            {"metadata": revision.metadata_json, "derived": revision.derived_metadata_json},
            sort_keys=True,
        )
        job.state = "SUCCEEDED"
        job.stage = "COMPLETED"
        job.progress_percent = 100
        job.converter_version = generator_version
        job.diagnostics_json = {
            "warnings": worker_warnings,
            "remote": {
                "cad_system": cad_system,
                "server": base_url,
                "job_id": str(remote_job_id),
            },
        }
        job.finished_at = datetime.now(UTC)
        record_audit(
            db,
            actor_id=None,
            action="processing.succeeded",
            entity_type="revision",
            entity_id=revision.id,
            request_id=f"job:{job.id}",
            details={"job_id": str(job.id), "type": job.type, "cad_system": cad_system},
        )
        db.commit()
        index_ai_best_effort(db, revision, settings)
        duplicate_check_best_effort(db, revision, settings)


def process_job(db: Session, job: ProcessingJob, settings: Settings) -> None:
    if job.type == "CAD_SESSION_EXECUTE":
        from modelryx.cad.execution import process_cad_execution

        process_cad_execution(db, job, settings)
        return
    if job.type == "ASSEMBLY_SOURCE_BUNDLE":
        from modelryx.api.bundles import process_bundle_job

        process_bundle_job(db, job, settings)
        return
    if job.type == "STEP_TO_MODELRYX":
        process_step_job(db, job, settings)
        return
    if job.type in {
        "AUTOCAD_TO_MODELRYX",
        "INVENTOR_TO_MODELRYX",
        "NX_TO_MODELRYX",
        EXTERNAL_CAD_JOB_TYPE,
    }:
        process_licensed_cad_job(db, job, settings)
        return
    if job.type != "FCSTD_TO_MODELRYX":
        raise RuntimeError(f"unsupported processing job type: {job.type}")
    update_stage(db, job, "INSPECTING", 10)
    revision = db.get(Revision, job.revision_id)
    source_snapshot = db.get(RevisionContentSnapshot, job.snapshot_id)
    source_files = (
        artifact_files_for_snapshot(
            db,
            revision.id,
            source_snapshot.id,
            artifact_types={"CAD_MODEL", "CAD_ASSEMBLY"},
        )
        if revision is not None and source_snapshot is not None
        else []
    )
    source_artifact = next(
        (file for file in source_files if file.role == "PRIMARY"),
        source_files[0] if source_files else None,
    )
    if revision is None or source_snapshot is None or source_artifact is None:
        raise RuntimeError("verified native artifact is missing")
    storage = LocalStorageProvider(settings.storage_root)
    packaged_dependencies = [file for file in source_files if file.id != source_artifact.id]
    dependency_files = packaged_dependencies or assembly_dependency_files(
        db, revision, source_snapshot
    )
    job.converter_version = settings.converter_version
    dependency_fingerprint = ":".join(
        sorted(
            f"{file.reference_name or file.original_filename}:{file.sha256}"
            for file in dependency_files
        )
    )
    job.cache_key = hashlib.sha256(
        f"{source_artifact.sha256}:{dependency_fingerprint}:{settings.converter_version}".encode()
    ).hexdigest()
    db.commit()
    cached_job = db.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.id != job.id,
            ProcessingJob.cache_key == job.cache_key,
            ProcessingJob.state == "SUCCEEDED",
            ProcessingJob.type == "FCSTD_TO_MODELRYX",
            ProcessingJob.converter_version == settings.converter_version,
        )
        .order_by(ProcessingJob.finished_at.desc())
    )
    if cached_job is not None:
        cached_artifacts = (
            [
                artifact
                for artifact in artifact_files_for_snapshot(
                    db, cached_job.revision_id, cached_job.snapshot_id
                )
                if (
                    artifact.kind in {"GLB", "METADATA", "OTHER"}
                    and artifact.original_filename
                    in {
                        "model.glb",
                        "model-preview.json",
                        "cad-raw-metadata.json",
                    }
                )
                or (artifact.kind == "IMAGE" and artifact.original_filename == THUMBNAIL_NAME)
                or is_cad_drawing_preview(db, artifact)
            ]
            if cached_job.snapshot_id
            else []
        )
        if {"GLB", "METADATA", "OTHER"}.issubset(
            {artifact.kind for artifact in cached_artifacts}
        ) and all(storage.exists(artifact.storage_key) for artifact in cached_artifacts):
            update_stage(db, job, "CACHE_REUSE", 75)
            check_processing_cancelled(db, job)
            outputs: dict[str, ArtifactFile] = {}
            published_outputs: list[ArtifactFile] = []
            for cached in cached_artifacts:
                stored = store_output(
                    db,
                    storage,
                    revision,
                    source_snapshot,
                    kind=cached.kind,
                    filename=cached.original_filename,
                    mime_type=cached.mime_type,
                    source=storage.path_for(cached.storage_key),
                    generator_version=settings.converter_version,
                )
                published_outputs.append(stored)
                outputs.setdefault(cached.kind, stored)
            if not any(item.original_filename == THUMBNAIL_NAME for item in published_outputs):
                with tempfile.TemporaryDirectory(prefix="modelryx-thumbnail-") as thumbnail_dir:
                    thumbnail = generate_revision_thumbnail(
                        storage.path_for(outputs["GLB"].storage_key),
                        Path(thumbnail_dir),
                        storage.path_for(outputs["METADATA"].storage_key),
                    )
                    if thumbnail is not None:
                        published_outputs.append(
                            store_output(
                                db,
                                storage,
                                revision,
                                source_snapshot,
                                kind="IMAGE",
                                filename=THUMBNAIL_NAME,
                                mime_type="image/png",
                                source=thumbnail,
                                generator_version=settings.converter_version,
                            )
                        )
            published_snapshot = publish_worker_outputs(
                db, revision, source_snapshot, published_outputs
            )
            if published_snapshot is None:
                raise RuntimeError("revision content changed before CAD publication")
            job.snapshot_id = published_snapshot.id
            metadata_path = storage.path_for(outputs["METADATA"].storage_key)
            bom = json.loads(metadata_path.read_text(encoding="utf-8"))
            cached_revision = db.get(Revision, cached_job.revision_id)
            cached_derived = cached_revision.derived_metadata_json if cached_revision else {}
            revision.derived_metadata_json = {
                **bom,
                **{key: cached_derived[key] for key in ("partMetadata",) if key in cached_derived},
            }
            cached_warnings = [
                str(value)
                for value in (cached_job.diagnostics_json or {}).get("warnings", [])
                if str(value).strip()
            ]
            part_metadata = revision.derived_metadata_json.get("partMetadata")
            if part_metadata:
                extraction = MetadataService(db).ingest(
                    revision=revision,
                    snapshot=published_snapshot,
                    raw_artifact=outputs["OTHER"],
                    metadata=part_metadata,
                )
                if published_snapshot is not None:
                    revision.state = published_revision_state(
                        extraction.status,
                        cached_warnings + list(part_metadata["quality"]["warnings"]),
                    )
            elif published_snapshot is not None:
                revision.state = "READY_WITH_WARNINGS"
            revision.search_text = json.dumps(
                {"metadata": revision.metadata_json, "derived": revision.derived_metadata_json},
                sort_keys=True,
            )
            workspace_files = [source_artifact, *dependency_files]
            update_file_stages(db, job, workspace_files, "FINISH", stage="CACHE_REUSED")
            if cached_warnings:
                update_file_stages(
                    db,
                    job,
                    [source_artifact],
                    "WARNING",
                    stage="CACHE_REUSED_WITH_WARNINGS",
                    message=" ".join(cached_warnings)[:2000],
                )
            job.state = "SUCCEEDED"
            job.stage = "COMPLETED"
            job.progress_percent = 100
            job.finished_at = datetime.now(UTC)
            job.diagnostics_json = {
                "warnings": cached_warnings,
                "files": (job.diagnostics_json or {}).get("files", {}),
                "cache": {"hit": True, "sourceJobId": str(cached_job.id)},
            }
            record_audit(
                db,
                actor_id=None,
                action="processing.cache_reused",
                entity_type="revision",
                entity_id=revision.id,
                request_id=f"job:{job.id}",
                details={"source_job_id": str(cached_job.id), "cache_key": job.cache_key},
            )
            db.commit()
            index_ai_best_effort(db, revision, settings)
            duplicate_check_best_effort(db, revision, settings)
            return
    with tempfile.TemporaryDirectory(prefix=f"modelryx-{job.id}-") as directory:
        work_directory = Path(directory)
        workspace_files = [source_artifact, *dependency_files]
        update_file_stages(db, job, workspace_files, "QUEUE", stage="QUEUED")
        update_file_stages(
            db,
            job,
            workspace_files,
            "PROCESSING",
            stage="MATERIALIZING_PACKAGE",
            message="Preparing the native CAD package workspace.",
        )
        source_directory = (
            work_directory / "source" if settings.freecad_worker_url else work_directory
        )
        source_directory.mkdir(exist_ok=True)
        converter_source = materialize_cad_workspace(
            storage, source_artifact, dependency_files, source_directory
        )
        check_processing_cancelled(db, job)
        output_pfg = Path(directory) / "preview.pfg"
        update_stage(db, job, "CONVERTING", 35)

        def report_native_progress(stage: str, progress: int, message: str) -> None:
            check_processing_cancelled(db, job)
            update_stage(db, job, stage, progress)
            update_file_stages(
                db,
                job,
                workspace_files,
                "PROCESSING",
                stage=stage,
                message=message,
            )

        if settings.freecad_worker_url:
            from modelryx.services.freecad_remote import convert_remote

            item = db.get(Item, revision.part_id)
            convert_remote(
                converter_source,
                source_directory,
                output_pfg,
                settings,
                job,
                "assembly" if item and item.part_type == "ASSEMBLY" else "part",
                lambda: check_processing_cancelled(db, job),
                report_native_progress,
            )
        else:
            run_converter(
                converter_source,
                output_pfg,
                settings,
                progress_callback=report_native_progress,
                cancellation_callback=lambda: check_processing_cancelled(db, job),
            )
        check_processing_cancelled(db, job)
        update_stage(db, job, "VALIDATING", 70)
        glb_path, bom_path, bom, diagnostics = read_converter_package(
            output_pfg, Path(directory), settings
        )
        _, part_metadata = read_worker_metadata_sidecars(output_pfg)
        drawing_previews = read_cad_drawing_previews(output_pfg, Path(directory), settings)
        update_file_stages(db, job, workspace_files, "FINISH", stage="VALIDATED")
        warnings = [str(value) for value in diagnostics.get("warnings", []) if str(value).strip()]
        if warnings:
            update_file_stages(
                db,
                job,
                [source_artifact],
                "WARNING",
                stage="VALIDATED_WITH_WARNINGS",
                message=" ".join(warnings)[:2000],
            )
        update_stage(db, job, "RENDERING_PREVIEW", 78)
        thumbnail = generate_revision_thumbnail(glb_path, Path(directory), bom_path)
        check_processing_cancelled(db, job)
        update_stage(db, job, "PUBLISHING", 85)
        outputs = [
            store_output(
                db,
                storage,
                revision,
                source_snapshot,
                kind="GLB",
                filename="model.glb",
                mime_type="model/gltf-binary",
                source=glb_path,
                generator_version=settings.converter_version,
            )
        ]
        outputs.append(
            store_output(
                db,
                storage,
                revision,
                source_snapshot,
                kind="METADATA",
                filename="model-preview.json",
                mime_type="application/json",
                source=bom_path,
                generator_version=settings.converter_version,
            )
        )
        raw_artifact = store_output(
            db,
            storage,
            revision,
            source_snapshot,
            kind="OTHER",
            filename="cad-raw-metadata.json",
            mime_type="application/json",
            source=output_pfg.with_suffix(".cad-raw-metadata.json"),
            generator_version=settings.converter_version,
        )
        outputs.append(raw_artifact)
        if thumbnail is not None:
            outputs.append(
                store_output(
                    db,
                    storage,
                    revision,
                    source_snapshot,
                    kind="IMAGE",
                    filename=THUMBNAIL_NAME,
                    mime_type="image/png",
                    source=thumbnail,
                    generator_version=settings.converter_version,
                )
            )
        outputs.extend(
            store_output(
                db,
                storage,
                revision,
                source_snapshot,
                kind="IMAGE",
                filename=preview.name,
                mime_type="image/png",
                source=preview,
                generator_version=settings.converter_version,
            )
            for preview in drawing_previews
        )
        published_snapshot = publish_worker_outputs(db, revision, source_snapshot, outputs)
        if published_snapshot is None:
            raise RuntimeError("revision content changed before CAD publication")
        job.snapshot_id = published_snapshot.id
        revision.derived_metadata_json = {
            **bom,
            "partMetadata": part_metadata,
        }
        extraction = MetadataService(db).ingest(
            revision=revision,
            snapshot=published_snapshot,
            raw_artifact=raw_artifact,
            metadata=part_metadata,
        )
        revision.search_text = json.dumps(
            {"metadata": revision.metadata_json, "derived": revision.derived_metadata_json},
            sort_keys=True,
        )
        if published_snapshot is not None:
            revision.state = published_revision_state(
                extraction.status, warnings + list(part_metadata["quality"]["warnings"])
            )
        job.state = "SUCCEEDED"
        job.stage = "COMPLETED"
        job.progress_percent = 100
        job.diagnostics_json = {
            **diagnostics,
            "files": (job.diagnostics_json or {}).get("files", {}),
        }
        job.finished_at = datetime.now(UTC)
        record_audit(
            db,
            actor_id=None,
            action="processing.succeeded",
            entity_type="revision",
            entity_id=revision.id,
            request_id=f"job:{job.id}",
            details={"job_id": str(job.id), "type": job.type},
        )
        db.commit()
        index_ai_best_effort(db, revision, settings)
        duplicate_check_best_effort(db, revision, settings)
        logger.info(
            "job_succeeded job_id=%s revision_id=%s glb_bytes=%s bom_items=%s",
            job.id,
            revision.id,
            glb_path.stat().st_size,
            len(bom.get("items", [])),
        )


def fail_job(db: Session, job: ProcessingJob, error: Exception) -> None:
    job = db.get(ProcessingJob, job.id)
    revision = db.get(Revision, job.revision_id)
    failed_stage = job.stage
    failure = classify_processing_failure(error)
    previous_diagnostics = dict(job.diagnostics_json or {})
    file_diagnostics = dict(previous_diagnostics.get("files") or {})
    for value in file_diagnostics.values():
        if value.get("status") in {"QUEUE", "PROCESSING"}:
            value.update(
                status="ERROR" if not isinstance(error, ProcessingCancelled) else "WARNING",
                stage="CANCELLED" if isinstance(error, ProcessingCancelled) else value.get("stage"),
                message=str(error)[-4000:],
                fix_suggestion=failure.suggestion,
            )
    job.error_detail = str(error)[-4000:]
    job.diagnostics_json = {
        "files": file_diagnostics,
        **(
            {"cancellation": previous_diagnostics["cancellation"]}
            if "cancellation" in previous_diagnostics
            else {}
        ),
        "errors": [
            {
                "stage": failed_stage,
                "code": failure.code,
                "message": job.error_detail,
                "retryable": failure.retryable,
                "fix_suggestion": failure.suggestion,
            }
        ],
    }
    if isinstance(error, ProcessingCancelled):
        job.state = "FAILED"
        job.stage = "CANCELLED"
        job.finished_at = datetime.now(UTC)
        if job.type == "CAD_SESSION_EXECUTE":
            from modelryx.cad.execution import fail_cad_execution

            fail_cad_execution(db, job, cancelled=True)
        elif (
            revision is not None and latest_content_snapshot_id(db, revision.id) == job.snapshot_id
        ):
            revision.state = "READY_WITH_WARNINGS"
        record_audit(
            db,
            actor_id=None,
            action="processing.cancellation_completed",
            entity_type="revision",
            entity_id=job.revision_id,
            request_id=f"job:{job.id}",
            details={"job_id": str(job.id), "stage": failed_stage},
        )
        db.commit()
        return
    if job.type == "CAD_SESSION_EXECUTE":
        from modelryx.cad.execution import fail_cad_execution
        from modelryx.models import CADExecution

        execution = db.scalar(select(CADExecution).where(CADExecution.processing_job_id == job.id))
        fail_cad_execution(db, job)
        job.state = "FAILED"
        job.stage = "FAILED"
        job.finished_at = datetime.now(UTC)
        record_audit(
            db,
            actor_id=None,
            action="cad_execution.failed",
            entity_type="cad_execution",
            entity_id=execution.id if execution else job.id,
            request_id=f"job:{job.id}",
            details={"stage": job.diagnostics_json["errors"][0]["stage"]},
        )
        db.commit()
        return
    if failure.retryable and job.attempt_count < MAX_ATTEMPTS:
        job.state = "PENDING"
        job.stage = "QUEUED"
        job.progress_percent = 0
        job.available_at = datetime.now(UTC) + timedelta(seconds=2**job.attempt_count)
    else:
        job.state = "FAILED"
        job.stage = "FAILED"
        job.finished_at = datetime.now(UTC)
        if revision is not None and job.type in {
            "AUTOCAD_TO_MODELRYX",
            EXTERNAL_CAD_JOB_TYPE,
            "FCSTD_TO_MODELRYX",
            "INVENTOR_TO_MODELRYX",
            "NX_TO_MODELRYX",
            "STEP_TO_MODELRYX",
        }:
            if latest_content_snapshot_id(db, revision.id) == job.snapshot_id:
                revision.state = (
                    "READY_WITH_WARNINGS" if job.type == "FCSTD_TO_MODELRYX" else "FAILED"
                )
            if failed_stage in {"VALIDATING", "VALIDATING_OUTPUTS"}:
                revision.metadata_status = "INVALID"
        if job.type == "ASSEMBLY_SOURCE_BUNDLE":
            from modelryx.models import DownloadBundle

            bundle = db.scalar(
                select(DownloadBundle).where(DownloadBundle.processing_job_id == job.id)
            )
            if bundle is not None:
                bundle.state = "FAILED"
                bundle.error_detail = job.error_detail
        record_audit(
            db,
            actor_id=None,
            action="processing.failed",
            entity_type="revision",
            entity_id=job.revision_id,
            request_id=f"job:{job.id}",
            details={"job_id": str(job.id), "error": job.error_detail},
        )
    db.commit()
    logger.warning(
        "job_failed job_id=%s revision_id=%s attempt=%s terminal=%s error=%s",
        job.id,
        job.revision_id,
        job.attempt_count,
        job.state == "FAILED",
        job.error_detail,
    )


# Import compatibility for extensions that used the pre-adapter helper names.
report_freecad_heartbeat = report_cad_worker_heartbeat
read_freecad_drawing_previews = read_cad_drawing_previews
is_freecad_drawing_preview = is_cad_drawing_preview


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    worker_role = settings.processing_worker_role
    logger.info("worker_started role=%s poll_seconds=%s", worker_role, settings.worker_poll_seconds)
    last_maintenance = 0.0
    while True:
        with SessionLocal() as db:
            current_settings = settings.model_copy(
                update={
                    "freecad_worker_url": configured_url(
                        db, FREECAD_WORKER_KEY, settings.freecad_worker_url
                    )
                }
            )
            if worker_role == "general":
                report_general_worker_heartbeat(db, status="IDLE")
            else:
                report_cad_worker_heartbeat(db, settings, status="IDLE")
            monotonic_now = time.monotonic()
            if (
                worker_role in {"all", "general"}
                and monotonic_now - last_maintenance >= settings.maintenance_cleanup_seconds
            ):
                try:
                    recovered = recover_abandoned_jobs(db, settings)
                    recovered_staging_workflows = recover_staging_workflows(
                        db, settings.processing_stale_seconds
                    )
                    notifications = create_checkout_expiry_notifications(
                        db,
                        warning_seconds=settings.checkout_expiry_warning_seconds,
                    )
                    record_checkout_notification_run(db, result=notifications)
                    approval_escalations = create_overdue_approval_escalations(db)
                    db.commit()
                    logger.info(
                        "checkout_expiry_notification_run_completed created=%s examined=%s",
                        notifications.created,
                        notifications.examined,
                    )
                    logger.info(
                        "approval_escalation_run_completed created=%s overdue=%s expired=%s",
                        approval_escalations.created,
                        approval_escalations.overdue_requests,
                        approval_escalations.expired_emergency_requests,
                    )
                    logger.info(
                        "abandoned_job_recovery_completed requeued=%s failed=%s",
                        recovered.requeued,
                        recovered.failed,
                    )
                    logger.info(
                        "staging_workflow_recovery_completed requeued=%s",
                        recovered_staging_workflows,
                    )
                except Exception as exc:
                    db.rollback()
                    record_checkout_notification_run(db, failure_detail=str(exc))
                    db.commit()
                    logger.exception("checkout_expiry_notification_generation_failed")
                last_maintenance = monotonic_now
            staging_workflow = (
                claim_staging_workflow(db) if worker_role in {"all", "general"} else None
            )
            if staging_workflow is not None:
                staging_id = staging_workflow.id
                logger.info("staging_workflow_claimed staging_id=%s", staging_id)
                try:
                    process_staging_workflow(db, staging_id, settings)
                    logger.info("staging_workflow_finished staging_id=%s", staging_id)
                except Exception as exc:
                    db.rollback()
                    fail_staging_workflow(db, staging_id, exc)
                    logger.exception("staging_workflow_failed staging_id=%s", staging_id)
                continue
            job = claim_job(
                db, worker_role, remote_freecad=bool(current_settings.freecad_worker_url)
            )
            if job is not None:
                if worker_role == "general":
                    report_general_worker_heartbeat(db, status="BUSY", current_job_id=job.id)
                else:
                    report_cad_worker_heartbeat(db, settings, status="BUSY", current_job_id=job.id)
                heartbeat_stop = threading.Event()
                heartbeat_thread = threading.Thread(
                    target=busy_heartbeat_loop,
                    args=(settings, job.id, heartbeat_stop, worker_role),
                    name=f"processing-worker-heartbeat-{job.id}",
                    daemon=True,
                )
                heartbeat_thread.start()
                try:
                    process_job(db, job, current_settings)
                except Exception as exc:
                    db.rollback()
                    fail_job(db, job, exc)
                finally:
                    heartbeat_stop.set()
                    heartbeat_thread.join(timeout=2)
        if job is None:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
