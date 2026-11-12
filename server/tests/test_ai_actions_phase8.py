import uuid
from datetime import UTC, datetime, timedelta

import pytest
from modelryx.actions.models import ProposalConfirm, ProposalCreate, RequestValidationArguments
from modelryx.actions.registry import TOOL_REGISTRY, tool_for
from modelryx.actions.service import ActionProposalService, staging_fingerprint
from modelryx.models import AIActionProposal, StagingSession
from pydantic import ValidationError


def staging() -> StagingSession:
    return StagingSession(
        id=uuid.uuid4(),
        client_staging_id=uuid.uuid4(),
        status="APPROVED",
        package_sha256="a" * 64,
        metadata_json={"identity": {"kind": "part"}},
        created_by=uuid.uuid4(),
        updated_at=datetime(2026, 8, 25, tzinfo=UTC),
    )


def proposal_for(value: StagingSession) -> AIActionProposal:
    return AIActionProposal(
        tool_name="request_validation",
        tool_version="1.0",
        risk="LOW",
        status="PENDING",
        arguments_json={"staging_id": str(value.id)},
        preview_json={},
        target_type="staging_session",
        target_id=value.id,
        state_fingerprint=staging_fingerprint(value),
        requested_by=value.created_by,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )


def test_registry_exposes_only_strict_confirmation_first_tool() -> None:
    tool = TOOL_REGISTRY["request_validation"]
    assert tool.mode == "PROPOSE"
    assert tool.risk == "LOW"
    assert tool.requires_confirmation is True
    assert tool.input_model is RequestValidationArguments
    with pytest.raises(ValueError, match="Unknown or disabled"):
        tool_for("delete_everything")


def test_tool_arguments_and_confirmation_reject_extra_or_false_values() -> None:
    with pytest.raises(ValidationError):
        RequestValidationArguments.model_validate(
            {"staging_id": str(uuid.uuid4()), "sql": "DROP TABLE parts"}
        )
    with pytest.raises(ValidationError):
        ProposalConfirm.model_validate({"confirmed": False, "idempotency_key": "abcdefgh"})
    with pytest.raises(ValidationError):
        ProposalConfirm.model_validate(
            {"confirmed": True, "idempotency_key": "valid-key\nforged-audit-entry"}
        )


def test_proposal_creation_persists_exact_arguments_and_does_not_mutate_target() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.added = []

        def add(self, value) -> None:
            self.added.append(value)

        def flush(self) -> None:
            pass

    value = staging()
    original_status = value.status
    original_metadata = value.metadata_json.copy()
    db = FakeSession()
    proposal = ActionProposalService(db).create(
        payload=ProposalCreate(
            tool_name="request_validation", arguments={"staging_id": str(value.id)}
        ),
        staging=value,
        user_id=value.created_by,
        role="engineer",
    )

    assert db.added == [proposal]
    assert proposal.arguments_json == {"staging_id": str(value.id)}
    assert proposal.target_id == value.id
    assert proposal.status == "PENDING"
    assert proposal.preview_json["persistent_change"]
    assert value.status == original_status
    assert value.metadata_json == original_metadata


def test_fingerprint_changes_when_authoritative_state_changes() -> None:
    value = staging()
    before = staging_fingerprint(value)
    assert staging_fingerprint(value) == before
    value.status = "PROMOTED"
    assert staging_fingerprint(value) != before


def test_confirmation_detects_stale_and_expired_proposals() -> None:
    value = staging()
    stale = proposal_for(value)
    value.metadata_json = {"identity": {"kind": "assembly"}}
    with pytest.raises(ValueError, match="Target state changed"):
        ActionProposalService.refresh_state(stale, value)
    assert stale.status == "STALE"

    current = staging()
    expired = proposal_for(current)
    expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ValueError, match="expired"):
        ActionProposalService.refresh_state(expired, current)
    assert expired.status == "EXPIRED"


def test_role_policy_rejects_readers() -> None:
    tool = tool_for("request_validation")
    with pytest.raises(PermissionError):
        ActionProposalService.authorize(tool, "reader")
    ActionProposalService.authorize(tool, "engineer")
