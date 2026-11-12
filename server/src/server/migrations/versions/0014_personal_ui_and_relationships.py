"""Add personal UI records and revision relationships."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_personal_ui"
down_revision = "0013_attachment_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "favorites",
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True
        ),
        sa.Column(
            "item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parts.id"), primary_key=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "recently_viewed_items",
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True
        ),
        sa.Column(
            "item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parts.id"), primary_key=True
        ),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recently_viewed_items_viewed_at", "recently_viewed_items", ["viewed_at"])
    op.create_table(
        "revision_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "target_revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("revisions.id")
        ),
        sa.Column("target_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parts.id")),
        sa.Column("relationship_type", sa.String(32), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "relationship_type IN "
            "('SPECIFICATION', 'MATERIAL_SPEC', 'REFERENCE', 'TEST_PROCEDURE')",
            name="ck_revision_relationships_type",
        ),
        sa.CheckConstraint(
            "target_revision_id IS NOT NULL OR target_item_id IS NOT NULL",
            name="ck_revision_relationships_target",
        ),
        sa.UniqueConstraint(
            "source_revision_id",
            "target_revision_id",
            "target_item_id",
            "relationship_type",
            name="uq_revision_relationships_target_type",
        ),
    )
    op.create_index(
        "ix_revision_relationships_source_revision_id",
        "revision_relationships",
        ["source_revision_id"],
    )
    op.create_index(
        "ix_revision_relationships_target_revision_id",
        "revision_relationships",
        ["target_revision_id"],
    )
    op.create_index(
        "ix_revision_relationships_target_item_id", "revision_relationships", ["target_item_id"]
    )


def downgrade() -> None:
    op.drop_table("revision_relationships")
    op.drop_index("ix_recently_viewed_items_viewed_at", table_name="recently_viewed_items")
    op.drop_table("recently_viewed_items")
    op.drop_table("favorites")
