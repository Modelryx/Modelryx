"""Separate Item and Item Revision attachment staging."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0087_revision_attachment_staging"
down_revision = "0086_attachment_edit_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("additional_file_stages", sa.Column(
        "revision_id", postgresql.UUID(as_uuid=True), nullable=True
    ))
    op.create_foreign_key(
        "fk_additional_file_stages_revision_id", "additional_file_stages", "revisions",
        ["revision_id"], ["id"],
    )
    op.create_index(
        "ix_additional_file_stages_revision_id", "additional_file_stages", ["revision_id"]
    )
    op.drop_constraint("ck_staging_sessions_operation", "staging_sessions", type_="check")
    op.create_check_constraint(
        "ck_staging_sessions_operation", "staging_sessions",
        "operation IN ('CREATE_ITEM', 'EDIT_ITEM_REVISION', 'ADD_ARTIFACT', "
        "'CHECKIN_REVISION', 'CHECKIN_STRUCTURE', 'EDIT_ITEM_ATTACHMENT', "
        "'EDIT_REVISION_ATTACHMENT')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_staging_sessions_operation", "staging_sessions", type_="check")
    op.create_check_constraint(
        "ck_staging_sessions_operation", "staging_sessions",
        "operation IN ('CREATE_ITEM', 'EDIT_ITEM_REVISION', 'ADD_ARTIFACT', "
        "'CHECKIN_REVISION', 'CHECKIN_STRUCTURE', 'EDIT_ITEM_ATTACHMENT')",
    )
    op.drop_index("ix_additional_file_stages_revision_id", table_name="additional_file_stages")
    op.drop_constraint(
        "fk_additional_file_stages_revision_id", "additional_file_stages", type_="foreignkey"
    )
    op.drop_column("additional_file_stages", "revision_id")
