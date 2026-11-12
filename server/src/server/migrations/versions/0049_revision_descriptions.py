"""Add revision descriptions."""

import sqlalchemy as sa
from alembic import op

revision = "0049_revision_descriptions"
down_revision = "0048_user_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("revisions", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("revisions", "description")
