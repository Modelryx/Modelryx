"""Guarded permanent deletion of mutable Item revisions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from modelryx.models import Item, Revision


IMMUTABLE_LIFECYCLE_STATES = {"RELEASED", "SUPERSEDED", "OBSOLETE"}


@dataclass(frozen=True)
class RevisionDeletionEligibility:
    eligible: bool
    reason: str | None = None


def revision_deletion_eligibility(
    db: Session, item: Item, revision: Revision
) -> RevisionDeletionEligibility:
    if revision.part_id != item.id:
        return RevisionDeletionEligibility(False, "Revision does not belong to this Item")
    if revision.lifecycle_state in IMMUTABLE_LIFECYCLE_STATES:
        return RevisionDeletionEligibility(
            False, f"{revision.lifecycle_state.title()} revisions are immutable"
        )
    parameters = {"revision_id": revision.id}
    if db.scalar(text("""SELECT EXISTS(SELECT 1 FROM checkouts
        WHERE revision_id = :revision_id OR root_revision_id = :revision_id)"""), parameters):
        return RevisionDeletionEligibility(False, "Revision is checked out")
    if db.scalar(text("""SELECT EXISTS(SELECT 1 FROM staging_sessions
        WHERE base_revision_id = :revision_id
          AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED', 'PROMOTED'))"""), parameters):
        return RevisionDeletionEligibility(False, "Revision has an active workspace task")
    if db.scalar(
        text("""SELECT EXISTS(SELECT 1 FROM bom_lines
        WHERE child_revision_id = :revision_id AND parent_revision_id <> :revision_id)"""),
        parameters,
    ):
        return RevisionDeletionEligibility(False, "Revision is used by an assembly BOM")
    if db.scalar(
        text("""SELECT EXISTS(SELECT 1 FROM revision_relationships
        WHERE target_revision_id = :revision_id AND source_revision_id <> :revision_id)"""),
        parameters,
    ):
        return RevisionDeletionEligibility(False, "Revision is referenced by another revision")
    if db.scalar(
        text("""SELECT EXISTS(SELECT 1 FROM attachment_checkouts
        WHERE revision_id = :revision_id)"""),
        parameters,
    ):
        return RevisionDeletionEligibility(False, "Revision has an active attachment edit")
    return RevisionDeletionEligibility(True)


def revision_storage_keys(db: Session, revision_id: uuid.UUID) -> tuple[list[str], list[str]]:
    parameters = {"revision_id": revision_id}
    artifact_keys = list(db.scalars(text("""SELECT storage_key FROM artifact_files
        WHERE revision_id = :revision_id
        UNION
        SELECT file.storage_key FROM artifact_files file
        JOIN revision_artifact_relations relation ON relation.artifact_id = file.artifact_id
        WHERE relation.revision_id = :revision_id
          AND NOT EXISTS (SELECT 1 FROM revision_artifact_relations other
              WHERE other.artifact_id = relation.artifact_id
                AND other.revision_id <> :revision_id)"""), parameters))
    artifact_keys.extend(db.scalars(text("""SELECT file.storage_key
        FROM staging_files file
        JOIN staging_sessions staging ON staging.id = file.staging_id
        WHERE staging.base_revision_id = :revision_id
           OR staging.base_snapshot_id IN (
             SELECT id FROM revision_content_snapshots WHERE revision_id = :revision_id
           )
        UNION
        SELECT storage_key FROM additional_file_stages
        WHERE revision_id = :revision_id AND duplicate_action = 'UPLOAD'"""), parameters))
    workspace_keys = list(db.scalars(
        text("SELECT workspace_key FROM cad_sessions WHERE revision_id = :revision_id"),
        parameters,
    ))
    return artifact_keys, workspace_keys


def purge_revision_records(db: Session, item: Item, revision: Revision) -> None:
    """Delete one eligible revision and its owned graph in dependency order."""
    revision_id = revision.id
    parameters = {"revision_id": revision_id}
    replacement_id = db.scalar(
        select(Revision.id)
        .where(Revision.part_id == item.id, Revision.id != revision_id)
        .order_by(Revision.created_at.desc(), Revision.revision_code.desc())
        .limit(1)
    )
    owned_artifact_ids = list(db.scalars(text("""SELECT DISTINCT relation.artifact_id
        FROM revision_artifact_relations relation
        WHERE relation.revision_id = :revision_id
          AND NOT EXISTS (SELECT 1 FROM revision_artifact_relations other
              WHERE other.artifact_id = relation.artifact_id
                AND other.revision_id <> :revision_id)"""), parameters))
    if item.current_revision_id == revision_id:
        item.current_revision_id = replacement_id
        db.flush()
    revision.current_metadata_extraction_id = None
    db.flush()
    statements = [
        """DELETE FROM duplicate_feedback WHERE duplicate_check_id IN
             (SELECT id FROM duplicate_checks WHERE source_revision_id = :revision_id)
             OR candidate_id IN (SELECT id FROM duplicate_candidates
                                  WHERE candidate_revision_id = :revision_id)""",
        """DELETE FROM duplicate_candidates WHERE candidate_revision_id = :revision_id
             OR duplicate_check_id IN
                (SELECT id FROM duplicate_checks WHERE source_revision_id = :revision_id)""",
        "DELETE FROM duplicate_checks WHERE source_revision_id = :revision_id",
        """DELETE FROM revision_relationships WHERE source_revision_id = :revision_id
             OR target_revision_id = :revision_id""",
        """DELETE FROM bom_lines WHERE parent_revision_id = :revision_id
             OR child_revision_id = :revision_id
             OR bom_view_revision_id IN
                (SELECT id FROM bom_view_revisions WHERE revision_id = :revision_id)""",
        "DELETE FROM bom_view_revisions WHERE revision_id = :revision_id",
        """DELETE FROM revision_comparisons WHERE from_revision_id = :revision_id
             OR to_revision_id = :revision_id""",
        "DELETE FROM ai_documents WHERE revision_id = :revision_id",
        "DELETE FROM knowledge_sources WHERE revision_id = :revision_id",
        """DELETE FROM cad_executions WHERE plan_id IN (SELECT id FROM cad_operation_plans
             WHERE session_id IN (SELECT id FROM cad_sessions WHERE revision_id = :revision_id))""",
        """DELETE FROM cad_commit_plans WHERE session_id IN
             (SELECT id FROM cad_sessions WHERE revision_id = :revision_id)
             OR committed_revision_id = :revision_id""",
        """DELETE FROM cad_operation_plans WHERE session_id IN
             (SELECT id FROM cad_sessions WHERE revision_id = :revision_id)""",
        "DELETE FROM cad_sessions WHERE revision_id = :revision_id",
        "DELETE FROM download_bundles WHERE revision_id = :revision_id",
        "DELETE FROM additional_file_stages WHERE revision_id = :revision_id",
        "DELETE FROM attachment_checkouts WHERE revision_id = :revision_id",
        "DELETE FROM processing_jobs WHERE revision_id = :revision_id",
        "DELETE FROM metadata_extractions WHERE revision_id = :revision_id",
        """DELETE FROM revision_approval_requests WHERE revision_id = :revision_id
             OR snapshot_id IN (SELECT id FROM revision_content_snapshots
                                WHERE revision_id = :revision_id)""",
        """DELETE FROM validation_runs WHERE staging_id IN
             (SELECT staging.id FROM staging_sessions staging
              WHERE staging.base_revision_id = :revision_id
                 OR staging.base_snapshot_id IN (SELECT id FROM revision_content_snapshots
                                                  WHERE revision_id = :revision_id))""",
        """DELETE FROM staging_files WHERE staging_id IN
             (SELECT staging.id FROM staging_sessions staging
              WHERE staging.base_revision_id = :revision_id
                 OR staging.base_snapshot_id IN (SELECT id FROM revision_content_snapshots
                                                  WHERE revision_id = :revision_id))""",
        """DELETE FROM staging_sessions WHERE base_revision_id = :revision_id
             OR base_snapshot_id IN (SELECT id FROM revision_content_snapshots
                                     WHERE revision_id = :revision_id)""",
        """DELETE FROM checkouts WHERE revision_id = :revision_id
             OR root_revision_id = :revision_id
             OR base_snapshot_id IN (SELECT id FROM revision_content_snapshots
                                     WHERE revision_id = :revision_id)""",
        """DELETE FROM revision_content_snapshot_artifacts WHERE snapshot_id IN
             (SELECT id FROM revision_content_snapshots WHERE revision_id = :revision_id)""",
        "DELETE FROM revision_content_snapshots WHERE revision_id = :revision_id",
        "DELETE FROM artifact_files WHERE revision_id = :revision_id",
        "DELETE FROM revision_artifact_relations WHERE revision_id = :revision_id",
    ]
    for statement in statements:
        db.execute(text(statement), parameters)
    if owned_artifact_ids:
        artifact_parameters = {"artifact_ids": owned_artifact_ids}
        db.execute(text("""UPDATE artifacts SET classification = 'AUTHORITATIVE',
            derived_from_artifact_id = NULL
            WHERE derived_from_artifact_id = ANY(:artifact_ids)
              AND NOT (id = ANY(:artifact_ids))"""), artifact_parameters)
        db.execute(
            text("DELETE FROM artifact_files WHERE artifact_id = ANY(:artifact_ids)"),
            artifact_parameters,
        )
        remaining = set(owned_artifact_ids)
        while remaining:
            deleted = set(db.scalars(text("""DELETE FROM artifacts artifact
                WHERE artifact.id = ANY(:artifact_ids)
                  AND NOT EXISTS (SELECT 1 FROM artifacts derived
                                  WHERE derived.derived_from_artifact_id = artifact.id)
                RETURNING artifact.id"""), {"artifact_ids": list(remaining)}))
            if not deleted:
                raise ValueError("Revision artifacts are still referenced")
            remaining.difference_update(deleted)
    db.execute(text("DELETE FROM revisions WHERE id = :revision_id"), parameters)
