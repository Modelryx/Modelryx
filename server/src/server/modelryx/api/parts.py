import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.db import get_db
from modelryx.models import Checkout, Part, Revision
from modelryx.models.schemas import (
    PartCreate,
    PartListResponse,
    PartResponse,
    PartUpdate,
    ProvisionalIdentityResolve,
    RevisionCreate,
    RevisionResponse,
)

router = APIRouter(prefix="/api/v1", tags=["parts"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]


def get_part_or_404(db: Session, part_id: uuid.UUID) -> Part:
    part = db.get(Part, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")
    return part


def require_engineer(principal: Principal) -> None:
    if principal.role not in {"engineer", "reviewer", "administrator"}:
        raise HTTPException(status_code=403, detail="Engineer role required")


@router.post("/parts", response_model=PartResponse, status_code=status.HTTP_201_CREATED)
def create_part(payload: PartCreate, request: Request, db: Db, principal: Actor) -> Part:
    require_engineer(principal)
    user = user_for_principal(db, principal)
    part = Part(**payload.model_dump(), created_by=user.id)
    db.add(part)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Part number already exists") from exc
    record_audit(
        db,
        actor_id=user.id,
        action="part.created",
        entity_type="part",
        entity_id=part.id,
        request_id=request.state.request_id,
        details={"part_number": part.part_number},
    )
    db.commit()
    db.refresh(part)
    return part


@router.get("/parts", response_model=PartListResponse)
def list_parts(
    db: Db,
    principal: Actor,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_incomplete: bool = False,
) -> PartListResponse:
    predicate = Part.status == "ACTIVE"
    if not include_incomplete:
        predicate = predicate & Part.current_revision_id.in_(
            select(Revision.id).where(Revision.state.in_(["READY", "READY_WITH_WARNINGS"]))
        )
    elif principal.role != "administrator":
        user = user_for_principal(db, principal)
        predicate = predicate & (
            Part.current_revision_id.in_(
                select(Revision.id).where(Revision.state.in_(["READY", "READY_WITH_WARNINGS"]))
            )
            | (Part.created_by == user.id)
        )
    total = db.scalar(select(func.count()).select_from(Part).where(predicate)) or 0
    parts = list(
        db.scalars(
            select(Part).where(predicate).order_by(Part.part_number).limit(limit).offset(offset)
        )
    )
    now = func.now()
    checkouts = {
        item.part_id: item
        for item in db.scalars(
            select(Checkout).where(
                Checkout.part_id.in_([part.id for part in parts]), Checkout.expires_at > now
            )
        )
    }
    user = user_for_principal(db, principal)
    items = []
    for part in parts:
        checkout = checkouts.get(part.id)
        checkout_status = "AVAILABLE"
        if checkout is not None:
            checkout_status = (
                "CHECKED_OUT_BY_ME" if checkout.owner_id == user.id else "CHECKED_OUT_BY_OTHER"
            )
        items.append(
            PartResponse.model_validate(part).model_copy(
                update={"checkout_status": checkout_status}
            )
        )
    return PartListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/parts/{part_id}", response_model=PartResponse)
def read_part(part_id: uuid.UUID, db: Db, principal: Actor) -> PartResponse:
    part = get_part_or_404(db, part_id)
    if part.status == "ARCHIVED":
        raise HTTPException(status_code=404, detail="Part not found")
    revision = db.get(Revision, part.current_revision_id) if part.current_revision_id else None
    if revision is None or revision.state not in {"READY", "READY_WITH_WARNINGS"}:
        user = user_for_principal(db, principal)
        if principal.role != "administrator" and part.created_by != user.id:
            raise HTTPException(status_code=404, detail="Part not found")
    checkout = db.scalar(
        select(Checkout).where(
            Checkout.part_id == part.id,
            Checkout.revision_id == part.current_revision_id,
            Checkout.expires_at > func.now(),
        )
    )
    user = user_for_principal(db, principal)
    checkout_status = "AVAILABLE"
    if checkout is not None:
        checkout_status = (
            "CHECKED_OUT_BY_ME" if checkout.owner_id == user.id else "CHECKED_OUT_BY_OTHER"
        )
    return PartResponse.model_validate(part).model_copy(update={"checkout_status": checkout_status})


@router.patch("/parts/{part_id}", response_model=PartResponse)
def update_part(
    part_id: uuid.UUID, payload: PartUpdate, request: Request, db: Db, principal: Actor
) -> Part:
    require_engineer(principal)
    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes and principal.role != "administrator":
        raise HTTPException(status_code=403, detail="Administrator role required to archive parts")
    part = get_part_or_404(db, part_id)
    for field, value in changes.items():
        setattr(part, field, value)
    user = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=user.id,
        action="part.updated",
        entity_type="part",
        entity_id=part.id,
        request_id=request.state.request_id,
        details={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(part)
    return part


@router.post("/parts/{part_id}/resolve-identity", response_model=PartResponse)
def resolve_provisional_identity(
    part_id: uuid.UUID,
    payload: ProvisionalIdentityResolve,
    request: Request,
    db: Db,
    principal: Actor,
) -> Part:
    require_engineer(principal)
    part = get_part_or_404(db, part_id)
    user = user_for_principal(db, principal)
    if principal.role != "administrator" and part.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can resolve this identity")
    if not part.part_number.startswith("AUTO-"):
        raise HTTPException(status_code=409, detail="Part identity is not provisional")
    old_part_number = part.part_number
    part.part_number = payload.part_number
    if payload.name is not None:
        part.name = payload.name.strip()
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Part number already exists") from exc
    record_audit(
        db,
        actor_id=user.id,
        action="part.identity_resolved",
        entity_type="part",
        entity_id=part.id,
        request_id=request.state.request_id,
        details={
            "old_part_number": old_part_number,
            "new_part_number": part.part_number,
        },
    )
    db.commit()
    db.refresh(part)
    return part


@router.post(
    "/parts/{part_id}/revisions",
    response_model=RevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_revision(
    part_id: uuid.UUID, payload: RevisionCreate, request: Request, db: Db, principal: Actor
) -> Revision:
    require_engineer(principal)
    get_part_or_404(db, part_id)
    user = user_for_principal(db, principal)
    revision = Revision(
        part_id=part_id,
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
            status_code=409, detail="Revision code already exists for part"
        ) from exc
    record_audit(
        db,
        actor_id=user.id,
        action="revision.created",
        entity_type="revision",
        entity_id=revision.id,
        request_id=request.state.request_id,
        details={"part_id": str(part_id), "revision_code": revision.revision_code},
    )
    db.commit()
    db.refresh(revision)
    return revision


@router.get("/parts/{part_id}/revisions", response_model=list[RevisionResponse])
def list_revisions(part_id: uuid.UUID, db: Db, principal: Actor) -> list[Revision]:
    part = get_part_or_404(db, part_id)
    states = ["READY", "READY_WITH_WARNINGS"]
    can_see_incomplete = principal.role == "administrator"
    if not can_see_incomplete:
        user = user_for_principal(db, principal)
        can_see_incomplete = part.created_by == user.id
    predicate = Revision.part_id == part_id
    if not can_see_incomplete:
        predicate = predicate & Revision.state.in_(states)
    return list(db.scalars(select(Revision).where(predicate).order_by(Revision.created_at.desc())))


@router.get("/revisions/{revision_id}", response_model=RevisionResponse)
def read_revision(revision_id: uuid.UUID, db: Db, principal: Actor) -> Revision:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    if revision.state not in {"READY", "READY_WITH_WARNINGS"}:
        part = db.get(Part, revision.part_id)
        user = user_for_principal(db, principal)
        if principal.role != "administrator" and part.created_by != user.id:
            raise HTTPException(status_code=404, detail="Revision not found")
    return revision
