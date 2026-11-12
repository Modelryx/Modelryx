import uuid

from modelryx.core.db import SessionLocal
from modelryx.models import (
    Artifact,
    ArtifactFile,
    Part,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    User,
    WorkingVersion,
)
from modelryx.services.artifact_content import (
    current_artifact_files,
    revisions_with_current_artifact_types,
)
from sqlalchemy import select

AUTH = {"X-User": "local-development-user", "X-Role": "administrator"}


def _artifact_relation(
    db,
    *,
    revision: Revision,
    user: User,
    version: WorkingVersion,
    artifact_type: str,
    filename: str,
) -> RevisionArtifactRelation:
    artifact = Artifact(
        artifact_type=artifact_type,
        name=filename,
        classification="AUTHORITATIVE",
        source="USER",
        state="READY",
        created_by=user.id,
    )
    db.add(artifact)
    db.flush()
    relation = RevisionArtifactRelation(
        revision_id=revision.id,
        artifact_id=artifact.id,
        relation_type="DRAWING" if artifact_type in {"DRAWING", "PDF"} else "SPECIFICATION",
        created_by=user.id,
    )
    db.add(relation)
    db.flush()
    db.add(
        ArtifactFile(
            artifact_id=artifact.id,
            reference_name="PRIMARY",
            role="PRIMARY",
            revision_id=revision.id,
            working_version_id=version.id,
            kind=artifact_type,
            storage_key=f"artifact-content/{uuid.uuid4()}/{filename}",
            original_filename=filename,
            mime_type="application/octet-stream",
            byte_size=1,
            sha256="0" * 64,
            state="VERIFIED",
            required=False,
        )
    )
    return relation


def test_current_artifact_type_query_excludes_drawing_removed_by_latest_snapshot() -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        assert user is not None
        item = Part(
            part_number=f"PF-ARTIFACT-CONTENT-{uuid.uuid4()}",
            name="Artifact content test",
            part_type="PART",
            created_by=user.id,
        )
        db.add(item)
        db.flush()
        revision = Revision(
            part_id=item.id,
            revision_code="A",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={},
            state="READY",
            created_by=user.id,
        )
        db.add(revision)
        db.flush()
        version = WorkingVersion(
            revision_id=revision.id,
            version_number=1,
            state="COMMITTED",
            metadata_json={},
            created_by=user.id,
        )
        db.add(version)
        db.flush()
        revision.current_working_version_id = version.id
        drawing = _artifact_relation(
            db,
            revision=revision,
            user=user,
            version=version,
            artifact_type="DRAWING",
            filename="old-drawing.pdf",
        )
        model = _artifact_relation(
            db,
            revision=revision,
            user=user,
            version=version,
            artifact_type="CAD_MODEL",
            filename="current-model.FCStd",
        )
        for sequence, relation in ((1, drawing), (2, model)):
            snapshot = RevisionContentSnapshot(
                revision_id=revision.id,
                sequence=sequence,
                commit_id=uuid.uuid4(),
                created_by=user.id,
            )
            db.add(snapshot)
            db.flush()
            db.add(
                RevisionContentSnapshotArtifact(
                    snapshot_id=snapshot.id,
                    relation_id=relation.id,
                    sequence=0,
                )
            )
        db.commit()

        assert revisions_with_current_artifact_types(db, {revision.id}, {"DRAWING", "PDF"}) == set()
        current_cad = current_artifact_files(
            db, revision.id, artifact_types={"CAD_MODEL", "CAD_ASSEMBLY"}
        )
        assert [file.original_filename for file in current_cad] == ["current-model.FCStd"]
