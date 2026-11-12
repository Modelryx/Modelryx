"""Pin checkouts to immutable Artifact content snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058_checkout_snapshot_base"
down_revision = "0057_create_revision_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "checkouts",
        sa.Column("base_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_checkouts_base_snapshot",
        "checkouts",
        "revision_content_snapshots",
        ["base_snapshot_id"],
        ["id"],
    )
    op.execute(
        """
        UPDATE checkouts
        SET base_snapshot_id = (
            SELECT id
            FROM revision_content_snapshots
            WHERE revision_id = checkouts.revision_id
            ORDER BY sequence DESC
            LIMIT 1
        )
        """
    )
    op.drop_constraint(
        "checkouts_base_working_version_id_fkey", "checkouts", type_="foreignkey"
    )
    op.drop_column("checkouts", "base_working_version_id")


def downgrade() -> None:
    op.add_column(
        "checkouts",
        sa.Column("base_working_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "checkouts_base_working_version_id_fkey",
        "checkouts",
        "working_versions",
        ["base_working_version_id"],
        ["id"],
    )
    op.execute(
        """
        UPDATE checkouts AS checkout
        SET base_working_version_id = revision.current_working_version_id
        FROM revisions AS revision
        WHERE revision.id = checkout.revision_id
        """
    )
    op.drop_constraint("fk_checkouts_base_snapshot", "checkouts", type_="foreignkey")
    op.drop_column("checkouts", "base_snapshot_id")
