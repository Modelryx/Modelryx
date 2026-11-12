"""Add Phase 6 deterministic validation and AI explanation persistence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_phase6_validation"
down_revision = "0035_phase56_explanations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "validation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "staging_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("staging_sessions.id"),
            nullable=False,
        ),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("summary_json", postgresql.JSONB(), nullable=False),
        sa.Column("validator_states_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("staging_id", "run_number", name="uq_validation_runs_staging_number"),
        sa.CheckConstraint(
            "status IN ('READY', 'READY_WITH_WARNINGS', 'BLOCKED', 'ERROR')",
            name="ck_validation_runs_status",
        ),
    )
    op.create_index("ix_validation_runs_staging_id", "validation_runs", ["staging_id"])
    op.create_index("ix_validation_runs_status", "validation_runs", ["status"])
    op.create_index("ix_validation_runs_report_hash", "validation_runs", ["report_hash"])
    op.create_table(
        "validation_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "validation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("validation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source_json", postgresql.JSONB(), nullable=False),
        sa.Column("location_json", postgresql.JSONB(), nullable=False),
        sa.Column("details_json", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False),
        sa.Column("actions_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'ERROR', 'CRITICAL')",
            name="ck_validation_findings_severity",
        ),
        sa.CheckConstraint(
            "state IN ('ACTIVE', 'RESOLVED', 'IGNORED', 'SUPERSEDED')",
            name="ck_validation_findings_state",
        ),
    )
    for column in ("validation_run_id", "code", "category", "severity", "blocking", "state"):
        op.create_index(f"ix_validation_findings_{column}", "validation_findings", [column])
    op.create_table(
        "validation_finding_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("validation_findings.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("comment", sa.String(500)),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('ACKNOWLEDGED')", name="ck_validation_finding_actions_action"
        ),
    )
    op.create_index(
        "ix_validation_finding_actions_finding_id", "validation_finding_actions", ["finding_id"]
    )
    op.create_table(
        "validation_ai_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "validation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("validation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("model", sa.String(255)),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False, unique=True),
        sa.Column("output_json", postgresql.JSONB(), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('AI', 'DETERMINISTIC')", name="ck_validation_ai_mode"),
    )
    op.create_index(
        "ix_validation_ai_analyses_validation_run_id",
        "validation_ai_analyses",
        ["validation_run_id"],
    )
    op.create_index("ix_validation_ai_analyses_cache_key", "validation_ai_analyses", ["cache_key"])


def downgrade() -> None:
    op.drop_table("validation_ai_analyses")
    op.drop_table("validation_finding_actions")
    op.drop_table("validation_findings")
    op.drop_table("validation_runs")
