import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


CADReadCapability = Literal[
    "document.inspect",
    "object.list",
    "feature.tree",
    "feature.parameters",
    "sketch.constraints",
    "geometry.inspect",
    "material.get",
]

CADMutationCapability = Literal[
    "sketch.constraint.set_value",
    "feature.parameter.set_value",
    "material.set",
    "sketch.create",
    "feature.create_pad",
    "feature.create_pocket",
    "feature.create_hole",
    "feature.create_fillet",
    "document.recompute",
    "document.save_as",
    "export.glb",
]


class CADWorkerLimits(StrictModel):
    max_document_bytes: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1, le=86400)
    max_objects: int = Field(ge=1, le=1_000_000)


class CADWorkerCapabilities(StrictModel):
    worker_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,99}$")
    worker_version: str = Field(min_length=1, max_length=100)
    protocol_version: Literal["1.0"] = "1.0"
    cad_system: str = Field(min_length=1, max_length=100)
    cad_version: str = Field(min_length=1, max_length=100)
    read: list[CADReadCapability]
    mutate: list[CADMutationCapability]
    limits: CADWorkerLimits
    vendor_extensions: dict[str, Any] = Field(default_factory=dict)


class CADWorkerSummary(StrictModel):
    worker_id: str
    cad_system: str
    worker_version: str
    protocol_version: str
    available: bool
    mutation_enabled: bool


class CADParameter(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    value: str | int | float | bool | None
    unit: str | None = Field(default=None, max_length=32)
    editable: bool = False
    native_constraint_type: str | None = Field(default=None, max_length=100)


class CADFeature(StrictModel):
    id: str = Field(min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    type: str = Field(min_length=1, max_length=100)
    parameters: list[CADParameter] = Field(default_factory=list, max_length=500)
    native_type: str | None = Field(default=None, max_length=255)


class CADGeometryInspection(StrictModel):
    bounding_box_mm: dict[str, float] = Field(default_factory=dict)
    volume_mm3: float | None = Field(default=None, ge=0)
    surface_area_mm2: float | None = Field(default=None, ge=0)
    solid_count: int | None = Field(default=None, ge=0)


class CADDocumentInspection(StrictModel):
    protocol_version: Literal["1.0"] = "1.0"
    worker_id: str
    worker_version: str
    cad_system: str
    cad_version: str | None
    source_revision_id: str
    source_artifact_sha256: str | None
    document_label: str | None
    body_count: int | None = Field(default=None, ge=0)
    features: list[CADFeature] = Field(default_factory=list, max_length=10_000)
    geometry: CADGeometryInspection
    material: str | None
    mutation_ready: bool
    warnings: list[str] = Field(default_factory=list, max_length=100)


class CADSessionCreate(StrictModel):
    worker_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,99}$")


class CADSessionResponse(StrictModel):
    id: uuid.UUID
    revision_id: uuid.UUID
    source_artifact_id: uuid.UUID
    worker_id: str
    status: str
    source_sha256: str
    lock_version: int
    created_at: datetime
    expires_at: datetime
    ready_at: datetime | None
    closed_at: datetime | None
    error: dict[str, Any]


class CADParameterChangeRequest(StrictModel):
    target_id: str = Field(min_length=1, max_length=255)
    parameter: str = Field(min_length=1, max_length=100)
    value: float = Field(ge=-1_000_000_000, le=1_000_000_000)
    unit: Literal["mm", "deg"] | None = None


class CADMaterialChangeRequest(StrictModel):
    material: str = Field(
        min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._+()/-]*$"
    )


class CADSketchCreateRequest(StrictModel):
    operation: Literal["sketch.create"]
    body_id: str = Field(min_length=1, max_length=255)
    plane: Literal["XY", "XZ", "YZ"] = "XY"
    geometry: Literal["rectangle", "circle"]
    width_mm: float | None = Field(default=None, gt=0, le=1_000_000)
    height_mm: float | None = Field(default=None, gt=0, le=1_000_000)
    radius_mm: float | None = Field(default=None, gt=0, le=1_000_000)


class CADPadCreateRequest(StrictModel):
    operation: Literal["feature.create_pad"]
    profile_id: str = Field(min_length=1, max_length=255)
    length_mm: float = Field(gt=0, le=1_000_000)


class CADPocketCreateRequest(StrictModel):
    operation: Literal["feature.create_pocket"]
    profile_id: str = Field(min_length=1, max_length=255)
    depth_mm: float = Field(gt=0, le=1_000_000)


class CADHoleCreateRequest(StrictModel):
    operation: Literal["feature.create_hole"]
    body_id: str = Field(min_length=1, max_length=255)
    plane: Literal["XY", "XZ", "YZ"]
    x_mm: float = Field(ge=-1_000_000, le=1_000_000)
    y_mm: float = Field(ge=-1_000_000, le=1_000_000)
    diameter_mm: float = Field(gt=0, le=1_000_000)
    depth_mm: float = Field(gt=0, le=1_000_000)


class CADFilletCreateRequest(StrictModel):
    operation: Literal["feature.create_fillet"]
    base_feature_id: str = Field(min_length=1, max_length=255)
    edge_indices: list[int] = Field(min_length=1, max_length=32)
    radius_mm: float = Field(gt=0, le=1_000_000)


CADFeatureCreateRequest = (
    CADSketchCreateRequest
    | CADPadCreateRequest
    | CADPocketCreateRequest
    | CADHoleCreateRequest
    | CADFilletCreateRequest
)


class CADPlanConfirm(StrictModel):
    confirmed: Literal[True]
    idempotency_key: str = Field(
        min_length=8, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )


class CADPlanResponse(StrictModel):
    id: uuid.UUID
    session_id: uuid.UUID
    status: str
    risk: str
    operation: str
    target_id: str
    parameter: str
    expected_value: Any
    new_value: Any
    unit: str | None
    preview: dict[str, Any]
    state_token: str
    requested_by: uuid.UUID
    confirmed_by: uuid.UUID | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None


class CADExecutionResponse(StrictModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    processing_job_id: uuid.UUID | None
    status: str
    idempotency_key: str
    result: dict[str, Any]
    error: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class CADResultResponse(StrictModel):
    session: CADSessionResponse
    plan: CADPlanResponse
    execution: CADExecutionResponse


class CADCommitConfirm(StrictModel):
    confirmed: Literal[True]
    operation: Literal["NEW_VERSION", "NEW_REVISION"]
    revision_code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    description: str | None = Field(default=None, max_length=10_000)
    idempotency_key: str = Field(
        min_length=8, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )


class CADCommitPlanResponse(StrictModel):
    id: uuid.UUID
    session_id: uuid.UUID
    status: str
    operation: Literal["NEW_VERSION", "NEW_REVISION"]
    revision_code: str
    description: str | None
    state_token: str
    preview: dict[str, Any]
    requested_by: uuid.UUID
    confirmed_by: uuid.UUID | None
    committed_revision_id: uuid.UUID | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None


class CADCommitResponse(StrictModel):
    commit_plan: CADCommitPlanResponse
    revision_id: uuid.UUID
    revision_code: str
