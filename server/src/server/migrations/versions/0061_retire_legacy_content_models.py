"""Retire WorkingVersion and AttachmentVersion persistence.

Revision ID: 0061_retire_legacy_content
Revises: 0060_snapshot_processing
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0061_retire_legacy_content"
down_revision = "0060_snapshot_processing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)
    op.add_column("artifacts", sa.Column("attachment_id", uuid_type, nullable=True))
    op.add_column("artifacts", sa.Column("client_version_id", uuid_type, nullable=True))
    op.add_column("artifacts", sa.Column("comment", sa.Text(), nullable=True))
    op.add_column("artifacts", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("artifacts", sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_artifacts_attachment", "artifacts", "attachments", ["attachment_id"], ["id"]
    )
    op.create_index("ix_artifacts_attachment_id", "artifacts", ["attachment_id"])
    op.create_index(
        "ix_artifacts_client_version_id", "artifacts", ["client_version_id"], unique=True
    )
    op.create_index("ix_artifacts_expires_at", "artifacts", ["expires_at"])
    op.execute(sa.text("""
        UPDATE artifacts AS container
        SET attachment_id = av.attachment_id,
            client_version_id = av.client_version_id,
            generation = av.version_number,
            name = attachment.name,
            description = attachment.description,
            comment = av.comment,
            artifact_type = 'DOCUMENT',
            classification = 'AUTHORITATIVE',
            source = 'USER',
            state = av.state,
            created_by = av.created_by,
            created_at = av.created_at,
            expires_at = av.expires_at,
            ready_at = av.ready_at
        FROM attachment_versions av
        JOIN artifact_files file ON file.id = av.artifact_id
        JOIN attachments attachment ON attachment.id = av.attachment_id
        WHERE container.id = file.artifact_id
    """))
    op.drop_constraint("ck_artifacts_state", "artifacts", type_="check")
    op.create_check_constraint(
        "ck_artifacts_state",
        "artifacts",
        "state IN ('UPLOADING', 'READY', 'PROCESSING', 'FAILED', 'SUPERSEDED', "
        "'CANCELLED', 'EXPIRED')",
    )
    op.drop_table("attachment_versions")

    columns = (
        ("revisions", "current_working_version_id", "fk_revisions_current_working_version"),
        ("artifact_files", "working_version_id", "fk_artifacts_working_version"),
        ("processing_jobs", "working_version_id", "fk_processing_jobs_working_version"),
        (
            "metadata_extractions",
            "working_version_id",
            "metadata_extractions_working_version_id_fkey",
        ),
        ("download_bundles", "working_version_id", "download_bundles_working_version_id_fkey"),
    )
    index_names = {
        ("artifact_files", "working_version_id"): "ix_artifacts_working_version_id",
        ("processing_jobs", "working_version_id"): "ix_processing_jobs_working_version_id",
        (
            "metadata_extractions",
            "working_version_id",
        ): "ix_metadata_extractions_working_version_id",
    }
    for table, column, constraint in columns:
        op.drop_constraint(constraint, table, type_="foreignkey")
        index_name = index_names.get((table, column))
        if index_name is not None:
            op.drop_index(index_name, table_name=table)
        op.drop_column(table, column)
    op.drop_table("working_versions")


def downgrade() -> None:
    raise RuntimeError(
        "0061 removes superseded content tables; restore the documented pre-upgrade database backup"
    )
