import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.models import (
    Item,
    Revision,
    RevisionApprovalRequest,
    User,
    UserNotification,
)


def notify_reviewers(
    db: Session,
    *,
    approval: RevisionApprovalRequest,
    item: Item,
    revision: Revision,
    eligible_user_ids: list[uuid.UUID] | None = None,
) -> int:
    """Create one durable inbox notification per eligible active reviewer."""
    statement = select(User).where(
        User.is_active.is_(True),
        User.role.in_(("reviewer", "administrator")),
        User.id != approval.requested_by,
    )
    if eligible_user_ids is not None:
        statement = statement.where(User.id.in_(eligible_user_ids))
    reviewers = list(db.scalars(statement))
    created = 0
    for reviewer in reviewers:
        dedupe_key = f"approval-requested:{approval.id}:{reviewer.id}"
        if db.scalar(
            select(UserNotification.id).where(UserNotification.dedupe_key == dedupe_key)
        ):
            continue
        db.add(
            UserNotification(
                user_id=reviewer.id,
                kind="APPROVAL_REQUESTED",
                dedupe_key=dedupe_key,
                title=f"Review requested: {item.part_number} Rev {revision.revision_code}",
                message="An Item Revision is ready for independent review.",
                entity_type="revision_approval_request",
                entity_id=approval.id,
                details_json={
                    "approval_request_id": str(approval.id),
                    "item_id": str(item.id),
                    "item_number": item.part_number,
                    "revision_id": str(revision.id),
                    "revision_code": revision.revision_code,
                    "due_at": approval.due_at.isoformat(),
                },
            )
        )
        created += 1
    return created


def notify_requester_of_decision(
    db: Session,
    *,
    approval: RevisionApprovalRequest,
    item: Item,
    revision: Revision,
    reviewer_id: uuid.UUID,
    decision: str,
    approval_count: int,
) -> bool:
    """Notify the requester once for each reviewer's recorded decision."""
    dedupe_key = f"approval-decision:{approval.id}:{reviewer_id}"
    if db.scalar(
        select(UserNotification.id).where(UserNotification.dedupe_key == dedupe_key)
    ):
        return False
    if decision == "REJECTED":
        message = "The approval request was rejected. Review the decision comment before rework."
    elif approval.status == "APPROVED":
        message = "The required independent approvals are complete. The revision can be released."
    else:
        message = (
            f"Approval {approval_count} of {approval.required_approvals} was recorded; "
            "additional review is still required."
        )
    db.add(
        UserNotification(
            user_id=approval.requested_by,
            kind=f"APPROVAL_{decision}",
            dedupe_key=dedupe_key,
            title=f"Review {decision.lower()}: {item.part_number} Rev {revision.revision_code}",
            message=message,
            entity_type="revision_approval_request",
            entity_id=approval.id,
            details_json={
                "approval_request_id": str(approval.id),
                "item_id": str(item.id),
                "item_number": item.part_number,
                "revision_id": str(revision.id),
                "revision_code": revision.revision_code,
                "decision": decision,
                "approval_count": approval_count,
                "required_approvals": approval.required_approvals,
            },
        )
    )
    return True
