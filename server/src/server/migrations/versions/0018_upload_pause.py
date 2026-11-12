"""Add explicit paused upload state."""

from alembic import op

revision = "0018_upload_pause"
down_revision = "0017_auth_version"
branch_labels = None
depends_on = None

CHECKIN_STATES = (
    "state IN ('UPLOADING', 'PAUSED', 'VALIDATING', 'COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED')"
)
OLD_CHECKIN_STATES = (
    "state IN ('UPLOADING', 'VALIDATING', 'COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED')"
)


def upgrade() -> None:
    op.drop_constraint("ck_checkins_state", "checkins", type_="check")
    op.create_check_constraint("ck_checkins_state", "checkins", CHECKIN_STATES)
    op.drop_constraint("ck_assembly_checkins_state", "assembly_checkins", type_="check")
    op.create_check_constraint("ck_assembly_checkins_state", "assembly_checkins", CHECKIN_STATES)


def downgrade() -> None:
    op.execute("UPDATE checkins SET state = 'UPLOADING' WHERE state = 'PAUSED'")
    op.execute("UPDATE assembly_checkins SET state = 'UPLOADING' WHERE state = 'PAUSED'")
    op.drop_constraint("ck_checkins_state", "checkins", type_="check")
    op.create_check_constraint("ck_checkins_state", "checkins", OLD_CHECKIN_STATES)
    op.drop_constraint("ck_assembly_checkins_state", "assembly_checkins", type_="check")
    op.create_check_constraint(
        "ck_assembly_checkins_state", "assembly_checkins", OLD_CHECKIN_STATES
    )
