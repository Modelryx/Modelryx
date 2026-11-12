import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.api.items import readable_item_revision
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.knowledge.models import (
    EvidenceBundle,
    EvidenceFact,
    EvidenceLocation,
    KnowledgeChunkDetail,
    KnowledgeFilter,
    KnowledgePublishRequest,
    KnowledgeRelationInput,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSourceDetail,
)
from modelryx.knowledge.service import (
    authorize_source,
    evidence_bundle,
    index_revision_knowledge,
    publish_knowledge,
    relate_sources,
    search_knowledge,
)
from modelryx.models import KnowledgeChunk, KnowledgeSource, Revision

router = APIRouter(prefix="/api/v1/knowledge", tags=["engineering-knowledge"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


@router.post("/search", response_model=KnowledgeSearchResponse)
def search(
    payload: KnowledgeSearchRequest,
    db: Db,
    principal: Actor,
    settings: Config,
) -> KnowledgeSearchResponse:
    return search_knowledge(db, principal, settings, payload)


@router.post("/retrieve", response_model=EvidenceBundle)
def retrieve(
    payload: KnowledgeSearchRequest,
    db: Db,
    principal: Actor,
    settings: Config,
) -> EvidenceBundle:
    response = search_knowledge(db, principal, settings, payload)
    return evidence_bundle(
        payload.query,
        response,
        {
            "item_ids": [str(value) for value in payload.filters.item_ids],
            "revision_ids": [str(value) for value in payload.filters.revision_ids],
        },
    )


@router.post(
    "/revisions/{revision_id}/index",
    response_model=KnowledgeSourceDetail,
    status_code=status.HTTP_200_OK,
)
def index_revision(
    revision_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> KnowledgeSourceDetail:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    readable_item_revision(db, revision.part_id, revision.id, principal)
    if principal.role not in {"engineer", "reviewer", "administrator"}:
        raise HTTPException(status_code=403, detail="Engineer role required")
    source, changed = index_revision_knowledge(db, revision, settings)
    user = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=user.id,
        action="knowledge.revision_indexed",
        entity_type="revision",
        entity_id=revision.id,
        request_id=request.state.request_id,
        details={"source_id": str(source.id), "changed": changed},
    )
    db.commit()
    return source_detail(db, source)


def source_detail(db: Session, source: KnowledgeSource) -> KnowledgeSourceDetail:
    chunks = list(
        db.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.source_id == source.id)
            .order_by(KnowledgeChunk.ordinal)
        )
    )
    return KnowledgeSourceDetail(
        id=source.id,
        source_type=source.source_type,
        source_object_type=source.source_object_type,
        source_object_id=source.source_object_id,
        title=source.title,
        status=source.status,
        lifecycle_state=source.lifecycle_state,
        authority_rank=source.authority_rank,
        item_id=source.item_id,
        revision_id=source.revision_id,
        index_version=source.index_version,
        is_stale=source.is_stale,
        metadata=source.metadata_json,
        chunks=[
            KnowledgeChunkDetail(
                id=chunk.id,
                section=chunk.section,
                ordinal=chunk.ordinal,
                content=chunk.content,
                location=EvidenceLocation.model_validate(chunk.location_json),
                metadata=chunk.metadata_json,
            )
            for chunk in chunks
        ],
    )


@router.get("/sources/{source_id}", response_model=KnowledgeSourceDetail)
def read_source(source_id: uuid.UUID, db: Db, principal: Actor) -> KnowledgeSourceDetail:
    source = db.get(KnowledgeSource, source_id)
    if source is None or not authorize_source(db, principal, source):
        raise HTTPException(status_code=404, detail="Knowledge source not found")
    return source_detail(db, source)


@router.post("/sources", response_model=KnowledgeSourceDetail, status_code=status.HTTP_201_CREATED)
def create_source(
    payload: KnowledgePublishRequest,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> KnowledgeSourceDetail:
    source = publish_knowledge(db, principal, settings, payload)
    user = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=user.id,
        action="knowledge.source_published",
        entity_type="knowledge_source",
        entity_id=source.id,
        request_id=request.state.request_id,
        details={"source_type": source.source_type, "lifecycle": source.lifecycle_state},
    )
    db.commit()
    return source_detail(db, source)


@router.post("/sources/{source_id}/relations", status_code=status.HTTP_201_CREATED)
def create_relation(
    source_id: uuid.UUID,
    payload: KnowledgeRelationInput,
    request: Request,
    db: Db,
    principal: Actor,
) -> dict:
    source = db.get(KnowledgeSource, source_id)
    target = db.get(KnowledgeSource, payload.to_source_id)
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="Knowledge source not found")
    relation = relate_sources(
        db, principal, source, target, payload.relation_type, payload.metadata
    )
    user = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=user.id,
        action="knowledge.relation_created",
        entity_type="knowledge_relation",
        entity_id=relation.id,
        request_id=request.state.request_id,
        details={"relation_type": relation.relation_type},
    )
    db.commit()
    return {"id": str(relation.id), "relation_type": relation.relation_type}


@router.get("/revisions/{revision_id}/context", response_model=EvidenceBundle)
def revision_context(
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
    settings: Config,
    question: str = Query(default="What evidence relates to this revision?", max_length=4000),
) -> EvidenceBundle:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    readable_item_revision(db, revision.part_id, revision.id, principal)
    previous = db.scalar(
        select(Revision)
        .where(
            Revision.part_id == revision.part_id,
            Revision.created_at < revision.created_at,
        )
        .order_by(Revision.created_at.desc())
    )
    response = search_knowledge(
        db,
        principal,
        settings,
        KnowledgeSearchRequest(
            query=question,
            filters=KnowledgeFilter(item_ids=[revision.part_id]),
            limit=20,
        ),
    )
    bundle = evidence_bundle(
        question,
        response,
        {
            "item_id": str(revision.part_id),
            "revision_id": str(revision.id),
            "previous_revision_id": str(previous.id) if previous else None,
        },
    )
    bundle.facts.append(
        EvidenceFact(
            type="REVISION_STATE",
            source="authoritative revision record",
            source_id=revision.id,
            fact=f"Revision {revision.revision_code} is {revision.state}.",
        )
    )
    if previous:
        bundle.facts.append(
            EvidenceFact(
                type="PREVIOUS_REVISION",
                source="authoritative revision history",
                source_id=previous.id,
                fact=f"The previous recorded revision is {previous.revision_code}.",
            )
        )
    return bundle
