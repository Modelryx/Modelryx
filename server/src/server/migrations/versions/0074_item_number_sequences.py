"""Add server-managed Item-number sequences.

Revision ID: 0074_item_number_sequences
Revises: 0073_worker_heartbeats
"""

import sqlalchemy as sa
from alembic import op

revision = "0074_item_number_sequences"
down_revision = "0073_worker_heartbeats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table(
        "item_number_sequences",
        sa.Column("item_type", sa.String(32), primary_key=True),
        sa.Column("next_value", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "item_type IN ('PART', 'ASSEMBLY')", name="ck_item_number_sequences_type"
        ),
        sa.CheckConstraint("next_value > 0", name="ck_item_number_sequences_positive"),
    )
    op.bulk_insert(table, [
        {"item_type": "PART", "next_value": 1},
        {"item_type": "ASSEMBLY", "next_value": 1},
    ])


def downgrade() -> None:
    op.drop_table("item_number_sequences")
