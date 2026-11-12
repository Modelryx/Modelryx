import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.db import get_db
from modelryx.models import ApprovalReviewerGroup, ApprovalReviewerGroupMember, User

router = APIRouter(prefix="/api/v1", tags=["approval-routing"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]


class ApprovalGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    member_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class ApprovalGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None
    member_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)


class ApprovalGroupResponse(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool
    member_ids: list[uuid.UUID]
    created_by: uuid.UUID
    created_at: datetime


class ApprovalGroupOption(BaseModel):
    id: uuid.UUID
    name: str
    eligible_member_count: int


def require_admin(principal: Principal) -> None:
    if principal.role != "administrator":
        raise HTTPException(status_code=403, detail="Administrator role required")


def validate_members(db: Session, member_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    unique_ids = list(dict.fromkeys(member_ids))
    if not unique_ids:
        return []
    eligible = set(
        db.scalars(
            select(User.id).where(
                User.id.in_(unique_ids),
                User.is_active.is_(True),
                User.role.in_(("reviewer", "administrator")),
            )
        )
    )
    if eligible != set(unique_ids):
        raise HTTPException(
            status_code=422,
            detail="Group members must be active reviewers or administrators",
        )
    return unique_ids


def group_response(db: Session, group: ApprovalReviewerGroup) -> ApprovalGroupResponse:
    member_ids = list(
        db.scalars(
            select(ApprovalReviewerGroupMember.user_id)
            .where(ApprovalReviewerGroupMember.group_id == group.id)
            .order_by(ApprovalReviewerGroupMember.user_id)
        )
    )
    return ApprovalGroupResponse(
        id=group.id, name=group.name, is_active=group.is_active,
        member_ids=member_ids, created_by=group.created_by, created_at=group.created_at,
    )


@router.get("/approval-groups", response_model=list[ApprovalGroupOption])
def list_active_group_options(db: Db, principal: Actor) -> list[ApprovalGroupOption]:
    del principal
    rows = db.execute(
        select(
            ApprovalReviewerGroup.id,
            ApprovalReviewerGroup.name,
            func.count(ApprovalReviewerGroupMember.user_id).filter(
                User.is_active.is_(True),
                User.role.in_(("reviewer", "administrator")),
            ),
        )
        .outerjoin(
            ApprovalReviewerGroupMember,
            ApprovalReviewerGroupMember.group_id == ApprovalReviewerGroup.id,
        )
        .outerjoin(User, User.id == ApprovalReviewerGroupMember.user_id)
        .where(ApprovalReviewerGroup.is_active.is_(True))
        .group_by(ApprovalReviewerGroup.id, ApprovalReviewerGroup.name)
        .order_by(ApprovalReviewerGroup.name)
    )
    return [
        ApprovalGroupOption(id=group_id, name=name, eligible_member_count=count)
        for group_id, name, count in rows
    ]


@router.get("/admin/approval-groups", response_model=list[ApprovalGroupResponse])
def list_groups(db: Db, principal: Actor) -> list[ApprovalGroupResponse]:
    require_admin(principal)
    groups = db.scalars(select(ApprovalReviewerGroup).order_by(ApprovalReviewerGroup.name))
    return [group_response(db, group) for group in groups]


@router.post(
    "/admin/approval-groups",
    response_model=ApprovalGroupResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_group(
    payload: ApprovalGroupCreate, request: Request, db: Db, principal: Actor
) -> ApprovalGroupResponse:
    require_admin(principal)
    actor = user_for_principal(db, principal)
    member_ids = validate_members(db, payload.member_ids)
    group = ApprovalReviewerGroup(name=payload.name.strip(), created_by=actor.id, is_active=True)
    if not group.name:
        raise HTTPException(status_code=422, detail="Group name cannot be blank")
    db.add(group)
    try:
        db.flush()
        db.add_all(
            ApprovalReviewerGroupMember(group_id=group.id, user_id=value)
            for value in member_ids
        )
        record_audit(
            db,
            actor_id=actor.id,
            action="approval_group.created",
            entity_type="approval_reviewer_group",
            entity_id=group.id,
            request_id=request.state.request_id,
            details={"name": group.name, "member_count": len(member_ids)},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Approval group name already exists") from exc
    db.refresh(group)
    return group_response(db, group)


@router.patch("/admin/approval-groups/{group_id}", response_model=ApprovalGroupResponse)
def update_group(group_id: uuid.UUID, payload: ApprovalGroupUpdate, request: Request,
                 db: Db, principal: Actor) -> ApprovalGroupResponse:
    require_admin(principal)
    group = db.get(ApprovalReviewerGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Approval group not found")
    actor = user_for_principal(db, principal)
    changed_fields = set(payload.model_fields_set)
    if payload.name is not None:
        group.name = payload.name.strip()
        if not group.name:
            raise HTTPException(status_code=422, detail="Group name cannot be blank")
    if payload.is_active is not None:
        group.is_active = payload.is_active
    if payload.member_ids is not None:
        member_ids = validate_members(db, payload.member_ids)
        db.execute(
            delete(ApprovalReviewerGroupMember).where(
                ApprovalReviewerGroupMember.group_id == group.id
            )
        )
        db.add_all(
            ApprovalReviewerGroupMember(group_id=group.id, user_id=value)
            for value in member_ids
        )
    try:
        record_audit(
            db,
            actor_id=actor.id,
            action="approval_group.updated",
            entity_type="approval_reviewer_group",
            entity_id=group.id,
            request_id=request.state.request_id,
            details={"fields": sorted(changed_fields)},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Approval group name already exists") from exc
    db.refresh(group)
    return group_response(db, group)
