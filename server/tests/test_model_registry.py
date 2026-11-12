from modelryx import models
from modelryx.core.db import Base
from modelryx.models import entities
from sqlalchemy.orm import configure_mappers

EXPECTED_TABLES = {
    "additional_file_stages",
    "ai_action_executions",
    "ai_action_proposals",
    "ai_conversations",
    "ai_documents",
    "ai_messages",
    "ai_search_feedback",
    "ai_search_sessions",
    "ai_tool_executions",
    "application_settings",
    "approval_reviewer_delegations",
    "approval_reviewer_group_members",
    "approval_reviewer_groups",
    "artifact_files",
    "artifacts",
    "attachment_checkouts",
    "attachments",
    "audit_log",
    "bom_lines",
    "bom_view_revisions",
    "cad_commit_plans",
    "cad_executions",
    "cad_operation_plans",
    "cad_sessions",
    "checkouts",
    "content_blobs",
    "download_bundles",
    "duplicate_candidates",
    "duplicate_checks",
    "duplicate_feedback",
    "favorites",
    "item_number_templates",
    "knowledge_chunks",
    "knowledge_index_jobs",
    "knowledge_relations",
    "knowledge_sources",
    "metadata_extractions",
    "parts",
    "processing_jobs",
    "recently_viewed_items",
    "revision_approval_decisions",
    "revision_approval_requests",
    "revision_artifact_relations",
    "revision_comparison_changes",
    "revision_comparison_domains",
    "revision_comparison_explanations",
    "revision_comparisons",
    "revision_content_snapshot_artifacts",
    "revision_content_snapshots",
    "revision_relationships",
    "revisions",
    "staging_files",
    "staging_sessions",
    "ui_saved_views",
    "user_notifications",
    "user_roles",
    "users",
    "validation_ai_analyses",
    "validation_finding_actions",
    "validation_findings",
    "validation_runs",
    "worker_heartbeats",
}


def test_model_package_registers_every_table() -> None:
    configure_mappers()
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_legacy_entities_module_reexports_public_models() -> None:
    assert entities.Item is models.Item
    assert entities.Part is models.Item
    assert entities.ArtifactFile is models.ArtifactFile
    assert entities.CADSession is models.CADSession
