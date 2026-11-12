"""Add privacy-aware search correction feedback.

Revision ID: 0064_search_feedback
Revises: 0063_feature_search_counts
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0064_search_feedback"
down_revision = "0063_feature_search_counts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_search_feedback",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.Column("interpretation_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('incorrect_interpretation', 'missing_result', "
            "'irrelevant_result', 'other')",
            name="ck_ai_search_feedback_category",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["session_id"], ["ai_search_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_search_feedback_session_id", "ai_search_feedback", ["session_id"])
    op.create_index("ix_ai_search_feedback_created_by", "ai_search_feedback", ["created_by"])
    op.create_index("ix_ai_search_feedback_category", "ai_search_feedback", ["category"])


def downgrade() -> None:
    op.drop_index("ix_ai_search_feedback_category", table_name="ai_search_feedback")
    op.drop_index("ix_ai_search_feedback_created_by", table_name="ai_search_feedback")
    op.drop_index("ix_ai_search_feedback_session_id", table_name="ai_search_feedback")
    op.drop_table("ai_search_feedback")
