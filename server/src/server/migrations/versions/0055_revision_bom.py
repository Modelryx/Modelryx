"""Add revision-owned BOM views and resolved child lines."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0055_revision_bom"
down_revision = "0054_item_number_casefold"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bom_view_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_bom_view_revisions_sequence"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["revisions.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["revision_content_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "sequence", name="uq_bom_view_revisions_sequence"),
        sa.UniqueConstraint("snapshot_id"),
    )
    op.create_index("ix_bom_view_revisions_revision_id", "bom_view_revisions", ["revision_id"])
    op.create_table(
        "bom_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bom_view_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurrence_key", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("sequence", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_bom_lines_quantity"),
        sa.CheckConstraint("sequence >= 0", name="ck_bom_lines_sequence"),
        sa.ForeignKeyConstraint(
            ["bom_view_revision_id"], ["bom_view_revisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["revisions.id"]),
        sa.ForeignKeyConstraint(["child_revision_id"], ["revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bom_view_revision_id", "occurrence_key", name="uq_bom_lines_occurrence"
        ),
    )
    op.create_index("ix_bom_lines_bom_view_revision_id", "bom_lines", ["bom_view_revision_id"])
    op.create_index("ix_bom_lines_child_revision_id", "bom_lines", ["child_revision_id"])
    op.create_index("ix_bom_lines_parent_revision_id", "bom_lines", ["parent_revision_id"])


def downgrade() -> None:
    op.drop_table("bom_lines")
    op.drop_table("bom_view_revisions")
