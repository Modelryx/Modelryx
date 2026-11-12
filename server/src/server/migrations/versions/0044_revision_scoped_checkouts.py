"""Scope checkout reservations to Item Revisions instead of Items."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_revision_checkouts"
down_revision = "0043_source_version_width"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "checkouts",
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE checkouts AS c SET revision_id = w.revision_id "
        "FROM working_versions AS w WHERE w.id = c.base_working_version_id"
    )
    op.drop_index("ix_checkouts_part_id", table_name="checkouts")
    op.create_index("ix_checkouts_part_id", "checkouts", ["part_id"], unique=False)
    op.create_index("ix_checkouts_revision_id", "checkouts", ["revision_id"], unique=True)
    op.create_index(
        "uq_checkouts_unversioned_item",
        "checkouts",
        ["part_id"],
        unique=True,
        postgresql_where=sa.text("revision_id IS NULL"),
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM checkouts AS newer USING checkouts AS older "
        "WHERE newer.part_id = older.part_id AND newer.acquired_at > older.acquired_at"
    )
    op.drop_index("uq_checkouts_unversioned_item", table_name="checkouts")
    op.drop_index("ix_checkouts_revision_id", table_name="checkouts")
    op.drop_index("ix_checkouts_part_id", table_name="checkouts")
    op.create_index("ix_checkouts_part_id", "checkouts", ["part_id"], unique=True)
    op.drop_column("checkouts", "revision_id")
