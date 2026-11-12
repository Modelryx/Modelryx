"""Add attachment-version idempotency keys."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_attachment_idempotency"
down_revision = "0011_item_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attachment_versions",
        sa.Column("client_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_attachment_versions_client_version_id",
        "attachment_versions",
        ["client_version_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_attachment_versions_client_version_id", table_name="attachment_versions")
    op.drop_column("attachment_versions", "client_version_id")
