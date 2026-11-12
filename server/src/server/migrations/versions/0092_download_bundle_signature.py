"""Track the native CAD source set used by a download bundle."""

import sqlalchemy as sa
from alembic import op

revision = "0092_bundle_signature"
down_revision = "0091_bundle_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "download_bundles",
        sa.Column("source_signature", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_download_bundles_source_signature",
        "download_bundles",
        ["source_signature"],
    )


def downgrade() -> None:
    op.drop_index("ix_download_bundles_source_signature", table_name="download_bundles")
    op.drop_column("download_bundles", "source_signature")
