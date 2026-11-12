"""Add asynchronous deterministic download bundles."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_download_bundles"
down_revision = "0009_processing_diagnostics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "download_bundles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "working_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("working_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "processing_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("processing_jobs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id")),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_detail", sa.Text()),
        sa.CheckConstraint(
            "state IN ('PENDING', 'RUNNING', 'READY', 'FAILED')", name="ck_download_bundles_state"
        ),
    )
    op.create_index("ix_download_bundles_revision_id", "download_bundles", ["revision_id"])
    op.create_index("ix_download_bundles_state", "download_bundles", ["state"])


def downgrade() -> None:
    op.drop_table("download_bundles")
