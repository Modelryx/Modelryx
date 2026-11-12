"""Derived processing results refresh a CAD version rather than creating one."""

import uuid

import pytest
from artifact_helpers import commit_item
from auth_helpers import AUTH as AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.db import SessionLocal
from modelryx.models import (
    Artifact,
    ArtifactFile,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
)
from modelryx.services.artifact_content import artifact_files_for_snapshot
from modelryx.services.processing import publish_worker_outputs
from sqlalchemy import func, select

client = TestClient(app)


def _source():
    committed = commit_item(
        client,
        item_number=f"PUBLICATION-{uuid.uuid4().hex}",
        name="Publication regression",
        files=[("source.FCStd", b"immutable-cad-source")],
    )
    return uuid.UUID(committed["revision_id"])


def _output(db, revision, source_id):
    artifact = Artifact(
        artifact_type="GLB",
        name="model.glb",
        classification="DERIVED",
        source="WORKER",
        derived_from_artifact_id=source_id,
        generator="publication-test",
        state="READY",
        created_by=revision.created_by,
    )
    db.add(artifact)
    db.flush()
    relation = RevisionArtifactRelation(
        revision_id=revision.id,
        artifact_id=artifact.id,
        relation_type="REPRESENTATION",
        sequence=100,
        is_primary=False,
        created_by=revision.created_by,
    )
    db.add(relation)
    file = ArtifactFile(
        artifact_id=artifact.id,
        revision_id=revision.id,
        reference_name="MODEL",
        role="PRIMARY",
        kind="GLB",
        storage_key=f"publication-tests/{uuid.uuid4()}/model.glb",
        original_filename="model.glb",
        mime_type="model/gltf-binary",
        byte_size=12,
        sha256="a" * 64,
        state="VERIFIED",
        required=False,
    )
    db.add(file)
    db.flush()
    return file, relation


def test_repeated_generation_preserves_source_version_and_replaces_only_derived_links():
    revision_id = _source()
    with SessionLocal() as db:
        revision = db.get(Revision, revision_id)
        snapshot = db.scalar(
            select(RevisionContentSnapshot).where(
                RevisionContentSnapshot.revision_id == revision_id,
            )
        )
        identity = (snapshot.id, snapshot.sequence, snapshot.commit_id, snapshot.created_at)
        source = artifact_files_for_snapshot(db, revision_id, snapshot.id)[0]
        source_hash = source.sha256
        source_link = db.scalar(
            select(RevisionContentSnapshotArtifact).where(
                RevisionContentSnapshotArtifact.snapshot_id == snapshot.id,
            )
        )
        source_membership = (source_link.relation_id, source_link.sequence)
        first, _ = _output(db, revision, source.artifact_id)
        assert publish_worker_outputs(db, revision, snapshot, [first]) is snapshot
        db.commit()
        second, _ = _output(db, revision, source.artifact_id)
        assert publish_worker_outputs(db, revision, snapshot, [second]) is snapshot
        db.commit()

        assert (
            db.scalar(
                select(func.count())
                .select_from(RevisionContentSnapshot)
                .where(
                    RevisionContentSnapshot.revision_id == revision_id,
                )
            )
            == 1
        )
        assert (snapshot.id, snapshot.sequence, snapshot.commit_id, snapshot.created_at) == identity
        assert {file.id for file in artifact_files_for_snapshot(db, revision_id, snapshot.id)} == {
            source.id,
            second.id,
        }
        assert source.sha256 == source_hash
        assert (
            db.get(RevisionContentSnapshotArtifact, (snapshot.id, source_membership[0])).sequence
            == (source_membership[1])
        )
        assert db.get(ArtifactFile, first.id).state == "VERIFIED"
        assert db.get(Artifact, first.artifact_id).state == "READY"


def test_publication_rejects_obsolete_source_without_changing_current_or_historical_links():
    revision_id = _source()
    with SessionLocal() as db:
        revision = db.get(Revision, revision_id)
        old = db.scalar(
            select(RevisionContentSnapshot).where(
                RevisionContentSnapshot.revision_id == revision_id,
            )
        )
        source = artifact_files_for_snapshot(db, revision_id, old.id)[0]
        current = RevisionContentSnapshot(
            revision_id=revision_id,
            sequence=2,
            commit_id=uuid.uuid4(),
            created_by=revision.created_by,
        )
        db.add(current)
        db.flush()
        output, _ = _output(db, revision, source.artifact_id)
        assert publish_worker_outputs(db, revision, old, [output]) is None
        assert [file.id for file in artifact_files_for_snapshot(db, revision_id, old.id)] == [
            source.id
        ]
        assert artifact_files_for_snapshot(db, revision_id, current.id) == []
        db.rollback()


def test_publication_cannot_replace_authoritative_cad_with_worker_outputs():
    revision_id = _source()
    with SessionLocal() as db:
        revision = db.get(Revision, revision_id)
        snapshot = db.scalar(
            select(RevisionContentSnapshot).where(
                RevisionContentSnapshot.revision_id == revision_id,
            )
        )
        source = artifact_files_for_snapshot(db, revision_id, snapshot.id)[0]
        with pytest.raises(RuntimeError, match="revision-scoped derived"):
            publish_worker_outputs(db, revision, snapshot, [source])
        assert [file.id for file in artifact_files_for_snapshot(db, revision_id, snapshot.id)] == [
            source.id,
        ]
        db.rollback()


def test_regeneration_keeps_previous_cad_version_preview_readable():
    revision_id = _source()
    with SessionLocal() as db:
        revision = db.get(Revision, revision_id)
        old = db.scalar(
            select(RevisionContentSnapshot).where(
                RevisionContentSnapshot.revision_id == revision_id,
            )
        )
        source = artifact_files_for_snapshot(db, revision_id, old.id)[0]
        first, _ = _output(db, revision, source.artifact_id)
        publish_worker_outputs(db, revision, old, [first])
        db.flush()
        old_links = list(
            db.execute(
                select(
                    RevisionContentSnapshotArtifact.relation_id,
                    RevisionContentSnapshotArtifact.sequence,
                ).where(RevisionContentSnapshotArtifact.snapshot_id == old.id)
            ).all()
        )
        current = RevisionContentSnapshot(
            revision_id=revision_id,
            sequence=2,
            commit_id=uuid.uuid4(),
            created_by=revision.created_by,
        )
        db.add(current)
        db.flush()
        for relation_id, sequence in old_links:
            db.add(
                RevisionContentSnapshotArtifact(
                    snapshot_id=current.id,
                    relation_id=relation_id,
                    sequence=sequence,
                )
            )
        db.flush()
        second, _ = _output(db, revision, source.artifact_id)
        assert publish_worker_outputs(db, revision, current, [second]) is current
        db.commit()
        assert {file.id for file in artifact_files_for_snapshot(db, revision_id, old.id)} == {
            source.id,
            first.id,
        }
        assert {file.id for file in artifact_files_for_snapshot(db, revision_id, current.id)} == {
            source.id,
            second.id,
        }
        assert (
            db.scalar(
                select(func.max(RevisionContentSnapshot.sequence)).where(
                    RevisionContentSnapshot.revision_id == revision_id,
                )
            )
            == 2
        )
