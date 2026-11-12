"""Add Phase 8 controlled AI action proposals and executions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0037_phase8_ai_actions"
down_revision = "0036_phase6_validation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_action_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("tool_version", sa.String(32), nullable=False),
        sa.Column("risk", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("arguments_json", postgresql.JSONB(), nullable=False),
        sa.Column("preview_json", postgresql.JSONB(), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "requested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "risk IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')", name="ck_ai_action_proposals_risk"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'EXECUTED', 'CANCELLED', "
            "'EXPIRED', 'STALE', 'FAILED')",
            name="ck_ai_action_proposals_status",
        ),
    )
    for column in ("tool_name", "status", "target_id", "expires_at"):
        op.create_index(f"ix_ai_action_proposals_{column}", "ai_action_proposals", [column])
    op.create_table(
        "ai_action_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "proposal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_action_proposals.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), nullable=False),
        sa.Column("error_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "executed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED')", name="ck_ai_action_executions_status"
        ),
    )
    for column in ("proposal_id", "idempotency_key", "status"):
        op.create_index(f"ix_ai_action_executions_{column}", "ai_action_executions", [column])


def downgrade() -> None:
    op.drop_table("ai_action_executions")
    op.drop_table("ai_action_proposals")
