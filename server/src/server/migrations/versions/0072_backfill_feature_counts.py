"""Backfill searchable feature counts from normalized metadata.

Revision ID: 0072_backfill_feature_counts
Revises: 0071_repair_approval_workflow
"""

from alembic import op

revision = "0072_backfill_feature_counts"
down_revision = "0071_repair_approval_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Feature-count columns were introduced after some successful extractions
    # had already persisted their normalized JSON. Preserve unknown coverage by
    # backfilling only counts explicitly present as non-negative integers.
    for feature in (
        "hole",
        "fillet",
        "chamfer",
        "extrusion",
        "revolution",
        "pocket",
        "pattern",
        "thread",
    ):
        column = f"{feature}_count"
        json_path = f"{{features,summary,{column}}}"
        op.execute(
            f"""
            UPDATE metadata_extractions
            SET {column} = (metadata_json #>> '{json_path}')::integer
            WHERE {column} IS NULL
              AND (metadata_json #>> '{json_path}') ~ '^\\d+$'
            """
        )


def downgrade() -> None:
    # Backfilled values are valid projections of authoritative normalized JSON
    # and should not be discarded when the migration marker is downgraded.
    pass
