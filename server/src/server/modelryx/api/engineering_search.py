import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

from modelryx.core.audit import user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.engineering_search.catalog_loader import active_scenario_catalog
from modelryx.engineering_search.interpreter import AISearchInterpreter
from modelryx.engineering_search.models import (
    AISearchRequest,
    EngineeringSearchResponse,
    SearchFeedbackRequest,
    SearchFeedbackResponse,
    SearchRequest,
    SearchSessionResponse,
)
from modelryx.engineering_search.registry import SEARCH_CAPABILITIES
from modelryx.engineering_search.service import execute_search
from modelryx.models import AiSearchFeedback, AiSearchSession
from modelryx.services.ai import AiGateway

router = APIRouter(prefix="/api/v1", tags=["engineering-search"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


@router.get("/search/capabilities")
def search_capabilities(_: Actor) -> dict[str, Any]:
    catalog = active_scenario_catalog()
    return {
        **SEARCH_CAPABILITIES,
        "scenario_catalog": catalog.public(),
    }


@router.post("/search", response_model=EngineeringSearchResponse)
def engineering_search(
    payload: SearchRequest, db: Db, principal: Actor
) -> EngineeringSearchResponse:
    return execute_search(db, principal, payload)


@router.post("/ai/search", response_model=EngineeringSearchResponse)
def ai_search(
    payload: AISearchRequest, db: Db, principal: Actor, settings: Config
) -> EngineeringSearchResponse:
    retention_cutoff = datetime.now(UTC) - timedelta(
        days=settings.ai_search_history_retention_days
    )
    db.execute(
        delete(AiSearchSession).where(AiSearchSession.updated_at < retention_cutoff)
    )
    user = user_for_principal(db, principal)
    session = None
    previous_request = None
    if payload.session_id is not None:
        session = db.get(AiSearchSession, payload.session_id)
        if session is None or session.created_by != user.id:
            raise HTTPException(status_code=404, detail="Search session not found")
        previous_request = SearchRequest.model_validate(session.current_request_json)
    request, warnings, confidence, parser = AISearchInterpreter(AiGateway(settings)).interpret(
        payload, previous_request
    )
    response = execute_search(
        db,
        principal,
        request,
        parser=parser,
        warnings=warnings,
        confidence=confidence,
    )
    if session is None:
        session = AiSearchSession(
            created_by=user.id,
            current_request_json={},
            history_json=[],
        )
        db.add(session)
        db.flush()
    history = list(session.history_json or [])
    history.append(
        {
            "query": payload.query if settings.ai_search_store_raw_queries else None,
            "request": request.model_dump(mode="json"),
            "result_count": response.total,
            "parser": response.trace.parser,
            "duration_ms": response.trace.duration_ms,
            "warning_count": len(response.warnings),
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    session.current_request_json = request.model_dump(mode="json")
    session.history_json = history[-50:]
    db.commit()
    response.session_id = session.id
    return response


@router.get("/ai/search/sessions/{session_id}", response_model=SearchSessionResponse)
def read_search_session(session_id: uuid.UUID, db: Db, principal: Actor) -> SearchSessionResponse:
    user = user_for_principal(db, principal)
    session = db.get(AiSearchSession, session_id)
    if session is None or session.created_by != user.id:
        raise HTTPException(status_code=404, detail="Search session not found")
    return SearchSessionResponse(
        id=session.id,
        current_request=SearchRequest.model_validate(session.current_request_json),
        history=session.history_json,
    )


@router.post(
    "/ai/search/sessions/{session_id}/feedback",
    response_model=SearchFeedbackResponse,
    status_code=201,
)
def create_search_feedback(
    session_id: uuid.UUID,
    payload: SearchFeedbackRequest,
    db: Db,
    principal: Actor,
) -> SearchFeedbackResponse:
    user = user_for_principal(db, principal)
    session = db.get(AiSearchSession, session_id)
    if session is None or session.created_by != user.id:
        raise HTTPException(status_code=404, detail="Search session not found")
    feedback = AiSearchFeedback(
        session_id=session.id,
        created_by=user.id,
        category=payload.category,
        comment=payload.comment.strip() if payload.comment else None,
        interpretation_json=session.current_request_json,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return SearchFeedbackResponse(
        id=feedback.id,
        session_id=feedback.session_id,
        category=feedback.category,
        created_at=feedback.created_at,
    )
