"""Add snapshot-bound revision approval workflow.

Revision ID: 0065_revision_approval_workflow
Revises: 0064_search_feedback
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0065_revision_approval_workflow"
down_revision = "0064_search_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revision_approval_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("revision_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("policy_json", postgresql.JSONB(), nullable=False),
        sa.Column("required_approvals", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_comment", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("warnings_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'WITHDRAWN', 'STALE')",
            name="ck_revision_approval_requests_status",
        ),
        sa.CheckConstraint(
            "decided_by IS NULL OR decided_by != requested_by",
            name="ck_revision_approval_requests_separation_of_duties",
        ),
        sa.CheckConstraint(
            "required_approvals BETWEEN 1 AND 5",
            name="ck_revision_approval_requests_required_approvals",
        ),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["revisions.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["revision_content_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_revision_approval_requests_pending",
        "revision_approval_requests",
        ["revision_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_revision_approval_requests_revision_id",
        "revision_approval_requests",
        ["revision_id"],
    )
    op.create_index(
        "ix_revision_approval_requests_snapshot_id",
        "revision_approval_requests",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_revision_approval_requests_status",
        "revision_approval_requests",
        ["status"],
    )
    op.create_index(
        "ix_revision_approval_requests_due_at",
        "revision_approval_requests",
        ["due_at"],
    )
    op.create_table(
        "revision_approval_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("approval_request_id", sa.UUID(), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("warnings_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED')",
            name="ck_revision_approval_decisions_decision",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"], ["revision_approval_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "approval_request_id",
            "decided_by",
            name="uq_revision_approval_decisions_reviewer",
        ),
    )
    op.create_index(
        "ix_revision_approval_decisions_approval_request_id",
        "revision_approval_decisions",
        ["approval_request_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_revision_approval_decisions_approval_request_id",
        table_name="revision_approval_decisions",
    )
    op.drop_table("revision_approval_decisions")
    op.drop_index("ix_revision_approval_requests_due_at", table_name="revision_approval_requests")
    op.drop_index("ix_revision_approval_requests_status", table_name="revision_approval_requests")
    op.drop_index(
        "ix_revision_approval_requests_snapshot_id", table_name="revision_approval_requests"
    )
    op.drop_index(
        "ix_revision_approval_requests_revision_id", table_name="revision_approval_requests"
    )
    op.drop_index(
        "uq_revision_approval_requests_pending", table_name="revision_approval_requests"
    )
    op.drop_table("revision_approval_requests")
