import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.actions.models import ProposalCreate
from modelryx.actions.registry import ToolDefinition, tool_for
from modelryx.models import AIActionExecution, AIActionProposal, StagingSession

PROPOSAL_TTL_MINUTES = 15


def fingerprint(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def staging_fingerprint(staging: StagingSession) -> str:
    return fingerprint(
        {
            "id": staging.id,
            "status": staging.status,
            "package_sha256": staging.package_sha256,
            "metadata": staging.metadata_json,
            "updated_at": staging.updated_at,
        }
    )


class ActionProposalService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def authorize(tool: ToolDefinition, role: str) -> None:
        if role not in tool.required_roles:
            raise PermissionError(f"Role {role} cannot use {tool.name}")

    def create(
        self,
        payload: ProposalCreate,
        *,
        staging: StagingSession,
        user_id: UUID,
        role: str,
    ) -> AIActionProposal:
        tool = tool_for(payload.tool_name)
        self.authorize(tool, role)
        arguments = tool.input_model.model_validate(payload.arguments).model_dump(mode="json")
        if UUID(str(arguments["staging_id"])) != staging.id:
            raise ValueError("Tool target does not match the authorized staging session")
        if staging.status in {"CANCELLED", "PROMOTED"}:
            raise ValueError("Staging session is no longer validatable")
        proposal = AIActionProposal(
            tool_name=tool.name,
            tool_version=tool.version,
            risk=tool.risk,
            status="PENDING",
            arguments_json=arguments,
            preview_json={
                "title": "Run staging validation",
                "summary": "Create a new immutable deterministic validation run.",
                "target": str(staging.id),
                "persistent_change": "A validation run and normalized findings will be added.",
                "confirmation_reason": "This operation creates persistent audit records.",
            },
            target_type="staging_session",
            target_id=staging.id,
            state_fingerprint=staging_fingerprint(staging),
            requested_by=user_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=PROPOSAL_TTL_MINUTES),
        )
        self.db.add(proposal)
        self.db.flush()
        return proposal

    @staticmethod
    def refresh_state(proposal: AIActionProposal, staging: StagingSession) -> None:
        now = datetime.now(UTC)
        if proposal.status != "PENDING":
            raise ValueError(f"Proposal is {proposal.status.lower()}")
        expires_at = proposal.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            proposal.status = "EXPIRED"
            proposal.decided_at = now
            raise ValueError("Proposal has expired")
        if proposal.state_fingerprint != staging_fingerprint(staging):
            proposal.status = "STALE"
            proposal.decided_at = now
            raise ValueError("Target state changed; create a new proposal")

    def execution_for_key(self, key: str) -> AIActionExecution | None:
        return self.db.scalar(
            select(AIActionExecution).where(AIActionExecution.idempotency_key == key)
        )
