"""Allow CAD session commits to publish multiple versions of one revision."""

from alembic import op

revision = "0050_cad_commit_versions"
down_revision = "0049_revision_descriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "cad_commit_plans_committed_revision_id_key",
        "cad_commit_plans",
        type_="unique",
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "cad_commit_plans_committed_revision_id_key",
        "cad_commit_plans",
        ["committed_revision_id"],
    )
