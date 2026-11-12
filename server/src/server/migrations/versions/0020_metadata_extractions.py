"""Add immutable revision metadata extractions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020_metadata_extractions"
down_revision = "0019_ai_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "revisions",
        sa.Column("metadata_status", sa.String(32), nullable=False, server_default="UNAVAILABLE"),
    )
    op.create_check_constraint(
        "ck_revisions_metadata_status",
        "revisions",
        "metadata_status IN ('UNAVAILABLE', 'READY', 'PARTIAL', 'INVALID')",
    )
    op.create_index("ix_revisions_metadata_status", "revisions", ["metadata_status"])
    op.create_table(
        "metadata_extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("working_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("worker_type", sa.String(100), nullable=False),
        sa.Column("worker_version", sa.String(100), nullable=False),
        sa.Column("source_system", sa.String(100), nullable=False),
        sa.Column("source_system_version", sa.String(100), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(), nullable=False),
        sa.Column("errors_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('READY', 'PARTIAL', 'INVALID')",
            name="ck_metadata_extractions_status",
        ),
        sa.ForeignKeyConstraint(["revision_id"], ["revisions.id"]),
        sa.ForeignKeyConstraint(["working_version_id"], ["working_versions.id"]),
        sa.ForeignKeyConstraint(["raw_artifact_id"], ["artifacts.id"]),
    )
    op.create_index("ix_metadata_extractions_revision_id", "metadata_extractions", ["revision_id"])
    op.create_index(
        "ix_metadata_extractions_working_version_id",
        "metadata_extractions",
        ["working_version_id"],
    )
    op.create_index("ix_metadata_extractions_status", "metadata_extractions", ["status"])


def downgrade() -> None:
    op.drop_table("metadata_extractions")
    op.drop_index("ix_revisions_metadata_status", table_name="revisions")
    op.drop_constraint("ck_revisions_metadata_status", "revisions", type_="check")
    op.drop_column("revisions", "metadata_status")
