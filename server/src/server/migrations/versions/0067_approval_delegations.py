"""Add time-bounded approval delegation.

Revision ID: 0067_approval_delegations
Revises: 0066_approval_reviewer_groups
"""

import sqlalchemy as sa
from alembic import op

revision = "0067_approval_delegations"
down_revision = "0066_approval_reviewer_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_reviewer_delegations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("grantor_id", sa.UUID(), nullable=False),
        sa.Column("delegate_id", sa.UUID(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "grantor_id <> delegate_id", name="ck_approval_delegation_distinct_users"
        ),
        sa.CheckConstraint(
            "ends_at > starts_at", name="ck_approval_delegation_time_window"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["delegate_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["grantor_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_approval_reviewer_delegations_grantor_id",
        "approval_reviewer_delegations",
        ["grantor_id"],
    )
    op.create_index(
        "ix_approval_reviewer_delegations_delegate_id",
        "approval_reviewer_delegations",
        ["delegate_id"],
    )
    op.create_index(
        "ix_approval_reviewer_delegations_ends_at",
        "approval_reviewer_delegations",
        ["ends_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_approval_reviewer_delegations_ends_at",
        table_name="approval_reviewer_delegations",
    )
    op.drop_index(
        "ix_approval_reviewer_delegations_delegate_id",
        table_name="approval_reviewer_delegations",
    )
    op.drop_index(
        "ix_approval_reviewer_delegations_grantor_id",
        table_name="approval_reviewer_delegations",
    )
    op.drop_table("approval_reviewer_delegations")
