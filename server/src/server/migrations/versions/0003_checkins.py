"""Add reliable check-in workflow state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_checkins"
down_revision = "0002_parts_revisions_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifacts", sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.create_table(
        "checkins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_checkin_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('UPLOADING', 'VALIDATING', 'COMPLETED', 'FAILED')",
            name="ck_checkins_state",
        ),
    )
    op.create_index("ix_checkins_state", "checkins", ["state"])


def downgrade() -> None:
    op.drop_table("checkins")
    op.drop_column("artifacts", "required")
