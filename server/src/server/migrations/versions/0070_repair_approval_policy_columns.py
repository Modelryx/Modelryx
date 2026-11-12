"""Repair approval policy columns missing from previously migrated databases.

Revision ID: 0070_repair_approval_policy
Revises: 0069_multi_role_users
"""

from alembic import op

revision = "0070_repair_approval_policy"
down_revision = "0069_multi_role_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Some databases applied an earlier form of migration 0065, before these
    # policy fields were added to that migration. IF NOT EXISTS keeps this a
    # no-op for databases created from the current migration chain.
    op.execute(
        "ALTER TABLE revision_approval_requests "
        "ADD COLUMN IF NOT EXISTS policy_version VARCHAR(32) NOT NULL DEFAULT '1'"
    )
    op.execute(
        "ALTER TABLE revision_approval_requests "
        "ADD COLUMN IF NOT EXISTS policy_json JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "ALTER TABLE revision_approval_requests "
        "ADD COLUMN IF NOT EXISTS required_approvals INTEGER NOT NULL DEFAULT 1"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_revision_approval_requests_required_approvals'
            ) THEN
                ALTER TABLE revision_approval_requests
                ADD CONSTRAINT ck_revision_approval_requests_required_approvals
                CHECK (required_approvals BETWEEN 1 AND 5);
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    # These columns are part of the 0065 schema on newly created databases and
    # must remain when this repair marker is downgraded.
    pass
