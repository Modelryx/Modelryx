"""Make staging a versioned aggregate proposal."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0052_staging_proposals"
down_revision = "0051_artifact_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "staging_sessions",
        sa.Column("operation", sa.String(length=32), server_default="CREATE_ITEM", nullable=False),
    )
    op.add_column(
        "staging_sessions",
        sa.Column("snapshot_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "staging_sessions",
        sa.Column("base_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "staging_sessions",
        sa.Column("base_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "staging_sessions",
        sa.Column("proposal_json", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.add_column(
        "staging_sessions",
        sa.Column("commit_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "staging_sessions",
        sa.Column("result_json", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.create_foreign_key(
        "fk_staging_sessions_base_revision",
        "staging_sessions",
        "revisions",
        ["base_revision_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_staging_sessions_base_snapshot",
        "staging_sessions",
        "revision_content_snapshots",
        ["base_snapshot_id"],
        ["id"],
    )
    op.create_unique_constraint("uq_staging_sessions_commit_id", "staging_sessions", ["commit_id"])
    op.create_index("ix_staging_sessions_operation", "staging_sessions", ["operation"])
    op.create_index(
        "ix_staging_sessions_base_revision_id", "staging_sessions", ["base_revision_id"]
    )
    op.create_check_constraint(
        "ck_staging_sessions_operation",
        "staging_sessions",
        "operation IN ('CREATE_ITEM', 'EDIT_ITEM_REVISION', 'ADD_ARTIFACT', "
        "'CHECKIN_REVISION', 'CHECKIN_STRUCTURE')",
    )
    op.create_check_constraint(
        "ck_staging_sessions_snapshot_version", "staging_sessions", "snapshot_version >= 1"
    )
    op.drop_constraint("ck_staging_sessions_status", "staging_sessions", type_="check")
    op.create_check_constraint(
        "ck_staging_sessions_status",
        "staging_sessions",
        "status IN ('HASHED', 'METADATA_READY', 'REVIEW_REQUIRED', 'APPROVED', "
        "'PREPARING', 'READY_TO_COMMIT', 'COMMITTING', 'COMPLETED', 'FAILED', "
        "'CANCELLED', 'PROMOTED')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE staging_sessions SET status = 'APPROVED' "
        "WHERE status IN ('PREPARING', 'READY_TO_COMMIT', 'FAILED')"
    )
    op.execute(
        "UPDATE staging_sessions SET status = 'PROMOTED' "
        "WHERE status IN ('COMMITTING', 'COMPLETED')"
    )
    op.drop_constraint("ck_staging_sessions_status", "staging_sessions", type_="check")
    op.create_check_constraint(
        "ck_staging_sessions_status",
        "staging_sessions",
        "status IN ('HASHED', 'METADATA_READY', 'REVIEW_REQUIRED', 'APPROVED', "
        "'CANCELLED', 'PROMOTED')",
    )
    op.drop_constraint("ck_staging_sessions_snapshot_version", "staging_sessions", type_="check")
    op.drop_constraint("ck_staging_sessions_operation", "staging_sessions", type_="check")
    op.drop_index("ix_staging_sessions_base_revision_id", table_name="staging_sessions")
    op.drop_index("ix_staging_sessions_operation", table_name="staging_sessions")
    op.drop_constraint("uq_staging_sessions_commit_id", "staging_sessions", type_="unique")
    op.drop_constraint("fk_staging_sessions_base_snapshot", "staging_sessions", type_="foreignkey")
    op.drop_constraint("fk_staging_sessions_base_revision", "staging_sessions", type_="foreignkey")
    op.drop_column("staging_sessions", "result_json")
    op.drop_column("staging_sessions", "commit_id")
    op.drop_column("staging_sessions", "proposal_json")
    op.drop_column("staging_sessions", "base_snapshot_id")
    op.drop_column("staging_sessions", "base_revision_id")
    op.drop_column("staging_sessions", "snapshot_version")
    op.drop_column("staging_sessions", "operation")
