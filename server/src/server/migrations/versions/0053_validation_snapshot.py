"""Bind validation results to an immutable staging proposal snapshot."""

import sqlalchemy as sa
from alembic import op

revision = "0053_validation_snapshot"
down_revision = "0052_staging_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "validation_runs",
        sa.Column("snapshot_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(
        "ck_validation_runs_snapshot_version",
        "validation_runs",
        "snapshot_version >= 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_validation_runs_snapshot_version", "validation_runs", type_="check")
    op.drop_column("validation_runs", "snapshot_version")
