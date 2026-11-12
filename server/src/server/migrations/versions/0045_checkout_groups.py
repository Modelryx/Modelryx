"""Persist single and structure checkout scope snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_checkout_groups"
down_revision = "0044_revision_checkouts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "checkouts",
        sa.Column("checkout_group_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "checkouts",
        sa.Column(
            "root_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "checkouts",
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="SINGLE"),
    )
    op.execute("UPDATE checkouts SET checkout_group_id = id, root_revision_id = revision_id")
    op.alter_column("checkouts", "checkout_group_id", nullable=False)
    op.create_index(
        "ix_checkouts_checkout_group_id", "checkouts", ["checkout_group_id"], unique=False
    )
    op.create_index(
        "ix_checkouts_root_revision_id", "checkouts", ["root_revision_id"], unique=False
    )
    op.create_check_constraint(
        "ck_checkouts_scope", "checkouts", "scope IN ('SINGLE', 'STRUCTURE')"
    )
    op.alter_column("checkouts", "scope", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_checkouts_scope", "checkouts", type_="check")
    op.drop_index("ix_checkouts_root_revision_id", table_name="checkouts")
    op.drop_index("ix_checkouts_checkout_group_id", table_name="checkouts")
    op.drop_column("checkouts", "scope")
    op.drop_column("checkouts", "root_revision_id")
    op.drop_column("checkouts", "checkout_group_id")
