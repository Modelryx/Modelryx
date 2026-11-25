import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.api.parts import require_engineer
from modelryx.api.staging import StagingProposalUpdate
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.duplicate_detection.search_document import build_semantic_text
from modelryx.metadata.service import MetadataService
from modelryx.models import (
    Artifact,
    ArtifactFile,
    Attachment,
    BomLine,
    BomViewRevision,
    Checkout,
    ContentBlob,
    Item,
    ProcessingJob,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    StagingFile,
    StagingSession,
)
from modelryx.services.storage import LocalStorageProvider

router = APIRouter(prefix="/api/v1/uploads", tags=["artifact-commits"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


class StagingCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_version: int = Field(ge=1)
    commit_id: uuid.UUID
    child_staging_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


class StagingCommitResponse(BaseModel):
    staging_id: uuid.UUID
    commit_id: uuid.UUID
    snapshot_version: int
    item_id: uuid.UUID
    revision_id: uuid.UUID
    content_snapshot_id: uuid.UUID
    artifact_ids: list[uuid.UUID]
    created_item_ids: list[uuid.UUID] = Field(default_factory=list)
    created_revision_ids: list[uuid.UUID] = Field(default_factory=list)
    reused_revision_ids: list[uuid.UUID] = Field(default_factory=list)
    bom_view_revision_id: uuid.UUID | None = None
    reused: bool = False


def _legacy_kind(artifact_type: str) -> str:
    return {
        "CAD_MODEL": "NATIVE",
        "CAD_ASSEMBLY": "NATIVE",
        "DRAWING": "DRAWING",
        "PDF": "PDF",
        "STEP": "STEP",
        "GLB": "GLB",
        "METADATA": "METADATA",
    }.get(artifact_type, "OTHER")


def _content_blob_for_staged_file(
    db: Session, source: StagingFile, storage: LocalStorageProvider
) -> ContentBlob:
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": f"{source.sha256}:{source.byte_size}"},
    )
    blob = db.scalar(
        select(ContentBlob).where(
            ContentBlob.sha256 == source.sha256,
            ContentBlob.byte_size == source.byte_size,
            ContentBlob.state == "VERIFIED",
        )
    )
    if blob is not None:
        if not storage.exists(blob.storage_key):
            raise HTTPException(status_code=409, detail="Reusable attachment content is missing")
        return blob
    blob_key = f"blobs/{source.sha256[:2]}/{source.sha256}-{source.byte_size}"
    destination = storage.create_upload(blob_key)
    shutil.copy2(storage.path_for(source.storage_key), destination)
    blob = ContentBlob(
        sha256=source.sha256,
        byte_size=source.byte_size,
        storage_key=blob_key,
        state="VERIFIED",
    )
    db.add(blob)
    db.flush()
    return blob


def _native_processing_type(filename: str, cad_system: str, db: Session | None = None) -> str | None:
    from modelryx.services.cad_routing import processing_type_for_source
    from modelryx.services.external_workers import AmbiguousWorkerError

    try:
        return processing_type_for_source(filename, cad_system, db)
    except AmbiguousWorkerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _stored_result(staging: StagingSession) -> StagingCommitResponse:
    try:
        return StagingCommitResponse.model_validate(staging.result_json)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Stored commit result is invalid") from exc


def _require_current_assembly_analysis(
    staging: StagingSession,
    proposal: StagingProposalUpdate,
) -> None:
    """Recheck assembly-analysis invariants inside the commit transaction."""
    if proposal.item is None or proposal.item.item_type != "ASSEMBLY":
        return
    replaces_primary = any(
        artifact.existing_relation_id is None
        and artifact.is_primary
        and artifact.artifact_type == "CAD_ASSEMBLY"
        and artifact.owner_staged_file_id is None
        for artifact in proposal.artifacts
    )
    if not replaces_primary:
        return
    analysis = dict((staging.result_json or {}).get("assembly_analysis") or {})
    if (
        not analysis
        or analysis.get("snapshot_version") != staging.snapshot_version
        or not analysis.get("package_staged")
    ):
        raise HTTPException(
            status_code=409,
            detail="A current persisted assembly analysis is required at commit time",
        )
    reconciliation = list(analysis.get("reconciliation") or [])
    if any(entry.get("status") in {"MISSING", "CONFLICT"} for entry in reconciliation):
        raise HTTPException(
            status_code=422, detail="Assembly analysis contains blocking references"
        )
    decisions = dict(analysis.get("identity_decisions") or {})
    unresolved = [
        entry
        for entry in reconciliation
        if entry.get("status") in {"ADDED", "RELINKED"}
        and (
            f"{str(entry.get('parent_logical_path') or '').casefold()}::"
            f"{str(entry.get('occurrence_key') or '').casefold()}"
        )
        not in decisions
    ]
    if unresolved:
        raise HTTPException(status_code=422, detail="Assembly analysis has unresolved identities")
    root_path = str((analysis.get("report") or {}).get("root_logical_path") or "")
    expected_occurrences = {
        str(entry.get("occurrence_key") or "").casefold()
        for entry in reconciliation
        if entry.get("depth") == 0
        and str(entry.get("parent_logical_path") or "").casefold() == root_path.casefold()
        and entry.get("status") != "REMOVED"
    }
    root_revision_id = (
        proposal.destination.revision_id if proposal.destination is not None else None
    )
    proposed_occurrences = {
        line.instance_name.casefold()
        for line in proposal.bom
        if line.parent_staged_file_id is None
        and (line.parent_revision_id is None or line.parent_revision_id == root_revision_id)
    }
    if proposed_occurrences != expected_occurrences:
        raise HTTPException(
            status_code=409,
            detail="The proposed BOM differs from the persisted assembly analysis",
        )


def _commit_edit(
    db: Session,
    staging: StagingSession,
    proposal: StagingProposalUpdate,
    payload: StagingCommitRequest,
    actor_id: uuid.UUID,
    now: datetime,
    settings: Config,
    force_reprocess: bool = False,
) -> StagingCommitResponse:
    assert proposal.destination is not None
    assert proposal.item is not None
    assert proposal.revision is not None
    revision = db.scalar(
        select(Revision).where(Revision.id == proposal.destination.revision_id).with_for_update()
    )
    item = db.get(Item, proposal.destination.item_id)
    checkout = db.scalar(
        select(Checkout).where(
            Checkout.revision_id == proposal.destination.revision_id,
            Checkout.owner_id == actor_id,
        )
    )
    latest_snapshot = db.scalar(
        select(RevisionContentSnapshot)
        .where(RevisionContentSnapshot.revision_id == proposal.destination.revision_id)
        .order_by(RevisionContentSnapshot.sequence.desc())
    )
    if (
        revision is None
        or item is None
        or revision.part_id != item.id
        or checkout is None
        or latest_snapshot is None
        or checkout.base_snapshot_id != latest_snapshot.id
        or latest_snapshot.id != proposal.destination.base_snapshot_id
    ):
        raise HTTPException(
            status_code=409,
            detail="The checked-out revision changed; cancel staging and open Edit again",
        )
    if proposal.revision.revision_code != revision.revision_code:
        raise HTTPException(status_code=422, detail="Edit cannot change the revision code")
    if revision.lifecycle_state != "WORKING":
        raise HTTPException(
            status_code=409,
            detail="Released revisions are immutable; create a new Item Revision to make changes",
        )
    duplicate = db.scalar(
        select(Item.id).where(
            func.lower(Item.part_number) == proposal.item.item_number.strip().lower(),
            Item.id != item.id,
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Item number already exists")

    base_relation_ids = set(
        db.scalars(
            select(RevisionArtifactRelation.id).where(
                RevisionArtifactRelation.revision_id == revision.id
            )
        )
    )
    retained_ids = [
        artifact.existing_relation_id
        for artifact in proposal.artifacts
        if artifact.existing_relation_id is not None
    ]
    if len(retained_ids) != len(set(retained_ids)) or not set(retained_ids) <= base_relation_ids:
        raise HTTPException(status_code=409, detail="The retained Artifact manifest is stale")
    linked_artifact_ids = [
        artifact.existing_artifact_id
        for artifact in proposal.artifacts
        if artifact.existing_artifact_id is not None
    ]
    if len(linked_artifact_ids) != len(set(linked_artifact_ids)):
        raise HTTPException(status_code=422, detail="An Additional File version is linked twice")
    for linked_id in linked_artifact_ids:
        linked = db.scalar(
            select(Artifact)
            .join(Attachment, Attachment.id == Artifact.attachment_id)
            .where(
                Artifact.id == linked_id,
                Artifact.state == "READY",
                Attachment.item_id == item.id,
            )
        )
        verified_file = db.scalar(
            select(ArtifactFile.id).where(
                ArtifactFile.artifact_id == linked_id,
                ArtifactFile.state == "VERIFIED",
            )
        )
        if linked is None or verified_file is None:
            raise HTTPException(status_code=409, detail="A linked Additional File version changed")
    primary_keys = [
        artifact.relation_type
        for artifact in proposal.artifacts
        if artifact.is_primary and artifact.owner_staged_file_id is None
    ]
    if len(primary_keys) != len(set(primary_keys)):
        raise HTTPException(status_code=422, detail="Only one primary Artifact is allowed per type")

    staged_files = {
        row.id: row
        for row in db.scalars(select(StagingFile).where(StagingFile.staging_id == staging.id))
    }
    selected_ids = {
        file.staged_file_id
        for artifact in proposal.artifacts
        if artifact.existing_relation_id is None
        for file in artifact.files
    }
    if selected_ids != set(staged_files) or any(
        staged_files[file_id].state != "VERIFIED" for file_id in selected_ids
    ):
        raise HTTPException(status_code=409, detail="Staged files changed after readiness checks")

    revision_by_file: dict[uuid.UUID, Revision] = {}
    created_items: list[Item] = []
    created_revisions: list[Revision] = []
    reused_revision_ids: set[uuid.UUID] = set()
    for decision in proposal.identity_decisions:
        source = staged_files.get(decision.staged_file_id)
        if source is None:
            raise HTTPException(status_code=422, detail="Identity decision file is unavailable")
        if decision.decision == "USE_EXISTING":
            existing = db.get(Revision, decision.existing_revision_id)
            matching = db.scalar(
                select(ArtifactFile.id).where(
                    ArtifactFile.revision_id == decision.existing_revision_id,
                    ArtifactFile.sha256 == source.sha256,
                    ArtifactFile.state == "VERIFIED",
                )
            )
            if (
                existing is None
                or existing.state not in {"READY", "READY_WITH_WARNINGS"}
                or matching is None
            ):
                raise HTTPException(status_code=409, detail="A selected duplicate changed")
            revision_by_file[source.id] = existing
            reused_revision_ids.add(existing.id)
            continue
        if decision.item is None or decision.revision is None:
            raise HTTPException(status_code=422, detail="A new component identity is incomplete")
        if (
            db.scalar(
                select(Item.id).where(
                    func.lower(Item.part_number) == decision.item.item_number.strip().casefold()
                )
            )
            is not None
        ):
            raise HTTPException(
                status_code=409, detail="A proposed component Item number already exists"
            )
        new_item = Item(
            part_number=decision.item.item_number.strip(),
            name=decision.item.name.strip(),
            description=decision.item.description,
            category=decision.item.category,
            part_type=decision.item.item_type,
            created_by=actor_id,
        )
        db.add(new_item)
        db.flush()
        new_revision = Revision(
            part_id=new_item.id,
            revision_code=decision.revision.revision_code,
            description=decision.revision.description,
            cad_system=decision.revision.cad_system,
            native_format=decision.revision.native_format,
            metadata_json={},
            search_text="",
            package_sha256=source.sha256,
            state="READY",
            created_by=actor_id,
            committed_at=now,
        )
        db.add(new_revision)
        db.flush()
        new_item.current_revision_id = new_revision.id
        created_items.append(new_item)
        created_revisions.append(new_revision)
        revision_by_file[source.id] = new_revision

    relation_ids = list(retained_ids)
    child_relations: dict[uuid.UUID, list[RevisionArtifactRelation]] = {
        child.id: [] for child in created_revisions
    }
    artifact_ids: list[uuid.UUID] = []
    new_primary_file: ArtifactFile | None = None
    child_primary_files: dict[uuid.UUID, ArtifactFile] = {}
    used_storage_keys: set[str] = set()
    storage = LocalStorageProvider(settings.storage_root)
    for proposed in proposal.artifacts:
        if proposed.existing_relation_id is not None:
            continue
        owner_revision = (
            revision
            if proposed.owner_staged_file_id is None
            else revision_by_file.get(proposed.owner_staged_file_id)
        )
        if owner_revision is None or (
            owner_revision.id != revision.id and owner_revision.id not in child_relations
        ):
            raise HTTPException(status_code=422, detail="Artifact owner is unresolved")
        if proposed.existing_artifact_id is not None:
            relation = db.scalar(
                select(RevisionArtifactRelation).where(
                    RevisionArtifactRelation.revision_id == owner_revision.id,
                    RevisionArtifactRelation.artifact_id == proposed.existing_artifact_id,
                    RevisionArtifactRelation.relation_type == "ATTACHMENT",
                )
            )
            if relation is None:
                relation = RevisionArtifactRelation(
                    revision_id=owner_revision.id,
                    artifact_id=proposed.existing_artifact_id,
                    relation_type="ATTACHMENT",
                    sequence=proposed.sequence,
                    is_primary=False,
                    created_by=actor_id,
                )
                db.add(relation)
                db.flush()
            if owner_revision.id == revision.id:
                relation_ids.append(relation.id)
            else:
                child_relations[owner_revision.id].append(relation)
            artifact_ids.append(proposed.existing_artifact_id)
            continue
        attachment = None
        if proposed.relation_type == "ATTACHMENT":
            attachment_type = {
                "DRAWING": "DRAWING",
                "DOCUMENT": "DOCUMENT",
                "PDF": "DOCUMENT",
                "SPECIFICATION": "SPECIFICATION",
                "IMAGE": "IMAGE",
            }.get(proposed.artifact_type, "OTHER")
            if proposed.attachment_id is not None:
                attachment = db.scalar(
                    select(Attachment)
                    .where(
                        Attachment.id == proposed.attachment_id,
                        Attachment.item_id == item.id,
                        Attachment.lifecycle_state == "ACTIVE",
                    )
                    .with_for_update()
                )
                if attachment is None:
                    raise HTTPException(status_code=409, detail="Additional File changed")
            else:
                attachment = Attachment(
                    item_id=item.id,
                    name=proposed.name,
                    attachment_type=attachment_type,
                    description=proposed.description,
                    created_by=actor_id,
                )
                db.add(attachment)
                db.flush()
        previous_attachment_version = (
            None
            if attachment is None
            else db.scalar(
                select(Artifact)
                .where(Artifact.attachment_id == attachment.id)
                .order_by(Artifact.generation.desc(), Artifact.id.desc())
                .limit(1)
            )
        )
        artifact = Artifact(
            artifact_key=(
                previous_attachment_version.artifact_key
                if previous_attachment_version is not None
                else proposed.client_artifact_id
            ),
            attachment_id=attachment.id if attachment is not None else None,
            generation=(
                previous_attachment_version.generation + 1
                if previous_attachment_version is not None
                else 1
            ),
            artifact_type=proposed.artifact_type,
            name=proposed.name,
            description=proposed.description,
            classification=proposed.classification,
            source="USER",
            state="READY",
            ready_at=now,
            created_by=actor_id,
        )
        db.add(artifact)
        db.flush()
        artifact_ids.append(artifact.id)
        relation = RevisionArtifactRelation(
            revision_id=owner_revision.id,
            artifact_id=artifact.id,
            relation_type=proposed.relation_type,
            sequence=proposed.sequence,
            is_primary=proposed.is_primary,
            created_by=actor_id,
        )
        db.add(relation)
        db.flush()
        if owner_revision.id == revision.id:
            relation_ids.append(relation.id)
        else:
            child_relations[owner_revision.id].append(relation)
        for proposed_file in proposed.files:
            source = staged_files[proposed_file.staged_file_id]
            content_blob = None
            if attachment is not None:
                content_blob = _content_blob_for_staged_file(db, source, storage)
                storage_key = (
                    f"items/{item.id}/attachments/{attachment.id}/versions/"
                    f"{artifact.id}/{Path(source.filename).name}"
                )
            else:
                storage_key = source.storage_key
            if attachment is None and storage_key in used_storage_keys:
                filename = Path(source.filename).name
                storage_key = (
                    f"parts/{owner_revision.part_id}/revisions/{owner_revision.id}/"
                    f"source/{uuid.uuid4()}/{filename}"
                )
                destination = storage.create_upload(storage_key)
                shutil.copy2(storage.path_for(source.storage_key), destination)
                storage.complete_upload(storage_key)
            used_storage_keys.add(storage_key)
            artifact_file = ArtifactFile(
                artifact_id=artifact.id,
                content_blob_id=content_blob.id if content_blob is not None else None,
                reference_name=proposed_file.reference_name,
                role=proposed_file.role,
                # Additional File ownership is expressed by Attachment and the
                # exact revision relation/snapshot, never by this legacy column.
                revision_id=None if attachment is not None else owner_revision.id,
                kind=_legacy_kind(proposed.artifact_type),
                storage_key=storage_key,
                original_filename=source.filename,
                mime_type=source.mime_type,
                byte_size=source.byte_size,
                sha256=source.sha256,
                state="VERIFIED",
                required=proposed_file.role == "PRIMARY",
            )
            db.add(artifact_file)
            db.flush()
            if proposed_file.role == "PRIMARY":
                if owner_revision.id == revision.id:
                    new_primary_file = artifact_file
                elif owner_revision.id not in child_primary_files:
                    child_primary_files[owner_revision.id] = artifact_file

    snapshot = RevisionContentSnapshot(
        revision_id=revision.id,
        sequence=latest_snapshot.sequence + 1,
        commit_id=payload.commit_id,
        created_by=actor_id,
    )
    db.add(snapshot)
    db.flush()
    for index, relation_id in enumerate(relation_ids):
        db.add(
            RevisionContentSnapshotArtifact(
                snapshot_id=snapshot.id, relation_id=relation_id, sequence=index
            )
        )
    child_snapshots: dict[uuid.UUID, RevisionContentSnapshot] = {}
    for child_revision in created_revisions:
        child_snapshot = RevisionContentSnapshot(
            revision_id=child_revision.id,
            sequence=1,
            commit_id=uuid.uuid5(payload.commit_id, str(child_revision.id)),
            created_by=actor_id,
        )
        db.add(child_snapshot)
        db.flush()
        child_snapshots[child_revision.id] = child_snapshot
        for index, relation in enumerate(child_relations[child_revision.id]):
            db.add(
                RevisionContentSnapshotArtifact(
                    snapshot_id=child_snapshot.id,
                    relation_id=relation.id,
                    sequence=index,
                )
            )
    bom_view_id: uuid.UUID | None = None
    lines_by_parent: dict[uuid.UUID, list] = {}
    for line in proposal.bom:
        parent_revision = (
            db.get(Revision, line.parent_revision_id)
            if line.parent_revision_id is not None
            else revision_by_file.get(line.parent_staged_file_id)
        )
        if parent_revision is None:
            raise HTTPException(status_code=422, detail="Edit BOM parent is unresolved")
        lines_by_parent.setdefault(parent_revision.id, []).append(line)
    from modelryx.services.bom_cycles import ensure_no_bom_cycle

    # Serialize graph checks and edits so concurrent reciprocal links cannot both pass.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('modelryx:bom-graph'))"))
    for parent_id, parent_lines in lines_by_parent.items():
        children = set()
        for line in parent_lines:
            child_id = line.child_revision_id
            if child_id is None and line.child_staged_file_id is not None:
                child = revision_by_file.get(line.child_staged_file_id)
                child_id = child.id if child is not None else None
            if child_id is not None:
                children.add(child_id)
        ensure_no_bom_cycle(db, parent_id, children)
    assembly_revision_ids = {revision.id} | {
        child.id
        for child in created_revisions
        if db.get(Item, child.part_id).part_type == "ASSEMBLY"
    }
    for parent_revision_id in assembly_revision_ids:
        parent_revision = (
            revision
            if parent_revision_id == revision.id
            else next(child for child in created_revisions if child.id == parent_revision_id)
        )
        parent_item = (
            item if parent_revision_id == revision.id else db.get(Item, parent_revision.part_id)
        )
        if parent_item is None or parent_item.part_type != "ASSEMBLY":
            continue
        parent_snapshot = (
            snapshot if parent_revision_id == revision.id else child_snapshots[parent_revision_id]
        )
        previous_bom_sequence = (
            db.scalar(
                select(func.max(BomViewRevision.sequence)).where(
                    BomViewRevision.revision_id == parent_revision_id
                )
            )
            or 0
        )
        bom_view = BomViewRevision(
            revision_id=parent_revision_id,
            snapshot_id=parent_snapshot.id,
            sequence=previous_bom_sequence + 1,
            created_by=actor_id,
        )
        db.add(bom_view)
        db.flush()
        if parent_revision_id == revision.id:
            bom_view_id = bom_view.id
        for index, line in enumerate(lines_by_parent.get(parent_revision_id, [])):
            child_revision_id = line.child_revision_id
            if child_revision_id is None and line.child_staged_file_id is not None:
                resolved_child = revision_by_file.get(line.child_staged_file_id)
                child_revision_id = resolved_child.id if resolved_child is not None else None
            if child_revision_id is None:
                raise HTTPException(status_code=422, detail="Edit BOM line is unresolved")
            child = db.get(Revision, child_revision_id)
            if child is None or child.id == parent_revision_id:
                raise HTTPException(status_code=422, detail="Edit BOM child is invalid")
            db.add(
                BomLine(
                    bom_view_revision_id=bom_view.id,
                    parent_revision_id=parent_revision_id,
                    child_revision_id=child.id,
                    occurrence_key=line.instance_name,
                    source_reference=line.source_reference,
                    reference_designator=line.reference_designator,
                    transform=line.transform,
                    is_suppressed=line.is_suppressed,
                    is_visible=line.is_visible,
                    properties_json=line.properties,
                    quantity=line.quantity,
                    sequence=index,
                )
            )
    item.part_number = proposal.item.item_number.strip()
    item.name = proposal.item.name.strip()
    item.description = proposal.item.description
    item.category = proposal.item.category
    revision.description = proposal.revision.description
    revision.cad_system = proposal.revision.cad_system
    revision.native_format = proposal.revision.native_format
    processing_type = (
        _native_processing_type(new_primary_file.original_filename, revision.cad_system, db)
        if new_primary_file is not None
        else None
    )
    if processing_type is None and new_primary_file is None and item.part_type == "ASSEMBLY":
        # Editing a BOM creates a new snapshot without the old derived artifacts.
        # Regenerate generic worker output against that snapshot so metadata and
        # previews remain available and unresolved-component warnings are updated.
        from modelryx.services.artifact_content import artifact_files_for_snapshot
        from modelryx.services.external_workers import JOB_TYPE

        db.flush()
        source_file = next(
            (
                value
                for value in artifact_files_for_snapshot(
                    db, revision.id, snapshot.id, artifact_types={"CAD_ASSEMBLY"}
                )
                if value.role == "PRIMARY"
            ),
            None,
        )
        if source_file is not None:
            candidate = _native_processing_type(
                source_file.original_filename, revision.cad_system, db
            )
            was_external = db.scalar(
                select(ProcessingJob.id)
                .where(
                    ProcessingJob.revision_id == revision.id,
                    ProcessingJob.type == JOB_TYPE,
                    ProcessingJob.state == "SUCCEEDED",
                )
                .limit(1)
            )
            if candidate == JOB_TYPE or (
                candidate is None and (was_external is not None or force_reprocess)
            ):
                processing_type = JOB_TYPE
    if processing_type is not None:
        revision.state = "PROCESSING"
        db.add(
            ProcessingJob(
                revision_id=revision.id,
                snapshot_id=snapshot.id,
                type=processing_type,
                state="PENDING",
                attempt_count=0,
                idempotency_key=f"{processing_type.lower()}:{snapshot.id}",
                available_at=now,
            )
        )
    for child_revision in created_revisions:
        primary_file = child_primary_files.get(child_revision.id)
        if primary_file is None:
            raise HTTPException(
                status_code=422, detail="A new component requires a primary CAD file"
            )
        child_processing_type = _native_processing_type(
            primary_file.original_filename, child_revision.cad_system, db
        )
        if child_processing_type is not None:
            child_revision.state = "PROCESSING"
            db.add(
                ProcessingJob(
                    revision_id=child_revision.id,
                    snapshot_id=child_snapshots[child_revision.id].id,
                    type=child_processing_type,
                    state="PENDING",
                    attempt_count=0,
                    idempotency_key=(
                        f"{child_processing_type.lower()}:{child_snapshots[child_revision.id].id}"
                    ),
                    available_at=now,
                )
            )
    db.delete(checkout)
    return StagingCommitResponse(
        staging_id=staging.id,
        commit_id=payload.commit_id,
        snapshot_version=staging.snapshot_version,
        item_id=item.id,
        revision_id=revision.id,
        content_snapshot_id=snapshot.id,
        artifact_ids=artifact_ids,
        created_item_ids=[created.id for created in created_items],
        created_revision_ids=[created.id for created in created_revisions],
        reused_revision_ids=sorted(reused_revision_ids, key=str),
        bom_view_revision_id=bom_view_id,
    )


@router.post("/{staging_id}/commit", response_model=StagingCommitResponse)
def commit_staging_upload(
    staging_id: uuid.UUID,
    payload: StagingCommitRequest,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> StagingCommitResponse:
    """Create an Item, Revision, Artifacts, and snapshot in one transaction."""
    require_engineer(principal)
    actor = user_for_principal(db, principal)
    staging = db.scalar(
        select(StagingSession).where(StagingSession.id == staging_id).with_for_update()
    )
    if staging is None or (principal.role != "administrator" and staging.created_by != actor.id):
        raise HTTPException(status_code=404, detail="Staging session not found")
    if staging.status == "COMPLETED":
        if staging.commit_id != payload.commit_id:
            raise HTTPException(
                status_code=409, detail="Staging session was completed by another commit"
            )
        return _stored_result(staging)
    if staging.snapshot_version != payload.snapshot_version:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Staging proposal changed; current snapshot is {staging.snapshot_version}. "
                "Review and submit the current proposal."
            ),
        )
    if staging.status != "READY_TO_COMMIT":
        raise HTTPException(
            status_code=409,
            detail="Staging proposal must pass readiness checks before commit",
        )
    if staging.operation not in {"CREATE_ITEM", "CREATE_REVISION", "EDIT_ITEM_REVISION"}:
        raise HTTPException(status_code=422, detail=f"Unsupported operation: {staging.operation}")
    try:
        proposal = StagingProposalUpdate.model_validate(
            {**staging.proposal_json, "expected_snapshot_version": staging.snapshot_version}
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"The item proposal is invalid: {exc}") from exc
    if proposal.item is None or proposal.revision is None:
        raise HTTPException(status_code=422, detail="A complete Item proposal is required")
    if proposal.operation == "EDIT_ITEM_REVISION":
        child_ids = list(dict.fromkeys(payload.child_staging_ids))
        if staging.id in child_ids:
            raise HTTPException(
                status_code=422,
                detail="The root staging session cannot be its own child",
            )
        participants = [staging]
        if child_ids:
            children = list(
                db.scalars(
                    select(StagingSession)
                    .where(StagingSession.id.in_(child_ids))
                    .order_by(StagingSession.id)
                    .with_for_update()
                )
            )
            if len(children) != len(child_ids):
                raise HTTPException(
                    status_code=404,
                    detail="One or more child staging sessions were not found",
                )
            participants.extend(children)
        results: list[StagingCommitResponse] = []
        for participant in participants:
            if (
                participant.created_by != actor.id
                or participant.operation != "EDIT_ITEM_REVISION"
                or participant.status != "READY_TO_COMMIT"
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Every checked-out child must be owned and ready before submission",
                )
            participant_proposal = StagingProposalUpdate.model_validate(
                {
                    **participant.proposal_json,
                    "expected_snapshot_version": participant.snapshot_version,
                }
            )
            participant_commit_id = (
                payload.commit_id
                if participant.id == staging.id
                else uuid.uuid5(payload.commit_id, str(participant.id))
            )
            participant_payload = StagingCommitRequest(
                snapshot_version=participant.snapshot_version,
                commit_id=participant_commit_id,
            )
            _require_current_assembly_analysis(participant, participant_proposal)
            participant_result = _commit_edit(
                db,
                participant,
                participant_proposal,
                participant_payload,
                actor.id,
                datetime.now(UTC),
                settings,
                force_reprocess=participant.id == staging.id and bool(child_ids),
            )
            participant.status = "COMPLETED"
            participant.commit_id = participant_commit_id
            participant.result_json = participant_result.model_dump(mode="json")
            results.append(participant_result)
        result = results[0]
        record_audit(
            db,
            actor_id=actor.id,
            action="staging.edit_committed",
            entity_type="revision_content_snapshot",
            entity_id=result.content_snapshot_id,
            request_id=request.state.request_id,
            details={
                "staging_id": str(staging.id),
                "revision_id": str(result.revision_id),
                "new_artifacts": sum(len(entry.artifact_ids) for entry in results),
                "committed_revision_ids": [str(entry.revision_id) for entry in results],
            },
        )
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Edit commit conflicted") from exc
        return result
    if proposal.operation not in {"CREATE_ITEM", "CREATE_REVISION"}:
        raise HTTPException(status_code=422, detail="Proposal operation does not match staging")
    if proposal.operation != staging.operation:
        raise HTTPException(status_code=422, detail="Proposal operation does not match staging")

    destination_item: Item | None = None
    destination_revision: Revision | None = None
    if proposal.operation == "CREATE_REVISION":
        assert proposal.destination is not None
        destination_item = db.get(Item, proposal.destination.item_id)
        destination_revision = db.scalar(
            select(Revision)
            .where(Revision.id == proposal.destination.revision_id)
            .with_for_update()
        )
        if (
            destination_item is None
            or destination_revision is None
            or destination_revision.part_id != destination_item.id
            or destination_item.current_revision_id != destination_revision.id
            or staging.base_revision_id != destination_revision.id
        ):
            raise HTTPException(status_code=409, detail="The target Item revision changed")
        if proposal.item.item_number.strip().casefold() != destination_item.part_number.casefold():
            raise HTTPException(
                status_code=422, detail="A new revision cannot change the Item number"
            )
        if (
            db.scalar(
                select(Revision.id).where(
                    Revision.part_id == destination_item.id,
                    func.lower(Revision.revision_code) == proposal.revision.revision_code.lower(),
                )
            )
            is not None
        ):
            raise HTTPException(
                status_code=409, detail="Revision code already exists for this Item"
            )

    new_item_proposals = ([] if proposal.operation == "CREATE_REVISION" else [proposal.item]) + [
        decision.item
        for decision in proposal.identity_decisions
        if decision.decision == "CREATE_NEW" and decision.item is not None
    ]
    existing_number = db.scalar(
        select(Item.part_number).where(
            func.lower(Item.part_number).in_(
                [proposed.item_number.strip().lower() for proposed in new_item_proposals]
            )
        )
    )
    if existing_number is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Item number {existing_number} already exists. "
                "Return to staging and choose another number."
            ),
        )

    staged_files = {
        row.id: row
        for row in db.scalars(select(StagingFile).where(StagingFile.staging_id == staging.id))
    }
    selected_ids = {
        file.staged_file_id for artifact in proposal.artifacts for file in artifact.files
    }
    decisions = {item.staged_file_id: item for item in proposal.identity_decisions}
    reused_ids = {
        item.staged_file_id
        for item in proposal.identity_decisions
        if item.decision == "USE_EXISTING"
    }
    if selected_ids | reused_ids != set(staged_files) or any(
        staged_files[file_id].state != "VERIFIED" for file_id in selected_ids - reused_ids
    ):
        raise HTTPException(
            status_code=409,
            detail="Staged files changed after readiness checks; review and validate again",
        )
    reused_revisions: dict[uuid.UUID, Revision] = {}
    reused_artifact_files: dict[uuid.UUID, ArtifactFile] = {}
    for staged_file_id in reused_ids:
        decision = decisions[staged_file_id]
        existing = db.get(Revision, decision.existing_revision_id)
        matching_file = db.scalar(
            select(ArtifactFile).where(
                ArtifactFile.revision_id == decision.existing_revision_id,
                ArtifactFile.sha256 == staged_files[staged_file_id].sha256,
                ArtifactFile.state == "VERIFIED",
            )
        )
        if (
            existing is None
            or existing.state not in {"READY", "READY_WITH_WARNINGS"}
            or matching_file is None
        ):
            raise HTTPException(
                status_code=409,
                detail="A selected duplicate changed; return to staging and choose it again",
            )
        reused_revisions[staged_file_id] = existing
        reused_artifact_files[staged_file_id] = matching_file

    now = datetime.now(UTC)
    created_items: list[Item] = []
    created_revisions: list[Revision] = []
    revision_by_file: dict[uuid.UUID, Revision] = dict(reused_revisions)
    metadata_by_revision: dict[uuid.UUID, dict] = {}

    def create_identity(item_proposal, revision_proposal, source_sha256: str, metadata: dict):
        new_item = Item(
            part_number=item_proposal.item_number.strip(),
            name=item_proposal.name.strip(),
            description=item_proposal.description,
            category=item_proposal.category,
            part_type=item_proposal.item_type,
            created_by=actor.id,
        )
        db.add(new_item)
        db.flush()
        new_revision = Revision(
            part_id=new_item.id,
            revision_code=revision_proposal.revision_code,
            description=revision_proposal.description,
            cad_system=revision_proposal.cad_system,
            native_format=revision_proposal.native_format,
            metadata_json=metadata,
            search_text=build_semantic_text(metadata) if metadata else "",
            package_sha256=source_sha256,
            state="READY",
            created_by=actor.id,
            committed_at=now,
        )
        db.add(new_revision)
        db.flush()
        new_item.current_revision_id = new_revision.id
        created_items.append(new_item)
        created_revisions.append(new_revision)
        metadata_by_revision[new_revision.id] = metadata
        return new_item, new_revision

    root_file_ids = {
        proposed_file.staged_file_id
        for proposed in proposal.artifacts
        if proposed.owner_staged_file_id is None
        for proposed_file in proposed.files
    }
    if not root_file_ids:
        raise HTTPException(status_code=422, detail="The root Item requires an Artifact")
    root_primary_file_ids = [
        proposed_file.staged_file_id
        for proposed in proposal.artifacts
        if proposed.owner_staged_file_id is None and proposed.is_primary
        for proposed_file in proposed.files
        if proposed_file.role == "PRIMARY"
    ]
    if len(root_primary_file_ids) != 1:
        raise HTTPException(
            status_code=422,
            detail="The root Item requires exactly one primary Artifact file",
        )
    root_sha = staged_files[root_primary_file_ids[0]].sha256
    metadata = staging.metadata_json or {}
    if proposal.operation == "CREATE_REVISION":
        assert destination_item is not None
        item = destination_item
        item.name = proposal.item.name.strip()
        item.description = proposal.item.description
        item.category = proposal.item.category
        revision = Revision(
            part_id=item.id,
            revision_code=proposal.revision.revision_code,
            description=proposal.revision.description,
            cad_system=proposal.revision.cad_system,
            native_format=proposal.revision.native_format,
            metadata_json=metadata,
            search_text=build_semantic_text(metadata) if metadata else "",
            package_sha256=root_sha,
            state="READY",
            created_by=actor.id,
            committed_at=now,
        )
        db.add(revision)
        db.flush()
        item.current_revision_id = revision.id
        created_revisions.append(revision)
        metadata_by_revision[revision.id] = metadata
    else:
        item, revision = create_identity(
            proposal.item,
            proposal.revision,
            root_sha,
            metadata,
        )
    for file_id in root_file_ids:
        revision_by_file[file_id] = revision
    # A packaged assembly keeps component files in the root authoritative
    # artifact even when a component identity reuses an existing Revision.
    revision_by_file.update(reused_revisions)
    for decision in proposal.identity_decisions:
        if decision.decision != "CREATE_NEW" or decision.item is None:
            continue
        assert decision.revision is not None
        _, component_revision = create_identity(
            decision.item,
            decision.revision,
            staged_files[decision.staged_file_id].sha256,
            {},
        )
        revision_by_file[decision.staged_file_id] = component_revision

    relations_by_revision: dict[uuid.UUID, list[RevisionArtifactRelation]] = {
        row.id: [] for row in created_revisions
    }
    primary_file_by_revision: dict[uuid.UUID, ArtifactFile] = {}
    artifact_ids: list[uuid.UUID] = []
    used_storage_keys: set[str] = set()
    storage = LocalStorageProvider(settings.storage_root)
    for proposed in proposal.artifacts:
        owner_revision = (
            revision
            if proposed.owner_staged_file_id is None
            else revision_by_file.get(proposed.owner_staged_file_id)
        )
        if owner_revision is None or owner_revision.id not in relations_by_revision:
            raise HTTPException(status_code=422, detail="Artifact owner is not a new Item Revision")
        artifact = Artifact(
            artifact_key=proposed.client_artifact_id,
            generation=1,
            artifact_type=proposed.artifact_type,
            name=proposed.name,
            description=proposed.description,
            classification=proposed.classification,
            source="USER",
            state="READY",
            created_by=actor.id,
        )
        db.add(artifact)
        db.flush()
        artifact_ids.append(artifact.id)
        relation = RevisionArtifactRelation(
            revision_id=owner_revision.id,
            artifact_id=artifact.id,
            relation_type=proposed.relation_type,
            sequence=proposed.sequence,
            is_primary=proposed.is_primary,
            created_by=actor.id,
        )
        db.add(relation)
        db.flush()
        relations_by_revision[owner_revision.id].append(relation)
        for proposed_file in proposed.files:
            source = staged_files[proposed_file.staged_file_id]
            reused_source = reused_artifact_files.get(proposed_file.staged_file_id)
            storage_key = source.storage_key
            if reused_source is not None or storage_key in used_storage_keys:
                filename = Path(source.filename).name
                storage_key = (
                    f"parts/{owner_revision.part_id}/revisions/{owner_revision.id}/"
                    f"source/{uuid.uuid4()}/{filename}"
                )
                destination = storage.create_upload(storage_key)
                copy_source = (
                    reused_source.storage_key if reused_source is not None else source.storage_key
                )
                shutil.copy2(storage.path_for(copy_source), destination)
                storage.complete_upload(storage_key)
            used_storage_keys.add(storage_key)
            artifact_file = ArtifactFile(
                artifact_id=artifact.id,
                reference_name=proposed_file.reference_name,
                role=proposed_file.role,
                revision_id=owner_revision.id,
                kind=_legacy_kind(proposed.artifact_type),
                storage_key=storage_key,
                original_filename=source.filename,
                mime_type=source.mime_type,
                byte_size=source.byte_size,
                sha256=source.sha256,
                state="VERIFIED",
                required=proposed_file.role == "PRIMARY",
            )
            db.add(artifact_file)
            db.flush()
            if owner_revision.id not in primary_file_by_revision and (
                proposed.is_primary or proposed_file.role == "PRIMARY"
            ):
                primary_file_by_revision[owner_revision.id] = artifact_file

    snapshots: dict[uuid.UUID, RevisionContentSnapshot] = {}
    for created_revision in created_revisions:
        snapshot = RevisionContentSnapshot(
            revision_id=created_revision.id,
            sequence=1,
            commit_id=(
                payload.commit_id
                if created_revision.id == revision.id
                else uuid.uuid5(payload.commit_id, str(created_revision.id))
            ),
            created_by=actor.id,
        )
        db.add(snapshot)
        db.flush()
        snapshots[created_revision.id] = snapshot
        for index, relation in enumerate(relations_by_revision[created_revision.id]):
            db.add(
                RevisionContentSnapshotArtifact(
                    snapshot_id=snapshot.id, relation_id=relation.id, sequence=index
                )
            )
    snapshot = snapshots[revision.id]
    bom_view: BomViewRevision | None = None
    if proposal.bom:
        if item.part_type != "ASSEMBLY":
            raise HTTPException(
                status_code=422, detail="Only an ASSEMBLY Item may contain BOM lines"
            )
        lines_by_parent: dict[uuid.UUID, list] = {}
        for line in proposal.bom:
            parent_revision = revision_by_file.get(line.parent_staged_file_id)
            if parent_revision is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"BOM parent for {line.instance_name} is unavailable",
                )
            parent_item = db.get(Item, parent_revision.part_id)
            if parent_item is None or parent_item.part_type != "ASSEMBLY":
                raise HTTPException(
                    status_code=422,
                    detail=f"BOM parent for {line.instance_name} is not an assembly",
                )
            # Existing revisions are immutable and already own their persisted BOM view.
            # The uploaded dependency graph can still contain their descendant edges;
            # only materialize edges for revisions created by this commit.
            if parent_revision.id not in snapshots:
                continue
            lines_by_parent.setdefault(parent_revision.id, []).append(line)

        for parent_revision_id, parent_lines in lines_by_parent.items():
            parent_snapshot = snapshots.get(parent_revision_id)
            if parent_snapshot is None:
                raise HTTPException(status_code=409, detail="BOM parent snapshot is unavailable")
            parent_bom_view = BomViewRevision(
                revision_id=parent_revision_id,
                snapshot_id=parent_snapshot.id,
                sequence=1,
                created_by=actor.id,
            )
            db.add(parent_bom_view)
            db.flush()
            if parent_revision_id == revision.id:
                bom_view = parent_bom_view
            for index, line in enumerate(parent_lines):
                parent_revision = revision_by_file.get(line.parent_staged_file_id)
                assert parent_revision is not None
                child_revision = (
                    db.get(Revision, line.child_revision_id)
                    if line.child_revision_id is not None
                    else revision_by_file.get(line.child_staged_file_id)
                )
                if child_revision is None or child_revision.id == parent_revision.id:
                    raise HTTPException(
                        status_code=409,
                        detail=f"BOM child for {line.instance_name} is unavailable or invalid",
                    )
                db.add(
                    BomLine(
                        bom_view_revision_id=parent_bom_view.id,
                        parent_revision_id=parent_revision.id,
                        child_revision_id=child_revision.id,
                        occurrence_key=line.instance_name,
                        source_reference=line.source_reference,
                        reference_designator=line.reference_designator,
                        transform=line.transform,
                        is_suppressed=line.is_suppressed,
                        is_visible=line.is_visible,
                        properties_json=line.properties,
                        quantity=line.quantity,
                        sequence=index,
                    )
                )
    for created_revision in created_revisions:
        primary_file = primary_file_by_revision.get(created_revision.id)
        revision_metadata = metadata_by_revision[created_revision.id]
        if revision_metadata and primary_file is not None:
            MetadataService(db).ingest(
                revision=created_revision,
                snapshot=snapshots[created_revision.id],
                raw_artifact=primary_file,
                metadata=revision_metadata,
            )
        if (
            primary_file is not None
            and (
                processing_type := _native_processing_type(
                    primary_file.original_filename, created_revision.cad_system, db
                )
            )
            is not None
        ):
            created_revision.state = "PROCESSING"
            db.add(
                ProcessingJob(
                    revision_id=created_revision.id,
                    snapshot_id=snapshots[created_revision.id].id,
                    type=processing_type,
                    state="PENDING",
                    attempt_count=0,
                    idempotency_key=(
                        f"{processing_type.lower()}:{snapshots[created_revision.id].id}"
                    ),
                    available_at=now,
                )
            )
    result = StagingCommitResponse(
        staging_id=staging.id,
        commit_id=payload.commit_id,
        snapshot_version=staging.snapshot_version,
        item_id=item.id,
        revision_id=revision.id,
        content_snapshot_id=snapshot.id,
        artifact_ids=artifact_ids,
        created_item_ids=[row.id for row in created_items],
        created_revision_ids=[row.id for row in created_revisions],
        reused_revision_ids=sorted({row.id for row in reused_revisions.values()}, key=str),
        bom_view_revision_id=bom_view.id if bom_view else None,
    )
    staging.status = "COMPLETED"
    staging.commit_id = payload.commit_id
    staging.result_json = result.model_dump(mode="json")
    record_audit(
        db,
        actor_id=actor.id,
        action="staging.committed",
        entity_type="revision_content_snapshot",
        entity_id=snapshot.id,
        request_id=request.state.request_id,
        details={
            "staging_id": str(staging.id),
            "commit_id": str(payload.commit_id),
            "item_id": str(item.id),
            "revision_id": str(revision.id),
            "artifacts": len(artifact_ids),
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Commit conflicts with an existing item number or commit identifier",
        ) from exc
    return result
