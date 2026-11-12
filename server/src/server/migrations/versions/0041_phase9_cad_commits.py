"""Add second-confirmation CAD result commit plans."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0041_phase9_cad_commits"
down_revision = "0040_phase9_cad_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cad_commit_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cad_sessions.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision_code", sa.String(50), nullable=False),
        sa.Column("state_token", sa.String(64), nullable=False),
        sa.Column("preview_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "requested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("idempotency_key", sa.String(255), unique=True),
        sa.Column(
            "committed_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            unique=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING', 'COMMITTED', 'CANCELLED', 'EXPIRED', 'STALE', 'FAILED')",
            name="ck_cad_commit_plans_status",
        ),
    )
    for column in ("session_id", "status", "state_token", "idempotency_key", "expires_at"):
        op.create_index(f"ix_cad_commit_plans_{column}", "cad_commit_plans", [column])


def downgrade() -> None:
    op.drop_table("cad_commit_plans")
