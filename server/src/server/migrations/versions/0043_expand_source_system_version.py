"""Allow full native CAD-system provenance version strings."""

import sqlalchemy as sa
from alembic import op

revision = "0043_source_version_width"
down_revision = "0042_phase9_feature_creation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "metadata_extractions",
        "source_system_version",
        existing_type=sa.String(length=100),
        type_=sa.String(length=512),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "metadata_extractions",
        "source_system_version",
        existing_type=sa.String(length=512),
        type_=sa.String(length=100),
        existing_nullable=True,
    )
