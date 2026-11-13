import hashlib
import json
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.api.assemblies import can_read_revision
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import (
    ArtifactFile,
    BomLine,
    BomViewRevision,
    DownloadBundle,
    Part,
    ProcessingJob,
    Revision,
    RevisionContentSnapshot,
)
from modelryx.services.artifact_content import (
    artifact_files_for_snapshot,
    latest_content_snapshot_id,
)
from modelryx.services.processing import (
    sha256_file,
    store_output,
    update_stage,
)
from modelryx.services.storage import LocalStorageProvider

router = APIRouter(prefix="/api/v1", tags=["downloads"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


class DownloadBundleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    revision_id: uuid.UUID
    snapshot_id: uuid.UUID | None
    processing_job_id: uuid.UUID
    artifact_id: uuid.UUID | None
    state: str
    workspace_state: str
    created_at: datetime
    completed_at: datetime | None
    error_detail: str | None


class DownloadBundleWorkspaceUpdate(BaseModel):
    action: str


def child_revisions(
    db: Session, parent_revision_id: uuid.UUID, snapshot_id: uuid.UUID | None = None
) -> list[uuid.UUID]:
    snapshot_id = snapshot_id or latest_content_snapshot_id(db, parent_revision_id)
    if snapshot_id is None:
        return []
    return list(
        db.scalars(
            select(BomLine.child_revision_id)
            .join(BomViewRevision, BomViewRevision.id == BomLine.bom_view_revision_id)
            .where(
                BomViewRevision.snapshot_id == snapshot_id,
                BomLine.parent_revision_id == parent_revision_id,
            )
            .order_by(BomLine.sequence, BomLine.occurrence_key)
        )
    )


def collect_revisions(
    db: Session, root_id: uuid.UUID, root_snapshot_id: uuid.UUID | None = None
) -> list[Revision]:
    result: list[Revision] = []
    visited: set[uuid.UUID] = set()

    def visit(revision_id: uuid.UUID) -> None:
        if revision_id in visited:
            return
        visited.add(revision_id)
        revision = db.get(Revision, revision_id)
        if revision is None:
            raise RuntimeError("assembly bundle references a missing Item Revision")
        result.append(revision)
        for child_id in child_revisions(
            db, revision_id, root_snapshot_id if revision_id == root_id else None
        ):
            visit(child_id)

    visit(root_id)
    return result


def collect_revision_snapshots(
    db: Session, root_id: uuid.UUID, root_snapshot_id: uuid.UUID
) -> dict[uuid.UUID, uuid.UUID]:
    """Resolve every child content version once, before queueing a bundle job."""
    selected = {root_id: root_snapshot_id}

    def visit(revision_id: uuid.UUID) -> None:
        for child_id in child_revisions(db, revision_id, selected[revision_id]):
            if child_id in selected:
                continue
            if db.get(Revision, child_id) is None:
                raise RuntimeError("assembly bundle references a missing Item Revision")
            snapshot_id = latest_content_snapshot_id(db, child_id)
            if snapshot_id is None:
                raise RuntimeError(
                    "assembly bundle references a revision without published content"
                )
            selected[child_id] = snapshot_id
            visit(child_id)

    visit(root_id)
    return selected


def _cad_source_records(
    db: Session, root_id: uuid.UUID, root_snapshot_id: uuid.UUID,
    revision_snapshots: dict[uuid.UUID, uuid.UUID] | None = None,
) -> list[dict[str, str | int]]:
    sources: list[dict[str, str | int]] = []
    selected = revision_snapshots or collect_revision_snapshots(db, root_id, root_snapshot_id)
    for revision_id, snapshot_id in selected.items():
        files = artifact_files_for_snapshot(
            db, revision_id, snapshot_id, artifact_types={"CAD_MODEL", "CAD_ASSEMBLY"}
        )
        for artifact in files:
            if artifact.kind != "NATIVE":
                continue
            sources.append({
                "revisionId": str(revision_id),
                "snapshotId": str(snapshot_id),
                "path": original_bundle_path(artifact) or artifact.original_filename,
                "sha256": artifact.sha256,
                "byteSize": artifact.byte_size,
            })
    return sources


def cad_source_signature(
    db: Session, root_id: uuid.UUID, root_snapshot_id: uuid.UUID | None = None,
    revision_snapshots: dict[uuid.UUID, uuid.UUID] | None = None,
) -> str:
    root_snapshot_id = root_snapshot_id or latest_content_snapshot_id(db, root_id)
    if root_snapshot_id is None:
        raise RuntimeError("revision has no published CAD content")
    sources = _cad_source_records(db, root_id, root_snapshot_id, revision_snapshots)
    encoded = json.dumps(
        {"snapshot_id": str(root_snapshot_id), "sources": sources},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def safe_bundle_path(part: Part, revision: Revision, artifact: ArtifactFile, root: bool) -> str:
    filename = PurePosixPath(artifact.original_filename).name
    if root:
        return filename
    part_number = "".join(
        char if char.isalnum() or char in "-_" else "_" for char in part.part_number
    )
    return f"components/{part_number}/rev-{revision.revision_code}/{revision.id}/{filename}"


def original_bundle_path(artifact: ArtifactFile) -> str | None:
    """Return a safe stored assembly-relative path, excluding legacy role labels."""
    candidate = (artifact.reference_name or "").replace("\\", "/").strip("/")
    if not candidate or candidate.upper() in {"MODEL", "PRIMARY", "SECONDARY", "AUXILIARY"}:
        return None
    path = PurePosixPath(candidate)
    if path.is_absolute() or ".." in path.parts or any(not part for part in path.parts):
        return None
    return str(path)


def unique_bundle_path(path: str, used: set[str]) -> str:
    key = path.casefold()
    if key not in used:
        used.add(key)
        return path
    value = PurePosixPath(path)
    for index in range(2, 10_000):
        candidate = str(value.with_name(f"{value.stem}-{index}{value.suffix}"))
        if candidate.casefold() not in used:
            used.add(candidate.casefold())
            return candidate
    raise RuntimeError("assembly bundle contains too many duplicate file paths")


def zip_write_bytes(package: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    package.writestr(info, content)


def process_bundle_job(db: Session, job: ProcessingJob, settings: Settings) -> None:
    bundle = db.scalar(select(DownloadBundle).where(DownloadBundle.processing_job_id == job.id))
    if bundle is None:
        raise RuntimeError("download bundle record is missing")
    bundle.state = "RUNNING"
    update_stage(db, job, "COLLECTING", 15)
    if bundle.snapshot_id is None:
        raise RuntimeError("download bundle source snapshot is unavailable")
    if bundle.revision_snapshots_json:
        selected = {
            uuid.UUID(revision_id): uuid.UUID(snapshot_id)
            for revision_id, snapshot_id in bundle.revision_snapshots_json.items()
        }
    else:
        # Resolve child selection for tasks that have not pinned it yet.
        selected = collect_revision_snapshots(db, bundle.revision_id, bundle.snapshot_id)
        bundle.revision_snapshots_json = {
            str(revision_id): str(snapshot_id)
            for revision_id, snapshot_id in selected.items()
        }
        db.flush()
    if selected.get(bundle.revision_id) != bundle.snapshot_id:
        raise RuntimeError("download bundle root version is inconsistent")
    revisions = [db.get(Revision, revision_id) for revision_id in selected]
    if any(revision is None for revision in revisions):
        raise RuntimeError("assembly bundle references a missing Item Revision")
    storage = LocalStorageProvider(settings.storage_root)
    entries: list[tuple[str, ArtifactFile, Part, Revision]] = []
    used_paths: set[str] = set()
    root_hashes: set[str] = set()
    for index, revision in enumerate(revisions):
        part = db.get(Part, revision.part_id)
        current_files = artifact_files_for_snapshot(
            db, revision.id, selected[revision.id], artifact_types={"CAD_MODEL", "CAD_ASSEMBLY"}
        )
        native_files = [file for file in current_files if file.kind == "NATIVE"]
        if not native_files:
            raise RuntimeError(f"verified source is unavailable for Part {part.part_number}")
        native_files.sort(
            key=lambda file: (file.role != "PRIMARY", file.reference_name or "", file.id)
        )
        for artifact in native_files:
            if not storage.exists(artifact.storage_key):
                raise RuntimeError(f"verified source is unavailable for Part {part.part_number}")
            original_path = original_bundle_path(artifact)
            if index > 0 and artifact.sha256 in root_hashes:
                continue
            if index == 0:
                root_hashes.add(artifact.sha256)
            path = original_path or safe_bundle_path(part, revision, artifact, index == 0)
            entries.append(
                (unique_bundle_path(path, used_paths), artifact, part, revision)
            )
    entries.sort(key=lambda item: item[0])
    update_stage(db, job, "PACKAGING", 55)
    manifest_files = [
        {
            "path": path,
            "itemId": str(part.id),
            "partNumber": part.part_number,
            "revisionId": str(revision.id),
            "snapshotId": str(selected[revision.id]),
            "role": artifact.role or "SECONDARY",
            "byteSize": artifact.byte_size,
            "sha256": artifact.sha256,
        }
        for path, artifact, part, revision in entries
    ]
    root_entry = next(
        (
            (path, artifact, part, revision)
            for path, artifact, part, revision in entries
            if revision.id == bundle.revision_id and artifact.role == "PRIMARY"
        ),
        next(
            (entry for entry in entries if entry[3].id == bundle.revision_id),
            None,
        ),
    )
    if root_entry is None:
        raise RuntimeError("download bundle has no root CAD file")
    root_path, _, root_part, root_revision = root_entry
    manifest = {
        "schemaVersion": 2,
        "revisionId": str(bundle.revision_id),
        "snapshotId": str(bundle.snapshot_id),
        "revisionSnapshots": {
            str(revision_id): str(snapshot_id) for revision_id, snapshot_id in selected.items()
        },
        "root": {
            "path": root_path,
            "itemId": str(root_part.id),
            "partNumber": root_part.part_number,
            "revisionId": str(root_revision.id),
        },
        "files": manifest_files,
    }
    with TemporaryDirectory(prefix=f"modelryx-bundle-{bundle.id}-") as directory:
        bundle_path = Path(directory) / "assembly-source.zip"
        with zipfile.ZipFile(bundle_path, "w") as package:
            zip_write_bytes(
                package,
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True).encode(),
            )
            for path, artifact, _, _ in entries:
                zip_write_bytes(package, path, storage.path_for(artifact.storage_key).read_bytes())
        update_stage(db, job, "PUBLISHING", 85)
        revision = db.get(Revision, bundle.revision_id)
        root_snapshot = db.get(RevisionContentSnapshot, bundle.snapshot_id)
        if root_snapshot is None:
            raise RuntimeError("download bundle source snapshot is unavailable")
        output = store_output(
            db,
            storage,
            revision,
            root_snapshot,
            kind="BUNDLE",
            filename="assembly-source.zip",
            mime_type="application/zip",
            source=bundle_path,
            generator_version=settings.converter_version,
        )
        # A download is read-only revision activity. Keep the generated archive as
        # the task artifact, but never publish it into a new revision snapshot.
        # Publishing here can change the authoritative file set simply by downloading it.
        db.flush()
        bundle.artifact_id = output.id
        bundle.state = "READY"
        bundle.completed_at = datetime.now(UTC)
        job.state = "SUCCEEDED"
        job.stage = "COMPLETED"
        job.progress_percent = 100
        job.finished_at = datetime.now(UTC)
        job.diagnostics_json = {
            "warnings": [],
            "statistics": {"files": len(entries), "bytes": output.byte_size},
        }
        record_audit(
            db,
            actor_id=bundle.created_by,
            action="download_bundle.created",
            entity_type="download_bundle",
            entity_id=bundle.id,
            request_id=f"job:{job.id}",
            details={"sha256": sha256_file(bundle_path), "files": len(entries)},
        )
        db.commit()


@router.post(
    "/revisions/{revision_id}/download-bundles",
    response_model=DownloadBundleResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_download_bundle(
    revision_id: uuid.UUID, request: Request, db: Db, principal: Actor,
    snapshot_id: uuid.UUID | None = None,
) -> DownloadBundle:
    revision = db.get(Revision, revision_id)
    if revision is None or not can_read_revision(db, revision, principal):
        raise HTTPException(status_code=404, detail="Revision not found")
    if snapshot_id is None:
        snapshot_id = latest_content_snapshot_id(db, revision.id)
    if snapshot_id is None:
        raise HTTPException(status_code=409, detail="Revision has no published CAD content")
    snapshot = db.get(RevisionContentSnapshot, snapshot_id)
    if snapshot is None or snapshot.revision_id != revision.id:
        raise HTTPException(status_code=404, detail="Revision version not found")
    try:
        selected = collect_revision_snapshots(db, revision.id, snapshot_id)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    for component_revision_id in selected:
        component_revision = db.get(Revision, component_revision_id)
        if not can_read_revision(db, component_revision, principal):
            raise HTTPException(status_code=403, detail="A referenced component is not authorized")
    user = user_for_principal(db, principal)
    source_signature = cad_source_signature(db, revision.id, snapshot_id, selected)
    existing = db.scalar(
        select(DownloadBundle)
        .where(
            DownloadBundle.revision_id == revision.id,
            DownloadBundle.snapshot_id == snapshot_id,
            DownloadBundle.source_signature == source_signature,
            DownloadBundle.created_by == user.id,
            DownloadBundle.workspace_state != "REMOVED",
            DownloadBundle.state.in_(["PENDING", "RUNNING", "READY"]),
        )
        .order_by(DownloadBundle.created_at.desc())
    )
    if existing is not None:
        if existing.workspace_state == "ARCHIVED":
            existing.workspace_state = "ACTIVE"
            db.commit()
            db.refresh(existing)
        return existing
    job = ProcessingJob(
        revision_id=revision.id,
        snapshot_id=snapshot_id,
        type="ASSEMBLY_SOURCE_BUNDLE",
        idempotency_key=f"assembly-bundle:{uuid.uuid4()}",
    )
    db.add(job)
    db.flush()
    bundle = DownloadBundle(
        revision_id=revision.id,
        snapshot_id=snapshot_id,
        processing_job_id=job.id,
        source_signature=source_signature,
        revision_snapshots_json={
            str(revision_id): str(selected_snapshot)
            for revision_id, selected_snapshot in selected.items()
        },
        created_by=user.id,
    )
    db.add(bundle)
    db.flush()
    record_audit(
        db,
        actor_id=user.id,
        action="download_bundle.requested",
        entity_type="download_bundle",
        entity_id=bundle.id,
        request_id=request.state.request_id,
    )
    db.commit()
    db.refresh(bundle)
    return bundle


@router.post(
    "/download-bundles/{bundle_id}/retry",
    response_model=DownloadBundleResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_download_bundle(
    bundle_id: uuid.UUID, request: Request, db: Db, principal: Actor
) -> DownloadBundle:
    user = user_for_principal(db, principal)
    bundle = db.scalar(select(DownloadBundle).where(
        DownloadBundle.id == bundle_id,
        DownloadBundle.created_by == user.id,
    ))
    if bundle is None:
        raise HTTPException(status_code=404, detail="Download task not found")
    if bundle.state != "FAILED":
        raise HTTPException(status_code=409, detail="Only failed download tasks can be retried")
    revision = db.get(Revision, bundle.revision_id)
    if revision is None or not can_read_revision(db, revision, principal):
        raise HTTPException(status_code=404, detail="Revision not found")
    job = ProcessingJob(
        revision_id=bundle.revision_id,
        snapshot_id=bundle.snapshot_id,
        type="ASSEMBLY_SOURCE_BUNDLE",
        idempotency_key=f"assembly-bundle-retry:{bundle.id}:{uuid.uuid4()}",
    )
    db.add(job)
    db.flush()
    bundle.processing_job_id = job.id
    bundle.state = "PENDING"
    bundle.workspace_state = "ACTIVE"
    bundle.artifact_id = None
    bundle.completed_at = None
    bundle.error_detail = None
    record_audit(
        db,
        actor_id=user.id,
        action="download_bundle.retry_requested",
        entity_type="download_bundle",
        entity_id=bundle.id,
        request_id=request.state.request_id,
        details={"processing_job_id": str(job.id)},
    )
    db.commit()
    db.refresh(bundle)
    return bundle


@router.get("/download-bundles/{bundle_id}", response_model=DownloadBundleResponse)
def read_download_bundle(bundle_id: uuid.UUID, db: Db, principal: Actor) -> DownloadBundle:
    bundle = db.get(DownloadBundle, bundle_id)
    if bundle is None or bundle.created_by != user_for_principal(db, principal).id:
        raise HTTPException(status_code=404, detail="Download bundle not found")
    return bundle


@router.patch("/download-bundles/{bundle_id}/workspace", status_code=status.HTTP_204_NO_CONTENT)
def update_download_bundle_workspace(
    bundle_id: uuid.UUID,
    payload: DownloadBundleWorkspaceUpdate,
    request: Request,
    db: Db,
    principal: Actor,
) -> None:
    user = user_for_principal(db, principal)
    bundle = db.scalar(select(DownloadBundle).where(
        DownloadBundle.id == bundle_id,
        DownloadBundle.created_by == user.id,
    ))
    if bundle is None:
        raise HTTPException(status_code=404, detail="Download task not found")
    states = {"ARCHIVE": "ARCHIVED", "RESTORE": "ACTIVE", "REMOVE": "REMOVED"}
    action = payload.action.upper()
    if action not in states:
        raise HTTPException(status_code=422, detail="Unsupported workspace action")
    bundle.workspace_state = states[action]
    record_audit(
        db,
        actor_id=user.id,
        action=f"download_bundle.workspace_{action.casefold()}",
        entity_type="download_bundle",
        entity_id=bundle.id,
        request_id=request.state.request_id,
    )
    db.commit()


@router.get("/download-bundles/{bundle_id}/download", response_class=FileResponse)
def download_bundle(
    bundle_id: uuid.UUID, request: Request, db: Db, principal: Actor, settings: Config
) -> FileResponse:
    bundle = db.get(DownloadBundle, bundle_id)
    if bundle is None or bundle.state != "READY" or bundle.artifact_id is None:
        raise HTTPException(status_code=409, detail="Download bundle is not ready")
    revision = db.get(Revision, bundle.revision_id)
    if not can_read_revision(db, revision, principal):
        raise HTTPException(status_code=404, detail="Download bundle not found")
    artifact = db.get(ArtifactFile, bundle.artifact_id)
    path = LocalStorageProvider(settings.storage_root).path_for(artifact.storage_key)
    if not path.is_file():
        raise HTTPException(status_code=409, detail="Download bundle file is unavailable")
    user = user_for_principal(db, principal)
    if bundle.created_by != user.id:
        raise HTTPException(status_code=404, detail="Download bundle not found")
    record_audit(
        db,
        actor_id=user.id,
        action="download_bundle.downloaded",
        entity_type="download_bundle",
        entity_id=bundle.id,
        request_id=request.state.request_id,
        details={"artifact_id": str(artifact.id)},
    )
    db.commit()
    part = db.get(Part, revision.part_id)
    filename = f"{part.part_number}-{revision.revision_code}-cad-files.zip"
    return FileResponse(path, media_type="application/zip", filename=filename)
