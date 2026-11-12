"""Add pgvector-backed AI retrieval documents."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision = "0019_ai_documents"
down_revision = "0018_upload_pause"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "ai_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_key", sa.String(512), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("embedding", VECTOR(1536), nullable=True),
        sa.Column("embedding_model", sa.String(255), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["parts.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["revisions.id"]),
        sa.UniqueConstraint("revision_id", "chunk_key", name="uq_ai_documents_revision_chunk"),
    )
    op.create_index("ix_ai_documents_item_id", "ai_documents", ["item_id"])
    op.create_index("ix_ai_documents_revision_id", "ai_documents", ["revision_id"])
    op.execute(
        "CREATE INDEX ix_ai_documents_embedding_hnsw ON ai_documents "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_ai_documents_content_fts ON ai_documents "
        "USING gin (to_tsvector('english', content))"
    )


def downgrade() -> None:
    op.drop_table("ai_documents")
