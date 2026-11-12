"""Create core part, revision, and artifact tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_parts_revisions_artifacts"
down_revision = "0001_foundations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("part_number", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("category", sa.String(100)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_revision_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("part_number"),
    )
    op.create_index("ix_parts_part_number", "parts", ["part_number"], unique=True)
    op.create_index("ix_parts_category", "parts", ["category"])
    op.create_index("ix_parts_status", "parts", ["status"])
    op.create_table(
        "revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "part_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parts.id"),
            nullable=False,
        ),
        sa.Column("revision_code", sa.String(50), nullable=False),
        sa.Column("cad_system", sa.String(100), nullable=False),
        sa.Column("native_format", sa.String(50), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('DRAFT', 'UPLOADING', 'VALIDATING', 'COMMITTED', 'PROCESSING', "
            "'READY', 'READY_WITH_WARNINGS', 'FAILED')",
            name="ck_revisions_state",
        ),
        sa.UniqueConstraint("part_id", "revision_code", name="uq_revisions_part_code"),
    )
    op.create_index("ix_revisions_part_id", "revisions", ["part_id"])
    op.create_index("ix_revisions_state", "revisions", ["state"])
    op.create_foreign_key(
        "fk_parts_current_revision", "parts", "revisions", ["current_revision_id"], ["id"]
    )
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revisions.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_revision_id", "artifacts", ["revision_id"])


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_constraint("fk_parts_current_revision", "parts", type_="foreignkey")
    op.drop_table("revisions")
    op.drop_table("parts")
