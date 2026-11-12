"""Add structured worker compatibility and capabilities.

Revision ID: 0077_worker_capabilities
Revises: 0076_paired_number_templates
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0077_worker_capabilities"
down_revision = "0076_paired_number_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("worker_heartbeats", sa.Column("adapter_version", sa.String(100)))
    op.add_column(
        "worker_heartbeats",
        sa.Column("compatibility_state", sa.String(24), nullable=False, server_default="UNKNOWN"),
    )
    op.add_column(
        "worker_heartbeats",
        sa.Column(
            "capabilities_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ck_worker_heartbeats_compatibility",
        "worker_heartbeats",
        "compatibility_state IN ('COMPATIBLE', 'INCOMPATIBLE', 'UNKNOWN')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_worker_heartbeats_compatibility", "worker_heartbeats", type_="check"
    )
    op.drop_column("worker_heartbeats", "capabilities_json")
    op.drop_column("worker_heartbeats", "compatibility_state")
    op.drop_column("worker_heartbeats", "adapter_version")
