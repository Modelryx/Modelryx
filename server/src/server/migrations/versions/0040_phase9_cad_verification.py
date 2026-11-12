"""Link Phase 9 CAD executions to the isolated worker queue."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0040_phase9_cad_verification"
down_revision = "0039_phase9_cad_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cad_executions",
        sa.Column("processing_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_cad_executions_processing_job_id",
        "cad_executions",
        "processing_jobs",
        ["processing_job_id"],
        ["id"],
    )
    op.create_index(
        "ix_cad_executions_processing_job_id",
        "cad_executions",
        ["processing_job_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_cad_executions_processing_job_id", table_name="cad_executions")
    op.drop_constraint("fk_cad_executions_processing_job_id", "cad_executions", type_="foreignkey")
    op.drop_column("cad_executions", "processing_job_id")
