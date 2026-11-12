import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from modelryx.api.parts import require_engineer
from modelryx.api.staging import _owned_staging
from modelryx.core.audit import record_audit
from modelryx.core.auth import Principal, current_principal
from modelryx.core.db import get_db
from modelryx.services.staging_workflow import (
    enqueue_staging_workflow,
    set_workflow,
    workflow_data,
)

router = APIRouter(prefix="/api/v1/staging", tags=["staging-workflows"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]


class StagingWorkflowResponse(BaseModel):
    staging_id: uuid.UUID
    state: str
    stage: str
    progress_percent: int
    error_detail: str | None = None


def _response(staging_id: uuid.UUID, value: dict) -> StagingWorkflowResponse:
    return StagingWorkflowResponse(
        staging_id=staging_id,
        state=value.get("state", "NOT_SUBMITTED"),
        stage=value.get("stage", "UPLOAD"),
        progress_percent=int(value.get("progress_percent", 0)),
        error_detail=value.get("error_detail"),
    )


@router.get("/{staging_id}/workflow", response_model=StagingWorkflowResponse)
def get_staging_workflow(
    staging_id: uuid.UUID, db: Db, principal: Actor
) -> StagingWorkflowResponse:
    staging = _owned_staging(db, staging_id, principal)
    return _response(staging.id, workflow_data(staging))


@router.post("/{staging_id}/submit", response_model=StagingWorkflowResponse, status_code=202)
def submit_staging_workflow(
    staging_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
) -> StagingWorkflowResponse:
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    if staging.status in {"CANCELLED", "PROMOTED", "COMPLETED", "COMMITTING"}:
        raise HTTPException(status_code=409, detail="Staging session cannot be submitted")
    workflow = enqueue_staging_workflow(db, staging)
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.workflow_queued",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={"stage": workflow["stage"], "commit_id": workflow["commit_id"]},
    )
    db.commit()
    return _response(staging.id, workflow)


@router.post("/{staging_id}/retry", response_model=StagingWorkflowResponse, status_code=202)
def retry_staging_workflow(
    staging_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
) -> StagingWorkflowResponse:
    return submit_staging_workflow(staging_id, request, db, principal)


@router.post("/{staging_id}/pause", response_model=StagingWorkflowResponse)
def pause_staging_workflow(
    staging_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
) -> StagingWorkflowResponse:
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    workflow = workflow_data(staging)
    if workflow.get("state") != "QUEUED":
        raise HTTPException(
            status_code=409,
            detail="Only queued work can be paused; a running commit must finish atomically",
        )
    set_workflow(
        staging,
        state="PAUSED",
        stage=workflow["stage"],
        progress_percent=int(workflow["progress_percent"]),
        commit_id=uuid.UUID(workflow["commit_id"]),
    )
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.workflow_paused",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
    )
    db.commit()
    return _response(staging.id, workflow_data(staging))
