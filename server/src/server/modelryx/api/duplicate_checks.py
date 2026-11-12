import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.api.admin import require_admin
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.duplicate_detection.reasoner import GatewayDuplicateReasoner
from modelryx.duplicate_detection.repository import DuplicateDetectionRepository
from modelryx.duplicate_detection.service import (
    DuplicateDetectionConfig,
    DuplicateDetectionService,
)
from modelryx.models import (
    DuplicateCandidate,
    DuplicateCheck,
    DuplicateFeedback,
    Item,
    Revision,
    StagingSession,
)
from modelryx.services.ai import AiGateway

router = APIRouter(prefix="/api/v1", tags=["duplicate-detection"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


class DuplicateCandidateResponse(BaseModel):
    id: uuid.UUID
    candidate_revision_id: uuid.UUID
    candidate_item_id: uuid.UUID
    item_number: str
    revision: str
    classification: str
    scores: dict[str, float | None]
    reasons: list[str]
    differences: list[str]
    recommended_action: str
    ai: dict[str, Any] | None


class DuplicateCheckResponse(BaseModel):
    id: uuid.UUID
    source_revision_id: uuid.UUID | None
    source_staging_id: uuid.UUID | None
    status: str
    algorithm_version: str
    candidate_count_before_filter: int
    candidate_count_after_filter: int
    exact_match_found: bool
    timings_ms: dict[str, float]
    candidates: list[DuplicateCandidateResponse]


class DuplicateMetricsResponse(BaseModel):
    checks: int
    exact_duplicate_rate: float
    average_candidates_before_filter: float
    average_candidates_after_filter: float
    average_duration_ms: float
    candidate_results: int
    ai_invocation_rate: float
    ai_classification_agreement_rate: float | None
    feedback_decisions: int
    user_override_rate: float | None


class DuplicateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["USE_EXISTING", "CREATE_NEW", "NOT_DUPLICATE"]
    candidate_id: uuid.UUID | None = None
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def candidate_required_for_candidate_decisions(self) -> "DuplicateDecisionRequest":
        if self.decision != "CREATE_NEW" and self.candidate_id is None:
            raise ValueError("candidate_id is required for this decision")
        return self


class DuplicateDecisionResponse(BaseModel):
    id: uuid.UUID
    duplicate_check_id: uuid.UUID
    candidate_id: uuid.UUID | None
    decision: str
    system_classification: str | None
    comment: str | None
    decided_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


def _source_for_actor(
    db: Session, revision_id: uuid.UUID, principal: Principal
) -> tuple[Revision, uuid.UUID]:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    item = db.get(Item, revision.part_id)
    user = user_for_principal(db, principal)
    if item is None or (
        principal.role != "administrator"
        and revision.state not in {"READY", "READY_WITH_WARNINGS"}
        and item.created_by != user.id
    ):
        raise HTTPException(status_code=404, detail="Revision not found")
    return revision, user.id


def _response(db: Session, check: DuplicateCheck) -> DuplicateCheckResponse:
    rows = db.execute(
        select(DuplicateCandidate, Revision, Item)
        .join(Revision, Revision.id == DuplicateCandidate.candidate_revision_id)
        .join(Item, Item.id == Revision.part_id)
        .where(DuplicateCandidate.duplicate_check_id == check.id)
    ).all()
    candidates = [
        DuplicateCandidateResponse(
            id=candidate.id,
            candidate_revision_id=revision.id,
            candidate_item_id=item.id,
            item_number=item.part_number,
            revision=revision.revision_code,
            classification=candidate.classification,
            scores=candidate.score_breakdown_json,
            reasons=candidate.reasons_json,
            differences=candidate.differences_json,
            recommended_action=candidate.recommended_action,
            ai=candidate.ai_result_json,
        )
        for candidate, revision, item in rows
    ]
    candidates.sort(key=lambda value: value.scores.get("combined") or 0.0, reverse=True)
    return DuplicateCheckResponse(
        id=check.id,
        source_revision_id=check.source_revision_id,
        source_staging_id=check.source_staging_id,
        status=check.status,
        algorithm_version=check.algorithm_version,
        candidate_count_before_filter=check.candidate_count_before_filter,
        candidate_count_after_filter=check.candidate_count_after_filter,
        exact_match_found=check.exact_match_found,
        timings_ms={
            "total": check.total_duration_ms,
            "metadata": check.metadata_duration_ms,
            "vector": check.vector_duration_ms,
            "ai": check.ai_duration_ms,
        },
        candidates=candidates,
    )


def _decision_response(feedback: DuplicateFeedback) -> DuplicateDecisionResponse:
    return DuplicateDecisionResponse(
        id=feedback.id,
        duplicate_check_id=feedback.duplicate_check_id,
        candidate_id=feedback.candidate_id,
        decision=feedback.user_decision,
        system_classification=feedback.system_classification,
        comment=feedback.comment,
        decided_by=feedback.decided_by,
        created_at=feedback.created_at,
        updated_at=feedback.updated_at,
    )


def build_duplicate_repository(db: Session, settings: Settings) -> DuplicateDetectionRepository:
    service = DuplicateDetectionService(
        DuplicateDetectionConfig(
            metadata_weight=settings.duplicate_metadata_weight,
            embedding_weight=settings.duplicate_embedding_weight,
            engineering_duplicate_min=settings.duplicate_engineering_min,
            likely_duplicate_min=settings.duplicate_likely_min,
            similar_min=settings.duplicate_similar_min,
        )
    )
    gateway = AiGateway(settings)
    reasoner = GatewayDuplicateReasoner(gateway) if gateway.chat_enabled else None
    return DuplicateDetectionRepository(
        db,
        service,
        reasoner,
        ai_review_min=settings.duplicate_ai_review_min,
        ai_review_max=settings.duplicate_ai_review_max,
        max_ai_candidates=settings.duplicate_max_ai_candidates,
    )


def _authorize_check_owner(db: Session, check: DuplicateCheck, principal: Principal) -> uuid.UUID:
    user = user_for_principal(db, principal)
    if check.source_revision_id is not None:
        revision, _ = _source_for_actor(db, check.source_revision_id, principal)
        item = db.get(Item, revision.part_id)
        owner_id = item.created_by if item else None
    else:
        staging = db.get(StagingSession, check.source_staging_id)
        if staging is None:
            raise HTTPException(status_code=404, detail="Duplicate check not found")
        owner_id = staging.created_by
    if principal.role != "administrator" and owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the source owner can decide")
    return user.id


@router.post(
    "/revisions/{revision_id}/duplicate-check",
    response_model=DuplicateCheckResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_duplicate_check(
    revision_id: uuid.UUID, db: Db, principal: Actor, settings: Config
) -> DuplicateCheckResponse:
    revision, actor_id = _source_for_actor(db, revision_id, principal)
    try:
        check = build_duplicate_repository(db, settings).run(
            revision, actor_id=actor_id, administrator=principal.role == "administrator"
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return _response(db, check)


@router.get(
    "/revisions/{revision_id}/duplicate-check",
    response_model=DuplicateCheckResponse,
)
def get_latest_duplicate_check(
    revision_id: uuid.UUID, db: Db, principal: Actor
) -> DuplicateCheckResponse:
    _source_for_actor(db, revision_id, principal)
    check = db.scalar(
        select(DuplicateCheck)
        .where(DuplicateCheck.source_revision_id == revision_id)
        .order_by(DuplicateCheck.created_at.desc(), DuplicateCheck.id.desc())
    )
    if check is None:
        raise HTTPException(status_code=404, detail="Duplicate check not found")
    return _response(db, check)


@router.post(
    "/duplicate-check/{check_id}/decision",
    response_model=DuplicateDecisionResponse,
)
def decide_duplicate_check(
    check_id: uuid.UUID,
    payload: DuplicateDecisionRequest,
    request: Request,
    db: Db,
    principal: Actor,
) -> DuplicateDecisionResponse:
    check = db.get(DuplicateCheck, check_id)
    if check is None:
        raise HTTPException(status_code=404, detail="Duplicate check not found")
    user_id = _authorize_check_owner(db, check, principal)
    candidate = None
    if payload.candidate_id is not None:
        candidate = db.get(DuplicateCandidate, payload.candidate_id)
        if candidate is None or candidate.duplicate_check_id != check.id:
            raise HTTPException(status_code=400, detail="Candidate does not belong to check")
    feedback = db.scalar(
        select(DuplicateFeedback).where(DuplicateFeedback.duplicate_check_id == check.id)
    )
    if feedback is None:
        feedback = DuplicateFeedback(duplicate_check_id=check.id, decided_by=user_id)
        db.add(feedback)
    feedback.candidate_id = candidate.id if candidate else None
    feedback.user_decision = payload.decision
    feedback.system_classification = candidate.classification if candidate else None
    feedback.comment = payload.comment
    feedback.decided_by = user_id
    if check.source_staging_id is not None:
        staging = db.get(StagingSession, check.source_staging_id)
        if staging is not None and staging.status != "PROMOTED":
            staging.status = "APPROVED"
    record_audit(
        db,
        actor_id=user_id,
        action="duplicate_check.decided",
        entity_type="duplicate_check",
        entity_id=check.id,
        request_id=request.state.request_id,
        details={
            "decision": payload.decision,
            "candidate_id": str(candidate.id) if candidate else None,
            "candidate_revision_id": str(candidate.candidate_revision_id) if candidate else None,
        },
    )
    db.commit()
    db.refresh(feedback)
    return _decision_response(feedback)


@router.get(
    "/duplicate-check/{check_id}/decision",
    response_model=DuplicateDecisionResponse,
)
def get_duplicate_decision(
    check_id: uuid.UUID, db: Db, principal: Actor
) -> DuplicateDecisionResponse:
    check = db.get(DuplicateCheck, check_id)
    if check is None:
        raise HTTPException(status_code=404, detail="Duplicate check not found")
    _authorize_check_owner(db, check, principal)
    feedback = db.scalar(
        select(DuplicateFeedback).where(DuplicateFeedback.duplicate_check_id == check.id)
    )
    if feedback is None:
        raise HTTPException(status_code=404, detail="Duplicate decision not found")
    return _decision_response(feedback)


@router.get(
    "/duplicate-detection/metrics",
    response_model=DuplicateMetricsResponse,
)
def duplicate_detection_metrics(db: Db, principal: Actor) -> DuplicateMetricsResponse:
    require_admin(principal)
    checks = list(db.scalars(select(DuplicateCheck)))
    candidates = list(db.scalars(select(DuplicateCandidate)))
    feedback = list(db.scalars(select(DuplicateFeedback)))
    check_count = len(checks)
    candidate_count = len(candidates)
    ai_candidates = [candidate for candidate in candidates if candidate.ai_result_json]
    agreements = [
        candidate.ai_result_json.get("classification") == candidate.classification
        for candidate in ai_candidates
    ]
    candidate_by_id = {candidate.id: candidate for candidate in candidates}
    duplicate_classes = {
        "exact_file_duplicate",
        "engineering_duplicate",
        "likely_duplicate",
    }
    overrides = []
    for decision in feedback:
        candidate = candidate_by_id.get(decision.candidate_id)
        if decision.user_decision == "NOT_DUPLICATE":
            overrides.append(bool(candidate and candidate.classification in duplicate_classes))
        elif decision.user_decision == "CREATE_NEW" and candidate:
            overrides.append(candidate.classification in duplicate_classes)
        elif decision.user_decision == "USE_EXISTING" and candidate:
            overrides.append(candidate.classification not in duplicate_classes)
        else:
            overrides.append(False)

    def average(values: list[float | int | bool]) -> float:
        return sum(values) / len(values) if values else 0.0

    return DuplicateMetricsResponse(
        checks=check_count,
        exact_duplicate_rate=(
            sum(check.exact_match_found for check in checks) / check_count if check_count else 0
        ),
        average_candidates_before_filter=average(
            [check.candidate_count_before_filter for check in checks]
        ),
        average_candidates_after_filter=average(
            [check.candidate_count_after_filter for check in checks]
        ),
        average_duration_ms=average([check.total_duration_ms for check in checks]),
        candidate_results=candidate_count,
        ai_invocation_rate=len(ai_candidates) / candidate_count if candidate_count else 0,
        ai_classification_agreement_rate=average(agreements) if agreements else None,
        feedback_decisions=len(feedback),
        user_override_rate=average(overrides) if overrides else None,
    )
