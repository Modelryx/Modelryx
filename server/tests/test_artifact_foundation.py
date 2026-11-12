import uuid

import pytest
from modelryx.api.artifact_commits import StagingCommitRequest
from modelryx.api.artifact_commits import router as commit_router
from modelryx.api.artifacts import router
from modelryx.api.staging import StagingProposalUpdate
from modelryx.api.staging import router as staging_router
from modelryx.models import (
    Artifact,
    ArtifactFile,
    BomLine,
    BomViewRevision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
)
from pydantic import ValidationError


def test_artifact_domain_uses_container_file_relation_and_snapshot_tables():
    assert Artifact.__tablename__ == "artifacts"
    assert ArtifactFile.__tablename__ == "artifact_files"
    assert RevisionArtifactRelation.__tablename__ == "revision_artifact_relations"
    assert RevisionContentSnapshot.__tablename__ == "revision_content_snapshots"
    assert RevisionContentSnapshotArtifact.__tablename__ == "revision_content_snapshot_artifacts"
    assert BomViewRevision.__tablename__ == "bom_view_revisions"
    assert BomLine.__tablename__ == "bom_lines"

    file_target = next(iter(ArtifactFile.__table__.c.artifact_id.foreign_keys)).target_fullname
    relation_target = next(
        iter(RevisionArtifactRelation.__table__.c.artifact_id.foreign_keys)
    ).target_fullname
    assert file_target == "artifacts.id"
    assert relation_target == "artifacts.id"


def test_artifact_read_routes_are_registered():
    routes = {(route.path, method) for route in router.routes for method in route.methods}
    assert ("/api/v1/item-revisions/{revision_id}/artifacts", "GET") in routes
    assert ("/api/v1/item-revisions/{revision_id}/content-snapshots", "GET") in routes
    assert ("/api/v1/artifacts/{artifact_id}", "GET") in routes
    assert ("/api/v1/artifacts/{artifact_id}/files", "GET") in routes
    assert (
        "/api/v1/artifacts/{artifact_id}/files/{file_id}/download",
        "GET",
    ) in routes
    staging_routes = {
        (route.path, method) for route in staging_router.routes for method in route.methods
    }
    assert ("/api/v1/staging/{staging_id}/proposal", "PUT") in staging_routes
    assert ("/api/v1/staging/{staging_id}/ready", "POST") in staging_routes
    commit_routes = {
        (route.path, method) for route in commit_router.routes for method in route.methods
    }
    assert ("/api/v1/uploads/{staging_id}/commit", "POST") in commit_routes


def test_create_item_staging_proposal_is_strict_and_versioned():
    staged_file_id = uuid.uuid4()
    proposal = StagingProposalUpdate.model_validate(
        {
            "expected_snapshot_version": 3,
            "operation": "CREATE_ITEM",
            "item": {
                "item_number": "PF-100",
                "name": "Housing",
                "item_type": "PART",
            },
            "revision": {"revision_code": "A"},
            "artifacts": [
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "artifact_type": "CAD_MODEL",
                    "name": "Housing",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": str(staged_file_id),
                            "reference_name": "MODEL",
                            "role": "PRIMARY",
                        }
                    ],
                }
            ],
        }
    )
    assert proposal.expected_snapshot_version == 3
    assert proposal.artifacts[0].files[0].staged_file_id == staged_file_id

    with pytest.raises(ValidationError, match="requires item and revision"):
        StagingProposalUpdate.model_validate(
            {"expected_snapshot_version": 1, "operation": "CREATE_ITEM"}
        )


def test_user_proposal_cannot_claim_worker_derived_artifact():
    with pytest.raises(ValidationError, match="worker-derived Artifacts"):
        StagingProposalUpdate.model_validate(
            {
                "expected_snapshot_version": 1,
                "operation": "CREATE_ITEM",
                "item": {"item_number": "PF-101", "name": "Part", "item_type": "PART"},
                "revision": {"revision_code": "A"},
                "artifacts": [
                    {
                        "client_artifact_id": str(uuid.uuid4()),
                        "artifact_type": "GLB",
                        "name": "Preview",
                        "classification": "DERIVED",
                        "relation_type": "REPRESENTATION",
                        "files": [
                            {
                                "staged_file_id": str(uuid.uuid4()),
                                "reference_name": "MODEL",
                                "role": "PRIMARY",
                            }
                        ],
                    }
                ],
            }
        )


def test_commit_request_requires_snapshot_and_idempotency_key():
    commit_id = uuid.uuid4()
    payload = StagingCommitRequest.model_validate(
        {"snapshot_version": 4, "commit_id": str(commit_id)}
    )
    assert payload.snapshot_version == 4
    assert payload.commit_id == commit_id
    with pytest.raises(ValidationError):
        StagingCommitRequest.model_validate({"snapshot_version": 0, "commit_id": str(commit_id)})


def test_assembly_proposal_can_resolve_a_staged_component_to_existing_revision():
    root_file_id = uuid.uuid4()
    child_file_id = uuid.uuid4()
    existing_revision_id = uuid.uuid4()
    proposal = StagingProposalUpdate.model_validate(
        {
            "expected_snapshot_version": 2,
            "operation": "CREATE_ITEM",
            "item": {"item_number": "ASM-100", "name": "Assembly", "item_type": "ASSEMBLY"},
            "revision": {"revision_code": "A"},
            "artifacts": [
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "artifact_type": "CAD_ASSEMBLY",
                    "name": "Assembly",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": str(root_file_id),
                            "reference_name": "MODEL",
                            "role": "PRIMARY",
                        }
                    ],
                }
            ],
            "identity_decisions": [
                {
                    "staged_file_id": str(child_file_id),
                    "decision": "USE_EXISTING",
                    "existing_revision_id": str(existing_revision_id),
                }
            ],
            "bom": [
                {
                    "parent_staged_file_id": str(root_file_id),
                    "child_staged_file_id": str(child_file_id),
                    "instance_name": "Bracket001",
                    "quantity": 2,
                }
            ],
        }
    )
    assert proposal.identity_decisions[0].existing_revision_id == existing_revision_id
    assert proposal.bom[0].quantity == 2
