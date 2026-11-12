"""Persist optional structured AI reasoning on duplicate candidates."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023_duplicate_ai_results"
down_revision = "0022_duplicate_detection_phase1a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "duplicate_candidates",
        sa.Column("ai_result_json", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("duplicate_candidates", "ai_result_json")
