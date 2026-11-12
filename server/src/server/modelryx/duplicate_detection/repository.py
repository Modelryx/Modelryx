from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from time import perf_counter
from types import SimpleNamespace

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from modelryx.metadata.derived import build_derived_metadata
from modelryx.models import (
    AiDocument,
    Artifact,
    ArtifactFile,
    DuplicateCandidate,
    DuplicateCheck,
    Item,
    MetadataExtraction,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    StagingFile,
    StagingSession,
)
from modelryx.services.artifact_content import current_artifact_files

from .models import Classification, DuplicateResult, ScoreBreakdown
from .reasoner import DuplicateReasoner
from .service import DuplicateDetectionService

ALGORITHM_VERSION = "part-similarity-v1"
logger = logging.getLogger("modelryx.duplicate_detection")


class DuplicateDetectionRepository:
    def __init__(
        self,
        db: Session,
        service: DuplicateDetectionService | None = None,
        reasoner: DuplicateReasoner | None = None,
        *,
        ai_review_min: float = 0.88,
        ai_review_max: float = 0.985,
        max_ai_candidates: int = 5,
    ):
        self.db = db
        self.service = service or DuplicateDetectionService()
        self.reasoner = reasoner
        self.ai_review_min = ai_review_min
        self.ai_review_max = ai_review_max
        self.max_ai_candidates = max_ai_candidates

    def run(self, source: Revision, *, actor_id: uuid.UUID, administrator: bool) -> DuplicateCheck:
        source_metadata = self.db.get(MetadataExtraction, source.current_metadata_extraction_id)
        if source_metadata is None:
            raise ValueError("source revision has no current metadata extraction")
        source_files = current_artifact_files(
            self.db,
            source.id,
            artifact_types={"CAD_MODEL", "CAD_ASSEMBLY"},
        )
        # Secondary files are package dependencies, not the revision's identity.
        # Treating a shared component as an exact match makes two different
        # assemblies exact duplicates merely because they contain the same part.
        source_hashes = {
            artifact_file.sha256
            for artifact_file in source_files
            if artifact_file.role == "PRIMARY"
        }
        if not source_hashes:
            source_hashes = set(
                self.db.scalars(
                    select(ArtifactFile.sha256).where(
                        ArtifactFile.revision_id == source.id,
                        ArtifactFile.state == "VERIFIED",
                        ArtifactFile.kind == "NATIVE",
                    )
                )
            )
        return self._run(
            source_metadata=source_metadata,
            source_hashes=source_hashes,
            source_revision_id=source.id,
            source_staging_id=None,
            actor_id=actor_id,
            administrator=administrator,
            source_embedding=None,
        )

    def run_staging(
        self,
        source: StagingSession,
        *,
        actor_id: uuid.UUID,
        administrator: bool,
        source_embedding: list[float] | None = None,
    ) -> DuplicateCheck:
        if source.metadata_json is None:
            raise ValueError("staging session has no normalized metadata")
        derived = build_derived_metadata(source.metadata_json)
        source_metadata = SimpleNamespace(
            metadata_json=source.metadata_json,
            kind=source.metadata_json["identity"]["kind"],
            bbox_max_mm=derived["bbox_max_mm"],
            volume_mm3=source.metadata_json["geometry"]["shape"]["volume_mm3"],
        )
        staged_hashes = list(
            self.db.scalars(select(StagingFile.sha256).where(StagingFile.staging_id == source.id))
        )
        source_hashes = set(staged_hashes) if len(staged_hashes) == 1 else set()
        return self._run(
            source_metadata=source_metadata,
            source_hashes=source_hashes,
            source_revision_id=None,
            source_staging_id=source.id,
            actor_id=actor_id,
            administrator=administrator,
            source_embedding=source_embedding,
        )

    def _run(
        self,
        *,
        source_metadata,
        source_hashes: set[str],
        source_revision_id: uuid.UUID | None,
        source_staging_id: uuid.UUID | None,
        actor_id: uuid.UUID,
        administrator: bool,
        source_embedding: list[float] | None,
    ) -> DuplicateCheck:
        started = perf_counter()
        exact_revision_ids: set[uuid.UUID] = set()
        if source_hashes:
            exact_filters = [
                ArtifactFile.state == "VERIFIED",
                ArtifactFile.role == "PRIMARY",
                Artifact.artifact_type.in_(["CAD_MODEL", "CAD_ASSEMBLY"]),
                ArtifactFile.sha256.in_(source_hashes),
                RevisionContentSnapshotArtifact.snapshot_id
                == select(RevisionContentSnapshot.id)
                .where(RevisionContentSnapshot.revision_id == RevisionArtifactRelation.revision_id)
                .order_by(RevisionContentSnapshot.sequence.desc())
                .limit(1)
                .correlate(RevisionArtifactRelation)
                .scalar_subquery(),
            ]
            if source_revision_id is not None:
                exact_filters.append(RevisionArtifactRelation.revision_id != source_revision_id)
            exact_revision_ids = set(
                self.db.scalars(
                    select(RevisionArtifactRelation.revision_id)
                    .join(Artifact, Artifact.id == RevisionArtifactRelation.artifact_id)
                    .join(ArtifactFile, ArtifactFile.artifact_id == Artifact.id)
                    .join(
                        RevisionContentSnapshotArtifact,
                        RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id,
                    )
                    .where(*exact_filters)
                )
            )
            legacy_filters = [
                ArtifactFile.state == "VERIFIED",
                ArtifactFile.kind == "NATIVE",
                ArtifactFile.sha256.in_(source_hashes),
                ~exists(
                    select(RevisionContentSnapshot.id).where(
                        RevisionContentSnapshot.revision_id == ArtifactFile.revision_id
                    )
                ),
            ]
            if source_revision_id is not None:
                legacy_filters.append(ArtifactFile.revision_id != source_revision_id)
            exact_revision_ids.update(
                self.db.scalars(select(ArtifactFile.revision_id).where(*legacy_filters))
            )
        visible = or_(
            Revision.state.in_(["READY", "READY_WITH_WARNINGS"]),
            Item.created_by == actor_id,
        )
        if administrator:
            visible = Item.status == "ACTIVE"
        coarse = and_(
            MetadataExtraction.kind == source_metadata.kind,
            MetadataExtraction.bbox_max_mm.between(
                source_metadata.bbox_max_mm * 0.7, source_metadata.bbox_max_mm * 1.3
            ),
            MetadataExtraction.volume_mm3.between(
                source_metadata.volume_mm3 * 0.6, source_metadata.volume_mm3 * 1.4
            ),
        )
        base = (
            select(Revision, MetadataExtraction, Item)
            .join(Item, Item.id == Revision.part_id)
            .join(
                MetadataExtraction, MetadataExtraction.id == Revision.current_metadata_extraction_id
            )
            .where(Item.status == "ACTIVE", visible)
        )
        if source_revision_id is not None:
            base = base.where(Revision.id != source_revision_id)
        before = self.db.scalar(select(func.count()).select_from(base.subquery())) or 0
        retrieval = coarse
        if exact_revision_ids:
            retrieval = or_(Revision.id.in_(exact_revision_ids), coarse)
        metadata_rows = self.db.execute(base.where(retrieval).limit(50)).all()
        candidate_ids = {revision.id for revision, _, _ in metadata_rows}
        embedding_scores: dict[uuid.UUID, float] = {}
        vector_started = perf_counter()
        source_document = None
        if source_revision_id is not None:
            source_document = self.db.scalar(
                select(AiDocument).where(
                    AiDocument.revision_id == source_revision_id,
                    AiDocument.chunk_key == "part-metadata-v1",
                    AiDocument.embedding.is_not(None),
                )
            )
        embedding = source_document.embedding if source_document is not None else source_embedding
        if embedding is not None:
            distance = AiDocument.embedding.cosine_distance(embedding)
            vector_rows = self.db.execute(
                select(AiDocument.revision_id, distance.label("distance"))
                .join(Revision, Revision.id == AiDocument.revision_id)
                .join(Item, Item.id == Revision.part_id)
                .where(
                    AiDocument.revision_id != source_revision_id,
                    AiDocument.chunk_key == "part-metadata-v1",
                    AiDocument.embedding.is_not(None),
                    Item.status == "ACTIVE",
                    visible,
                )
                .order_by(distance)
                .limit(50)
            ).all()
            for revision_id, cosine_distance in vector_rows:
                candidate_ids.add(revision_id)
                embedding_scores[revision_id] = max(0.0, 1.0 - float(cosine_distance))
        vector_duration_ms = (perf_counter() - vector_started) * 1000
        rows = metadata_rows
        missing_ids = candidate_ids - {revision.id for revision, _, _ in metadata_rows}
        if missing_ids:
            rows.extend(self.db.execute(base.where(Revision.id.in_(missing_ids))).all())
        check = DuplicateCheck(
            source_revision_id=source_revision_id,
            source_staging_id=source_staging_id,
            status="COMPLETED",
            algorithm_version=ALGORITHM_VERSION,
            candidate_count_before_filter=before,
            candidate_count_after_filter=len(rows),
            exact_match_found=False,
            vector_duration_ms=vector_duration_ms,
            created_by=actor_id,
        )
        self.db.add(check)
        self.db.flush()
        metadata_started = perf_counter()
        ranked: list[tuple[float, DuplicateCandidate, dict, str]] = []
        for revision, extraction, item in rows:
            exact = revision.id in exact_revision_ids
            if exact:
                result = DuplicateResult(
                    Classification.EXACT_FILE_DUPLICATE,
                    ScoreBreakdown(metadata=1.0, combined=1.0),
                    ["Verified native-file SHA-256 matches."],
                    recommended_action="reuse_existing",
                )
                check.exact_match_found = True
            else:
                result = self.service.compare(
                    source_metadata.metadata_json,
                    extraction.metadata_json,
                    embedding_score=embedding_scores.get(revision.id),
                )
            if result.classification == Classification.DIFFERENT:
                continue
            candidate = DuplicateCandidate(
                duplicate_check_id=check.id,
                candidate_revision_id=revision.id,
                classification=result.classification.value,
                score_breakdown_json=asdict(result.scores),
                reasons_json=result.reasons,
                differences_json=result.differences,
                recommended_action=result.recommended_action,
            )
            ranked.append(
                (
                    result.scores.combined or 0.0,
                    candidate,
                    extraction.metadata_json,
                    str(item.id),
                )
            )
        check.metadata_duration_ms = (perf_counter() - metadata_started) * 1000
        selected = sorted(ranked, key=lambda entry: entry[0], reverse=True)[:10]
        ai_used = 0
        ai_started = perf_counter()
        for score, candidate, candidate_metadata, candidate_item_id in selected:
            if (
                self.reasoner is not None
                and ai_used < self.max_ai_candidates
                and self.ai_review_min <= score < self.ai_review_max
            ):
                ai_used += 1
                try:
                    ai_result = self.reasoner.classify(
                        candidate_item_id=candidate_item_id,
                        source=source_metadata.metadata_json,
                        candidate=candidate_metadata,
                        scores=ScoreBreakdown(**candidate.score_breakdown_json),
                    )
                    candidate.ai_result_json = ai_result.model_dump()
                except Exception:
                    logger.exception(
                        "duplicate_ai_reasoning_failed candidate_revision_id=%s",
                        candidate.candidate_revision_id,
                    )
            self.db.add(candidate)
        check.ai_duration_ms = (perf_counter() - ai_started) * 1000 if ai_used else 0
        check.total_duration_ms = (perf_counter() - started) * 1000
        return check
