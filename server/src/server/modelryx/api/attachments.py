import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.api.items import readable_item_revision
from modelryx.api.parts import require_engineer
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import (
    AdditionalFileStage,
    Artifact,
    ArtifactFile,
    Attachment,
    AttachmentCheckout,
    ContentBlob,
    Item,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    StagingSession,
)
from modelryx.models.schemas import (
    AdditionalFileDuplicateCandidate,
    AdditionalFilePreflightRequest,
    AdditionalFilePreflightResponse,
    AdditionalFileRemoveStageCreate,
    AdditionalFileStageCommit,
    AdditionalFileStageCreate,
    AdditionalFileStageResponse,
    AttachmentCheckoutResponse,
    AttachmentCreate,
    AttachmentResponse,
    AttachmentUpdate,
    AttachmentVersionPlan,
    AttachmentVersionResponse,
)
from modelryx.services.storage import LocalStorageProvider

router = APIRouter(prefix="/api/v1", tags=["attachments"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def require_attachment_editor(
    db: Session, item: Item, principal: Principal, user_id: uuid.UUID
) -> None:
    require_engineer(principal)
    if item.status != "ACTIVE":
        raise HTTPException(
            status_code=409, detail="Attachments cannot be changed on an archived Item"
        )


def latest_attachment_version(db: Session, attachment_id: uuid.UUID) -> Artifact | None:
    return db.scalar(
        select(Artifact)
        .where(Artifact.attachment_id == attachment_id)
        .order_by(Artifact.generation.desc(), Artifact.id.desc())
        .limit(1)
    )


def active_attachment_checkout(
    db: Session, item_id: uuid.UUID, attachment_id: uuid.UUID | None
) -> AttachmentCheckout | None:
    statement = select(AttachmentCheckout).where(
        AttachmentCheckout.item_id == item_id,
        AttachmentCheckout.attachment_id == attachment_id,
    )
    checkout = db.scalar(statement)
    if checkout is not None and checkout.expires_at <= datetime.now(UTC):
        db.delete(checkout)
        db.flush()
        return None
    return checkout


def ensure_attachment_checkout(
    db: Session,
    item: Item,
    attachment: Attachment | None,
    principal: Principal,
    user_id: uuid.UUID,
    settings: Settings,
    revision_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> AttachmentCheckout:
    require_attachment_editor(db, item, principal, user_id)
    attachment_id = attachment.id if attachment else None
    existing = active_attachment_checkout(db, item.id, attachment_id)
    base = latest_attachment_version(db, attachment_id) if attachment_id else None
    if existing is not None:
        if existing.owner_id != user_id:
            raise HTTPException(status_code=409, detail="Attachment is checked out by another user")
        if existing.base_version_id != (base.id if base else None):
            raise HTTPException(
                status_code=409, detail="Attachment changed after checkout; refresh"
            )
        if revision_id is not None:
            existing.revision_id = revision_id
        if task_id is not None:
            existing.task_id = task_id
        return existing
    checkout = AttachmentCheckout(
        item_id=item.id,
        attachment_id=attachment_id,
        base_version_id=base.id if base else None,
        revision_id=revision_id,
        task_id=task_id,
        owner_id=user_id,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.checkout_expiry_seconds),
    )
    db.add(checkout)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Attachment was checked out concurrently"
        ) from exc
    return checkout


def active_attachment_edit_task(
    db: Session, item_id: uuid.UUID, revision_id: uuid.UUID | None, user_id: uuid.UUID
) -> tuple[AttachmentCheckout | None, StagingSession | None]:
    checkout = active_attachment_checkout(db, item_id, None)
    if checkout is None or checkout.owner_id != user_id or checkout.revision_id != revision_id:
        return checkout, None
    task = db.get(StagingSession, checkout.task_id) if checkout.task_id else None
    return checkout, task


def complete_attachment_edit_task(
    db: Session,
    item_id: uuid.UUID,
    revision_id: uuid.UUID | None,
    user_id: uuid.UUID,
    cancelled: bool = False,
) -> StagingSession:
    checkout, task = active_attachment_edit_task(db, item_id, revision_id, user_id)
    if checkout is None or task is None:
        raise HTTPException(status_code=409, detail="No active attachment edit task")
    pending = db.scalar(
        select(AdditionalFileStage.id).where(
            AdditionalFileStage.item_id == item_id,
            AdditionalFileStage.revision_id == revision_id,
            AdditionalFileStage.created_by == user_id,
            AdditionalFileStage.status.in_(["DRAFT", "READY"]),
        )
    )
    if pending is not None:
        raise HTTPException(status_code=409, detail="Commit or cancel staged attachments first")
    # Stages created by older clients may predate the explicit task association.
    # Bind only completed changes made during this task's lifetime and scope.
    for completed_stage in db.scalars(
        select(AdditionalFileStage).where(
            AdditionalFileStage.item_id == item_id,
            AdditionalFileStage.revision_id == revision_id,
            AdditionalFileStage.created_by == user_id,
            AdditionalFileStage.task_id.is_(None),
            AdditionalFileStage.status == "COMPLETED",
            AdditionalFileStage.created_at >= task.created_at,
        )
    ):
        completed_stage.task_id = task.id
    for owned in list(
        db.scalars(
            select(AttachmentCheckout).where(
                AttachmentCheckout.item_id == item_id,
                AttachmentCheckout.owner_id == user_id,
                AttachmentCheckout.revision_id == revision_id,
            )
        )
    ):
        db.delete(owned)
    task.status = "CANCELLED" if cancelled else "COMPLETED"
    result = dict(task.result_json or {})
    if cancelled:
        result["workspace"] = {"state": "REMOVED"}
        result["workflow"] = {"state": "CANCELLED", "stage": "CANCELLED"}
    else:
        result["workflow"] = {
            "state": "COMPLETED",
            "stage": "COMPLETED",
            "progress_percent": 100,
        }
    task.result_json = result
    return task


def require_owned_attachment_checkout(
    db: Session, attachment: Attachment, user_id: uuid.UUID
) -> AttachmentCheckout:
    checkout = active_attachment_checkout(db, attachment.item_id, attachment.id)
    if checkout is None or checkout.owner_id != user_id:
        raise HTTPException(status_code=409, detail="Attachment checkout is required")
    return checkout


def version_response(version: Artifact, artifact: ArtifactFile) -> AttachmentVersionResponse:
    return AttachmentVersionResponse(
        id=version.id,
        attachment_id=version.attachment_id,
        client_version_id=version.client_version_id,
        version_number=version.generation,
        state=version.state,
        filename=artifact.original_filename,
        mime_type=artifact.mime_type,
        byte_size=artifact.byte_size,
        sha256=artifact.sha256,
        comment=version.comment,
        scope="REVISION" if artifact.revision_id is not None else "ITEM",
        revision_id=artifact.revision_id,
        created_by=version.created_by,
        created_at=version.created_at,
        expires_at=version.expires_at,
        ready_at=version.ready_at,
        upload_url=f"/api/v1/attachment-versions/{version.id}/content"
        if version.state == "UPLOADING"
        else None,
        download_url=f"/api/v1/attachment-versions/{version.id}/download"
        if version.state == "READY"
        else None,
    )


def attachment_response(
    db: Session,
    attachment: Attachment,
    revision_id: uuid.UUID | None = None,
    artifact_ids: set[uuid.UUID] | None = None,
    user_id: uuid.UUID | None = None,
) -> AttachmentResponse:
    statement = (
        select(Artifact, ArtifactFile)
        .join(ArtifactFile, ArtifactFile.artifact_id == Artifact.id)
        .where(Artifact.attachment_id == attachment.id)
        .order_by(Artifact.generation)
    )
    if artifact_ids is not None:
        statement = statement.where(Artifact.id.in_(artifact_ids))
    elif revision_id is not None:
        statement = statement.join(
            RevisionArtifactRelation,
            RevisionArtifactRelation.artifact_id == Artifact.id,
        ).where(
            RevisionArtifactRelation.revision_id == revision_id,
            RevisionArtifactRelation.relation_type == "ATTACHMENT",
        )
    rows = db.execute(statement).all()
    versions = [version_response(version, artifact) for version, artifact in rows]
    checkout = active_attachment_checkout(db, attachment.item_id, attachment.id)
    checkout_status = (
        "AVAILABLE"
        if checkout is None
        else "CHECKED_OUT_BY_ME"
        if checkout.owner_id == user_id
        else "CHECKED_OUT_BY_OTHER"
    )
    return AttachmentResponse(
        id=attachment.id,
        item_id=attachment.item_id,
        name=attachment.name,
        attachment_type=attachment.attachment_type,
        description=attachment.description,
        lifecycle_state=attachment.lifecycle_state,
        created_by=attachment.created_by,
        created_at=attachment.created_at,
        latest_ready_version=next(
            (version for version in reversed(versions) if version.state == "READY"), None
        ),
        versions=versions,
        checkout_status=checkout_status,
    )


def record_revision_attachment_audit(
    db: Session,
    request: Request,
    user_id: uuid.UUID,
    version: Artifact,
    artifact: ArtifactFile,
) -> None:
    if artifact.revision_id is None:
        attachment = db.get(Attachment, version.attachment_id)
        record_audit(
            db,
            actor_id=user_id,
            action=("attachment.added" if version.generation == 1 else "attachment.replaced"),
            entity_type="item",
            entity_id=attachment.item_id,
            request_id=request.state.request_id,
            details={
                "attachment_id": str(version.attachment_id),
                "filename": artifact.original_filename,
                "version": version.generation,
                "sha256": artifact.sha256,
                "scope": "ITEM",
            },
        )
        return
    record_audit(
        db,
        actor_id=user_id,
        action=("attachment.added" if version.generation == 1 else "attachment.replaced"),
        entity_type="revision",
        entity_id=artifact.revision_id,
        request_id=request.state.request_id,
        details={
            "attachment_id": str(version.attachment_id),
            "filename": artifact.original_filename,
            "version": version.generation,
            "sha256": artifact.sha256,
            "scope": "REVISION",
        },
    )


def add_version(
    db: Session,
    attachment: Attachment,
    payload: AttachmentVersionPlan,
    user_id: uuid.UUID,
    upload_expiry_seconds: int,
    storage_root: Path,
) -> Artifact:
    attachment = db.scalar(
        select(Attachment).where(Attachment.id == attachment.id).with_for_update()
    )
    if attachment.lifecycle_state != "ACTIVE":
        raise HTTPException(status_code=409, detail="Archived Additional Files cannot be changed")
    item = db.get(Item, attachment.item_id)
    revision = db.get(Revision, payload.revision_id) if payload.revision_id else None
    if payload.scope == "REVISION" and (revision is None or revision.part_id != attachment.item_id):
        raise HTTPException(status_code=404, detail="Target Item Revision not found")
    if payload.client_version_id is not None:
        existing = db.scalar(
            select(Artifact).where(Artifact.client_version_id == payload.client_version_id)
        )
        if existing is not None:
            if existing.attachment_id != attachment.id:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency key belongs to another attachment",
                )
            if revision is not None and existing.state == "READY":
                existing_file = db.scalar(
                    select(ArtifactFile).where(ArtifactFile.artifact_id == existing.id)
                )
                relation = ensure_revision_attachment_relation(
                    db, existing, existing_file, user_id, revision.id
                )
                publish_revision_attachment_snapshot(db, relation, user_id)
            return existing
    unchanged = db.scalar(
        select(Artifact)
        .join(ArtifactFile, ArtifactFile.artifact_id == Artifact.id)
        .where(
            Artifact.attachment_id == attachment.id,
            Artifact.state == "READY",
            ArtifactFile.sha256 == payload.sha256.lower(),
        )
        .order_by(Artifact.generation.desc())
    )
    if unchanged is not None:
        if revision is not None:
            unchanged_file = db.scalar(
                select(ArtifactFile).where(ArtifactFile.artifact_id == unchanged.id)
            )
            relation = ensure_revision_attachment_relation(
                db, unchanged, unchanged_file, user_id, revision.id
            )
            publish_revision_attachment_snapshot(db, relation, user_id)
        return unchanged
    number = (
        db.scalar(
            select(func.max(Artifact.generation)).where(Artifact.attachment_id == attachment.id)
        )
        or 0
    ) + 1
    artifact_id = uuid.uuid4()
    version_id = uuid.uuid4()
    filename = Path(payload.filename).name
    if not filename or filename != payload.filename:
        raise HTTPException(status_code=422, detail="filename must be a plain file name")
    reusable_blob = None
    if payload.duplicate_action == "REUSE_EXISTING":
        reusable_blob = db.scalar(
            select(ContentBlob).where(
                ContentBlob.sha256 == payload.sha256.lower(),
                ContentBlob.byte_size == payload.byte_size,
                ContentBlob.state == "VERIFIED",
            )
        )
        if reusable_blob is None or not LocalStorageProvider(storage_root).exists(
            reusable_blob.storage_key
        ):
            raise HTTPException(
                status_code=409,
                detail="No verified reusable content matches this SHA-256 and size",
            )
    artifact = ArtifactFile(
        id=artifact_id,
        artifact_id=version_id,
        content_blob_id=reusable_blob.id if reusable_blob is not None else None,
        revision_id=revision.id if revision is not None else None,
        kind="ATTACHMENT",
        storage_key=f"items/{item.id}/attachments/{attachment.id}/versions/{version_id}/{filename}",
        original_filename=filename,
        mime_type=payload.mime_type,
        byte_size=payload.byte_size,
        sha256=payload.sha256.lower(),
        state="VERIFIED" if reusable_blob is not None else "PENDING",
        required=False,
    )
    previous = db.scalar(
        select(Artifact)
        .where(Artifact.attachment_id == attachment.id)
        .order_by(Artifact.generation.desc())
    )
    version = Artifact(
        id=version_id,
        artifact_key=previous.artifact_key if previous is not None else uuid.uuid4(),
        attachment_id=attachment.id,
        client_version_id=payload.client_version_id,
        generation=number,
        artifact_type="DOCUMENT",
        name=attachment.name,
        description=attachment.description,
        classification="AUTHORITATIVE",
        source="USER",
        state="READY" if reusable_blob is not None else "UPLOADING",
        comment=payload.comment,
        created_by=user_id,
        expires_at=datetime.now(UTC) + timedelta(seconds=upload_expiry_seconds),
        ready_at=datetime.now(UTC) if reusable_blob is not None else None,
    )
    db.add_all([artifact, version])
    db.flush()
    if reusable_blob is not None:
        relation = ensure_revision_attachment_relation(db, version, artifact, user_id)
        publish_revision_attachment_snapshot(db, relation, user_id)
    return version


def promote_attachment_blob(
    db: Session, artifact: ArtifactFile, storage: LocalStorageProvider
) -> ContentBlob:
    """Move verified upload bytes to their content address, reusing an existing blob."""
    # Serialize promotion of identical bytes so concurrent uploads cannot race
    # the unique (sha256, byte_size) constraint or replace one another's blob.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": f"{artifact.sha256}:{artifact.byte_size}"},
    )
    existing = db.scalar(
        select(ContentBlob).where(
            ContentBlob.sha256 == artifact.sha256,
            ContentBlob.byte_size == artifact.byte_size,
            ContentBlob.state == "VERIFIED",
        )
    )
    uploaded_path = storage.path_for(artifact.storage_key)
    if existing is not None:
        if not storage.exists(existing.storage_key):
            raise HTTPException(status_code=409, detail="Reusable content is missing from storage")
        uploaded_path.unlink(missing_ok=True)
        artifact.content_blob_id = existing.id
        return existing
    blob_key = f"blobs/{artifact.sha256[:2]}/{artifact.sha256}-{artifact.byte_size}"
    blob_path = storage.create_upload(blob_key)
    os.replace(uploaded_path, blob_path)
    blob = ContentBlob(
        sha256=artifact.sha256,
        byte_size=artifact.byte_size,
        storage_key=blob_key,
        state="VERIFIED",
    )
    db.add(blob)
    db.flush()
    artifact.content_blob_id = blob.id
    return blob


def attachment_content_path(
    db: Session, artifact: ArtifactFile, storage: LocalStorageProvider
) -> Path:
    if artifact.content_blob_id is None:
        return storage.path_for(artifact.storage_key)
    blob = db.get(ContentBlob, artifact.content_blob_id)
    if blob is None or blob.state != "VERIFIED":
        raise HTTPException(status_code=404, detail="Attachment content not found")
    return storage.path_for(blob.storage_key)


def additional_file_stage_response(stage: AdditionalFileStage) -> AdditionalFileStageResponse:
    return AdditionalFileStageResponse(
        id=stage.id,
        client_staging_id=stage.client_staging_id,
        item_id=stage.item_id,
        revision_id=stage.revision_id,
        task_id=stage.task_id,
        attachment_id=stage.attachment_id,
        base_version_id=stage.base_version_id,
        action=stage.action,
        status=stage.status,
        name=stage.name,
        attachment_type=stage.attachment_type,
        description=stage.description,
        filename=stage.filename,
        mime_type=stage.mime_type,
        byte_size=stage.byte_size,
        sha256=stage.sha256,
        comment=stage.comment,
        duplicate_action=stage.duplicate_action,
        upload_url=(
            f"/api/v1/additional-files/staging/{stage.id}/content"
            if stage.status == "DRAFT"
            else None
        ),
        commit_id=stage.commit_id,
        result=stage.result_json,
        created_at=stage.created_at,
        updated_at=stage.updated_at,
    )


def owned_additional_file_stage(
    db: Session, stage_id: uuid.UUID, principal: Principal
) -> tuple[AdditionalFileStage, object]:
    stage = db.get(AdditionalFileStage, stage_id)
    if stage is None:
        raise HTTPException(status_code=404, detail="Additional File staging session not found")
    user = user_for_principal(db, principal)
    if stage.created_by != user.id and principal.role != "administrator":
        raise HTTPException(status_code=404, detail="Additional File staging session not found")
    return stage, user


def promote_stage_blob(
    db: Session, stage: AdditionalFileStage, storage: LocalStorageProvider, source: Path
) -> ContentBlob:
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": f"{stage.sha256}:{stage.byte_size}"},
    )
    blob = db.scalar(
        select(ContentBlob).where(
            ContentBlob.sha256 == stage.sha256,
            ContentBlob.byte_size == stage.byte_size,
            ContentBlob.state == "VERIFIED",
        )
    )
    if blob is not None:
        if not storage.exists(blob.storage_key):
            raise HTTPException(status_code=409, detail="Reusable content is missing from storage")
        source.unlink(missing_ok=True)
    else:
        blob_key = f"blobs/{stage.sha256[:2]}/{stage.sha256}-{stage.byte_size}"
        os.replace(source, storage.create_upload(blob_key))
        blob = ContentBlob(
            sha256=stage.sha256,
            byte_size=stage.byte_size,
            storage_key=blob_key,
            state="VERIFIED",
        )
        db.add(blob)
        db.flush()
    stage.content_blob_id = blob.id
    stage.status = "READY"
    return blob


def ensure_revision_attachment_relation(
    db: Session,
    version: Artifact,
    artifact: ArtifactFile,
    user_id: uuid.UUID,
    revision_id: uuid.UUID | None = None,
) -> RevisionArtifactRelation | None:
    """Link a verified revision-scoped Additional File version exactly once."""
    target_revision_id = revision_id or artifact.revision_id
    if target_revision_id is None:
        return None
    existing = db.scalar(
        select(RevisionArtifactRelation).where(
            RevisionArtifactRelation.revision_id == target_revision_id,
            RevisionArtifactRelation.artifact_id == version.id,
            RevisionArtifactRelation.relation_type == "ATTACHMENT",
        )
    )
    if existing is not None:
        return existing
    sequence = (
        db.scalar(
            select(func.max(RevisionArtifactRelation.sequence)).where(
                RevisionArtifactRelation.revision_id == target_revision_id,
            )
        )
        or 0
    )
    relation = RevisionArtifactRelation(
        revision_id=target_revision_id,
        artifact_id=version.id,
        relation_type="ATTACHMENT",
        sequence=sequence + 1,
        is_primary=False,
        created_by=user_id,
    )
    db.add(relation)
    db.flush()
    return relation


def publish_revision_attachment_snapshot(
    db: Session,
    relation: RevisionArtifactRelation | None,
    user_id: uuid.UUID,
) -> RevisionContentSnapshot | None:
    """Publish a new immutable snapshot containing a newly linked Additional File."""
    if relation is None:
        return None
    latest = db.scalar(
        select(RevisionContentSnapshot)
        .where(RevisionContentSnapshot.revision_id == relation.revision_id)
        .order_by(RevisionContentSnapshot.sequence.desc())
        .limit(1)
        .with_for_update()
    )
    if latest is None:
        raise HTTPException(
            status_code=409,
            detail="Target Item Revision has no committed content snapshot",
        )
    already_published = db.scalar(
        select(RevisionContentSnapshotArtifact.snapshot_id).where(
            RevisionContentSnapshotArtifact.snapshot_id == latest.id,
            RevisionContentSnapshotArtifact.relation_id == relation.id,
        )
    )
    if already_published is not None:
        return latest
    previous_links = list(
        db.scalars(
            select(RevisionContentSnapshotArtifact)
            .where(RevisionContentSnapshotArtifact.snapshot_id == latest.id)
            .order_by(
                RevisionContentSnapshotArtifact.sequence,
                RevisionContentSnapshotArtifact.relation_id,
            )
        )
    )
    snapshot = RevisionContentSnapshot(
        revision_id=relation.revision_id,
        sequence=latest.sequence + 1,
        commit_id=uuid.uuid4(),
        comment="Additional File linked to revision",
        created_by=user_id,
    )
    db.add(snapshot)
    db.flush()
    for sequence, link in enumerate(previous_links):
        db.add(
            RevisionContentSnapshotArtifact(
                snapshot_id=snapshot.id,
                relation_id=link.relation_id,
                sequence=sequence,
            )
        )
    db.add(
        RevisionContentSnapshotArtifact(
            snapshot_id=snapshot.id,
            relation_id=relation.id,
            sequence=len(previous_links),
        )
    )
    db.flush()
    return snapshot


def publish_revision_attachment_removal(
    db: Session,
    revision_id: uuid.UUID,
    attachment_id: uuid.UUID,
    user_id: uuid.UUID,
) -> RevisionContentSnapshot:
    latest = db.scalar(
        select(RevisionContentSnapshot)
        .where(RevisionContentSnapshot.revision_id == revision_id)
        .order_by(RevisionContentSnapshot.sequence.desc())
        .limit(1)
        .with_for_update()
    )
    if latest is None:
        raise HTTPException(
            status_code=409, detail="Target Item Revision has no committed content snapshot"
        )
    links = list(
        db.scalars(
            select(RevisionContentSnapshotArtifact)
            .where(RevisionContentSnapshotArtifact.snapshot_id == latest.id)
            .order_by(RevisionContentSnapshotArtifact.sequence)
        )
    )
    retained = []
    removed = False
    for link in links:
        relation = db.get(RevisionArtifactRelation, link.relation_id)
        artifact = db.get(Artifact, relation.artifact_id) if relation else None
        if (
            relation
            and relation.relation_type == "ATTACHMENT"
            and artifact
            and artifact.attachment_id == attachment_id
        ):
            removed = True
        else:
            retained.append(link)
    if not removed:
        raise HTTPException(status_code=409, detail="Attachment is not present in this revision")
    snapshot = RevisionContentSnapshot(
        revision_id=revision_id,
        sequence=latest.sequence + 1,
        commit_id=uuid.uuid4(),
        comment="Attachment removed from revision",
        created_by=user_id,
    )
    db.add(snapshot)
    db.flush()
    for sequence, link in enumerate(retained):
        db.add(
            RevisionContentSnapshotArtifact(
                snapshot_id=snapshot.id,
                relation_id=link.relation_id,
                sequence=sequence,
            )
        )
    db.flush()
    return snapshot


@router.post(
    "/attachments/{attachment_id}/staging-remove",
    response_model=AdditionalFileStageResponse,
    status_code=status.HTTP_201_CREATED,
)
def stage_attachment_removal(
    attachment_id: uuid.UUID,
    payload: AdditionalFileRemoveStageCreate,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AdditionalFileStageResponse:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None or attachment.lifecycle_state != "ACTIVE":
        raise HTTPException(status_code=404, detail="Active attachment not found")
    item = db.get(Item, attachment.item_id)
    user = user_for_principal(db, principal)
    revision = None
    if payload.scope == "REVISION":
        revision = readable_item_revision(db, item.id, payload.revision_id, principal)
        if revision.lifecycle_state != "WORKING":
            raise HTTPException(status_code=409, detail="Released revisions are immutable")
    existing = db.scalar(
        select(AdditionalFileStage).where(
            AdditionalFileStage.attachment_id == attachment.id,
            AdditionalFileStage.revision_id == (revision.id if revision else None),
            AdditionalFileStage.status.in_(["DRAFT", "READY"]),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Attachment already has a staged change")
    version = latest_attachment_version(db, attachment.id)
    file = (
        db.scalar(select(ArtifactFile).where(ArtifactFile.artifact_id == version.id))
        if version
        else None
    )
    if version is None or file is None or version.state != "READY":
        raise HTTPException(status_code=409, detail="Attachment has no ready version")
    checkout = ensure_attachment_checkout(
        db,
        item,
        attachment,
        principal,
        user.id,
        settings,
        revision_id=revision.id if revision else None,
    )
    _task_checkout, task = active_attachment_edit_task(
        db, item.id, revision.id if revision else None, user.id
    )
    stage = AdditionalFileStage(
        client_staging_id=uuid.uuid4(),
        item_id=item.id,
        revision_id=revision.id if revision else None,
        attachment_id=attachment.id,
        task_id=task.id if task else None,
        base_version_id=checkout.base_version_id,
        content_blob_id=file.content_blob_id,
        action="REMOVE",
        duplicate_action="REUSE_EXISTING",
        status="READY",
        name=attachment.name,
        attachment_type=attachment.attachment_type,
        description=attachment.description,
        filename=file.original_filename,
        mime_type=file.mime_type,
        byte_size=file.byte_size,
        sha256=file.sha256,
        storage_key=f"staging/additional-files/{uuid.uuid4()}/remove-{file.original_filename}",
        created_by=user.id,
    )
    db.add(stage)
    record_audit(
        db,
        actor_id=user.id,
        action="additional_file.removal_staged",
        entity_type="additional_file_stage",
        entity_id=stage.id,
        request_id=request.state.request_id,
        details={"item_id": str(item.id), "scope": payload.scope},
    )
    db.commit()
    db.refresh(stage)
    return additional_file_stage_response(stage)


@router.post(
    "/items/{item_id}/attachment-checkout",
    response_model=AttachmentCheckoutResponse,
    status_code=status.HTTP_201_CREATED,
)
def acquire_new_attachment_checkout(
    item_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AttachmentCheckout:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    user = user_for_principal(db, principal)
    checkout = ensure_attachment_checkout(db, item, None, principal, user.id, settings)
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.create_checked_out",
        entity_type="item",
        entity_id=item.id,
        request_id=request.state.request_id,
    )
    db.commit()
    db.refresh(checkout)
    return checkout


@router.get("/items/{item_id}/attachment-edit")
def item_attachment_edit_status(item_id: uuid.UUID, db: Db, principal: Actor) -> dict:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    user = user_for_principal(db, principal)
    checkout = active_attachment_checkout(db, item_id, None)
    owned = checkout is not None and checkout.owner_id == user.id
    return {
        "active": owned and checkout.revision_id is None,
        "checked_out_by_other": checkout is not None and not owned,
        "task_id": str(checkout.task_id) if owned and checkout.task_id else None,
    }


@router.post(
    "/items/{item_id}/attachment-edit",
    response_model=AttachmentCheckoutResponse,
    status_code=status.HTTP_201_CREATED,
)
def begin_item_attachment_edit(
    item_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AttachmentCheckout:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    user = user_for_principal(db, principal)
    checkout = active_attachment_checkout(db, item_id, None)
    if checkout is not None and checkout.owner_id == user.id and checkout.revision_id is not None:
        raise HTTPException(
            status_code=409, detail="Finish the active revision attachment task first"
        )
    task = db.get(StagingSession, checkout.task_id) if checkout and checkout.task_id else None
    if task is None:
        task = StagingSession(
            client_staging_id=uuid.uuid4(),
            status="PREPARING",
            operation="EDIT_ITEM_ATTACHMENT",
            base_revision_id=None,
            package_sha256="0" * 64,
            metadata_json=None,
            proposal_json={
                "item": {"item_number": item.part_number, "name": item.name},
                "destination": {"item_id": str(item.id)},
            },
            result_json={"item_id": str(item.id)},
            created_by=user.id,
        )
        db.add(task)
        db.flush()
    checkout = ensure_attachment_checkout(
        db, item, None, principal, user.id, settings, task_id=task.id
    )
    record_audit(
        db,
        actor_id=user.id,
        action="item_attachment.edit_started",
        entity_type="item",
        entity_id=item.id,
        request_id=request.state.request_id,
        details={"task_id": str(task.id)},
    )
    db.commit()
    db.refresh(checkout)
    return checkout


@router.delete("/items/{item_id}/attachment-edit", status_code=status.HTTP_204_NO_CONTENT)
def finish_item_attachment_edit(
    item_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    cancel: Annotated[bool, Query()] = False,
) -> Response:
    if db.get(Item, item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    user = user_for_principal(db, principal)
    task = complete_attachment_edit_task(db, item_id, None, user.id, cancelled=cancel)
    record_audit(
        db,
        actor_id=user.id,
        action="item_attachment.edit_cancelled" if cancel else "item_attachment.edit_completed",
        entity_type="item",
        entity_id=item_id,
        request_id=request.state.request_id,
        details={"task_id": str(task.id)},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/items/{item_id}/revisions/{revision_id}/attachment-edit")
def attachment_edit_status(
    item_id: uuid.UUID, revision_id: uuid.UUID, db: Db, principal: Actor
) -> dict:
    revision = readable_item_revision(db, item_id, revision_id, principal)
    user = user_for_principal(db, principal)
    checkout = active_attachment_checkout(db, item_id, None)
    owned = checkout is not None and checkout.owner_id == user.id
    return {
        "active": owned and checkout.revision_id == revision.id,
        "checked_out_by_other": checkout is not None and not owned,
        "task_id": str(checkout.task_id) if owned and checkout.task_id else None,
    }


@router.post(
    "/items/{item_id}/revisions/{revision_id}/attachment-edit",
    response_model=AttachmentCheckoutResponse,
    status_code=status.HTTP_201_CREATED,
)
def begin_revision_attachment_edit(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AttachmentCheckout:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    revision = readable_item_revision(db, item_id, revision_id, principal)
    if revision.lifecycle_state != "WORKING":
        raise HTTPException(status_code=409, detail="Released revisions are immutable")
    user = user_for_principal(db, principal)
    checkout = active_attachment_checkout(db, item_id, None)
    if (
        checkout is not None
        and checkout.owner_id == user.id
        and checkout.revision_id != revision_id
    ):
        raise HTTPException(status_code=409, detail="Finish the active attachment edit task first")
    task = db.get(StagingSession, checkout.task_id) if checkout and checkout.task_id else None
    if task is None:
        task = StagingSession(
            client_staging_id=uuid.uuid4(),
            status="PREPARING",
            operation="EDIT_REVISION_ATTACHMENT",
            base_revision_id=revision.id,
            package_sha256="0" * 64,
            metadata_json=None,
            proposal_json={
                "item": {"item_number": item.part_number, "name": item.name},
                "destination": {"item_id": str(item.id), "revision_id": str(revision.id)},
            },
            result_json={"item_id": str(item.id), "revision_id": str(revision.id)},
            created_by=user.id,
        )
        db.add(task)
        db.flush()
    checkout = ensure_attachment_checkout(
        db,
        item,
        None,
        principal,
        user.id,
        settings,
        revision_id=revision.id,
        task_id=task.id,
    )
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.edit_started",
        entity_type="revision",
        entity_id=revision.id,
        request_id=request.state.request_id,
        details={"task_id": str(task.id)},
    )
    db.commit()
    db.refresh(checkout)
    return checkout


@router.delete(
    "/items/{item_id}/revisions/{revision_id}/attachment-edit",
    status_code=status.HTTP_204_NO_CONTENT,
)
def finish_revision_attachment_edit(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    cancel: Annotated[bool, Query()] = False,
) -> Response:
    readable_item_revision(db, item_id, revision_id, principal)
    user = user_for_principal(db, principal)
    task = complete_attachment_edit_task(db, item_id, revision_id, user.id, cancelled=cancel)
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.edit_cancelled" if cancel else "attachment.edit_completed",
        entity_type="revision",
        entity_id=revision_id,
        request_id=request.state.request_id,
        details={"task_id": str(task.id)},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/attachments/{attachment_id}/checkout",
    response_model=AttachmentCheckoutResponse,
    status_code=status.HTTP_201_CREATED,
)
def acquire_attachment_checkout(
    attachment_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AttachmentCheckout:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    item = db.get(Item, attachment.item_id)
    user = user_for_principal(db, principal)
    checkout = ensure_attachment_checkout(db, item, attachment, principal, user.id, settings)
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.checked_out",
        entity_type="attachment",
        entity_id=attachment.id,
        request_id=request.state.request_id,
        details={"base_version_id": str(checkout.base_version_id)},
    )
    db.commit()
    db.refresh(checkout)
    return checkout


@router.delete(
    "/items/{item_id}/attachment-checkout",
    status_code=status.HTTP_204_NO_CONTENT,
)
def release_new_attachment_checkout(
    item_id: uuid.UUID, request: Request, db: Db, principal: Actor
) -> Response:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    user = user_for_principal(db, principal)
    checkout = active_attachment_checkout(db, item_id, None)
    if checkout is not None:
        if checkout.owner_id != user.id and principal.role != "administrator":
            raise HTTPException(status_code=403, detail="Only the owner can release this checkout")
        db.delete(checkout)
        record_audit(
            db,
            actor_id=user.id,
            action="attachment.create_checkout_released",
            entity_type="item",
            entity_id=item_id,
            request_id=request.state.request_id,
        )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/attachments/{attachment_id}/checkout",
    status_code=status.HTTP_204_NO_CONTENT,
)
def release_attachment_checkout(
    attachment_id: uuid.UUID, request: Request, db: Db, principal: Actor
) -> Response:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    user = user_for_principal(db, principal)
    checkout = active_attachment_checkout(db, attachment.item_id, attachment.id)
    if checkout is not None:
        if checkout.owner_id != user.id and principal.role != "administrator":
            raise HTTPException(status_code=403, detail="Only the owner can release this checkout")
        db.delete(checkout)
        record_audit(
            db,
            actor_id=user.id,
            action="attachment.checkout_released",
            entity_type="attachment",
            entity_id=attachment.id,
            request_id=request.state.request_id,
        )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/items/{item_id}/additional-files/preflight",
    response_model=AdditionalFilePreflightResponse,
)
def preflight_additional_file(
    item_id: uuid.UUID,
    payload: AdditionalFilePreflightRequest,
    db: Db,
    principal: Actor,
) -> AdditionalFilePreflightResponse:
    """Classify exact duplicate content before an Additional File upload."""
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    user = user_for_principal(db, principal)
    require_attachment_editor(db, item, principal, user.id)

    target_attachment = None
    if payload.attachment_id is not None:
        target_attachment = db.get(Attachment, payload.attachment_id)
        if target_attachment is None or target_attachment.item_id != item_id:
            raise HTTPException(status_code=404, detail="Additional File not found on this Item")

    exact_rows = list(
        db.execute(
            select(Attachment, Artifact, ArtifactFile)
            .join(Artifact, Artifact.attachment_id == Attachment.id)
            .join(ArtifactFile, ArtifactFile.artifact_id == Artifact.id)
            .where(
                Artifact.state == "READY",
                ArtifactFile.state == "VERIFIED",
                ArtifactFile.sha256 == payload.sha256,
            )
            .order_by(Artifact.generation.desc(), Artifact.id)
        )
    )
    item_matches = [row for row in exact_rows if row[0].item_id == item_id]
    candidates = [
        AdditionalFileDuplicateCandidate(
            attachment_id=attachment.id,
            attachment_name=attachment.name,
            attachment_type=attachment.attachment_type,
            version_id=version.id,
            version_number=version.generation,
            filename=file.original_filename,
            scope="REVISION" if file.revision_id is not None else "ITEM",
            revision_id=file.revision_id,
        )
        for attachment, version, file in item_matches
    ]

    current_target_version_id = None
    if target_attachment is not None:
        current_target_version_id = db.scalar(
            select(Artifact.id)
            .where(
                Artifact.attachment_id == target_attachment.id,
                Artifact.state == "READY",
            )
            .order_by(Artifact.generation.desc(), Artifact.id.desc())
            .limit(1)
        )
    if current_target_version_id is not None and any(
        version.id == current_target_version_id for _attachment, version, _file in item_matches
    ):
        classification = "EXACT_CURRENT_VERSION"
    elif item_matches:
        classification = "EXACT_ITEM_MATCH"
    elif exact_rows:
        classification = "EXACT_GLOBAL_MATCH"
    else:
        same_name = target_attachment or db.scalar(
            select(Attachment).where(
                Attachment.item_id == item_id,
                func.lower(Attachment.name) == payload.name.casefold(),
            )
        )
        classification = "SAME_NAME_DIFFERENT_CONTENT" if same_name else "NEW_CONTENT"

    reusable = classification.startswith("EXACT_")
    return AdditionalFilePreflightResponse(
        classification=classification,
        upload_required=not reusable,
        reusable_content=reusable,
        candidates=candidates,
    )


@router.post(
    "/items/{item_id}/additional-files/staging",
    response_model=AdditionalFileStageResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_additional_file_stage(
    item_id: uuid.UUID,
    payload: AdditionalFileStageCreate,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AdditionalFileStageResponse:
    if payload.byte_size > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Additional File exceeds the upload limit")
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    user = user_for_principal(db, principal)
    require_attachment_editor(db, item, principal, user.id)
    revision = None
    if payload.scope == "REVISION":
        revision = readable_item_revision(db, item_id, payload.revision_id, principal)
        if revision.lifecycle_state != "WORKING":
            raise HTTPException(status_code=409, detail="Released revisions are immutable")
    existing = db.scalar(
        select(AdditionalFileStage).where(
            AdditionalFileStage.client_staging_id == payload.client_staging_id
        )
    )
    if existing is not None:
        if existing.item_id != item_id:
            raise HTTPException(status_code=409, detail="Staging key belongs to another Item")
        return additional_file_stage_response(existing)

    attachment = db.get(Attachment, payload.attachment_id) if payload.attachment_id else None
    if payload.attachment_id and (attachment is None or attachment.item_id != item_id):
        raise HTTPException(status_code=404, detail="Additional File not found on this Item")
    if attachment is not None and attachment.lifecycle_state != "ACTIVE":
        raise HTTPException(status_code=409, detail="Archived Additional Files cannot be changed")
    duplicate = db.scalar(
        select(Artifact.id)
        .join(ArtifactFile, ArtifactFile.artifact_id == Artifact.id)
        .join(Attachment, Attachment.id == Artifact.attachment_id)
        .where(
            Attachment.item_id == item_id,
            Artifact.state == "READY",
            ArtifactFile.state == "VERIFIED",
            ArtifactFile.sha256 == payload.sha256,
            Artifact.attachment_id != payload.attachment_id
            if payload.attachment_id is not None
            else True,
        )
        .limit(1)
    )
    if duplicate is not None and not payload.duplicate_confirmed:
        raise HTTPException(
            status_code=409,
            detail=(
                "Duplicate attachment content exists on this Item; "
                "confirm the duplicate before staging"
            ),
        )
    if attachment is not None:
        pending_stage = db.scalar(
            select(AdditionalFileStage).where(
                AdditionalFileStage.item_id == item_id,
                AdditionalFileStage.attachment_id == attachment.id,
                AdditionalFileStage.status.in_(["DRAFT", "READY"]),
            )
        )
        if pending_stage is not None:
            raise HTTPException(status_code=409, detail="Attachment already has a staged change")
    checkout = ensure_attachment_checkout(
        db,
        item,
        attachment,
        principal,
        user.id,
        settings,
        revision_id=revision.id if revision else None,
    )
    _task_checkout, task = active_attachment_edit_task(
        db, item.id, revision.id if revision else None, user.id
    )
    if attachment is not None:
        # Revalidate extension/MIME policy against the stored logical file type.
        AttachmentCreate(
            **payload.model_dump(
                exclude={
                    "client_staging_id",
                    "attachment_id",
                    "name",
                    "attachment_type",
                    "description",
                    "duplicate_confirmed",
                }
            ),
            name=attachment.name,
            attachment_type=attachment.attachment_type,
            description=attachment.description,
        )
    latest = (
        None
        if attachment is None
        else db.scalar(
            select(Artifact)
            .where(Artifact.attachment_id == attachment.id)
            .order_by(Artifact.generation.desc(), Artifact.id.desc())
            .limit(1)
        )
    )
    if latest is not None and latest.state != "READY":
        raise HTTPException(
            status_code=409, detail="The Additional File already has a pending change"
        )
    blob = None
    if payload.duplicate_action == "REUSE_EXISTING":
        blob = db.scalar(
            select(ContentBlob).where(
                ContentBlob.sha256 == payload.sha256,
                ContentBlob.byte_size == payload.byte_size,
                ContentBlob.state == "VERIFIED",
            )
        )
        if blob is None or not LocalStorageProvider(settings.storage_root).exists(blob.storage_key):
            raise HTTPException(status_code=409, detail="No verified reusable content matches")
    stage_id = uuid.uuid4()
    stage = AdditionalFileStage(
        id=stage_id,
        client_staging_id=payload.client_staging_id,
        item_id=item_id,
        revision_id=revision.id if revision else None,
        task_id=task.id if task else None,
        attachment_id=attachment.id if attachment else None,
        base_version_id=checkout.base_version_id,
        content_blob_id=blob.id if blob else None,
        action="NEW_VERSION" if attachment else "CREATE",
        duplicate_action=payload.duplicate_action,
        status="READY" if blob else "DRAFT",
        name=attachment.name if attachment else payload.name.strip(),
        attachment_type=attachment.attachment_type if attachment else payload.attachment_type,
        description=attachment.description if attachment else payload.description,
        filename=payload.filename,
        mime_type=payload.mime_type.lower(),
        byte_size=payload.byte_size,
        sha256=payload.sha256,
        comment=payload.comment,
        storage_key=f"staging/additional-files/{stage_id}/{payload.filename}",
        created_by=user.id,
    )
    db.add(stage)
    record_audit(
        db,
        actor_id=user.id,
        action="additional_file.staging_created",
        entity_type="additional_file_stage",
        entity_id=stage.id,
        request_id=request.state.request_id,
        details={"item_id": str(item_id), "action": stage.action},
    )
    db.commit()
    db.refresh(stage)
    return additional_file_stage_response(stage)


@router.get(
    "/items/{item_id}/additional-files/staging",
    response_model=list[AdditionalFileStageResponse],
)
def list_additional_file_stages(
    item_id: uuid.UUID,
    db: Db,
    principal: Actor,
    scope: Annotated[str, Query(pattern="^(ITEM|REVISION)$")] = "ITEM",
    revision_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[AdditionalFileStageResponse]:
    if db.get(Item, item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    user = user_for_principal(db, principal)
    statement = select(AdditionalFileStage).where(
        AdditionalFileStage.item_id == item_id,
        AdditionalFileStage.created_by == user.id,
        AdditionalFileStage.status.in_(["DRAFT", "READY"]),
    )
    if scope == "REVISION":
        if revision_id is None:
            raise HTTPException(status_code=422, detail="revision_id is required")
        statement = statement.where(AdditionalFileStage.revision_id == revision_id)
    else:
        statement = statement.where(AdditionalFileStage.revision_id.is_(None))
    stages = db.scalars(statement.order_by(AdditionalFileStage.updated_at.desc()))
    return [additional_file_stage_response(stage) for stage in stages]


@router.get(
    "/attachment-edit-tasks/{task_id}/changes",
    response_model=list[AdditionalFileStageResponse],
)
def list_attachment_task_changes(
    task_id: uuid.UUID, db: Db, principal: Actor
) -> list[AdditionalFileStageResponse]:
    """Return the immutable changed-file list for an attachment workspace task."""
    user = user_for_principal(db, principal)
    task = db.get(StagingSession, task_id)
    if (
        task is None
        or task.operation not in {"EDIT_ITEM_ATTACHMENT", "EDIT_REVISION_ATTACHMENT"}
        or (task.created_by != user.id and principal.role != "administrator")
    ):
        raise HTTPException(status_code=404, detail="Attachment edit task not found")
    stages = list(
        db.scalars(
            select(AdditionalFileStage)
            .where(
                AdditionalFileStage.task_id == task.id,
                AdditionalFileStage.status == "COMPLETED",
            )
            .order_by(AdditionalFileStage.created_at, AdditionalFileStage.id)
        )
    )
    if not stages:
        # Compatibility for tasks completed before task_id was introduced. Bound
        # the fallback by the next task for the same Item and attachment scope.
        result = task.result_json or {}
        destination = (task.proposal_json or {}).get("destination") or {}
        item_id = result.get("item_id") or destination.get("item_id")
        revision_id = result.get("revision_id") or destination.get("revision_id")
        next_started_at = None
        for candidate in db.scalars(
            select(StagingSession)
            .where(
                StagingSession.created_by == task.created_by,
                StagingSession.operation == task.operation,
                StagingSession.created_at > task.created_at,
            )
            .order_by(StagingSession.created_at)
        ):
            candidate_result = candidate.result_json or {}
            candidate_destination = (candidate.proposal_json or {}).get("destination") or {}
            if (candidate_result.get("item_id") or candidate_destination.get("item_id")) != item_id:
                continue
            if (
                candidate_result.get("revision_id") or candidate_destination.get("revision_id")
            ) != revision_id:
                continue
            next_started_at = candidate.created_at
            break
        if item_id:
            legacy_statement = select(AdditionalFileStage).where(
                AdditionalFileStage.item_id == uuid.UUID(str(item_id)),
                AdditionalFileStage.revision_id
                == (uuid.UUID(str(revision_id)) if revision_id else None),
                AdditionalFileStage.created_by == task.created_by,
                AdditionalFileStage.task_id.is_(None),
                AdditionalFileStage.status == "COMPLETED",
                AdditionalFileStage.created_at >= task.created_at,
            )
            if next_started_at is not None:
                legacy_statement = legacy_statement.where(
                    AdditionalFileStage.created_at < next_started_at
                )
            stages = list(
                db.scalars(
                    legacy_statement.order_by(
                        AdditionalFileStage.created_at, AdditionalFileStage.id
                    )
                )
            )
    return [additional_file_stage_response(stage) for stage in stages]


@router.get("/additional-files/staging/{stage_id}", response_model=AdditionalFileStageResponse)
def read_additional_file_stage(
    stage_id: uuid.UUID, db: Db, principal: Actor
) -> AdditionalFileStageResponse:
    stage, _user = owned_additional_file_stage(db, stage_id, principal)
    return additional_file_stage_response(stage)


@router.put(
    "/additional-files/staging/{stage_id}/content",
    response_model=AdditionalFileStageResponse,
)
async def upload_additional_file_stage(
    stage_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AdditionalFileStageResponse:
    stage, user = owned_additional_file_stage(db, stage_id, principal)
    if stage.status != "DRAFT" or stage.content_blob_id is not None:
        raise HTTPException(status_code=409, detail="Staging session is not accepting uploads")
    item = db.get(Item, stage.item_id)
    require_attachment_editor(db, item, principal, user.id)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != stage.mime_type.lower():
        raise HTTPException(status_code=415, detail="Content-Type does not match manifest")
    storage = LocalStorageProvider(settings.storage_root)
    partial = storage.partial_path(stage.storage_key)
    partial.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with partial.open("wb") as stream:
        async for chunk in request.stream():
            size += len(chunk)
            if size > stage.byte_size:
                partial.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail="Uploaded size exceeds manifest")
            digest.update(chunk)
            stream.write(chunk)
    if size != stage.byte_size or digest.hexdigest() != stage.sha256:
        partial.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Size or SHA-256 does not match manifest")
    promote_stage_blob(db, stage, storage, partial)
    record_audit(
        db,
        actor_id=user.id,
        action="additional_file.staging_uploaded",
        entity_type="additional_file_stage",
        entity_id=stage.id,
        request_id=request.state.request_id,
        details={"sha256": stage.sha256, "byte_size": stage.byte_size},
    )
    db.commit()
    db.refresh(stage)
    return additional_file_stage_response(stage)


@router.head("/additional-files/staging/{stage_id}/content")
def inspect_additional_file_stage_upload(
    stage_id: uuid.UUID, db: Db, principal: Actor, settings: Config
) -> Response:
    stage, _user = owned_additional_file_stage(db, stage_id, principal)
    if stage.status != "DRAFT":
        raise HTTPException(status_code=404, detail="Uploading staging session not found")
    partial = LocalStorageProvider(settings.storage_root).partial_path(stage.storage_key)
    offset = partial.stat().st_size if partial.is_file() else 0
    return Response(
        status_code=204,
        headers={"Upload-Offset": str(offset), "Upload-Length": str(stage.byte_size)},
    )


@router.patch(
    "/additional-files/staging/{stage_id}/content",
    response_model=AdditionalFileStageResponse,
)
async def resume_additional_file_stage_upload(
    stage_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AdditionalFileStageResponse:
    stage, user = owned_additional_file_stage(db, stage_id, principal)
    if stage.status != "DRAFT" or stage.content_blob_id is not None:
        raise HTTPException(status_code=404, detail="Uploading staging session not found")
    try:
        requested_offset = int(request.headers.get("upload-offset", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="A valid Upload-Offset is required") from exc
    storage = LocalStorageProvider(settings.storage_root)
    partial = storage.partial_path(stage.storage_key)
    partial.parent.mkdir(parents=True, exist_ok=True)
    actual_offset = partial.stat().st_size if partial.is_file() else 0
    if requested_offset != actual_offset:
        raise HTTPException(
            status_code=409,
            detail="Upload offset does not match",
            headers={"Upload-Offset": str(actual_offset)},
        )
    with partial.open("ab") as stream:
        async for chunk in request.stream():
            actual_offset += len(chunk)
            if actual_offset > stage.byte_size:
                raise HTTPException(status_code=413, detail="Upload exceeds declared size")
            stream.write(chunk)
    if actual_offset == stage.byte_size:
        if sha256_file(partial) != stage.sha256:
            partial.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="SHA-256 does not match manifest")
        promote_stage_blob(db, stage, storage, partial)
        record_audit(
            db,
            actor_id=user.id,
            action="additional_file.staging_uploaded",
            entity_type="additional_file_stage",
            entity_id=stage.id,
            request_id=request.state.request_id,
        )
    db.commit()
    db.refresh(stage)
    return additional_file_stage_response(stage)


@router.post(
    "/additional-files/staging/{stage_id}/commit",
    response_model=AdditionalFileStageResponse,
)
def commit_additional_file_stage(
    stage_id: uuid.UUID,
    payload: AdditionalFileStageCommit,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AdditionalFileStageResponse:
    stage, user = owned_additional_file_stage(db, stage_id, principal)
    stage = db.scalar(
        select(AdditionalFileStage).where(AdditionalFileStage.id == stage.id).with_for_update()
    )
    if stage.status == "COMPLETED":
        if stage.commit_id != payload.commit_id:
            raise HTTPException(status_code=409, detail="Stage was committed with another key")
        return additional_file_stage_response(stage)
    if stage.status != "READY" or stage.content_blob_id is None:
        raise HTTPException(status_code=409, detail="Staged content is not ready to commit")
    item = db.scalar(select(Item).where(Item.id == stage.item_id).with_for_update())
    require_attachment_editor(db, item, principal, user.id)
    checkout = active_attachment_checkout(db, stage.item_id, stage.attachment_id)
    if checkout is None or checkout.owner_id != user.id:
        raise HTTPException(status_code=409, detail="Attachment checkout is required")
    if checkout.revision_id != stage.revision_id:
        raise HTTPException(status_code=409, detail="Attachment checkout scope changed")
    if checkout.base_version_id != stage.base_version_id:
        raise HTTPException(status_code=409, detail="Attachment changed after checkout; refresh")
    blob = db.get(ContentBlob, stage.content_blob_id)
    if blob is None or blob.state != "VERIFIED":
        raise HTTPException(status_code=409, detail="Staged content is no longer verified")
    creating_attachment = stage.attachment_id is None
    attachment = None
    if stage.attachment_id is not None:
        attachment = db.scalar(
            select(Attachment).where(Attachment.id == stage.attachment_id).with_for_update()
        )
        if attachment is None or attachment.item_id != stage.item_id:
            raise HTTPException(status_code=409, detail="Target Additional File no longer exists")
        if attachment.lifecycle_state != "ACTIVE":
            raise HTTPException(status_code=409, detail="Target Additional File is archived")
        current = db.scalar(
            select(Artifact)
            .where(Artifact.attachment_id == attachment.id)
            .order_by(Artifact.generation.desc(), Artifact.id.desc())
            .limit(1)
        )
        if current is None or current.id != stage.base_version_id:
            raise HTTPException(
                status_code=409, detail="Additional File changed after review; refresh"
            )
        if stage.action == "REMOVE":
            if stage.revision_id is None:
                attachment.lifecycle_state = "ARCHIVED"
                result = {"attachment_id": str(attachment.id), "scope": "ITEM"}
            else:
                snapshot = publish_revision_attachment_removal(
                    db, stage.revision_id, attachment.id, user.id
                )
                result = {
                    "attachment_id": str(attachment.id),
                    "scope": "REVISION",
                    "content_snapshot_id": str(snapshot.id),
                }
            stage.status = "COMPLETED"
            stage.commit_id = payload.commit_id
            stage.result_json = result
            record_audit(
                db,
                actor_id=user.id,
                action="additional_file.removal_committed",
                entity_type="additional_file_stage",
                entity_id=stage.id,
                request_id=request.state.request_id,
                details=result,
            )
            db.delete(checkout)
            db.commit()
            db.refresh(stage)
            return additional_file_stage_response(stage)
    else:
        attachment = Attachment(
            item_id=stage.item_id,
            name=stage.name,
            attachment_type=stage.attachment_type,
            description=stage.description,
            lifecycle_state="ACTIVE",
            created_by=user.id,
        )
        db.add(attachment)
        db.flush()
    previous = db.scalar(
        select(Artifact)
        .where(Artifact.attachment_id == attachment.id)
        .order_by(Artifact.generation.desc(), Artifact.id.desc())
        .limit(1)
    )
    generation = 1 if previous is None else previous.generation + 1
    version = Artifact(
        artifact_key=previous.artifact_key if previous else uuid.uuid4(),
        attachment_id=attachment.id,
        generation=generation,
        artifact_type="DOCUMENT",
        name=attachment.name,
        description=attachment.description,
        classification="AUTHORITATIVE",
        source="USER",
        state="READY",
        comment=stage.comment,
        created_by=user.id,
        ready_at=datetime.now(UTC),
    )
    db.add(version)
    db.flush()
    file = ArtifactFile(
        artifact_id=version.id,
        content_blob_id=blob.id,
        revision_id=stage.revision_id,
        kind="ATTACHMENT",
        storage_key=f"items/{item.id}/attachments/{attachment.id}/versions/{version.id}/{stage.filename}",
        original_filename=stage.filename,
        mime_type=stage.mime_type,
        byte_size=stage.byte_size,
        sha256=stage.sha256,
        state="VERIFIED",
        required=False,
    )
    db.add(file)
    if stage.revision_id is not None:
        db.flush()
        relation = ensure_revision_attachment_relation(
            db, version, file, user.id, stage.revision_id
        )
        publish_revision_attachment_snapshot(db, relation, user.id)
    stage.status = "COMPLETED"
    stage.commit_id = payload.commit_id
    stage.attachment_id = attachment.id
    stage.result_json = {
        "attachment_id": str(attachment.id),
        "version_id": str(version.id),
        "version_number": generation,
    }
    record_audit(
        db,
        actor_id=user.id,
        action="additional_file.staging_committed",
        entity_type="additional_file_stage",
        entity_id=stage.id,
        request_id=request.state.request_id,
        details={**stage.result_json, "item_id": str(item.id), "action": stage.action},
    )
    record_revision_attachment_audit(db, request, user.id, version, file)
    # Keep the Item-level creation checkout while the Attachment editor remains
    # open so the user can stage more than one new attachment. Existing-file
    # checkouts are version-specific and end when that version is committed.
    if not creating_attachment:
        db.delete(checkout)
    db.commit()
    db.refresh(stage)
    return additional_file_stage_response(stage)


@router.delete("/additional-files/staging/{stage_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_additional_file_stage(
    stage_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> None:
    stage, user = owned_additional_file_stage(db, stage_id, principal)
    if stage.status == "COMPLETED":
        raise HTTPException(status_code=409, detail="Committed staging cannot be cancelled")
    LocalStorageProvider(settings.storage_root).partial_path(stage.storage_key).unlink(
        missing_ok=True
    )
    stage.status = "CANCELLED"
    checkout = active_attachment_checkout(db, stage.item_id, stage.attachment_id)
    if checkout is not None and checkout.owner_id == user.id:
        # A revision attachment editor owns its creation checkout for the whole
        # task, not just for one staged file.
        if checkout.attachment_id is not None or checkout.task_id is None:
            db.delete(checkout)
    record_audit(
        db,
        actor_id=user.id,
        action="additional_file.staging_cancelled",
        entity_type="additional_file_stage",
        entity_id=stage.id,
        request_id=request.state.request_id,
    )
    db.commit()


@router.post(
    "/items/{item_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_attachment(
    item_id: uuid.UUID,
    payload: AttachmentCreate,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AttachmentResponse:
    if payload.byte_size > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Attachment exceeds the upload size limit")
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    user = user_for_principal(db, principal)
    require_attachment_editor(db, item, principal, user.id)
    if payload.client_version_id is not None:
        existing = db.scalar(
            select(Artifact).where(Artifact.client_version_id == payload.client_version_id)
        )
        if existing is not None:
            existing_attachment = db.get(Attachment, existing.attachment_id)
            if existing_attachment.item_id != item_id:
                raise HTTPException(
                    status_code=409, detail="Idempotency key belongs to another Item"
                )
            return attachment_response(db, existing_attachment)
    attachment = Attachment(
        item_id=item_id,
        name=payload.name,
        attachment_type=payload.attachment_type,
        description=payload.description,
        created_by=user.id,
    )
    db.add(attachment)
    db.flush()
    version = add_version(
        db, attachment, payload, user.id, settings.upload_expiry_seconds, settings.storage_root
    )
    if version.state == "READY" and payload.duplicate_action == "REUSE_EXISTING":
        file = db.scalar(select(ArtifactFile).where(ArtifactFile.artifact_id == version.id))
        record_audit(
            db,
            actor_id=user.id,
            action="attachment.content_reused",
            entity_type="attachment_version",
            entity_id=version.id,
            request_id=request.state.request_id,
            details={"sha256": file.sha256, "byte_size": file.byte_size},
        )
        record_revision_attachment_audit(db, request, user.id, version, file)
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.created",
        entity_type="attachment",
        entity_id=attachment.id,
        request_id=request.state.request_id,
        details={"item_id": str(item_id), "type": attachment.attachment_type},
    )
    db.commit()
    db.refresh(attachment)
    return attachment_response(db, attachment)


@router.post(
    "/attachments/{attachment_id}/versions",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_attachment_version(
    attachment_id: uuid.UUID,
    payload: AttachmentVersionPlan,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AttachmentResponse:
    if payload.byte_size > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Attachment exceeds the upload size limit")
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    AttachmentCreate(
        **payload.model_dump(),
        name=attachment.name,
        attachment_type=attachment.attachment_type,
        description=attachment.description,
    )
    user = user_for_principal(db, principal)
    item = db.get(Item, attachment.item_id)
    require_attachment_editor(db, item, principal, user.id)
    if payload.client_version_id is not None:
        existing = db.scalar(
            select(Artifact).where(Artifact.client_version_id == payload.client_version_id)
        )
        if existing is not None:
            if existing.attachment_id != attachment.id:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency key belongs to another attachment",
                )
            return attachment_response(db, attachment)
    version = add_version(
        db, attachment, payload, user.id, settings.upload_expiry_seconds, settings.storage_root
    )
    if version.state == "READY" and payload.duplicate_action == "REUSE_EXISTING":
        file = db.scalar(select(ArtifactFile).where(ArtifactFile.artifact_id == version.id))
        record_audit(
            db,
            actor_id=user.id,
            action="attachment.content_reused",
            entity_type="attachment_version",
            entity_id=version.id,
            request_id=request.state.request_id,
            details={"sha256": file.sha256, "byte_size": file.byte_size},
        )
        record_revision_attachment_audit(db, request, user.id, version, file)
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.version_created",
        entity_type="attachment_version",
        entity_id=version.id,
        request_id=request.state.request_id,
        details={"attachment_id": str(attachment.id), "version": version.generation},
    )
    db.commit()
    return attachment_response(db, attachment)


@router.get("/items/{item_id}/attachments", response_model=list[AttachmentResponse])
def list_attachments(
    item_id: uuid.UUID,
    db: Db,
    principal: Actor,
    include_archived: Annotated[bool, Query()] = False,
) -> list[AttachmentResponse]:
    if db.get(Item, item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    user = user_for_principal(db, principal)
    statement = (
        select(Attachment, Artifact.id)
        .join(Artifact, Artifact.attachment_id == Attachment.id)
        .join(ArtifactFile, ArtifactFile.artifact_id == Artifact.id)
        .where(
            Attachment.item_id == item_id,
            ArtifactFile.revision_id.is_(None),
        )
    )
    if not include_archived:
        statement = statement.where(Attachment.lifecycle_state == "ACTIVE")
    artifact_ids_by_attachment: dict[uuid.UUID, set[uuid.UUID]] = {}
    attachments_by_id: dict[uuid.UUID, Attachment] = {}
    for attachment, artifact_id in db.execute(
        statement.order_by(Attachment.created_at, Artifact.generation)
    ):
        attachments_by_id[attachment.id] = attachment
        artifact_ids_by_attachment.setdefault(attachment.id, set()).add(artifact_id)
    return [
        attachment_response(
            db,
            attachment,
            artifact_ids=artifact_ids_by_attachment[attachment.id],
            user_id=user.id,
        )
        for attachment in attachments_by_id.values()
    ]


@router.get(
    "/items/{item_id}/revisions/{revision_id}/attachments",
    response_model=list[AttachmentResponse],
)
def list_revision_attachments(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
) -> list[AttachmentResponse]:
    user = user_for_principal(db, principal)
    revision = db.get(Revision, revision_id)
    if revision is None or revision.part_id != item_id:
        raise HTTPException(status_code=404, detail="Item revision not found")
    latest_snapshot = db.scalar(
        select(RevisionContentSnapshot)
        .where(RevisionContentSnapshot.revision_id == revision_id)
        .order_by(RevisionContentSnapshot.sequence.desc(), RevisionContentSnapshot.id.desc())
        .limit(1)
    )
    if latest_snapshot is None:
        return []
    artifact_ids = set(
        db.scalars(
            select(RevisionArtifactRelation.artifact_id)
            .join(
                RevisionContentSnapshotArtifact,
                RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id,
            )
            .where(
                RevisionContentSnapshotArtifact.snapshot_id == latest_snapshot.id,
                RevisionArtifactRelation.relation_type == "ATTACHMENT",
            )
        )
    )
    if not artifact_ids:
        return []
    attachments = list(
        db.scalars(
            select(Attachment)
            .join(Artifact, Artifact.attachment_id == Attachment.id)
            .where(
                Attachment.item_id == item_id,
                Artifact.id.in_(artifact_ids),
            )
            .distinct()
            .order_by(Attachment.created_at)
        )
    )
    return [
        attachment_response(db, attachment, artifact_ids=artifact_ids, user_id=user.id)
        for attachment in attachments
    ]


@router.get("/attachments/{attachment_id}", response_model=AttachmentResponse)
def read_attachment(attachment_id: uuid.UUID, db: Db, principal: Actor) -> AttachmentResponse:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    user = user_for_principal(db, principal)
    return attachment_response(db, attachment, user_id=user.id)


@router.get(
    "/additional-files/{attachment_id}/versions",
    response_model=list[AttachmentVersionResponse],
)
def list_additional_file_versions(
    attachment_id: uuid.UUID, db: Db, principal: Actor
) -> list[AttachmentVersionResponse]:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Additional File not found")
    return attachment_response(db, attachment).versions


@router.post("/attachments/{attachment_id}/archive", response_model=AttachmentResponse)
def archive_attachment(
    attachment_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
) -> AttachmentResponse:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Additional File not found")
    user = user_for_principal(db, principal)
    item = db.get(Item, attachment.item_id)
    require_attachment_editor(db, item, principal, user.id)
    checkout = require_owned_attachment_checkout(db, attachment, user.id)
    attachment.lifecycle_state = "ARCHIVED"
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.archived",
        entity_type="attachment",
        entity_id=attachment.id,
        request_id=request.state.request_id,
    )
    db.delete(checkout)
    db.commit()
    db.refresh(attachment)
    return attachment_response(db, attachment)


@router.post("/attachments/{attachment_id}/restore", response_model=AttachmentResponse)
def restore_attachment(
    attachment_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
) -> AttachmentResponse:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Additional File not found")
    user = user_for_principal(db, principal)
    item = db.get(Item, attachment.item_id)
    require_attachment_editor(db, item, principal, user.id)
    attachment.lifecycle_state = "ACTIVE"
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.restored",
        entity_type="attachment",
        entity_id=attachment.id,
        request_id=request.state.request_id,
    )
    db.commit()
    db.refresh(attachment)
    return attachment_response(db, attachment)


@router.patch("/attachments/{attachment_id}", response_model=AttachmentResponse)
def update_attachment(
    attachment_id: uuid.UUID,
    payload: AttachmentUpdate,
    request: Request,
    db: Db,
    principal: Actor,
) -> AttachmentResponse:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    user = user_for_principal(db, principal)
    item = db.get(Item, attachment.item_id)
    require_attachment_editor(db, item, principal, user.id)
    require_owned_attachment_checkout(db, attachment, user.id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(attachment, field, value)
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.updated",
        entity_type="attachment",
        entity_id=attachment.id,
        request_id=request.state.request_id,
        details={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(attachment)
    return attachment_response(db, attachment)


@router.put("/attachment-versions/{version_id}/content", response_model=AttachmentVersionResponse)
async def upload_attachment(
    version_id: uuid.UUID, request: Request, db: Db, principal: Actor, settings: Config
) -> AttachmentVersionResponse:
    version = db.get(Artifact, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Attachment version not found")
    if version.state != "UPLOADING":
        raise HTTPException(status_code=409, detail="Attachment version is not accepting uploads")
    if version.expires_at is not None and version.expires_at < datetime.now(UTC):
        version.state = "EXPIRED"
        db.commit()
        raise HTTPException(status_code=410, detail="Attachment upload has expired")
    user = user_for_principal(db, principal)
    if version.created_by != user.id and principal.role != "administrator":
        raise HTTPException(status_code=404, detail="Attachment version not found")
    attachment = db.get(Attachment, version.attachment_id)
    item = db.get(Item, attachment.item_id)
    require_attachment_editor(db, item, principal, user.id)
    artifact = db.scalar(select(ArtifactFile).where(ArtifactFile.artifact_id == version.id))
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != artifact.mime_type.lower():
        raise HTTPException(status_code=415, detail="Content-Type does not match manifest")
    storage = LocalStorageProvider(settings.storage_root)
    final_path = storage.create_upload(artifact.storage_key)
    partial = storage.partial_path(artifact.storage_key)
    digest = hashlib.sha256()
    size = 0
    with partial.open("wb") as stream:
        async for chunk in request.stream():
            size += len(chunk)
            if size > artifact.byte_size:
                stream.close()
                partial.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail="Uploaded size exceeds manifest")
            digest.update(chunk)
            stream.write(chunk)
    if size != artifact.byte_size or digest.hexdigest() != artifact.sha256:
        partial.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422, detail="Attachment size or SHA-256 does not match manifest"
        )
    os.replace(partial, final_path)
    promote_attachment_blob(db, artifact, storage)
    artifact.state = "VERIFIED"
    version.state = "READY"
    version.ready_at = datetime.now(UTC)
    relation = ensure_revision_attachment_relation(db, version, artifact, user.id)
    publish_revision_attachment_snapshot(db, relation, user.id)
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.uploaded",
        entity_type="attachment_version",
        entity_id=version.id,
        request_id=request.state.request_id,
        details={"sha256": artifact.sha256, "byte_size": artifact.byte_size},
    )
    record_revision_attachment_audit(db, request, user.id, version, artifact)
    db.commit()
    db.refresh(version)
    return version_response(version, artifact)


@router.head("/attachment-versions/{version_id}/content")
def inspect_attachment_upload(
    version_id: uuid.UUID, db: Db, principal: Actor, settings: Config
) -> Response:
    version = db.get(Artifact, version_id)
    if version is None or version.state != "UPLOADING":
        raise HTTPException(status_code=404, detail="Uploading attachment version not found")
    user = user_for_principal(db, principal)
    if version.created_by != user.id and principal.role != "administrator":
        raise HTTPException(status_code=404, detail="Uploading attachment version not found")
    artifact = db.scalar(select(ArtifactFile).where(ArtifactFile.artifact_id == version.id))
    partial = LocalStorageProvider(settings.storage_root).partial_path(artifact.storage_key)
    offset = partial.stat().st_size if partial.is_file() else 0
    return Response(
        status_code=204,
        headers={"Upload-Offset": str(offset), "Upload-Length": str(artifact.byte_size)},
    )


@router.patch("/attachment-versions/{version_id}/content", response_model=AttachmentVersionResponse)
async def resume_attachment_upload(
    version_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AttachmentVersionResponse:
    version = db.get(Artifact, version_id)
    if version is None or version.state != "UPLOADING":
        raise HTTPException(status_code=404, detail="Uploading attachment version not found")
    user = user_for_principal(db, principal)
    if version.created_by != user.id and principal.role != "administrator":
        raise HTTPException(status_code=404, detail="Uploading attachment version not found")
    if version.expires_at is not None and version.expires_at < datetime.now(UTC):
        version.state = "EXPIRED"
        db.commit()
        raise HTTPException(status_code=410, detail="Attachment upload has expired")
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type != "application/offset+octet-stream":
        raise HTTPException(status_code=415, detail="Use application/offset+octet-stream")
    try:
        requested_offset = int(request.headers.get("upload-offset", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="A valid Upload-Offset is required") from exc
    artifact = db.scalar(select(ArtifactFile).where(ArtifactFile.artifact_id == version.id))
    storage = LocalStorageProvider(settings.storage_root)
    final_path = storage.create_upload(artifact.storage_key)
    partial = storage.partial_path(artifact.storage_key)
    actual_offset = partial.stat().st_size if partial.is_file() else 0
    if requested_offset != actual_offset:
        raise HTTPException(
            status_code=409,
            detail="Upload offset does not match",
            headers={"Upload-Offset": str(actual_offset)},
        )
    with partial.open("ab") as stream:
        async for chunk in request.stream():
            actual_offset += len(chunk)
            if actual_offset > artifact.byte_size:
                raise HTTPException(status_code=413, detail="Upload exceeds declared size")
            stream.write(chunk)
    if actual_offset == artifact.byte_size:
        digest = sha256_file(partial)
        if digest != artifact.sha256:
            partial.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="SHA-256 does not match manifest")
        os.replace(partial, final_path)
        promote_attachment_blob(db, artifact, storage)
        artifact.state = "VERIFIED"
        version.state = "READY"
        version.ready_at = datetime.now(UTC)
        relation = ensure_revision_attachment_relation(db, version, artifact, user.id)
        publish_revision_attachment_snapshot(db, relation, user.id)
        record_audit(
            db,
            actor_id=user.id,
            action="attachment.uploaded",
            entity_type="attachment_version",
            entity_id=version.id,
            request_id=request.state.request_id,
            details={"sha256": artifact.sha256, "byte_size": artifact.byte_size},
        )
        record_revision_attachment_audit(db, request, user.id, version, artifact)
    db.commit()
    db.refresh(version)
    return version_response(version, artifact)


@router.delete(
    "/attachment-versions/{version_id}/upload",
    status_code=status.HTTP_204_NO_CONTENT,
)
def cancel_attachment_upload(
    version_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> None:
    version = db.get(Artifact, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Attachment version not found")
    if version.state == "READY":
        raise HTTPException(
            status_code=409, detail="A ready attachment version cannot be cancelled"
        )
    user = user_for_principal(db, principal)
    if version.created_by != user.id and principal.role != "administrator":
        raise HTTPException(status_code=404, detail="Attachment version not found")
    attachment = db.get(Attachment, version.attachment_id)
    item = db.get(Item, attachment.item_id)
    require_attachment_editor(db, item, principal, user.id)
    artifact = db.scalar(select(ArtifactFile).where(ArtifactFile.artifact_id == version.id))
    storage = LocalStorageProvider(settings.storage_root)
    storage.partial_path(artifact.storage_key).unlink(missing_ok=True)
    storage.delete(artifact.storage_key)
    version.state = "CANCELLED"
    artifact.state = "CANCELLED"
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.upload_cancelled",
        entity_type="attachment_version",
        entity_id=version.id,
        request_id=request.state.request_id,
    )
    db.commit()


@router.get("/attachment-versions/{version_id}/download")
def download_attachment(
    version_id: uuid.UUID, request: Request, db: Db, principal: Actor, settings: Config
) -> FileResponse:
    version = db.get(Artifact, version_id)
    if version is None or version.state != "READY":
        raise HTTPException(status_code=404, detail="Ready attachment version not found")
    artifact = db.scalar(select(ArtifactFile).where(ArtifactFile.artifact_id == version.id))
    storage = LocalStorageProvider(settings.storage_root)
    path = attachment_content_path(db, artifact, storage)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment content not found")
    user = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.downloaded",
        entity_type="attachment_version",
        entity_id=version.id,
        request_id=request.state.request_id,
        details={"filename": artifact.original_filename},
    )
    db.commit()
    return FileResponse(path, media_type=artifact.mime_type, filename=artifact.original_filename)


@router.get("/attachment-versions/{version_id}/preview")
def preview_attachment(
    version_id: uuid.UUID, request: Request, db: Db, principal: Actor, settings: Config
) -> FileResponse:
    version = db.get(Artifact, version_id)
    if version is None or version.state != "READY":
        raise HTTPException(status_code=404, detail="Ready attachment version not found")
    attachment = db.get(Attachment, version.attachment_id)
    artifact = db.scalar(select(ArtifactFile).where(ArtifactFile.artifact_id == version.id))
    if attachment is None or artifact is None or artifact.state != "VERIFIED":
        raise HTTPException(status_code=404, detail="Verified attachment content not found")
    if artifact.revision_id is not None:
        readable_item_revision(db, attachment.item_id, artifact.revision_id, principal)
    storage = LocalStorageProvider(settings.storage_root)
    path = attachment_content_path(db, artifact, storage)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment content not found")
    size = path.stat().st_size
    if size > settings.max_pdf_preview_bytes:
        raise HTTPException(
            status_code=413,
            detail="PDF exceeds the preview size limit. Download the original instead.",
        )
    with path.open("rb") as stream:
        header = stream.read(8)
        stream.seek(max(0, size - 1024))
        trailer = stream.read()
    if (
        not (header.startswith(b"%PDF-1.") or header.startswith(b"%PDF-2."))
        or b"%%EOF" not in trailer
    ):
        raise HTTPException(status_code=415, detail="This file is not a supported PDF document")
    user = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=user.id,
        action="attachment.previewed",
        entity_type="attachment_version",
        entity_id=version.id,
        request_id=request.state.request_id,
        details={"filename": artifact.original_filename, "version": version.generation},
    )
    db.commit()
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=artifact.original_filename,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
