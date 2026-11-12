import uuid
from datetime import UTC, datetime, timedelta

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.db import SessionLocal
from modelryx.models import AuditLog, Checkout, Item, Revision, User, UserNotification
from modelryx.services.checkout_notifications import (
    CheckoutNotificationResult,
    checkout_notification_health,
    create_checkout_expiry_notifications,
    record_checkout_notification_run,
)
from sqlalchemy import select

client = TestClient(app)


def test_administrator_can_run_notification_generation_with_audited_result() -> None:
    response = client.post(
        "/api/v1/admin/maintenance/checkout-notifications/run",
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["examined"] >= response.json()["created"]
    assert response.json()["warning_seconds"] > 0

    with SessionLocal() as db:
        audit = db.scalar(
            select(AuditLog)
            .where(AuditLog.action == "checkout.expiry_notification_run_completed")
            .order_by(AuditLog.occurred_at.desc())
        )
        assert audit is not None
        assert audit.actor_id is not None
        assert audit.details_json["trigger"] == "administrator"
        assert audit.details_json["examined"] == response.json()["examined"]
        assert audit.details_json["created"] == response.json()["created"]


def test_notification_delivery_health_tracks_failures_and_success() -> None:
    with SessionLocal() as db:
        for index in range(3):
            record_checkout_notification_run(db, failure_detail=f"failure {index}")
            db.commit()
        degraded = checkout_notification_health(db)
        assert degraded.health == "DEGRADED"
        assert degraded.consecutive_failures == 3

        record_checkout_notification_run(
            db, result=CheckoutNotificationResult(examined=4, created=2)
        )
        db.commit()
        healthy = checkout_notification_health(db)
        assert healthy.health == "HEALTHY"
        assert healthy.consecutive_failures == 0
        assert healthy.last_examined == 4
        assert healthy.last_created == 2
        assert healthy.last_success_at is not None


def test_checkout_expiry_notification_is_deduplicated_without_extending_lease() -> None:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=30)
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        item = Item(
            part_number=f"NOTIFY-{uuid.uuid4().hex}",
            name="Notification test",
            part_type="PART",
            created_by=owner.id,
        )
        db.add(item)
        db.flush()
        revision = Revision(
            part_id=item.id,
            revision_code="A",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={},
            state="READY",
            created_by=owner.id,
        )
        db.add(revision)
        db.flush()
        checkout = Checkout(
            part_id=item.id,
            revision_id=revision.id,
            owner_id=owner.id,
            root_revision_id=revision.id,
            expires_at=expires_at,
        )
        db.add(checkout)
        db.commit()
        checkout_id = checkout.id

        first = create_checkout_expiry_notifications(db, warning_seconds=3600, now=now)
        db.commit()
        second = create_checkout_expiry_notifications(db, warning_seconds=3600, now=now)
        db.commit()

        assert first.created == 1
        assert second.created == 0
        assert db.get(Checkout, checkout_id).expires_at == expires_at
        notification_rows = db.scalars(
            select(UserNotification).where(UserNotification.entity_id == revision.id)
        )
        assert len(list(notification_rows)) == 1

    response = client.get("/api/v1/me/notifications?unread_only=true", headers=AUTH)
    assert response.status_code == 200
    row = next(
        entry for entry in response.json() if entry["details"]["checkout_id"] == str(checkout_id)
    )
    assert row["read_at"] is None
    marked = client.post(f"/api/v1/me/notifications/{row['id']}/read", headers=AUTH)
    assert marked.status_code == 200
    assert marked.json()["read_at"] is not None


def test_user_cannot_read_another_users_notification() -> None:
    with SessionLocal() as db:
        other = User(
            external_subject=f"notification-owner-{uuid.uuid4().hex}",
            display_name="Other Engineer",
            role="engineer",
            is_active=True,
        )
        db.add(other)
        db.flush()
        row = UserNotification(
            user_id=other.id,
            kind="CHECKOUT_EXPIRING",
            dedupe_key=f"test:{uuid.uuid4()}",
            title="Private warning",
            message="Private",
            entity_type="revision",
            details_json={},
        )
        db.add(row)
        db.commit()
        notification_id = row.id

    assert (
        client.post(f"/api/v1/me/notifications/{notification_id}/read", headers=AUTH).status_code
        == 404
    )
