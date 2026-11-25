import hashlib
import os
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.api.duplicate_checks import (
    DuplicateCheckResponse,
    _response,
    build_duplicate_repository,
)
from modelryx.api.parts import require_engineer
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.duplicate_detection.hashing import canonical_package_sha256
from modelryx.duplicate_detection.models import ScoreBreakdown
from modelryx.duplicate_detection.search_document import build_semantic_text
from modelryx.metadata.engineering_validator import validate_engineering_metadata
from modelryx.metadata.service import MetadataService
from modelryx.metadata.validator import validate_part_metadata
from modelryx.models import (
    Artifact,
    ArtifactFile,
    BomLine,
    BomViewRevision,
    CADSession,
    Checkout,
    DuplicateCandidate,
    DuplicateCheck,
    DuplicateFeedback,
    Item,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    StagingFile,
    StagingSession,
    User,
    ValidationRun,
)
from modelryx.services.ai import AiGateway
from modelryx.services.artifact_content import current_artifact_files
from modelryx.services.cad_dependencies import is_managed_bom_reference
from modelryx.services.item_numbers import allocate_item_number
from modelryx.services.storage import LocalStorageProvider


class StagingPromoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_number: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=100)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    category: str | None = Field(default=None, max_length=100)
    revision_code: str = Field(default="A", min_length=1, max_length=50)
    cad_system: str = Field(default="Unknown", min_length=1, max_length=100)
    native_format: str = Field(default="unknown", min_length=1, max_length=50)


class StagingWorkspaceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["ARCHIVE", "RESTORE", "REMOVE"]


class StagingPromotionResponse(BaseModel):
    staging_id: uuid.UUID
    item_id: uuid.UUID
    revision_id: uuid.UUID
    reused: bool


class UploadPreflightFile(BaseModel):
    logical_path: str
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    byte_size: int = Field(ge=0)
    item_number: str | None = None


class UploadPreflightRequest(BaseModel):
    files: list[UploadPreflightFile] = Field(min_length=1, max_length=500)


class UploadDuplicateMatch(BaseModel):
    item_id: uuid.UUID
    item_number: str
    item_name: str
    item_type: str
    revision_id: uuid.UUID
    revision_code: str


class UploadPreflightResult(BaseModel):
    logical_path: str
    item_number_exists: bool
    existing_item_id: uuid.UUID | None
    duplicates: list[UploadDuplicateMatch]


class UploadPreflightResponse(BaseModel):
    files: list[UploadPreflightResult]


class ItemNumberGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: Literal["PART", "ASSEMBLY"]
    template_id: uuid.UUID | None = None


class ItemNumberGenerateResponse(BaseModel):
    item_number: str
    item_type: Literal["PART", "ASSEMBLY"]
    template: str
    template_id: uuid.UUID
    template_name: str


router = APIRouter(prefix="/api/v1/staging", tags=["staging"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


@router.post("/item-numbers/generate", response_model=ItemNumberGenerateResponse)
def generate_item_number(
    payload: ItemNumberGenerateRequest, db: Db, principal: Actor
) -> ItemNumberGenerateResponse:
    require_engineer(principal)
    try:
        item_number, template = allocate_item_number(
            db, payload.item_type, str(payload.template_id) if payload.template_id else None
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return ItemNumberGenerateResponse(
        item_number=item_number,
        item_type=payload.item_type,
        template=(
            template.part_pattern if payload.item_type == "PART" else template.assembly_pattern
        ),
        template_id=template.id,
        template_name=template.name,
    )


class StagingFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_path: str = Field(min_length=1, max_length=1024)
    filename: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    mime_type: str = Field(default="application/octet-stream", min_length=1, max_length=255)


class StagingFileAddRequest(StagingFileRequest):
    expected_snapshot_version: int = Field(ge=1)


class StagingFileResponse(StagingFileRequest):
    id: uuid.UUID
    state: str
    upload_url: str


class RetainedArtifactFileResponse(BaseModel):
    id: uuid.UUID
    reference_name: str | None
    filename: str
    byte_size: int
    sha256: str
    mime_type: str
    download_url: str


class RetainedArtifactResponse(BaseModel):
    relation_id: uuid.UUID | None = None
    artifact_id: uuid.UUID
    files: list[RetainedArtifactFileResponse]


class StagingCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_staging_id: uuid.UUID
    operation: Literal["CREATE_ITEM", "CREATE_REVISION"] = "CREATE_ITEM"
    item_id: uuid.UUID | None = None
    base_revision_id: uuid.UUID | None = None
    files: list[StagingFileRequest] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def paths_are_safe_and_unique(self) -> "StagingCreateRequest":
        if self.operation == "CREATE_REVISION":
            if self.item_id is None or self.base_revision_id is None:
                raise ValueError("CREATE_REVISION requires item_id and base_revision_id")
        elif self.item_id is not None or self.base_revision_id is not None:
            raise ValueError("CREATE_ITEM cannot specify an existing destination")
        seen: set[str] = set()
        for item in self.files:
            normalized = item.logical_path.replace("\\", "/").strip("/")
            path = PurePosixPath(normalized)
            if (
                not normalized
                or path.is_absolute()
                or ".." in path.parts
                or (path.parts and ":" in path.parts[0])
            ):
                raise ValueError(f"unsafe logical path: {item.logical_path}")
            normalized = "/".join(part for part in path.parts if part != ".")
            key = normalized.casefold()
            if key in seen:
                raise ValueError(f"duplicate logical path: {item.logical_path}")
            seen.add(key)
            item.logical_path = normalized
            item.sha256 = item.sha256.lower()
        return self


class EditStagingCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_staging_id: uuid.UUID
    source_snapshot_id: uuid.UUID | None = None


class AssemblyAnalysisFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_path: str = Field(min_length=1, max_length=1024)
    filename: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class AssemblyAnalysisSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_snapshot_version: int = Field(ge=1)
    report: dict[str, Any]
    files: list[AssemblyAnalysisFileRequest] = Field(min_length=1, max_length=5000)


class AssemblyReconciliationApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_snapshot_version: int = Field(ge=1)


class CoordinatedAssemblyAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_snapshot_version: int = Field(ge=1)
    source_staging_id: uuid.UUID
    root_logical_path: str = Field(min_length=1, max_length=1024)


class StagingResponse(BaseModel):
    id: uuid.UUID
    client_staging_id: uuid.UUID
    status: str
    operation: str
    snapshot_version: int
    base_revision_id: uuid.UUID | None
    base_snapshot_id: uuid.UUID | None
    package_sha256: str
    proposal: dict[str, Any]
    commit_id: uuid.UUID | None
    result: dict[str, Any]
    files: list[StagingFileResponse]
    retained_artifacts: list[RetainedArtifactResponse]
    duplicate_check: DuplicateCheckResponse


class StagingSummary(BaseModel):
    id: uuid.UUID
    status: str
    operation: str
    snapshot_version: int
    base_revision_id: uuid.UUID | None
    owner_id: uuid.UUID
    owner_name: str
    file_count: int
    verified_file_count: int
    item_id: uuid.UUID | None = None
    item_number: str | None = None
    revision_id: uuid.UUID | None = None
    created_revision_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    can_cancel: bool
    can_resume: bool
    workflow_state: str | None = None
    workflow_stage: str | None = None
    workflow_progress_percent: int | None = None
    workflow_error_detail: str | None = None


class StagingListResponse(BaseModel):
    items: list[StagingSummary]
    total: int


def _bom_proposal_at_snapshot(
    db: Session,
    revision_id: uuid.UUID,
    snapshot: RevisionContentSnapshot,
) -> list[dict[str, Any]]:
    """Return the BOM effective at a content snapshot, carrying it forward when unchanged."""
    bom_view = db.scalar(
        select(BomViewRevision)
        .join(RevisionContentSnapshot, RevisionContentSnapshot.id == BomViewRevision.snapshot_id)
        .where(
            BomViewRevision.revision_id == revision_id,
            RevisionContentSnapshot.sequence <= snapshot.sequence,
        )
        .order_by(
            RevisionContentSnapshot.sequence.desc(),
            BomViewRevision.sequence.desc(),
            BomViewRevision.id.desc(),
        )
    )
    if bom_view is None:
        return []
    return [
        {
            "parent_revision_id": str(line.parent_revision_id),
            "child_revision_id": str(line.child_revision_id),
            "instance_name": line.occurrence_key,
            "quantity": line.quantity,
            "source_reference": line.source_reference,
            "reference_designator": line.reference_designator,
            "transform": line.transform,
            "is_suppressed": line.is_suppressed,
            "is_visible": line.is_visible,
            "properties": line.properties_json,
        }
        for line in db.scalars(
            select(BomLine)
            .where(BomLine.bom_view_revision_id == bom_view.id)
            .order_by(BomLine.sequence, BomLine.id)
        )
    ]


def _external_assembly_worker(db: Session, filename: str, cad_system: str) -> dict | None:
    from modelryx.services.external_workers import AmbiguousWorkerError, matching_worker

    if PurePosixPath(filename).suffix.casefold() in {".step", ".stp"}:
        return None
    try:
        worker = matching_worker(db, filename, cad_system)
    except AmbiguousWorkerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if worker and "assembly" in worker["capabilities"]["documentKinds"]:
        return worker
    return None


class StagingMetadataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: dict[str, Any]
    source_logical_path: str | None = Field(default=None, min_length=1, max_length=1024)
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    analyzer: str | None = Field(default=None, min_length=1, max_length=255)


class StagingDestinationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    revision_id: uuid.UUID
    base_snapshot_id: uuid.UUID | None = None


class StagingItemProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_number: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=100)
    name: str = Field(min_length=1, max_length=255)
    item_type: Literal["PART", "ASSEMBLY"]
    description: str | None = Field(default=None, max_length=10000)
    category: str | None = Field(default=None, max_length=100)


class StagingRevisionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_code: str = Field(min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=10000)
    cad_system: str = Field(default="Unknown", min_length=1, max_length=100)
    native_format: str = Field(default="unknown", min_length=1, max_length=50)


class AssemblyIdentityDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_snapshot_version: int = Field(ge=1)
    parent_logical_path: str = Field(min_length=1, max_length=1024)
    occurrence_key: str = Field(min_length=1, max_length=255)
    decision: Literal["USE_EXISTING", "CREATE_NEW"]
    existing_revision_id: uuid.UUID | None = None
    item: StagingItemProposal | None = None
    revision: StagingRevisionProposal | None = None

    @model_validator(mode="after")
    def valid_identity_choice(self) -> "AssemblyIdentityDecisionRequest":
        if self.decision == "USE_EXISTING":
            if (
                self.existing_revision_id is None
                or self.item is not None
                or self.revision is not None
            ):
                raise ValueError("USE_EXISTING requires only an existing revision")
        elif self.existing_revision_id is not None or self.item is None or self.revision is None:
            raise ValueError("CREATE_NEW requires an Item and Revision proposal")
        return self


class ProposedArtifactFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staged_file_id: uuid.UUID
    reference_name: str = Field(min_length=1, max_length=100)
    role: Literal["PRIMARY", "SECONDARY", "AUXILIARY"]


class ProposedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_artifact_id: uuid.UUID
    existing_relation_id: uuid.UUID | None = None
    existing_artifact_id: uuid.UUID | None = None
    attachment_id: uuid.UUID | None = None
    owner_staged_file_id: uuid.UUID | None = None
    artifact_type: Literal[
        "CAD_MODEL",
        "CAD_ASSEMBLY",
        "DRAWING",
        "PDF",
        "STEP",
        "GLB",
        "THUMBNAIL",
        "METADATA",
        "BOM",
        "SPECIFICATION",
        "DOCUMENT",
        "IMAGE",
        "AI_ANALYSIS",
        "OTHER",
    ]
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    classification: Literal["AUTHORITATIVE", "DERIVED", "EXTERNAL_REFERENCE"]
    relation_type: Literal[
        "SPECIFICATION",
        "REFERENCE",
        "DRAWING",
        "REPRESENTATION",
        "THUMBNAIL",
        "ANALYSIS",
        "ATTACHMENT",
    ]
    sequence: int = Field(default=0, ge=0)
    is_primary: bool = False
    files: list[ProposedArtifactFile] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def references_are_unique(self) -> "ProposedArtifact":
        names = [item.reference_name.casefold() for item in self.files]
        if len(names) != len(set(names)):
            raise ValueError("artifact reference names must be unique")
        if self.existing_relation_id is not None and self.existing_artifact_id is not None:
            raise ValueError("choose either an existing relation or an existing Artifact")
        if self.classification == "DERIVED" and (
            self.existing_relation_id is None and self.existing_artifact_id is None
        ):
            raise ValueError("worker-derived Artifacts must be added from verified worker results")
        if (
            self.existing_relation_id is None
            and self.existing_artifact_id is None
            and not self.files
        ):
            raise ValueError("a new Artifact requires at least one staged file")
        if (
            self.existing_relation_id is not None or self.existing_artifact_id is not None
        ) and self.files:
            raise ValueError("an existing Artifact cannot contain staged files")
        if self.attachment_id is not None and self.relation_type != "ATTACHMENT":
            raise ValueError("attachment_id is only valid for ATTACHMENT relations")
        return self


class StagingIdentityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staged_file_id: uuid.UUID
    decision: Literal["CREATE_NEW", "USE_EXISTING"]
    existing_revision_id: uuid.UUID | None = None
    item: StagingItemProposal | None = None
    revision: StagingRevisionProposal | None = None

    @model_validator(mode="after")
    def selected_revision_matches_decision(self) -> "StagingIdentityDecision":
        if self.decision == "USE_EXISTING":
            if (
                self.existing_revision_id is None
                or self.item is not None
                or self.revision is not None
            ):
                raise ValueError("USE_EXISTING requires an existing_revision_id and no new Item")
        elif self.existing_revision_id is not None or (
            (self.item is None) != (self.revision is None)
        ):
            raise ValueError(
                "CREATE_NEW requires both item and revision when component identity is supplied"
            )
        return self


class StagingBomLineProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_staged_file_id: uuid.UUID | None = None
    parent_revision_id: uuid.UUID | None = None
    child_staged_file_id: uuid.UUID | None = None
    child_revision_id: uuid.UUID | None = None
    instance_name: str = Field(min_length=1, max_length=255)
    quantity: int = Field(default=1, ge=1)
    source_reference: str = Field(default="", max_length=500)
    reference_designator: str | None = Field(default=None, max_length=255)
    transform: list[float] = Field(
        default_factory=lambda: [
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ],
        min_length=16,
        max_length=16,
    )
    is_suppressed: bool = False
    is_visible: bool = True
    properties: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def child_is_unambiguous(self) -> "StagingBomLineProposal":
        if (self.parent_staged_file_id is None) == (self.parent_revision_id is None):
            raise ValueError("BOM line requires exactly one staged or existing parent")
        if (self.child_staged_file_id is None) == (self.child_revision_id is None):
            raise ValueError("BOM line requires exactly one staged or existing child")
        return self


def _duplicate_bom_occurrence_groups(
    lines: list[StagingBomLineProposal],
) -> list[list[StagingBomLineProposal]]:
    """Find duplicate occurrence names within the same immediate parent assembly."""
    groups: dict[
        tuple[uuid.UUID | None, uuid.UUID | None, str],
        list[StagingBomLineProposal],
    ] = {}
    for line in lines:
        key = (
            line.parent_staged_file_id,
            line.parent_revision_id,
            line.instance_name.casefold(),
        )
        groups.setdefault(key, []).append(line)
    return [group for group in groups.values() if len(group) > 1]


class StagingProposalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_snapshot_version: int = Field(ge=1)
    operation: Literal[
        "CREATE_ITEM",
        "CREATE_REVISION",
        "EDIT_ITEM_REVISION",
        "ADD_ARTIFACT",
        "CHECKIN_REVISION",
        "CHECKIN_STRUCTURE",
    ]
    destination: StagingDestinationProposal | None = None
    item: StagingItemProposal | None = None
    revision: StagingRevisionProposal | None = None
    artifacts: list[ProposedArtifact] = Field(default_factory=list, max_length=500)
    identity_decisions: list[StagingIdentityDecision] = Field(default_factory=list, max_length=500)
    bom: list[StagingBomLineProposal] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def operation_has_destination(self) -> "StagingProposalUpdate":
        if self.operation == "CREATE_ITEM":
            if self.item is None or self.revision is None or self.destination is not None:
                raise ValueError("CREATE_ITEM requires item and revision and no destination")
        elif self.destination is None:
            raise ValueError(f"{self.operation} requires a destination")
        elif self.operation in {"CREATE_REVISION", "EDIT_ITEM_REVISION"} and (
            self.item is None or self.revision is None
        ):
            raise ValueError(f"{self.operation} requires item and revision properties")
        return self


class StagingReadyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_snapshot_version: int = Field(ge=1)


def _owned_staging(db: Session, staging_id: uuid.UUID, principal: Principal) -> StagingSession:
    staging = db.get(StagingSession, staging_id)
    if staging is None:
        raise HTTPException(status_code=404, detail="Staging session not found")
    user = user_for_principal(db, principal)
    if principal.role != "administrator" and staging.created_by != user.id:
        raise HTTPException(status_code=404, detail="Staging session not found")
    return staging


def _staging_response(db: Session, staging: StagingSession) -> StagingResponse:
    files = list(
        db.scalars(
            select(StagingFile)
            .where(StagingFile.staging_id == staging.id)
            .order_by(StagingFile.logical_path)
        )
    )

    check = db.scalar(
        select(DuplicateCheck)
        .where(DuplicateCheck.source_staging_id == staging.id)
        .order_by(DuplicateCheck.created_at.desc(), DuplicateCheck.id.desc())
    )
    if check is None:
        raise RuntimeError("staging duplicate check is missing")
    retained_artifacts: list[RetainedArtifactResponse] = []
    relation_ids = [
        artifact.get("existing_relation_id")
        for artifact in staging.proposal_json.get("artifacts", [])
        if artifact.get("existing_relation_id")
    ]
    artifact_entries: list[tuple[uuid.UUID | None, uuid.UUID]] = []
    if relation_ids:
        relations = {
            str(relation.id): relation
            for relation in db.scalars(
                select(RevisionArtifactRelation).where(
                    RevisionArtifactRelation.id.in_(relation_ids)
                )
            )
        }
        for relation_id in relation_ids:
            relation = relations.get(str(relation_id))
            if relation is None:
                continue
            artifact_entries.append((relation.id, relation.artifact_id))
    artifact_entries.extend(
        (None, uuid.UUID(str(artifact["existing_artifact_id"])))
        for artifact in staging.proposal_json.get("artifacts", [])
        if artifact.get("existing_artifact_id")
    )
    for relation_id, artifact_id in artifact_entries:
        artifact_files = list(
            db.scalars(
                select(ArtifactFile)
                .where(ArtifactFile.artifact_id == artifact_id)
                .order_by(
                    ArtifactFile.reference_name,
                    ArtifactFile.original_filename,
                    ArtifactFile.id,
                )
            )
        )
        retained_artifacts.append(
            RetainedArtifactResponse(
                relation_id=relation_id,
                artifact_id=artifact_id,
                files=[
                    RetainedArtifactFileResponse(
                        id=item.id,
                        reference_name=item.reference_name,
                        filename=item.original_filename,
                        byte_size=item.byte_size,
                        sha256=item.sha256,
                        mime_type=item.mime_type,
                        download_url=(f"/api/v1/artifacts/{artifact_id}/files/{item.id}/download"),
                    )
                    for item in artifact_files
                ],
            )
        )
    return StagingResponse(
        id=staging.id,
        client_staging_id=staging.client_staging_id,
        status=staging.status,
        operation=staging.operation,
        snapshot_version=staging.snapshot_version,
        base_revision_id=staging.base_revision_id,
        base_snapshot_id=staging.base_snapshot_id,
        package_sha256=staging.package_sha256,
        proposal=staging.proposal_json,
        commit_id=staging.commit_id,
        result=staging.result_json,
        files=[
            StagingFileResponse(
                id=item.id,
                logical_path=item.logical_path,
                filename=item.filename,
                byte_size=item.byte_size,
                sha256=item.sha256,
                mime_type=item.mime_type,
                state=item.state,
                upload_url=f"/api/v1/staging/files/{item.id}/content",
            )
            for item in files
        ],
        retained_artifacts=retained_artifacts,
        duplicate_check=_response(db, check),
    )


ONGOING_STAGING_STATUSES = {
    "HASHED",
    "METADATA_READY",
    "REVIEW_REQUIRED",
    "APPROVED",
    "PREPARING",
    "READY_TO_COMMIT",
    "COMMITTING",
}
RESUMABLE_STAGING_STATUSES = ONGOING_STAGING_STATUSES - {"COMMITTING"}


@router.get("", response_model=StagingListResponse)
def list_staging_sessions(
    db: Db,
    principal: Actor,
    state: Annotated[Literal["all", "ongoing", "finished"], Query()] = "ongoing",
    owner_id: Annotated[uuid.UUID | None, Query()] = None,
    revision_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> StagingListResponse:
    user = user_for_principal(db, principal)
    conditions = []
    if principal.role != "administrator":
        conditions.append(StagingSession.created_by == user.id)
    elif owner_id is not None:
        conditions.append(StagingSession.created_by == owner_id)
    if state == "ongoing":
        conditions.append(StagingSession.status.in_(ONGOING_STAGING_STATUSES))
    elif state == "finished":
        conditions.append(~StagingSession.status.in_(ONGOING_STAGING_STATUSES))
    if revision_id is not None:
        conditions.append(
            or_(
                StagingSession.base_revision_id == revision_id,
                StagingSession.result_json["revision_id"].astext == str(revision_id),
            )
        )
    total = db.scalar(select(func.count()).select_from(StagingSession).where(*conditions)) or 0
    rows = db.execute(
        select(StagingSession, User.display_name)
        .join(User, User.id == StagingSession.created_by)
        .where(*conditions)
        .order_by(StagingSession.updated_at.desc(), StagingSession.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    ids = [row[0].id for row in rows]
    file_counts = (
        {
            row.staging_id: (int(row.file_count), int(row.verified_file_count))
            for row in db.execute(
                select(
                    StagingFile.staging_id,
                    func.count().label("file_count"),
                    func.count()
                    .filter(StagingFile.state == "VERIFIED")
                    .label("verified_file_count"),
                )
                .where(StagingFile.staging_id.in_(ids))
                .group_by(StagingFile.staging_id)
            )
        }
        if ids
        else {}
    )
    items = []
    for staging, owner_name in rows:
        proposal = staging.proposal_json or {}
        result = staging.result_json or {}
        workflow = result.get("workflow") or {}
        destination = proposal.get("destination") or {}
        item = proposal.get("item") or {}
        items.append(
            StagingSummary(
                id=staging.id,
                status=staging.status,
                operation=staging.operation,
                snapshot_version=staging.snapshot_version,
                base_revision_id=staging.base_revision_id,
                owner_id=staging.created_by,
                owner_name=owner_name,
                file_count=file_counts.get(staging.id, (0, 0))[0],
                verified_file_count=file_counts.get(staging.id, (0, 0))[1],
                item_id=result.get("item_id") or destination.get("item_id"),
                item_number=item.get("item_number"),
                revision_id=result.get("revision_id") or destination.get("revision_id"),
                created_revision_ids=result.get("created_revision_ids") or [],
                created_at=staging.created_at,
                updated_at=staging.updated_at,
                can_cancel=staging.status in RESUMABLE_STAGING_STATUSES,
                can_resume=staging.status in RESUMABLE_STAGING_STATUSES,
                workflow_state=workflow.get("state"),
                workflow_stage=workflow.get("stage"),
                workflow_progress_percent=workflow.get("progress_percent"),
                workflow_error_detail=workflow.get("error_detail"),
            )
        )
    return StagingListResponse(items=items, total=total)


@router.post("/upload-preflight", response_model=UploadPreflightResponse)
def upload_preflight(
    payload: UploadPreflightRequest,
    db: Db,
    principal: Actor,
) -> UploadPreflightResponse:
    """Resolve current exact-file duplicates and Item-number conflicts before upload."""
    require_engineer(principal)
    current_rows = db.execute(
        select(Item, Revision)
        .join(Revision, Revision.id == Item.current_revision_id)
        .where(
            Item.status == "ACTIVE",
            Revision.state.in_(["READY", "READY_WITH_WARNINGS"]),
        )
    ).all()
    hashes_by_revision = {
        revision.id: {
            (row.sha256, row.byte_size)
            for row in current_artifact_files(db, revision.id)
            # An assembly's authoritative Artifact also contains SECONDARY files so
            # that its native package can be reconstructed.  Those dependencies do
            # not represent the assembly Item's own identity: matching them made
            # every component look identical to the parent assembly.  Exact-file
            # identity is therefore based only on the revision's primary native
            # source file.
            if row.kind in {"NATIVE", "STEP"} and row.role == "PRIMARY"
        }
        for _item, revision in current_rows
    }
    results: list[UploadPreflightResult] = []
    for entry in payload.files:
        normalized_number = entry.item_number.strip().lower() if entry.item_number else None
        existing_item = (
            db.scalar(select(Item).where(func.lower(Item.part_number) == normalized_number))
            if normalized_number
            else None
        )
        matches = [
            UploadDuplicateMatch(
                item_id=item.id,
                item_number=item.part_number,
                item_name=item.name,
                item_type=item.part_type,
                revision_id=revision.id,
                revision_code=revision.revision_code,
            )
            for item, revision in current_rows
            if (entry.sha256.lower(), entry.byte_size) in hashes_by_revision[revision.id]
        ]
        results.append(
            UploadPreflightResult(
                logical_path=entry.logical_path,
                item_number_exists=existing_item is not None,
                existing_item_id=existing_item.id if existing_item else None,
                duplicates=matches,
            )
        )
    return UploadPreflightResponse(files=results)


@router.post("", response_model=StagingResponse, status_code=status.HTTP_201_CREATED)
def create_staging(
    payload: StagingCreateRequest, request: Request, db: Db, principal: Actor
) -> StagingResponse:
    require_engineer(principal)
    user = user_for_principal(db, principal)
    package_hash = canonical_package_sha256(
        (item.logical_path, item.sha256) for item in payload.files
    )
    existing = db.scalar(
        select(StagingSession).where(StagingSession.client_staging_id == payload.client_staging_id)
    )
    if existing is not None:
        if (
            existing.created_by != user.id
            or existing.package_sha256 != package_hash
            or existing.operation != payload.operation
            or existing.base_revision_id != payload.base_revision_id
        ):
            raise HTTPException(status_code=409, detail="Staging identity conflict")
        return _staging_response(db, existing)
    staging = StagingSession(
        client_staging_id=payload.client_staging_id,
        operation=payload.operation,
        base_revision_id=payload.base_revision_id,
        package_sha256=package_hash,
        created_by=user.id,
    )
    if payload.operation == "CREATE_REVISION":
        item = db.get(Item, payload.item_id)
        base_revision = db.get(Revision, payload.base_revision_id)
        if (
            item is None
            or base_revision is None
            or base_revision.part_id != item.id
            or item.current_revision_id != base_revision.id
        ):
            raise HTTPException(status_code=409, detail="The target Item revision changed")
    db.add(staging)
    db.flush()
    for item in payload.files:
        file_id = uuid.uuid4()
        db.add(
            StagingFile(
                id=file_id,
                staging_id=staging.id,
                logical_path=item.logical_path,
                filename=item.filename,
                byte_size=item.byte_size,
                sha256=item.sha256,
                mime_type=item.mime_type,
                storage_key=f"staging/{staging.id}/{file_id}/{item.filename}",
            )
        )
    check = DuplicateCheck(
        source_staging_id=staging.id,
        status="COMPLETED",
        algorithm_version="staging-hash-v1",
        candidate_count_before_filter=0,
        candidate_count_after_filter=0,
        exact_match_found=False,
        created_by=user.id,
    )
    db.add(check)
    db.flush()
    # A single-file hash is a complete logical package match. Multi-file manifests
    # are persisted for package-level matching after promotion support is added.
    if len(payload.files) == 1:
        visible = Revision.state.in_(["READY", "READY_WITH_WARNINGS"])
        if principal.role != "administrator":
            visible = or_(visible, Item.created_by == user.id)
        rows = db.execute(
            select(Revision, Item)
            .join(Item, Item.id == Revision.part_id)
            .join(ArtifactFile, ArtifactFile.revision_id == Revision.id)
            .where(
                ArtifactFile.sha256 == payload.files[0].sha256,
                ArtifactFile.kind == "NATIVE",
                ArtifactFile.state == "VERIFIED",
                Item.status == "ACTIVE",
                visible,
            )
            .distinct()
            .limit(10)
        ).all()
    else:
        visible = Revision.state.in_(["READY", "READY_WITH_WARNINGS"])
        if principal.role != "administrator":
            visible = or_(visible, Item.created_by == user.id)
        rows = db.execute(
            select(Revision, Item)
            .join(Item, Item.id == Revision.part_id)
            .where(
                Revision.package_sha256 == package_hash,
                Item.status == "ACTIVE",
                visible,
            )
            .limit(10)
        ).all()
    if payload.files:
        check.candidate_count_before_filter = len(rows)
        check.candidate_count_after_filter = len(rows)
        check.exact_match_found = bool(rows)
        for revision, _item in rows:
            db.add(
                DuplicateCandidate(
                    duplicate_check_id=check.id,
                    candidate_revision_id=revision.id,
                    classification="exact_file_duplicate",
                    score_breakdown_json=asdict(ScoreBreakdown(metadata=1.0, combined=1.0)),
                    reasons_json=["Verified native-file SHA-256 matches."],
                    differences_json=[],
                    recommended_action="reuse_existing",
                )
            )
        if rows:
            staging.status = "REVIEW_REQUIRED"
    record_audit(
        db,
        actor_id=user.id,
        action="staging.created",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={"files": len(payload.files), "package_sha256": package_hash},
    )
    db.commit()
    return _staging_response(db, staging)


@router.post(
    "/items/{item_id}/revisions/{revision_id}/edit",
    response_model=StagingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_edit_staging(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    payload: EditStagingCreateRequest,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> StagingResponse:
    """Acquire a revision checkout and snapshot its Artifact manifest for editing."""
    require_engineer(principal)
    actor = user_for_principal(db, principal)
    item = db.get(Item, item_id)
    revision = db.scalar(select(Revision).where(Revision.id == revision_id).with_for_update())
    if item is None or revision is None or revision.part_id != item.id:
        raise HTTPException(status_code=404, detail="Item revision not found")
    if revision.created_by != actor.id:
        raise HTTPException(status_code=403, detail="Only the revision creator can edit it")
    existing_staging = db.scalar(
        select(StagingSession).where(StagingSession.client_staging_id == payload.client_staging_id)
    )
    if existing_staging is not None:
        if (
            existing_staging.created_by != actor.id
            or existing_staging.operation != "EDIT_ITEM_REVISION"
            or existing_staging.base_revision_id != revision_id
        ):
            raise HTTPException(status_code=409, detail="Staging identity conflict")
        return _staging_response(db, existing_staging)

    if item.current_revision_id != revision.id:
        raise HTTPException(status_code=409, detail="Only the current Item Revision can be edited")
    if revision.lifecycle_state != "WORKING":
        raise HTTPException(
            status_code=409,
            detail="Released revisions are immutable; create a new Item Revision to make changes",
        )
    if revision.state not in {"COMMITTED", "PROCESSING", "READY", "READY_WITH_WARNINGS"}:
        raise HTTPException(status_code=409, detail="This Item Revision is not editable")

    latest_snapshot = db.scalar(
        select(RevisionContentSnapshot)
        .where(RevisionContentSnapshot.revision_id == revision.id)
        .order_by(RevisionContentSnapshot.sequence.desc())
    )
    if latest_snapshot is None:
        raise HTTPException(
            status_code=409,
            detail="This revision has no committed content snapshot and cannot be edited",
        )

    now = datetime.now(UTC)
    checkout = db.scalar(select(Checkout).where(Checkout.revision_id == revision.id))
    if checkout is not None and checkout.expires_at <= now:
        db.delete(checkout)
        db.flush()
        checkout = None
    if checkout is not None:
        if checkout.owner_id != actor.id:
            raise HTTPException(
                status_code=409,
                detail="Revision is already checked out by another user",
            )
        resumable_edit = db.scalar(
            select(StagingSession)
            .where(
                StagingSession.created_by == actor.id,
                StagingSession.operation == "EDIT_ITEM_REVISION",
                StagingSession.base_revision_id == revision.id,
                StagingSession.status.in_(RESUMABLE_STAGING_STATUSES),
            )
            .order_by(StagingSession.updated_at.desc(), StagingSession.id.desc())
        )
        if resumable_edit is not None and payload.source_snapshot_id is None:
            checkout.expires_at = now + timedelta(seconds=settings.checkout_expiry_seconds)
            db.commit()
            return _staging_response(db, resumable_edit)
        if resumable_edit is not None:
            raise HTTPException(
                status_code=409,
                detail="Continue or cancel the current edit before restoring another snapshot",
            )
        # A checkout may have been created by this user's CAD session. Reuse it
        # instead of rejecting the owner, while retaining its original base snapshot.
        if checkout.base_snapshot_id != latest_snapshot.id:
            raise HTTPException(
                status_code=409,
                detail="The existing checkout is based on an outdated content snapshot",
            )
        checkout.expires_at = now + timedelta(seconds=settings.checkout_expiry_seconds)
    else:
        checkout = Checkout(
            part_id=item.id,
            revision_id=revision.id,
            base_snapshot_id=latest_snapshot.id,
            owner_id=actor.id,
            checkout_group_id=uuid.uuid4(),
            root_revision_id=revision.id,
            scope="SINGLE",
            expires_at=now + timedelta(seconds=settings.checkout_expiry_seconds),
        )
        db.add(checkout)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="Revision was checked out concurrently"
            ) from exc

    snapshot = latest_snapshot
    if payload.source_snapshot_id is not None:
        snapshot = db.get(RevisionContentSnapshot, payload.source_snapshot_id)
        if snapshot is None or snapshot.revision_id != revision.id:
            raise HTTPException(status_code=404, detail="Source content snapshot not found")
    relation_rows = db.execute(
        select(RevisionArtifactRelation, Artifact)
        .join(
            RevisionContentSnapshotArtifact,
            RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id,
        )
        .join(Artifact, Artifact.id == RevisionArtifactRelation.artifact_id)
        .where(
            RevisionArtifactRelation.revision_id == revision.id,
            RevisionContentSnapshotArtifact.snapshot_id == snapshot.id,
            Artifact.classification != "DERIVED",
        )
        .order_by(RevisionArtifactRelation.sequence, RevisionArtifactRelation.id)
    ).all()
    bom = _bom_proposal_at_snapshot(db, revision.id, snapshot)
    proposal = {
        "operation": "EDIT_ITEM_REVISION",
        "destination": {
            "item_id": str(item.id),
            "revision_id": str(revision.id),
            "base_snapshot_id": str(latest_snapshot.id),
        },
        "item": {
            "item_number": item.part_number,
            "name": item.name,
            "item_type": item.part_type,
            "description": item.description,
            "category": item.category,
        },
        "revision": {
            "revision_code": revision.revision_code,
            "description": revision.description,
            "cad_system": revision.cad_system,
            "native_format": revision.native_format,
        },
        "artifacts": [
            {
                "client_artifact_id": str(artifact.artifact_key),
                "existing_relation_id": str(relation.id),
                "owner_staged_file_id": None,
                "artifact_type": artifact.artifact_type,
                "name": artifact.name,
                "description": artifact.description,
                "classification": artifact.classification,
                "relation_type": relation.relation_type,
                "sequence": relation.sequence,
                "is_primary": relation.is_primary,
                "files": [],
            }
            for relation, artifact in relation_rows
        ],
        "identity_decisions": [],
        "bom": bom,
    }
    staging = StagingSession(
        client_staging_id=payload.client_staging_id,
        operation="EDIT_ITEM_REVISION",
        status="PREPARING",
        base_revision_id=revision.id,
        base_snapshot_id=latest_snapshot.id,
        package_sha256=revision.package_sha256 or "0" * 64,
        proposal_json=proposal,
        created_by=actor.id,
    )
    db.add(staging)
    db.flush()
    db.add(
        DuplicateCheck(
            source_staging_id=staging.id,
            status="COMPLETED",
            algorithm_version="edit-staging-v1",
            candidate_count_before_filter=0,
            candidate_count_after_filter=0,
            exact_match_found=False,
            created_by=actor.id,
        )
    )
    record_audit(
        db,
        actor_id=actor.id,
        action="staging.edit_created",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={
            "item_id": str(item.id),
            "revision_id": str(revision.id),
            "base_snapshot_id": str(snapshot.id),
            "retained_artifacts": len(relation_rows),
        },
    )
    db.commit()
    return _staging_response(db, staging)


@router.put("/{staging_id}/proposal", response_model=StagingResponse)
def update_staging_proposal(
    staging_id: uuid.UUID,
    payload: StagingProposalUpdate,
    request: Request,
    db: Db,
    principal: Actor,
) -> StagingResponse:
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    if staging.status in {"CANCELLED", "PROMOTED", "COMMITTING", "COMPLETED"}:
        raise HTTPException(status_code=409, detail="Staging session is no longer editable")
    if staging.snapshot_version != payload.expected_snapshot_version:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Staging proposal changed; reload before saving",
                "current_snapshot_version": staging.snapshot_version,
            },
        )
    if payload.operation != staging.operation:
        raise HTTPException(status_code=422, detail="Proposal operation does not match staging")
    if payload.operation == "CREATE_REVISION":
        if (
            payload.destination is None
            or payload.destination.revision_id != staging.base_revision_id
        ):
            raise HTTPException(status_code=409, detail="The target Item revision changed")

    staged_files = list(db.scalars(select(StagingFile).where(StagingFile.staging_id == staging.id)))
    staged_ids = {item.id for item in staged_files}
    staged_by_id = {item.id: item for item in staged_files}
    proposed_ids = [
        item.staged_file_id for artifact in payload.artifacts for item in artifact.files
    ]
    proposed_ids.extend(
        artifact.owner_staged_file_id
        for artifact in payload.artifacts
        if artifact.owner_staged_file_id is not None
    )
    proposed_ids.extend(item.staged_file_id for item in payload.identity_decisions)
    for line in payload.bom:
        if line.parent_staged_file_id is not None:
            proposed_ids.append(line.parent_staged_file_id)
        if line.child_staged_file_id is not None:
            proposed_ids.append(line.child_staged_file_id)
    unknown = set(proposed_ids) - staged_ids
    if unknown:
        unknown_ids = sorted(map(str, unknown))
        raise HTTPException(
            status_code=422,
            detail=f"Proposal references files outside this staging session: {unknown_ids}",
        )
    artifact_file_ids = [
        item.staged_file_id for artifact in payload.artifacts for item in artifact.files
    ]
    native_assembly_package = False
    if not native_assembly_package and payload.revision is not None:
        root = next(
            (
                staged_by_id.get(file.staged_file_id)
                for artifact in payload.artifacts
                if artifact.is_primary and artifact.artifact_type == "CAD_ASSEMBLY"
                for file in artifact.files
                if file.role == "PRIMARY"
            ),
            None,
        )
        native_assembly_package = bool(
            root and _external_assembly_worker(db, root.filename, payload.revision.cad_system)
        )
    if len(artifact_file_ids) != len(set(artifact_file_ids)) and not native_assembly_package:
        raise HTTPException(status_code=422, detail="A staged file may belong to only one Artifact")
    decision_ids = [item.staged_file_id for item in payload.identity_decisions]
    if len(decision_ids) != len(set(decision_ids)):
        raise HTTPException(
            status_code=422, detail="A staged file may have only one identity decision"
        )
    decisions = {item.staged_file_id: item for item in payload.identity_decisions}
    for artifact in payload.artifacts:
        if artifact.existing_relation_id is not None:
            if payload.destination is None:
                raise HTTPException(
                    status_code=422,
                    detail="Retained Artifact relations require an existing revision destination",
                )
            relation = db.get(RevisionArtifactRelation, artifact.existing_relation_id)
            if relation is None or relation.revision_id != payload.destination.revision_id:
                raise HTTPException(
                    status_code=422,
                    detail="A retained Artifact relation is outside the destination revision",
                )
        if artifact.owner_staged_file_id is None:
            continue
        decision = decisions.get(artifact.owner_staged_file_id)
        if decision is None or decision.decision != "CREATE_NEW":
            raise HTTPException(
                status_code=422,
                detail="An Artifact owner must have a CREATE_NEW identity decision",
            )
        if decision.item is None or decision.revision is None:
            raise HTTPException(
                status_code=422,
                detail="A new component Artifact owner requires its Item and Revision proposal",
            )

    proposed_numbers = [payload.item.item_number] if payload.item is not None else []
    proposed_numbers.extend(
        decision.item.item_number
        for decision in payload.identity_decisions
        if decision.decision == "CREATE_NEW" and decision.item is not None
    )
    folded_numbers = [number.strip().casefold() for number in proposed_numbers]
    if len(folded_numbers) != len(set(folded_numbers)):
        raise HTTPException(
            status_code=422, detail="Item numbers must be unique within this upload"
        )

    staging.operation = payload.operation
    staging.base_revision_id = payload.destination.revision_id if payload.destination else None
    staging.base_snapshot_id = payload.destination.base_snapshot_id if payload.destination else None
    staging.proposal_json = payload.model_dump(mode="json", exclude={"expected_snapshot_version"})
    staging.snapshot_version += 1
    staging.status = "PREPARING"
    staging.commit_id = None
    staging.result_json = {}
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.proposal_updated",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={
            "operation": staging.operation,
            "snapshot_version": staging.snapshot_version,
            "artifacts": len(payload.artifacts),
            "bom_lines": len(payload.bom),
        },
    )
    db.commit()
    return _staging_response(db, staging)


@router.post("/{staging_id}/ready", response_model=StagingResponse)
def mark_staging_ready(
    staging_id: uuid.UUID,
    payload: StagingReadyRequest,
    request: Request,
    db: Db,
    principal: Actor,
) -> StagingResponse:
    """Freeze a fully validated proposal so it can be committed atomically."""
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    if staging.status in {"CANCELLED", "PROMOTED", "COMPLETED", "COMMITTING"}:
        raise HTTPException(status_code=409, detail="Staging session is no longer editable")
    if payload.expected_snapshot_version != staging.snapshot_version:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Staging proposal changed; expected snapshot {payload.expected_snapshot_version}, "
                f"current snapshot is {staging.snapshot_version}. Review and validate it again."
            ),
        )
    if not staging.proposal_json:
        raise HTTPException(status_code=422, detail="Complete the item proposal before submitting")
    try:
        proposal = StagingProposalUpdate.model_validate(
            {**staging.proposal_json, "expected_snapshot_version": staging.snapshot_version}
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"The item proposal is incomplete: {exc}"
        ) from exc

    files = list(db.scalars(select(StagingFile).where(StagingFile.staging_id == staging.id)))
    use_existing_ids = {
        item.staged_file_id
        for item in proposal.identity_decisions
        if item.decision == "USE_EXISTING"
    }
    unverified = sorted(
        item.logical_path
        for item in files
        if item.state != "VERIFIED" and item.id not in use_existing_ids
    )
    if unverified:
        raise HTTPException(
            status_code=422,
            detail=f"Upload is incomplete; these files are not verified: {unverified}",
        )
    staged_ids = {item.id for item in files}
    artifact_ids = {
        item.staged_file_id for artifact in proposal.artifacts for item in artifact.files
    }
    unassigned = staged_ids - artifact_ids - use_existing_ids
    if unassigned:
        names = sorted(item.logical_path for item in files if item.id in unassigned)
        raise HTTPException(
            status_code=422,
            detail=f"Choose create new or use existing for every file: {names}",
        )
    if proposal.operation not in {"CREATE_ITEM", "CREATE_REVISION", "EDIT_ITEM_REVISION"}:
        raise HTTPException(
            status_code=422,
            detail=f"{proposal.operation} commit is not available in this phase",
        )
    assert proposal.item is not None
    if proposal.operation == "CREATE_REVISION":
        assert proposal.destination is not None
        item = db.get(Item, proposal.destination.item_id)
        revision = db.get(Revision, proposal.destination.revision_id)
        if (
            item is None
            or revision is None
            or revision.part_id != item.id
            or item.current_revision_id != revision.id
            or staging.base_revision_id != revision.id
        ):
            raise HTTPException(status_code=409, detail="The target Item revision changed")
        if (
            db.scalar(
                select(Revision.id).where(
                    Revision.part_id == item.id,
                    func.lower(Revision.revision_code) == proposal.revision.revision_code.lower(),
                )
            )
            is not None
        ):
            raise HTTPException(
                status_code=409, detail="Revision code already exists for this Item"
            )
    elif proposal.operation == "EDIT_ITEM_REVISION":
        assert proposal.destination is not None
        revision = db.get(Revision, proposal.destination.revision_id)
        checkout = db.scalar(
            select(Checkout).where(
                Checkout.revision_id == proposal.destination.revision_id,
                Checkout.owner_id == staging.created_by,
            )
        )
        latest_snapshot = db.scalar(
            select(RevisionContentSnapshot)
            .where(RevisionContentSnapshot.revision_id == proposal.destination.revision_id)
            .order_by(RevisionContentSnapshot.sequence.desc())
        )
        if (
            revision is None
            or revision.part_id != proposal.destination.item_id
            or checkout is None
            or latest_snapshot is None
            or checkout.base_snapshot_id != latest_snapshot.id
            or latest_snapshot.id != proposal.destination.base_snapshot_id
        ):
            raise HTTPException(
                status_code=409,
                detail="The checked-out revision changed; cancel staging and open Edit again",
            )
        base_relation_ids = set(
            db.scalars(
                select(RevisionContentSnapshotArtifact.relation_id).where(
                    RevisionContentSnapshotArtifact.snapshot_id == latest_snapshot.id
                )
            )
        )
        retained_ids = {
            artifact.existing_relation_id
            for artifact in proposal.artifacts
            if artifact.existing_relation_id is not None
        }
        if not retained_ids <= base_relation_ids:
            raise HTTPException(status_code=409, detail="A retained Artifact is no longer current")
        analysis = (staging.result_json or {}).get("assembly_analysis")
        if analysis:
            if analysis.get("snapshot_version") != staging.snapshot_version:
                raise HTTPException(
                    status_code=422, detail="Assembly analysis is stale; analyze again"
                )
            reconciliation = analysis.get("reconciliation") or []
            blocked = [
                entry for entry in reconciliation if entry.get("status") in {"MISSING", "CONFLICT"}
            ]
            decisions = analysis.get("identity_decisions") or {}
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
            if blocked or unresolved:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Resolve all missing, conflicting, added, and relinked assembly "
                        "references before submitting"
                    ),
                )
        replaces_root_assembly = any(
            artifact.existing_relation_id is None
            and artifact.is_primary
            and artifact.artifact_type == "CAD_ASSEMBLY"
            for artifact in proposal.artifacts
        )
        package_staged = bool(analysis and analysis.get("package_staged"))
        if proposal.item.item_type == "ASSEMBLY" and replaces_root_assembly and not package_staged:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Root assembly replacement requires dependency analysis and BOM "
                    "reconciliation. Keep the current assembly file or cancel this edit; "
                    "assembly package editing will be enabled by the reconciliation workflow."
                ),
            )
    if proposal.bom and proposal.item.item_type != "ASSEMBLY":
        raise HTTPException(status_code=422, detail="Only an ASSEMBLY Item may contain BOM lines")
    if proposal.operation == "EDIT_ITEM_REVISION" and proposal.destination is not None:
        from modelryx.services.bom_cycles import ensure_no_bom_cycle

        ensure_no_bom_cycle(
            db,
            proposal.destination.revision_id,
            {line.child_revision_id for line in proposal.bom if line.child_revision_id is not None},
        )
    decisions = {item.staged_file_id: item for item in proposal.identity_decisions}
    for staged_file_id in use_existing_ids:
        decision = decisions[staged_file_id]
        existing = db.get(Revision, decision.existing_revision_id)
        staged_file = next(item for item in files if item.id == staged_file_id)
        matching_file = db.scalar(
            select(ArtifactFile.id).where(
                ArtifactFile.revision_id == decision.existing_revision_id,
                ArtifactFile.sha256 == staged_file.sha256,
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
                detail=(
                    f"The existing revision selected for {staged_file.logical_path} no longer "
                    "matches that staged file. Choose the duplicate again."
                ),
            )
    duplicate_groups = _duplicate_bom_occurrence_groups(proposal.bom)
    if duplicate_groups:
        files_by_id = {item.id: item for item in files}
        used_names = {
            (line.parent_staged_file_id, line.parent_revision_id, line.instance_name.casefold())
            for line in proposal.bom
        }
        conflicts: list[dict[str, object]] = []
        message_groups: list[str] = []
        for group in duplicate_groups:
            suggestions: list[dict[str, str]] = []
            for line in group:
                staged_file = (
                    files_by_id.get(line.child_staged_file_id)
                    if line.child_staged_file_id is not None
                    else None
                )
                source = (
                    staged_file.logical_path
                    if staged_file is not None
                    else str(line.child_revision_id or "unknown child")
                )
                suffix = "Component"
                base = line.instance_name[: max(1, 255 - len(suffix) - 3)]
                candidate = f"{base} [{suffix}]"
                sequence = 2
                parent_key = (line.parent_staged_file_id, line.parent_revision_id)
                while (*parent_key, candidate.casefold()) in used_names:
                    ending = f" [{suffix}] {sequence}"
                    candidate = f"{line.instance_name[: max(1, 255 - len(ending))]}{ending}"
                    sequence += 1
                used_names.add((*parent_key, candidate.casefold()))
                suggestions.append({"source": source, "suggested_name": candidate})
            conflicts.append({"instance_name": group[0].instance_name, "entries": suggestions})
            rendered = "; ".join(
                f"{item['source']} → {item['suggested_name']}" for item in suggestions
            )
            message_groups.append(f"'{group[0].instance_name}' is used by {rendered}")
        raise HTTPException(
            status_code=422,
            detail={
                "code": "DUPLICATE_BOM_INSTANCE_NAMES",
                "message": (
                    "BOM instance names must be unique within the assembly. "
                    + "Conflicts: "
                    + " | ".join(message_groups)
                    + ". Use the suggested names or otherwise rename each occurrence uniquely."
                ),
                "reason": (
                    "An assembly and part can share a filename stem, but their BOM occurrence "
                    "keys cannot be identical."
                ),
                "conflicts": conflicts,
            },
        )
    for line in proposal.bom:
        if (
            line.parent_staged_file_id is not None
            and line.parent_staged_file_id not in artifact_ids | use_existing_ids
        ):
            raise HTTPException(
                status_code=422,
                detail=f"BOM parent {line.parent_staged_file_id} has no resolved Item Revision",
            )
        if line.parent_staged_file_id is not None:
            parent_decision = decisions.get(line.parent_staged_file_id)
            if parent_decision is None or parent_decision.decision != "CREATE_NEW":
                raise HTTPException(
                    status_code=422,
                    detail=f"BOM parent for {line.instance_name} must be a new assembly Item",
                )
        child_revision_id = line.child_revision_id
        if line.child_staged_file_id is not None:
            decision = decisions.get(line.child_staged_file_id)
            if decision is not None and decision.decision == "CREATE_NEW" and decision.item:
                continue
            child_revision_id = decision.existing_revision_id if decision else None
        if child_revision_id is None or db.get(Revision, child_revision_id) is None:
            raise HTTPException(
                status_code=422,
                detail=f"BOM child for {line.instance_name} has no resolved Item Revision",
            )
        if proposal.operation == "EDIT_ITEM_REVISION":
            assert proposal.destination is not None
            if (
                line.parent_revision_id is not None
                and line.parent_revision_id != proposal.destination.revision_id
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"BOM parent for {line.instance_name} is not the edited revision",
                )
            if child_revision_id == proposal.destination.revision_id:
                raise HTTPException(status_code=422, detail="An assembly cannot contain itself")
    proposed_item_numbers = [proposal.item.item_number]
    proposed_item_numbers.extend(
        decision.item.item_number
        for decision in proposal.identity_decisions
        if decision.decision == "CREATE_NEW" and decision.item is not None
    )
    duplicate_query = select(Item.part_number).where(
        func.lower(Item.part_number).in_(
            [number.strip().lower() for number in proposed_item_numbers]
        )
    )
    if (
        proposal.operation in {"CREATE_REVISION", "EDIT_ITEM_REVISION"}
        and proposal.destination is not None
    ):
        duplicate_query = duplicate_query.where(Item.id != proposal.destination.item_id)
    duplicate = db.scalar(duplicate_query)
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Item number {duplicate} already exists. Enter a different number or choose "
                "the existing item."
            ),
        )
    latest = db.scalar(
        select(ValidationRun)
        .where(ValidationRun.staging_id == staging.id)
        .order_by(ValidationRun.run_number.desc())
    )
    if latest is None or latest.snapshot_version != staging.snapshot_version:
        raise HTTPException(
            status_code=422,
            detail="Validate the current staging proposal before submitting",
        )
    if latest.status not in {"READY", "READY_WITH_WARNINGS"}:
        raise HTTPException(
            status_code=422,
            detail=f"Validation must be resolved before submitting (status: {latest.status})",
        )
    staging.status = "READY_TO_COMMIT"
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.ready_to_commit",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={
            "snapshot_version": staging.snapshot_version,
            "validation_run_id": str(latest.id),
        },
    )
    db.commit()
    return _staging_response(db, staging)


@router.get("/{staging_id}", response_model=StagingResponse)
def get_staging(staging_id: uuid.UUID, db: Db, principal: Actor) -> StagingResponse:
    return _staging_response(db, _owned_staging(db, staging_id, principal))


@router.put("/{staging_id}/assembly-analysis", response_model=StagingResponse)
def save_assembly_analysis(
    staging_id: uuid.UUID,
    payload: AssemblyAnalysisSaveRequest,
    request: Request,
    db: Db,
    principal: Actor,
) -> StagingResponse:
    """Persist a dependency report and its exact input manifest for an assembly edit."""
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    if staging.operation != "EDIT_ITEM_REVISION" or staging.status != "PREPARING":
        raise HTTPException(status_code=409, detail="This staging session cannot accept analysis")
    if staging.snapshot_version != payload.expected_snapshot_version:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Staging proposal changed; expected snapshot {payload.expected_snapshot_version}, "
                f"current snapshot is {staging.snapshot_version}. Analyze the current edit again."
            ),
        )
    item = (staging.proposal_json or {}).get("item") or {}
    if item.get("item_type") != "ASSEMBLY":
        raise HTTPException(status_code=422, detail="Assembly analysis requires an ASSEMBLY Item")

    def normalized_path(value: str) -> str:
        normalized = value.replace("\\", "/").strip("/")
        path = PurePosixPath(normalized)
        if not normalized or path.is_absolute() or ".." in path.parts:
            raise HTTPException(status_code=422, detail=f"Unsafe analysis path: {value}")
        return normalized

    manifest_paths: set[str] = set()
    manifest: list[dict[str, object]] = []
    for entry in payload.files:
        logical_path = normalized_path(entry.logical_path)
        folded = logical_path.casefold()
        if folded in manifest_paths:
            raise HTTPException(status_code=422, detail=f"Duplicate analysis path: {logical_path}")
        manifest_paths.add(folded)
        manifest.append(
            {
                "logical_path": logical_path,
                "filename": entry.filename,
                "byte_size": entry.byte_size,
                "sha256": entry.sha256.lower(),
            }
        )

    report = dict(payload.report)
    if report.get("document_type") != "ASSEMBLY":
        raise HTTPException(status_code=422, detail="The dependency report is not an assembly")
    reported_missing = [
        dict(entry) for entry in report.get("missing_references") or [] if isinstance(entry, dict)
    ]
    external_worker = _external_assembly_worker(
        db,
        str(report.get("root_logical_path") or ""),
        str(((staging.proposal_json or {}).get("revision") or {}).get("cad_system") or ""),
    )
    cad_document_extensions = set(
        external_worker["capabilities"]["sourceExtensions"] if external_worker else []
    )

    def is_missing_supporting_reference(entry: dict[str, Any]) -> bool:
        if str(entry.get("kind") or "").casefold() == "linked-resource":
            return True
        return False

    report["missing_supporting_references"] = [
        entry for entry in reported_missing if is_missing_supporting_reference(entry)
    ]
    report["missing_references"] = [
        entry for entry in reported_missing if not is_missing_supporting_reference(entry)
    ]
    root_path = normalized_path(str(report.get("root_logical_path") or ""))
    if root_path.casefold() not in manifest_paths:
        raise HTTPException(
            status_code=422, detail="The analyzed root file is absent from the manifest"
        )
    report_paths = {
        normalized_path(str(entry.get("logical_path") or "")).casefold()
        for entry in report.get("files", [])
        if isinstance(entry, dict)
    }
    missing_manifest_files = sorted(report_paths - manifest_paths)
    if missing_manifest_files:
        raise HTTPException(
            status_code=422,
            detail=f"Analyzed files are absent from the manifest: {missing_manifest_files}",
        )

    manifest_by_path = {str(entry["logical_path"]).casefold(): entry for entry in manifest}
    base_snapshot = db.get(RevisionContentSnapshot, staging.base_snapshot_id)
    if base_snapshot is None or staging.base_revision_id is None:
        raise HTTPException(status_code=409, detail="The edit base snapshot is unavailable")
    root_manifest = manifest_by_path[root_path.casefold()]
    base_root_file = next(
        (
            file
            for file in current_artifact_files(db, staging.base_revision_id)
            if file.kind == "NATIVE" and file.role == "PRIMARY"
        ),
        None,
    )
    root_content_matches = bool(
        base_root_file is not None
        and base_root_file.sha256 == root_manifest["sha256"]
        and base_root_file.byte_size == root_manifest["byte_size"]
    )
    root_file_reconciliation = {
        "parent_logical_path": None,
        "depth": 0,
        "occurrence_key": root_path,
        "status": "UNCHANGED" if root_content_matches else "MODIFIED",
        "old": None
        if base_root_file is None
        else {
            "revision_id": str(staging.base_revision_id),
            "filename": base_root_file.original_filename,
            "sha256": base_root_file.sha256,
            "byte_size": base_root_file.byte_size,
        },
        "new": root_manifest,
        "reason": (
            "The selected root assembly matches the checked-out revision."
            if root_content_matches
            else "The selected root assembly differs from the checked-out revision."
        ),
    }
    old_lines = _bom_proposal_at_snapshot(db, staging.base_revision_id, base_snapshot)

    child_revision_ids = {
        uuid.UUID(str(line["child_revision_id"]))
        for line in old_lines
        if line.get("child_revision_id")
    }
    child_names = {
        str(revision_id): name
        for revision_id, name in db.execute(
            select(Revision.id, Item.name)
            .join(Item, Item.id == Revision.part_id)
            .where(Revision.id.in_(child_revision_ids))
        )
    }
    checked_out_child_revision_ids = set(
        db.scalars(
            select(Checkout.revision_id).where(
                Checkout.revision_id.in_(child_revision_ids),
                Checkout.owner_id == staging.created_by,
                Checkout.expires_at > datetime.now(UTC),
            )
        )
    )

    def identity_token(value: object) -> str:
        from modelryx.services.cad_routing import identity_token as normalize

        return normalize(value, cad_document_extensions)

    def child_identity_token(value: object) -> str:
        from modelryx.services.cad_routing import identity_token as normalize

        return normalize(value, cad_document_extensions, child=True)

    def matching_old_line(
        reference: dict[str, Any],
        old_by_key: dict[str, dict[str, Any]],
        matched: set[str],
    ) -> tuple[dict[str, Any] | None, bool]:
        exact = reference_key(reference).casefold()
        if exact in old_by_key and exact not in matched:
            return old_by_key[exact], False
        instance_token = identity_token(reference_key(reference))
        child_token = child_identity_token(reference.get("child_logical_path"))
        occurrence_candidates: list[dict[str, Any]] = []
        child_candidates: list[dict[str, Any]] = []
        for old_key, line in old_by_key.items():
            if old_key in matched:
                continue
            old_token = identity_token(old_key)
            stored_child_token = child_identity_token(
                child_names.get(str(line.get("child_revision_id")))
            )
            occurrence_matches = bool(instance_token) and old_token.endswith(instance_token)
            child_matches = bool(child_token) and stored_child_token == child_token
            if occurrence_matches:
                occurrence_candidates.append(line)
            elif child_matches:
                child_candidates.append(line)
        # An occurrence key identifies repeated placements (for example :1 and :2),
        # while a child filename only identifies the shared component. Prefer the
        # occurrence match so a direct subassembly edit is compatible with legacy
        # keys that include ancestor prefixes from the original root upload.
        if len(occurrence_candidates) == 1:
            return occurrence_candidates[0], False
        if len(occurrence_candidates) > 1:
            return None, True
        if len(child_candidates) == 1:
            return child_candidates[0], False
        return None, len(child_candidates) > 1

    def reference_key(entry: dict[str, Any]) -> str:
        return str(
            entry.get("occurrence_key")
            or entry.get("occurrence_path")
            or entry.get("occurrence_id")
            or entry.get("instance_name")
            or ""
        ).strip()

    root_folded = root_path.casefold()
    new_references = [
        dict(entry)
        for entry in report.get("references", [])
        if isinstance(entry, dict)
        and is_managed_bom_reference(entry)
        and normalized_path(str(entry.get("parent_logical_path") or "")).casefold() == root_folded
    ]
    old_by_key = {
        str(line.get("instance_name") or "").strip().casefold(): line
        for line in old_lines
        if str(line.get("instance_name") or "").strip()
    }
    new_key_counts: dict[str, int] = {}
    for reference in new_references:
        key = reference_key(reference).casefold()
        new_key_counts[key] = new_key_counts.get(key, 0) + 1

    reconciliation: list[dict[str, Any]] = []
    matched_old_keys: set[str] = set()
    for reference in new_references:
        key_value = reference_key(reference)
        key = key_value.casefold()
        new_source = normalized_path(str(reference.get("child_logical_path") or ""))
        old, ambiguous_old = matching_old_line(reference, old_by_key, matched_old_keys)
        if not key_value or new_key_counts.get(key, 0) > 1:
            state = "CONFLICT"
            reason = "The analyzed occurrence key is missing or duplicated."
        elif ambiguous_old:
            state = "CONFLICT"
            reason = "The analyzed occurrence cannot be uniquely matched to the current BOM."
        elif old is None:
            state = "ADDED"
            reason = "The analyzed occurrence is not present in the current BOM."
        else:
            matched_old_keys.add(str(old.get("instance_name") or "").strip().casefold())
            old_source = str(old.get("source_reference") or "").replace("\\", "/").strip("/")
            child_revision_id = old.get("child_revision_id")
            new_manifest = manifest_by_path.get(new_source.casefold())
            same_content = False
            if child_revision_id and new_manifest:
                same_content = (
                    db.scalar(
                        select(ArtifactFile.id).where(
                            ArtifactFile.revision_id == uuid.UUID(str(child_revision_id)),
                            ArtifactFile.sha256 == new_manifest["sha256"],
                            ArtifactFile.state == "VERIFIED",
                        )
                    )
                    is not None
                )
            same_reference = not old_source or old_source.casefold() == new_source.casefold()
            child_checked_out = (
                uuid.UUID(str(child_revision_id)) in checked_out_child_revision_ids
                if child_revision_id
                else False
            )
            if same_reference and not same_content and not child_checked_out:
                state = "UNCHANGED"
                reason = (
                    "The occurrence still references the existing child revision. Its local "
                    "file differs, but that child is not checked out, "
                    "so the local bytes are ignored."
                )
            elif same_reference:
                state = "UNCHANGED" if same_content else "MODIFIED"
                reason = (
                    "The occurrence and referenced content match the current BOM."
                    if same_content
                    else "The occurrence is unchanged but its referenced file content differs."
                )
            elif same_content:
                state = "MOVED_RENAMED"
                reason = "The referenced content is unchanged but its logical path changed."
            else:
                state = "RELINKED"
                reason = "The occurrence now resolves to different content or a different path."
        reconciliation.append(
            {
                "parent_logical_path": root_path,
                "depth": 0,
                "occurrence_key": key_value,
                "status": state,
                "old": old,
                "new": reference,
                "reason": reason,
            }
        )

    for key, old in old_by_key.items():
        if key not in matched_old_keys:
            reconciliation.append(
                {
                    "parent_logical_path": root_path,
                    "depth": 0,
                    "occurrence_key": old.get("instance_name"),
                    "status": "REMOVED",
                    "old": old,
                    "new": None,
                    "reason": "The current BOM occurrence is absent from the analyzed assembly.",
                }
            )
    for missing in report.get("missing_references") or []:
        if not isinstance(missing, dict):
            continue
        reconciliation.append(
            {
                "parent_logical_path": str(missing.get("referenced_by") or root_path),
                "depth": 0,
                "occurrence_key": missing.get("reference"),
                "status": "MISSING",
                "old": None,
                "new": missing,
                "reason": "A referenced CAD document was not available to the analyzer.",
            }
        )

    # Reconcile nested assemblies when their root occurrence can be tied to an
    # existing child revision. Added/relinked children remain identity work for Phase 4.
    pending_parents: list[tuple[str, uuid.UUID, int]] = []
    for entry in reconciliation:
        if entry["status"] not in {"UNCHANGED", "MODIFIED", "MOVED_RENAMED"}:
            continue
        old = entry.get("old") or {}
        new = entry.get("new") or {}
        child_revision_id = old.get("child_revision_id")
        child_path = new.get("child_logical_path")
        if child_revision_id and child_path:
            pending_parents.append(
                (normalized_path(str(child_path)), uuid.UUID(str(child_revision_id)), 1)
            )
    visited_nested: set[tuple[str, uuid.UUID]] = set()
    while pending_parents:
        parent_path, parent_revision_id, depth = pending_parents.pop(0)
        visit_key = (parent_path.casefold(), parent_revision_id)
        if visit_key in visited_nested:
            continue
        visited_nested.add(visit_key)
        nested_refs = [
            dict(entry)
            for entry in report.get("references", [])
            if isinstance(entry, dict)
            and is_managed_bom_reference(entry)
            and normalized_path(str(entry.get("parent_logical_path") or "")).casefold()
            == parent_path.casefold()
        ]
        if not nested_refs:
            continue
        nested_view = db.scalar(
            select(BomViewRevision)
            .where(BomViewRevision.revision_id == parent_revision_id)
            .order_by(BomViewRevision.sequence.desc(), BomViewRevision.id.desc())
        )
        nested_lines = (
            []
            if nested_view is None
            else [
                {
                    "parent_revision_id": str(line.parent_revision_id),
                    "child_revision_id": str(line.child_revision_id),
                    "instance_name": line.occurrence_key,
                    "source_reference": line.source_reference,
                    "reference_designator": line.reference_designator,
                    "transform": line.transform,
                    "is_suppressed": line.is_suppressed,
                    "is_visible": line.is_visible,
                    "properties": line.properties_json,
                }
                for line in db.scalars(
                    select(BomLine)
                    .where(BomLine.bom_view_revision_id == nested_view.id)
                    .order_by(BomLine.sequence, BomLine.id)
                )
            ]
        )
        nested_old_by_key = {
            str(line.get("instance_name") or "").strip().casefold(): line
            for line in nested_lines
            if str(line.get("instance_name") or "").strip()
        }
        nested_counts: dict[str, int] = {}
        for reference in nested_refs:
            nested_key = reference_key(reference).casefold()
            nested_counts[nested_key] = nested_counts.get(nested_key, 0) + 1
        nested_matched: set[str] = set()
        for reference in nested_refs:
            key_value = reference_key(reference)
            key = key_value.casefold()
            new_source = normalized_path(str(reference.get("child_logical_path") or ""))
            old, ambiguous_old = matching_old_line(reference, nested_old_by_key, nested_matched)
            if not key_value or nested_counts.get(key, 0) > 1:
                state, reason = "CONFLICT", "The analyzed occurrence key is missing or duplicated."
            elif ambiguous_old:
                state, reason = (
                    "CONFLICT",
                    "The analyzed occurrence cannot be uniquely matched to the current BOM.",
                )
            elif old is None:
                state, reason = (
                    "ADDED",
                    "The analyzed occurrence is not present in the current BOM.",
                )
            else:
                nested_matched.add(str(old.get("instance_name") or "").strip().casefold())
                old_source = str(old.get("source_reference") or "").replace("\\", "/").strip("/")
                child_revision_id = old.get("child_revision_id")
                new_manifest = manifest_by_path.get(new_source.casefold())
                same_content = bool(
                    child_revision_id
                    and new_manifest
                    and db.scalar(
                        select(ArtifactFile.id).where(
                            ArtifactFile.revision_id == uuid.UUID(str(child_revision_id)),
                            ArtifactFile.sha256 == new_manifest["sha256"],
                            ArtifactFile.state == "VERIFIED",
                        )
                    )
                )
                same_reference = not old_source or old_source.casefold() == new_source.casefold()
                child_checked_out = (
                    uuid.UUID(str(child_revision_id)) in checked_out_child_revision_ids
                    if child_revision_id
                    else False
                )
                if same_reference and not same_content and not child_checked_out:
                    state = "UNCHANGED"
                    reason = (
                        "The occurrence still references the existing child revision. Its local "
                        "file differs, but that child is not checked out, "
                        "so the local bytes are ignored."
                    )
                elif same_reference:
                    state = "UNCHANGED" if same_content else "MODIFIED"
                    reason = (
                        "The occurrence content matches."
                        if same_content
                        else "The referenced content differs."
                    )
                elif same_content:
                    state, reason = (
                        "MOVED_RENAMED",
                        "The content is unchanged but its path changed.",
                    )
                else:
                    state, reason = (
                        "RELINKED",
                        "The occurrence resolves to different content or a different path.",
                    )
                if state in {"UNCHANGED", "MODIFIED", "MOVED_RENAMED"} and child_revision_id:
                    pending_parents.append(
                        (new_source, uuid.UUID(str(child_revision_id)), depth + 1)
                    )
            reconciliation.append(
                {
                    "parent_logical_path": parent_path,
                    "depth": depth,
                    "occurrence_key": key_value,
                    "status": state,
                    "old": old,
                    "new": reference,
                    "reason": reason,
                }
            )
        for key, old in nested_old_by_key.items():
            if key not in nested_matched:
                reconciliation.append(
                    {
                        "parent_logical_path": parent_path,
                        "depth": depth,
                        "occurrence_key": old.get("instance_name"),
                        "status": "REMOVED",
                        "old": old,
                        "new": None,
                        "reason": (
                            "The current nested BOM occurrence is absent "
                            "from the analyzed assembly."
                        ),
                    }
                )

    current_rows = db.execute(
        select(Item, Revision)
        .join(Revision, Revision.id == Item.current_revision_id)
        .where(Item.status == "ACTIVE", Revision.state.in_(["READY", "READY_WITH_WARNINGS"]))
    ).all()
    primary_hashes = {
        revision.id: {
            (file.sha256, file.byte_size)
            for file in current_artifact_files(db, revision.id)
            if file.kind in {"NATIVE", "STEP"} and file.role == "PRIMARY"
        }
        for _item, revision in current_rows
    }
    for entry in reconciliation:
        if entry.get("status") not in {"ADDED", "RELINKED"}:
            continue
        reference = entry.get("new") or {}
        child_path = str(reference.get("child_logical_path") or "").casefold()
        candidate_file = manifest_by_path.get(child_path)
        matches = (
            []
            if candidate_file is None
            else [
                {
                    "item_id": str(item.id),
                    "item_number": item.part_number,
                    "item_name": item.name,
                    "item_type": item.part_type,
                    "revision_id": str(revision.id),
                    "revision_code": revision.revision_code,
                }
                for item, revision in current_rows
                if (candidate_file["sha256"], candidate_file["byte_size"])
                in primary_hashes[revision.id]
            ]
        )
        entry["duplicate_candidates"] = matches

    result = dict(staging.result_json or {})
    result["assembly_analysis"] = {
        "snapshot_version": staging.snapshot_version,
        "report": report,
        "files": manifest,
        "root_file_reconciliation": root_file_reconciliation,
        "reconciliation": reconciliation,
    }
    staging.result_json = result
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.assembly_analyzed",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={
            "snapshot_version": staging.snapshot_version,
            "root_logical_path": root_path,
            "files": len(manifest),
            "references": len(report.get("references") or []),
            "missing_references": len(report.get("missing_references") or []),
            "reconciliation": {
                state: sum(1 for entry in reconciliation if entry["status"] == state)
                for state in {
                    "UNCHANGED",
                    "MODIFIED",
                    "ADDED",
                    "REMOVED",
                    "MOVED_RENAMED",
                    "RELINKED",
                    "MISSING",
                    "CONFLICT",
                }
            },
        },
    )
    db.commit()
    return _staging_response(db, staging)


@router.post("/{staging_id}/assembly-reconciliation/apply-safe", response_model=StagingResponse)
def apply_safe_assembly_reconciliation(
    staging_id: uuid.UUID,
    payload: AssemblyReconciliationApplyRequest,
    request: Request,
    db: Db,
    principal: Actor,
) -> StagingResponse:
    """Apply root relationship removals and metadata changes that need no child identity choice."""
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    if staging.operation != "EDIT_ITEM_REVISION" or staging.status != "PREPARING":
        raise HTTPException(status_code=409, detail="This staging session cannot be reconciled")
    if staging.snapshot_version != payload.expected_snapshot_version:
        raise HTTPException(status_code=409, detail="Staging changed; analyze the assembly again")
    result = dict(staging.result_json or {})
    analysis = dict(result.get("assembly_analysis") or {})
    if analysis.get("snapshot_version") != staging.snapshot_version:
        raise HTTPException(status_code=409, detail="Assembly analysis is stale; analyze again")
    report = analysis.get("report") or {}
    root_path = str(report.get("root_logical_path") or "")
    reconciliation = list(analysis.get("reconciliation") or [])
    blocking = [entry for entry in reconciliation if entry.get("status") in {"MISSING", "CONFLICT"}]
    if blocking:
        raise HTTPException(
            status_code=422,
            detail="Resolve missing references and occurrence conflicts before applying changes",
        )

    root_entries = {
        str(entry.get("occurrence_key") or "").casefold(): entry
        for entry in reconciliation
        if entry.get("depth") == 0
        and str(entry.get("parent_logical_path") or "").casefold() == root_path.casefold()
    }
    proposal = dict(staging.proposal_json or {})
    updated_bom: list[dict[str, Any]] = []
    removed = 0
    updated = 0
    for original in proposal.get("bom") or []:
        line = dict(original)
        entry = root_entries.get(str(line.get("instance_name") or "").casefold())
        if entry is None:
            updated_bom.append(line)
            continue
        if entry.get("status") == "REMOVED":
            removed += 1
            continue
        if entry.get("status") not in {"UNCHANGED", "MOVED_RENAMED"}:
            updated_bom.append(line)
            continue
        reference = entry.get("new") or {}
        line.update(
            {
                "instance_name": (
                    reference.get("occurrence_key")
                    or reference.get("occurrence_path")
                    or reference.get("occurrence_id")
                    or line.get("instance_name")
                ),
                "source_reference": reference.get("source_reference")
                or reference.get("child_logical_path"),
                "reference_designator": reference.get("reference_designator")
                or reference.get("instance_name"),
                "transform": reference.get("transform"),
                "is_suppressed": reference.get("is_suppressed", False),
                "is_visible": reference.get("is_visible", True),
                "properties": reference.get("properties") or {},
            }
        )
        updated_bom.append(line)
        updated += 1
    proposal["bom"] = updated_bom
    staging.proposal_json = proposal
    staging.snapshot_version += 1
    analysis["snapshot_version"] = staging.snapshot_version
    analysis["safe_changes_applied"] = True
    result["assembly_analysis"] = analysis
    staging.result_json = result
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.assembly_reconciliation_applied",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={"removed": removed, "updated": updated},
    )
    db.commit()
    return _staging_response(db, staging)


@router.put("/{staging_id}/assembly-identity", response_model=StagingResponse)
def save_assembly_identity_decision(
    staging_id: uuid.UUID,
    payload: AssemblyIdentityDecisionRequest,
    request: Request,
    db: Db,
    principal: Actor,
) -> StagingResponse:
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    if staging.operation != "EDIT_ITEM_REVISION" or staging.status != "PREPARING":
        raise HTTPException(
            status_code=409, detail="This staging session cannot accept identity decisions"
        )
    if staging.snapshot_version != payload.expected_snapshot_version:
        raise HTTPException(status_code=409, detail="Staging changed; analyze the assembly again")
    result = dict(staging.result_json or {})
    analysis = dict(result.get("assembly_analysis") or {})
    if analysis.get("snapshot_version") != staging.snapshot_version:
        raise HTTPException(status_code=409, detail="Assembly analysis is stale; analyze again")
    target = next(
        (
            entry
            for entry in analysis.get("reconciliation") or []
            if str(entry.get("parent_logical_path") or "").casefold()
            == payload.parent_logical_path.casefold()
            and str(entry.get("occurrence_key") or "").casefold()
            == payload.occurrence_key.casefold()
            and entry.get("status") in {"ADDED", "RELINKED"}
        ),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Unresolved assembly occurrence not found")
    if payload.decision == "USE_EXISTING":
        candidates = target.get("duplicate_candidates") or []
        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate.get("revision_id") == str(payload.existing_revision_id)
            ),
            None,
        )
        if selected is None:
            raise HTTPException(status_code=422, detail="Select a verified duplicate revision")
        saved = {
            "decision": "USE_EXISTING",
            "existing_revision_id": str(payload.existing_revision_id),
            "item": None,
            "revision": None,
        }
    else:
        assert payload.item is not None and payload.revision is not None
        duplicate_number = db.scalar(
            select(Item.id).where(
                func.lower(Item.part_number) == payload.item.item_number.strip().casefold()
            )
        )
        if duplicate_number is not None:
            raise HTTPException(status_code=409, detail="The proposed Item number already exists")
        saved = {
            "decision": "CREATE_NEW",
            "existing_revision_id": None,
            "item": payload.item.model_dump(mode="json"),
            "revision": payload.revision.model_dump(mode="json"),
        }
    decisions = dict(analysis.get("identity_decisions") or {})
    decision_key = f"{payload.parent_logical_path.casefold()}::{payload.occurrence_key.casefold()}"
    decisions[decision_key] = saved
    analysis["identity_decisions"] = decisions
    result["assembly_analysis"] = analysis
    staging.result_json = result
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.assembly_identity_decided",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={
            "parent_logical_path": payload.parent_logical_path,
            "occurrence_key": payload.occurrence_key,
            "decision": payload.decision,
        },
    )
    db.commit()
    return _staging_response(db, staging)


@router.post("/{staging_id}/assembly-reconciliation/stage", response_model=StagingResponse)
def stage_reconciled_assembly(
    staging_id: uuid.UUID,
    payload: AssemblyReconciliationApplyRequest,
    request: Request,
    db: Db,
    principal: Actor,
) -> StagingResponse:
    """Translate a resolved analysis and verified package into the edit proposal."""
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    if staging.operation != "EDIT_ITEM_REVISION" or staging.status != "PREPARING":
        raise HTTPException(status_code=409, detail="This staging session cannot stage an assembly")
    if staging.snapshot_version != payload.expected_snapshot_version:
        raise HTTPException(status_code=409, detail="Staging changed; reload before staging")
    result = dict(staging.result_json or {})
    analysis = dict(result.get("assembly_analysis") or {})
    report = analysis.get("report") or {}
    reconciliation = list(analysis.get("reconciliation") or [])
    decisions = dict(analysis.get("identity_decisions") or {})
    report_root = str(report.get("root_logical_path") or "")
    report_system = str(
        ((staging.proposal_json or {}).get("revision") or {}).get("cad_system") or ""
    )
    if _external_assembly_worker(db, report_root, report_system) is not None and any(
        int(entry.get("depth") or 0) > 0
        and entry.get("status") in {"ADDED", "RELINKED", "MODIFIED", "REMOVED"}
        for entry in reconciliation
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Changes inside external subassemblies must be reconciled "
                "in their own revisions first"
            ),
        )
    if not report or report.get("missing_references"):
        raise HTTPException(status_code=422, detail="A complete assembly analysis is required")
    if report.get("managed_bom_supported") is False:
        raise HTTPException(
            status_code=422,
            detail=(
                "Resolve native configuration and component identities before managed BOM staging"
            ),
        )
    if any(entry.get("status") in {"MISSING", "CONFLICT"} for entry in reconciliation):
        raise HTTPException(status_code=422, detail="Resolve assembly conflicts before staging")
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
        raise HTTPException(status_code=422, detail="Resolve every added or relinked reference")

    staged_files = list(db.scalars(select(StagingFile).where(StagingFile.staging_id == staging.id)))
    staged_by_path = {file.logical_path.casefold(): file for file in staged_files}
    manifest = list(analysis.get("files") or [])
    for expected in manifest:
        staged = staged_by_path.get(str(expected.get("logical_path") or "").casefold())
        if (
            staged is None
            or staged.state != "VERIFIED"
            or staged.sha256 != expected.get("sha256")
            or staged.byte_size != expected.get("byte_size")
        ):
            raise HTTPException(
                status_code=422,
                detail=f"Analyzed file is not verified in staging: {expected.get('logical_path')}",
            )
    root_path = str(report.get("root_logical_path") or "")
    root_file = staged_by_path.get(root_path.casefold())
    if root_file is None:
        raise HTTPException(status_code=422, detail="The analyzed root assembly is not staged")
    proposal = dict(staging.proposal_json or {})
    destination = proposal.get("destination") or {}
    root_revision_id = destination.get("revision_id")
    if not root_revision_id:
        raise HTTPException(status_code=422, detail="The edited root revision is unavailable")

    retained = [
        artifact for artifact in proposal.get("artifacts") or [] if not artifact.get("is_primary")
    ]
    package_files = [
        {
            "staged_file_id": str(staged_by_path[str(entry["logical_path"]).casefold()].id),
            "reference_name": str(entry["logical_path"]),
            "role": "PRIMARY"
            if str(entry["logical_path"]).casefold() == root_path.casefold()
            else "SECONDARY",
        }
        for entry in manifest
    ]
    artifacts: list[dict[str, Any]] = [
        *retained,
        {
            "client_artifact_id": str(uuid.uuid4()),
            "existing_relation_id": None,
            "owner_staged_file_id": None,
            "artifact_type": "CAD_ASSEMBLY",
            "name": PurePosixPath(root_path).stem,
            "description": None,
            "classification": "AUTHORITATIVE",
            "relation_type": "SPECIFICATION",
            "sequence": len(retained),
            "is_primary": True,
            "files": package_files,
        },
    ]
    identity_decisions: list[dict[str, Any]] = []
    bom: list[dict[str, Any]] = []
    root_entries = [
        entry
        for entry in reconciliation
        if entry.get("depth") == 0
        and str(entry.get("parent_logical_path") or "").casefold() == root_path.casefold()
        and entry.get("status") != "REMOVED"
    ]
    for entry in root_entries:
        reference = entry.get("new") or {}
        child_path = str(reference.get("child_logical_path") or "")
        child_file = staged_by_path.get(child_path.casefold())
        old = entry.get("old") or {}
        child_revision_id = old.get("child_revision_id")
        child_staged_file_id = None
        if entry.get("status") in {"ADDED", "RELINKED"}:
            key = (
                f"{str(entry.get('parent_logical_path') or '').casefold()}::"
                f"{str(entry.get('occurrence_key') or '').casefold()}"
            )
            decision = decisions[key]
            if child_file is None:
                raise HTTPException(
                    status_code=422, detail=f"Referenced file is not staged: {child_path}"
                )
            if decision["decision"] == "USE_EXISTING":
                child_revision_id = decision["existing_revision_id"]
                identity = {
                    "staged_file_id": str(child_file.id),
                    "decision": "USE_EXISTING",
                    "existing_revision_id": child_revision_id,
                    "item": None,
                    "revision": None,
                }
                previous = next(
                    (
                        value
                        for value in identity_decisions
                        if value["staged_file_id"] == str(child_file.id)
                    ),
                    None,
                )
                if previous is not None and previous != identity:
                    raise HTTPException(
                        status_code=422, detail=f"Conflicting identity decisions for {child_path}"
                    )
                if previous is None:
                    identity_decisions.append(identity)
            else:
                child_revision_id = None
                child_staged_file_id = str(child_file.id)
                identity = {
                    "staged_file_id": str(child_file.id),
                    "decision": "CREATE_NEW",
                    "existing_revision_id": None,
                    "item": decision["item"],
                    "revision": decision["revision"],
                }
                previous = next(
                    (
                        value
                        for value in identity_decisions
                        if value["staged_file_id"] == str(child_file.id)
                    ),
                    None,
                )
                if previous is not None and previous != identity:
                    raise HTTPException(
                        status_code=422, detail=f"Conflicting identity decisions for {child_path}"
                    )
                if previous is not None:
                    # Repeated occurrences share one definition and Item identity.
                    continue_create_artifact = False
                else:
                    identity_decisions.append(identity)
                    continue_create_artifact = True
                child_type = (decision.get("item") or {}).get("item_type")
                if continue_create_artifact:
                    artifacts.append(
                        {
                            "client_artifact_id": str(uuid.uuid4()),
                            "existing_relation_id": None,
                            "owner_staged_file_id": str(child_file.id),
                            "artifact_type": "CAD_ASSEMBLY"
                            if child_type == "ASSEMBLY"
                            else "CAD_MODEL",
                            "name": (decision.get("item") or {}).get("name") or child_file.filename,
                            "description": None,
                            "classification": "AUTHORITATIVE",
                            "relation_type": "SPECIFICATION",
                            "sequence": len(artifacts),
                            "is_primary": True,
                            "files": [
                                {
                                    "staged_file_id": str(child_file.id),
                                    "reference_name": "MODEL",
                                    "role": "PRIMARY",
                                }
                            ],
                        }
                    )
        if child_revision_id is None and child_staged_file_id is None:
            raise HTTPException(
                status_code=422,
                detail=f"Occurrence has no resolved child: {entry.get('occurrence_key')}",
            )
        bom.append(
            {
                "parent_staged_file_id": None,
                "parent_revision_id": root_revision_id,
                "child_staged_file_id": child_staged_file_id,
                "child_revision_id": child_revision_id,
                "instance_name": entry.get("occurrence_key"),
                "quantity": 1,
                "source_reference": reference.get("source_reference") or child_path,
                "reference_designator": reference.get("reference_designator")
                or reference.get("instance_name"),
                "transform": reference.get("transform")
                or [
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                ],
                "is_suppressed": reference.get("is_suppressed", False),
                "is_visible": reference.get("is_visible", True),
                "properties": reference.get("properties") or {},
            }
        )
    proposal["artifacts"] = artifacts
    proposal["identity_decisions"] = identity_decisions
    proposal["bom"] = bom
    extension = PurePosixPath(root_path).suffix.casefold()
    revision = dict(proposal.get("revision") or {})
    worker = _external_assembly_worker(db, root_path, str(revision.get("cad_system") or ""))
    if worker is None:
        raise HTTPException(
            status_code=422, detail="No enabled external worker supports this assembly root"
        )
    revision["cad_system"] = worker["capabilities"]["cadSystem"]
    revision["native_format"] = extension.lstrip(".").upper()
    proposal["revision"] = revision
    staging.proposal_json = proposal
    staging.snapshot_version += 1
    analysis["snapshot_version"] = staging.snapshot_version
    analysis["package_staged"] = True
    result["assembly_analysis"] = analysis
    staging.result_json = result
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.assembly_package_staged",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={"files": len(manifest), "bom_lines": len(bom)},
    )
    db.commit()
    return _staging_response(db, staging)


@router.post(
    "/{staging_id}/assembly-reconciliation/adopt",
    response_model=StagingResponse,
)
def adopt_coordinated_assembly_analysis(
    staging_id: uuid.UUID,
    payload: CoordinatedAssemblyAnalysisRequest,
    request: Request,
    db: Db,
    principal: Actor,
) -> StagingResponse:
    """Attach a verified subtree of a root analysis to a coordinated child edit."""
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    source = _owned_staging(db, payload.source_staging_id, principal)
    if staging.operation != "EDIT_ITEM_REVISION" or staging.status != "PREPARING":
        raise HTTPException(status_code=409, detail="This child edit cannot adopt analysis")
    if staging.snapshot_version != payload.expected_snapshot_version:
        raise HTTPException(
            status_code=409, detail="Child staging changed; reload before continuing"
        )
    source_analysis = dict((source.result_json or {}).get("assembly_analysis") or {})
    if (
        not source_analysis
        or source_analysis.get("snapshot_version") != source.snapshot_version
        or not source_analysis.get("package_staged")
    ):
        raise HTTPException(status_code=422, detail="The root assembly analysis is not current")
    report = dict(source_analysis.get("report") or {})
    root_path = payload.root_logical_path.replace("\\", "/")
    manifest = list(source_analysis.get("files") or [])
    if not any(
        str(entry.get("logical_path") or "").casefold() == root_path.casefold()
        for entry in manifest
    ):
        raise HTTPException(
            status_code=422, detail="The child assembly is absent from the analysis"
        )
    direct_reconciliation = [
        {**entry, "depth": 0}
        for entry in source_analysis.get("reconciliation") or []
        if str(entry.get("parent_logical_path") or "").casefold() == root_path.casefold()
    ]
    if any(entry.get("status") in {"MISSING", "CONFLICT"} for entry in direct_reconciliation):
        raise HTTPException(status_code=422, detail="The child assembly subtree has conflicts")
    source_decisions = dict(source_analysis.get("identity_decisions") or {})
    decisions = {
        key: value
        for key, value in source_decisions.items()
        if key.startswith(f"{root_path.casefold()}::")
    }
    unresolved = [
        entry
        for entry in direct_reconciliation
        if entry.get("status") in {"ADDED", "RELINKED"}
        and (f"{root_path.casefold()}::{str(entry.get('occurrence_key') or '').casefold()}")
        not in decisions
    ]
    if unresolved:
        raise HTTPException(status_code=422, detail="Resolve every child assembly identity")
    staged_files = list(db.scalars(select(StagingFile).where(StagingFile.staging_id == staging.id)))
    child_analysis = {
        "snapshot_version": staging.snapshot_version,
        "report": {
            "root_logical_path": root_path,
            "files": [
                entry
                for entry in report.get("files") or []
                if str(entry.get("logical_path") or "").casefold() == root_path.casefold()
            ],
            "references": [
                entry
                for entry in report.get("references") or []
                if is_managed_bom_reference(entry)
                and str(entry.get("parent_logical_path") or "").casefold() == root_path.casefold()
            ],
            "missing_references": [],
            "validation_issues": [],
        },
        "files": [
            {
                "logical_path": row.logical_path,
                "filename": row.filename,
                "byte_size": row.byte_size,
                "sha256": row.sha256,
            }
            for row in staged_files
        ],
        "reconciliation": direct_reconciliation,
        "identity_decisions": decisions,
        "safe_changes_applied": True,
        "package_staged": True,
        "source_staging_id": str(source.id),
        "source_root_logical_path": root_path,
    }
    result = dict(staging.result_json or {})
    result["assembly_analysis"] = child_analysis
    staging.result_json = result
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.assembly_analysis_adopted",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={"source_staging_id": str(source.id), "root_logical_path": root_path},
    )
    db.commit()
    return _staging_response(db, staging)


@router.post("/{staging_id}/files", response_model=StagingResponse, status_code=201)
def add_staging_file(
    staging_id: uuid.UUID,
    payload: StagingFileAddRequest,
    request: Request,
    db: Db,
    principal: Actor,
) -> StagingResponse:
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    if staging.operation != "EDIT_ITEM_REVISION" or staging.status != "PREPARING":
        raise HTTPException(status_code=409, detail="This staging session cannot accept files")
    if staging.snapshot_version != payload.expected_snapshot_version:
        raise HTTPException(
            status_code=409, detail="Staging proposal changed; reload before adding"
        )
    logical_path = payload.logical_path.replace("\\", "/").strip("/")
    path = PurePosixPath(logical_path)
    if not logical_path or path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=422, detail="Unsafe staged file path")
    if (
        db.scalar(
            select(StagingFile.id).where(
                StagingFile.staging_id == staging.id,
                func.lower(StagingFile.logical_path) == logical_path.lower(),
            )
        )
        is not None
    ):
        raise HTTPException(status_code=409, detail="A staged file already uses this path")
    file_id = uuid.uuid4()
    db.add(
        StagingFile(
            id=file_id,
            staging_id=staging.id,
            logical_path=logical_path,
            filename=payload.filename,
            byte_size=payload.byte_size,
            sha256=payload.sha256.lower(),
            mime_type=payload.mime_type,
            storage_key=f"staging/{staging.id}/{file_id}/{payload.filename}",
        )
    )
    staging.snapshot_version += 1
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.file_added",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={"logical_path": logical_path, "snapshot_version": staging.snapshot_version},
    )
    db.commit()
    return _staging_response(db, staging)


@router.delete("/{staging_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_staging(staging_id: uuid.UUID, request: Request, db: Db, principal: Actor) -> None:
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    actor = user_for_principal(db, principal)
    if staging.status not in RESUMABLE_STAGING_STATUSES:
        raise HTTPException(
            status_code=409, detail="This staging session cannot be cancelled safely"
        )
    staging.status = "CANCELLED"
    if staging.operation == "EDIT_ITEM_REVISION" and staging.base_revision_id is not None:
        checkout = db.scalar(
            select(Checkout).where(
                Checkout.revision_id == staging.base_revision_id,
                Checkout.owner_id == staging.created_by,
            )
        )
        if checkout is not None:
            active_cad = db.scalar(
                select(CADSession.id).where(
                    CADSession.revision_id == staging.base_revision_id,
                    CADSession.status.in_(
                        {
                            "CREATED",
                            "OPENING",
                            "READY",
                            "EXECUTING",
                            "VERIFYING",
                            "READY_TO_REVIEW",
                        }
                    ),
                )
            )
            if active_cad is None:
                db.delete(checkout)
    record_audit(
        db,
        actor_id=actor.id,
        action="staging.cancelled",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={"operation": staging.operation, "owner_id": str(staging.created_by)},
    )
    db.commit()


@router.patch("/{staging_id}/workspace", status_code=status.HTTP_204_NO_CONTENT)
def update_staging_workspace_state(
    staging_id: uuid.UUID,
    payload: StagingWorkspaceActionRequest,
    request: Request,
    db: Db,
    principal: Actor,
) -> None:
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    if payload.action == "REMOVE" and staging.status not in {"COMPLETED", "PROMOTED", "CANCELLED"}:
        raise HTTPException(
            status_code=409,
            detail="Cancel an unfinished task before removing it from Workspace",
        )
    result = dict(staging.result_json or {})
    workspace = dict(result.get("workspace") or {})
    workspace["state"] = {
        "ARCHIVE": "ARCHIVED",
        "RESTORE": "ACTIVE",
        "REMOVE": "REMOVED",
    }[payload.action]
    result["workspace"] = workspace
    staging.result_json = result
    actor = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=actor.id,
        action=f"staging.workspace_{payload.action.casefold()}",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={"status": staging.status},
    )
    db.commit()


@router.delete("/{staging_id}/files/{file_id}", response_model=StagingResponse)
def remove_staging_file(
    staging_id: uuid.UUID,
    file_id: uuid.UUID,
    expected_snapshot_version: int,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> StagingResponse:
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    if staging.operation != "EDIT_ITEM_REVISION" or staging.status != "PREPARING":
        raise HTTPException(status_code=409, detail="This staging session cannot remove files")
    if staging.snapshot_version != expected_snapshot_version:
        raise HTTPException(
            status_code=409, detail="Staging proposal changed; reload before removing"
        )
    staged_file = db.get(StagingFile, file_id)
    if staged_file is None or staged_file.staging_id != staging.id:
        raise HTTPException(status_code=404, detail="Staged file not found")
    referenced_ids = {
        str(file.get("staged_file_id"))
        for artifact in (staging.proposal_json or {}).get("artifacts", [])
        for file in artifact.get("files", [])
    }
    if str(staged_file.id) in referenced_ids:
        raise HTTPException(status_code=409, detail="Remove the Artifact before removing its file")
    LocalStorageProvider(settings.storage_root).delete(staged_file.storage_key)
    db.delete(staged_file)
    staging.snapshot_version += 1
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.file_removed",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={"file_id": str(file_id), "snapshot_version": staging.snapshot_version},
    )
    db.commit()
    return _staging_response(db, staging)


@router.post("/{staging_id}/metadata", response_model=StagingResponse)
def add_staging_metadata(
    staging_id: uuid.UUID,
    payload: StagingMetadataRequest,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> StagingResponse:
    require_engineer(principal)
    staging = _owned_staging(db, staging_id, principal)
    if staging.status in {"CANCELLED", "PROMOTED"}:
        raise HTTPException(status_code=409, detail="Staging session is no longer editable")
    if (payload.source_logical_path is None) != (payload.source_sha256 is None):
        raise HTTPException(
            status_code=422,
            detail="Local metadata must identify both its source path and SHA-256",
        )
    if payload.source_logical_path is not None:
        source_file = db.scalar(
            select(StagingFile).where(
                StagingFile.staging_id == staging.id,
                StagingFile.logical_path == payload.source_logical_path,
            )
        )
        if source_file is None:
            raise HTTPException(
                status_code=422,
                detail="Metadata source is not in this staging package",
            )
        if source_file.sha256.casefold() != payload.source_sha256.casefold():
            raise HTTPException(
                status_code=409,
                detail="Local metadata does not match the staged source file SHA-256",
            )
    try:
        validate_part_metadata(payload.metadata)
        validate_engineering_metadata(payload.metadata)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if MetadataService.status_for(payload.metadata) == "INVALID":
        raise HTTPException(status_code=422, detail="Complete geometry metadata is required")
    staging.metadata_json = payload.metadata
    if payload.source_logical_path is not None:
        result = dict(staging.result_json or {})
        result["local_metadata"] = {
            "source_logical_path": payload.source_logical_path,
            "source_sha256": payload.source_sha256.casefold(),
            "analyzer": payload.analyzer,
        }
        staging.result_json = result
    staging.status = "METADATA_READY"
    source_embedding = None
    gateway = AiGateway(settings)
    if gateway.embeddings_enabled:
        try:
            source_embedding = gateway.embed([build_semantic_text(payload.metadata)])[0]
        except HTTPException:
            source_embedding = None
    check = build_duplicate_repository(db, settings).run_staging(
        staging,
        actor_id=staging.created_by,
        administrator=principal.role == "administrator",
        source_embedding=source_embedding,
    )
    db.flush()
    candidate_exists = db.scalar(
        select(DuplicateCandidate.id)
        .where(DuplicateCandidate.duplicate_check_id == check.id)
        .limit(1)
    )
    staging.status = "REVIEW_REQUIRED" if candidate_exists else "APPROVED"
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.metadata_added",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={
            "schema_version": payload.metadata["schema_version"],
            "duplicate_check_id": str(check.id),
            "review_required": bool(candidate_exists),
        },
    )
    db.commit()
    return _staging_response(db, staging)


@router.put("/files/{file_id}/content", response_model=StagingResponse)
async def upload_staging_file(
    file_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> StagingResponse:
    staged_file = db.get(StagingFile, file_id)
    if staged_file is None:
        raise HTTPException(status_code=404, detail="Staged file not found")
    staging = _owned_staging(db, staged_file.staging_id, principal)
    if staging.status in {"CANCELLED", "PROMOTED"}:
        raise HTTPException(status_code=409, detail="Staging session is no longer editable")
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type != staged_file.mime_type:
        raise HTTPException(status_code=415, detail="Content-Type does not match manifest")
    storage = LocalStorageProvider(settings.storage_root)
    final_path = storage.create_upload(staged_file.storage_key)
    temporary = final_path.with_suffix(final_path.suffix + f".{uuid.uuid4().hex}.upload")
    digest = hashlib.sha256()
    received = 0
    try:
        with temporary.open("xb") as output:
            async for chunk in request.stream():
                received += len(chunk)
                if received > staged_file.byte_size or received > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Upload exceeds declared size")
                digest.update(chunk)
                output.write(chunk)
        if received != staged_file.byte_size or digest.hexdigest() != staged_file.sha256:
            raise HTTPException(status_code=422, detail="Upload does not match manifest")
        os.replace(temporary, final_path)
    finally:
        temporary.unlink(missing_ok=True)
    staged_file.state = "VERIFIED"
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.file_verified",
        entity_type="staging_file",
        entity_id=staged_file.id,
        request_id=request.state.request_id,
        details={"sha256": staged_file.sha256, "byte_size": staged_file.byte_size},
    )
    db.commit()
    return _staging_response(db, staging)


@router.post("/{staging_id}/reuse", response_model=StagingPromotionResponse)
def reuse_staging(
    staging_id: uuid.UUID, request: Request, db: Db, principal: Actor
) -> StagingPromotionResponse:
    staging = _owned_staging(db, staging_id, principal)
    check = db.scalar(
        select(DuplicateCheck)
        .where(DuplicateCheck.source_staging_id == staging.id)
        .order_by(DuplicateCheck.created_at.desc())
    )
    feedback = (
        db.scalar(select(DuplicateFeedback).where(DuplicateFeedback.duplicate_check_id == check.id))
        if check
        else None
    )
    if feedback is None or feedback.user_decision != "USE_EXISTING" or not feedback.candidate_id:
        raise HTTPException(status_code=409, detail="Use Existing decision is required")
    candidate = db.get(DuplicateCandidate, feedback.candidate_id)
    revision = db.get(Revision, candidate.candidate_revision_id) if candidate else None
    if revision is None:
        raise HTTPException(status_code=409, detail="Selected candidate is unavailable")
    staging.status = "PROMOTED"
    record_audit(
        db,
        actor_id=staging.created_by,
        action="staging.reused",
        entity_type="staging",
        entity_id=staging.id,
        request_id=request.state.request_id,
        details={"revision_id": str(revision.id)},
    )
    db.commit()
    return StagingPromotionResponse(
        staging_id=staging.id,
        item_id=revision.part_id,
        revision_id=revision.id,
        reused=True,
    )
