"""Add named approval reviewer groups.

Revision ID: 0066_approval_reviewer_groups
Revises: 0065_revision_approval_workflow
"""

import sqlalchemy as sa
from alembic import op

revision = "0066_approval_reviewer_groups"
down_revision = "0065_revision_approval_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_reviewer_groups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "approval_reviewer_group_members",
        sa.Column("group_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"], ["approval_reviewer_groups.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "user_id"),
    )
    op.add_column(
        "revision_approval_requests",
        sa.Column("reviewer_group_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "revision_approval_requests",
        sa.Column("reviewer_group_name", sa.String(length=100), nullable=True),
    )
    op.create_foreign_key(
        "fk_revision_approval_requests_reviewer_group",
        "revision_approval_requests",
        "approval_reviewer_groups",
        ["reviewer_group_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_revision_approval_requests_reviewer_group",
        "revision_approval_requests",
        type_="foreignkey",
    )
    op.drop_column("revision_approval_requests", "reviewer_group_name")
    op.drop_column("revision_approval_requests", "reviewer_group_id")
    op.drop_table("approval_reviewer_group_members")
    op.drop_table("approval_reviewer_groups")
