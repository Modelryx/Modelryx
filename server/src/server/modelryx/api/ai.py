import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from modelryx.api.admin import require_admin
from modelryx.api.items import readable_item_revision
from modelryx.api.parts import require_engineer
from modelryx.assistant.feature_search import (
    AssemblyFeatureSearch,
    requested_features,
    validate_feature_question,
)
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import AiDocument, Revision
from modelryx.services.ai import AiGateway, index_revision, retrieve

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


class AiIndexResponse(BaseModel):
    revision_id: uuid.UUID
    chunks: int
    embeddings: bool


class AiBackfillResponse(BaseModel):
    indexed_revisions: int
    indexed_chunks: int
    embeddings: bool


class AiQueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    item_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    limit: int | None = Field(default=None, ge=1, le=20)


class AiSource(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    revision_id: uuid.UUID
    label: str
    title: str
    item_number: str
    revision_code: str
    chunk_key: str
    excerpt: str
    score: float
    metadata: dict


class AiQueryResponse(BaseModel):
    answer: str
    sources: list[AiSource]


class AiStatusResponse(BaseModel):
    gateway_configured: bool
    embeddings_configured: bool
    indexed_chunks: int
    indexed_revisions: int


@router.get("/status", response_model=AiStatusResponse)
def ai_status(db: Db, _: Actor, settings: Config) -> AiStatusResponse:
    gateway = AiGateway(settings)
    return AiStatusResponse(
        gateway_configured=gateway.chat_enabled,
        embeddings_configured=gateway.embeddings_enabled,
        indexed_chunks=db.scalar(select(func.count()).select_from(AiDocument)) or 0,
        indexed_revisions=(
            db.scalar(select(func.count(func.distinct(AiDocument.revision_id)))) or 0
        ),
    )


@router.post(
    "/items/{item_id}/revisions/{revision_id}/index",
    response_model=AiIndexResponse,
)
def index_item_revision(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AiIndexResponse:
    require_engineer(principal)
    revision = readable_item_revision(db, item_id, revision_id, principal)
    chunks = index_revision(db, revision, settings)
    return AiIndexResponse(
        revision_id=revision.id,
        chunks=chunks,
        embeddings=AiGateway(settings).embeddings_enabled,
    )


@router.post("/index-missing", response_model=AiBackfillResponse)
def index_missing_revisions(
    db: Db,
    principal: Actor,
    settings: Config,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
) -> AiBackfillResponse:
    """Backfill revisions that became ready before AI indexing was enabled."""
    require_admin(principal)
    revisions = db.scalars(
        select(Revision)
        .where(
            Revision.state.in_(["READY", "READY_WITH_WARNINGS"]),
            ~exists(select(AiDocument.id).where(AiDocument.revision_id == Revision.id)),
        )
        .order_by(Revision.committed_at.desc().nullslast(), Revision.created_at.desc())
        .limit(limit)
    ).all()
    chunks = 0
    for revision in revisions:
        chunks += index_revision(db, revision, settings)
    return AiBackfillResponse(
        indexed_revisions=len(revisions),
        indexed_chunks=chunks,
        embeddings=AiGateway(settings).embeddings_enabled,
    )


@router.post("/query", response_model=AiQueryResponse)
def query_modelryx(
    payload: AiQueryRequest,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AiQueryResponse:
    features = requested_features(payload.question)
    if features and payload.item_id is None and payload.revision_id is None:
        validate_feature_question(payload.question, discovery=True)
        reports, paths, warnings = AssemblyFeatureSearch(db, principal).discover(
            features, limit=payload.limit or 20,
            match_any=bool(re.search(r"\bor\b", payload.question, re.I)),
        )
        answer = "Matching parts and parent assemblies from authorized feature evidence."
        for index, report in enumerate(reports, 1):
            answer += f"\n[S{index}] " + report.text()
        if not reports:
            answer += " No matches were confirmed; missing coverage is not evidence of absence."
        if warnings:
            answer += "\n" + "\n".join(sorted(warnings))
        return AiQueryResponse(answer=answer, sources=[
            AiSource(
                id=report.revision.id, item_id=report.item.id, revision_id=report.revision.id,
                label=f"S{index}", title="Authoritative feature evidence",
                item_number=report.item.part_number, revision_code=report.revision.revision_code,
                chunk_key="assembly-feature-evidence", excerpt=report.text()[:500], score=1.0,
                metadata={**report.summary(), "discovery_paths": paths.get(report.revision.id, [])},
            ) for index, report in enumerate(reports, 1)
        ])
    if payload.revision_id and db.get(Revision, payload.revision_id) is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    sources = retrieve(
        db,
        payload.question,
        principal,
        settings,
        item_id=payload.item_id,
        revision_id=payload.revision_id,
        limit=payload.limit,
    )
    if not sources:
        raise HTTPException(
            status_code=404,
            detail="No indexed Modelryx sources matched the question",
        )
    gateway = AiGateway(settings)
    if gateway.chat_enabled:
        answer = gateway.answer(payload.question, sources)
    else:
        matches = []
        seen = set()
        for index, source in enumerate(sources, 1):
            key = (source.item_number, source.revision_code)
            if key not in seen:
                seen.add(key)
                matches.append(
                    f"- {source.item_number}, revision {source.revision_code} [S{index}]"
                )
        answer = "Matching indexed items:\n" + "\n".join(matches)
    return AiQueryResponse(
        answer=answer,
        sources=[
            AiSource(
                id=source.document.id,
                item_id=source.document.item_id,
                revision_id=source.document.revision_id,
                label=f"S{index}",
                title=source.document.title,
                item_number=source.item_number,
                revision_code=source.revision_code,
                chunk_key=source.document.chunk_key,
                excerpt=source.document.content[:500],
                score=source.score,
                metadata=source.document.metadata_json,
            )
            for index, source in enumerate(sources, 1)
        ],
    )


@router.get("/search", response_model=list[AiSource])
def search_ai_documents(
    question: Annotated[str, Query(min_length=2, max_length=4000)],
    db: Db,
    principal: Actor,
    settings: Config,
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> list[AiSource]:
    return [
        AiSource(
            id=source.document.id,
            item_id=source.document.item_id,
            revision_id=source.document.revision_id,
            label=f"S{index}",
            title=source.document.title,
            item_number=source.item_number,
            revision_code=source.revision_code,
            chunk_key=source.document.chunk_key,
            excerpt=source.document.content[:500],
            score=source.score,
            metadata=source.document.metadata_json,
        )
        for index, source in enumerate(retrieve(db, question, principal, settings, limit=limit), 1)
    ]
