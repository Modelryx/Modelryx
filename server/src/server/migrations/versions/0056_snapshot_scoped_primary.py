"""Scope primary Artifact selection to content snapshots.

Revision relations are historical. A replacement must be able to add a new
primary relation while an older snapshot continues to reference the previous
primary relation.
"""

from alembic import op

revision = "0056_snapshot_scoped_primary"
down_revision = "0055_revision_bom"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_revision_artifact_primary", table_name="revision_artifact_relations")


def downgrade() -> None:
    op.create_index(
        "uq_revision_artifact_primary",
        "revision_artifact_relations",
        ["revision_id", "relation_type"],
        unique=True,
        postgresql_where="is_primary IS true",
    )
