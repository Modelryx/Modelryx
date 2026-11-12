"""Phase 2.6 conversational search sessions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031_phase26_search_sessions"
down_revision = "0030_phase22_text_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_search_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("current_request_json", postgresql.JSONB(), nullable=False),
        sa.Column("history_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_search_sessions_created_by", "ai_search_sessions", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_ai_search_sessions_created_by", table_name="ai_search_sessions")
    op.drop_table("ai_search_sessions")
