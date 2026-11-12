"""Store Part and Assembly patterns together in each numbering template.

Revision ID: 0076_paired_number_templates
Revises: 0075_item_number_templates
"""

import sqlalchemy as sa
from alembic import op

revision = "0076_paired_number_templates"
down_revision = "0075_item_number_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_item_number_templates_selected_type", table_name="item_number_templates")
    op.add_column("item_number_templates", sa.Column("part_pattern", sa.String(100)))
    op.add_column("item_number_templates", sa.Column("assembly_pattern", sa.String(100)))
    op.add_column("item_number_templates", sa.Column("part_next_value", sa.BigInteger()))
    op.add_column("item_number_templates", sa.Column("assembly_next_value", sa.BigInteger()))
    connection = op.get_bind()
    connection.execute(sa.text("""
        UPDATE item_number_templates SET
          part_pattern = CASE WHEN item_type = 'PART' THEN pattern
                              ELSE 'PART-{sequence:06d}' END,
          assembly_pattern = CASE WHEN item_type = 'ASSEMBLY' THEN pattern
                                  ELSE 'ASM-{sequence:06d}' END,
          part_next_value = CASE WHEN item_type = 'PART' THEN next_value ELSE 1 END,
          assembly_next_value = CASE WHEN item_type = 'ASSEMBLY' THEN next_value ELSE 1 END
    """))
    selected_id = connection.execute(sa.text("""
        SELECT id FROM item_number_templates
        ORDER BY CASE WHEN is_selected THEN 0 ELSE 1 END,
                 CASE WHEN item_type = 'PART' THEN 0 ELSE 1 END, created_at, id
        LIMIT 1
    """)).scalar()
    connection.execute(
        sa.text("UPDATE item_number_templates SET is_selected = (id = :selected_id)"),
        {"selected_id": selected_id},
    )
    for column in (
        "part_pattern", "assembly_pattern", "part_next_value", "assembly_next_value"
    ):
        op.alter_column("item_number_templates", column, nullable=False)
    op.drop_constraint("ck_item_number_templates_type", "item_number_templates", type_="check")
    op.drop_constraint("ck_item_number_templates_positive", "item_number_templates", type_="check")
    op.drop_index("ix_item_number_templates_item_type", table_name="item_number_templates")
    op.drop_column("item_number_templates", "item_type")
    op.drop_column("item_number_templates", "pattern")
    op.drop_column("item_number_templates", "next_value")
    op.create_check_constraint(
        "ck_item_number_templates_part_positive", "item_number_templates",
        "part_next_value > 0",
    )
    op.create_check_constraint(
        "ck_item_number_templates_assembly_positive", "item_number_templates",
        "assembly_next_value > 0",
    )
    op.create_index(
        "uq_item_number_templates_selected", "item_number_templates", [sa.text("(1)")],
        unique=True, postgresql_where=sa.text("is_selected"),
    )


def downgrade() -> None:
    op.drop_index("uq_item_number_templates_selected", table_name="item_number_templates")
    op.add_column("item_number_templates", sa.Column("item_type", sa.String(32)))
    op.add_column("item_number_templates", sa.Column("pattern", sa.String(100)))
    op.add_column("item_number_templates", sa.Column("next_value", sa.BigInteger()))
    op.execute("""
        UPDATE item_number_templates SET item_type='PART', pattern=part_pattern,
          next_value=part_next_value
    """)
    op.alter_column("item_number_templates", "item_type", nullable=False)
    op.alter_column("item_number_templates", "pattern", nullable=False)
    op.alter_column("item_number_templates", "next_value", nullable=False)
    op.create_check_constraint(
        "ck_item_number_templates_type", "item_number_templates",
        "item_type IN ('PART', 'ASSEMBLY')",
    )
    op.create_check_constraint(
        "ck_item_number_templates_positive", "item_number_templates", "next_value > 0"
    )
    op.create_index(
        "ix_item_number_templates_item_type", "item_number_templates", ["item_type"]
    )
    op.create_index(
        "uq_item_number_templates_selected_type", "item_number_templates", ["item_type"],
        unique=True, postgresql_where=sa.text("is_selected"),
    )
    op.drop_constraint(
        "ck_item_number_templates_part_positive", "item_number_templates", type_="check"
    )
    op.drop_constraint(
        "ck_item_number_templates_assembly_positive", "item_number_templates", type_="check"
    )
    op.drop_column("item_number_templates", "part_pattern")
    op.drop_column("item_number_templates", "assembly_pattern")
    op.drop_column("item_number_templates", "part_next_value")
    op.drop_column("item_number_templates", "assembly_next_value")
