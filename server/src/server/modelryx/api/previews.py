import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.api.assemblies import read_item_bom
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import (
    Artifact,
    ArtifactFile,
    Part,
    ProcessingJob,
    Revision,
    RevisionContentSnapshot,
)
from modelryx.models.schemas import ProcessingFileStatus, ProcessingJobResponse
from modelryx.services.artifact_content import (
    artifact_files_for_snapshot,
    current_artifact_files,
    has_published_derived_content,
    latest_content_snapshot_id,
)
from modelryx.services.glb_thumbnail import THUMBNAIL_NAME, render_glb_thumbnail
from modelryx.services.storage import LocalStorageProvider

router = APIRouter(prefix="/api/v1", tags=["processing"])
logger = logging.getLogger("modelryx.preview")
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


def _can_control_processing(db: Session, revision: Revision, principal: Principal) -> bool:
    if principal.role == "administrator":
        return True
    item = db.get(Part, revision.part_id)
    user = user_for_principal(db, principal)
    return item is not None and item.created_by == user.id


def _require_processing_control(db: Session, revision: Revision, principal: Principal) -> None:
    if not _can_control_processing(db, revision, principal):
        raise HTTPException(
            status_code=403,
            detail="Only the item creator or an administrator can control processing",
        )


def _native_processing_type(
    filename: str, cad_system: str, db: Session | None = None
) -> str | None:
    extension = Path(filename).suffix.casefold()
    if db is not None and extension not in {".step", ".stp"}:
        from modelryx.services.external_workers import (
            JOB_TYPE,
            AmbiguousWorkerError,
            matching_worker,
        )

        try:
            if matching_worker(db, filename, cad_system):
                return JOB_TYPE
        except AmbiguousWorkerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if extension == ".fcstd":
        return "FCSTD_TO_MODELRYX"
    if extension in {".step", ".stp"}:
        return "STEP_TO_MODELRYX"
    return None


def _processing_fix_suggestion(job: ProcessingJob) -> str | None:
    if job.state != "FAILED":
        return None
    errors = (job.diagnostics_json or {}).get("errors") or []
    if errors and isinstance(errors[0], dict):
        suggestion = errors[0].get("fix_suggestion")
        if isinstance(suggestion, str) and suggestion.strip():
            return suggestion
    stage = job.stage.upper()
    if "QUEUED" in stage:
        return (
            "Confirm that the required CAD worker is running and connected, then retry the stage."
        )
    if "INSPECT" in stage:
        return (
            "Open the source in its CAD application, resolve missing references or "
            "repair the document, save it, and retry."
        )
    if "CONVERT" in stage:
        return (
            "Confirm the model contains supported geometry and that the "
            "geometry-conversion worker is available, then retry."
        )
    if "VALIDAT" in stage:
        return (
            "Review the worker diagnostic, correct invalid geometry or metadata in "
            "the source document, and retry."
        )
    if "PUBLISH" in stage:
        return "Verify Modelryx storage is writable and has free space, then retry the stage."
    return (
        "Review the error below, correct the source or worker configuration, and retry the stage."
    )


def _job_response_with_files(
    db: Session, job: ProcessingJob, *, can_control: bool
) -> ProcessingJobResponse:
    status = {
        "PENDING": "QUEUE",
        "RUNNING": "PROCESSING",
        "SUCCEEDED": "FINISH",
        "FAILED": "ERROR",
    }.get(job.state, "PROCESSING")
    if job.state == "SUCCEEDED" and (job.diagnostics_json or {}).get("warnings"):
        status = "WARNING"
    suggestion = _processing_fix_suggestion(job)
    file_diagnostics = (job.diagnostics_json or {}).get("files") or {}
    revision = db.get(Revision, job.revision_id)
    item = db.get(Part, revision.part_id) if revision is not None else None
    files = []
    if job.snapshot_id is not None:
        files = artifact_files_for_snapshot(
            db,
            job.revision_id,
            job.snapshot_id,
            artifact_types={"CAD_MODEL", "CAD_ASSEMBLY", "STEP"},
        )
    return ProcessingJobResponse.model_validate(job).model_copy(
        update={
            "can_control": can_control,
            "fix_suggestion": suggestion,
            "files": [
                ProcessingFileStatus(
                    id=value.id,
                    logical_path=value.reference_name or value.original_filename,
                    filename=value.original_filename,
                    byte_size=value.byte_size,
                    role=value.role,
                    status=file_diagnostics.get(str(value.id), {}).get("status", status),
                    stage=file_diagnostics.get(str(value.id), {}).get("stage"),
                    message=file_diagnostics.get(str(value.id), {}).get(
                        "message", job.error_detail if job.state == "FAILED" else None
                    ),
                    fix_suggestion=file_diagnostics.get(str(value.id), {}).get(
                        "fix_suggestion", suggestion
                    ),
                )
                for value in files
            ],
            "item_number": item.part_number if item is not None else None,
            "item_name": item.name if item is not None else None,
        }
    )


@router.get("/processing-jobs", response_model=list[ProcessingJobResponse])
def list_processing_jobs(
    db: Db,
    principal: Actor,
    state: Annotated[Literal["all", "ongoing", "finished"], Query()] = "ongoing",
    owner_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[ProcessingJobResponse]:
    """List processing work for the global Stages page, scoped to the current owner."""
    user = user_for_principal(db, principal)
    conditions = []
    if principal.role != "administrator":
        conditions.append(Part.created_by == user.id)
    elif owner_id is not None:
        conditions.append(Part.created_by == owner_id)
    if state == "ongoing":
        conditions.append(ProcessingJob.state.in_(["PENDING", "RUNNING"]))
    elif state == "finished":
        conditions.append(ProcessingJob.state.in_(["SUCCEEDED", "FAILED"]))
    rows = db.execute(
        select(ProcessingJob, Revision)
        .join(Revision, Revision.id == ProcessingJob.revision_id)
        .join(Part, Part.id == Revision.part_id)
        .where(*conditions)
        .order_by(ProcessingJob.available_at.desc(), ProcessingJob.id.desc())
        .limit(limit)
    ).all()
    return [
        _job_response_with_files(
            db,
            job,
            can_control=_can_control_processing(db, revision, principal),
        )
        for job, revision in rows
    ]


@router.get("/processing-jobs/{job_id}", response_model=ProcessingJobResponse)
def read_processing_job(job_id: uuid.UUID, db: Db, principal: Actor) -> ProcessingJobResponse:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    revision = db.get(Revision, job.revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    return _job_response_with_files(
        db, job, can_control=_can_control_processing(db, revision, principal)
    )


@router.get(
    "/revisions/{revision_id}/processing-jobs",
    response_model=list[ProcessingJobResponse],
)
def revision_processing_jobs(
    revision_id: uuid.UUID, db: Db, principal: Actor
) -> list[ProcessingJobResponse]:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    can_control = _can_control_processing(db, revision, principal)
    jobs = list(
        db.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.revision_id == revision_id)
            .order_by(ProcessingJob.available_at.desc())
        )
    )
    return [_job_response_with_files(db, job, can_control=can_control) for job in jobs]


@router.post(
    "/revisions/{revision_id}/preview/reprocess",
    response_model=ProcessingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_revision_preview(
    revision_id: uuid.UUID, request: Request, db: Db, principal: Actor
) -> ProcessingJob:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    if not principal.has_role("designer", "administrator"):
        raise HTTPException(
            status_code=403, detail="Designer role required to generate metadata and preview"
        )
    from modelryx.api.items import revision_action_availability

    availability = revision_action_availability(revision.part_id, revision.id, db, principal)
    if not availability["worker_configured"]:
        raise HTTPException(status_code=409, detail="No matching CAD worker is configured")
    snapshot_id = latest_content_snapshot_id(db, revision.id)
    if snapshot_id is None:
        raise HTTPException(status_code=409, detail="Revision has no published content snapshot")
    candidates = [
        (file, processing_type)
        for file in current_artifact_files(db, revision.id)
        if (
            processing_type := _native_processing_type(
                file.original_filename, revision.cad_system, db
            )
        )
    ]
    native, processing_type = next(
        (candidate for candidate in candidates if candidate[0].role == "PRIMARY"),
        candidates[0] if candidates else (None, None),
    )
    if native is None or processing_type is None:
        raise HTTPException(
            status_code=409,
            detail="Revision has no supported native CAD source to regenerate",
        )
    active = db.scalar(
        select(ProcessingJob).where(
            ProcessingJob.revision_id == revision.id,
            ProcessingJob.snapshot_id == snapshot_id,
            ProcessingJob.type == processing_type,
            ProcessingJob.state.in_(["PENDING", "RUNNING"]),
        )
    )
    if active is not None:
        return active
    job = ProcessingJob(
        revision_id=revision.id,
        snapshot_id=snapshot_id,
        type=processing_type,
        idempotency_key=f"regenerate:{processing_type.lower()}:{snapshot_id}:{uuid.uuid4()}",
    )
    db.add(job)
    user = user_for_principal(db, principal)
    db.flush()
    record_audit(
        db,
        actor_id=user.id,
        action="preview.regeneration_requested",
        entity_type="revision",
        entity_id=revision.id,
        request_id=request.state.request_id,
        details={"job_id": str(job.id), "processing_type": processing_type},
    )
    db.commit()
    db.refresh(job)
    return job


@router.post(
    "/processing-jobs/{job_id}/retry",
    response_model=ProcessingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_processing_job(
    job_id: uuid.UUID, request: Request, db: Db, principal: Actor
) -> ProcessingJobResponse:
    previous = db.get(ProcessingJob, job_id)
    if previous is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    if previous.type == "CAD_SESSION_EXECUTE":
        raise HTTPException(status_code=409, detail="Controlled CAD editing is disabled")
    revision = db.get(Revision, previous.revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    _require_processing_control(db, revision, principal)
    if previous.state != "FAILED":
        raise HTTPException(status_code=409, detail="Only failed processing jobs can be retried")
    if previous.snapshot_id != latest_content_snapshot_id(db, revision.id):
        raise HTTPException(
            status_code=409,
            detail=(
                "This job belongs to an earlier content version. Refresh the revision "
                "and generate metadata and preview for the current content instead."
            ),
        )
    active = db.scalar(
        select(ProcessingJob).where(
            ProcessingJob.revision_id == previous.revision_id,
            ProcessingJob.snapshot_id == previous.snapshot_id,
            ProcessingJob.type == previous.type,
            ProcessingJob.state.in_(["PENDING", "RUNNING"]),
        )
    )
    if active is not None:
        return _job_response_with_files(db, active, can_control=True)
    job = ProcessingJob(
        revision_id=previous.revision_id,
        snapshot_id=previous.snapshot_id,
        type=previous.type,
        idempotency_key=f"retry:{previous.type.lower()}:{previous.id}:{uuid.uuid4()}",
    )
    if revision.state not in {"READY", "READY_WITH_WARNINGS"}:
        revision.state = (
            "READY_WITH_WARNINGS"
            if has_published_derived_content(db, revision.id)
            else "PROCESSING"
        )
    db.add(job)
    actor = user_for_principal(db, principal)
    db.flush()
    record_audit(
        db,
        actor_id=actor.id,
        action="processing.retry_requested",
        entity_type="revision",
        entity_id=revision.id,
        request_id=request.state.request_id,
        details={"job_id": str(job.id), "previous_job_id": str(previous.id)},
    )
    db.commit()
    db.refresh(job)
    return _job_response_with_files(db, job, can_control=True)


@router.post(
    "/processing-jobs/{job_id}/cancel",
    response_model=ProcessingJobResponse,
)
def cancel_processing_job(
    job_id: uuid.UUID, request: Request, db: Db, principal: Actor
) -> ProcessingJobResponse:
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    revision = db.get(Revision, job.revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    _require_processing_control(db, revision, principal)
    if job.state == "RUNNING":
        freecad_execution = False
        if job.type == "CAD_SESSION_EXECUTE":
            from modelryx.models import CADExecution, CADOperationPlan, CADSession

            worker_id = db.scalar(
                select(CADSession.worker_id)
                .join(CADOperationPlan, CADOperationPlan.session_id == CADSession.id)
                .join(CADExecution, CADExecution.plan_id == CADOperationPlan.id)
                .where(CADExecution.processing_job_id == job.id)
            )
            freecad_execution = worker_id == "freecad-controlled"
        if job.type != "FCSTD_TO_MODELRYX" and not freecad_execution:
            raise HTTPException(
                status_code=409,
                detail="In-flight cancellation is currently supported only for FreeCAD processing",
            )
        if job.stage in {"PUBLISHING", "COMPLETED"}:
            raise HTTPException(
                status_code=409,
                detail="Processing can no longer be cancelled because publication has begun",
            )
        diagnostics = dict(job.diagnostics_json or {})
        if (diagnostics.get("cancellation") or {}).get("requested") is not True:
            actor = user_for_principal(db, principal)
            diagnostics["cancellation"] = {
                "requested": True,
                "requested_at": datetime.now(UTC).isoformat(),
                "requested_by": str(actor.id),
            }
            job.diagnostics_json = diagnostics
            job.stage = "CANCELLING"
            record_audit(
                db,
                actor_id=actor.id,
                action="processing.cancellation_requested",
                entity_type="revision",
                entity_id=revision.id,
                request_id=request.state.request_id,
                details={"job_id": str(job.id), "processing_type": job.type},
            )
            db.commit()
            db.refresh(job)
        return _job_response_with_files(db, job, can_control=True)
    if job.state != "PENDING":
        raise HTTPException(
            status_code=409,
            detail="Only pending or safely cancellable FreeCAD jobs can be cancelled",
        )
    job.state = "FAILED"
    job.stage = "CANCELLED"
    job.error_detail = "Cancelled by the item creator or an administrator"
    job.finished_at = datetime.now(UTC)
    if job.type == "CAD_SESSION_EXECUTE":
        from modelryx.cad.execution import fail_cad_execution

        fail_cad_execution(db, job, cancelled=True)
    else:
        revision.state = "READY_WITH_WARNINGS"
    actor = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=actor.id,
        action="processing.cancelled",
        entity_type="revision",
        entity_id=revision.id,
        request_id=request.state.request_id,
        details={"job_id": str(job.id), "processing_type": job.type},
    )
    db.commit()
    db.refresh(job)
    return _job_response_with_files(db, job, can_control=True)


@router.get("/revisions/{revision_id}/preview", response_class=FileResponse)
def revision_preview(
    revision_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Db,
    _: Actor,
    settings: Config,
) -> FileResponse:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    artifacts = current_artifact_files(
        db,
        revision.id,
        artifact_types={"GLB", "METADATA"},
    )
    glb = next((item for item in artifacts if item.original_filename == "model.glb"), None)
    metadata = next(
        (item for item in artifacts if item.original_filename == "model-preview.json"), None
    )
    if glb is None or metadata is None:
        job = db.scalar(
            select(ProcessingJob)
            .where(
                ProcessingJob.revision_id == revision_id,
                ProcessingJob.snapshot_id == latest_content_snapshot_id(db, revision.id),
            )
            .order_by(ProcessingJob.started_at.desc().nullslast())
        )
        detail = "Preview processing has not completed"
        if job is not None and job.state == "FAILED":
            detail = "Preview generation failed"
        raise HTTPException(status_code=409, detail=detail)

    storage = LocalStorageProvider(settings.storage_root)
    glb_path = storage.path_for(glb.storage_key)
    metadata_path = storage.path_for(metadata.storage_key)
    if not glb_path.is_file() or not metadata_path.is_file():
        raise HTTPException(status_code=409, detail="Preview artifacts are unavailable")

    preview_directory = settings.storage_root / ".previews"
    preview_directory.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(suffix=".pfg", dir=preview_directory)
    os.close(handle)
    Path(temporary_name).unlink(missing_ok=True)
    manifest = {
        "format": "Modelryx Graphics",
        "version": 1,
        "units": "m",
        "model": "model.glb",
        "modelPreview": "model-preview.json",
        "source": {"system": revision.cad_system, "revisionId": str(revision.id)},
    }
    with zipfile.ZipFile(temporary_name, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("manifest.json", json.dumps(manifest, indent=2))
        package.write(glb_path, "model.glb")
        package.write(metadata_path, "model-preview.json")
    background_tasks.add_task(Path(temporary_name).unlink, missing_ok=True)
    return FileResponse(
        temporary_name,
        media_type="application/vnd.modelryx.graphics+zip",
        filename=f"revision-{revision.revision_code}.pfg",
    )


def preview_files(
    db: Session, revision_id: uuid.UUID, snapshot_id: uuid.UUID | None = None
) -> list[ArtifactFile]:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    if snapshot_id is None:
        return current_artifact_files(db, revision_id)
    snapshot = db.get(RevisionContentSnapshot, snapshot_id)
    if snapshot is None or snapshot.revision_id != revision_id:
        raise HTTPException(status_code=404, detail="Revision version not found")
    return artifact_files_for_snapshot(db, revision_id, snapshot_id)


def preview_artifact(
    db: Session,
    revision_id: uuid.UUID,
    kind: str,
    snapshot_id: uuid.UUID | None = None,
) -> ArtifactFile:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    expected_name = {
        "GLB": "model.glb",
        "PDF": "drawing.pdf",
    }.get(kind, "model-preview.json")
    artifact = next(
        (
            item
            for item in preview_files(db, revision.id, snapshot_id)
            if item.kind == kind and item.original_filename == expected_name
        ),
        None,
    )
    if artifact is None:
        raise HTTPException(status_code=409, detail=f"{kind} preview artifact is unavailable")
    return artifact


@router.get("/revisions/{revision_id}/preview/model", response_class=FileResponse)
def revision_preview_model(
    revision_id: uuid.UUID,
    db: Db,
    _: Actor,
    settings: Config,
    snapshot_id: uuid.UUID | None = None,
) -> FileResponse:
    artifact = preview_artifact(db, revision_id, "GLB", snapshot_id)
    path = LocalStorageProvider(settings.storage_root).path_for(artifact.storage_key)
    return FileResponse(path, media_type="model/gltf-binary", filename="model.glb")


@lru_cache(maxsize=64)
def _display_thumbnail(
    model_path: str, model_sha: str, metadata_path: str, metadata_sha: str
) -> bytes:
    # The content hashes keep this cache tied to immutable managed representations.
    with tempfile.TemporaryDirectory(prefix="modelryx-display-thumbnail-") as directory:
        output = Path(directory) / THUMBNAIL_NAME
        render_glb_thumbnail(Path(model_path), output, preview_path=Path(metadata_path))
        return output.read_bytes()


@router.get("/revisions/{revision_id}/preview/thumbnail", response_class=Response)
def revision_preview_thumbnail(
    revision_id: uuid.UUID,
    db: Db,
    _: Actor,
    settings: Config,
    snapshot_id: uuid.UUID | None = None,
) -> Response:
    files = preview_files(db, revision_id, snapshot_id)
    thumbnail = next(
        (
            item
            for item in files
            if item.kind == "IMAGE" and item.original_filename == THUMBNAIL_NAME
        ),
        None,
    )
    model = next(
        (item for item in files if item.kind == "GLB" and item.original_filename == "model.glb"),
        None,
    )
    metadata = next(
        (
            item
            for item in files
            if item.kind == "METADATA" and item.original_filename == "model-preview.json"
        ),
        None,
    )
    storage = LocalStorageProvider(settings.storage_root)
    if model is not None and metadata is not None:
        model_path = storage.path_for(model.storage_key)
        metadata_path = storage.path_for(metadata.storage_key)
        try:
            # Keep oversized models on the already-generated artifact path.
            if model_path.stat().st_size <= 64 * 1024 * 1024:
                image = _display_thumbnail(
                    str(model_path), model.sha256, str(metadata_path), metadata.sha256
                )
                return Response(
                    content=image,
                    media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="{THUMBNAIL_NAME}"'},
                )
        except Exception as error:  # A thumbnail failure must not hide a stored preview.
            logger.warning("display_thumbnail_failed revision=%s error=%s", revision_id, error)
    if thumbnail is None:
        raise HTTPException(status_code=404, detail="Revision thumbnail is unavailable")
    path = storage.path_for(thumbnail.storage_key)
    return FileResponse(
        path,
        media_type="image/png",
        filename=thumbnail.original_filename,
        content_disposition_type="inline",
    )


@router.get("/revisions/{revision_id}/preview/metadata", response_class=Response)
def revision_preview_metadata(
    revision_id: uuid.UUID,
    db: Db,
    _: Actor,
    settings: Config,
    snapshot_id: uuid.UUID | None = None,
) -> Response:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    artifact = preview_artifact(db, revision_id, "METADATA", snapshot_id)
    path = LocalStorageProvider(settings.storage_root).path_for(artifact.storage_key)
    return Response(
        path.read_bytes(),
        media_type="application/json",
        headers={"Content-Disposition": 'inline; filename="model-preview.json"'},
    )


@router.get("/items/{item_id}/revisions/{revision_id}/preview/occurrence-map")
def preview_occurrence_map(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
    settings: Config,
    snapshot_id: uuid.UUID | None = None,
) -> dict:
    """Map managed BOM occurrences to preview IDs only when exact identifiers agree uniquely."""
    # Root occurrences belong to the selected snapshot. Child content snapshots
    # are not recorded in this BOM, so nested matches would imply false provenance.
    bom = read_item_bom(item_id, revision_id, db, principal, 1, snapshot_id)
    metadata = preview_artifact(db, revision_id, "METADATA", snapshot_id)
    path = LocalStorageProvider(settings.storage_root).path_for(metadata.storage_key)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=409, detail="Preview navigation data is unavailable"
        ) from error
    raw_items = document.get("items", []) if isinstance(document, dict) else []
    preview_values: dict[str, set[str]] = {}
    preview_id_counts: dict[str, int] = {}
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("occurrenceId"), str):
            continue
        preview_id = item["occurrenceId"]
        preview_id_counts[preview_id] = preview_id_counts.get(preview_id, 0) + 1
        for key in (preview_id, item.get("sourceObjectId")):
            if isinstance(key, str) and key:
                preview_values.setdefault(key, set()).add(preview_id)
    managed = []

    def visit(nodes: list) -> None:
        for node in nodes:
            managed.append(node)
            visit(node.children)

    visit(bom.occurrences)
    candidates: dict[str, set[str]] = {}
    for node in managed:
        keys = {str(node.id), node.occurrence_key, node.source_reference}
        candidates[str(node.id)] = set().union(
            *(preview_values.get(key, set()) for key in keys if key)
        )
    reverse: dict[str, int] = {}
    for options in candidates.values():
        if len(options) == 1:
            preview_id = next(iter(options))
            reverse[preview_id] = reverse.get(preview_id, 0) + 1
    matches = {}
    ambiguous = []
    unmapped = []
    for managed_id, options in candidates.items():
        preview_id = next(iter(options)) if len(options) == 1 else None
        if preview_id and reverse[preview_id] == 1 and preview_id_counts[preview_id] == 1:
            matches[managed_id] = preview_id
        elif options:
            ambiguous.append(managed_id)
        else:
            unmapped.append(managed_id)
    return {"matches": matches, "ambiguous": ambiguous, "unmapped": unmapped}


@router.get("/revisions/{revision_id}/preview/drawing", response_class=FileResponse)
def revision_preview_drawing(
    revision_id: uuid.UUID, db: Db, _: Actor, settings: Config
) -> FileResponse:
    artifact = preview_artifact(db, revision_id, "PDF")
    path = LocalStorageProvider(settings.storage_root).path_for(artifact.storage_key)
    return FileResponse(path, media_type="application/pdf", filename="drawing.pdf")


def _drawing_representation_metadata(
    db: Session,
    revision_id: uuid.UUID,
    settings: Settings,
    snapshot_id: uuid.UUID | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    files = preview_files(db, revision_id, snapshot_id)
    result: dict[str, dict] = {}
    failures: list[dict] = []
    normalized_file = next(
        (item for item in files if item.original_filename == "part-metadata.json"),
        None,
    )
    if normalized_file is not None:
        path = LocalStorageProvider(settings.storage_root).path_for(normalized_file.storage_key)
        try:
            normalized = json.loads(path.read_text(encoding="utf-8"))
            sheets = normalized.get("native", {}).get("drawing_sheets", [])
            if isinstance(sheets, list) and sheets:
                result["drawing.pdf"] = {
                    "display_name": str(
                        normalized.get("identity", {}).get("display_name") or "Drawing"
                    ),
                    "source_path": str(normalized.get("identity", {}).get("name") or "drawing"),
                    "sheet_count": len(sheets),
                    "sheets": sheets,
                    "warnings": [],
                }
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            pass
    manifest_file = next(
        (item for item in files if item.original_filename == "drawing-previews.json"),
        None,
    )
    if manifest_file is None:
        return result, failures
    path = LocalStorageProvider(settings.storage_root).path_for(manifest_file.storage_key)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return result, failures
    drawings = manifest.get("drawings", [])
    if not isinstance(drawings, list):
        return result, failures
    for index, drawing in enumerate(drawings[:100], start=1):
        if not isinstance(drawing, dict):
            continue
        source = str(drawing.get("source") or drawing.get("file") or "drawing")
        source_name = Path(source).stem
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", source_name).strip("-._")[:80]
        published_name = f"drawing-{index:04d}-{safe_name or 'drawing'}.pdf"
        sheets = drawing.get("sheets", [])
        result[published_name] = {
            "display_name": str(drawing.get("displayName") or source_name),
            "source_path": source,
            "sheet_count": len(sheets) if isinstance(sheets, list) else 0,
            "sheets": sheets if isinstance(sheets, list) else [],
            "warnings": drawing.get("warnings", []),
        }
    source_files = [
        item
        for item in files
        if item.artifact_id
        and (source := db.get(Artifact, item.artifact_id))
        and source.artifact_type == "DRAWING"
    ]
    source_by_path = {
        (item.reference_name or item.original_filename).replace("\\", "/").casefold(): item
        for item in source_files
    }
    raw_failures = manifest.get("failures", [])
    if isinstance(raw_failures, list):
        for failure in raw_failures[:100]:
            if not isinstance(failure, dict):
                continue
            source = str(failure.get("source") or "related drawing").replace("\\", "/")
            source_file = source_by_path.get(source.casefold())
            stable_id = (
                str(source_file.artifact_id)
                if source_file is not None and source_file.artifact_id is not None
                else "failed-" + hashlib.sha256(source.encode()).hexdigest()[:24]
            )
            failures.append(
                {
                    "id": stable_id,
                    "kind": "2D",
                    "filename": Path(source).name,
                    "display_name": str(failure.get("displayName") or Path(source).stem),
                    "source_path": source,
                    "source_artifact_id": (
                        str(source_file.artifact_id)
                        if source_file is not None and source_file.artifact_id is not None
                        else None
                    ),
                    "media_type": "application/pdf",
                    "preview_url": None,
                    "retry_url": None,
                    "state": "FAILED",
                    "error": str(failure.get("error") or "Drawing representation export failed"),
                    "sheet_count": None,
                    "sheets": [],
                }
            )
    return result, failures


@router.get("/revisions/{revision_id}/preview/representations")
def revision_preview_representations(
    revision_id: uuid.UUID,
    db: Db,
    _: Actor,
    settings: Config,
    snapshot_id: uuid.UUID | None = None,
) -> list[dict]:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    files = preview_files(db, revision.id, snapshot_id)
    drawing_metadata, failed_drawings = _drawing_representation_metadata(
        db, revision.id, settings, snapshot_id
    )
    query = f"?snapshot_id={snapshot_id}" if snapshot_id is not None else ""
    result: list[dict] = []
    for item in files:
        container = db.get(Artifact, item.artifact_id) if item.artifact_id else None
        source_artifact_id = (
            str(container.derived_from_artifact_id)
            if container is not None and container.derived_from_artifact_id is not None
            else None
        )
        if item.kind == "GLB":
            result.append(
                {
                    "id": str(item.id),
                    "kind": "3D",
                    "filename": item.original_filename,
                    "media_type": item.mime_type,
                    "preview_url": f"/revisions/{revision.id}/preview/model{query}",
                    "source_artifact_id": source_artifact_id,
                    "state": "READY",
                }
            )
        elif item.kind == "PDF":
            metadata = drawing_metadata.get(item.original_filename, {})
            result.append(
                {
                    "id": str(item.id),
                    "kind": "2D",
                    "filename": item.original_filename,
                    "media_type": item.mime_type,
                    "preview_url": f"/revisions/{revision.id}/preview/drawings/{item.id}{query}",
                    "source_artifact_id": source_artifact_id,
                    "state": "READY",
                    **metadata,
                }
            )
        elif item.kind == "IMAGE" and item.mime_type == "image/png":
            if item.original_filename == THUMBNAIL_NAME:
                continue
            result.append(
                {
                    "id": str(item.id),
                    "kind": "2D",
                    "filename": item.original_filename,
                    "media_type": item.mime_type,
                    "preview_url": f"/revisions/{revision.id}/preview/images/{item.id}{query}",
                    "source_artifact_id": source_artifact_id,
                    "state": "READY",
                    "display_name": Path(item.original_filename).stem,
                }
            )
    ready_source_ids = {
        item.get("source_artifact_id")
        for item in result
        if item.get("state") == "READY" and item.get("source_artifact_id")
    }
    result.extend(
        item for item in failed_drawings if item.get("source_artifact_id") not in ready_source_ids
    )
    return result


@router.get(
    "/revisions/{revision_id}/preview/drawings/{artifact_file_id}",
    response_class=FileResponse,
)
def revision_preview_drawing_file(
    revision_id: uuid.UUID,
    artifact_file_id: uuid.UUID,
    db: Db,
    _: Actor,
    settings: Config,
    snapshot_id: uuid.UUID | None = None,
) -> FileResponse:
    artifact = next(
        (
            item
            for item in preview_files(db, revision_id, snapshot_id)
            if item.id == artifact_file_id and item.kind == "PDF"
        ),
        None,
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="Drawing representation not found")
    path = LocalStorageProvider(settings.storage_root).path_for(artifact.storage_key)
    return FileResponse(path, media_type="application/pdf", filename=artifact.original_filename)


@router.get(
    "/revisions/{revision_id}/preview/images/{artifact_file_id}",
    response_class=FileResponse,
)
def revision_preview_image_file(
    revision_id: uuid.UUID,
    artifact_file_id: uuid.UUID,
    db: Db,
    _: Actor,
    settings: Config,
    snapshot_id: uuid.UUID | None = None,
) -> FileResponse:
    artifact = next(
        (
            item
            for item in preview_files(db, revision_id, snapshot_id)
            if item.id == artifact_file_id and item.kind == "IMAGE"
        ),
        None,
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="Image representation not found")
    path = LocalStorageProvider(settings.storage_root).path_for(artifact.storage_key)
    return FileResponse(path, media_type=artifact.mime_type, filename=artifact.original_filename)
