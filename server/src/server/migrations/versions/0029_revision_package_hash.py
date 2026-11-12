"""Persist canonical logical-package hashes on promoted revisions."""

import sqlalchemy as sa
from alembic import op

revision = "0029_revision_package_hash"
down_revision = "0028_staging_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("revisions", sa.Column("package_sha256", sa.String(64), nullable=True))
    op.create_index("ix_revisions_package_sha256", "revisions", ["package_sha256"])


def downgrade() -> None:
    op.drop_index("ix_revisions_package_sha256", table_name="revisions")
    op.drop_column("revisions", "package_sha256")
