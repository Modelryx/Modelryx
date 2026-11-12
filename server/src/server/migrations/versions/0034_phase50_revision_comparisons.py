"""Phase 5.0 persistent deterministic revision comparisons."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034_phase50_comparisons"
down_revision = "0033_phase40_knowledge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revision_comparisons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parts.id"),
            nullable=False,
        ),
        sa.Column(
            "from_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "to_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False, unique=True),
        sa.Column("from_fingerprint", sa.String(64), nullable=False),
        sa.Column("to_fingerprint", sa.String(64), nullable=False),
        sa.Column("comparison_engine_version", sa.String(64), nullable=False),
        sa.Column("metadata_schema_version", sa.String(64), nullable=False),
        sa.Column("rules_version", sa.String(64), nullable=False),
        sa.Column("requested_domains_json", postgresql.JSONB(), nullable=False),
        sa.Column("summary_json", postgresql.JSONB(), nullable=False),
        sa.Column("error_detail", sa.Text()),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'PARTIAL', 'COMPLETED', 'FAILED', 'STALE')",
            name="ck_revision_comparisons_status",
        ),
        sa.CheckConstraint(
            "from_revision_id <> to_revision_id",
            name="ck_revision_comparisons_distinct_revisions",
        ),
    )
    for column in ("item_id", "from_revision_id", "to_revision_id", "status", "cache_key"):
        op.create_index(f"ix_revision_comparisons_{column}", "revision_comparisons", [column])

    op.create_table(
        "revision_comparison_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "comparison_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revision_comparisons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("engine_version", sa.String(64), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("change_count", sa.Integer(), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(), nullable=False),
        sa.Column("error_detail", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'UNCHANGED', 'UNSUPPORTED', 'FAILED')",
            name="ck_revision_comparison_domains_status",
        ),
        sa.UniqueConstraint("comparison_id", "domain", name="uq_revision_comparison_domains_name"),
    )
    op.create_index(
        "ix_revision_comparison_domains_comparison_id",
        "revision_comparison_domains",
        ["comparison_id"],
    )
    op.create_index(
        "ix_revision_comparison_domains_domain", "revision_comparison_domains", ["domain"]
    )
    op.create_index(
        "ix_revision_comparison_domains_status", "revision_comparison_domains", ["status"]
    )

    op.create_table(
        "revision_comparison_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "domain_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revision_comparison_domains.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("change_key", sa.String(512), nullable=False),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("property_path", sa.String(512), nullable=False),
        sa.Column("old_value_json", postgresql.JSONB()),
        sa.Column("new_value_json", postgresql.JSONB()),
        sa.Column("unit", sa.String(32)),
        sa.Column("delta", sa.Float()),
        sa.Column("percent_change", sa.Float()),
        sa.Column("significance", sa.String(32), nullable=False),
        sa.Column("categories_json", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "change_type IN ('ADDED', 'REMOVED', 'MODIFIED', 'UNCHANGED', 'MOVED', "
            "'RENAMED', 'REPLACED', 'UNKNOWN')",
            name="ck_revision_comparison_changes_type",
        ),
        sa.CheckConstraint(
            "significance IN ('INFO', 'MINOR', 'MODERATE', 'MAJOR', 'CRITICAL')",
            name="ck_revision_comparison_changes_significance",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_revision_comparison_changes_confidence",
        ),
        sa.UniqueConstraint("domain_id", "change_key", name="uq_revision_comparison_changes_key"),
    )
    op.create_index(
        "ix_revision_comparison_changes_domain_id",
        "revision_comparison_changes",
        ["domain_id"],
    )
    op.create_index(
        "ix_revision_comparison_changes_change_type",
        "revision_comparison_changes",
        ["change_type"],
    )
    op.create_index(
        "ix_revision_comparison_changes_significance",
        "revision_comparison_changes",
        ["significance"],
    )


def downgrade() -> None:
    op.drop_table("revision_comparison_changes")
    op.drop_table("revision_comparison_domains")
    op.drop_table("revision_comparisons")
