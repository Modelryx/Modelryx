"""Add staged Item-level Additional File workflow and archive state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0084_additional_file_staging"
down_revision = "0083_additional_file_blobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column("lifecycle_state", sa.String(16), nullable=False, server_default="ACTIVE"),
    )
    op.create_index("ix_attachments_lifecycle_state", "attachments", ["lifecycle_state"])
    op.create_check_constraint(
        "ck_attachments_lifecycle", "attachments", "lifecycle_state IN ('ACTIVE', 'ARCHIVED')"
    )
    op.create_table(
        "additional_file_stages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_staging_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("base_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content_blob_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("duplicate_action", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("attachment_type", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("commit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("result_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action IN ('CREATE', 'NEW_VERSION')", name="ck_additional_file_stage_action"),
        sa.CheckConstraint(
            "duplicate_action IN ('UPLOAD', 'REUSE_EXISTING')",
            name="ck_additional_file_stage_duplicate_action",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'READY', 'COMPLETED', 'CANCELLED')",
            name="ck_additional_file_stage_status",
        ),
        sa.CheckConstraint("byte_size > 0", name="ck_additional_file_stage_byte_size"),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"]),
        sa.ForeignKeyConstraint(["base_version_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["content_blob_id"], ["content_blobs.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["parts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_staging_id"),
        sa.UniqueConstraint("commit_id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_additional_file_stages_item_id", "additional_file_stages", ["item_id"])
    op.create_index("ix_additional_file_stages_attachment_id", "additional_file_stages", ["attachment_id"])
    op.create_index("ix_additional_file_stages_status", "additional_file_stages", ["status"])
    op.create_index("ix_additional_file_stages_sha256", "additional_file_stages", ["sha256"])


def downgrade() -> None:
    op.drop_table("additional_file_stages")
    op.drop_constraint("ck_attachments_lifecycle", "attachments", type_="check")
    op.drop_index("ix_attachments_lifecycle_state", table_name="attachments")
    op.drop_column("attachments", "lifecycle_state")
