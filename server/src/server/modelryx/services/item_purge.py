"""Administrator-only permanent cleanup of one item and its owned records."""

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from modelryx.models import Item, Revision


@dataclass(frozen=True)
class ItemPurgePreview:
    revision_count: int
    artifact_count: int
    stored_bytes: int
    attachment_count: int
    blocking_reference_count: int
    blocking_item_numbers: list[str]


@dataclass(frozen=True)
class LegacyCascadePlan:
    deletable_items: list[Item]
    protected_items: list[Item]
    external_item_numbers: list[str]


def legacy_cascade_plan(db: Session, item_id: uuid.UUID) -> LegacyCascadePlan:
    """Classify referenced child Items that may be explicitly cascade-deleted."""
    source_revision_ids = {
        str(value) for value in db.scalars(select(Revision.id).where(Revision.part_id == item_id))
    }
    # Older generated components can retain a sourceRevisionId from a revision that was
    # replaced during reprocessing. The committed BOM is the authoritative current
    # ownership graph, so include marked Inventor components reachable from the root.
    descendant_item_ids = set(
        db.scalars(
            text(
                """
                WITH RECURSIVE descendants(id) AS (
                    SELECT child.part_id
                    FROM bom_lines line
                    JOIN revisions parent ON parent.id = line.parent_revision_id
                    JOIN revisions child ON child.id = line.child_revision_id
                    WHERE parent.part_id = :root_id
                    UNION
                    SELECT child.part_id
                    FROM descendants descendant
                    JOIN revisions parent ON parent.part_id = descendant.id
                    JOIN bom_lines line ON line.parent_revision_id = parent.id
                    JOIN revisions child ON child.id = line.child_revision_id
                )
                SELECT id FROM descendants
                """
            ),
            {"root_id": item_id},
        )
    )
    rows = db.execute(
        select(Item, Revision)
        .join(Revision, Revision.part_id == Item.id)
        .where(Revision.derived_metadata_json.has_key("inventorComponent"))
        .order_by(Item.part_number)
    ).all()
    candidates: dict[uuid.UUID, Item] = {}
    # Current assembly relationships are authoritative regardless of how the child Item was
    # created. Restricting this list to the old inventorComponent marker caused ordinary BOM
    # references to disappear from the permanent-deletion preview.
    if descendant_item_ids:
        for descendant in db.scalars(
            select(Item).where(Item.id.in_(descendant_item_ids), Item.id != item_id)
        ):
            candidates[descendant.id] = descendant
    for item, revision in rows:
        component = (revision.derived_metadata_json or {}).get("inventorComponent") or {}
        if (
            str(component.get("sourceRevisionId") or "") in source_revision_ids
            or item.id in descendant_item_ids
        ):
            candidates[item.id] = item
    if not candidates:
        return LegacyCascadePlan([], [], [])

    candidate_ids = list(candidates)
    neighbors = db.execute(
        text(
            """
            WITH candidates AS (SELECT unnest(CAST(:candidate_ids AS uuid[])) AS id), links AS (
                SELECT source.part_id AS left_id,
                       coalesce(target_revision.part_id, relationship.target_item_id) AS right_id
                FROM revision_relationships relationship
                JOIN revisions source ON source.id = relationship.source_revision_id
                LEFT JOIN revisions target_revision
                  ON target_revision.id = relationship.target_revision_id
                UNION ALL
                SELECT parent.part_id, child.part_id
                FROM bom_lines line
                JOIN revisions parent ON parent.id = line.parent_revision_id
                JOIN revisions child ON child.id = line.child_revision_id
                UNION ALL
                SELECT first_owner.part_id, second_owner.part_id
                FROM revision_artifact_relations first_relation
                JOIN revision_artifact_relations second_relation
                  ON second_relation.artifact_id = first_relation.artifact_id
                 AND second_relation.revision_id <> first_relation.revision_id
                JOIN revisions first_owner ON first_owner.id = first_relation.revision_id
                JOIN revisions second_owner ON second_owner.id = second_relation.revision_id
                UNION ALL
                SELECT owner_revision.part_id, derived_owner.part_id
                FROM artifacts derived
                JOIN revision_artifact_relations source_relation
                  ON source_relation.artifact_id = derived.derived_from_artifact_id
                JOIN revisions owner_revision ON owner_revision.id = source_relation.revision_id
                JOIN revision_artifact_relations derived_relation
                  ON derived_relation.artifact_id = derived.id
                JOIN revisions derived_owner ON derived_owner.id = derived_relation.revision_id
            )
            SELECT DISTINCT candidate.id, external.part_number
            FROM candidates candidate
            JOIN links ON links.left_id = candidate.id OR links.right_id = candidate.id
            JOIN parts external ON external.id = CASE
                WHEN links.left_id = candidate.id THEN links.right_id ELSE links.left_id END
            WHERE external.id <> :root_id
              AND external.id <> candidate.id
              AND NOT (external.id = ANY(CAST(:candidate_ids AS uuid[])))
            ORDER BY external.part_number
            """
        ),
        {"candidate_ids": candidate_ids, "root_id": item_id},
    ).all()
    blocked_ids = {row[0] for row in neighbors}
    deletable = [item for item in candidates.values() if item.id not in blocked_ids]
    protected = [item for item in candidates.values() if item.id in blocked_ids]
    external = sorted({str(row[1]) for row in neighbors})
    return LegacyCascadePlan(deletable, protected, external)


def preview_item_purge(db: Session, item_id: uuid.UUID) -> ItemPurgePreview:
    values = db.execute(
        text(
            """
            WITH target_revisions AS (
                SELECT id FROM revisions WHERE part_id = :item_id
            ), target_artifacts AS (
                SELECT DISTINCT artifact_id FROM revision_artifact_relations
                WHERE revision_id IN (SELECT id FROM target_revisions)
            )
            SELECT
                (SELECT count(*) FROM target_revisions),
                (SELECT count(*) FROM artifact_files
                 WHERE revision_id IN (SELECT id FROM target_revisions)),
                (SELECT coalesce(sum(byte_size), 0) FROM artifact_files
                 WHERE revision_id IN (SELECT id FROM target_revisions)),
                (SELECT count(*) FROM attachments WHERE part_id = :item_id),
                (
                    SELECT count(*) FROM revision_relationships
                    WHERE (target_item_id = :item_id
                           OR target_revision_id IN (SELECT id FROM target_revisions))
                      AND source_revision_id NOT IN (SELECT id FROM target_revisions)
                ) + (
                    SELECT count(*) FROM bom_lines line
                    JOIN bom_view_revisions view ON view.id = line.bom_view_revision_id
                    WHERE line.child_revision_id IN (SELECT id FROM target_revisions)
                      AND line.parent_revision_id NOT IN (SELECT id FROM target_revisions)
                ) + (
                    SELECT count(*) FROM revision_artifact_relations relation
                    WHERE relation.artifact_id IN (SELECT artifact_id FROM target_artifacts)
                      AND relation.revision_id NOT IN (SELECT id FROM target_revisions)
                ) + (
                    SELECT count(*) FROM artifacts derived
                    JOIN revision_artifact_relations relation
                      ON relation.artifact_id = derived.id
                    WHERE derived.derived_from_artifact_id IN
                          (SELECT artifact_id FROM target_artifacts)
                      AND relation.revision_id NOT IN (SELECT id FROM target_revisions)
                )
            """
        ),
        {"item_id": item_id},
    ).one()
    blocking_items = list(
        db.scalars(
            text("""
        SELECT DISTINCT part_number FROM (
            SELECT owner.part_number
            FROM revision_relationships link
            JOIN revisions source ON source.id = link.source_revision_id
            JOIN parts owner ON owner.id = source.part_id
            LEFT JOIN revisions target ON target.id = link.target_revision_id
            WHERE (link.target_item_id = :item_id OR target.part_id = :item_id)
              AND owner.id <> :item_id
            UNION
            SELECT owner.part_number
            FROM bom_lines line
            JOIN bom_view_revisions view ON view.id = line.bom_view_revision_id
            JOIN revisions parent_revision ON parent_revision.id = line.parent_revision_id
            JOIN parts owner ON owner.id = parent_revision.part_id
            JOIN revisions child_revision ON child_revision.id = line.child_revision_id
            WHERE child_revision.part_id = :item_id AND owner.id <> :item_id
            UNION
            SELECT owner.part_number
            FROM revision_artifact_relations shared_relation
            JOIN revisions owner_revision ON owner_revision.id = shared_relation.revision_id
            JOIN parts owner ON owner.id = owner_revision.part_id
            WHERE shared_relation.artifact_id IN (
                SELECT target_relation.artifact_id
                FROM revision_artifact_relations target_relation
                JOIN revisions target_revision ON target_revision.id = target_relation.revision_id
                WHERE target_revision.part_id = :item_id
            ) AND owner.id <> :item_id
            UNION
            SELECT owner.part_number
            FROM artifacts derived
            JOIN revision_artifact_relations derived_relation
              ON derived_relation.artifact_id = derived.id
            JOIN revisions owner_revision ON owner_revision.id = derived_relation.revision_id
            JOIN parts owner ON owner.id = owner_revision.part_id
            WHERE derived.derived_from_artifact_id IN (
                SELECT target_relation.artifact_id
                FROM revision_artifact_relations target_relation
                JOIN revisions target_revision ON target_revision.id = target_relation.revision_id
                WHERE target_revision.part_id = :item_id
            ) AND owner.id <> :item_id
        ) blockers ORDER BY part_number
    """),
            {"item_id": item_id},
        )
    )
    return ItemPurgePreview(*map(int, values), blocking_item_numbers=blocking_items)


def item_storage_keys(db: Session, item_id: uuid.UUID) -> tuple[list[str], list[str]]:
    artifact_keys = list(
        db.scalars(
            text(
                """
                SELECT storage_key FROM artifact_files
                WHERE revision_id IN (SELECT id FROM revisions WHERE part_id = :item_id)
                """
            ),
            {"item_id": item_id},
        )
    )
    artifact_keys.extend(
        db.scalars(
            text(
                """
                SELECT file.storage_key
                FROM staging_files file
                JOIN staging_sessions staging ON staging.id = file.staging_id
                WHERE staging.base_revision_id IN
                    (SELECT id FROM revisions WHERE part_id = :item_id)
                   OR staging.base_snapshot_id IN
                    (SELECT snapshot.id FROM revision_content_snapshots snapshot
                     JOIN revisions revision ON revision.id = snapshot.revision_id
                     WHERE revision.part_id = :item_id)
                """
            ),
            {"item_id": item_id},
        )
    )
    workspace_keys = list(
        db.scalars(
            text(
                """
                SELECT workspace_key FROM cad_sessions
                WHERE revision_id IN (SELECT id FROM revisions WHERE part_id = :item_id)
                """
            ),
            {"item_id": item_id},
        )
    )
    return artifact_keys, workspace_keys


def purge_item_records(db: Session, item: Item) -> None:
    """Delete an unreferenced item's complete owned graph in dependency order."""
    item_id = item.id
    artifact_container_ids = list(
        db.scalars(
            text("""
        SELECT DISTINCT relation.artifact_id
        FROM revision_artifact_relations relation
        JOIN revisions revision ON revision.id = relation.revision_id
        WHERE revision.part_id = :item_id
    """),
            {"item_id": item_id},
        )
    )
    if artifact_container_ids:
        # A generated legacy child can be retained when its source Item is purged. Its
        # representation is already self-contained, so remove the provenance dependency
        # and promote it to authoritative content before deleting the source containers.
        # Without this, an unchecked cascade Item leaves a derived artifact pointing at
        # content that the administrator explicitly asked to remove.
        db.execute(
            text(
                """
                UPDATE artifacts AS retained
                SET classification = 'AUTHORITATIVE',
                    derived_from_artifact_id = NULL
                WHERE retained.derived_from_artifact_id = ANY(:artifact_ids)
                  AND NOT (retained.id = ANY(:artifact_ids))
                """
            ),
            {"artifact_ids": artifact_container_ids},
        )
    statements = [
        "UPDATE parts SET current_revision_id = NULL WHERE id = :item_id",
        "UPDATE revisions SET current_metadata_extraction_id = NULL WHERE part_id = :item_id",
        """DELETE FROM duplicate_feedback WHERE duplicate_check_id IN
             (SELECT check_row.id FROM duplicate_checks check_row
              JOIN staging_sessions staging ON staging.id = check_row.source_staging_id
              WHERE staging.base_revision_id IN
                (SELECT id FROM revisions WHERE part_id = :item_id)
                 OR staging.base_snapshot_id IN
                (SELECT snapshot.id FROM revision_content_snapshots snapshot
                 JOIN revisions revision ON revision.id = snapshot.revision_id
                 WHERE revision.part_id = :item_id))""",
        """DELETE FROM duplicate_candidates WHERE duplicate_check_id IN
             (SELECT check_row.id FROM duplicate_checks check_row
              JOIN staging_sessions staging ON staging.id = check_row.source_staging_id
              WHERE staging.base_revision_id IN
                (SELECT id FROM revisions WHERE part_id = :item_id)
                 OR staging.base_snapshot_id IN
                (SELECT snapshot.id FROM revision_content_snapshots snapshot
                 JOIN revisions revision ON revision.id = snapshot.revision_id
                 WHERE revision.part_id = :item_id))""",
        """DELETE FROM duplicate_checks WHERE source_staging_id IN
             (SELECT staging.id FROM staging_sessions staging
              WHERE staging.base_revision_id IN
                (SELECT id FROM revisions WHERE part_id = :item_id)
                 OR staging.base_snapshot_id IN
                (SELECT snapshot.id FROM revision_content_snapshots snapshot
                 JOIN revisions revision ON revision.id = snapshot.revision_id
                 WHERE revision.part_id = :item_id))""",
        """DELETE FROM duplicate_feedback WHERE duplicate_check_id IN
               (SELECT id FROM duplicate_checks WHERE source_revision_id IN
                (SELECT id FROM revisions WHERE part_id = :item_id))
             OR candidate_id IN (SELECT id FROM duplicate_candidates WHERE candidate_revision_id IN
                (SELECT id FROM revisions WHERE part_id = :item_id))""",
        """DELETE FROM duplicate_candidates WHERE candidate_revision_id IN
               (SELECT id FROM revisions WHERE part_id = :item_id)
             OR duplicate_check_id IN (SELECT id FROM duplicate_checks WHERE source_revision_id IN
               (SELECT id FROM revisions WHERE part_id = :item_id))""",
        """DELETE FROM duplicate_checks WHERE source_revision_id IN
               (SELECT id FROM revisions WHERE part_id = :item_id)""",
        """DELETE FROM revision_relationships WHERE target_item_id = :item_id
             OR source_revision_id IN (SELECT id FROM revisions WHERE part_id = :item_id)
             OR target_revision_id IN (SELECT id FROM revisions WHERE part_id = :item_id)""",
        """DELETE FROM bom_lines WHERE bom_view_revision_id IN
             (SELECT view.id FROM bom_view_revisions view JOIN revisions revision
              ON revision.id = view.revision_id WHERE revision.part_id = :item_id)
             OR parent_revision_id IN (SELECT id FROM revisions WHERE part_id = :item_id)
             OR child_revision_id IN (SELECT id FROM revisions WHERE part_id = :item_id)""",
        """DELETE FROM bom_view_revisions WHERE revision_id IN
             (SELECT id FROM revisions WHERE part_id = :item_id)""",
        """DELETE FROM revision_comparisons WHERE item_id = :item_id
             OR from_revision_id IN (SELECT id FROM revisions WHERE part_id = :item_id)
             OR to_revision_id IN (SELECT id FROM revisions WHERE part_id = :item_id)""",
        "DELETE FROM ai_documents WHERE item_id = :item_id",
        "DELETE FROM knowledge_sources WHERE item_id = :item_id",
        """DELETE FROM cad_executions WHERE plan_id IN (SELECT id FROM cad_operation_plans
             WHERE session_id IN (SELECT id FROM cad_sessions WHERE revision_id IN
             (SELECT id FROM revisions WHERE part_id = :item_id)))""",
        """DELETE FROM cad_commit_plans WHERE session_id IN (SELECT id FROM cad_sessions
             WHERE revision_id IN (SELECT id FROM revisions WHERE part_id = :item_id))
             OR committed_revision_id IN (SELECT id FROM revisions WHERE part_id = :item_id)""",
        """DELETE FROM cad_operation_plans WHERE session_id IN (SELECT id FROM cad_sessions
             WHERE revision_id IN (SELECT id FROM revisions WHERE part_id = :item_id))""",
        """DELETE FROM cad_sessions WHERE revision_id IN
             (SELECT id FROM revisions WHERE part_id = :item_id)""",
        """DELETE FROM download_bundles WHERE revision_id IN
             (SELECT id FROM revisions WHERE part_id = :item_id)""",
        """DELETE FROM artifact_files WHERE artifact_id IN
             (SELECT artifact.id FROM artifacts artifact JOIN attachments attachment
              ON attachment.id = artifact.attachment_id WHERE attachment.part_id = :item_id)""",
        """DELETE FROM artifacts WHERE attachment_id IN
             (SELECT id FROM attachments WHERE part_id = :item_id)""",
        "DELETE FROM attachments WHERE part_id = :item_id",
        """DELETE FROM processing_jobs WHERE revision_id IN
             (SELECT id FROM revisions WHERE part_id = :item_id)""",
        """DELETE FROM metadata_extractions WHERE revision_id IN
             (SELECT id FROM revisions WHERE part_id = :item_id)""",
        """DELETE FROM revision_approval_requests WHERE revision_id IN
             (SELECT id FROM revisions WHERE part_id = :item_id)
             OR snapshot_id IN
             (SELECT snapshot.id FROM revision_content_snapshots snapshot
              JOIN revisions revision ON revision.id = snapshot.revision_id
              WHERE revision.part_id = :item_id)""",
        """DELETE FROM validation_runs WHERE staging_id IN
             (SELECT staging.id FROM staging_sessions staging
              WHERE staging.base_revision_id IN
                (SELECT id FROM revisions WHERE part_id = :item_id)
                 OR staging.base_snapshot_id IN
                (SELECT snapshot.id FROM revision_content_snapshots snapshot
                 JOIN revisions revision ON revision.id = snapshot.revision_id
                 WHERE revision.part_id = :item_id))""",
        """DELETE FROM staging_files WHERE staging_id IN
             (SELECT staging.id FROM staging_sessions staging
              WHERE staging.base_revision_id IN
                (SELECT id FROM revisions WHERE part_id = :item_id)
                 OR staging.base_snapshot_id IN
                (SELECT snapshot.id FROM revision_content_snapshots snapshot
                 JOIN revisions revision ON revision.id = snapshot.revision_id
                 WHERE revision.part_id = :item_id))""",
        """DELETE FROM staging_sessions WHERE base_revision_id IN
             (SELECT id FROM revisions WHERE part_id = :item_id)
             OR base_snapshot_id IN
             (SELECT snapshot.id FROM revision_content_snapshots snapshot
              JOIN revisions revision ON revision.id = snapshot.revision_id
              WHERE revision.part_id = :item_id)""",
        "DELETE FROM checkouts WHERE part_id = :item_id",
        """DELETE FROM revision_content_snapshot_artifacts WHERE snapshot_id IN
             (SELECT snapshot.id FROM revision_content_snapshots snapshot JOIN revisions revision
              ON revision.id = snapshot.revision_id WHERE revision.part_id = :item_id)""",
        """DELETE FROM revision_content_snapshots WHERE revision_id IN
             (SELECT id FROM revisions WHERE part_id = :item_id)""",
        """DELETE FROM artifact_files WHERE revision_id IN
             (SELECT id FROM revisions WHERE part_id = :item_id)""",
        """DELETE FROM revision_artifact_relations WHERE revision_id IN
             (SELECT id FROM revisions WHERE part_id = :item_id)""",
        "DELETE FROM revisions WHERE part_id = :item_id",
        "DELETE FROM favorites WHERE item_id = :item_id",
        "DELETE FROM recently_viewed_items WHERE item_id = :item_id",
        "DELETE FROM parts WHERE id = :item_id",
    ]
    for statement in statements:
        db.execute(text(statement), {"item_id": item_id})
    if artifact_container_ids:
        # Delete provenance leaves before their authoritative sources. A bulk DELETE has no
        # ordering guarantee and can otherwise violate artifacts.derived_from_artifact_id.
        remaining = set(artifact_container_ids)
        while remaining:
            deleted = set(
                db.scalars(
                    text(
                        """
                        DELETE FROM artifacts artifact
                        WHERE artifact.id = ANY(:artifact_ids)
                          AND NOT EXISTS (
                            SELECT 1 FROM artifacts derived
                            WHERE derived.derived_from_artifact_id = artifact.id
                          )
                        RETURNING artifact.id
                        """
                    ),
                    {"artifact_ids": list(remaining)},
                )
            )
            if not deleted:
                raise ValueError(
                    "Item artifacts are still referenced by retained derived artifacts"
                )
            remaining.difference_update(deleted)


def remove_purged_files(
    storage_root: Path, artifact_keys: list[str], workspace_keys: list[str]
) -> list[str]:
    """Best-effort post-commit cleanup; the reconciler handles any retained orphan."""
    failures: list[str] = []
    root = storage_root.resolve()
    for key in artifact_keys:
        try:
            target = (root / key).resolve()
            if not target.is_relative_to(root):
                raise ValueError("storage key escapes storage root")
            target.unlink(missing_ok=True)
            target.with_suffix(target.suffix + ".partial").unlink(missing_ok=True)
        except (OSError, ValueError):
            failures.append(key)
    for key in workspace_keys:
        try:
            target = (root / key).resolve()
            session_root = (root / "cad-sessions").resolve()
            if not target.is_relative_to(session_root):
                raise ValueError("workspace key escapes CAD session root")
            if target.exists():
                import shutil

                shutil.rmtree(target)
        except (OSError, ValueError):
            failures.append(key)
    return failures
