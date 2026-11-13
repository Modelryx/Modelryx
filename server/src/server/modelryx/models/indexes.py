"""PostgreSQL expression, partial, and compound indexes for the current schema."""

from sqlalchemy import Index, text

from modelryx.core.db import Base


def _index(name: str, table_name: str, *expressions: str, **options) -> None:
    table = Base.metadata.tables[table_name]
    Index(
        name,
        *(table.c[value] if value in table.c else text(value) for value in expressions),
        _table=table,
        **options,
    )


_index(
    "ix_ai_documents_content_fts",
    "ai_documents",
    "to_tsvector('english'::regconfig, content)",
    postgresql_using="gin",
)

_index(
    "ix_ai_documents_embedding_hnsw",
    "ai_documents",
    "embedding",
    postgresql_ops={"embedding": "vector_cosine_ops"},
    postgresql_using="hnsw",
)

_index(
    "ix_artifacts_filename_search_en",
    "artifact_files",
    ("to_tsvector('english'::regconfig, COALESCE(original_filename, ''::character varying)::text)"),
    postgresql_using="gin",
)

_index(
    "ix_artifacts_verified_native_sha256",
    "artifact_files",
    "sha256",
    "byte_size",
    postgresql_where=text(
        "(((kind)::text = 'NATIVE'::text) AND ((state)::text = 'VERIFIED'::text))"
    ),
)

_index(
    "uq_attachment_checkouts_item_create",
    "attachment_checkouts",
    "item_id",
    unique=True,
    postgresql_where=text("(attachment_id IS NULL)"),
)

_index(
    "uq_cad_sessions_active_revision",
    "cad_sessions",
    "revision_id",
    unique=True,
    postgresql_where=text(
        "status IN ('CREATED', 'OPENING', 'READY', 'EXECUTING', 'VERIFYING', 'READY_TO_REVIEW')"
    ),
)

_index(
    "uq_checkouts_unversioned_item",
    "checkouts",
    "part_id",
    unique=True,
    postgresql_where=text("(revision_id IS NULL)"),
)

_index(
    "uq_item_number_templates_selected",
    "item_number_templates",
    "(1)",
    unique=True,
    postgresql_where=text("is_selected"),
)

_index(
    "ix_knowledge_chunks_content_fts",
    "knowledge_chunks",
    "to_tsvector('english'::regconfig, content)",
    postgresql_using="gin",
)

_index(
    "ix_knowledge_chunks_embedding_hnsw",
    "knowledge_chunks",
    "embedding",
    postgresql_ops={"embedding": "vector_cosine_ops"},
    postgresql_using="hnsw",
)

_index(
    "ix_metadata_extractions_chamfer_count",
    "metadata_extractions",
    "chamfer_count",
)

_index(
    "ix_metadata_extractions_density_kg_m3",
    "metadata_extractions",
    "density_kg_m3",
)

_index(
    "ix_metadata_extractions_drawing_count",
    "metadata_extractions",
    "drawing_count",
)

_index(
    "ix_metadata_extractions_engineering_search_en",
    "metadata_extractions",
    ("to_tsvector('english'::regconfig, (metadata_json::text || ' '::text) || derived_json::text)"),
    postgresql_using="gin",
)

_index(
    "ix_metadata_extractions_extrusion_count",
    "metadata_extractions",
    "extrusion_count",
)

_index(
    "ix_metadata_extractions_fillet_count",
    "metadata_extractions",
    "fillet_count",
)

_index(
    "ix_metadata_extractions_hole_count",
    "metadata_extractions",
    "hole_count",
)

_index(
    "ix_metadata_extractions_hole_diameter_max_mm",
    "metadata_extractions",
    "hole_diameter_max_mm",
)

_index(
    "ix_metadata_extractions_hole_diameter_min_mm",
    "metadata_extractions",
    "hole_diameter_min_mm",
)

_index(
    "ix_metadata_extractions_instance_count",
    "metadata_extractions",
    "instance_count",
)

_index(
    "ix_metadata_extractions_pattern_count",
    "metadata_extractions",
    "pattern_count",
)

_index(
    "ix_metadata_extractions_pocket_count",
    "metadata_extractions",
    "pocket_count",
)

_index(
    "ix_metadata_extractions_revolution_count",
    "metadata_extractions",
    "revolution_count",
)

_index(
    "ix_metadata_extractions_thread_count",
    "metadata_extractions",
    "thread_count",
)

_index(
    "ix_metadata_extractions_tolerance_count",
    "metadata_extractions",
    "tolerance_count",
)

_index(
    "ix_parts_engineering_search_en",
    "parts",
    (
        "(((setweight(to_tsvector('english'::regconfig, COALESCE(part_number, ''::ch"
        "aracter varying)::text), 'A'::\"char\") || setweight(to_tsvector('english'::r"
        "egconfig, COALESCE(name, ''::character varying)::text), 'A'::\"char\")) || se"
        "tweight(to_tsvector('english'::regconfig, COALESCE(category, ''::character "
        "varying)::text), 'B'::\"char\")) || setweight(to_tsvector('english'::regconfi"
        "g, COALESCE(description, ''::text)), 'B'::\"char\"))"
    ),
    postgresql_using="gin",
)

_index(
    "ix_parts_search_vector",
    "parts",
    (
        "to_tsvector('simple'::regconfig, (((((part_number::text || ' '::text) || na"
        "me::text) || ' '::text) || COALESCE(description, ''::text)) || ' '::text) |"
        "| COALESCE(category, ''::character varying)::text)"
    ),
    postgresql_using="gin",
)

_index(
    "uq_parts_part_number_casefold",
    "parts",
    "lower(part_number::text)",
    unique=True,
)

_index(
    "uq_revision_approval_requests_pending",
    "revision_approval_requests",
    "revision_id",
    unique=True,
    postgresql_where=text("((status)::text = 'PENDING'::text)"),
)

_index(
    "ix_revisions_engineering_search_en",
    "revisions",
    "to_tsvector('english'::regconfig, COALESCE(search_text, ''::text))",
    postgresql_using="gin",
)

_index(
    "ix_revisions_search_vector",
    "revisions",
    "to_tsvector('simple'::regconfig, search_text)",
    postgresql_using="gin",
)

_index(
    "ix_ui_saved_views_scope_entity",
    "ui_saved_views",
    "scope",
    "entity_type",
)
