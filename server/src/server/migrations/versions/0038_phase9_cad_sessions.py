"""Add isolated Phase 9 CAD sessions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038_phase9_cad_sessions"
down_revision = "0037_phase8_ai_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cad_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "source_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("worker_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("workspace_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("error_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('CREATED', 'OPENING', 'READY', 'EXECUTING', 'VERIFYING', "
            "'READY_TO_REVIEW', 'COMMITTED', 'FAILED', 'DISCARDED', 'CANCELLED', 'EXPIRED')",
            name="ck_cad_sessions_status",
        ),
    )
    for column in (
        "revision_id",
        "source_artifact_id",
        "owner_id",
        "worker_id",
        "status",
        "expires_at",
    ):
        op.create_index(f"ix_cad_sessions_{column}", "cad_sessions", [column])
    op.create_index(
        "uq_cad_sessions_active_revision",
        "cad_sessions",
        ["revision_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('CREATED', 'OPENING', 'READY', 'EXECUTING', 'VERIFYING', 'READY_TO_REVIEW')"
        ),
    )


def downgrade() -> None:
    op.drop_table("cad_sessions")
