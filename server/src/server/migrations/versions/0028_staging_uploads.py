"""Add verified staged object storage fields."""

import sqlalchemy as sa
from alembic import op

revision = "0028_staging_uploads"
down_revision = "0027_staging_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "staging_files",
        sa.Column(
            "mime_type", sa.String(255), nullable=False, server_default="application/octet-stream"
        ),
    )
    op.add_column("staging_files", sa.Column("storage_key", sa.String(1024), nullable=True))
    op.add_column(
        "staging_files", sa.Column("state", sa.String(32), nullable=False, server_default="PENDING")
    )
    op.execute("UPDATE staging_files SET storage_key = 'staging/legacy/' || id::text")
    op.alter_column("staging_files", "storage_key", nullable=False)
    op.create_unique_constraint("uq_staging_files_storage_key", "staging_files", ["storage_key"])
    op.create_index("ix_staging_files_state", "staging_files", ["state"])
    op.create_check_constraint(
        "ck_staging_files_state", "staging_files", "state IN ('PENDING', 'VERIFIED')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_staging_files_state", "staging_files", type_="check")
    op.drop_index("ix_staging_files_state", table_name="staging_files")
    op.drop_constraint("uq_staging_files_storage_key", "staging_files", type_="unique")
    op.drop_column("staging_files", "state")
    op.drop_column("staging_files", "storage_key")
    op.drop_column("staging_files", "mime_type")
