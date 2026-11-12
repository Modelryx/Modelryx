import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.db import get_db
from modelryx.models import ApprovalReviewerDelegation, User

router = APIRouter(prefix="/api/v1/approval-delegations", tags=["approval-routing"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]


class DelegationCreate(BaseModel):
    delegate_id: uuid.UUID
    starts_at: datetime | None = None
    ends_at: datetime


class DelegationResponse(BaseModel):
    id: uuid.UUID
    grantor_id: uuid.UUID
    delegate_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    is_active: bool
    created_at: datetime
    can_revoke: bool = False


class DelegateCandidate(BaseModel):
    id: uuid.UUID
    display_name: str
    role: str


def delegation_response(
    value: ApprovalReviewerDelegation, *, can_revoke: bool = False
) -> DelegationResponse:
    return DelegationResponse(
        id=value.id,
        grantor_id=value.grantor_id,
        delegate_id=value.delegate_id,
        starts_at=value.starts_at,
        ends_at=value.ends_at,
        is_active=value.is_active,
        created_at=value.created_at,
        can_revoke=can_revoke,
    )


def require_reviewer(principal: Principal) -> None:
    if principal.role not in {"reviewer", "administrator"}:
        raise HTTPException(status_code=403, detail="Reviewer role required")


@router.get("", response_model=list[DelegationResponse])
def list_delegations(db: Db, principal: Actor) -> list[DelegationResponse]:
    require_reviewer(principal)
    actor = user_for_principal(db, principal)
    values = db.scalars(
        select(ApprovalReviewerDelegation)
        .where(
            or_(
                ApprovalReviewerDelegation.grantor_id == actor.id,
                ApprovalReviewerDelegation.delegate_id == actor.id,
            )
        )
        .order_by(ApprovalReviewerDelegation.ends_at.desc())
    )
    return [
        delegation_response(
            value,
            can_revoke=value.grantor_id == actor.id or principal.role == "administrator",
        )
        for value in values
    ]


@router.get("/candidates", response_model=list[DelegateCandidate])
def list_delegate_candidates(db: Db, principal: Actor) -> list[DelegateCandidate]:
    require_reviewer(principal)
    actor = user_for_principal(db, principal)
    users = db.scalars(
        select(User)
        .where(
            User.is_active.is_(True),
            User.role.in_(("reviewer", "administrator")),
            User.id != actor.id,
        )
        .order_by(User.display_name)
    )
    return [
        DelegateCandidate(id=user.id, display_name=user.display_name, role=user.role)
        for user in users
    ]


@router.post("", response_model=DelegationResponse, status_code=status.HTTP_201_CREATED)
def create_delegation(
    payload: DelegationCreate, request: Request, db: Db, principal: Actor
) -> DelegationResponse:
    require_reviewer(principal)
    actor = user_for_principal(db, principal)
    delegate = db.get(User, payload.delegate_id)
    if (
        delegate is None
        or not delegate.is_active
        or delegate.role not in {"reviewer", "administrator"}
    ):
        raise HTTPException(
            status_code=422, detail="Delegate must be an active reviewer or administrator"
        )
    if delegate.id == actor.id:
        raise HTTPException(status_code=422, detail="A reviewer cannot delegate to themselves")
    starts_at = payload.starts_at or datetime.now(UTC)
    if starts_at.tzinfo is None or payload.ends_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="Delegation times must include a timezone")
    if payload.ends_at <= starts_at:
        raise HTTPException(status_code=422, detail="Delegation must end after it starts")
    if payload.ends_at - starts_at > timedelta(days=365):
        raise HTTPException(status_code=422, detail="Delegation cannot exceed 365 days")
    overlap = db.scalar(
        select(ApprovalReviewerDelegation.id).where(
            ApprovalReviewerDelegation.grantor_id == actor.id,
            ApprovalReviewerDelegation.delegate_id == delegate.id,
            ApprovalReviewerDelegation.is_active.is_(True),
            ApprovalReviewerDelegation.starts_at < payload.ends_at,
            ApprovalReviewerDelegation.ends_at > starts_at,
        )
    )
    if overlap is not None:
        raise HTTPException(status_code=409, detail="An overlapping delegation already exists")
    delegation = ApprovalReviewerDelegation(
        grantor_id=actor.id,
        delegate_id=delegate.id,
        starts_at=starts_at,
        ends_at=payload.ends_at,
        is_active=True,
        created_by=actor.id,
    )
    db.add(delegation)
    db.flush()
    record_audit(
        db,
        actor_id=actor.id,
        action="approval_delegation.created",
        entity_type="approval_reviewer_delegation",
        entity_id=delegation.id,
        request_id=request.state.request_id,
        details={"delegate_id": str(delegate.id), "ends_at": payload.ends_at.isoformat()},
    )
    db.commit()
    db.refresh(delegation)
    return delegation_response(delegation, can_revoke=True)


@router.delete("/{delegation_id}", response_model=DelegationResponse)
def revoke_delegation(
    delegation_id: uuid.UUID, request: Request, db: Db, principal: Actor
) -> DelegationResponse:
    require_reviewer(principal)
    actor = user_for_principal(db, principal)
    delegation = db.get(ApprovalReviewerDelegation, delegation_id)
    if delegation is None:
        raise HTTPException(status_code=404, detail="Delegation not found")
    if delegation.grantor_id != actor.id and principal.role != "administrator":
        raise HTTPException(
            status_code=403,
            detail="Only the grantor or an administrator can revoke",
        )
    if not delegation.is_active:
        raise HTTPException(status_code=409, detail="Delegation is already inactive")
    delegation.is_active = False
    record_audit(
        db,
        actor_id=actor.id,
        action="approval_delegation.revoked",
        entity_type="approval_reviewer_delegation",
        entity_id=delegation.id,
        request_id=request.state.request_id,
    )
    db.commit()
    db.refresh(delegation)
    return delegation_response(delegation, can_revoke=True)
