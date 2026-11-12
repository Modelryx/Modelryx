"""Store validated normalized metadata on pre-creation staging sessions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027_staging_metadata"
down_revision = "0026_precreation_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "staging_sessions",
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
    )
    op.drop_constraint("ck_staging_sessions_status", "staging_sessions", type_="check")
    op.create_check_constraint(
        "ck_staging_sessions_status",
        "staging_sessions",
        "status IN ('HASHED', 'METADATA_READY', 'REVIEW_REQUIRED', 'APPROVED', "
        "'CANCELLED', 'PROMOTED')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_staging_sessions_status", "staging_sessions", type_="check")
    op.create_check_constraint(
        "ck_staging_sessions_status",
        "staging_sessions",
        "status IN ('HASHED', 'REVIEW_REQUIRED', 'APPROVED', 'CANCELLED', 'PROMOTED')",
    )
    op.drop_column("staging_sessions", "metadata_json")
