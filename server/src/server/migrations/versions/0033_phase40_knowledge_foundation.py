"""Phase 4.0 generalized engineering knowledge foundation."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision = "0033_phase40_knowledge"
down_revision = "0032_phase31a_assistant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_object_type", sa.String(64), nullable=False),
        sa.Column("source_object_id", postgresql.UUID(as_uuid=True)),
        sa.Column("external_key", sa.String(255)),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("lifecycle_state", sa.String(32)),
        sa.Column("authority_rank", sa.Integer(), nullable=False),
        sa.Column("security_scope", sa.String(32), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parts.id")),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("revisions.id")),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("index_version", sa.String(32), nullable=False),
        sa.Column("is_stale", sa.Boolean(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'SUPERSEDED', 'WITHDRAWN')",
            name="ck_knowledge_sources_status",
        ),
        sa.CheckConstraint(
            "security_scope IN ('PUBLIC', 'ENGINEERING', 'PDM')",
            name="ck_knowledge_sources_security_scope",
        ),
        sa.CheckConstraint(
            "authority_rank >= 0 AND authority_rank <= 100",
            name="ck_knowledge_sources_authority_rank",
        ),
        sa.UniqueConstraint(
            "source_object_type",
            "source_object_id",
            "external_key",
            name="uq_knowledge_sources_identity",
        ),
    )
    for column in (
        "source_type",
        "source_object_type",
        "source_object_id",
        "external_key",
        "status",
        "lifecycle_state",
        "security_scope",
        "item_id",
        "revision_id",
        "is_stale",
    ):
        op.create_index(f"ix_knowledge_sources_{column}", "knowledge_sources", [column])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section", sa.String(255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("embedding", VECTOR(1536)),
        sa.Column("embedding_model", sa.String(255)),
        sa.Column("location_json", postgresql.JSONB(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "section", "ordinal", name="uq_knowledge_chunks_location"),
    )
    op.create_index("ix_knowledge_chunks_source_id", "knowledge_chunks", ["source_id"])
    op.create_index("ix_knowledge_chunks_content_sha256", "knowledge_chunks", ["content_sha256"])
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding_hnsw ON knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_content_fts ON knowledge_chunks "
        "USING gin (to_tsvector('english', content))"
    )

    op.create_table(
        "knowledge_relations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "from_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(64), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "from_source_id",
            "to_source_id",
            "relation_type",
            name="uq_knowledge_relations_edge",
        ),
    )
    op.create_index(
        "ix_knowledge_relations_from_source_id", "knowledge_relations", ["from_source_id"]
    )
    op.create_index("ix_knowledge_relations_to_source_id", "knowledge_relations", ["to_source_id"])
    op.create_index(
        "ix_knowledge_relations_relation_type", "knowledge_relations", ["relation_type"]
    )

    op.create_table(
        "knowledge_index_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_sources.id", ondelete="SET NULL"),
        ),
        sa.Column("source_object_type", sa.String(64), nullable=False),
        sa.Column("source_object_id", postgresql.UUID(as_uuid=True)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_detail", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_knowledge_index_jobs_state",
        ),
    )
    op.create_index("ix_knowledge_index_jobs_source_id", "knowledge_index_jobs", ["source_id"])
    op.create_index(
        "ix_knowledge_index_jobs_source_object_id",
        "knowledge_index_jobs",
        ["source_object_id"],
    )
    op.create_index("ix_knowledge_index_jobs_state", "knowledge_index_jobs", ["state"])


def downgrade() -> None:
    op.drop_table("knowledge_index_jobs")
    op.drop_table("knowledge_relations")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_sources")
