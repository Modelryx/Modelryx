"""Add attachment upload lifecycle timestamps and terminal states."""

import sqlalchemy as sa
from alembic import op

revision = "0013_attachment_lifecycle"
down_revision = "0012_attachment_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attachment_versions", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "attachment_versions", sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_attachment_versions_expires_at", "attachment_versions", ["expires_at"])
    op.drop_constraint("ck_attachment_versions_state", "attachment_versions", type_="check")
    op.create_check_constraint(
        "ck_attachment_versions_state",
        "attachment_versions",
        "state IN ('UPLOADING', 'READY', 'FAILED', 'CANCELLED', 'EXPIRED')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_attachment_versions_state", "attachment_versions", type_="check")
    op.create_check_constraint(
        "ck_attachment_versions_state",
        "attachment_versions",
        "state IN ('UPLOADING', 'READY', 'FAILED')",
    )
    op.drop_index("ix_attachment_versions_expires_at", table_name="attachment_versions")
    op.drop_column("attachment_versions", "ready_at")
    op.drop_column("attachment_versions", "expires_at")
