"""Restore CREATE_REVISION as an allowed staging operation."""

from alembic import op

revision = "0090_restore_create_revision"
down_revision = "0089_attachment_stage_task"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_staging_sessions_operation", "staging_sessions", type_="check")
    op.create_check_constraint(
        "ck_staging_sessions_operation",
        "staging_sessions",
        "operation IN ('CREATE_ITEM', 'CREATE_REVISION', 'EDIT_ITEM_REVISION', "
        "'ADD_ARTIFACT', 'CHECKIN_REVISION', 'CHECKIN_STRUCTURE', "
        "'EDIT_ITEM_ATTACHMENT', 'EDIT_REVISION_ATTACHMENT')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE staging_sessions SET operation = 'CREATE_ITEM' "
        "WHERE operation = 'CREATE_REVISION'"
    )
    op.drop_constraint("ck_staging_sessions_operation", "staging_sessions", type_="check")
    op.create_check_constraint(
        "ck_staging_sessions_operation",
        "staging_sessions",
        "operation IN ('CREATE_ITEM', 'EDIT_ITEM_REVISION', 'ADD_ARTIFACT', "
        "'CHECKIN_REVISION', 'CHECKIN_STRUCTURE', 'EDIT_ITEM_ATTACHMENT', "
        "'EDIT_REVISION_ATTACHMENT')",
    )
