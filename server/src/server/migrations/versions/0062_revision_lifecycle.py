"""Add explicit ItemRevision lifecycle state.

Revision ID: 0062_revision_lifecycle
Revises: 0061_retire_legacy_content
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0062_revision_lifecycle"
down_revision = "0061_retire_legacy_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "revisions",
        sa.Column("lifecycle_state", sa.String(24), nullable=False, server_default="WORKING"),
    )
    op.add_column(
        "revisions", sa.Column("released_by", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("revisions", sa.Column("released_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_revisions_released_by", "revisions", "users", ["released_by"], ["id"]
    )
    op.create_index("ix_revisions_lifecycle_state", "revisions", ["lifecycle_state"])
    op.create_check_constraint(
        "ck_revisions_lifecycle_state",
        "revisions",
        "lifecycle_state IN ('WORKING', 'RELEASED', 'SUPERSEDED', 'OBSOLETE')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_revisions_lifecycle_state", "revisions", type_="check")
    op.drop_index("ix_revisions_lifecycle_state", table_name="revisions")
    op.drop_constraint("fk_revisions_released_by", "revisions", type_="foreignkey")
    op.drop_column("revisions", "released_at")
    op.drop_column("revisions", "released_by")
    op.drop_column("revisions", "lifecycle_state")
