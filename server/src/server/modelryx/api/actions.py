import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.actions.models import (
    ExecutionResponse,
    ProposalConfirm,
    ProposalCreate,
    ProposalResponse,
    ToolDescription,
)
from modelryx.actions.registry import TOOL_REGISTRY, tool_for
from modelryx.actions.service import ActionProposalService
from modelryx.api.staging import _owned_staging
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import AIActionExecution, AIActionProposal
from modelryx.validation.service import ValidationService

router = APIRouter(prefix="/api/v1/ai/actions", tags=["ai-actions"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


def proposal_response(value: AIActionProposal) -> ProposalResponse:
    return ProposalResponse(
        id=value.id,
        tool_name=value.tool_name,
        tool_version=value.tool_version,
        risk=value.risk,
        status=value.status,
        arguments=value.arguments_json,
        preview=value.preview_json,
        target_type=value.target_type,
        target_id=value.target_id,
        requested_by=value.requested_by,
        confirmed_by=value.confirmed_by,
        created_at=value.created_at,
        expires_at=value.expires_at,
        decided_at=value.decided_at,
    )


def execution_response(value: AIActionExecution) -> ExecutionResponse:
    return ExecutionResponse(
        id=value.id,
        proposal_id=value.proposal_id,
        idempotency_key=value.idempotency_key,
        status=value.status,
        result=value.result_json,
        error=value.error_json,
        executed_by=value.executed_by,
        started_at=value.started_at,
        completed_at=value.completed_at,
    )


def owned_proposal(
    db: Session, proposal_id: uuid.UUID, principal: Principal, *, lock: bool = False
) -> AIActionProposal:
    if lock:
        proposal = db.scalar(
            select(AIActionProposal).where(AIActionProposal.id == proposal_id).with_for_update()
        )
    else:
        proposal = db.get(AIActionProposal, proposal_id)
    user = user_for_principal(db, principal)
    if proposal is None or (principal.role != "administrator" and proposal.requested_by != user.id):
        raise HTTPException(status_code=404, detail="Action proposal not found")
    return proposal


@router.get("/tools", response_model=list[ToolDescription])
def list_tools(principal: Actor) -> list[ToolDescription]:
    return [
        tool.public() for tool in TOOL_REGISTRY.values() if principal.role in tool.required_roles
    ]


@router.post("/proposals", response_model=ProposalResponse, status_code=201)
def create_proposal(
    payload: ProposalCreate, request: Request, db: Db, principal: Actor
) -> ProposalResponse:
    user = user_for_principal(db, principal)
    try:
        tool = tool_for(payload.tool_name)
        arguments = tool.input_model.model_validate(payload.arguments)
        staging = _owned_staging(db, arguments.staging_id, principal)
        proposal = ActionProposalService(db).create(
            payload, staging=staging, user_id=user.id, role=principal.role
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_audit(
        db,
        actor_id=user.id,
        action="ai_action.proposed",
        entity_type="ai_action_proposal",
        entity_id=proposal.id,
        request_id=request.state.request_id,
        details={"tool": proposal.tool_name, "risk": proposal.risk},
    )
    db.commit()
    return proposal_response(proposal)


@router.get("/proposals/{proposal_id}", response_model=ProposalResponse)
def get_proposal(proposal_id: uuid.UUID, db: Db, principal: Actor) -> ProposalResponse:
    return proposal_response(owned_proposal(db, proposal_id, principal))


@router.post("/proposals/{proposal_id}/cancel", response_model=ProposalResponse)
def cancel_proposal(
    proposal_id: uuid.UUID, request: Request, db: Db, principal: Actor
) -> ProposalResponse:
    proposal = owned_proposal(db, proposal_id, principal, lock=True)
    if proposal.status != "PENDING":
        raise HTTPException(status_code=409, detail=f"Proposal is {proposal.status.lower()}")
    user = user_for_principal(db, principal)
    proposal.status = "CANCELLED"
    proposal.decided_at = datetime.now(UTC)
    record_audit(
        db,
        actor_id=user.id,
        action="ai_action.cancelled",
        entity_type="ai_action_proposal",
        entity_id=proposal.id,
        request_id=request.state.request_id,
        details={"tool": proposal.tool_name},
    )
    db.commit()
    return proposal_response(proposal)


@router.post("/proposals/{proposal_id}/confirm", response_model=ExecutionResponse)
def confirm_proposal(
    proposal_id: uuid.UUID,
    payload: ProposalConfirm,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> ExecutionResponse:
    proposal = owned_proposal(db, proposal_id, principal, lock=True)
    user = user_for_principal(db, principal)
    service = ActionProposalService(db)
    existing = service.execution_for_key(payload.idempotency_key)
    if existing is not None:
        if existing.proposal_id != proposal.id:
            raise HTTPException(
                status_code=409, detail="Idempotency key belongs to another proposal"
            )
        return execution_response(existing)
    staging = _owned_staging(db, proposal.target_id, principal)
    try:
        service.authorize(tool_for(proposal.tool_name), principal.role)
        service.refresh_state(proposal, staging)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    proposal.status = "CONFIRMED"
    proposal.confirmed_by = user.id
    proposal.decided_at = datetime.now(UTC)
    record_audit(
        db,
        actor_id=user.id,
        action="ai_action.confirmed",
        entity_type="ai_action_proposal",
        entity_id=proposal.id,
        request_id=request.state.request_id,
        details={
            "tool": proposal.tool_name,
            "tool_version": proposal.tool_version,
            "idempotency_key": payload.idempotency_key,
        },
    )
    execution = AIActionExecution(
        proposal_id=proposal.id,
        idempotency_key=payload.idempotency_key,
        status="RUNNING",
        executed_by=user.id,
    )
    db.add(execution)
    db.flush()
    try:
        run = ValidationService(db, settings).run(staging, user.id)
        execution.status = "SUCCEEDED"
        execution.result_json = {
            "validation_id": str(run.id),
            "status": run.status,
            "summary": run.summary_json,
        }
        proposal.status = "EXECUTED"
    except Exception as exc:
        execution.status = "FAILED"
        execution.error_json = {
            "code": "TOOL_EXECUTION_FAILED",
            "message": str(exc)[:1000] or "The action failed without an error message",
        }
        proposal.status = "FAILED"
    execution.completed_at = datetime.now(UTC)
    record_audit(
        db,
        actor_id=user.id,
        action="ai_action.executed",
        entity_type="ai_action_execution",
        entity_id=execution.id,
        request_id=request.state.request_id,
        details={
            "proposal_id": str(proposal.id),
            "tool": proposal.tool_name,
            "status": execution.status,
        },
    )
    db.commit()
    return execution_response(execution)


@router.get("/executions/{execution_id}", response_model=ExecutionResponse)
def get_execution(execution_id: uuid.UUID, db: Db, principal: Actor) -> ExecutionResponse:
    execution = db.get(AIActionExecution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Action execution not found")
    owned_proposal(db, execution.proposal_id, principal)
    return execution_response(execution)
