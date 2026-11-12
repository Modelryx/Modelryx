"""Add worker heartbeat registry.

Revision ID: 0073_worker_heartbeats
Revises: 0072_backfill_feature_counts
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0073_worker_heartbeats"
down_revision = "0072_backfill_feature_counts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_type", sa.String(32), primary_key=True),
        sa.Column("machine_name", sa.String(255), primary_key=True),
        sa.Column("worker_version", sa.String(100), nullable=False),
        sa.Column("application_version", sa.String(100)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("current_job_id", postgresql.UUID(as_uuid=True)),
        sa.Column("detail", sa.String(500)),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "worker_type IN ('FREECAD', 'AUTOCAD', 'INVENTOR', 'STEP')",
            name="ck_worker_heartbeats_type",
        ),
        sa.CheckConstraint(
            "status IN ('IDLE', 'BUSY', 'DEGRADED')", name="ck_worker_heartbeats_status"
        ),
    )
    op.create_index(
        "ix_worker_heartbeats_last_heartbeat_at", "worker_heartbeats", ["last_heartbeat_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_worker_heartbeats_last_heartbeat_at", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
