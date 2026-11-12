"""Enforce case-insensitive Item number uniqueness."""

import sqlalchemy as sa
from alembic import op

revision = "0054_item_number_casefold"
down_revision = "0053_validation_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_parts_part_number_casefold",
        "parts",
        [sa.text("lower(part_number)")],
        unique=True,
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index("uq_parts_part_number_casefold", table_name="parts")
