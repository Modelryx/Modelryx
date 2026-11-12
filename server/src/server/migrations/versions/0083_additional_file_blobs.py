"""Add content-addressed blobs for Additional File deduplication."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0083_additional_file_blobs"
down_revision = "0082_additional_file_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_blobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("byte_size >= 0", name="ck_content_blobs_byte_size"),
        sa.CheckConstraint(
            "state IN ('VERIFIED', 'QUARANTINED')", name="ck_content_blobs_state"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256", "byte_size", name="uq_content_blobs_digest_size"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_content_blobs_sha256", "content_blobs", ["sha256"])
    op.add_column(
        "artifact_files",
        sa.Column("content_blob_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_artifact_files_content_blob_id", "artifact_files", ["content_blob_id"]
    )
    op.create_foreign_key(
        "fk_artifact_files_content_blob_id",
        "artifact_files",
        "content_blobs",
        ["content_blob_id"],
        ["id"],
    )
    # Preserve existing paths as canonical blob locations. Existing duplicate
    # copies remain harmless orphans until the Phase 7 storage cleanup.
    op.execute(
        """
        INSERT INTO content_blobs
            (id, sha256, byte_size, storage_key, state, created_at, verified_at)
        SELECT DISTINCT ON (file.sha256, file.byte_size)
            gen_random_uuid(), file.sha256, file.byte_size, file.storage_key,
            'VERIFIED', file.created_at, file.created_at
        FROM artifact_files file
        JOIN artifacts artifact ON artifact.id = file.artifact_id
        WHERE artifact.attachment_id IS NOT NULL
          AND artifact.state = 'READY'
          AND file.state = 'VERIFIED'
        ORDER BY file.sha256, file.byte_size, file.created_at, file.id
        """
    )
    op.execute(
        """
        UPDATE artifact_files file
        SET content_blob_id = blob.id
        FROM artifacts artifact, content_blobs blob
        WHERE artifact.id = file.artifact_id
          AND artifact.attachment_id IS NOT NULL
          AND file.state = 'VERIFIED'
          AND blob.sha256 = file.sha256
          AND blob.byte_size = file.byte_size
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_artifact_files_content_blob_id", "artifact_files", type_="foreignkey")
    op.drop_index("ix_artifact_files_content_blob_id", table_name="artifact_files")
    op.drop_column("artifact_files", "content_blob_id")
    op.drop_index("ix_content_blobs_sha256", table_name="content_blobs")
    op.drop_table("content_blobs")
