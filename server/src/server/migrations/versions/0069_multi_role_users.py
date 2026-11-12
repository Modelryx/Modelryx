"""Add independent multi-role user assignments.

Revision ID: 0069_multi_role_users
Revises: 0068_emergency_approval_expiry
"""

import sqlalchemy as sa
from alembic import op

revision = "0069_multi_role_users"
down_revision = "0068_emergency_approval_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('reader', 'designer', 'reviewer', 'administrator')",
            name="ck_user_roles_role",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role"),
    )
    op.execute("""
        INSERT INTO user_roles (user_id, role)
        SELECT id, CASE WHEN role = 'engineer' THEN 'designer' ELSE role END FROM users
    """)
    # Reviewer was historically a hierarchical engineer+reviewer role.
    op.execute("""
        INSERT INTO user_roles (user_id, role)
        SELECT id, 'designer' FROM users WHERE role = 'reviewer'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("user_roles")
