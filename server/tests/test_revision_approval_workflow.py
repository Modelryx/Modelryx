import uuid
from datetime import UTC, datetime, timedelta

from artifact_helpers import commit_item
from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.auth import Principal, create_access_token
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import (
    Revision,
    RevisionApprovalRequest,
    RevisionContentSnapshot,
    User,
    UserNotification,
)

client = TestClient(app)


def role_auth(role: str) -> dict[str, str]:
    return role_identity(role)[0]


def role_identity(role: str) -> tuple[dict[str, str], uuid.UUID]:
    subject = f"approval-{role}-{uuid.uuid4()}"
    display_name = f"Approval {role.title()}"
    user_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                external_subject=subject,
                display_name=display_name,
                role=role,
                is_active=True,
                auth_version=0,
            )
        )
        db.commit()
    token = create_access_token(
        Principal(subject=subject, display_name=display_name, role=role)
    )
    return {"Authorization": f"Bearer {token}"}, user_id


def ready_item() -> dict:
    committed = commit_item(
        client,
        item_number=f"PF-APPROVAL-{uuid.uuid4().hex}",
        name="Approval bracket",
        files=[("approval.step", b"snapshot-bound-approval")],
    )
    with SessionLocal() as db:
        revision = db.get(Revision, uuid.UUID(committed["revision_id"]))
        assert revision is not None
        revision.state = "READY"
        db.commit()
    return committed


def submit(committed: dict):
    return client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/"
        f"{committed['revision_id']}/approval-requests",
        headers=AUTH,
        json={"comment": "Ready for independent review"},
    )


def test_revision_actions_use_creator_and_review_assignment() -> None:
    committed = ready_item()
    base = f"/api/v1/items/{committed['item_id']}/revisions/{committed['revision_id']}"
    reviewer, _ = role_identity("reviewer")
    other, _ = role_identity("engineer")
    creator_actions = client.get(f"{base}/action-availability", headers=AUTH)
    assert creator_actions.status_code == 200, creator_actions.text
    assert creator_actions.json()["is_creator"] is True
    assert creator_actions.json()["worker_configured"] is True
    assert client.get(f"{base}/action-availability", headers=reviewer).json()["can_review"] is False
    assert client.post(f"{base}/approval-requests", headers=other, json={}).status_code == 403
    assert submit(committed).status_code == 201
    actions = client.get(f"{base}/action-availability", headers=reviewer).json()
    assert actions["is_assigned_reviewer"] is True
    assert actions["can_review"] is True
    assert actions["can_release"] is True


def test_snapshot_bound_approval_enables_enforced_release(monkeypatch) -> None:
    committed = ready_item()
    settings = get_settings()
    monkeypatch.setattr(settings, "require_release_approval", True)

    blocked = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/{committed['revision_id']}/release",
        headers=AUTH,
    )
    assert blocked.status_code == 409
    assert "requires approval" in blocked.json()["detail"]

    requested = submit(committed)
    assert requested.status_code == 201, requested.text
    approval = requested.json()
    assert approval["status"] == "PENDING"
    history = client.get(
        f"/api/v1/items/{committed['item_id']}/revisions/"
        f"{committed['revision_id']}/approval-requests",
        headers=AUTH,
    ).json()
    assert history[0]["can_withdraw"] is True
    assert history[0]["can_decide"] is False

    self_decision = client.post(
        f"/api/v1/approval-requests/{approval['id']}/decision",
        headers=AUTH,
        json={"decision": "APPROVED", "comment": "Self approval attempt"},
    )
    assert self_decision.status_code == 409

    decided = client.post(
        f"/api/v1/approval-requests/{approval['id']}/decision",
        headers=role_auth("reviewer"),
        json={"decision": "APPROVED", "comment": "Evidence reviewed"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "APPROVED"

    released = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/{committed['revision_id']}/release",
        headers=AUTH,
    )
    assert released.status_code == 200, released.text
    assert released.json()["lifecycle_state"] == "RELEASED"


def test_duplicate_pending_request_is_rejected_and_history_is_listed() -> None:
    committed = ready_item()
    first = submit(committed)
    assert first.status_code == 201, first.text
    duplicate = submit(committed)
    assert duplicate.status_code == 409

    history = client.get(
        f"/api/v1/items/{committed['item_id']}/revisions/"
        f"{committed['revision_id']}/approval-requests",
        headers=AUTH,
    )
    assert history.status_code == 200
    assert [entry["id"] for entry in history.json()] == [first.json()["id"]]


def test_new_snapshot_makes_pending_request_stale() -> None:
    committed = ready_item()
    requested = submit(committed)
    assert requested.status_code == 201, requested.text
    revision_id = uuid.UUID(committed["revision_id"])
    with SessionLocal() as db:
        latest = db.query(RevisionContentSnapshot).filter_by(revision_id=revision_id).first()
        assert latest is not None
        db.add(
            RevisionContentSnapshot(
                revision_id=revision_id,
                sequence=latest.sequence + 1,
                commit_id=uuid.uuid4(),
                comment="Changed after review request",
                created_by=latest.created_by,
            )
        )
        db.commit()

    decision = client.post(
        f"/api/v1/approval-requests/{requested.json()['id']}/decision",
        headers=role_auth("reviewer"),
        json={"decision": "APPROVED", "comment": "Attempt to approve stale content"},
    )
    assert decision.status_code == 409
    assert "stale" in decision.json()["detail"]

    history = client.get(
        f"/api/v1/items/{committed['item_id']}/revisions/"
        f"{committed['revision_id']}/approval-requests",
        headers=AUTH,
    )
    assert history.json()[0]["status"] == "STALE"
    revision = client.get(
        f"/api/v1/items/{committed['item_id']}/revisions/{committed['revision_id']}",
        headers=AUTH,
    )
    assert revision.json()["approval_status"] == "STALE"

    resubmitted = submit(committed)
    assert resubmitted.status_code == 201, resubmitted.text
    assert resubmitted.json()["snapshot_id"] != requested.json()["snapshot_id"]


def test_requester_can_withdraw_and_another_user_cannot() -> None:
    committed = ready_item()
    requested = submit(committed).json()
    forbidden = client.post(
        f"/api/v1/approval-requests/{requested['id']}/withdraw",
        headers=role_auth("engineer"),
    )
    assert forbidden.status_code == 403

    withdrawn = client.post(
        f"/api/v1/approval-requests/{requested['id']}/withdraw",
        headers=AUTH,
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "WITHDRAWN"
    assert submit(committed).status_code == 201


def test_role_matrix_for_submit_decide_and_list() -> None:
    committed = ready_item()
    reader = role_auth("reader")
    reader_submit = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/"
        f"{committed['revision_id']}/approval-requests",
        headers=reader,
        json={},
    )
    assert reader_submit.status_code == 403

    requested = submit(committed).json()
    assert client.get("/api/v1/approval-requests/inbox", headers=reader).status_code == 403
    inbox = client.get(
        "/api/v1/approval-requests/inbox", headers=role_auth("reviewer")
    )
    assert inbox.status_code == 200
    matching = [row for row in inbox.json() if row["approval"]["id"] == requested["id"]]
    assert len(matching) == 1
    assert matching[0]["snapshot_sequence"] == 1
    assert matching[0]["item_number"].startswith("PF-APPROVAL-")
    for role in ("reader", "engineer"):
        rejected = client.post(
            f"/api/v1/approval-requests/{requested['id']}/decision",
            headers=role_auth(role),
            json={"decision": "APPROVED", "comment": "Unauthorized decision"},
        )
        assert rejected.status_code == 403

    for role in ("reader", "engineer", "reviewer", "administrator"):
        listed = client.get(
            f"/api/v1/items/{committed['item_id']}/revisions/"
            f"{committed['revision_id']}/approval-requests",
            headers=role_auth(role),
        )
        assert listed.status_code == 200

    approved = client.post(
        f"/api/v1/approval-requests/{requested['id']}/decision",
        headers=role_auth("administrator"),
        json={"decision": "APPROVED", "comment": "Authorized decision"},
    )
    assert approved.status_code == 200
    repeated = client.post(
        f"/api/v1/approval-requests/{requested['id']}/decision",
        headers=role_auth("reviewer"),
        json={"decision": "REJECTED", "comment": "Too late"},
    )
    assert repeated.status_code == 409


def test_ready_with_warnings_requires_explicit_reviewer_acknowledgement() -> None:
    committed = ready_item()
    with SessionLocal() as db:
        revision = db.get(Revision, uuid.UUID(committed["revision_id"]))
        assert revision is not None
        revision.state = "READY_WITH_WARNINGS"
        db.commit()
    requested = submit(committed).json()
    reviewer = role_auth("reviewer")
    missing_acknowledgement = client.post(
        f"/api/v1/approval-requests/{requested['id']}/decision",
        headers=reviewer,
        json={"decision": "APPROVED", "comment": "Reviewed the content"},
    )
    assert missing_acknowledgement.status_code == 409
    assert "acknowledge" in missing_acknowledgement.json()["detail"]

    approved = client.post(
        f"/api/v1/approval-requests/{requested['id']}/decision",
        headers=reviewer,
        json={
            "decision": "APPROVED",
            "comment": "Reviewed the content and processing warnings",
            "warnings_acknowledged": True,
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["warnings_acknowledged"] is True


def test_snapshotted_policy_requires_distinct_reviewers(monkeypatch) -> None:
    committed = ready_item()
    settings = get_settings()
    monkeypatch.setattr(settings, "approval_required_count", 2)
    monkeypatch.setattr(settings, "require_release_approval", True)
    requested = submit(committed)
    assert requested.status_code == 201, requested.text
    approval = requested.json()
    assert approval["required_approvals"] == 2
    assert approval["policy"]["eligible_roles"] == ["reviewer", "administrator"]

    first_reviewer = role_auth("reviewer")
    first = client.post(
        f"/api/v1/approval-requests/{approval['id']}/decision",
        headers=first_reviewer,
        json={"decision": "APPROVED", "comment": "First independent approval"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "PENDING"
    assert first.json()["approval_count"] == 1
    repeated = client.post(
        f"/api/v1/approval-requests/{approval['id']}/decision",
        headers=first_reviewer,
        json={"decision": "APPROVED", "comment": "Duplicate approval"},
    )
    assert repeated.status_code == 409

    blocked = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/{committed['revision_id']}/release",
        headers=AUTH,
    )
    assert blocked.status_code == 409

    second = client.post(
        f"/api/v1/approval-requests/{approval['id']}/decision",
        headers=role_auth("administrator"),
        json={"decision": "APPROVED", "comment": "Second independent approval"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "APPROVED"
    assert second.json()["approval_count"] == 2


def test_request_and_decision_create_deduplicated_notifications() -> None:
    reviewer = role_auth("reviewer")
    committed = ready_item()
    requested = submit(committed)
    assert requested.status_code == 201, requested.text
    approval = requested.json()

    with SessionLocal() as db:
        review_notifications = list(
            db.query(UserNotification).filter_by(
                kind="APPROVAL_REQUESTED",
                entity_id=uuid.UUID(approval["id"]),
            )
        )
        assert review_notifications
        assert all(
            notification.details_json["revision_id"] == committed["revision_id"]
            for notification in review_notifications
        )

    decided = client.post(
        f"/api/v1/approval-requests/{approval['id']}/decision",
        headers=reviewer,
        json={"decision": "APPROVED", "comment": "Notification workflow reviewed"},
    )
    assert decided.status_code == 200, decided.text

    with SessionLocal() as db:
        decision_notifications = list(
            db.query(UserNotification).filter_by(
                kind="APPROVAL_APPROVED",
                entity_id=uuid.UUID(approval["id"]),
                user_id=uuid.UUID(approval["requested_by"]),
            )
        )
        assert len(decision_notifications) == 1
        assert decision_notifications[0].details_json["decision"] == "APPROVED"


def test_named_group_snapshots_membership_and_restricts_routing() -> None:
    assigned_headers, assigned_id = role_identity("reviewer")
    unassigned_headers, unassigned_id = role_identity("reviewer")
    created_group = client.post(
        "/api/v1/admin/approval-groups",
        headers=AUTH,
        json={"name": f"Mechanical Review {uuid.uuid4()}", "member_ids": [str(assigned_id)]},
    )
    assert created_group.status_code == 201, created_group.text
    group = created_group.json()
    assert group["member_ids"] == [str(assigned_id)]

    committed = ready_item()
    requested = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/"
        f"{committed['revision_id']}/approval-requests",
        headers=AUTH,
        json={"comment": "Route to mechanical review", "reviewer_group_id": group["id"]},
    )
    assert requested.status_code == 201, requested.text
    approval = requested.json()
    assert approval["reviewer_group_name"].startswith("Mechanical Review")
    assert approval["policy"]["eligible_user_ids"] == [str(assigned_id)]
    action_url = (
        f"/api/v1/items/{committed['item_id']}/revisions/"
        f"{committed['revision_id']}/action-availability"
    )
    assert client.get(action_url, headers=assigned_headers).json()["can_review"] is True
    assert client.get(action_url, headers=unassigned_headers).json()["can_review"] is False

    unassigned_inbox = client.get(
        "/api/v1/approval-requests/inbox", headers=unassigned_headers
    )
    assert all(row["approval"]["id"] != approval["id"] for row in unassigned_inbox.json())
    forbidden = client.post(
        f"/api/v1/approval-requests/{approval['id']}/decision",
        headers=unassigned_headers,
        json={"decision": "APPROVED", "comment": "Not assigned"},
    )
    assert forbidden.status_code == 403

    updated_group = client.patch(
        f"/api/v1/admin/approval-groups/{group['id']}",
        headers=AUTH,
        json={"member_ids": [str(unassigned_id)]},
    )
    assert updated_group.status_code == 200, updated_group.text
    approved = client.post(
        f"/api/v1/approval-requests/{approval['id']}/decision",
        headers=assigned_headers,
        json={"decision": "APPROVED", "comment": "Assigned snapshot reviewer"},
    )
    assert approved.status_code == 200, approved.text


def test_overdue_escalation_is_durable_and_deduplicated() -> None:
    committed = ready_item()
    approval = submit(committed).json()
    with SessionLocal() as db:
        request = db.get(RevisionApprovalRequest, uuid.UUID(approval["id"]))
        assert request is not None
        request.due_at = datetime.now(UTC) - timedelta(minutes=5)
        db.commit()

    first = client.post(
        "/api/v1/admin/maintenance/approval-escalations/run", headers=AUTH
    )
    assert first.status_code == 200, first.text
    assert first.json()["overdue_requests"] >= 1
    assert first.json()["created"] >= 1
    second = client.post(
        "/api/v1/admin/maintenance/approval-escalations/run", headers=AUTH
    )
    assert second.status_code == 200, second.text
    assert second.json()["created"] == 0

    with SessionLocal() as db:
        escalations = list(
            db.query(UserNotification).filter_by(
                kind="APPROVAL_OVERDUE", entity_id=uuid.UUID(approval["id"])
            )
        )
        assert escalations


def test_active_delegation_is_snapshotted_for_group_routing() -> None:
    grantor_headers, grantor_id = role_identity("reviewer")
    delegate_headers, delegate_id = role_identity("reviewer")
    delegation = client.post(
        "/api/v1/approval-delegations",
        headers=grantor_headers,
        json={
            "delegate_id": str(delegate_id),
            "ends_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
    )
    assert delegation.status_code == 201, delegation.text
    group = client.post(
        "/api/v1/admin/approval-groups",
        headers=AUTH,
        json={"name": f"Delegated Review {uuid.uuid4()}", "member_ids": [str(grantor_id)]},
    ).json()
    committed = ready_item()
    requested = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/"
        f"{committed['revision_id']}/approval-requests",
        headers=AUTH,
        json={"reviewer_group_id": group["id"]},
    )
    assert requested.status_code == 201, requested.text
    approval = requested.json()
    assert str(delegate_id) in approval["policy"]["eligible_user_ids"]
    assert approval["policy"]["delegations"] == [
        {"grantor_id": str(grantor_id), "delegate_id": str(delegate_id)}
    ]

    revoked = client.delete(
        f"/api/v1/approval-delegations/{delegation.json()['id']}",
        headers=grantor_headers,
    )
    assert revoked.status_code == 200, revoked.text
    approved = client.post(
        f"/api/v1/approval-requests/{approval['id']}/decision",
        headers=delegate_headers,
        json={"decision": "APPROVED", "comment": "Acting under snapshotted delegation"},
    )
    assert approved.status_code == 200, approved.text


def test_emergency_review_requires_independent_administrator(monkeypatch) -> None:
    committed = ready_item()
    approver_headers = role_auth("administrator")
    settings = get_settings()
    monkeypatch.setattr(settings, "require_release_approval", True)
    engineer_attempt = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/"
        f"{committed['revision_id']}/approval-requests",
        headers=role_auth("engineer"),
        json={"emergency": True, "incident_reference": "INC-403"},
    )
    assert engineer_attempt.status_code == 403
    requested = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/"
        f"{committed['revision_id']}/approval-requests",
        headers=AUTH,
        json={
            "emergency": True,
            "incident_reference": "INC-1001",
            "comment": "Restore production tooling",
        },
    )
    assert requested.status_code == 201, requested.text
    approval = requested.json()
    assert approval["policy_version"] == "emergency-1"
    assert approval["policy"]["eligible_roles"] == ["administrator"]
    assert approval["policy"]["incident_reference"] == "INC-1001"
    reviewer_attempt = client.post(
        f"/api/v1/approval-requests/{approval['id']}/decision",
        headers=role_auth("reviewer"),
        json={"decision": "APPROVED", "comment": "Reviewer is insufficient"},
    )
    assert reviewer_attempt.status_code == 403
    decided = client.post(
        f"/api/v1/approval-requests/{approval['id']}/decision",
        headers=approver_headers,
        json={"decision": "APPROVED", "comment": "Independent emergency authorization"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "APPROVED"
    released = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/{committed['revision_id']}/release",
        headers=AUTH,
    )
    assert released.status_code == 200, released.text


def test_expired_emergency_review_cannot_be_approved() -> None:
    committed = ready_item()
    approval = client.post(
        f"/api/v1/items/{committed['item_id']}/revisions/"
        f"{committed['revision_id']}/approval-requests",
        headers=AUTH,
        json={"emergency": True, "incident_reference": "INC-EXPIRED", "comment": "Urgent"},
    ).json()
    with SessionLocal() as db:
        request = db.get(RevisionApprovalRequest, uuid.UUID(approval["id"]))
        assert request is not None
        request.due_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    decision = client.post(
        f"/api/v1/approval-requests/{approval['id']}/decision",
        headers=role_auth("administrator"),
        json={"decision": "APPROVED", "comment": "Too late"},
    )
    assert decision.status_code == 409
    assert "expired" in decision.json()["detail"]
    history = client.get(
        f"/api/v1/items/{committed['item_id']}/revisions/"
        f"{committed['revision_id']}/approval-requests",
        headers=AUTH,
    ).json()
    assert history[0]["status"] == "EXPIRED"
