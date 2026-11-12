"""Store personal and administrator-published Library views."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0097_ui_saved_views"
down_revision = "0096_bundle_revision_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ui_saved_views",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("settings_json", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "owner_id", "entity_type", "scope", "name", name="uq_ui_saved_view_owner"
        ),
    )
    op.create_index("ix_ui_saved_views_scope_entity", "ui_saved_views", ["scope", "entity_type"])
    op.create_index(
        "uq_ui_saved_view_shared_name", "ui_saved_views", ["entity_type", "name"],
        unique=True, postgresql_where=sa.text("scope = 'SHARED'"),
    )


def downgrade() -> None:
    op.drop_index("uq_ui_saved_view_shared_name", table_name="ui_saved_views")
    op.drop_index("ix_ui_saved_views_scope_entity", table_name="ui_saved_views")
    op.drop_table("ui_saved_views")
