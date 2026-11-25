import uuid
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PartCreate(BaseModel):
    part_number: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    category: str | None = Field(default=None, max_length=100)

    @field_validator("part_number", "name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        if not (value := value.strip()):
            raise ValueError("must not be blank")
        return value


class PartUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    category: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, pattern=r"^(ACTIVE|ARCHIVED)$")

    @field_validator("name")
    @classmethod
    def name_cannot_be_null_or_blank(cls, value: str | None) -> str:
        if value is None or not (value := value.strip()):
            raise ValueError("name must not be null or blank")
        return value


class ProvisionalIdentityResolve(BaseModel):
    part_number: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    name: str | None = Field(default=None, min_length=1, max_length=255)


class PartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    part_number: str
    name: str
    description: str | None
    category: str | None
    status: str
    part_type: str
    current_revision_id: uuid.UUID | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    checkout_status: str = "AVAILABLE"


class PartListResponse(BaseModel):
    items: list[PartResponse]
    total: int
    limit: int
    offset: int


class ItemCreate(BaseModel):
    item_number: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    category: str | None = Field(default=None, max_length=100)
    item_type: str = Field(default="PART", pattern=r"^(PART|ASSEMBLY)$")

    @field_validator("item_number", "name")
    @classmethod
    def strip_item_required(cls, value: str) -> str:
        if not (value := value.strip()):
            raise ValueError("must not be blank")
        return value


class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    category: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, pattern=r"^(ACTIVE|ARCHIVED)$")

    @field_validator("name")
    @classmethod
    def item_name_cannot_be_null_or_blank(cls, value: str | None) -> str:
        if value is None or not (value := value.strip()):
            raise ValueError("name must not be null or blank")
        return value


class ItemIdentityResolve(BaseModel):
    item_number: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str | None = Field(default=None, min_length=1, max_length=255)


class ItemResponse(BaseModel):
    id: uuid.UUID
    item_number: str
    name: str
    description: str | None
    category: str | None
    status: str
    item_type: str
    current_revision_id: uuid.UUID | None
    current_revision_code: str | None = None
    cad_system: str | None = None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    checkout_status: str = "AVAILABLE"


class ItemListResponse(BaseModel):
    items: list[ItemResponse]
    total: int
    limit: int
    offset: int
    facets: dict[str, list[str | None]] | None = None


class ItemRevisionResponse(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    revision_code: str
    description: str | None
    cad_system: str
    native_format: str
    metadata_json: dict[str, Any]
    derived_metadata_json: dict[str, Any] | None
    state: str
    lifecycle_state: str
    metadata_status: str
    created_by: uuid.UUID
    created_at: datetime
    committed_at: datetime | None
    released_by: uuid.UUID | None
    released_at: datetime | None
    current_metadata_extraction_id: uuid.UUID | None
    checkout_status: str = "AVAILABLE"
    approval_status: str | None = None
    approval_request_id: uuid.UUID | None = None
    approval_snapshot_id: uuid.UUID | None = None


class ItemHistoryEntry(BaseModel):
    id: uuid.UUID
    action: str
    changed_by: str
    details: dict[str, Any]
    occurred_at: datetime


class ItemRevisionFileEntry(BaseModel):
    id: uuid.UUID
    role: str
    item_id: uuid.UUID
    item_number: str
    item_name: str
    revision_id: uuid.UUID
    revision_code: str
    kind: str
    filename: str
    mime_type: str
    byte_size: int
    state: str
    download_url: str


class ItemSearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=100)
    cad_system: str | None = Field(default=None, max_length=100)
    revision_state: str | None = Field(default=None, max_length=32)
    material: str | None = Field(default=None, max_length=255)
    bbox_max_mm_gte: float | None = Field(default=None, ge=0)
    bbox_max_mm_lte: float | None = Field(default=None, ge=0)
    volume_mm3_gte: float | None = Field(default=None, ge=0)
    volume_mm3_lte: float | None = Field(default=None, ge=0)
    solid_count: int | None = Field(default=None, ge=0)
    item_type: str | None = Field(default=None, pattern=r"^(PART|ASSEMBLY)$")
    include_incomplete: bool = False
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ItemSearchItem(BaseModel):
    item: ItemResponse
    current_revision: ItemRevisionResponse | None


class ItemSearchResponse(BaseModel):
    items: list[ItemSearchItem]
    total: int
    limit: int
    offset: int


class AttachmentVersionPlan(BaseModel):
    client_version_id: uuid.UUID | None = None
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    comment: str | None = Field(default=None, max_length=10_000)
    scope: Literal["ITEM", "REVISION"] = "ITEM"
    revision_id: uuid.UUID | None = None
    duplicate_action: Literal["UPLOAD", "REUSE_EXISTING"] = "UPLOAD"

    @model_validator(mode="after")
    def normalize_attachment_version(self) -> "AttachmentVersionPlan":
        self.sha256 = self.sha256.lower()
        if PurePosixPath(self.filename).name != self.filename or "\\" in self.filename:
            raise ValueError("filename must be a plain file name")
        if self.scope == "REVISION" and self.revision_id is None:
            raise ValueError("revision_id is required for REVISION scope")
        if self.scope == "ITEM" and self.revision_id is not None:
            raise ValueError("revision_id is only valid for REVISION scope")
        return self


class AdditionalFilePreflightRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    attachment_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def normalize_preflight(self) -> "AdditionalFilePreflightRequest":
        self.name = self.name.strip()
        self.sha256 = self.sha256.lower()
        if not self.name:
            raise ValueError("name must not be blank")
        if PurePosixPath(self.filename).name != self.filename or "\\" in self.filename:
            raise ValueError("filename must be a plain file name")
        return self


class AdditionalFileDuplicateCandidate(BaseModel):
    attachment_id: uuid.UUID
    attachment_name: str
    attachment_type: str
    version_id: uuid.UUID
    version_number: int
    filename: str
    scope: Literal["ITEM", "REVISION"]
    revision_id: uuid.UUID | None = None


class AdditionalFilePreflightResponse(BaseModel):
    classification: str
    upload_required: bool
    reusable_content: bool
    candidates: list[AdditionalFileDuplicateCandidate] = Field(default_factory=list)


class AttachmentCreate(AttachmentVersionPlan):
    name: str = Field(min_length=1, max_length=255)
    attachment_type: str = Field(
        default="OTHER",
        pattern=r"^(DRAWING|DOCUMENT|SPECIFICATION|IMAGE|SPREADSHEET|CERTIFICATE|OTHER)$",
    )
    description: str | None = Field(default=None, max_length=10_000)


class AttachmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("name")
    @classmethod
    def attachment_name_not_blank(cls, value: str | None) -> str:
        if value is None or not (value := value.strip()):
            raise ValueError("name must not be null or blank")
        return value


class AttachmentVersionResponse(BaseModel):
    id: uuid.UUID
    attachment_id: uuid.UUID
    client_version_id: uuid.UUID | None
    version_number: int
    state: str
    filename: str
    mime_type: str
    byte_size: int
    sha256: str
    comment: str | None
    scope: Literal["ITEM", "REVISION"]
    revision_id: uuid.UUID | None
    created_by: uuid.UUID
    created_at: datetime
    expires_at: datetime | None
    ready_at: datetime | None
    upload_url: str | None = None
    download_url: str | None = None


class AttachmentResponse(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    name: str
    attachment_type: str
    description: str | None
    lifecycle_state: Literal["ACTIVE", "ARCHIVED"]
    created_by: uuid.UUID
    created_at: datetime
    latest_ready_version: AttachmentVersionResponse | None
    versions: list[AttachmentVersionResponse]
    checkout_status: Literal["AVAILABLE", "CHECKED_OUT_BY_ME", "CHECKED_OUT_BY_OTHER"] = "AVAILABLE"


class AttachmentCheckoutResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    attachment_id: uuid.UUID | None
    base_version_id: uuid.UUID | None
    revision_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    owner_id: uuid.UUID
    acquired_at: datetime
    expires_at: datetime


class AdditionalFileStageCreate(AttachmentCreate):
    client_staging_id: uuid.UUID
    attachment_id: uuid.UUID | None = None
    duplicate_confirmed: bool = False


class AdditionalFileStageCommit(BaseModel):
    commit_id: uuid.UUID


class AdditionalFileRemoveStageCreate(BaseModel):
    scope: Literal["ITEM", "REVISION"]
    revision_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "AdditionalFileRemoveStageCreate":
        if self.scope == "REVISION" and self.revision_id is None:
            raise ValueError("revision_id is required for REVISION scope")
        if self.scope == "ITEM" and self.revision_id is not None:
            raise ValueError("revision_id is only valid for REVISION scope")
        return self


class AdditionalFileStageResponse(BaseModel):
    id: uuid.UUID
    client_staging_id: uuid.UUID
    item_id: uuid.UUID
    revision_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    attachment_id: uuid.UUID | None
    base_version_id: uuid.UUID | None
    action: Literal["CREATE", "NEW_VERSION", "REMOVE"]
    status: Literal["DRAFT", "READY", "COMPLETED", "CANCELLED"]
    name: str
    attachment_type: str
    description: str | None
    filename: str
    mime_type: str
    byte_size: int
    sha256: str
    comment: str | None
    duplicate_action: Literal["UPLOAD", "REUSE_EXISTING"]
    upload_url: str | None
    commit_id: uuid.UUID | None
    result: dict
    created_at: datetime
    updated_at: datetime


class RevisionRelationshipCreate(BaseModel):
    target_revision_id: uuid.UUID | None = None
    target_item_id: uuid.UUID | None = None
    relationship_type: str = Field(
        pattern=r"^(SPECIFICATION|MATERIAL_SPEC|REFERENCE|TEST_PROCEDURE)$"
    )
    note: str | None = Field(default=None, max_length=10_000)

    @model_validator(mode="after")
    def require_target(self) -> "RevisionRelationshipCreate":
        if self.target_revision_id is None and self.target_item_id is None:
            raise ValueError("a target item or revision is required")
        return self


class RevisionRelationshipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_revision_id: uuid.UUID
    target_revision_id: uuid.UUID | None
    target_item_id: uuid.UUID | None
    relationship_type: str
    note: str | None
    created_at: datetime


class RevisionRelationshipView(RevisionRelationshipResponse):
    direction: Literal["reference", "referenced_by"]
    item_id: uuid.UUID
    item_number: str
    item_name: str
    revision_code: str | None = None
    is_derived: bool = False
    occurrence_count: int = Field(default=1, ge=1)


class RevisionCreate(BaseModel):
    revision_code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    cad_system: str = Field(min_length=1, max_length=100)
    native_format: str = Field(min_length=1, max_length=50)
    metadata: dict[str, Any]

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        version = value.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("metadata.schema_version must be a positive integer")
        properties = value.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("metadata.properties must be an object")
        return value


class RevisionCopyCreate(BaseModel):
    source_revision_id: uuid.UUID
    revision_code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")


class RevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    part_id: uuid.UUID
    revision_code: str
    description: str | None
    cad_system: str
    native_format: str
    metadata_json: dict[str, Any]
    derived_metadata_json: dict[str, Any] | None
    state: str
    lifecycle_state: str
    metadata_status: str
    created_by: uuid.UUID
    created_at: datetime
    committed_at: datetime | None
    released_by: uuid.UUID | None
    released_at: datetime | None
    current_metadata_extraction_id: uuid.UUID | None


class ProcessingFileStatus(BaseModel):
    id: uuid.UUID
    logical_path: str
    filename: str
    byte_size: int
    role: str | None
    status: str
    stage: str | None = None
    message: str | None = None
    fix_suggestion: str | None = None


class ProcessingJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    revision_id: uuid.UUID
    snapshot_id: uuid.UUID | None
    type: str
    state: str
    stage: str
    progress_percent: int
    converter_version: str | None
    cache_key: str | None
    diagnostics_json: dict | None
    attempt_count: int
    error_detail: str | None
    available_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    can_control: bool = False
    can_cancel: bool = False
    files: list[ProcessingFileStatus] = Field(default_factory=list)
    fix_suggestion: str | None = None
    item_number: str | None = None
    item_name: str | None = None


class PartSearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=100)
    cad_system: str | None = Field(default=None, max_length=100)
    revision_state: str | None = Field(default=None, max_length=32)
    material: str | None = Field(default=None, max_length=255)
    bbox_max_mm_gte: float | None = Field(default=None, ge=0)
    bbox_max_mm_lte: float | None = Field(default=None, ge=0)
    volume_mm3_gte: float | None = Field(default=None, ge=0)
    volume_mm3_lte: float | None = Field(default=None, ge=0)
    solid_count: int | None = Field(default=None, ge=0)
    include_incomplete: bool = False
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class PartSearchItem(BaseModel):
    part: PartResponse
    current_revision: RevisionResponse | None


class PartSearchResponse(BaseModel):
    items: list[PartSearchItem]
    total: int
    limit: int
    offset: int


class CheckoutResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    part_id: uuid.UUID
    revision_id: uuid.UUID | None
    base_snapshot_id: uuid.UUID | None
    owner_id: uuid.UUID
    checkout_group_id: uuid.UUID
    root_revision_id: uuid.UUID | None
    scope: str
    acquired_at: datetime
    expires_at: datetime


class ItemCheckoutResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID = Field(validation_alias="part_id")
    revision_id: uuid.UUID | None
    base_snapshot_id: uuid.UUID | None
    owner_id: uuid.UUID
    checkout_group_id: uuid.UUID
    root_revision_id: uuid.UUID | None
    scope: str
    acquired_at: datetime
    expires_at: datetime


class BomOccurrenceResponse(BaseModel):
    id: uuid.UUID
    occurrence_key: str
    source_reference: str
    reference_designator: str | None
    quantity: int
    transform: list[float]
    is_suppressed: bool
    is_visible: bool
    properties: dict[str, Any]
    child_part: PartResponse
    child_revision: RevisionResponse
    children: list["BomOccurrenceResponse"] = Field(default_factory=list)


class BomResponse(BaseModel):
    assembly_part: PartResponse
    assembly_revision: RevisionResponse
    occurrences: list[BomOccurrenceResponse]


class NativeBomRowResponse(BaseModel):
    id: str
    item_number: str
    item_quantity: int
    total_quantity: str
    bom_structure: str
    merged: bool
    promoted: bool
    rolled_up: bool
    occurrence_names: list[str]
    definition_paths: list[str]
    children: list["NativeBomRowResponse"] = Field(default_factory=list)


class NativeBomViewResponse(BaseModel):
    name: str
    view_type: str
    model_state_member_name: str | None = None
    iassembly_member_name: str | None = None
    rows: list[NativeBomRowResponse]


class NativeBomResponse(BaseModel):
    revision_id: uuid.UUID
    active_model_state: str | None = None
    active_iassembly_member: str | None = None
    selection_reason: str | None = None
    views: list[NativeBomViewResponse]


class WhereUsedPathSegmentResponse(BaseModel):
    occurrence_id: uuid.UUID
    occurrence_key: str
    quantity: int
    parent_item_id: uuid.UUID
    parent_revision_id: uuid.UUID
    child_item_id: uuid.UUID
    child_revision_id: uuid.UUID


class WhereUsedItemResponse(BaseModel):
    occurrence_id: uuid.UUID
    occurrence_key: str
    parent_part: PartResponse
    parent_revision: RevisionResponse
    direct: bool = True
    depth: int = 1
    total_quantity: int = 1
    path: list[WhereUsedPathSegmentResponse] = Field(default_factory=list)
    occurrence_count: int = Field(default=1, ge=1)
    paths: list[list[WhereUsedPathSegmentResponse]] = Field(default_factory=list)


class WhereUsedResponse(BaseModel):
    revision_id: uuid.UUID
    items: list[WhereUsedItemResponse]
    warnings: list[str] = Field(default_factory=list)


class ItemBomOccurrenceResponse(BaseModel):
    id: uuid.UUID
    occurrence_key: str
    source_reference: str
    reference_designator: str | None
    quantity: int
    transform: list[float]
    is_suppressed: bool
    is_visible: bool
    properties: dict[str, Any]
    child_item: ItemResponse
    child_revision: ItemRevisionResponse
    children: list["ItemBomOccurrenceResponse"] = Field(default_factory=list)


class ItemBomResponse(BaseModel):
    assembly_item: ItemResponse
    assembly_revision: ItemRevisionResponse
    occurrences: list[ItemBomOccurrenceResponse]


class ItemWhereUsedEntryResponse(BaseModel):
    occurrence_id: uuid.UUID
    occurrence_key: str
    parent_item: ItemResponse
    parent_revision: ItemRevisionResponse
    direct: bool = True
    depth: int = 1
    total_quantity: int = 1
    path: list[WhereUsedPathSegmentResponse] = Field(default_factory=list)
    occurrence_count: int = Field(default=1, ge=1)
    paths: list[list[WhereUsedPathSegmentResponse]] = Field(default_factory=list)


class ItemWhereUsedResponse(BaseModel):
    revision_id: uuid.UUID
    items: list[ItemWhereUsedEntryResponse]
    warnings: list[str] = Field(default_factory=list)


class MetadataExtractionResponse(BaseModel):
    id: uuid.UUID
    revision_id: uuid.UUID
    snapshot_id: uuid.UUID | None
    schema_version: str
    worker_type: str
    worker_version: str
    source_system: str
    source_system_version: str | None
    status: str
    extracted_at: datetime
    metadata: dict[str, Any]
    derived: dict[str, Any]
    warnings: list[Any]
    errors: list[Any]
    raw_metadata_url: str | None
    created_at: datetime


class RevisionMetadataResponse(BaseModel):
    revision_id: uuid.UUID
    metadata_status: str
    current: MetadataExtractionResponse
