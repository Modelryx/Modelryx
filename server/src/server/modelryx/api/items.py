import json
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.api.parts import require_engineer
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import (
    Artifact,
    ArtifactFile,
    AuditLog,
    BomLine,
    BomViewRevision,
    Checkout,
    Favorite,
    Item,
    ProcessingJob,
    RecentlyViewedItem,
    Revision,
    RevisionApprovalRequest,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    User,
)
from modelryx.models.schemas import (
    ItemCreate,
    ItemHistoryEntry,
    ItemIdentityResolve,
    ItemListResponse,
    ItemResponse,
    ItemRevisionResponse,
    ItemSearchItem,
    ItemSearchResponse,
    ItemUpdate,
    RevisionCopyCreate,
    RevisionCreate,
)
from modelryx.services.storage import LocalStorageProvider

router = APIRouter(prefix="/api/v1", tags=["items"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


def item_response(
    item: Item,
    checkout_status: str = "AVAILABLE",
    current_revision: Revision | None = None,
) -> ItemResponse:
    return ItemResponse(
        id=item.id,
        item_number=item.part_number,
        name=item.name,
        description=item.description,
        category=item.category,
        status=item.status,
        item_type=item.part_type,
        current_revision_id=item.current_revision_id,
        current_revision_code=current_revision.revision_code if current_revision else None,
        cad_system=current_revision.cad_system if current_revision else None,
        created_by=item.created_by,
        created_at=item.created_at,
        updated_at=item.updated_at,
        checkout_status=checkout_status,
    )


def item_revision_response(
    revision: Revision, checkout_status: str = "AVAILABLE", db: Session | None = None
) -> ItemRevisionResponse:
    approval = None
    approval_status = None
    if db is not None:
        approval = db.scalar(
            select(RevisionApprovalRequest)
            .where(RevisionApprovalRequest.revision_id == revision.id)
            .order_by(RevisionApprovalRequest.requested_at.desc())
        )
        if approval is not None:
            approval_status = approval.status
            if approval.status == "PENDING":
                latest_snapshot_id = db.scalar(
                    select(RevisionContentSnapshot.id)
                    .where(RevisionContentSnapshot.revision_id == revision.id)
                    .order_by(RevisionContentSnapshot.sequence.desc())
                )
                if latest_snapshot_id is not None and approval.snapshot_id != latest_snapshot_id:
                    approval_status = "STALE"
    return ItemRevisionResponse(
        id=revision.id,
        item_id=revision.part_id,
        revision_code=revision.revision_code,
        description=revision.description,
        cad_system=revision.cad_system,
        native_format=revision.native_format,
        metadata_json=revision.metadata_json,
        derived_metadata_json=revision.derived_metadata_json,
        state=revision.state,
        lifecycle_state=revision.lifecycle_state,
        metadata_status=revision.metadata_status,
        created_by=revision.created_by,
        created_at=revision.created_at,
        committed_at=revision.committed_at,
        released_by=revision.released_by,
        released_at=revision.released_at,
        current_metadata_extraction_id=revision.current_metadata_extraction_id,
        checkout_status=checkout_status,
        approval_status=approval_status,
        approval_request_id=approval.id if approval else None,
        approval_snapshot_id=approval.snapshot_id if approval else None,
    )


def revision_checkout_status(db: Session, revision_id: uuid.UUID, principal: Principal) -> str:
    checkout = db.scalar(select(Checkout).where(Checkout.revision_id == revision_id))
    if checkout is None or checkout.expires_at <= datetime.now(UTC):
        return "AVAILABLE"
    user = user_for_principal(db, principal)
    return "CHECKED_OUT_BY_ME" if checkout.owner_id == user.id else "CHECKED_OUT_BY_OTHER"


def get_item_or_404(db: Session, item_id: uuid.UUID) -> Item:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.post("/items", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_item(payload: ItemCreate, request: Request, db: Db, principal: Actor) -> ItemResponse:
    require_engineer(principal)
    if (
        db.scalar(
            select(Item.id).where(
                func.lower(Item.part_number) == payload.item_number.strip().lower()
            )
        )
        is not None
    ):
        raise HTTPException(
            status_code=409, detail="Item number already exists. Enter a different number."
        )
    user = user_for_principal(db, principal)
    item = Item(
        part_number=payload.item_number.strip(),
        name=payload.name.strip(),
        description=payload.description,
        category=payload.category,
        part_type=payload.item_type,
        created_by=user.id,
    )
    db.add(item)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Item number already exists") from exc
    record_audit(
        db,
        actor_id=user.id,
        action="item.created",
        entity_type="item",
        entity_id=item.id,
        request_id=request.state.request_id,
        details={"item_number": item.part_number, "item_type": item.part_type},
    )
    db.commit()
    db.refresh(item)
    return item_response(item)


@router.get("/items", response_model=ItemListResponse)
def list_items(
    db: Db,
    principal: Actor,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_incomplete: bool = False,
    include_archived: bool = False,
    item_type: Annotated[str | None, Query()] = None,
    item_number_regex: Annotated[str | None, Query(max_length=100)] = None,
    name_regex: Annotated[str | None, Query(max_length=100)] = None,
    cad_system: Annotated[str | None, Query(max_length=100)] = None,
    item_status: Annotated[str | None, Query()] = None,
    item_types: Annotated[list[str] | None, Query()] = None,
    cad_systems: Annotated[list[str] | None, Query()] = None,
    item_statuses: Annotated[list[str] | None, Query()] = None,
    include_facets: bool = False,
) -> ItemListResponse:
    ready = Item.current_revision_id.in_(
        select(Revision.id).where(Revision.state.in_(["READY", "READY_WITH_WARNINGS"]))
    )
    if include_archived and principal.role != "administrator":
        raise HTTPException(status_code=403, detail="Administrator role required")
    predicate = (
        Item.status.in_(["ACTIVE", "ARCHIVED"]) if include_archived else Item.status == "ACTIVE"
    )
    if not include_incomplete:
        predicate &= ready
    elif principal.role != "administrator":
        user = user_for_principal(db, principal)
        predicate &= ready | (Item.created_by == user.id)
    if item_type:
        predicate &= func.lower(Item.part_type) == item_type.lower()
    if item_status:
        predicate &= func.lower(Item.status) == item_status.lower()
    for pattern, column, label in (
        (item_number_regex, Item.part_number, "item number"),
        (name_regex, Item.name, "name"),
    ):
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise HTTPException(
                    status_code=422, detail=f"Invalid {label} regular expression: {exc}"
                ) from exc
            predicate &= column.regexp_match(pattern, flags="i")
    if cad_system:
        predicate &= Item.current_revision_id.in_(
            select(Revision.id).where(func.lower(Revision.cad_system) == cad_system.lower())
        )
    # Facets reflect the permitted, text-filtered population, before the multi-select
    # filters, so options remain available across pages and can be reselected.
    facet_predicate = predicate
    if item_types is not None:
        predicate &= func.lower(Item.part_type).in_([value.lower() for value in item_types])
    if item_statuses is not None:
        predicate &= func.lower(Item.status).in_([value.lower() for value in item_statuses])
    if cad_systems is not None:
        named = [value.lower() for value in cad_systems if value]
        matches = [
            Item.current_revision_id.in_(
                select(Revision.id).where(func.lower(Revision.cad_system).in_(named))
            )
        ]
        if "" in cad_systems:
            matches.append(Item.current_revision_id.is_(None))
            matches.append(
                Item.current_revision_id.in_(
                    select(Revision.id).where(
                        or_(Revision.cad_system.is_(None), Revision.cad_system == "")
                    )
                )
            )
        predicate &= or_(*matches)
    facets = None
    if include_facets:
        facets = {
            "item_types": list(
                db.scalars(select(Item.part_type).where(facet_predicate).distinct())
            ),
            "item_statuses": list(
                db.scalars(select(Item.status).where(facet_predicate).distinct())
            ),
            "cad_systems": list(
                db.scalars(
                    select(Revision.cad_system)
                    .select_from(Item)
                    .outerjoin(Revision, Item.current_revision_id == Revision.id)
                    .where(facet_predicate)
                    .distinct()
                )
            ),
        }
    total = db.scalar(select(func.count()).select_from(Item).where(predicate)) or 0
    ordering = Item.updated_at.desc() if include_archived else Item.part_number
    rows = list(
        db.scalars(select(Item).where(predicate).order_by(ordering).limit(limit).offset(offset))
    )
    user = user_for_principal(db, principal)
    checkout_by_item: dict[uuid.UUID, list[Checkout]] = {}
    if rows:
        for row in db.scalars(
            select(Checkout).where(
                Checkout.part_id.in_([item.id for item in rows]),
                Checkout.expires_at > func.now(),
            )
        ):
            checkout_by_item.setdefault(row.part_id, []).append(row)
    revisions_by_id = {
        revision.id: revision
        for revision in db.scalars(
            select(Revision).where(
                Revision.id.in_(
                    [item.current_revision_id for item in rows if item.current_revision_id]
                )
            )
        )
    }
    responses = []
    for item in rows:
        item_checkouts = checkout_by_item.get(item.id, [])
        checkout_status = "AVAILABLE"
        if any(checkout.owner_id == user.id for checkout in item_checkouts):
            checkout_status = "CHECKED_OUT_BY_ME"
        elif item_checkouts:
            checkout_status = "CHECKED_OUT_BY_OTHER"
        responses.append(
            item_response(item, checkout_status, revisions_by_id.get(item.current_revision_id))
        )
    return ItemListResponse(items=responses, total=total, limit=limit, offset=offset, facets=facets)


@router.get("/items/favorites", response_model=ItemSearchResponse)
def list_favorite_items(db: Db, principal: Actor) -> ItemSearchResponse:
    """Return the current user's favorites in the shape consumed by item lists."""
    user = user_for_principal(db, principal)
    rows = db.execute(
        select(Item, Revision)
        .join(Favorite, Favorite.item_id == Item.id)
        .outerjoin(Revision, Revision.id == Item.current_revision_id)
        .where(Favorite.user_id == user.id, Item.status == "ACTIVE")
        .order_by(Favorite.created_at.desc())
    ).all()
    items = [
        ItemSearchItem(
            item=item_response(item),
            current_revision=item_revision_response(revision, db=db) if revision else None,
        )
        for item, revision in rows
    ]
    return ItemSearchResponse(items=items, total=len(items), limit=len(items), offset=0)


@router.get("/items/{item_id}", response_model=ItemResponse)
def read_item(item_id: uuid.UUID, db: Db, principal: Actor) -> ItemResponse:
    item = get_item_or_404(db, item_id)
    if item.status == "ARCHIVED":
        raise HTTPException(status_code=404, detail="Item not found")
    revision = db.get(Revision, item.current_revision_id) if item.current_revision_id else None
    if revision is None or revision.state not in {"READY", "READY_WITH_WARNINGS"}:
        user = user_for_principal(db, principal)
        if principal.role != "administrator" and item.created_by != user.id:
            raise HTTPException(status_code=404, detail="Item not found")
    checkout = db.scalar(
        select(Checkout).where(
            Checkout.part_id == item_id,
            Checkout.revision_id == item.current_revision_id,
            Checkout.expires_at > func.now(),
        )
    )
    checkout_status = "AVAILABLE"
    if checkout:
        user = user_for_principal(db, principal)
        checkout_status = (
            "CHECKED_OUT_BY_ME" if checkout.owner_id == user.id else "CHECKED_OUT_BY_OTHER"
        )
    user = user_for_principal(db, principal)
    recent = db.get(RecentlyViewedItem, {"user_id": user.id, "item_id": item.id})
    if recent is None:
        db.add(RecentlyViewedItem(user_id=user.id, item_id=item.id))
    else:
        recent.viewed_at = func.now()
    db.commit()
    return item_response(item, checkout_status, revision)


@router.patch("/items/{item_id}", response_model=ItemResponse)
def update_item(
    item_id: uuid.UUID, payload: ItemUpdate, request: Request, db: Db, principal: Actor
) -> ItemResponse:
    require_engineer(principal)
    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes and principal.role != "administrator":
        raise HTTPException(status_code=403, detail="Administrator role required to archive items")
    item = get_item_or_404(db, item_id)
    changed_values = {
        field: {"before": getattr(item, field), "after": value}
        for field, value in changes.items()
        if getattr(item, field) != value
    }
    if not changed_values:
        return item_response(item)
    for field, value in changes.items():
        setattr(item, field, value)
    user = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=user.id,
        action="item.updated",
        entity_type="item",
        entity_id=item.id,
        request_id=request.state.request_id,
        details={"fields": sorted(changed_values), "changes": changed_values},
    )
    db.commit()
    db.refresh(item)
    return item_response(item)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: uuid.UUID, request: Request, db: Db, principal: Actor) -> None:
    """Soft-delete an item while preserving its controlled history and files."""
    if principal.role != "administrator":
        raise HTTPException(status_code=403, detail="Administrator role required")
    item = get_item_or_404(db, item_id)
    if item.status == "ARCHIVED":
        return
    item.status = "ARCHIVED"
    user = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=user.id,
        action="item.deleted",
        entity_type="item",
        entity_id=item.id,
        request_id=request.state.request_id,
        details={"item_number": item.part_number, "deletion": "soft"},
    )
    db.commit()


@router.post("/items/{item_id}/resolve-identity", response_model=ItemResponse)
def resolve_item_identity(
    item_id: uuid.UUID,
    payload: ItemIdentityResolve,
    request: Request,
    db: Db,
    principal: Actor,
) -> ItemResponse:
    require_engineer(principal)
    item = get_item_or_404(db, item_id)
    user = user_for_principal(db, principal)
    if principal.role != "administrator" and item.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can resolve this identity")
    if not item.part_number.startswith("AUTO-"):
        raise HTTPException(status_code=409, detail="Item identity is not provisional")
    old_number = item.part_number
    item.part_number = payload.item_number.strip()
    if payload.name is not None:
        item.name = payload.name.strip()
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Item number already exists") from exc
    record_audit(
        db,
        actor_id=user.id,
        action="item.identity_resolved",
        entity_type="item",
        entity_id=item.id,
        request_id=request.state.request_id,
        details={"old_item_number": old_number, "new_item_number": item.part_number},
    )
    db.commit()
    db.refresh(item)
    return item_response(item)


@router.post(
    "/items/{item_id}/revisions",
    response_model=ItemRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_item_revision(
    item_id: uuid.UUID, payload: RevisionCreate, request: Request, db: Db, principal: Actor
) -> ItemRevisionResponse:
    require_engineer(principal)
    get_item_or_404(db, item_id)
    user = user_for_principal(db, principal)
    revision = Revision(
        part_id=item_id,
        revision_code=payload.revision_code,
        cad_system=payload.cad_system,
        native_format=payload.native_format,
        metadata_json=payload.metadata,
        search_text=json.dumps(payload.metadata, sort_keys=True),
        state="DRAFT",
        created_by=user.id,
    )
    db.add(revision)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Revision code already exists for Item"
        ) from exc
    record_audit(
        db,
        actor_id=user.id,
        action="revision.created",
        entity_type="revision",
        entity_id=revision.id,
        request_id=request.state.request_id,
        details={"item_id": str(item_id), "revision_code": revision.revision_code},
    )
    db.commit()
    db.refresh(revision)
    return item_revision_response(revision, db=db)


@router.post(
    "/items/{item_id}/revisions/{revision_id}/release",
    response_model=ItemRevisionResponse,
)
def release_item_revision(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> ItemRevisionResponse:
    """Release the current ready revision and freeze its authoritative content."""
    require_engineer(principal)
    item = get_item_or_404(db, item_id)
    revision = db.scalar(select(Revision).where(Revision.id == revision_id).with_for_update())
    if revision is None or revision.part_id != item.id:
        raise HTTPException(status_code=404, detail="Item revision not found")
    actor = user_for_principal(db, principal)
    if revision.created_by != actor.id and not principal.has_role("reviewer"):
        raise HTTPException(
            status_code=403, detail="Only the revision creator or a reviewer can release it"
        )
    if revision.lifecycle_state == "RELEASED":
        checkout_status = revision_checkout_status(db, revision.id, principal)
        return item_revision_response(revision, checkout_status, db)
    if revision.lifecycle_state != "WORKING":
        raise HTTPException(status_code=409, detail="Only a working revision can be released")
    if item.current_revision_id != revision.id:
        raise HTTPException(
            status_code=409, detail="Only the current Item Revision can be released"
        )
    if revision.state not in {"READY", "READY_WITH_WARNINGS"}:
        raise HTTPException(
            status_code=409, detail="Revision processing must finish before release"
        )
    active_checkout = db.scalar(
        select(Checkout.id).where(
            Checkout.revision_id == revision.id,
            Checkout.expires_at > datetime.now(UTC),
        )
    )
    if active_checkout is not None:
        raise HTTPException(
            status_code=409, detail="Check in or cancel the checkout before release"
        )
    snapshot = db.scalar(
        select(RevisionContentSnapshot)
        .where(RevisionContentSnapshot.revision_id == revision.id)
        .order_by(RevisionContentSnapshot.sequence.desc())
    )
    if snapshot is None:
        raise HTTPException(status_code=409, detail="Revision has no committed content snapshot")
    if settings.require_release_approval:
        approved = db.scalar(
            select(RevisionApprovalRequest.id).where(
                RevisionApprovalRequest.revision_id == revision.id,
                RevisionApprovalRequest.snapshot_id == snapshot.id,
                RevisionApprovalRequest.status == "APPROVED",
            )
        )
        if approved is None:
            raise HTTPException(
                status_code=409,
                detail="Current revision content snapshot requires approval before release",
            )
    invalid_authoritative = db.scalar(
        select(Artifact.id)
        .join(RevisionArtifactRelation, RevisionArtifactRelation.artifact_id == Artifact.id)
        .join(
            RevisionContentSnapshotArtifact,
            RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id,
        )
        .join(ArtifactFile, ArtifactFile.artifact_id == Artifact.id)
        .where(
            RevisionContentSnapshotArtifact.snapshot_id == snapshot.id,
            Artifact.classification == "AUTHORITATIVE",
            ArtifactFile.state != "VERIFIED",
        )
        .limit(1)
    )
    if invalid_authoritative is not None:
        raise HTTPException(status_code=409, detail="All authoritative files must be verified")
    now = datetime.now(UTC)
    for previous in db.scalars(
        select(Revision).where(
            Revision.part_id == item.id,
            Revision.id != revision.id,
            Revision.lifecycle_state == "RELEASED",
        )
    ):
        previous.lifecycle_state = "SUPERSEDED"
    revision.lifecycle_state = "RELEASED"
    revision.released_by = actor.id
    revision.released_at = now
    record_audit(
        db,
        actor_id=actor.id,
        action="revision.released",
        entity_type="revision",
        entity_id=revision.id,
        request_id=request.state.request_id,
        details={"item_id": str(item.id), "snapshot_id": str(snapshot.id)},
    )
    db.commit()
    db.refresh(revision)
    return item_revision_response(revision, db=db)


@router.post(
    "/items/{item_id}/revisions/copy",
    response_model=ItemRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def copy_item_revision(
    item_id: uuid.UUID,
    payload: RevisionCopyCreate,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> ItemRevisionResponse:
    """Revise an Item using relation-specific copy, reuse, and regeneration rules."""
    require_engineer(principal)
    item = get_item_or_404(db, item_id)
    source = db.get(Revision, payload.source_revision_id)
    if source is None or source.part_id != item_id:
        raise HTTPException(status_code=404, detail="Source revision not found")
    if source.state not in {"READY", "READY_WITH_WARNINGS"}:
        raise HTTPException(status_code=409, detail="Only a ready revision can be copied")
    source_snapshot = db.scalar(
        select(RevisionContentSnapshot)
        .where(RevisionContentSnapshot.revision_id == source.id)
        .order_by(RevisionContentSnapshot.sequence.desc())
    )
    if source_snapshot is None:
        raise HTTPException(status_code=409, detail="Source revision has no content snapshot")
    user = user_for_principal(db, principal)
    now = datetime.now(UTC)
    storage = LocalStorageProvider(settings.storage_root)
    copied_storage_keys: list[str] = []
    policy_counts = {"copied": 0, "reused": 0, "regenerate": 0, "omitted": 0}
    revision = Revision(
        part_id=item_id,
        revision_code=payload.revision_code,
        cad_system=source.cad_system,
        native_format=source.native_format,
        metadata_json=source.metadata_json,
        search_text=source.search_text,
        package_sha256=source.package_sha256,
        state="READY_WITH_WARNINGS",
        metadata_status="UNAVAILABLE",
        created_by=user.id,
        committed_at=now,
    )
    db.add(revision)
    try:
        db.flush()
        snapshot = RevisionContentSnapshot(
            revision_id=revision.id,
            sequence=1,
            commit_id=uuid.uuid4(),
            created_by=user.id,
        )
        db.add(snapshot)
        db.flush()
        if source_snapshot is not None:
            source_relations = list(
                db.scalars(
                    select(RevisionArtifactRelation)
                    .join(
                        RevisionContentSnapshotArtifact,
                        RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id,
                    )
                    .where(RevisionContentSnapshotArtifact.snapshot_id == source_snapshot.id)
                    .order_by(RevisionContentSnapshotArtifact.sequence)
                )
            )
            target_sequence = 0
            copied_primary_file: ArtifactFile | None = None
            for source_relation in source_relations:
                source_artifact = db.get(Artifact, source_relation.artifact_id)
                if source_artifact is None:
                    continue
                if source_relation.relation_type in {"REPRESENTATION", "THUMBNAIL"}:
                    policy_counts["regenerate"] += 1
                    continue
                if source_relation.relation_type == "ANALYSIS":
                    policy_counts["omitted"] += 1
                    continue

                target_artifact = source_artifact
                if source_relation.relation_type in {"SPECIFICATION", "DRAWING"}:
                    latest_generation = (
                        db.scalar(
                            select(func.max(Artifact.generation)).where(
                                Artifact.artifact_key == source_artifact.artifact_key
                            )
                        )
                        or 0
                    )
                    target_artifact = Artifact(
                        artifact_key=source_artifact.artifact_key,
                        attachment_id=source_artifact.attachment_id,
                        generation=latest_generation + 1,
                        artifact_type=source_artifact.artifact_type,
                        name=source_artifact.name,
                        description=source_artifact.description,
                        comment=source_artifact.comment,
                        classification=source_artifact.classification,
                        source=source_artifact.source,
                        derived_from_artifact_id=source_artifact.derived_from_artifact_id,
                        generator=source_artifact.generator,
                        generator_version=source_artifact.generator_version,
                        state="READY",
                        created_by=user.id,
                        ready_at=now,
                    )
                    db.add(target_artifact)
                    db.flush()
                    record_audit(
                        db,
                        actor_id=user.id,
                        action="artifact.generation_created",
                        entity_type="artifact",
                        entity_id=target_artifact.id,
                        request_id=request.state.request_id,
                        details={
                            "source_artifact_id": str(source_artifact.id),
                            "artifact_key": str(target_artifact.artifact_key),
                            "generation": target_artifact.generation,
                            "revision_id": str(revision.id),
                        },
                    )
                    for file_index, source_file in enumerate(
                        db.scalars(
                            select(ArtifactFile)
                            .where(ArtifactFile.artifact_id == source_artifact.id)
                            .order_by(ArtifactFile.created_at, ArtifactFile.id)
                        )
                    ):
                        safe_name = Path(source_file.original_filename).name
                        storage_key = (
                            f"items/{item.id}/revisions/{revision.id}/artifacts/"
                            f"{target_artifact.id}/{file_index}-{safe_name}"
                        )
                        source_path = storage.path_for(source_file.storage_key)
                        if not source_path.is_file():
                            raise HTTPException(
                                status_code=409,
                                detail=f"Source Artifact file is missing: {safe_name}",
                            )
                        target_path = storage.create_upload(storage_key)
                        shutil.copy2(source_path, target_path)
                        copied_storage_keys.append(storage_key)
                        copied_file = ArtifactFile(
                            artifact_id=target_artifact.id,
                            reference_name=source_file.reference_name,
                            role=source_file.role,
                            revision_id=revision.id,
                            kind=source_file.kind,
                            storage_key=storage_key,
                            original_filename=source_file.original_filename,
                            mime_type=source_file.mime_type,
                            byte_size=source_file.byte_size,
                            sha256=source_file.sha256,
                            state="VERIFIED",
                            required=source_file.required,
                        )
                        db.add(copied_file)
                        if source_relation.is_primary and source_file.role == "PRIMARY":
                            copied_primary_file = copied_file
                    policy_counts["copied"] += 1
                else:
                    policy_counts["reused"] += 1

                relation = RevisionArtifactRelation(
                    revision_id=revision.id,
                    artifact_id=target_artifact.id,
                    relation_type=source_relation.relation_type,
                    sequence=source_relation.sequence,
                    is_primary=source_relation.is_primary,
                    created_by=user.id,
                )
                db.add(relation)
                db.flush()
                db.add(
                    RevisionContentSnapshotArtifact(
                        snapshot_id=snapshot.id,
                        relation_id=relation.id,
                        sequence=target_sequence,
                    )
                )
                target_sequence += 1
            source_bom = db.scalar(
                select(BomViewRevision).where(BomViewRevision.snapshot_id == source_snapshot.id)
            )
            if source_bom is not None:
                copied_bom = BomViewRevision(
                    revision_id=revision.id,
                    snapshot_id=snapshot.id,
                    sequence=1,
                    created_by=user.id,
                )
                db.add(copied_bom)
                db.flush()
                for index, line in enumerate(
                    db.scalars(
                        select(BomLine)
                        .where(BomLine.bom_view_revision_id == source_bom.id)
                        .order_by(BomLine.sequence, BomLine.id)
                    )
                ):
                    db.add(
                        BomLine(
                            bom_view_revision_id=copied_bom.id,
                            parent_revision_id=revision.id,
                            child_revision_id=line.child_revision_id,
                            occurrence_key=line.occurrence_key,
                            source_reference=line.source_reference,
                            reference_designator=line.reference_designator,
                            transform=line.transform,
                            is_suppressed=line.is_suppressed,
                            is_visible=line.is_visible,
                            properties_json=line.properties_json,
                            quantity=line.quantity,
                            sequence=index,
                        )
                    )
        copied_extension = (
            Path(copied_primary_file.original_filename).suffix.casefold()
            if copied_primary_file is not None
            else ""
        )
        copied_processing_type = {
            ".fcstd": "FCSTD_TO_MODELRYX",
            ".step": "STEP_TO_MODELRYX",
            ".stp": "STEP_TO_MODELRYX",
        }.get(copied_extension)
        if copied_primary_file is not None and copied_extension not in {".step", ".stp"}:
            from modelryx.services.external_workers import (
                JOB_TYPE,
                AmbiguousWorkerError,
                matching_worker,
            )

            try:
                if matching_worker(db, copied_primary_file.original_filename, revision.cad_system):
                    copied_processing_type = JOB_TYPE
            except AmbiguousWorkerError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        if copied_primary_file is not None and copied_processing_type is not None:
            revision.state = "PROCESSING"
            db.add(
                ProcessingJob(
                    revision_id=revision.id,
                    snapshot_id=snapshot.id,
                    type=copied_processing_type,
                    state="PENDING",
                    attempt_count=0,
                    idempotency_key=(f"{copied_extension.lstrip('.')}-preview:{snapshot.id}"),
                    available_at=now,
                )
            )
        item.current_revision_id = revision.id
        record_audit(
            db,
            actor_id=user.id,
            action="revision.copied",
            entity_type="revision",
            entity_id=revision.id,
            request_id=request.state.request_id,
            details={
                "item_id": str(item_id),
                "source_revision_id": str(source.id),
                "source_revision_code": source.revision_code,
                "revision_code": revision.revision_code,
                "source_snapshot_id": str(source_snapshot.id),
                "artifact_policy": policy_counts,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        for storage_key in copied_storage_keys:
            storage.delete(storage_key)
        raise HTTPException(
            status_code=409, detail="Revision code already exists for Item"
        ) from exc
    except Exception:
        db.rollback()
        for storage_key in copied_storage_keys:
            storage.delete(storage_key)
        raise
    db.refresh(revision)
    return item_revision_response(revision, db=db)


@router.get("/items/{item_id}/revisions", response_model=list[ItemRevisionResponse])
def list_item_revisions(item_id: uuid.UUID, db: Db, principal: Actor) -> list[ItemRevisionResponse]:
    item = get_item_or_404(db, item_id)
    can_see_incomplete = principal.role == "administrator"
    if not can_see_incomplete:
        can_see_incomplete = item.created_by == user_for_principal(db, principal).id
    predicate = Revision.part_id == item_id
    if not can_see_incomplete:
        predicate &= Revision.state.in_(["READY", "READY_WITH_WARNINGS"])
    rows = list(db.scalars(select(Revision).where(predicate).order_by(Revision.created_at.desc())))
    checkouts = {
        checkout.revision_id: checkout
        for checkout in db.scalars(
            select(Checkout).where(
                Checkout.part_id == item_id,
                Checkout.expires_at > func.now(),
            )
        )
    }
    user = user_for_principal(db, principal)
    return [
        item_revision_response(
            row,
            "CHECKED_OUT_BY_ME"
            if (checkout := checkouts.get(row.id)) and checkout.owner_id == user.id
            else "CHECKED_OUT_BY_OTHER"
            if checkout
            else "AVAILABLE",
            db,
        )
        for row in rows
    ]


@router.get("/items/{item_id}/history", response_model=list[ItemHistoryEntry])
def list_item_history(item_id: uuid.UUID, db: Db, principal: Actor) -> list[ItemHistoryEntry]:
    get_item_or_404(db, item_id)
    revision_ids = select(Revision.id).where(Revision.part_id == item_id)
    rows = db.execute(
        select(AuditLog, User.display_name)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where((AuditLog.entity_id == item_id) | (AuditLog.entity_id.in_(revision_ids)))
        .order_by(AuditLog.occurred_at.desc())
    ).all()
    return [
        ItemHistoryEntry(
            id=audit.id,
            action=audit.action,
            changed_by=display_name or "System",
            details=audit.details_json,
            occurred_at=audit.occurred_at,
        )
        for audit, display_name in rows
    ]


def readable_item_revision(
    db: Session, item_id: uuid.UUID, revision_id: uuid.UUID, principal: Principal
) -> Revision:
    item = get_item_or_404(db, item_id)
    revision = db.get(Revision, revision_id)
    if revision is None or revision.part_id != item_id:
        raise HTTPException(status_code=404, detail="Item revision not found")
    if revision.state not in {"READY", "READY_WITH_WARNINGS"}:
        user = user_for_principal(db, principal)
        if principal.role != "administrator" and item.created_by != user.id:
            raise HTTPException(status_code=404, detail="Item revision not found")
    return revision


@router.get("/items/{item_id}/revisions/{revision_id}", response_model=ItemRevisionResponse)
def read_item_revision(
    item_id: uuid.UUID, revision_id: uuid.UUID, db: Db, principal: Actor
) -> ItemRevisionResponse:
    revision = readable_item_revision(db, item_id, revision_id, principal)
    return item_revision_response(
        revision,
        revision_checkout_status(db, revision.id, principal),
        db,
    )


@router.get("/items/{item_id}/revisions/{revision_id}/action-availability")
def revision_action_availability(
    item_id: uuid.UUID, revision_id: uuid.UUID, db: Db, principal: Actor
) -> dict:
    from modelryx.api.revision_approvals import actor_is_eligible
    from modelryx.services.artifact_content import current_artifact_files
    from modelryx.services.external_workers import AmbiguousWorkerError, matching_worker

    revision = readable_item_revision(db, item_id, revision_id, principal)
    actor = user_for_principal(db, principal)
    snapshot = db.scalar(select(RevisionContentSnapshot).where(
        RevisionContentSnapshot.revision_id == revision.id
    ).order_by(RevisionContentSnapshot.sequence.desc()))
    approvals = list(db.scalars(select(RevisionApprovalRequest).where(
        RevisionApprovalRequest.revision_id == revision.id,
        RevisionApprovalRequest.snapshot_id == (snapshot.id if snapshot else None),
    )))
    assigned = any(actor_is_eligible(approval, actor.id, principal.role) for approval in approvals)
    can_review = any(approval.status == "PENDING" and actor_is_eligible(
        approval, actor.id, principal.role) for approval in approvals)
    source_files = current_artifact_files(
        db,
        revision.id,
        artifact_types={"CAD_MODEL", "CAD_ASSEMBLY", "DRAWING", "CAD_DRAWING", "STEP"},
    )
    filenames = [file.original_filename for file in source_files if file.role == "PRIMARY"]
    if not filenames:
        filenames = [file.original_filename for file in source_files]
    built_in = any(
        Path(name).suffix.casefold() in {".fcstd", ".step", ".stp"} for name in filenames
    )
    try:
        worker_configured = built_in or any(
            matching_worker(db, name, revision.cad_system) is not None for name in filenames
        )
    except AmbiguousWorkerError:
        worker_configured = False
    return {
        "is_creator": revision.created_by == actor.id,
        "is_assigned_reviewer": assigned,
        "can_review": can_review,
        "can_release": revision.created_by == actor.id or principal.has_role("reviewer"),
        "can_generate": principal.has_role("designer", "administrator"),
        "worker_configured": worker_configured,
    }


@router.get(
    "/items/{item_id}/revisions/{revision_id}/history",
    response_model=list[ItemHistoryEntry],
)
def list_item_revision_history(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
) -> list[ItemHistoryEntry]:
    readable_item_revision(db, item_id, revision_id, principal)
    rows = db.execute(
        select(AuditLog, User.display_name)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where(AuditLog.entity_id == revision_id)
        .order_by(AuditLog.occurred_at.desc())
    ).all()
    return [
        ItemHistoryEntry(
            id=audit.id,
            action=audit.action,
            changed_by=display_name or "System",
            details=audit.details_json,
            occurred_at=audit.occurred_at,
        )
        for audit, display_name in rows
    ]
