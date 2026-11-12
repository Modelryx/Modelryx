"""Add observable conversion stages and diagnostics."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_processing_diagnostics"
down_revision = "0008_assembly_occurrences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "processing_jobs",
        sa.Column("stage", sa.String(32), nullable=False, server_default="QUEUED"),
    )
    op.add_column(
        "processing_jobs",
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("processing_jobs", sa.Column("converter_version", sa.String(64)))
    op.add_column("processing_jobs", sa.Column("cache_key", sa.String(128)))
    op.add_column("processing_jobs", sa.Column("diagnostics_json", postgresql.JSONB()))
    op.create_index("ix_processing_jobs_cache_key", "processing_jobs", ["cache_key"])


def downgrade() -> None:
    op.drop_column("processing_jobs", "diagnostics_json")
    op.drop_index("ix_processing_jobs_cache_key", table_name="processing_jobs")
    op.drop_column("processing_jobs", "cache_key")
    op.drop_column("processing_jobs", "converter_version")
    op.drop_column("processing_jobs", "progress_percent")
    op.drop_column("processing_jobs", "stage")
