"""Record duplicate detection stage timings."""

import sqlalchemy as sa
from alembic import op

revision = "0025_duplicate_observability"
down_revision = "0024_duplicate_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in (
        "total_duration_ms",
        "metadata_duration_ms",
        "vector_duration_ms",
        "ai_duration_ms",
    ):
        op.add_column(
            "duplicate_checks",
            sa.Column(name, sa.Float(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for name in (
        "ai_duration_ms",
        "vector_duration_ms",
        "metadata_duration_ms",
        "total_duration_ms",
    ):
        op.drop_column("duplicate_checks", name)
