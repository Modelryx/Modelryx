"""Remove the legacy Item-level checkout uniqueness constraint."""

from alembic import op

revision = "0047_revision_checkout_unique"
down_revision = "0046_structure_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("checkouts_part_id_key", "checkouts", type_="unique")


def downgrade() -> None:
    op.execute(
        "DELETE FROM checkouts AS newer USING checkouts AS older "
        "WHERE newer.part_id = older.part_id AND newer.acquired_at > older.acquired_at"
    )
    op.create_unique_constraint("checkouts_part_id_key", "checkouts", ["part_id"])
