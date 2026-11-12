"""Add independently versioned Item attachments."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_item_attachments"
down_revision = "0010_download_bundles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "part_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parts.id"), nullable=False
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("attachment_type", sa.String(32), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attachment_type IN ('DRAWING', 'DOCUMENT', 'SPECIFICATION', 'IMAGE', "
            "'SPREADSHEET', 'CERTIFICATE', 'OTHER')",
            name="ck_attachments_type",
        ),
    )
    op.create_index("ix_attachments_part_id", "attachments", ["part_id"])
    op.create_index("ix_attachments_attachment_type", "attachments", ["attachment_type"])
    op.create_table(
        "attachment_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attachment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attachments.id"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('UPLOADING', 'READY', 'FAILED')", name="ck_attachment_versions_state"
        ),
        sa.UniqueConstraint(
            "attachment_id", "version_number", name="uq_attachment_versions_number"
        ),
    )
    op.create_index(
        "ix_attachment_versions_attachment_id", "attachment_versions", ["attachment_id"]
    )
    op.create_index("ix_attachment_versions_state", "attachment_versions", ["state"])


def downgrade() -> None:
    op.drop_table("attachment_versions")
    op.drop_table("attachments")
