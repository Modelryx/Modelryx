import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modelryx.api.assemblies import can_read_revision
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import (
    Artifact,
    ArtifactFile,
    AuditLog,
    ContentBlob,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    User,
)
from modelryx.services.storage import LocalStorageProvider

router = APIRouter(prefix="/api/v1", tags=["artifacts"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


class ArtifactFileView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    reference_name: str | None
    original_filename: str
    mime_type: str
    byte_size: int
    sha256: str
    role: str | None
    state: str
    download_url: str


class ArtifactView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    artifact_key: uuid.UUID
    generation: int
    artifact_type: str
    name: str
    description: str | None
    classification: str
    source: str
    derived_from_artifact_id: uuid.UUID | None
    generator: str | None
    generator_version: str | None
    state: str
    created_by: uuid.UUID
    created_at: datetime
    files: list[ArtifactFileView]


class RevisionArtifactView(BaseModel):
    relation_id: uuid.UUID
    relation_type: str
    sequence: int
    is_primary: bool
    artifact: ArtifactView


class RevisionContentSnapshotView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence: int
    commit_id: uuid.UUID
    comment: str | None
    created_by: uuid.UUID
    created_at: datetime
    artifact_count: int


class ArtifactHistoryEntry(BaseModel):
    id: uuid.UUID
    action: str
    changed_by: str
    details: dict | None
    occurred_at: datetime


def _files(db: Session, artifact_id: uuid.UUID) -> list[ArtifactFileView]:
    rows = db.scalars(
        select(ArtifactFile)
        .where(ArtifactFile.artifact_id == artifact_id)
        .order_by(ArtifactFile.reference_name, ArtifactFile.original_filename, ArtifactFile.id)
    )
    return [
        ArtifactFileView(
            **{
                field: getattr(row, field)
                for field in ArtifactFileView.model_fields
                if field != "download_url"
            },
            download_url=f"/api/v1/artifacts/{artifact_id}/files/{row.id}/download",
        )
        for row in rows
    ]


def _artifact_view(db: Session, artifact: Artifact) -> ArtifactView:
    return ArtifactView(
        **{
            column: getattr(artifact, column)
            for column in ArtifactView.model_fields
            if column != "files"
        },
        files=_files(db, artifact.id),
    )


def _readable_revision(db: Session, revision_id: uuid.UUID, principal: Principal) -> Revision:
    revision = db.get(Revision, revision_id)
    if revision is None or not can_read_revision(db, revision, principal):
        raise HTTPException(status_code=404, detail="Item revision not found")
    return revision


def _readable_artifact(db: Session, artifact_id: uuid.UUID, principal: Principal) -> Artifact:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    revision_ids = db.scalars(
        select(RevisionArtifactRelation.revision_id).where(
            RevisionArtifactRelation.artifact_id == artifact.id
        )
    )
    if not any(
        (revision := db.get(Revision, revision_id)) is not None
        and can_read_revision(db, revision, principal)
        for revision_id in revision_ids
    ):
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


@router.get("/item-revisions/{revision_id}/artifacts", response_model=list[RevisionArtifactView])
def list_revision_artifacts(
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
    snapshot_id: uuid.UUID | None = None,
):
    _readable_revision(db, revision_id, principal)
    snapshot_query = select(RevisionContentSnapshot).where(
        RevisionContentSnapshot.revision_id == revision_id
    )
    if snapshot_id is not None:
        snapshot_query = snapshot_query.where(RevisionContentSnapshot.id == snapshot_id)
    else:
        snapshot_query = snapshot_query.order_by(RevisionContentSnapshot.sequence.desc())
    snapshot = db.scalar(snapshot_query)
    if snapshot is None:
        if snapshot_id is not None:
            raise HTTPException(status_code=404, detail="Content snapshot not found for revision")
        return []
    rows = db.execute(
        select(RevisionArtifactRelation, Artifact)
        .join(
            RevisionContentSnapshotArtifact,
            RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id,
        )
        .join(Artifact, Artifact.id == RevisionArtifactRelation.artifact_id)
        .where(
            RevisionArtifactRelation.revision_id == revision_id,
            RevisionContentSnapshotArtifact.snapshot_id == snapshot.id,
        )
        .order_by(
            RevisionArtifactRelation.sequence,
            RevisionArtifactRelation.relation_type,
            Artifact.name,
        )
    ).all()
    return [
        RevisionArtifactView(
            relation_id=relation.id,
            relation_type=relation.relation_type,
            sequence=relation.sequence,
            is_primary=relation.is_primary,
            artifact=_artifact_view(db, artifact),
        )
        for relation, artifact in rows
    ]


@router.get(
    "/item-revisions/{revision_id}/content-snapshots",
    response_model=list[RevisionContentSnapshotView],
)
def list_revision_content_snapshots(revision_id: uuid.UUID, db: Db, principal: Actor):
    _readable_revision(db, revision_id, principal)
    snapshots = list(
        db.scalars(
            select(RevisionContentSnapshot)
            .where(RevisionContentSnapshot.revision_id == revision_id)
            .order_by(RevisionContentSnapshot.sequence.desc())
        )
    )
    counts = (
        dict(
            db.execute(
                select(
                    RevisionContentSnapshotArtifact.snapshot_id,
                    func.count(),
                )
                .where(
                    RevisionContentSnapshotArtifact.snapshot_id.in_([row.id for row in snapshots])
                )
                .group_by(RevisionContentSnapshotArtifact.snapshot_id)
            ).all()
        )
        if snapshots
        else {}
    )
    return [
        RevisionContentSnapshotView(
            **{
                field: getattr(snapshot, field)
                for field in ("id", "sequence", "commit_id", "comment", "created_by", "created_at")
            },
            artifact_count=counts.get(snapshot.id, 0),
        )
        for snapshot in snapshots
    ]


@router.get("/artifacts/{artifact_id}", response_model=ArtifactView)
def read_artifact(artifact_id: uuid.UUID, db: Db, principal: Actor):
    return _artifact_view(db, _readable_artifact(db, artifact_id, principal))


@router.get("/artifacts/{artifact_id}/files", response_model=list[ArtifactFileView])
def list_artifact_files(artifact_id: uuid.UUID, db: Db, principal: Actor):
    artifact = _readable_artifact(db, artifact_id, principal)
    return _files(db, artifact.id)


@router.get(
    "/artifacts/{artifact_id}/history",
    response_model=list[ArtifactHistoryEntry],
)
def read_artifact_history(
    artifact_id: uuid.UUID,
    db: Db,
    principal: Actor,
) -> list[ArtifactHistoryEntry]:
    artifact = _readable_artifact(db, artifact_id, principal)
    file_ids = select(ArtifactFile.id).where(ArtifactFile.artifact_id == artifact.id)
    rows = db.execute(
        select(AuditLog, User.display_name)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where(
            (AuditLog.entity_type == "artifact") & (AuditLog.entity_id == artifact.id)
            | (AuditLog.entity_type == "artifact_file") & (AuditLog.entity_id.in_(file_ids))
        )
        .order_by(AuditLog.occurred_at.desc())
    ).all()
    return [
        ArtifactHistoryEntry(
            id=audit.id,
            action=audit.action,
            changed_by=display_name or "System",
            details=audit.details_json,
            occurred_at=audit.occurred_at,
        )
        for audit, display_name in rows
    ]


@router.get(
    "/artifacts/{artifact_id}/files/{file_id}/download",
    response_class=FileResponse,
)
def download_artifact_file(
    artifact_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> FileResponse:
    artifact = _readable_artifact(db, artifact_id, principal)
    artifact_file = db.get(ArtifactFile, file_id)
    if (
        artifact_file is None
        or artifact_file.artifact_id != artifact.id
        or artifact_file.state != "VERIFIED"
    ):
        raise HTTPException(status_code=404, detail="ArtifactFile not found")
    storage = LocalStorageProvider(settings.storage_root)
    blob = (
        db.get(ContentBlob, artifact_file.content_blob_id)
        if artifact_file.content_blob_id is not None
        else None
    )
    path = storage.path_for(blob.storage_key if blob is not None else artifact_file.storage_key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Stored object not found")
    user = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=user.id,
        action="artifact_file.downloaded",
        entity_type="artifact_file",
        entity_id=artifact_file.id,
        request_id=request.state.request_id,
        details={"artifact_id": str(artifact.id)},
    )
    db.commit()
    return FileResponse(
        path,
        media_type=artifact_file.mime_type,
        filename=artifact_file.original_filename,
    )
