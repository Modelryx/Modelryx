import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from modelryx.comparison.explanations import explain_comparison, explanation_response
from modelryx.comparison.models import (
    ComparePreviousRequest,
    ComparisonExplanationRequest,
    ComparisonExplanationResponse,
    RevisionComparisonRequest,
    RevisionComparisonResponse,
)
from modelryx.comparison.service import (
    authorize_comparison,
    comparison_response,
    create_comparison,
    previous_revision,
)
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import RevisionComparison

router = APIRouter(prefix="/api/v1", tags=["revision-comparisons"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


def _audit_comparison(
    db: Session,
    request: Request,
    principal: Principal,
    comparison: RevisionComparison,
    created: bool,
) -> None:
    user = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=user.id,
        action="revision_comparison.created" if created else "revision_comparison.reused",
        entity_type="revision_comparison",
        entity_id=comparison.id,
        request_id=request.state.request_id,
        details={
            "item_id": str(comparison.item_id),
            "from_revision_id": str(comparison.from_revision_id),
            "to_revision_id": str(comparison.to_revision_id),
            "status": comparison.status,
        },
    )


@router.post(
    "/items/{item_id}/revision-comparisons",
    response_model=RevisionComparisonResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_revision_comparison(
    item_id: uuid.UUID,
    payload: RevisionComparisonRequest,
    request: Request,
    db: Db,
    principal: Actor,
) -> RevisionComparisonResponse:
    comparison, created = create_comparison(
        db,
        principal,
        item_id,
        payload.from_revision_id,
        payload.to_revision_id,
        payload.domains,
    )
    _audit_comparison(db, request, principal, comparison, created)
    db.commit()
    return comparison_response(db, comparison)


@router.post(
    "/items/{item_id}/revisions/{revision_id}/compare-previous",
    response_model=RevisionComparisonResponse,
    status_code=status.HTTP_201_CREATED,
)
def compare_previous_revision(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    payload: ComparePreviousRequest,
    request: Request,
    db: Db,
    principal: Actor,
) -> RevisionComparisonResponse:
    previous = previous_revision(db, principal, item_id, revision_id)
    comparison, created = create_comparison(
        db, principal, item_id, previous.id, revision_id, payload.domains
    )
    _audit_comparison(db, request, principal, comparison, created)
    db.commit()
    return comparison_response(db, comparison)


@router.get("/revision-comparisons/{comparison_id}", response_model=RevisionComparisonResponse)
def read_revision_comparison(
    comparison_id: uuid.UUID, db: Db, principal: Actor
) -> RevisionComparisonResponse:
    comparison = db.get(RevisionComparison, comparison_id)
    if comparison is None:
        raise HTTPException(status_code=404, detail="Revision comparison not found")
    authorize_comparison(db, principal, comparison)
    return comparison_response(db, comparison)


@router.post(
    "/revision-comparisons/{comparison_id}/explanations",
    response_model=ComparisonExplanationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_revision_comparison_explanation(
    comparison_id: uuid.UUID,
    payload: ComparisonExplanationRequest,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> ComparisonExplanationResponse:
    comparison = db.get(RevisionComparison, comparison_id)
    if comparison is None:
        raise HTTPException(status_code=404, detail="Revision comparison not found")
    explanation = explain_comparison(db, principal, settings, comparison, payload.level)
    user = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=user.id,
        action="revision_comparison.explained",
        entity_type="revision_comparison_explanation",
        entity_id=explanation.id,
        request_id=request.state.request_id,
        details={
            "comparison_id": str(comparison.id),
            "level": explanation.level,
            "mode": explanation.mode,
            "result_hash": explanation.comparison_result_hash,
        },
    )
    db.commit()
    return explanation_response(explanation)
