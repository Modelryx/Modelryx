import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BOMTraversal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    depth: Literal["direct", "recursive"] = "recursive"
    mode: Literal["occurrences", "unique_components"] = "unique_components"
    max_depth: int = Field(default=20, ge=1, le=20)
    include_suppressed: bool = False


class BOMFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lifecycle_status: list[str] = Field(default_factory=list, max_length=20)
    has_drawing: bool | None = None
    item_type: list[Literal["PART", "ASSEMBLY"]] = Field(default_factory=list)


class BOMQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["summary", "search", "documentation_gaps", "lifecycle", "single_use"]
    traversal: BOMTraversal = Field(default_factory=BOMTraversal)
    filters: BOMFilters = Field(default_factory=BOMFilters)
    limit: int = Field(default=100, ge=1, le=500)


class BOMNode(BaseModel):
    occurrence_id: uuid.UUID
    occurrence_path: str
    item_id: uuid.UUID
    item_number: str
    item_name: str
    item_type: str
    revision_id: uuid.UUID
    revision: str
    lifecycle_status: str
    quantity: int
    effective_quantity: int
    suppressed: bool
    has_drawing: bool = False
    material: str | None = None
    mass_kg: float | None = None
    metadata_status: str | None = None


class BOMSnapshot(BaseModel):
    id: str
    root_item_id: uuid.UUID
    root_revision_id: uuid.UUID
    root_item_number: str
    root_revision: str
    bom_hash: str
    nodes: list[BOMNode]


class BOMSummary(BaseModel):
    snapshot_id: str
    unique_component_revisions: int
    total_occurrences: int
    total_quantity: int
    subassemblies: int
    lifecycle: dict[str, int]
    documentation: dict[str, int | float]
    reuse: dict[str, int]
    materials: dict[str, int]
    material_coverage: dict[str, int | float]
    mass: dict[str, int | float]


class BOMQueryResult(BaseModel):
    snapshot_id: str
    operation: str
    interpreted_as: str | None = None
    total: int
    returned: int
    truncated: bool
    nodes: list[BOMNode]
    summary: BOMSummary


class BOMDiffEntry(BaseModel):
    change: Literal["ADDED", "REMOVED", "REVISION_CHANGED", "QUANTITY_CHANGED"]
    identity: str
    item_number: str
    item_id: uuid.UUID
    old_revision_id: uuid.UUID | None = None
    new_revision_id: uuid.UUID | None = None
    old_revision: str | None = None
    new_revision: str | None = None
    old_quantity: int | None = None
    new_quantity: int | None = None
    comparison_path: str | None = None


class BOMDiff(BaseModel):
    from_snapshot_id: str
    to_snapshot_id: str
    summary: dict[str, int]
    changes: list[BOMDiffEntry]


class BOMAIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)


class BOMAIResponse(BaseModel):
    plan: BOMQuery
    result: BOMQueryResult
    mode: Literal["AI", "DETERMINISTIC"]
    answer: str
    evidence_occurrence_ids: list[uuid.UUID]
    warnings: list[str]


class StandardizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_score: float = Field(default=0.70, ge=0, le=1)
    limit: int = Field(default=50, ge=1, le=100)


class StandardizationCandidate(BaseModel):
    revision_a_id: uuid.UUID
    revision_b_id: uuid.UUID
    item_a: str
    item_b: str
    score: float
    score_coverage: float
    classification: str
    signals: dict[str, float | None]
    reasons: list[str]
    differences: list[str]
    limitations: list[str]
    advisory: str = "Review candidate only; interchangeability has not been established."


class StandardizationResponse(BaseModel):
    snapshot_id: str
    rules_version: str
    total: int
    returned: int
    truncated: bool
    candidates: list[StandardizationCandidate]


class BOMDuplicatePair(BaseModel):
    revision_a_id: uuid.UUID
    revision_b_id: uuid.UUID
    item_a: str
    item_b: str
    classification: str
    score: float | None
    reasons: list[str]
    differences: list[str]
    advisory: str = "Review duplicate evidence; no BOM change is authorized."


class BOMDuplicateResponse(BaseModel):
    snapshot_id: str
    total: int
    pairs: list[BOMDuplicatePair]


class ReadinessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_lifecycle_states: list[str] = Field(
        default_factory=lambda: ["READY", "READY_WITH_WARNINGS"], max_length=20
    )
    require_drawings: bool = False
    require_material: bool = False
    require_mass: bool = False


class ReadinessFinding(BaseModel):
    code: str
    severity: Literal["WARNING", "BLOCKING"]
    count: int
    occurrence_ids: list[uuid.UUID]
    message: str


class BOMReadinessResponse(BaseModel):
    snapshot_id: str
    policy: ReadinessPolicy
    status: Literal["READY", "READY_WITH_WARNINGS", "BLOCKED"]
    findings: list[ReadinessFinding]
    validation_coverage: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    limitations: list[str]


class CommonalityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_ids: list[uuid.UUID] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def unique_revisions(self) -> "CommonalityRequest":
        if len(set(self.revision_ids)) != len(self.revision_ids):
            raise ValueError("revision_ids must be unique")
        return self


class CommonalityPair(BaseModel):
    revision_a_id: uuid.UUID
    revision_b_id: uuid.UUID
    shared_components: int
    union_components: int
    commonality_percent: float
    shared_item_numbers: list[str]


class CommonalityResponse(BaseModel):
    formula: str = "shared unique item numbers / union unique item numbers"
    product_count: int
    overall_shared_components: int
    overall_union_components: int
    overall_commonality_percent: float
    overall_shared_item_numbers: list[str]
    pairs: list[CommonalityPair]


class BOMKnowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component_revision_id: uuid.UUID
    question: str = Field(min_length=2, max_length=2000)
