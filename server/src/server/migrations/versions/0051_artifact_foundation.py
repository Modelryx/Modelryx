"""Introduce Artifact containers and ArtifactFile named references."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0051_artifact_foundation"
down_revision = "0050_cad_commit_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The pre-foundation artifacts table stores individual files. Preserve those
    # rows as the transitional ArtifactFile model while the write paths move to
    # managed Artifact containers.
    op.rename_table("artifacts", "artifact_files")

    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("classification", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("derived_from_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generator", sa.String(length=100), nullable=True),
        sa.Column("generator_version", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation >= 1", name="ck_artifacts_generation"),
        sa.CheckConstraint(
            "classification IN ('AUTHORITATIVE', 'DERIVED', 'EXTERNAL_REFERENCE')",
            name="ck_artifacts_classification",
        ),
        sa.CheckConstraint(
            "source IN ('USER', 'WORKER', 'IMPORT', 'SYSTEM')", name="ck_artifacts_source"
        ),
        sa.CheckConstraint(
            "state IN ('READY', 'PROCESSING', 'FAILED', 'SUPERSEDED')",
            name="ck_artifacts_state",
        ),
        sa.CheckConstraint(
            "classification != 'DERIVED' OR derived_from_artifact_id IS NOT NULL",
            name="ck_artifacts_derived_source",
        ),
        sa.ForeignKeyConstraint(
            ["derived_from_artifact_id"], ["artifacts.id"], name="fk_artifacts_derived_from"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_key", "generation", name="uq_artifacts_key_generation"),
    )
    op.create_index("ix_artifacts_artifact_key", "artifacts", ["artifact_key"])
    op.create_index("ix_artifacts_artifact_type", "artifacts", ["artifact_type"])
    op.create_index("ix_artifacts_classification", "artifacts", ["classification"])
    op.create_index("ix_artifacts_source", "artifacts", ["source"])
    op.create_index("ix_artifacts_state", "artifacts", ["state"])

    op.add_column(
        "artifact_files",
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "artifact_files", sa.Column("reference_name", sa.String(length=100), nullable=True)
    )
    op.add_column("artifact_files", sa.Column("role", sa.String(length=24), nullable=True))
    op.create_foreign_key(
        "fk_artifact_files_artifact", "artifact_files", "artifacts", ["artifact_id"], ["id"]
    )
    op.create_unique_constraint(
        "uq_artifact_files_reference", "artifact_files", ["artifact_id", "reference_name"]
    )
    op.create_check_constraint("ck_artifact_files_byte_size", "artifact_files", "byte_size >= 0")
    op.create_check_constraint(
        "ck_artifact_files_role",
        "artifact_files",
        "role IS NULL OR role IN ('PRIMARY', 'SECONDARY', 'AUXILIARY')",
    )
    op.create_index("ix_artifact_files_artifact_id", "artifact_files", ["artifact_id"])

    op.create_table(
        "revision_artifact_relations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="ck_revision_artifact_relations_sequence"),
        sa.CheckConstraint(
            "relation_type IN ('SPECIFICATION', 'REFERENCE', 'DRAWING', 'REPRESENTATION', "
            "'THUMBNAIL', 'ANALYSIS', 'ATTACHMENT')",
            name="ck_revision_artifact_relations_type",
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "revision_id", "artifact_id", "relation_type", name="uq_revision_artifact_relation"
        ),
    )
    op.create_index(
        "ix_revision_artifact_relations_revision_id",
        "revision_artifact_relations",
        ["revision_id"],
    )
    op.create_index(
        "ix_revision_artifact_relations_artifact_id",
        "revision_artifact_relations",
        ["artifact_id"],
    )
    op.create_index(
        "ix_revision_artifact_relations_relation_type",
        "revision_artifact_relations",
        ["relation_type"],
    )
    op.create_index(
        "uq_revision_artifact_primary",
        "revision_artifact_relations",
        ["revision_id", "relation_type"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    op.create_table(
        "revision_content_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("commit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_revision_content_snapshots_sequence"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("commit_id"),
        sa.UniqueConstraint(
            "revision_id", "sequence", name="uq_revision_content_snapshot_sequence"
        ),
    )
    op.create_index(
        "ix_revision_content_snapshots_revision_id",
        "revision_content_snapshots",
        ["revision_id"],
    )
    op.create_index(
        "ix_revision_content_snapshots_commit_id",
        "revision_content_snapshots",
        ["commit_id"],
        unique=True,
    )

    op.create_table(
        "revision_content_snapshot_artifacts",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="ck_snapshot_artifacts_sequence"),
        sa.ForeignKeyConstraint(["relation_id"], ["revision_artifact_relations.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["revision_content_snapshots.id"]),
        sa.PrimaryKeyConstraint("snapshot_id", "relation_id"),
    )


def downgrade() -> None:
    op.drop_table("revision_content_snapshot_artifacts")
    op.drop_index(
        "ix_revision_content_snapshots_commit_id", table_name="revision_content_snapshots"
    )
    op.drop_index(
        "ix_revision_content_snapshots_revision_id", table_name="revision_content_snapshots"
    )
    op.drop_table("revision_content_snapshots")
    op.drop_index("uq_revision_artifact_primary", table_name="revision_artifact_relations")
    op.drop_index(
        "ix_revision_artifact_relations_relation_type", table_name="revision_artifact_relations"
    )
    op.drop_index(
        "ix_revision_artifact_relations_artifact_id", table_name="revision_artifact_relations"
    )
    op.drop_index(
        "ix_revision_artifact_relations_revision_id", table_name="revision_artifact_relations"
    )
    op.drop_table("revision_artifact_relations")

    op.drop_index("ix_artifact_files_artifact_id", table_name="artifact_files")
    op.drop_constraint("ck_artifact_files_role", "artifact_files", type_="check")
    op.drop_constraint("ck_artifact_files_byte_size", "artifact_files", type_="check")
    op.drop_constraint("uq_artifact_files_reference", "artifact_files", type_="unique")
    op.drop_constraint("fk_artifact_files_artifact", "artifact_files", type_="foreignkey")
    op.drop_column("artifact_files", "role")
    op.drop_column("artifact_files", "reference_name")
    op.drop_column("artifact_files", "artifact_id")

    op.drop_index("ix_artifacts_state", table_name="artifacts")
    op.drop_index("ix_artifacts_source", table_name="artifacts")
    op.drop_index("ix_artifacts_classification", table_name="artifacts")
    op.drop_index("ix_artifacts_artifact_type", table_name="artifacts")
    op.drop_index("ix_artifacts_artifact_key", table_name="artifacts")
    op.drop_table("artifacts")
    op.rename_table("artifact_files", "artifacts")
