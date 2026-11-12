"""Expose persistent CAD download bundles as Workspace tasks."""

import sqlalchemy as sa
from alembic import op

revision = "0091_bundle_workspace"
down_revision = "0090_restore_create_revision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "download_bundles",
        sa.Column("workspace_state", sa.String(length=32), nullable=False, server_default="ACTIVE"),
    )
    op.create_index(
        "ix_download_bundles_workspace_state",
        "download_bundles",
        ["workspace_state"],
    )
    op.create_check_constraint(
        "ck_download_bundles_workspace_state",
        "download_bundles",
        "workspace_state IN ('ACTIVE', 'ARCHIVED', 'REMOVED')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_download_bundles_workspace_state", "download_bundles", type_="check"
    )
    op.drop_index("ix_download_bundles_workspace_state", table_name="download_bundles")
    op.drop_column("download_bundles", "workspace_state")
