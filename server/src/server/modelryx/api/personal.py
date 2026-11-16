import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modelryx.api.items import get_item_or_404, item_response
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.db import get_db
from modelryx.models import (
    AuditLog,
    Checkout,
    DownloadBundle,
    Favorite,
    Item,
    ProcessingJob,
    RecentlyViewedItem,
    Revision,
    StagingFile,
    StagingSession,
    User,
    UserNotification,
)
from modelryx.models.schemas import ItemResponse

router = APIRouter(prefix="/api/v1/me", tags=["personal"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]


class WorkspaceItemResponse(ItemResponse):
    current_revision_code: str | None = None
    workspace_revision_id: uuid.UUID | None = None
    workspace_revision_code: str | None = None
    checkout_acquired_at: datetime | None = None
    checkout_expires_at: datetime | None = None
    checkout_scope: str | None = None


class WorkspaceUploadResponse(BaseModel):
    id: uuid.UUID
    status: str
    operation: str
    snapshot_version: int
    item_number: str | None = None
    item_name: str | None = None
    item_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    primary_filename: str | None = None
    file_count: int
    verified_file_count: int
    total_bytes: int
    verified_bytes: int
    progress_percent: int
    workflow_state: str | None = None
    workflow_stage: str | None = None
    workflow_error_detail: str | None = None
    can_cancel: bool
    can_resume: bool
    can_edit: bool
    is_finished: bool
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    download_url: str | None = None


class WorkspaceResponse(BaseModel):
    checked_out: list[WorkspaceItemResponse]
    uploads: list[WorkspaceUploadResponse]


class LibraryUserResponse(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str


class ActivityResponse(BaseModel):
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    occurred_at: datetime


class HomeResponse(BaseModel):
    recent: list[ItemResponse]
    favorites: list[ItemResponse]
    activity: list[ActivityResponse]


class NotificationResponse(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    message: str
    entity_type: str
    entity_id: uuid.UUID | None
    details: dict
    created_at: datetime
    read_at: datetime | None


def notification_response(row: UserNotification) -> NotificationResponse:
    return NotificationResponse(
        id=row.id,
        kind=row.kind,
        title=row.title,
        message=row.message,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        details=row.details_json,
        created_at=row.created_at,
        read_at=row.read_at,
    )


@router.get("/notifications", response_model=list[NotificationResponse])
def list_notifications(
    db: Db,
    principal: Actor,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[NotificationResponse]:
    user = user_for_principal(db, principal)
    statement = select(UserNotification).where(UserNotification.user_id == user.id)
    if unread_only:
        statement = statement.where(UserNotification.read_at.is_(None))
    rows = db.scalars(statement.order_by(UserNotification.created_at.desc()).limit(limit))
    return [notification_response(row) for row in rows]


@router.post("/notifications/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: uuid.UUID,
    db: Db,
    principal: Actor,
) -> NotificationResponse:
    user = user_for_principal(db, principal)
    row = db.scalar(
        select(UserNotification).where(
            UserNotification.id == notification_id,
            UserNotification.user_id == user.id,
        )
    )
    if row is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="notification not found")
    if row.read_at is None:
        row.read_at = datetime.now(UTC)
        db.commit()
        db.refresh(row)
    return notification_response(row)


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_notifications_read(db: Db, principal: Actor) -> None:
    user = user_for_principal(db, principal)
    rows = db.scalars(
        select(UserNotification).where(
            UserNotification.user_id == user.id,
            UserNotification.read_at.is_(None),
        )
    )
    current = datetime.now(UTC)
    changed = False
    for row in rows:
        row.read_at = current
        changed = True
    if changed:
        db.commit()


def visible_items(db: Session, item_ids: list[uuid.UUID]) -> list[Item]:
    if not item_ids:
        return []
    found = {item.id: item for item in db.scalars(select(Item).where(Item.id.in_(item_ids)))}
    return [
        found[item_id]
        for item_id in item_ids
        if item_id in found and found[item_id].status == "ACTIVE"
    ]


@router.get("/favorites", response_model=list[ItemResponse])
def list_favorites(db: Db, principal: Actor) -> list[ItemResponse]:
    user = user_for_principal(db, principal)
    rows = list(
        db.scalars(
            select(Favorite).where(Favorite.user_id == user.id).order_by(Favorite.created_at.desc())
        )
    )
    return [item_response(item) for item in visible_items(db, [row.item_id for row in rows])]


@router.put("/favorites/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_favorite(item_id: uuid.UUID, request: Request, db: Db, principal: Actor) -> None:
    user = user_for_principal(db, principal)
    get_item_or_404(db, item_id)
    if db.get(Favorite, {"user_id": user.id, "item_id": item_id}) is None:
        db.add(Favorite(user_id=user.id, item_id=item_id))
        record_audit(
            db,
            actor_id=user.id,
            action="favorite.added",
            entity_type="item",
            entity_id=item_id,
            request_id=request.state.request_id,
        )
        db.commit()


@router.delete("/favorites/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_favorite(item_id: uuid.UUID, request: Request, db: Db, principal: Actor) -> None:
    user = user_for_principal(db, principal)
    favorite = db.get(Favorite, {"user_id": user.id, "item_id": item_id})
    if favorite is not None:
        db.delete(favorite)
        record_audit(
            db,
            actor_id=user.id,
            action="favorite.removed",
            entity_type="item",
            entity_id=item_id,
            request_id=request.state.request_id,
        )
        db.commit()


@router.get("/recent", response_model=list[ItemResponse])
def list_recent(db: Db, principal: Actor) -> list[ItemResponse]:
    user = user_for_principal(db, principal)
    rows = list(
        db.scalars(
            select(RecentlyViewedItem)
            .where(RecentlyViewedItem.user_id == user.id)
            .order_by(RecentlyViewedItem.viewed_at.desc())
            .limit(50)
        )
    )
    return [item_response(item) for item in visible_items(db, [row.item_id for row in rows])]


@router.get("/activity", response_model=list[ActivityResponse])
def list_activity(
    db: Db,
    principal: Actor,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ActivityResponse]:
    user = user_for_principal(db, principal)
    rows = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.actor_id == user.id)
            .order_by(AuditLog.occurred_at.desc())
            .limit(limit)
        )
    )
    return [
        ActivityResponse(
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            occurred_at=row.occurred_at,
        )
        for row in rows
    ]


@router.get("/workspace", response_model=WorkspaceResponse)
def workspace(db: Db, principal: Actor) -> WorkspaceResponse:
    user = user_for_principal(db, principal)
    checked_out = list(
        db.scalars(
            select(Checkout).where(
                Checkout.owner_id == user.id,
                Checkout.expires_at > datetime.now(UTC),
            )
        )
    )
    checkout_by_item = {row.part_id: row for row in checked_out}
    items = sorted(
        visible_items(db, list(checkout_by_item)),
        key=lambda item: item.part_number.casefold(),
    )
    revision_ids = {
        revision_id
        for item in items
        for revision_id in (
            item.current_revision_id,
            checkout_by_item[item.id].revision_id,
        )
        if revision_id
    }
    revisions_by_id = {
        revision.id: revision
        for revision in db.scalars(select(Revision).where(Revision.id.in_(revision_ids)))
    } if revision_ids else {}
    staging_rows = list(db.scalars(
        select(StagingSession)
        .where(
            StagingSession.created_by == user.id,
            StagingSession.status != "CANCELLED",
        )
        .order_by(StagingSession.updated_at.desc())
    ))
    staging_rows = [
        row for row in staging_rows
        if ((row.result_json or {}).get("workspace") or {}).get("state") != "REMOVED"
    ]
    staging_ids = [row.id for row in staging_rows]
    file_stats = {
        row.staging_id: row
        for row in db.execute(
            select(
                StagingFile.staging_id,
                func.count(StagingFile.id).label("file_count"),
                func.count(StagingFile.id).filter(StagingFile.state == "VERIFIED").label(
                    "verified_file_count"
                ),
                func.coalesce(func.sum(StagingFile.byte_size), 0).label("total_bytes"),
                func.coalesce(
                    func.sum(StagingFile.byte_size).filter(StagingFile.state == "VERIFIED"), 0
                ).label("verified_bytes"),
            )
            .where(StagingFile.staging_id.in_(staging_ids))
            .group_by(StagingFile.staging_id)
        )
    } if staging_ids else {}
    staging_files: dict[uuid.UUID, list[StagingFile]] = {stage_id: [] for stage_id in staging_ids}
    if staging_ids:
        for file in db.scalars(
            select(StagingFile)
            .where(StagingFile.staging_id.in_(staging_ids))
            .order_by(StagingFile.logical_path)
        ):
            staging_files[file.staging_id].append(file)

    def primary_file(files: list[StagingFile]) -> str | None:
        if not files:
            return None
        return min(
            files,
            key=lambda file: (
                file.logical_path.count("/") + file.logical_path.count("\\"),
                file.logical_path.casefold(),
            ),
        ).filename

    uploads = []
    for staging in staging_rows:
        stats = file_stats.get(staging.id)
        proposal = staging.proposal_json or {}
        destination = proposal.get("destination") or {}
        result = staging.result_json or {}
        workflow = result.get("workflow") or {}
        workspace_state = (result.get("workspace") or {}).get("state")
        is_finished = staging.status in {"COMPLETED", "PROMOTED"}
        workflow_state = str(workflow.get("state") or "")
        effective_status = staging.status
        revision_id = (
            result.get("revision_id")
            or destination.get("revision_id")
            or staging.base_revision_id
        )
        if is_finished and revision_id:
            revision_id = uuid.UUID(str(revision_id))
            processing_job = db.scalar(
                select(ProcessingJob)
                .where(ProcessingJob.revision_id == revision_id)
                .order_by(ProcessingJob.available_at.desc(), ProcessingJob.id.desc())
                .limit(1)
            )
            if processing_job is not None:
                workflow = {
                    "state": processing_job.state,
                    "stage": processing_job.stage,
                    "progress_percent": processing_job.progress_percent,
                    "error_detail": processing_job.error_detail,
                }
                workflow_state = processing_job.state
                if processing_job.state == "FAILED":
                    effective_status = "FAILED"
        item = proposal.get("item") or {}
        files = staging_files.get(staging.id, [])
        total_bytes = int(stats.total_bytes) if stats else 0
        verified_bytes = int(stats.verified_bytes) if stats else 0
        uploads.append(WorkspaceUploadResponse(
            id=staging.id,
            status=effective_status,
            operation=staging.operation,
            snapshot_version=staging.snapshot_version,
            item_number=item.get("item_number"),
            item_name=item.get("name"),
            item_id=result.get("item_id") or destination.get("item_id"),
            revision_id=revision_id,
            primary_filename=primary_file(files),
            file_count=int(stats.file_count) if stats else 0,
            verified_file_count=int(stats.verified_file_count) if stats else 0,
            total_bytes=total_bytes,
            verified_bytes=verified_bytes,
            progress_percent=int(workflow.get("progress_percent") or (
                round(verified_bytes * 100 / total_bytes) if total_bytes else 0
            )),
            workflow_state=workflow.get("state"),
            workflow_stage=workflow.get("stage"),
            workflow_error_detail=workflow.get("error_detail"),
            can_cancel=not is_finished and staging.status != "COMMITTING",
            can_resume=not is_finished and staging.status != "COMMITTING",
            can_edit=(
                not is_finished
                and staging.status != "COMMITTING"
                and workflow_state not in {"QUEUED", "RUNNING"}
            ),
            is_finished=is_finished,
            is_archived=workspace_state == "ARCHIVED",
            created_at=staging.created_at,
            updated_at=staging.updated_at,
        ))

    bundle_rows = list(db.execute(
        select(DownloadBundle, ProcessingJob, Revision, Item)
        .join(ProcessingJob, ProcessingJob.id == DownloadBundle.processing_job_id)
        .join(Revision, Revision.id == DownloadBundle.revision_id)
        .join(Item, Item.id == Revision.part_id)
        .where(
            DownloadBundle.created_by == user.id,
            DownloadBundle.workspace_state != "REMOVED",
        )
        .order_by(DownloadBundle.created_at.desc())
    ))
    for bundle, job, revision, item in bundle_rows:
        statistics = (job.diagnostics_json or {}).get("statistics") or {}
        file_count = int(statistics.get("files") or 0)
        total_bytes = int(statistics.get("bytes") or 0)
        is_ready = bundle.state == "READY" and bundle.artifact_id is not None
        uploads.append(WorkspaceUploadResponse(
            id=bundle.id,
            status=bundle.state,
            operation="DOWNLOAD_CAD_FILES",
            snapshot_version=1,
            item_number=item.part_number,
            item_name=item.name,
            item_id=item.id,
            revision_id=revision.id,
            primary_filename=f"{item.part_number}-{revision.revision_code}-cad-files.zip",
            file_count=file_count,
            verified_file_count=file_count if is_ready else 0,
            total_bytes=total_bytes,
            verified_bytes=total_bytes if is_ready else 0,
            progress_percent=job.progress_percent,
            workflow_state=job.state,
            workflow_stage=job.stage,
            workflow_error_detail=bundle.error_detail or job.error_detail,
            can_cancel=False,
            can_resume=is_ready,
            can_edit=False,
            is_finished=bundle.state in {"READY", "FAILED"},
            is_archived=bundle.workspace_state == "ARCHIVED",
            created_at=bundle.created_at,
            updated_at=bundle.completed_at or bundle.created_at,
            download_url=(f"/download-bundles/{bundle.id}/download" if is_ready else None),
        ))

    uploads.sort(key=lambda row: row.updated_at, reverse=True)

    return WorkspaceResponse(
        checked_out=[
            WorkspaceItemResponse(
                **item_response(
                    item,
                    "CHECKED_OUT_BY_ME",
                    revisions_by_id.get(item.current_revision_id),
                ).model_dump(),
                workspace_revision_id=checkout_by_item[item.id].revision_id,
                workspace_revision_code=(
                    workspace_revision.revision_code
                    if (workspace_revision := revisions_by_id.get(
                        checkout_by_item[item.id].revision_id
                    ))
                    else None
                ),
                checkout_acquired_at=checkout_by_item[item.id].acquired_at,
                checkout_expires_at=checkout_by_item[item.id].expires_at,
                checkout_scope=checkout_by_item[item.id].scope,
            )
            for item in items
        ],
        uploads=uploads,
    )


@router.get("/library-users", response_model=list[LibraryUserResponse])
def library_users(db: Db, principal: Actor) -> list[LibraryUserResponse]:
    """Return active users for Library ownership and checkout filters."""
    user_for_principal(db, principal)
    users = db.scalars(
        select(User)
        .where(User.is_active.is_(True))
        .order_by(User.display_name, User.external_subject)
    )
    return [
        LibraryUserResponse(
            id=user.id,
            username=user.external_subject,
            display_name=user.display_name,
        )
        for user in users
    ]


@router.get("/home", response_model=HomeResponse)
def home(db: Db, principal: Actor) -> HomeResponse:
    user = user_for_principal(db, principal)
    recent_rows = list(
        db.scalars(
            select(RecentlyViewedItem)
            .where(RecentlyViewedItem.user_id == user.id)
            .order_by(RecentlyViewedItem.viewed_at.desc())
            .limit(6)
        )
    )
    favorite_rows = list(
        db.scalars(
            select(Favorite)
            .where(Favorite.user_id == user.id)
            .order_by(Favorite.created_at.desc())
            .limit(6)
        )
    )
    activity = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.actor_id == user.id)
            .order_by(AuditLog.occurred_at.desc())
            .limit(10)
        )
    )
    return HomeResponse(
        recent=[
            item_response(item) for item in visible_items(db, [row.item_id for row in recent_rows])
        ],
        favorites=[
            item_response(item)
            for item in visible_items(db, [row.item_id for row in favorite_rows])
        ],
        activity=[
            ActivityResponse(
                action=row.action,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                occurred_at=row.occurred_at,
            )
            for row in activity
        ],
    )
