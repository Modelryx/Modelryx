"""Persist authoritative user decisions for duplicate checks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_duplicate_feedback"
down_revision = "0023_duplicate_ai_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "duplicate_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("duplicate_check_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_decision", sa.String(32), nullable=False),
        sa.Column("system_classification", sa.String(32), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "user_decision IN ('USE_EXISTING', 'CREATE_NEW', 'NOT_DUPLICATE')",
            name="ck_duplicate_feedback_decision",
        ),
        sa.ForeignKeyConstraint(["duplicate_check_id"], ["duplicate_checks.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["duplicate_candidates.id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
        sa.UniqueConstraint("duplicate_check_id"),
    )
    op.create_index(
        "ix_duplicate_feedback_duplicate_check_id",
        "duplicate_feedback",
        ["duplicate_check_id"],
        unique=True,
    )
    op.create_index("ix_duplicate_feedback_candidate_id", "duplicate_feedback", ["candidate_id"])
    op.create_index("ix_duplicate_feedback_user_decision", "duplicate_feedback", ["user_decision"])


def downgrade() -> None:
    op.drop_table("duplicate_feedback")
