from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.core.audit import record_audit
from modelryx.models import (
    Item,
    Revision,
    RevisionApprovalRequest,
    User,
    UserNotification,
)


@dataclass(frozen=True)
class ApprovalEscalationResult:
    overdue_requests: int
    created: int
    expired_emergency_requests: int = 0


def create_overdue_approval_escalations(
    db: Session,
    *,
    now: datetime | None = None,
    actor_id=None,
    request_id: str | None = None,
) -> ApprovalEscalationResult:
    """Notify active administrators once for each overdue approval request."""
    current = now or datetime.now(UTC)
    overdue = list(
        db.execute(
            select(RevisionApprovalRequest, Revision, Item)
            .join(Revision, Revision.id == RevisionApprovalRequest.revision_id)
            .join(Item, Item.id == Revision.part_id)
            .where(
                RevisionApprovalRequest.status == "PENDING",
                RevisionApprovalRequest.due_at <= current,
            )
            .order_by(RevisionApprovalRequest.due_at)
        )
    )
    administrators = list(
        db.scalars(
            select(User).where(
                User.is_active.is_(True), User.role == "administrator"
            )
        )
    )
    created = 0
    expired = 0
    for approval, revision, item in overdue:
        if approval.policy_json.get("emergency") is True:
            approval.status = "EXPIRED"
            approval.decided_at = current
            key = f"approval-expired:{approval.id}:{approval.requested_by}"
            if not db.scalar(
                select(UserNotification.id).where(UserNotification.dedupe_key == key)
            ):
                db.add(
                    UserNotification(
                        user_id=approval.requested_by,
                        kind="APPROVAL_EXPIRED",
                        dedupe_key=key,
                        title=(
                            f"Emergency review expired: {item.part_number} "
                            f"Rev {revision.revision_code}"
                        ),
                        message="The emergency approval window expired; submit a new request.",
                        entity_type="revision_approval_request",
                        entity_id=approval.id,
                        details_json={
                            "approval_request_id": str(approval.id),
                            "item_id": str(item.id),
                            "revision_id": str(revision.id),
                            "due_at": approval.due_at.isoformat(),
                        },
                    )
                )
                created += 1
            expired += 1
            continue
        for administrator in administrators:
            key = f"approval-overdue:{approval.id}:{administrator.id}"
            if db.scalar(
                select(UserNotification.id).where(UserNotification.dedupe_key == key)
            ):
                continue
            db.add(
                UserNotification(
                    user_id=administrator.id,
                    kind="APPROVAL_OVERDUE",
                    dedupe_key=key,
                    title=f"Review overdue: {item.part_number} Rev {revision.revision_code}",
                    message="A pending approval request has passed its policy due date.",
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
    if created:
        record_audit(
            db,
            actor_id=actor_id,
            action="revision.approval_escalations_created",
            entity_type="revision_approval_request",
            entity_id=None,
            request_id=request_id or f"maintenance:{current.isoformat()}",
            details={
                "overdue_requests": len(overdue),
                "expired_emergency_requests": expired,
                "notifications_created": created,
            },
        )
    return ApprovalEscalationResult(
        overdue_requests=len(overdue),
        created=created,
        expired_emergency_requests=expired,
    )
