"""Support terminal expiry for emergency approval requests.

Revision ID: 0068_emergency_approval_expiry
Revises: 0067_approval_delegations
"""

from alembic import op

revision = "0068_emergency_approval_expiry"
down_revision = "0067_approval_delegations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_revision_approval_requests_status",
        "revision_approval_requests",
        type_="check",
    )
    op.create_check_constraint(
        "ck_revision_approval_requests_status",
        "revision_approval_requests",
        "status IN ('PENDING', 'APPROVED', 'REJECTED', 'WITHDRAWN', 'STALE', 'EXPIRED')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE revision_approval_requests SET status = 'WITHDRAWN' "
        "WHERE status = 'EXPIRED'"
    )
    op.drop_constraint(
        "ck_revision_approval_requests_status",
        "revision_approval_requests",
        type_="check",
    )
    op.create_check_constraint(
        "ck_revision_approval_requests_status",
        "revision_approval_requests",
        "status IN ('PENDING', 'APPROVED', 'REJECTED', 'WITHDRAWN', 'STALE')",
    )
