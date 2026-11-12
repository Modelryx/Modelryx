import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.cad.sessions import file_sha256
from modelryx.core.config import Settings
from modelryx.metadata import MetadataService
from modelryx.models import (
    Artifact,
    ArtifactFile,
    CADCommitPlan,
    CADExecution,
    CADOperationPlan,
    CADSession,
    Checkout,
    Item,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
)
from modelryx.services.artifact_content import (
    current_artifact_files,
    latest_content_snapshot_id,
)
from modelryx.services.storage import LocalStorageProvider


class StaleCADCommitError(ValueError):
    """The immutable commit proposal no longer matches authoritative state."""


def cleanup_pending_commit(db: Session, settings: Settings) -> None:
    keys = db.info.pop("cad_commit_created_keys", [])
    storage = LocalStorageProvider(settings.storage_root)
    for storage_key in keys:
        storage.delete(storage_key)


def next_revision_code(current: str, existing: set[str]) -> str:
    if current.isdigit():
        width = len(current)
        candidate = str(int(current) + 1).zfill(width)
    elif current.isalpha() and current.isupper():
        number = 0
        for character in current:
            number = number * 26 + ord(character) - 64
        number += 1
        pieces = []
        while number:
            number, remainder = divmod(number - 1, 26)
            pieces.append(chr(65 + remainder))
        candidate = "".join(reversed(pieces))
    else:
        candidate = f"{current}.1"
    base = candidate
    suffix = 1
    while candidate.casefold() in {value.casefold() for value in existing}:
        candidate = f"{base}.{suffix}"
        suffix += 1
    return candidate


def commit_state_token(
    db: Session,
    session: CADSession,
    execution: CADExecution,
    source_revision: Revision,
    item: Item,
) -> str:
    payload = {
        "session_id": str(session.id),
        "lock_version": session.lock_version,
        "source_revision_id": str(source_revision.id),
        "source_snapshot_id": str(latest_content_snapshot_id(db, source_revision.id)),
        "item_current_revision_id": str(item.current_revision_id),
        "execution_id": str(execution.id),
        "output_sha256": execution.result_json.get("output_sha256"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verified_execution(db: Session, session: CADSession) -> tuple[CADOperationPlan, CADExecution]:
    plan = db.scalar(
        select(CADOperationPlan)
        .where(CADOperationPlan.session_id == session.id, CADOperationPlan.status == "SUCCEEDED")
        .order_by(CADOperationPlan.created_at.desc())
    )
    execution = (
        db.scalar(
            select(CADExecution).where(
                CADExecution.plan_id == plan.id, CADExecution.status == "SUCCEEDED"
            )
        )
        if plan
        else None
    )
    if plan is None or execution is None:
        raise ValueError("Verified CAD execution is unavailable")
    validation = execution.result_json.get("validation", {})
    if validation.get("passed") is not True:
        raise ValueError("CAD result validation is not acceptable")
    return plan, execution


def _result_path(workspace: Path, relative: str) -> Path:
    value = (workspace / relative).resolve()
    if not value.is_relative_to(workspace) or not value.is_file():
        raise ValueError("CAD result artifact is unavailable or outside the session workspace")
    return value


def commit_verified_result(
    db: Session,
    settings: Settings,
    commit_plan: CADCommitPlan,
    *,
    actor_id: uuid.UUID,
) -> Revision:
    session = db.scalar(
        select(CADSession).where(CADSession.id == commit_plan.session_id).with_for_update()
    )
    if session is None or session.status != "READY_TO_REVIEW":
        raise ValueError("CAD session is not ready to commit")
    _, execution = verified_execution(db, session)
    source_revision = db.scalar(
        select(Revision).where(Revision.id == session.revision_id).with_for_update()
    )
    if source_revision is None:
        raise ValueError("Source revision is unavailable")
    item = db.scalar(select(Item).where(Item.id == source_revision.part_id).with_for_update())
    if item is None:
        raise ValueError("Source item is unavailable")
    source_artifact = db.get(ArtifactFile, session.source_artifact_id)
    current_file_ids = {file.id for file in current_artifact_files(db, source_revision.id)}
    if source_artifact is None or source_artifact.id not in current_file_ids:
        raise ValueError("Source revision changed after the CAD session was created")
    checkout = db.scalar(
        select(Checkout)
        .where(
            Checkout.part_id == item.id,
            Checkout.revision_id == source_revision.id,
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    latest_snapshot_id = db.scalar(
        select(RevisionContentSnapshot.id)
        .where(RevisionContentSnapshot.revision_id == source_revision.id)
        .order_by(RevisionContentSnapshot.sequence.desc())
    )
    if (
        checkout is None
        or checkout.expires_at <= now
        or checkout.owner_id != actor_id
        or checkout.base_snapshot_id != latest_snapshot_id
    ):
        raise ValueError("An owned checkout at the source content snapshot is required")
    if commit_plan.state_token != commit_state_token(
        db, session, execution, source_revision, item
    ):
        commit_plan.status = "STALE"
        commit_plan.decided_at = now
        raise StaleCADCommitError("CAD commit proposal is stale")
    operation = commit_plan.preview_json.get("operation", "NEW_REVISION")
    if operation not in {"NEW_VERSION", "NEW_REVISION"}:
        raise ValueError("CAD commit operation is invalid")
    if operation == "NEW_REVISION":
        existing_codes = set(
            db.scalars(select(Revision.revision_code).where(Revision.part_id == item.id))
        )
        if commit_plan.revision_code.casefold() in {code.casefold() for code in existing_codes}:
            commit_plan.status = "STALE"
            commit_plan.decided_at = now
            raise StaleCADCommitError("Selected revision code is no longer available")
    workspace_root = (settings.storage_root.resolve() / "cad-sessions").resolve()
    workspace = (settings.storage_root.resolve() / session.workspace_key).resolve()
    if not workspace.is_relative_to(workspace_root):
        raise ValueError("CAD session workspace is invalid")
    artifacts = execution.result_json.get("artifacts", {})
    hashes = execution.result_json.get("artifact_sha256", {})
    native_filename = Path(source_artifact.original_filename).name
    if not native_filename or not Path(native_filename).suffix:
        raise ValueError("CAD session source artifact has an invalid native filename")
    specifications = (
        ("native", "NATIVE", native_filename, "application/octet-stream", True),
        ("glb", "GLB", "model.glb", "model/gltf-binary", False),
        ("preview", "METADATA", "model-preview.json", "application/json", False),
        ("raw_metadata", "OTHER", "cad-raw-metadata.json", "application/json", False),
        ("part_metadata", "OTHER", "part-metadata.json", "application/json", False),
    )
    resolved = {}
    for key, *_ in specifications:
        source = _result_path(workspace, str(artifacts.get(key, "")))
        if file_sha256(source) != hashes.get(key):
            raise ValueError(f"CAD result artifact checksum failed: {key}")
        resolved[key] = source
    preview = json.loads(resolved["preview"].read_text(encoding="utf-8"))
    part_metadata = json.loads(resolved["part_metadata"].read_text(encoding="utf-8"))
    result_metadata = {
        **source_revision.metadata_json,
        "cad_commit": {
            "source_revision_id": str(source_revision.id),
            "session_id": str(session.id),
            "execution_id": str(execution.id),
            "operation": operation,
        },
    }
    if operation == "NEW_REVISION":
        revision = Revision(
            part_id=item.id,
            revision_code=commit_plan.revision_code,
            cad_system=source_revision.cad_system,
            native_format=source_revision.native_format,
            description=commit_plan.preview_json.get("description"),
            metadata_json=result_metadata,
            derived_metadata_json={**preview, "partMetadata": part_metadata},
            state="READY",
            created_by=actor_id,
            committed_at=now,
        )
        db.add(revision)
        db.flush()
    else:
        revision = source_revision
        revision.metadata_json = result_metadata
        revision.derived_metadata_json = {**preview, "partMetadata": part_metadata}
        revision.committed_at = now
    storage = LocalStorageProvider(settings.storage_root)
    created_keys = []
    persisted = {}
    relations: list[RevisionArtifactRelation] = []
    try:
        for key, kind, filename, mime_type, required in specifications:
            artifact_id = uuid.uuid4()
            storage_key = (
                f"parts/{item.id}/revisions/{revision.id}/cad-commits/{commit_plan.id}/artifacts/"
                f"{artifact_id}/{filename}"
            )
            destination = storage.create_upload(storage_key)
            temporary = destination.with_suffix(destination.suffix + ".committing")
            shutil.copyfile(resolved[key], temporary)
            os.replace(temporary, destination)
            created_keys.append(storage_key)
            container = Artifact(
                artifact_type={
                    "NATIVE": "CAD_MODEL",
                    "GLB": "GLB",
                    "METADATA": "METADATA",
                    "OTHER": "METADATA",
                }[kind],
                name=filename,
                classification="AUTHORITATIVE" if kind == "NATIVE" else "DERIVED",
                source="USER" if kind == "NATIVE" else "WORKER",
                derived_from_artifact_id=None,
                generator=None if kind == "NATIVE" else "modelryx-cad-session",
                state="READY",
                created_by=actor_id,
            )
            if kind != "NATIVE":
                container.derived_from_artifact_id = persisted["native"].artifact_id
            db.add(container)
            db.flush()
            relation = RevisionArtifactRelation(
                revision_id=revision.id,
                artifact_id=container.id,
                relation_type="SPECIFICATION" if kind == "NATIVE" else "REPRESENTATION",
                sequence=len(relations),
                is_primary=kind == "NATIVE",
                created_by=actor_id,
            )
            db.add(relation)
            relations.append(relation)
            artifact = ArtifactFile(
                id=artifact_id,
                artifact_id=container.id,
                reference_name="MODEL" if kind in {"NATIVE", "GLB"} else "CONTENT",
                role="PRIMARY",
                revision_id=revision.id,
                kind=kind,
                storage_key=storage_key,
                original_filename=filename,
                mime_type=mime_type,
                byte_size=destination.stat().st_size,
                sha256=hashes[key],
                state="VERIFIED",
                required=required,
            )
            db.add(artifact)
            persisted[key] = artifact
        db.flush()
        previous_snapshot = db.scalar(
            select(RevisionContentSnapshot)
            .where(RevisionContentSnapshot.revision_id == revision.id)
            .order_by(RevisionContentSnapshot.sequence.desc())
        )
        snapshot = RevisionContentSnapshot(
            revision_id=revision.id,
            sequence=(previous_snapshot.sequence if previous_snapshot else 0) + 1,
            commit_id=commit_plan.id,
            comment="Controlled CAD session commit",
            created_by=actor_id,
        )
        db.add(snapshot)
        db.flush()
        for sequence, relation in enumerate(relations):
            db.add(
                RevisionContentSnapshotArtifact(
                    snapshot_id=snapshot.id,
                    relation_id=relation.id,
                    sequence=sequence,
                )
            )
        MetadataService(db).ingest(
            revision=revision,
            snapshot=snapshot,
            raw_artifact=persisted["raw_metadata"],
            metadata=part_metadata,
        )
        revision.search_text = json.dumps(
            {"metadata": revision.metadata_json, "derived": revision.derived_metadata_json},
            sort_keys=True,
        )
        if operation == "NEW_REVISION":
            item.current_revision_id = revision.id
        session.status = "COMMITTED"
        session.closed_at = now
        session.lock_version += 1
        commit_plan.status = "COMMITTED"
        commit_plan.committed_revision_id = revision.id
        commit_plan.confirmed_by = actor_id
        commit_plan.decided_at = now
        db.delete(checkout)
        db.flush()
        db.info["cad_commit_created_keys"] = created_keys
    except Exception:
        for storage_key in created_keys:
            storage.delete(storage_key)
        raise
    return revision
