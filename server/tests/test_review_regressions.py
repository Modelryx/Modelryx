"""Regression checks for authentication and revision-edit edge cases."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from modelryx.api import staging
from modelryx.api.auth import LoginRequest, secrets_match_bootstrap
from modelryx.api.cad import _require_cad_editor
from modelryx.core.auth import Principal
from modelryx.core.roles import assign_roles
from modelryx.models import User
from modelryx.services.staging_workflow import _principal


@pytest.mark.parametrize(
    ("username", "password", "expected"),
    [
        ("admin", "密碼-example", False),
        ("使用者", "wrong", False),
        ("管理員", "密碼-example", True),
        ("管理員", "wrong", False),
    ],
)
def test_bootstrap_comparison_accepts_unicode(username, password, expected):
    settings = SimpleNamespace(
        bootstrap_admin_username="管理員", bootstrap_admin_password="密碼-example"
    )
    payload = LoginRequest(username=username, password=password)
    assert secrets_match_bootstrap(payload, username, settings) is expected


def test_combined_designer_reviewer_can_edit_cad():
    principal = Principal(
        subject="dual", display_name="Dual role", role="reviewer", roles=("designer", "reviewer")
    )
    _require_cad_editor(principal)


@pytest.mark.parametrize("role", ["reader", "reviewer"])
def test_non_designers_cannot_edit_cad(role):
    principal = Principal(subject="user", display_name="User", role=role, roles=(role,))
    with pytest.raises(HTTPException) as error:
        _require_cad_editor(principal)
    assert error.value.status_code == 403


def test_staging_workflow_preserves_all_assigned_roles():
    user = User(external_subject="dual", display_name="Dual role", role="engineer")
    assign_roles(user, ["designer", "reviewer"])
    principal = _principal(user)
    assert set(principal.roles) == {"designer", "reviewer"}
    assert principal.has_role("designer")
    assert principal.has_role("reviewer")


def test_edit_without_content_snapshot_reports_conflict(monkeypatch):
    item_id, revision_id, base_snapshot_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    proposal = {
        "expected_snapshot_version": 1,
        "operation": "EDIT_ITEM_REVISION",
        "destination": {
            "item_id": str(item_id),
            "revision_id": str(revision_id),
            "base_snapshot_id": str(base_snapshot_id),
        },
        "item": {"item_number": "ITEM-1", "name": "Item", "item_type": "PART"},
        "revision": {"revision_code": "A"},
    }
    session = SimpleNamespace(
        id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        status="PREPARING",
        snapshot_version=1,
        proposal_json=proposal,
    )
    monkeypatch.setattr(staging, "_owned_staging", lambda *_args: session)
    db = Mock()
    db.scalars.return_value = []
    db.get.return_value = SimpleNamespace(part_id=item_id)
    db.scalar.side_effect = [SimpleNamespace(base_snapshot_id=base_snapshot_id), None]
    principal = Principal(subject="designer", display_name="Designer", role="engineer")
    with pytest.raises(HTTPException) as error:
        staging.mark_staging_ready(
            session.id,
            staging.StagingReadyRequest(expected_snapshot_version=1),
            Mock(),
            db,
            principal,
        )
    assert error.value.status_code == 409
    db.commit.assert_not_called()
