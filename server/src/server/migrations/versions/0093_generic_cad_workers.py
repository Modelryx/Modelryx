"""Allow CAD worker types to be registered without a server migration."""

from alembic import op

revision = "0093_generic_cad_workers"
down_revision = "0092_bundle_signature"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_worker_heartbeats_type", "worker_heartbeats", type_="check"
    )


def downgrade() -> None:
    op.create_check_constraint(
        "ck_worker_heartbeats_type",
        "worker_heartbeats",
        "worker_type IN ('FREECAD', 'AUTOCAD', 'INVENTOR', 'STEP')",
    )
