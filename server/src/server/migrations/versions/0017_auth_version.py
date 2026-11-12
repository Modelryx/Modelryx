"""Add authentication version for immediate token revocation."""

import sqlalchemy as sa
from alembic import op

revision = "0017_auth_version"
down_revision = "0016_user_activation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("users", "auth_version", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "auth_version")
