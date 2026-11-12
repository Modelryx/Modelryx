import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from auth_helpers import AUTH  # noqa: F401 -- provisions the integration-test identity
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.auth import Principal, create_access_token
from modelryx.core.db import SessionLocal
from modelryx.models import User
from sqlalchemy import select


def test_user_can_hold_designer_and_reviewer_roles() -> None:
    username = f"designer-reviewer-{uuid.uuid4()}"
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/admin/users",
            headers=AUTH,
            json={
                "username": username,
                "display_name": "Designer Reviewer",
                "roles": ["designer"],
                "password": "temporary-password",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["roles"] == ["designer"]
        updated = client.patch(
            f"/api/v1/admin/users/{created.json()['id']}",
            headers=AUTH,
            json={"roles": ["designer", "reviewer"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["roles"] == ["designer", "reviewer"]
        # Effective role remains for older clients, but the independent role list
        # is authoritative for new authorization and navigation.
        assert updated.json()["role"] == "reviewer"

        login = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "temporary-password"},
        )
        assert login.status_code == 200, login.text
        assert login.json()["roles"] == ["designer", "reviewer"]
        session = client.get(
            "/api/v1/session",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert session.status_code == 200
        assert session.json()["roles"] == ["designer", "reviewer"]


def test_administrator_can_revoke_user_sessions_and_read_activity() -> None:
    username = f"revoke-user-{uuid.uuid4()}"
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/admin/users",
            headers=AUTH,
            json={
                "username": username,
                "display_name": "Revoked Session User",
                "roles": ["reader"],
                "password": "temporary-password",
            },
        )
        assert created.status_code == 201, created.text
        login = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "temporary-password"},
        )
        assert login.status_code == 200, login.text
        token_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert client.get("/api/v1/session", headers=token_headers).status_code == 200

        revoked = client.post(
            f"/api/v1/admin/users/{created.json()['id']}/sessions/revoke", headers=AUTH
        )
        assert revoked.status_code == 204, revoked.text
        assert client.get("/api/v1/session", headers=token_headers).status_code == 401
        activity = client.get(
            f"/api/v1/admin/users/{created.json()['id']}/activity", headers=AUTH
        )
        assert activity.status_code == 200, activity.text
        assert any(row["action"] == "user.sessions_revoked" for row in activity.json())


def test_administrator_can_unlock_user_account() -> None:
    username = f"locked-user-{uuid.uuid4()}"
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/admin/users",
            headers=AUTH,
            json={
                "username": username,
                "display_name": "Locked User",
                "roles": ["reader"],
                "password": "temporary-password",
            },
        )
        assert created.status_code == 201, created.text
        user_id = uuid.UUID(created.json()["id"])
        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None
            user.failed_login_attempts = 5
            user.locked_until = datetime.now(UTC) + timedelta(minutes=15)
            db.commit()

        unlocked = client.post(f"/api/v1/admin/users/{user_id}/unlock", headers=AUTH)
        assert unlocked.status_code == 204, unlocked.text
        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None
            assert user.failed_login_attempts == 0
            assert user.locked_until is None
        activity = client.get(f"/api/v1/admin/users/{user_id}/activity", headers=AUTH)
        assert activity.status_code == 200
        assert any(row["action"] == "user.unlocked" for row in activity.json())


@pytest.fixture
def administrators():
    with SessionLocal() as db:
        existing = list(
            db.scalars(select(User).where(User.role == "administrator", User.is_active.is_(True)))
        )
        restore_ids = [user.id for user in existing]
        for user in existing:
            user.is_active = False
        pair = [
            User(
                external_subject=f"admin-guard-{uuid.uuid4()}",
                display_name="Guard test",
                role="administrator",
                is_active=True,
            )
            for _ in range(2)
        ]
        db.add_all(pair)
        db.commit()
        identities = [
            (
                user.id,
                {
                    "Authorization": "Bearer "
                    + create_access_token(
                        Principal(
                            subject=user.external_subject,
                            display_name=user.display_name,
                            role=user.role,
                        )
                    )
                },
            )
            for user in pair
        ]
    try:
        yield identities
    finally:
        with SessionLocal() as db:
            for user_id in restore_ids:
                db.get(User, user_id).is_active = True
            for user_id, _ in identities:
                db.get(User, user_id).is_active = False
            db.commit()


@pytest.mark.parametrize("change", [{"is_active": False}, {"role": "reader"}])
def test_last_administrator_and_self_access_are_protected(administrators, change):
    (first, headers), (second, _) = administrators
    with TestClient(app) as client:
        self_change = client.patch(f"/api/v1/admin/users/{first}", headers=headers, json=change)
        assert self_change.status_code == 409
        assert "own administrator" in self_change.json()["detail"]
        assert (
            client.patch(
                f"/api/v1/admin/users/{second}", headers=headers, json={"is_active": False}
            ).status_code
            == 200
        )
        last_change = client.patch(f"/api/v1/admin/users/{first}", headers=headers, json=change)
        assert last_change.status_code == 409
        assert last_change.json()["detail"] == "At least one active administrator must remain"


@pytest.mark.parametrize("change", [{"is_active": False}, {"role": "reader"}])
def test_concurrent_mutual_access_removal_preserves_an_administrator(administrators, change):
    barrier = Barrier(2)

    def remove(index):
        _, headers = administrators[index]
        target, _ = administrators[1 - index]
        with TestClient(app) as client:
            barrier.wait(timeout=10)
            return client.patch(
                f"/api/v1/admin/users/{target}", headers=headers, json=change
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(remove, [0, 1]))
    assert statuses.count(200) == 1
    assert any(code in {401, 403, 409} for code in statuses)
    with SessionLocal() as db:
        remaining = list(
            db.scalars(select(User).where(User.role == "administrator", User.is_active.is_(True)))
        )
        assert len(remaining) == 1
