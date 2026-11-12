"""Make Additional File revision ownership relation-based."""

import sqlalchemy as sa
from alembic import op

revision = "0082_additional_file_links"
down_revision = "0081_user_login_lockout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("artifact_files", "revision_id", existing_type=sa.UUID(), nullable=True)
    op.execute(
        """
        INSERT INTO revision_artifact_relations
            (id, revision_id, artifact_id, relation_type, sequence, is_primary,
             created_by, created_at)
        SELECT
            gen_random_uuid(), artifact_file.revision_id, artifact.id, 'ATTACHMENT',
            0, false, artifact.created_by, artifact.created_at
        FROM artifacts artifact
        JOIN artifact_files artifact_file ON artifact_file.artifact_id = artifact.id
        WHERE artifact.attachment_id IS NOT NULL
          AND artifact_file.revision_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM revision_artifact_relations relation
              WHERE relation.revision_id = artifact_file.revision_id
                AND relation.artifact_id = artifact.id
                AND relation.relation_type = 'ATTACHMENT'
          )
        """
    )
    # Legacy attachment uploads were presented as revision content through the
    # ArtifactFile revision adapter. Preserve that visible state by pinning each
    # backfilled relation into the revision's latest existing snapshot.
    op.execute(
        """
        INSERT INTO revision_content_snapshot_artifacts (snapshot_id, relation_id, sequence)
        SELECT latest_snapshot.id, relation.id,
               COALESCE((
                   SELECT MAX(existing.sequence) + 1
                   FROM revision_content_snapshot_artifacts existing
                   WHERE existing.snapshot_id = latest_snapshot.id
               ), 0)
        FROM revision_artifact_relations relation
        JOIN artifacts artifact ON artifact.id = relation.artifact_id
        JOIN LATERAL (
            SELECT snapshot.id
            FROM revision_content_snapshots snapshot
            WHERE snapshot.revision_id = relation.revision_id
            ORDER BY snapshot.sequence DESC, snapshot.id DESC
            LIMIT 1
        ) latest_snapshot ON true
        WHERE relation.relation_type = 'ATTACHMENT'
          AND artifact.attachment_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM revision_content_snapshot_artifacts existing
              WHERE existing.snapshot_id = latest_snapshot.id
                AND existing.relation_id = relation.id
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE artifact_files artifact_file
        SET revision_id = item.current_revision_id
        FROM artifacts artifact
        JOIN attachments attachment ON attachment.id = artifact.attachment_id
        JOIN parts item ON item.id = attachment.part_id
        WHERE artifact_file.artifact_id = artifact.id
          AND artifact_file.revision_id IS NULL
        """
    )
    op.alter_column("artifact_files", "revision_id", existing_type=sa.UUID(), nullable=False)
