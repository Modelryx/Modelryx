"""Add derived and indexed metadata search fields."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_metadata_search_fields"
down_revision = "0020_metadata_extractions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = (
        sa.Column("derived_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("kind", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("material", sa.String(255), nullable=True),
        sa.Column("bbox_x_mm", sa.Float(), nullable=False, server_default="0"),
        sa.Column("bbox_y_mm", sa.Float(), nullable=False, server_default="0"),
        sa.Column("bbox_z_mm", sa.Float(), nullable=False, server_default="0"),
        sa.Column("bbox_min_mm", sa.Float(), nullable=False, server_default="0"),
        sa.Column("bbox_mid_mm", sa.Float(), nullable=False, server_default="0"),
        sa.Column("bbox_max_mm", sa.Float(), nullable=False, server_default="0"),
        sa.Column("volume_mm3", sa.Float(), nullable=False, server_default="0"),
        sa.Column("surface_area_mm2", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mass_kg", sa.Float(), nullable=True),
        sa.Column("solid_count", sa.Integer(), nullable=True),
        sa.Column("face_count", sa.Integer(), nullable=True),
        sa.Column("edge_count", sa.Integer(), nullable=True),
        sa.Column("hole_count", sa.Integer(), nullable=True),
    )
    for column in columns:
        op.add_column("metadata_extractions", column)
    op.create_index("ix_metadata_extractions_kind", "metadata_extractions", ["kind"])
    op.create_index("ix_metadata_extractions_material", "metadata_extractions", ["material"])
    op.create_index("ix_metadata_extractions_bbox_max_mm", "metadata_extractions", ["bbox_max_mm"])
    op.create_index("ix_metadata_extractions_volume_mm3", "metadata_extractions", ["volume_mm3"])
    op.add_column(
        "revisions",
        sa.Column("current_metadata_extraction_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_revisions_current_metadata_extraction",
        "revisions",
        "metadata_extractions",
        ["current_metadata_extraction_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_revisions_current_metadata_extraction", "revisions", type_="foreignkey")
    op.drop_column("revisions", "current_metadata_extraction_id")
    for name in ("volume_mm3", "bbox_max_mm", "material", "kind"):
        op.drop_index(f"ix_metadata_extractions_{name}", table_name="metadata_extractions")
    for name in (
        "hole_count",
        "edge_count",
        "face_count",
        "solid_count",
        "mass_kg",
        "surface_area_mm2",
        "volume_mm3",
        "bbox_max_mm",
        "bbox_mid_mm",
        "bbox_min_mm",
        "bbox_z_mm",
        "bbox_y_mm",
        "bbox_x_mm",
        "material",
        "kind",
        "derived_json",
    ):
        op.drop_column("metadata_extractions", name)
