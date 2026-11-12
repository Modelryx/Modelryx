"""Allow staged attachment removal."""

from alembic import op

revision = "0088_attachment_staged_remove"
down_revision = "0087_revision_attachment_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_additional_file_stage_action", "additional_file_stages", type_="check")
    op.create_check_constraint(
        "ck_additional_file_stage_action", "additional_file_stages",
        "action IN ('CREATE', 'NEW_VERSION', 'REMOVE')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_additional_file_stage_action", "additional_file_stages", type_="check")
    op.create_check_constraint(
        "ck_additional_file_stage_action", "additional_file_stages",
        "action IN ('CREATE', 'NEW_VERSION')",
    )
