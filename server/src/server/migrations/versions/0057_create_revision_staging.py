"""Allow atomic new-revision staging sessions."""

from alembic import op

revision = "0057_create_revision_staging"
down_revision = "0056_snapshot_scoped_primary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_staging_sessions_operation", "staging_sessions", type_="check")
    op.create_check_constraint(
        "ck_staging_sessions_operation",
        "staging_sessions",
        "operation IN ('CREATE_ITEM', 'CREATE_REVISION', 'EDIT_ITEM_REVISION', "
        "'ADD_ARTIFACT', 'CHECKIN_REVISION', 'CHECKIN_STRUCTURE')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE staging_sessions SET operation = 'CREATE_ITEM' WHERE operation = 'CREATE_REVISION'"
    )
    op.drop_constraint("ck_staging_sessions_operation", "staging_sessions", type_="check")
    op.create_check_constraint(
        "ck_staging_sessions_operation",
        "staging_sessions",
        "operation IN ('CREATE_ITEM', 'EDIT_ITEM_REVISION', 'ADD_ARTIFACT', "
        "'CHECKIN_REVISION', 'CHECKIN_STRUCTURE')",
    )
