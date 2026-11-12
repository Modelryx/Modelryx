"""Add password credentials for JWT authentication."""

import sqlalchemy as sa
from alembic import op

revision = "0015_user_passwords"
down_revision = "0014_personal_ui"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "password_hash")
