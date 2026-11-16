import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.api.parts import require_engineer
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import (
    ApprovalReviewerDelegation,
    ApprovalReviewerGroup,
    ApprovalReviewerGroupMember,
    Item,
    Revision,
    RevisionApprovalDecision,
    RevisionApprovalRequest,
    RevisionContentSnapshot,
    User,
)
from modelryx.services.approval_escalations import create_overdue_approval_escalations
from modelryx.services.approval_notifications import (
    notify_requester_of_decision,
    notify_reviewers,
)

router = APIRouter(prefix="/api/v1", tags=["revision-approvals"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


class ApprovalRequestCreate(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)
    reviewer_group_id: uuid.UUID | None = None
    emergency: bool = False
    incident_reference: str | None = Field(default=None, max_length=200)


class ApprovalDecision(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    comment: str = Field(min_length=1, max_length=2000)
    warnings_acknowledged: bool = False


class ApprovalResponse(BaseModel):
    id: uuid.UUID
    revision_id: uuid.UUID
    snapshot_id: uuid.UUID
    reviewer_group_id: uuid.UUID | None
    reviewer_group_name: str | None
    status: str
    policy_version: str
    policy: dict
    required_approvals: int
    approval_count: int
    due_at: datetime
    request_comment: str | None
    requested_by: uuid.UUID
    requested_at: datetime
    decision_comment: str | None
    warnings_acknowledged: bool
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    can_withdraw: bool = False
    can_decide: bool = False


class ReviewInboxEntry(BaseModel):
    approval: ApprovalResponse
    item_id: uuid.UUID
    item_number: str
    item_name: str
    revision_code: str
    revision_state: str
    snapshot_sequence: int


def approval_response(
    value: RevisionApprovalRequest,
    effective_status: str | None = None,
    *,
    approval_count: int = 0,
    can_withdraw: bool = False,
    can_decide: bool = False,
) -> ApprovalResponse:
    return ApprovalResponse(
        id=value.id,
        revision_id=value.revision_id,
        snapshot_id=value.snapshot_id,
        reviewer_group_id=value.reviewer_group_id,
        reviewer_group_name=value.reviewer_group_name,
        status=effective_status or value.status,
        policy_version=value.policy_version,
        policy=value.policy_json,
        required_approvals=value.required_approvals,
        approval_count=approval_count,
        due_at=value.due_at,
        request_comment=value.request_comment,
        requested_by=value.requested_by,
        requested_at=value.requested_at,
        decision_comment=value.decision_comment,
        warnings_acknowledged=value.warnings_acknowledged,
        decided_by=value.decided_by,
        decided_at=value.decided_at,
        can_withdraw=can_withdraw,
        can_decide=can_decide,
    )


def actor_is_eligible(
    approval: RevisionApprovalRequest, actor_id: uuid.UUID, role: str
) -> bool:
    if role not in {"reviewer", "administrator"} or actor_id == approval.requested_by:
        return False
    eligible_user_ids = approval.policy_json.get("eligible_user_ids")
    return eligible_user_ids is None or str(actor_id) in eligible_user_ids


def revision_for_item(db: Session, item_id: uuid.UUID, revision_id: uuid.UUID) -> Revision:
    if db.get(Item, item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    revision = db.get(Revision, revision_id)
    if revision is None or revision.part_id != item_id:
        raise HTTPException(status_code=404, detail="Item revision not found")
    return revision


def latest_snapshot(db: Session, revision_id: uuid.UUID) -> RevisionContentSnapshot:
    snapshot = db.scalar(
        select(RevisionContentSnapshot)
        .where(RevisionContentSnapshot.revision_id == revision_id)
        .order_by(RevisionContentSnapshot.sequence.desc())
    )
    if snapshot is None:
        raise HTTPException(status_code=409, detail="Revision has no committed content snapshot")
    return snapshot


def mark_stale(
    db: Session,
    approval: RevisionApprovalRequest,
    snapshot: RevisionContentSnapshot,
    request: Request,
    actor_id: uuid.UUID,
) -> None:
    approval.status = "STALE"
    record_audit(
        db,
        actor_id=actor_id,
        action="revision.approval_stale",
        entity_type="revision_approval_request",
        entity_id=approval.id,
        request_id=request.state.request_id,
        details={
            "revision_id": str(approval.revision_id),
            "approved_snapshot_id": str(approval.snapshot_id),
            "current_snapshot_id": str(snapshot.id),
        },
    )


@router.post(
    "/items/{item_id}/revisions/{revision_id}/approval-requests",
    response_model=ApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_approval_request(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    payload: ApprovalRequestCreate,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> ApprovalResponse:
    require_engineer(principal)
    incident_reference = (payload.incident_reference or "").strip()
    if payload.emergency and principal.role != "administrator":
        raise HTTPException(status_code=403, detail="Emergency review requires administrator role")
    if payload.emergency and not incident_reference:
        raise HTTPException(
            status_code=422, detail="Emergency review requires an incident reference"
        )
    if payload.emergency and not (payload.comment or "").strip():
        raise HTTPException(status_code=422, detail="Emergency review requires a justification")
    if payload.emergency and payload.reviewer_group_id is not None:
        raise HTTPException(status_code=422, detail="Emergency review cannot use a reviewer group")
    revision = revision_for_item(db, item_id, revision_id)
    actor = user_for_principal(db, principal)
    if not payload.emergency and revision.created_by != actor.id:
        raise HTTPException(
            status_code=403, detail="Only the revision creator can request approval"
        )
    if revision.lifecycle_state != "WORKING":
        raise HTTPException(status_code=409, detail="Only a working revision can be submitted")
    if revision.state not in {"READY", "READY_WITH_WARNINGS"}:
        raise HTTPException(status_code=409, detail="Revision processing must finish before review")
    item = db.get(Item, item_id)
    if item is None or item.current_revision_id != revision.id:
        raise HTTPException(
            status_code=409, detail="Only the current Item Revision can be submitted"
        )
    snapshot = latest_snapshot(db, revision.id)
    pending = db.scalar(
        select(RevisionApprovalRequest)
        .where(
            RevisionApprovalRequest.revision_id == revision.id,
            RevisionApprovalRequest.status == "PENDING",
        )
        .with_for_update()
    )
    if pending is not None:
        if pending.snapshot_id == snapshot.id:
            raise HTTPException(status_code=409, detail="Revision already has a pending review")
        mark_stale(db, pending, snapshot, request, actor.id)
    required_approvals = 1 if payload.emergency else (
        settings.approval_assembly_required_count
        if item.part_type == "ASSEMBLY"
        else settings.approval_required_count
    )
    reviewer_group = (
        db.get(ApprovalReviewerGroup, payload.reviewer_group_id)
        if payload.reviewer_group_id is not None
        else None
    )
    if payload.reviewer_group_id is not None and (
        reviewer_group is None or not reviewer_group.is_active
    ):
        raise HTTPException(
            status_code=422, detail="Reviewer group is not active or does not exist"
        )
    eligible_user_ids = (
        list(
            db.scalars(
                select(ApprovalReviewerGroupMember.user_id)
                .join(User, User.id == ApprovalReviewerGroupMember.user_id)
                .where(
                    ApprovalReviewerGroupMember.group_id == reviewer_group.id,
                    User.is_active.is_(True),
                    User.role.in_(("reviewer", "administrator")),
                )
            )
        )
        if reviewer_group is not None
        else None
    )
    if payload.emergency:
        eligible_user_ids = list(
            db.scalars(
                select(User.id).where(
                    User.is_active.is_(True),
                    User.role == "administrator",
                    User.id != actor.id,
                )
            )
        )
    submitted_at = datetime.now(UTC)
    delegation_rows = (
        list(
            db.execute(
                select(
                    ApprovalReviewerDelegation.grantor_id,
                    ApprovalReviewerDelegation.delegate_id,
                )
                .join(User, User.id == ApprovalReviewerDelegation.delegate_id)
                .where(
                    ApprovalReviewerDelegation.grantor_id.in_(eligible_user_ids),
                    ApprovalReviewerDelegation.is_active.is_(True),
                    ApprovalReviewerDelegation.starts_at <= submitted_at,
                    ApprovalReviewerDelegation.ends_at > submitted_at,
                    User.is_active.is_(True),
                    User.role.in_(("reviewer", "administrator")),
                )
            )
        )
        if eligible_user_ids is not None and not payload.emergency
        else []
    )
    eligible_other_users = (
        sorted(
            (
                {*eligible_user_ids, *(delegate_id for _, delegate_id in delegation_rows)}
                - {actor.id}
            ),
            key=str,
        )
        if eligible_user_ids is not None
        else None
    )
    if eligible_other_users is not None and len(eligible_other_users) < required_approvals:
        raise HTTPException(
            status_code=409,
            detail="Reviewer group does not contain enough independent eligible reviewers",
        )
    approval = RevisionApprovalRequest(
        revision_id=revision.id,
        snapshot_id=snapshot.id,
        request_comment=payload.comment,
        requested_by=actor.id,
        reviewer_group_id=reviewer_group.id if reviewer_group is not None else None,
        reviewer_group_name=reviewer_group.name if reviewer_group is not None else None,
        policy_version="emergency-1" if payload.emergency else "1",
        required_approvals=required_approvals,
        due_at=submitted_at
        + timedelta(
            hours=(
                settings.approval_emergency_due_hours
                if payload.emergency
                else settings.approval_due_hours
            )
        ),
        policy_json={
            "version": "emergency-1" if payload.emergency else "1",
            "eligible_roles": ["administrator"]
            if payload.emergency
            else ["reviewer", "administrator"],
            "due_hours": settings.approval_emergency_due_hours
            if payload.emergency
            else settings.approval_due_hours,
            "item_type": item.part_type,
            "emergency": payload.emergency,
            "incident_reference": incident_reference if payload.emergency else None,
            "reviewer_group_id": str(reviewer_group.id) if reviewer_group is not None else None,
            "reviewer_group_name": reviewer_group.name if reviewer_group is not None else None,
            "eligible_user_ids": [str(value) for value in eligible_other_users]
            if eligible_other_users is not None
            else None,
            "delegations": [
                {"grantor_id": str(grantor_id), "delegate_id": str(delegate_id)}
                for grantor_id, delegate_id in delegation_rows
            ],
        },
    )
    db.add(approval)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Revision already has a pending review"
        ) from exc
    record_audit(
        db,
        actor_id=actor.id,
        action=(
            "revision.emergency_approval_requested"
            if payload.emergency
            else "revision.approval_requested"
        ),
        entity_type="revision_approval_request",
        entity_id=approval.id,
        request_id=request.state.request_id,
        details={
            "revision_id": str(revision.id),
            "snapshot_id": str(snapshot.id),
            "notified_reviewers": notify_reviewers(
                db,
                approval=approval,
                item=item,
                revision=revision,
                eligible_user_ids=eligible_other_users,
            ),
        },
    )
    db.commit()
    db.refresh(approval)
    return approval_response(approval)


@router.get(
    "/items/{item_id}/revisions/{revision_id}/approval-requests",
    response_model=list[ApprovalResponse],
)
def list_approval_requests(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
) -> list[ApprovalResponse]:
    revision_for_item(db, item_id, revision_id)
    values = list(
        db.scalars(
            select(RevisionApprovalRequest)
            .where(RevisionApprovalRequest.revision_id == revision_id)
            .order_by(RevisionApprovalRequest.requested_at.desc())
        )
    )
    snapshot = latest_snapshot(db, revision_id)
    actor = user_for_principal(db, principal)
    approval_counts = {
        request_id: count
        for request_id, count in db.execute(
            select(
                RevisionApprovalDecision.approval_request_id,
                func.count(RevisionApprovalDecision.id),
            )
            .where(RevisionApprovalDecision.decision == "APPROVED")
            .group_by(RevisionApprovalDecision.approval_request_id)
        )
    }
    return [
        approval_response(
            value,
            "STALE"
            if value.status == "PENDING" and value.snapshot_id != snapshot.id
            else None,
            approval_count=approval_counts.get(value.id, 0),
            can_withdraw=value.status == "PENDING" and value.requested_by == actor.id,
            can_decide=(
                value.status == "PENDING"
                and value.snapshot_id == snapshot.id
                and actor_is_eligible(value, actor.id, principal.role)
            ),
        )
        for value in values
    ]


@router.get("/approval-requests/inbox", response_model=list[ReviewInboxEntry])
def review_inbox(db: Db, principal: Actor) -> list[ReviewInboxEntry]:
    if principal.role not in {"reviewer", "administrator"}:
        raise HTTPException(status_code=403, detail="Reviewer role required")
    actor = user_for_principal(db, principal)
    rows = db.execute(
        select(RevisionApprovalRequest, Revision, Item, RevisionContentSnapshot)
        .join(Revision, Revision.id == RevisionApprovalRequest.revision_id)
        .join(Item, Item.id == Revision.part_id)
        .join(
            RevisionContentSnapshot,
            RevisionContentSnapshot.id == RevisionApprovalRequest.snapshot_id,
        )
        .where(RevisionApprovalRequest.status == "PENDING")
        .order_by(RevisionApprovalRequest.requested_at.asc())
    )
    latest_snapshot_ids = {
        revision_id: snapshot_id
        for revision_id, snapshot_id in db.execute(
            select(RevisionContentSnapshot.revision_id, RevisionContentSnapshot.id).distinct(
                RevisionContentSnapshot.revision_id
            ).order_by(
                RevisionContentSnapshot.revision_id,
                RevisionContentSnapshot.sequence.desc(),
            )
        )
    }
    approval_counts = {
        request_id: count
        for request_id, count in db.execute(
            select(
                RevisionApprovalDecision.approval_request_id,
                func.count(RevisionApprovalDecision.id),
            )
            .where(RevisionApprovalDecision.decision == "APPROVED")
            .group_by(RevisionApprovalDecision.approval_request_id)
        )
    }
    return [
        ReviewInboxEntry(
            approval=approval_response(
                approval,
                "STALE"
                if latest_snapshot_ids.get(revision.id) != approval.snapshot_id
                else None,
                approval_count=approval_counts.get(approval.id, 0),
                can_decide=actor_is_eligible(approval, actor.id, principal.role),
            ),
            item_id=item.id,
            item_number=item.part_number,
            item_name=item.name,
            revision_code=revision.revision_code,
            revision_state=revision.state,
            snapshot_sequence=snapshot.sequence,
        )
        for approval, revision, item, snapshot in rows
        if actor_is_eligible(approval, actor.id, principal.role)
    ]


@router.post("/approval-requests/{approval_id}/decision", response_model=ApprovalResponse)
def decide_approval_request(
    approval_id: uuid.UUID,
    payload: ApprovalDecision,
    request: Request,
    db: Db,
    principal: Actor,
) -> ApprovalResponse:
    if principal.role not in {"reviewer", "administrator"}:
        raise HTTPException(status_code=403, detail="Reviewer role required")
    approval = db.scalar(
        select(RevisionApprovalRequest)
        .where(RevisionApprovalRequest.id == approval_id)
        .with_for_update()
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if approval.status != "PENDING":
        raise HTTPException(status_code=409, detail="Approval request is no longer pending")
    actor = user_for_principal(db, principal)
    if approval.policy_json.get("emergency") is True and approval.due_at <= datetime.now(UTC):
        create_overdue_approval_escalations(
            db, actor_id=actor.id, request_id=request.state.request_id
        )
        db.commit()
        raise HTTPException(status_code=409, detail="Emergency approval request has expired")
    if actor.id == approval.requested_by:
        raise HTTPException(
            status_code=409, detail="Requester cannot approve or reject own request"
        )
    if not actor_is_eligible(approval, actor.id, principal.role):
        raise HTTPException(status_code=403, detail="Reviewer is not assigned to this request")
    prior_decision = db.scalar(
        select(RevisionApprovalDecision.id).where(
            RevisionApprovalDecision.approval_request_id == approval.id,
            RevisionApprovalDecision.decided_by == actor.id,
        )
    )
    if prior_decision is not None:
        raise HTTPException(status_code=409, detail="Reviewer already decided this request")
    current_snapshot = latest_snapshot(db, approval.revision_id)
    if current_snapshot.id != approval.snapshot_id:
        mark_stale(db, approval, current_snapshot, request, actor.id)
        db.commit()
        raise HTTPException(
            status_code=409, detail="Approval request is stale after content changed"
        )
    revision = db.get(Revision, approval.revision_id)
    if (
        payload.decision == "APPROVED"
        and revision is not None
        and revision.state == "READY_WITH_WARNINGS"
        and not payload.warnings_acknowledged
    ):
        raise HTTPException(
            status_code=409,
            detail="Approval must explicitly acknowledge revision processing warnings",
        )
    decision = RevisionApprovalDecision(
        approval_request_id=approval.id,
        decision=payload.decision,
        comment=payload.comment,
        warnings_acknowledged=payload.warnings_acknowledged,
        decided_by=actor.id,
    )
    db.add(decision)
    approved_count = db.scalar(
        select(func.count())
        .select_from(RevisionApprovalDecision)
        .where(
            RevisionApprovalDecision.approval_request_id == approval.id,
            RevisionApprovalDecision.decision == "APPROVED",
        )
    ) or 0
    if payload.decision == "REJECTED" or approved_count >= approval.required_approvals:
        approval.status = payload.decision
        approval.decision_comment = payload.comment
        approval.warnings_acknowledged = payload.warnings_acknowledged
        approval.decided_by = actor.id
        approval.decided_at = datetime.now(UTC)
    item = db.get(Item, revision.part_id) if revision is not None else None
    if revision is None or item is None:
        raise HTTPException(status_code=409, detail="Approval target no longer exists")
    requester_notified = notify_requester_of_decision(
        db,
        approval=approval,
        item=item,
        revision=revision,
        reviewer_id=actor.id,
        decision=payload.decision,
        approval_count=int(approved_count),
    )
    is_terminal = approval.status in {"APPROVED", "REJECTED"}
    if is_terminal and approval.policy_json.get("emergency") is True:
        audit_action = f"revision.emergency_approval_{payload.decision.lower()}"
    elif is_terminal:
        audit_action = f"revision.approval_{payload.decision.lower()}"
    else:
        audit_action = "revision.approval_recorded"
    record_audit(
        db,
        actor_id=actor.id,
        action=audit_action,
        entity_type="revision_approval_request",
        entity_id=approval.id,
        request_id=request.state.request_id,
        details={
            "revision_id": str(approval.revision_id),
            "snapshot_id": str(approval.snapshot_id),
            "decision": payload.decision,
            "approval_count": int(approved_count),
            "required_approvals": approval.required_approvals,
            "requester_notified": requester_notified,
        },
    )
    db.commit()
    db.refresh(approval)
    return approval_response(approval, approval_count=int(approved_count))


@router.post("/approval-requests/{approval_id}/withdraw", response_model=ApprovalResponse)
def withdraw_approval_request(
    approval_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
) -> ApprovalResponse:
    require_engineer(principal)
    approval = db.scalar(
        select(RevisionApprovalRequest)
        .where(RevisionApprovalRequest.id == approval_id)
        .with_for_update()
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if approval.status != "PENDING":
        raise HTTPException(status_code=409, detail="Only a pending request can be withdrawn")
    actor = user_for_principal(db, principal)
    if approval.requested_by != actor.id:
        raise HTTPException(status_code=403, detail="Only the requester can withdraw this request")
    approval.status = "WITHDRAWN"
    approval.decided_at = datetime.now(UTC)
    record_audit(
        db,
        actor_id=actor.id,
        action="revision.approval_withdrawn",
        entity_type="revision_approval_request",
        entity_id=approval.id,
        request_id=request.state.request_id,
        details={
            "revision_id": str(approval.revision_id),
            "snapshot_id": str(approval.snapshot_id),
        },
    )
    db.commit()
    db.refresh(approval)
    return approval_response(approval)
