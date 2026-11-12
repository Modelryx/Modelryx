"""Add indexed feature counts for reliable presence search.

Revision ID: 0063_feature_search_counts
Revises: 0062_revision_lifecycle
"""

import sqlalchemy as sa
from alembic import op

revision = "0063_feature_search_counts"
down_revision = "0062_revision_lifecycle"
branch_labels = None
depends_on = None


FEATURES = (
    "fillet",
    "chamfer",
    "extrusion",
    "revolution",
    "pocket",
    "pattern",
    "thread",
)


def upgrade() -> None:
    for feature in FEATURES:
        column = f"{feature}_count"
        op.add_column("metadata_extractions", sa.Column(column, sa.Integer(), nullable=True))
        op.create_index(f"ix_metadata_extractions_{column}", "metadata_extractions", [column])
    op.create_index("ix_metadata_extractions_hole_count", "metadata_extractions", ["hole_count"])


def downgrade() -> None:
    op.drop_index("ix_metadata_extractions_hole_count", table_name="metadata_extractions")
    for feature in reversed(FEATURES):
        column = f"{feature}_count"
        op.drop_index(f"ix_metadata_extractions_{column}", table_name="metadata_extractions")
        op.drop_column("metadata_extractions", column)
