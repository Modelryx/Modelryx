import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.core.audit import record_audit
from modelryx.models import AuditLog, Checkout, Item, Revision, UserNotification


@dataclass(frozen=True)
class CheckoutNotificationResult:
    examined: int
    created: int


@dataclass(frozen=True)
class CheckoutNotificationHealth:
    health: str
    consecutive_failures: int
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_examined: int
    last_created: int


def checkout_notification_health(
    db: Session, *, failure_threshold: int = 3
) -> CheckoutNotificationHealth:
    rows = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.action.in_(
                    [
                        "checkout.expiry_notification_run_completed",
                        "checkout.expiry_notification_run_failed",
                    ]
                )
            )
            .order_by(AuditLog.occurred_at.desc())
            .limit(50)
        )
    )
    consecutive_failures = 0
    for row in rows:
        if row.action != "checkout.expiry_notification_run_failed":
            break
        consecutive_failures += 1
    last_success = next(
        (row for row in rows if row.action == "checkout.expiry_notification_run_completed"), None
    )
    latest = rows[0] if rows else None
    details = last_success.details_json if last_success else {}
    health = "HEALTHY"
    if consecutive_failures:
        health = "WARNING"
    if consecutive_failures >= failure_threshold:
        health = "DEGRADED"
    return CheckoutNotificationHealth(
        health=health,
        consecutive_failures=consecutive_failures,
        last_attempt_at=latest.occurred_at if latest else None,
        last_success_at=last_success.occurred_at if last_success else None,
        last_examined=int(details.get("examined", 0)),
        last_created=int(details.get("created", 0)),
    )


def record_checkout_notification_run(
    db: Session,
    *,
    result: CheckoutNotificationResult | None = None,
    failure_detail: str | None = None,
    actor_id: uuid.UUID | None = None,
    request_id: str | None = None,
    trigger: str = "scheduled",
) -> None:
    failed = failure_detail is not None
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=(
                "checkout.expiry_notification_run_failed"
                if failed
                else "checkout.expiry_notification_run_completed"
            ),
            entity_type="checkout_notification_maintenance",
            entity_id=None,
            request_id=request_id or f"maintenance:{uuid.uuid4()}",
            details_json=(
                {"detail": str(failure_detail)[:1000], "trigger": trigger}
                if failed
                else {
                    "examined": result.examined,
                    "created": result.created,
                    "trigger": trigger,
                }
            ),
        )
    )


def create_checkout_expiry_notifications(
    db: Session,
    *,
    warning_seconds: int,
    now: datetime | None = None,
) -> CheckoutNotificationResult:
    """Warn checkout owners once for each lease, without extending that lease."""
    current = now or datetime.now(UTC)
    warning_deadline = current + timedelta(seconds=warning_seconds)
    rows = list(
        db.execute(
            select(Checkout, Item, Revision)
            .join(Item, Item.id == Checkout.part_id)
            .outerjoin(Revision, Revision.id == Checkout.revision_id)
            .where(Checkout.expires_at > current, Checkout.expires_at <= warning_deadline)
            .order_by(Checkout.expires_at)
        )
    )
    keys = [
        f"checkout-expiring:{checkout.id}:{checkout.expires_at.isoformat()}"
        for checkout, _, _ in rows
    ]
    existing = (
        set(
            db.scalars(
                select(UserNotification.dedupe_key).where(UserNotification.dedupe_key.in_(keys))
            )
        )
        if keys
        else set()
    )
    created = 0
    for checkout, item, revision in rows:
        key = f"checkout-expiring:{checkout.id}:{checkout.expires_at.isoformat()}"
        if key in existing:
            continue
        revision_label = revision.revision_code if revision is not None else "unknown"
        db.add(
            UserNotification(
                user_id=checkout.owner_id,
                kind="CHECKOUT_EXPIRING",
                dedupe_key=key,
                title=f"Checkout expiring: {item.part_number} Rev {revision_label}",
                message="Finish or check in your local CAD changes before this checkout expires.",
                entity_type="revision",
                entity_id=checkout.revision_id,
                details_json={
                    "checkout_id": str(checkout.id),
                    "item_id": str(item.id),
                    "item_number": item.part_number,
                    "revision_id": str(checkout.revision_id) if checkout.revision_id else None,
                    "revision_code": revision_label,
                    "expires_at": checkout.expires_at.isoformat(),
                    "scope": checkout.scope,
                },
            )
        )
        existing.add(key)
        created += 1
    if created:
        record_audit(
            db,
            actor_id=None,
            action="checkout.expiry_notifications_created",
            entity_type="checkout",
            entity_id=None,
            request_id=f"maintenance:{current.isoformat()}",
            details={"created": created, "warning_seconds": warning_seconds},
        )
    return CheckoutNotificationResult(examined=len(rows), created=created)
