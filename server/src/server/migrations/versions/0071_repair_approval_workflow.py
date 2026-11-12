"""Repair remaining approval workflow schema drift.

Revision ID: 0071_repair_approval_workflow
Revises: 0070_repair_approval_policy
"""

from alembic import op

revision = "0071_repair_approval_workflow"
down_revision = "0070_repair_approval_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE revision_approval_requests "
        "ADD COLUMN IF NOT EXISTS due_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_revision_approval_requests_due_at "
        "ON revision_approval_requests (due_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS revision_approval_decisions (
            id UUID PRIMARY KEY,
            approval_request_id UUID NOT NULL
                REFERENCES revision_approval_requests(id) ON DELETE CASCADE,
            decision VARCHAR(24) NOT NULL,
            comment TEXT NOT NULL,
            warnings_acknowledged BOOLEAN NOT NULL,
            decided_by UUID NOT NULL REFERENCES users(id),
            decided_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_revision_approval_decisions_decision
                CHECK (decision IN ('APPROVED', 'REJECTED')),
            CONSTRAINT uq_revision_approval_decisions_reviewer
                UNIQUE (approval_request_id, decided_by)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_revision_approval_decisions_approval_request_id "
        "ON revision_approval_decisions (approval_request_id)"
    )


def downgrade() -> None:
    # The repaired objects are part of the 0065 schema and must remain when the
    # repair marker itself is downgraded.
    pass
