from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.auth import Principal, create_access_token, hash_password
from modelryx.core.config import Settings
from modelryx.core.db import get_db
from modelryx.models import User
from pydantic import ValidationError

client = TestClient(app)


def use_fake_user(subject: str, display_name: str, role: str) -> None:
    user = User(
        external_subject=subject,
        display_name=display_name,
        role=role,
        password_hash=None,
        is_active=True,
    )

    class FakeSession:
        def scalar(self, _statement):
            return user

        def add(self, _record):
            pass

        def commit(self):
            pass

    app.dependency_overrides[get_db] = lambda: FakeSession()


def test_protected_endpoint_rejects_missing_token() -> None:
    response = client.get("/api/v1/session")
    assert response.status_code == 401


def test_signed_jwt_authenticates() -> None:
    token = create_access_token(
        Principal(subject="test-user", display_name="Test User", role="administrator")
    )
    use_fake_user("test-user", "Test User", "administrator")
    try:
        response = client.get("/api/v1/session", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["role"] == "administrator"
        assert response.headers["X-Request-ID"]
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_tampered_jwt_is_rejected() -> None:
    token = create_access_token(
        Principal(subject="test-user", display_name="Test User", role="administrator")
    )
    response = client.get("/api/v1/session", headers={"Authorization": f"Bearer {token[:-1]}x"})
    assert response.status_code == 401


def test_login_returns_a_usable_jwt() -> None:
    user = User(
        external_subject="alex",
        display_name="Alex Engineer",
        role="engineer",
        password_hash=hash_password("correct horse battery staple"),
        is_active=True,
        auth_version=0,
    )

    class FakeSession:
        def scalar(self, _statement):
            return user

        def commit(self):
            pass

    app.dependency_overrides[get_db] = lambda: FakeSession()
    try:
        login_response = client.post(
            "/api/v1/auth/login",
            json={"username": "alex", "password": "correct horse battery staple"},
        )
        assert login_response.status_code == 200
        assert user.last_login_at is not None
        token = login_response.json()["access_token"]
        session_response = client.get(
            "/api/v1/session", headers={"Authorization": f"Bearer {token}"}
        )
        assert session_response.status_code == 200
        assert session_response.json() == {
            "subject": "alex",
            "display_name": "Alex Engineer",
            "role": "engineer",
            "roles": ["designer"],
        }
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_repeated_failed_logins_temporarily_lock_account() -> None:
    user = User(
        external_subject="lock-test",
        display_name="Lock Test",
        role="reader",
        password_hash=hash_password("correct-password"),
        is_active=True,
        auth_version=0,
        failed_login_attempts=0,
    )

    class FakeSession:
        def scalar(self, _statement):
            return user

        def add(self, _record):
            pass

        def commit(self):
            pass

    app.dependency_overrides[get_db] = lambda: FakeSession()
    try:
        for _ in range(5):
            response = client.post(
                "/api/v1/auth/login",
                json={"username": "lock-test", "password": "wrong-password"},
            )
            assert response.status_code == 401
        assert user.failed_login_attempts == 5
        assert user.locked_until is not None

        locked = client.post(
            "/api/v1/auth/login",
            json={"username": "lock-test", "password": "correct-password"},
        )
        assert locked.status_code == 401

        user.locked_until = datetime.now(UTC) - timedelta(seconds=1)
        unlocked = client.post(
            "/api/v1/auth/login",
            json={"username": "lock-test", "password": "correct-password"},
        )
        assert unlocked.status_code == 200
        assert user.failed_login_attempts == 0
        assert user.locked_until is None
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_non_administrator_cannot_access_user_management() -> None:
    token = create_access_token(
        Principal(subject="engineer", display_name="Engineer", role="engineer")
    )
    use_fake_user("engineer", "Engineer", "engineer")
    try:
        response = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_non_administrator_cannot_include_archived_items() -> None:
    token = create_access_token(
        Principal(subject="engineer", display_name="Engineer", role="engineer")
    )
    use_fake_user("engineer", "Engineer", "engineer")
    try:
        response = client.get(
            "/api/v1/items?include_archived=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_database_role_overrides_stale_jwt_role() -> None:
    token = create_access_token(
        Principal(subject="changed-user", display_name="Old Name", role="administrator")
    )
    use_fake_user("changed-user", "Current Name", "reader")
    try:
        response = client.get("/api/v1/session", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["role"] == "reader"
        assert response.json()["display_name"] == "Current Name"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_disabled_user_jwt_is_rejected_immediately() -> None:
    token = create_access_token(
        Principal(subject="disabled", display_name="Disabled", role="administrator")
    )
    use_fake_user("disabled", "Disabled", "administrator")
    fake_db = app.dependency_overrides[get_db]()
    fake_db.scalar(None).is_active = False
    app.dependency_overrides[get_db] = lambda: fake_db
    try:
        response = client.get("/api/v1/session", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_revoked_auth_version_is_rejected_immediately() -> None:
    token = create_access_token(
        Principal(subject="revoked", display_name="Revoked", role="administrator")
    )
    use_fake_user("revoked", "Revoked", "administrator")
    fake_db = app.dependency_overrides[get_db]()
    fake_db.scalar(None).auth_version = 1
    app.dependency_overrides[get_db] = lambda: fake_db
    try:
        response = client.get("/api/v1/session", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_engineer_cannot_archive_an_item() -> None:
    token = create_access_token(
        Principal(subject="engineer", display_name="Engineer", role="engineer")
    )
    use_fake_user("engineer", "Engineer", "engineer")
    try:
        response = client.patch(
            "/api/v1/items/00000000-0000-0000-0000-000000000001",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "ARCHIVED"},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_engineer_cannot_delete_an_item() -> None:
    token = create_access_token(
        Principal(subject="engineer", display_name="Engineer", role="engineer")
    )
    use_fake_user("engineer", "Engineer", "engineer")
    try:
        response = client.delete(
            "/api/v1/items/00000000-0000-0000-0000-000000000001",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_production_rejects_development_authentication_defaults() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", _env_file=None)


def test_liveness_does_not_require_database() -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
