import hashlib
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from modelryx.api.items import readable_item_revision
from modelryx.core.audit import user_for_principal
from modelryx.core.auth import Principal
from modelryx.core.config import Settings
from modelryx.models import (
    Artifact,
    Attachment,
    Item,
    KnowledgeChunk,
    KnowledgeRelation,
    KnowledgeSource,
    ProcessingJob,
    Revision,
)
from modelryx.services.ai import AiGateway, build_revision_documents

from .models import (
    EvidenceBundle,
    EvidenceDocument,
    EvidenceLocation,
    KnowledgePublishRequest,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
)

KNOWLEDGE_INDEX_VERSION = "phase4-v2"


def authorize_source(db: Session, principal: Principal, source: KnowledgeSource) -> bool:
    if source.security_scope in {"PUBLIC", "ENGINEERING"} and source.item_id is None:
        return True
    if source.revision_id and source.item_id:
        try:
            readable_item_revision(db, source.item_id, source.revision_id, principal)
            return True
        except HTTPException as exc:
            if exc.status_code not in {403, 404}:
                raise
            return False
    if source.item_id:
        item = db.get(Item, source.item_id)
        if item is None or item.status != "ACTIVE":
            return False
        if principal.role == "administrator":
            return True
        return item.created_by == user_for_principal(db, principal).id
    return principal.role == "administrator"


def _embed(contents: list[str], settings: Settings) -> list[list[float] | None]:
    gateway = AiGateway(settings)
    if not gateway.embeddings_enabled:
        return [None] * len(contents)
    try:
        return gateway.embed(contents)
    except HTTPException:
        return [None] * len(contents)


def index_revision_knowledge(
    db: Session, revision: Revision, settings: Settings
) -> tuple[KnowledgeSource, bool]:
    item = db.get(Item, revision.part_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    documents = build_revision_documents(item, revision)
    linked_documents = db.execute(
        select(Attachment, Artifact)
        .join(Artifact, Artifact.attachment_id == Attachment.id)
        .where(Attachment.item_id == item.id, Artifact.state == "READY")
    ).all()
    jobs = list(db.scalars(select(ProcessingJob).where(ProcessingJob.revision_id == revision.id)))
    fingerprints = [document["content"] for document in documents]
    fingerprints.extend(
        f"{attachment.id}:{version.id}:{attachment.description}:{version.comment}"
        for attachment, version in linked_documents
    )
    fingerprints.extend(
        f"{job.id}:{job.state}:{job.stage}:{job.error_detail}:{job.diagnostics_json}"
        for job in jobs
    )
    canonical = "\n\n".join(fingerprints)
    content_hash = hashlib.sha256(canonical.encode()).hexdigest()
    source = db.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.source_object_type == "revision",
            KnowledgeSource.source_object_id == revision.id,
            KnowledgeSource.external_key == "canonical",
        )
    )
    if (
        source is not None
        and source.content_hash == content_hash
        and source.index_version == KNOWLEDGE_INDEX_VERSION
        and not source.is_stale
    ):
        embeddings_current = True
        if AiGateway(settings).embeddings_enabled:
            # Only canonical documents are embedded here; attachment and job
            # chunks intentionally remain lexical and must not force rebuilds.
            chunks = list(db.scalars(select(KnowledgeChunk).where(
                KnowledgeChunk.source_id == source.id,
                KnowledgeChunk.section.in_([document["chunk_key"] for document in documents]),
            )))
            embeddings_current = len(chunks) == len(documents) and all(
                chunk.embedding is not None
                and chunk.embedding_model == settings.ai_embedding_model
                and len(chunk.embedding) == settings.ai_embedding_dimensions
                for chunk in chunks
            )
        if embeddings_current:
            return source, False
    if source is None:
        source = KnowledgeSource(
            source_type="ASSEMBLY" if item.part_type == "ASSEMBLY" else "ITEM_REVISION",
            source_object_type="revision",
            source_object_id=revision.id,
            external_key="canonical",
            title=f"{item.part_number} revision {revision.revision_code}",
            item_id=item.id,
            revision_id=revision.id,
            content_hash=content_hash,
        )
        db.add(source)
        db.flush()
    else:
        db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id))
        source.source_type = "ASSEMBLY" if item.part_type == "ASSEMBLY" else "ITEM_REVISION"
        source.title = f"{item.part_number} revision {revision.revision_code}"
        source.content_hash = content_hash
    source.status = "ACTIVE"
    source.lifecycle_state = revision.lifecycle_state
    source.authority_rank = 100
    source.security_scope = "PDM"
    source.index_version = KNOWLEDGE_INDEX_VERSION
    source.is_stale = False
    source.source_updated_at = revision.committed_at or revision.created_at
    source.metadata_json = {
        "item_number": item.part_number,
        "revision_code": revision.revision_code,
        "cad_system": revision.cad_system,
        "native_format": revision.native_format,
    }
    vectors = _embed([document["content"] for document in documents], settings)
    for ordinal, (document, vector) in enumerate(zip(documents, vectors, strict=True)):
        content = document["content"]
        db.add(
            KnowledgeChunk(
                source_id=source.id,
                section=document["chunk_key"],
                ordinal=ordinal,
                content=content,
                content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                embedding=vector,
                embedding_model=settings.ai_embedding_model or None,
                location_json={"section": document["chunk_key"]},
                metadata_json=document["metadata"],
            )
        )
    ordinal = len(documents)
    for attachment, version in linked_documents:
        content = "\n".join(
            value
            for value in [
                f"Attachment: {attachment.name}",
                f"Type: {attachment.attachment_type}",
                attachment.description,
                version.comment,
            ]
            if value
        )
        db.add(
            KnowledgeChunk(
                source_id=source.id,
                section=f"attachment:{attachment.id}",
                ordinal=ordinal,
                content=content,
                content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                location_json={"section": attachment.name},
                metadata_json={
                    "attachment_id": str(attachment.id),
                    "attachment_version_id": str(version.id),
                    "attachment_type": attachment.attachment_type,
                },
            )
        )
        ordinal += 1
    for job in jobs:
        if job.state != "FAILED" and not job.diagnostics_json:
            continue
        bounded_error = (job.error_detail or "")[:4000]
        content = (
            f"Processing job {job.type}; state {job.state}; stage {job.stage}; "
            f"attempts {job.attempt_count}; error {bounded_error}; "
            f"diagnostics {job.diagnostics_json or {}}"
        )
        db.add(
            KnowledgeChunk(
                source_id=source.id,
                section=f"processing-job:{job.id}",
                ordinal=ordinal,
                content=content,
                content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                location_json={"section": "processing history"},
                metadata_json={
                    "processing_job_id": str(job.id),
                    "operation": job.type,
                    "stage": job.stage,
                    "state": job.state,
                    "recorded_resolution": (job.diagnostics_json or {}).get("resolution"),
                },
            )
        )
        ordinal += 1
    db.flush()
    return source, True


def _query_terms(query: str) -> list[str]:
    ignored = {"a", "an", "and", "did", "do", "does", "is", "of", "the", "this", "was"}
    terms = []
    for value in re.findall(r"[a-z0-9_]+", query.casefold()):
        if value not in ignored and value not in terms:
            terms.append(value)
    return terms[:20]


def search_knowledge(
    db: Session,
    principal: Principal,
    settings: Settings,
    request: KnowledgeSearchRequest,
) -> KnowledgeSearchResponse:
    filters = [KnowledgeSource.status == "ACTIVE"]
    if not request.filters.include_stale:
        filters.append(KnowledgeSource.is_stale.is_(False))
    if request.filters.source_types:
        filters.append(KnowledgeSource.source_type.in_(request.filters.source_types))
    if request.filters.item_ids:
        filters.append(KnowledgeSource.item_id.in_(request.filters.item_ids))
    if request.filters.revision_ids:
        filters.append(KnowledgeSource.revision_id.in_(request.filters.revision_ids))
    if request.filters.lifecycle_states:
        filters.append(KnowledgeSource.lifecycle_state.in_(request.filters.lifecycle_states))
    base = (
        select(KnowledgeChunk, KnowledgeSource)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
        .where(*filters)
    )
    ranked: dict[uuid.UUID, tuple[KnowledgeChunk, KnowledgeSource, float]] = {}
    terms = _query_terms(request.query)
    lexical_query = func.to_tsquery("english", " | ".join(terms)) if terms else None
    if lexical_query is not None:
        lexical_rank = func.ts_rank_cd(
            func.to_tsvector("english", KnowledgeChunk.content), lexical_query
        )
        rows = db.execute(
            base.where(lexical_rank > 0)
            .order_by(lexical_rank.desc(), KnowledgeSource.authority_rank.desc())
            .limit(request.limit * 4)
        ).all()
        for rank, (chunk, source) in enumerate(rows, 1):
            ranked[chunk.id] = (chunk, source, 1 / (60 + rank))

    warnings = []
    parser = "lexical"
    gateway = AiGateway(settings)
    if gateway.embeddings_enabled:
        try:
            vector = gateway.embed([request.query])[0]
            rows = db.execute(
                base.where(
                    KnowledgeChunk.embedding.is_not(None),
                    KnowledgeChunk.embedding_model == settings.ai_embedding_model,
                )
                .order_by(KnowledgeChunk.embedding.cosine_distance(vector))
                .limit(request.limit * 4)
            ).all()
            for rank, (chunk, source) in enumerate(rows, 1):
                previous = ranked.get(chunk.id)
                score = (previous[2] if previous else 0) + 1 / (60 + rank)
                ranked[chunk.id] = (chunk, source, score)
            parser = "hybrid"
        except HTTPException:
            warnings.append("Embedding retrieval failed; lexical evidence was used")

    authorized = []
    for chunk, source, score in ranked.values():
        if authorize_source(db, principal, source):
            context_boost = 0.002 if source.item_id in request.filters.item_ids else 0
            authority_boost = source.authority_rank / 100_000
            authorized.append((chunk, source, score + context_boost + authority_boost))
    authorized.sort(key=lambda row: row[2], reverse=True)
    selected = authorized[: request.limit]
    results = [
        KnowledgeSearchResult(
            source_id=source.id,
            chunk_id=chunk.id,
            source_type=source.source_type,
            source_object_type=source.source_object_type,
            source_object_id=source.source_object_id,
            title=source.title,
            snippet=chunk.content[:1000],
            item_id=source.item_id,
            revision_id=source.revision_id,
            lifecycle_state=source.lifecycle_state,
            authority_rank=source.authority_rank,
            location=EvidenceLocation.model_validate(chunk.location_json),
            updated_at=source.updated_at,
            metadata={**source.metadata_json, **chunk.metadata_json},
        )
        for chunk, source, _ in selected
    ]
    return KnowledgeSearchResponse(
        results=results,
        total=len(results),
        parser=parser,
        warnings=warnings,
    )


def publish_knowledge(
    db: Session,
    principal: Principal,
    settings: Settings,
    payload: KnowledgePublishRequest,
) -> KnowledgeSource:
    if principal.role not in {"engineer", "reviewer", "administrator"}:
        raise HTTPException(status_code=403, detail="Engineer role required")
    item_id = payload.item_id
    if payload.revision_id:
        revision = db.get(Revision, payload.revision_id)
        if revision is None or (payload.item_id and revision.part_id != payload.item_id):
            raise HTTPException(status_code=404, detail="Revision not found")
        readable_item_revision(db, revision.part_id, revision.id, principal)
        item_id = revision.part_id
    canonical = "\n\n".join(chunk.content for chunk in payload.chunks)
    source = KnowledgeSource(
        source_type=payload.source_type,
        source_object_type=payload.source_type.casefold(),
        title=payload.title,
        lifecycle_state=payload.lifecycle_state,
        authority_rank=payload.authority_rank,
        security_scope="PDM" if item_id else "ENGINEERING",
        item_id=item_id,
        revision_id=payload.revision_id,
        content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        index_version=KNOWLEDGE_INDEX_VERSION,
        metadata_json=payload.metadata,
    )
    db.add(source)
    db.flush()
    vectors = _embed([chunk.content for chunk in payload.chunks], settings)
    for ordinal, (chunk, vector) in enumerate(zip(payload.chunks, vectors, strict=True)):
        db.add(
            KnowledgeChunk(
                source_id=source.id,
                section=chunk.section,
                ordinal=ordinal,
                content=chunk.content,
                content_sha256=hashlib.sha256(chunk.content.encode()).hexdigest(),
                embedding=vector,
                embedding_model=settings.ai_embedding_model or None,
                location_json=chunk.location.model_dump(exclude_none=True),
                metadata_json=chunk.metadata,
            )
        )
    db.flush()
    return source


def relate_sources(
    db: Session,
    principal: Principal,
    from_source: KnowledgeSource,
    to_source: KnowledgeSource,
    relation_type: str,
    metadata: dict,
) -> KnowledgeRelation:
    if principal.role not in {"engineer", "reviewer", "administrator"}:
        raise HTTPException(status_code=403, detail="Engineer role required")
    if not authorize_source(db, principal, from_source) or not authorize_source(
        db, principal, to_source
    ):
        raise HTTPException(status_code=404, detail="Knowledge source not found")
    relation = KnowledgeRelation(
        from_source_id=from_source.id,
        to_source_id=to_source.id,
        relation_type=relation_type,
        metadata_json=metadata,
    )
    db.add(relation)
    db.flush()
    return relation


def evidence_bundle(
    question: str, response: KnowledgeSearchResponse, context: dict | None = None
) -> EvidenceBundle:
    documents = [
        EvidenceDocument(
            source_id=row.source_id,
            chunk_id=row.chunk_id,
            source_type=row.source_type,
            title=row.title,
            snippet=row.snippet,
            location=row.location,
            authority_rank=row.authority_rank,
        )
        for row in response.results
    ]
    quality = "NONE"
    if documents:
        quality = "STRONG" if any(row.authority_rank >= 80 for row in documents) else "PARTIAL"
    warnings = list(response.warnings)
    if not documents:
        warnings.append("No authorized recorded evidence matched the question")
    if "why" in question.casefold() and not any(
        row.source_type in {"CHANGE_REQUEST", "CHANGE_NOTICE", "REVISION_NOTE"}
        for row in response.results
    ):
        warnings.append("No recorded change reason was found")
    return EvidenceBundle(
        question=question,
        context=context or {},
        documents=documents,
        evidence_quality=quality,
        warnings=warnings,
    )
