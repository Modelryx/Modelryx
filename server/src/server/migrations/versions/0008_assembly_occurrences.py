"""Add immutable assembly occurrence relationships."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_assembly_occurrences"
down_revision = "0007_assembly_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assembly_checkins",
        sa.Column("base_main_version_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_assembly_checkins_base_main_version",
        "assembly_checkins",
        "working_versions",
        ["base_main_version_id"],
        ["id"],
    )
    op.create_table(
        "assembly_occurrences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "assembly_checkin_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assembly_checkins.id"),
            nullable=False,
        ),
        sa.Column(
            "parent_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("working_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "child_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("working_versions.id"),
            nullable=False,
        ),
        sa.Column("parent_occurrence_id", postgresql.UUID(as_uuid=True)),
        sa.Column("occurrence_key", sa.String(255), nullable=False),
        sa.Column("source_reference", sa.String(1024), nullable=False),
        sa.Column("reference_designator", sa.String(255)),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("transform_json", postgresql.JSONB(), nullable=False),
        sa.Column("is_suppressed", sa.Boolean(), nullable=False),
        sa.Column("is_visible", sa.Boolean(), nullable=False),
        sa.Column("properties_json", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_assembly_occurrences_quantity"),
        sa.UniqueConstraint(
            "parent_version_id",
            "occurrence_key",
            name="uq_assembly_occurrences_parent_key",
        ),
    )
    op.create_foreign_key(
        "fk_assembly_occurrences_parent",
        "assembly_occurrences",
        "assembly_occurrences",
        ["parent_occurrence_id"],
        ["id"],
    )
    op.create_index(
        "ix_assembly_occurrences_assembly_checkin_id",
        "assembly_occurrences",
        ["assembly_checkin_id"],
    )
    op.create_index(
        "ix_assembly_occurrences_parent_version_id",
        "assembly_occurrences",
        ["parent_version_id"],
    )
    op.create_index(
        "ix_assembly_occurrences_child_version_id",
        "assembly_occurrences",
        ["child_version_id"],
    )


def downgrade() -> None:
    op.drop_table("assembly_occurrences")
    op.drop_constraint(
        "fk_assembly_checkins_base_main_version",
        "assembly_checkins",
        type_="foreignkey",
    )
    op.drop_column("assembly_checkins", "base_main_version_id")
