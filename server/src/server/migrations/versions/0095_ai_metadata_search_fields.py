"""Add indexed metadata fields used by engineering and AI search."""

from alembic import op
import sqlalchemy as sa

revision = "0095_ai_metadata_search"
down_revision = "0094_scope_bom_occurrence"
branch_labels = None
depends_on = None

FIELDS = (
    ("density_kg_m3", sa.Float()),
    ("hole_diameter_min_mm", sa.Float()),
    ("hole_diameter_max_mm", sa.Float()),
    ("drawing_count", sa.Integer()),
    ("tolerance_count", sa.Integer()),
    ("instance_count", sa.Integer()),
)


def upgrade() -> None:
    for name, type_ in FIELDS:
        op.add_column("metadata_extractions", sa.Column(name, type_, nullable=True))
        op.create_index(f"ix_metadata_extractions_{name}", "metadata_extractions", [name])


def downgrade() -> None:
    for name, _ in reversed(FIELDS):
        op.drop_index(f"ix_metadata_extractions_{name}", table_name="metadata_extractions")
        op.drop_column("metadata_extractions", name)
