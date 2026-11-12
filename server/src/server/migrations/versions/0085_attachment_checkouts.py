"""Add independent attachment edit checkouts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0085_attachment_checkouts"
down_revision = "0084_additional_file_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachment_checkouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("base_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"]),
        sa.ForeignKeyConstraint(["base_version_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["parts.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attachment_id"),
    )
    op.create_index("ix_attachment_checkouts_item_id", "attachment_checkouts", ["item_id"])
    op.create_index(
        "ix_attachment_checkouts_attachment_id", "attachment_checkouts", ["attachment_id"]
    )
    op.create_index("ix_attachment_checkouts_expires_at", "attachment_checkouts", ["expires_at"])
    op.create_index(
        "uq_attachment_checkouts_item_create",
        "attachment_checkouts",
        ["item_id"],
        unique=True,
        postgresql_where=sa.text("attachment_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("attachment_checkouts")
