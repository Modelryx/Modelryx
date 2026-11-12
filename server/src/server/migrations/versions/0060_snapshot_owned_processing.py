"""Bind processing records to immutable revision content snapshots.

Revision ID: 0060_snapshot_processing
Revises: 0059_retire_split_sessions
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0060_snapshot_processing"
down_revision = "0059_retire_split_sessions"
branch_labels = None
depends_on = None


def _add_snapshot(table: str) -> None:
    op.add_column(table, sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        f"fk_{table}_snapshot", table, "revision_content_snapshots", ["snapshot_id"], ["id"]
    )
    op.create_index(f"ix_{table}_snapshot_id", table, ["snapshot_id"])
    op.execute(
        sa.text(
            f"""
            UPDATE {table} AS target
            SET snapshot_id = (
                SELECT id
                FROM revision_content_snapshots
                WHERE revision_id = target.revision_id
                ORDER BY sequence DESC
                LIMIT 1
            )
            WHERE target.snapshot_id IS NULL
            """
        )
    )


def upgrade() -> None:
    op.alter_column("artifact_files", "working_version_id", nullable=True)
    for table in ("processing_jobs", "metadata_extractions", "download_bundles"):
        _add_snapshot(table)


def downgrade() -> None:
    for table in ("download_bundles", "metadata_extractions", "processing_jobs"):
        op.drop_index(f"ix_{table}_snapshot_id", table_name=table)
        op.drop_constraint(f"fk_{table}_snapshot", table, type_="foreignkey")
        op.drop_column(table, "snapshot_id")
    op.alter_column("artifact_files", "working_version_id", nullable=False)
