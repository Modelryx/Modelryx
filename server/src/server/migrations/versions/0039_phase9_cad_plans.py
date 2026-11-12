"""Add immutable Phase 9 CAD plans and queued executions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0039_phase9_cad_plans"
down_revision = "0038_phase9_cad_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cad_operation_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cad_sessions.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("risk", sa.String(16), nullable=False),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("parameter", sa.String(100), nullable=False),
        sa.Column("expected_value_json", postgresql.JSONB(), nullable=False),
        sa.Column("new_value_json", postgresql.JSONB(), nullable=False),
        sa.Column("unit", sa.String(16)),
        sa.Column("preview_json", postgresql.JSONB(), nullable=False),
        sa.Column("state_token", sa.String(64), nullable=False),
        sa.Column(
            "requested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'EXECUTING', 'SUCCEEDED', "
            "'FAILED', 'CANCELLED', 'EXPIRED', 'STALE')",
            name="ck_cad_operation_plans_status",
        ),
        sa.CheckConstraint("risk IN ('MEDIUM')", name="ck_cad_operation_plans_risk"),
        sa.CheckConstraint(
            "operation IN ('feature.parameter.set_value', 'sketch.constraint.set_value', "
            "'material.set')",
            name="ck_cad_operation_plans_operation",
        ),
    )
    for column in ("session_id", "status", "state_token", "expires_at"):
        op.create_index(f"ix_cad_operation_plans_{column}", "cad_operation_plans", [column])
    op.create_table(
        "cad_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cad_operation_plans.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("result_json", postgresql.JSONB(), nullable=False),
        sa.Column("error_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_cad_executions_status",
        ),
    )
    for column in ("plan_id", "status", "idempotency_key"):
        op.create_index(f"ix_cad_executions_{column}", "cad_executions", [column])


def downgrade() -> None:
    op.drop_table("cad_executions")
    op.drop_table("cad_operation_plans")
