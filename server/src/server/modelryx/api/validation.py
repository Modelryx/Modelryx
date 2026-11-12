import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.api.parts import require_engineer
from modelryx.api.staging import _owned_staging
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import ValidationFinding, ValidationFindingAction, ValidationRun
from modelryx.validation.service import ValidationService, can_acknowledge

router = APIRouter(prefix="/api/v1/staging", tags=["validation"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


class FindingResponse(BaseModel):
    id: uuid.UUID
    code: str
    category: str
    severity: str
    blocking: bool
    state: str
    title: str
    message: str
    source: dict[str, Any]
    location: dict[str, Any]
    details: dict[str, Any]
    evidence: dict[str, Any]
    actions: list[str]
    acknowledged: bool = False
    acknowledged_by: uuid.UUID | None = None
    acknowledged_at: str | None = None
    acknowledgement_comment: str | None = None


class AcknowledgeFindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=500)


class FindingActionResponse(BaseModel):
    id: uuid.UUID
    finding_id: uuid.UUID
    action: str
    comment: str | None
    actor_id: uuid.UUID
    created_at: str


class ValidationResponse(BaseModel):
    validation_id: uuid.UUID
    staging_id: uuid.UUID
    snapshot_version: int
    run_number: int
    status: str
    report_hash: str
    policy_version: str
    summary: dict[str, int]
    validator_states: dict[str, str]
    findings: list[FindingResponse]


class AnalysisResponse(BaseModel):
    id: uuid.UUID
    validation_id: uuid.UUID
    mode: str
    model: str | None
    prompt_version: str
    output: dict[str, Any]
    warnings: list[str]


def _report(service: ValidationService, run: Any) -> ValidationResponse:
    def serialize_finding(f: Any) -> FindingResponse:
        acknowledgement = service.acknowledgement(f.id)
        return FindingResponse(
            id=f.id,
            code=f.code,
            category=f.category,
            severity=f.severity,
            blocking=f.blocking,
            state=f.state,
            title=f.title,
            message=f.message,
            source=f.source_json,
            location=f.location_json,
            details=f.details_json,
            evidence=f.evidence_json,
            actions=f.actions_json,
            acknowledged=acknowledgement is not None,
            acknowledged_by=acknowledgement.actor_id if acknowledgement else None,
            acknowledged_at=acknowledgement.created_at.isoformat() if acknowledgement else None,
            acknowledgement_comment=acknowledgement.comment if acknowledgement else None,
        )

    return ValidationResponse(
        validation_id=run.id,
        staging_id=run.staging_id,
        snapshot_version=run.snapshot_version,
        run_number=run.run_number,
        status=run.status,
        report_hash=run.report_hash,
        policy_version=run.policy_version,
        summary=run.summary_json,
        validator_states=run.validator_states_json,
        findings=[serialize_finding(f) for f in service.findings(run.id)],
    )


def _latest(service: ValidationService, staging_id: uuid.UUID) -> Any:
    run = service.latest(staging_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Validation has not been run")
    return run


@router.post("/{staging_id}/validate", response_model=ValidationResponse)
@router.post("/{staging_id}/revalidate", response_model=ValidationResponse)
def validate(
    staging_id: uuid.UUID, request: Request, db: Db, principal: Actor, settings: Config
) -> ValidationResponse:
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    if staging.status in {"CANCELLED", "PROMOTED"}:
        raise HTTPException(status_code=409, detail="Staging session is no longer validatable")
    actor = user_for_principal(db, principal)
    service = ValidationService(db, settings)
    previous = service.latest(staging.id)
    run = service.run(staging, actor.id)
    record_audit(
        db,
        actor_id=actor.id,
        action="validation.revalidated" if previous is not None else "validation.completed",
        entity_type="validation_run",
        entity_id=run.id,
        request_id=request.state.request_id,
        details={
            "staging_id": str(staging.id),
            "run_number": run.run_number,
            "status": run.status,
            "summary": run.summary_json,
        },
    )
    db.commit()
    return _report(service, run)


@router.get("/{staging_id}/validation", response_model=ValidationResponse)
def get_validation(
    staging_id: uuid.UUID, db: Db, principal: Actor, settings: Config
) -> ValidationResponse:
    _owned_staging(db, staging_id, principal)
    service = ValidationService(db, settings)
    return _report(service, _latest(service, staging_id))


@router.get("/{staging_id}/validation/history", response_model=list[ValidationResponse])
def get_validation_history(
    staging_id: uuid.UUID,
    db: Db,
    principal: Actor,
    settings: Config,
    limit: int = Query(default=20, ge=1, le=50),
) -> list[ValidationResponse]:
    _owned_staging(db, staging_id, principal)
    service = ValidationService(db, settings)
    return [_report(service, run) for run in service.history(staging_id, limit)]


@router.get("/{staging_id}/validation/findings", response_model=list[FindingResponse])
def get_findings(
    staging_id: uuid.UUID,
    db: Db,
    principal: Actor,
    settings: Config,
    severity: str | None = Query(default=None, pattern="^(INFO|WARNING|ERROR|CRITICAL)$"),
    category: str | None = None,
    blocking: bool | None = None,
) -> list[FindingResponse]:
    _owned_staging(db, staging_id, principal)
    service = ValidationService(db, settings)
    findings = service.findings(_latest(service, staging_id).id)
    if severity is not None:
        findings = [f for f in findings if f.severity == severity]
    if category is not None:
        findings = [f for f in findings if f.category == category]
    if blocking is not None:
        findings = [f for f in findings if f.blocking is blocking]
    report = _report(service, _latest(service, staging_id))
    allowed_ids = {finding.id for finding in findings}
    return [finding for finding in report.findings if finding.id in allowed_ids]


@router.post(
    "/{staging_id}/validation/findings/{finding_id}/acknowledge",
    response_model=FindingActionResponse,
)
def acknowledge_finding(
    staging_id: uuid.UUID,
    finding_id: uuid.UUID,
    payload: AcknowledgeFindingRequest,
    request: Request,
    db: Db,
    principal: Actor,
) -> FindingActionResponse:
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    finding = db.get(ValidationFinding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="Validation finding not found")
    run = db.get(ValidationRun, finding.validation_run_id)
    if run is None or run.staging_id != staging.id:
        raise HTTPException(status_code=404, detail="Validation finding not found")
    if not can_acknowledge(finding):
        raise HTTPException(
            status_code=409,
            detail="Only acknowledgeable non-blocking warnings may be acknowledged",
        )
    existing = db.scalar(
        select(ValidationFindingAction).where(ValidationFindingAction.finding_id == finding.id)
    )
    if existing is None:
        actor = user_for_principal(db, principal)
        existing = ValidationFindingAction(
            finding_id=finding.id,
            action="ACKNOWLEDGED",
            comment=payload.comment,
            actor_id=actor.id,
        )
        db.add(existing)
        db.flush()
        record_audit(
            db,
            actor_id=actor.id,
            action="validation.finding_acknowledged",
            entity_type="validation_finding",
            entity_id=finding.id,
            request_id=request.state.request_id,
            details={"validation_run_id": str(run.id), "code": finding.code},
        )
        db.commit()
    return FindingActionResponse(
        id=existing.id,
        finding_id=existing.finding_id,
        action=existing.action,
        comment=existing.comment,
        actor_id=existing.actor_id,
        created_at=existing.created_at.isoformat(),
    )


@router.get("/{staging_id}/validation/ai-analysis", response_model=AnalysisResponse)
def get_ai_analysis(
    staging_id: uuid.UUID, db: Db, principal: Actor, settings: Config
) -> AnalysisResponse:
    _owned_staging(db, staging_id, principal)
    service = ValidationService(db, settings)
    analysis = service.analysis(_latest(service, staging_id))
    db.commit()
    return AnalysisResponse(
        id=analysis.id,
        validation_id=analysis.validation_run_id,
        mode=analysis.mode,
        model=analysis.model,
        prompt_version=analysis.prompt_version,
        output=analysis.output_json,
        warnings=analysis.warnings_json,
    )
