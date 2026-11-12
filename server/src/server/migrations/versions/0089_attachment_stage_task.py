"""Associate attachment changes with their workspace task."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0089_attachment_stage_task"
down_revision = "0088_attachment_staged_remove"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "additional_file_stages",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_additional_file_stages_task_id",
        "additional_file_stages",
        "staging_sessions",
        ["task_id"],
        ["id"],
    )
    op.create_index(
        "ix_additional_file_stages_task_id",
        "additional_file_stages",
        ["task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_additional_file_stages_task_id", table_name="additional_file_stages")
    op.drop_constraint(
        "fk_additional_file_stages_task_id",
        "additional_file_stages",
        type_="foreignkey",
    )
    op.drop_column("additional_file_stages", "task_id")
