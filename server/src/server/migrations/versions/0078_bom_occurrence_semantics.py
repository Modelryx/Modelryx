"""Persist assembly occurrence placement and display semantics.

Revision ID: 0078_bom_occurrence_semantics
Revises: 0077_worker_capabilities
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0078_bom_occurrence_semantics"
down_revision = "0077_worker_capabilities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    identity = "[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]"
    op.add_column(
        "bom_lines",
        sa.Column("source_reference", sa.String(500), nullable=False, server_default=""),
    )
    op.add_column("bom_lines", sa.Column("reference_designator", sa.String(255)))
    op.add_column(
        "bom_lines",
        sa.Column(
            "transform",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(f"'{identity}'::jsonb"),
        ),
    )
    op.add_column(
        "bom_lines",
        sa.Column("is_suppressed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "bom_lines",
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "bom_lines",
        sa.Column(
            "properties_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("bom_lines", "properties_json")
    op.drop_column("bom_lines", "is_visible")
    op.drop_column("bom_lines", "is_suppressed")
    op.drop_column("bom_lines", "transform")
    op.drop_column("bom_lines", "reference_designator")
    op.drop_column("bom_lines", "source_reference")
