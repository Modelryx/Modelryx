"""Scope BOM occurrence keys to their immediate parent assembly."""

from alembic import op

revision = "0094_scope_bom_occurrence"
down_revision = "0093_generic_cad_workers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_bom_lines_occurrence", "bom_lines", type_="unique")
    op.create_unique_constraint(
        "uq_bom_lines_occurrence",
        "bom_lines",
        ["bom_view_revision_id", "parent_revision_id", "occurrence_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_bom_lines_occurrence", "bom_lines", type_="unique")
    op.create_unique_constraint(
        "uq_bom_lines_occurrence",
        "bom_lines",
        ["bom_view_revision_id", "occurrence_key"],
    )
