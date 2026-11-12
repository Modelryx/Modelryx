"""Replace fixed Item-number sequences with administrator-managed templates.

Revision ID: 0075_item_number_templates
Revises: 0074_item_number_sequences
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0075_item_number_templates"
down_revision = "0074_item_number_sequences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_number_templates",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("item_type", sa.String(32), nullable=False),
        sa.Column("pattern", sa.String(100), nullable=False),
        sa.Column("next_value", sa.BigInteger(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "item_type IN ('PART', 'ASSEMBLY')", name="ck_item_number_templates_type"
        ),
        sa.CheckConstraint("next_value > 0", name="ck_item_number_templates_positive"),
    )
    op.create_index("ix_item_number_templates_item_type", "item_number_templates", ["item_type"])
    op.create_index(
        "uq_item_number_templates_selected_type",
        "item_number_templates",
        ["item_type"],
        unique=True,
        postgresql_where=sa.text("is_selected"),
    )
    connection = op.get_bind()
    sequences = {
        row.item_type: row.next_value
        for row in connection.execute(
            sa.text("SELECT item_type, next_value FROM item_number_sequences")
        )
    }
    table = sa.table(
        "item_number_templates",
        sa.column("id", sa.UUID()), sa.column("name", sa.String()),
        sa.column("item_type", sa.String()), sa.column("pattern", sa.String()),
        sa.column("next_value", sa.BigInteger()), sa.column("is_enabled", sa.Boolean()),
        sa.column("is_selected", sa.Boolean()),
    )
    op.bulk_insert(table, [
        {
            "id": uuid.uuid4(), "name": "Default part numbering", "item_type": "PART",
            "pattern": "PART-{sequence:06d}", "next_value": sequences.get("PART", 1),
            "is_enabled": True, "is_selected": True,
        },
        {
            "id": uuid.uuid4(), "name": "Default assembly numbering", "item_type": "ASSEMBLY",
            "pattern": "ASM-{sequence:06d}", "next_value": sequences.get("ASSEMBLY", 1),
            "is_enabled": True, "is_selected": True,
        },
    ])
    op.drop_table("item_number_sequences")


def downgrade() -> None:
    sequence_table = op.create_table(
        "item_number_sequences",
        sa.Column("item_type", sa.String(32), primary_key=True),
        sa.Column("next_value", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "item_type IN ('PART', 'ASSEMBLY')", name="ck_item_number_sequences_type"
        ),
        sa.CheckConstraint("next_value > 0", name="ck_item_number_sequences_positive"),
    )
    connection = op.get_bind()
    values = {
        row.item_type: row.next_value
        for row in connection.execute(sa.text(
            "SELECT item_type, next_value FROM item_number_templates WHERE is_selected"
        ))
    }
    op.bulk_insert(sequence_table, [
        {"item_type": "PART", "next_value": values.get("PART", 1)},
        {"item_type": "ASSEMBLY", "next_value": values.get("ASSEMBLY", 1)},
    ])
    op.drop_index("uq_item_number_templates_selected_type", table_name="item_number_templates")
    op.drop_index("ix_item_number_templates_item_type", table_name="item_number_templates")
    op.drop_table("item_number_templates")
