import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.api.assemblies import read_bom
from modelryx.bom_analysis.models import (
    BOMAIRequest,
    BOMAIResponse,
    BOMDiff,
    BOMDuplicateResponse,
    BOMKnowledgeRequest,
    BOMQuery,
    BOMQueryResult,
    BOMReadinessResponse,
    BOMSnapshot,
    BOMSummary,
    CommonalityRequest,
    CommonalityResponse,
    ReadinessPolicy,
    StandardizationRequest,
    StandardizationResponse,
)
from modelryx.bom_analysis.service import BOMAnalysisService
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.knowledge.models import EvidenceBundle, KnowledgeFilter, KnowledgeSearchRequest
from modelryx.knowledge.service import evidence_bundle, search_knowledge
from modelryx.models import (
    Artifact,
    Attachment,
    DuplicateCandidate,
    DuplicateCheck,
    MetadataExtraction,
    Revision,
)
from modelryx.services.artifact_content import revisions_with_current_artifact_types

router = APIRouter(prefix="/api/v1", tags=["bom-analysis"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


def _snapshot(
    revision_id: uuid.UUID, db: Session, principal: Principal, depth: int = 20
) -> BOMSnapshot:
    bom = read_bom(revision_id, db, principal, depth)
    revision_ids = {row.child_revision.id for row in _flatten(bom.occurrences)}
    item_ids = set(db.scalars(select(Revision.part_id).where(Revision.id.in_(revision_ids))))
    artifact_drawings = revisions_with_current_artifact_types(
        db,
        revision_ids,
        ("DRAWING", "PDF"),
    )
    item_drawings = set(
        db.scalars(
            select(Revision.id)
            .join(Attachment, Attachment.item_id == Revision.part_id)
            .join(Artifact, Artifact.attachment_id == Attachment.id)
            .where(
                Revision.id.in_(revision_ids),
                Attachment.item_id.in_(item_ids),
                Attachment.attachment_type == "DRAWING",
                Artifact.state == "READY",
            )
            .distinct()
        )
    )
    metadata_by_revision = {}
    metadata_rows = db.scalars(
        select(MetadataExtraction)
        .where(
            MetadataExtraction.revision_id.in_(revision_ids),
            MetadataExtraction.status.in_(["READY", "PARTIAL"]),
        )
        .order_by(MetadataExtraction.created_at.desc(), MetadataExtraction.id.desc())
    )
    for extraction in metadata_rows:
        metadata_by_revision.setdefault(
            extraction.revision_id,
            {
                "material": extraction.material,
                "mass_kg": extraction.mass_kg,
                "status": extraction.status,
            },
        )
    return BOMAnalysisService.snapshot(
        bom,
        artifact_drawings | item_drawings,
        metadata_by_revision,
    )


def _flatten(rows: list) -> list:
    result = []
    for row in rows:
        result.append(row)
        result.extend(_flatten(row.children))
    return result


def _duplicate_evidence(snapshot: BOMSnapshot, db: Session) -> list[dict]:
    revision_ids = {node.revision_id for node in snapshot.nodes if not node.suppressed}
    rows = db.execute(
        select(DuplicateCheck, DuplicateCandidate)
        .join(DuplicateCandidate, DuplicateCandidate.duplicate_check_id == DuplicateCheck.id)
        .where(
            DuplicateCheck.source_revision_id.in_(revision_ids),
            DuplicateCheck.status == "COMPLETED",
            DuplicateCandidate.candidate_revision_id.in_(revision_ids),
        )
        .order_by(DuplicateCheck.created_at.desc(), DuplicateCandidate.created_at.desc())
    ).all()
    return [
        {
            "source_revision_id": check.source_revision_id,
            "candidate_revision_id": candidate.candidate_revision_id,
            "classification": candidate.classification,
            "scores": candidate.score_breakdown_json,
            "reasons": candidate.reasons_json,
            "differences": candidate.differences_json,
        }
        for check, candidate in rows
    ]


@router.get("/item-revisions/{revision_id}/bom/snapshot", response_model=BOMSnapshot)
def get_bom_snapshot(
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
    depth: Annotated[int, Query(ge=1, le=20)] = 20,
) -> BOMSnapshot:
    return _snapshot(revision_id, db, principal, depth)


@router.get("/item-revisions/{revision_id}/bom/summary", response_model=BOMSummary)
def get_bom_summary(revision_id: uuid.UUID, db: Db, principal: Actor) -> BOMSummary:
    return BOMAnalysisService().summary(_snapshot(revision_id, db, principal))


@router.post("/item-revisions/{revision_id}/bom/query", response_model=BOMQueryResult)
def query_bom(
    revision_id: uuid.UUID,
    payload: BOMQuery,
    db: Db,
    principal: Actor,
) -> BOMQueryResult:
    snapshot = _snapshot(revision_id, db, principal, payload.traversal.max_depth)
    return BOMAnalysisService().execute(snapshot, payload)


@router.get("/item-revisions/{revision_id}/bom/single-use", response_model=BOMQueryResult)
def single_use(
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
    scope: str = Query(default="assembly", pattern="^assembly$"),
) -> BOMQueryResult:
    result = BOMAnalysisService().execute(
        _snapshot(revision_id, db, principal),
        BOMQuery(operation="single_use"),
    )
    result.interpreted_as = f"one occurrence in this {scope}"
    return result


@router.post("/item-revisions/{revision_id}/bom/ai-query", response_model=BOMAIResponse)
def ai_query(
    revision_id: uuid.UUID,
    payload: BOMAIRequest,
    db: Db,
    principal: Actor,
) -> BOMAIResponse:
    service = BOMAnalysisService()
    plan, interpretation = service.plan(payload.message)
    result = service.execute(_snapshot(revision_id, db, principal), plan)
    result.interpreted_as = interpretation
    if plan.operation == "summary":
        summary = result.summary
        answer = (
            f"This BOM contains {summary.unique_component_revisions} unique component "
            f"revisions across {summary.total_occurrences} occurrences "
            f"({summary.total_quantity} total quantity)."
        )
    else:
        answer = f"Modelryx found {result.total} {interpretation}."
        if result.truncated:
            answer += f" The response shows the first {result.returned}."
    return BOMAIResponse(
        plan=plan,
        result=result,
        mode="DETERMINISTIC",
        answer=answer,
        evidence_occurrence_ids=[node.occurrence_id for node in result.nodes],
        warnings=["Deterministic interpretation used; no engineering substitution is implied."],
    )


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_revision_id: uuid.UUID
    to_revision_id: uuid.UUID


@router.post("/bom/compare", response_model=BOMDiff)
def compare_bom(payload: CompareRequest, db: Db, principal: Actor) -> BOMDiff:
    if payload.from_revision_id == payload.to_revision_id:
        raise HTTPException(status_code=422, detail="BOM revisions must be different")
    return BOMAnalysisService.compare(
        _snapshot(payload.from_revision_id, db, principal),
        _snapshot(payload.to_revision_id, db, principal),
    )


@router.post(
    "/item-revisions/{revision_id}/bom/standardization",
    response_model=StandardizationResponse,
)
def standardization_candidates(
    revision_id: uuid.UUID,
    payload: StandardizationRequest,
    db: Db,
    principal: Actor,
) -> StandardizationResponse:
    snapshot = _snapshot(revision_id, db, principal)
    return BOMAnalysisService.standardization_candidates(
        snapshot,
        _duplicate_evidence(snapshot, db),
        minimum_score=payload.minimum_score,
        limit=payload.limit,
    )


@router.get(
    "/item-revisions/{revision_id}/bom/duplicate-pairs",
    response_model=BOMDuplicateResponse,
)
def duplicate_pairs(revision_id: uuid.UUID, db: Db, principal: Actor) -> BOMDuplicateResponse:
    snapshot = _snapshot(revision_id, db, principal)
    return BOMAnalysisService.duplicate_pairs(snapshot, _duplicate_evidence(snapshot, db))


@router.post(
    "/item-revisions/{revision_id}/bom/readiness",
    response_model=BOMReadinessResponse,
)
def bom_readiness(
    revision_id: uuid.UUID,
    payload: ReadinessPolicy,
    db: Db,
    principal: Actor,
) -> BOMReadinessResponse:
    return BOMAnalysisService.readiness(_snapshot(revision_id, db, principal), payload)


@router.post("/bom/commonality", response_model=CommonalityResponse)
def bom_commonality(payload: CommonalityRequest, db: Db, principal: Actor) -> CommonalityResponse:
    snapshots = [_snapshot(revision_id, db, principal) for revision_id in payload.revision_ids]
    return BOMAnalysisService.commonality(snapshots)


@router.post(
    "/item-revisions/{revision_id}/bom/knowledge",
    response_model=EvidenceBundle,
)
def bom_component_knowledge(
    revision_id: uuid.UUID,
    payload: BOMKnowledgeRequest,
    db: Db,
    principal: Actor,
    settings: Config,
) -> EvidenceBundle:
    snapshot = _snapshot(revision_id, db, principal)
    component = BOMAnalysisService.visible_component(snapshot, payload.component_revision_id)
    if component is None:
        raise HTTPException(status_code=404, detail="BOM component not found")
    response = search_knowledge(
        db,
        principal,
        settings,
        KnowledgeSearchRequest(
            query=payload.question,
            filters=KnowledgeFilter(item_ids=[component.item_id]),
            limit=10,
        ),
    )
    return evidence_bundle(
        payload.question,
        response,
        {
            "assembly_revision_id": str(snapshot.root_revision_id),
            "component_item_id": str(component.item_id),
            "component_revision_id": str(component.revision_id),
            "occurrence_path": component.occurrence_path,
        },
    )
