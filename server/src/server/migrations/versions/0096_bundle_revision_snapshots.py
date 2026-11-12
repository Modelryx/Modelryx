"""Pin every referenced revision version when a CAD bundle is requested."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0096_bundle_revision_snapshots"
down_revision = "0095_ai_metadata_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("download_bundles", sa.Column("revision_snapshots_json", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("download_bundles", "revision_snapshots_json")
