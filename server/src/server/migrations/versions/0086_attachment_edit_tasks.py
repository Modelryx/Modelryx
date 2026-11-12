"""Add revision-scoped attachment edit tasks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0086_attachment_edit_tasks"
down_revision = "0085_attachment_checkouts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("attachment_checkouts", sa.Column(
        "revision_id", postgresql.UUID(as_uuid=True), nullable=True
    ))
    op.add_column("attachment_checkouts", sa.Column(
        "task_id", postgresql.UUID(as_uuid=True), nullable=True
    ))
    op.create_foreign_key(
        "fk_attachment_checkouts_revision_id", "attachment_checkouts", "revisions",
        ["revision_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_attachment_checkouts_task_id", "attachment_checkouts", "staging_sessions",
        ["task_id"], ["id"],
    )
    op.create_index("ix_attachment_checkouts_revision_id", "attachment_checkouts", ["revision_id"])
    op.create_index("ix_attachment_checkouts_task_id", "attachment_checkouts", ["task_id"])
    op.drop_constraint("ck_staging_sessions_operation", "staging_sessions", type_="check")
    op.create_check_constraint(
        "ck_staging_sessions_operation", "staging_sessions",
        "operation IN ('CREATE_ITEM', 'EDIT_ITEM_REVISION', 'ADD_ARTIFACT', "
        "'CHECKIN_REVISION', 'CHECKIN_STRUCTURE', 'EDIT_ITEM_ATTACHMENT')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_staging_sessions_operation", "staging_sessions", type_="check")
    op.create_check_constraint(
        "ck_staging_sessions_operation", "staging_sessions",
        "operation IN ('CREATE_ITEM', 'EDIT_ITEM_REVISION', 'ADD_ARTIFACT', "
        "'CHECKIN_REVISION', 'CHECKIN_STRUCTURE')",
    )
    op.drop_index("ix_attachment_checkouts_task_id", table_name="attachment_checkouts")
    op.drop_index("ix_attachment_checkouts_revision_id", table_name="attachment_checkouts")
    op.drop_constraint(
        "fk_attachment_checkouts_task_id", "attachment_checkouts", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_attachment_checkouts_revision_id", "attachment_checkouts", type_="foreignkey"
    )
    op.drop_column("attachment_checkouts", "task_id")
    op.drop_column("attachment_checkouts", "revision_id")
