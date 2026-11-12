import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ComparisonStatus = Literal["PENDING", "RUNNING", "PARTIAL", "COMPLETED", "FAILED", "STALE"]
DomainStatus = Literal["PENDING", "RUNNING", "COMPLETED", "UNCHANGED", "UNSUPPORTED", "FAILED"]
ComparisonDomainName = Literal[
    "METADATA",
    "PHYSICAL_PROPERTIES",
    "FILES",
    "ATTACHMENTS",
    "VALIDATION",
    "GEOMETRY",
    "FEATURES",
    "BOM",
    "DRAWING",
]
ChangeType = Literal[
    "ADDED", "REMOVED", "MODIFIED", "UNCHANGED", "MOVED", "RENAMED", "REPLACED", "UNKNOWN"
]
Significance = Literal["INFO", "MINOR", "MODERATE", "MAJOR", "CRITICAL"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RevisionComparisonRequest(StrictModel):
    from_revision_id: uuid.UUID
    to_revision_id: uuid.UUID
    domains: list[ComparisonDomainName] = Field(
        default_factory=lambda: ["METADATA", "PHYSICAL_PROPERTIES", "FILES"],
        min_length=1,
        max_length=9,
    )

    @model_validator(mode="after")
    def distinct_revisions(self):
        if self.from_revision_id == self.to_revision_id:
            raise ValueError("from and to revisions must differ")
        if len(self.domains) != len(set(self.domains)):
            raise ValueError("comparison domains must be unique")
        return self


class ComparePreviousRequest(StrictModel):
    domains: list[ComparisonDomainName] = Field(
        default_factory=lambda: ["METADATA", "PHYSICAL_PROPERTIES", "FILES"],
        min_length=1,
        max_length=9,
    )


class ComparisonChangeResponse(StrictModel):
    id: uuid.UUID
    change_key: str
    change_type: ChangeType
    property_path: str
    old_value: object | None
    new_value: object | None
    unit: str | None
    delta: float | None
    percent_change: float | None
    significance: Significance
    categories: list[str]
    confidence: float
    provenance: str
    metadata: dict


class ComparisonDomainResponse(StrictModel):
    id: uuid.UUID
    domain: ComparisonDomainName
    status: DomainStatus
    engine_version: str
    result: dict
    result_hash: str
    change_count: int
    warnings: list[str]
    error_detail: str | None
    changes: list[ComparisonChangeResponse]


class GeometryAlignment(StrictModel):
    method: Literal["CAD_COORDINATE_SYSTEM", "AUTOMATIC", "UNAVAILABLE"]
    confidence: float = Field(ge=0, le=1)


class GeometryArtifactReferences(StrictModel):
    from_preview_url: str | None = None
    to_preview_url: str | None = None
    common_geometry_url: str | None = None
    added_geometry_url: str | None = None
    removed_geometry_url: str | None = None


class GeometryComparisonResult(StrictModel):
    alignment: GeometryAlignment
    geometry_changed: bool | None
    comparison_level: Literal["METADATA_METRICS", "BOOLEAN_GEOMETRY", "UNAVAILABLE"]
    common_volume_mm3: float | None = None
    added_volume_mm3: float | None = None
    removed_volume_mm3: float | None = None
    artifacts: GeometryArtifactReferences = Field(default_factory=GeometryArtifactReferences)
    limitations: list[str] = Field(default_factory=list)


NormalizedFeatureType = Literal[
    "HOLE",
    "POCKET",
    "PAD",
    "EXTRUSION",
    "FILLET",
    "CHAMFER",
    "BOSS",
    "SLOT",
    "PATTERN",
    "SHELL",
    "REVOLVE",
    "THREAD",
    "UNKNOWN_FEATURE",
]


class NormalizedFeature(StrictModel):
    persistent_id: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    type: NormalizedFeatureType
    properties: dict = Field(default_factory=dict)


class FeatureMatch(StrictModel):
    from_persistent_id: str | None = None
    to_persistent_id: str | None = None
    method: Literal["PERSISTENT_ID", "SIGNATURE", "TYPE_AND_NAME", "TYPE_ONLY", "UNMATCHED"]
    confidence: float = Field(ge=0, le=1)


class FeatureComparisonResult(StrictModel):
    comparison_level: Literal["INDIVIDUAL", "SUMMARY", "UNAVAILABLE"]
    source: str | None = None
    added_count: int = Field(default=0, ge=0)
    removed_count: int = Field(default=0, ge=0)
    modified_count: int = Field(default=0, ge=0)
    matches: list[FeatureMatch] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class BomMatch(StrictModel):
    from_occurrence_key: str | None = None
    to_occurrence_key: str | None = None
    method: Literal[
        "OCCURRENCE_KEY",
        "NATIVE_OCCURRENCE_IDS",
        "COMPONENT_AND_REFERENCE",
        "COMPONENT_ONLY",
        "UNMATCHED",
        "AMBIGUOUS",
    ]
    confidence: float = Field(ge=0, le=1)


class BomComparisonResult(StrictModel):
    comparison_level: Literal["AUTHORITATIVE_OCCURRENCES", "UNAVAILABLE"]
    added_count: int = Field(default=0, ge=0)
    removed_count: int = Field(default=0, ge=0)
    modified_count: int = Field(default=0, ge=0)
    moved_count: int = Field(default=0, ge=0)
    replaced_count: int = Field(default=0, ge=0)
    ambiguous_count: int = Field(default=0, ge=0)
    matches: list[BomMatch] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DrawingComparisonResult(StrictModel):
    comparison_level: Literal["FILE", "TEXT", "STRUCTURED", "UNAVAILABLE"]
    file_change_count: int = Field(default=0, ge=0)
    text_change_count: int = Field(default=0, ge=0)
    structured_change_count: int = Field(default=0, ge=0)
    matched_drawing_count: int = Field(default=0, ge=0)
    worker_contract_versions: list[str] = Field(default_factory=list)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


ExplanationLevel = Literal["QUICK", "ENGINEERING", "DETAILED"]


class ComparisonExplanationRequest(StrictModel):
    level: ExplanationLevel = "ENGINEERING"


class ExplanationFact(StrictModel):
    text: str
    change_keys: list[str] = Field(min_length=1)


class ExplanationCause(StrictModel):
    text: str
    source_ids: list[uuid.UUID] = Field(default_factory=list)
    recorded: bool


class ComparisonExplanationResponse(StrictModel):
    id: uuid.UUID
    comparison_id: uuid.UUID
    comparison_result_hash: str
    level: ExplanationLevel
    mode: Literal["AI", "DETERMINISTIC"]
    prompt_version: str
    contract_version: str
    provider: str
    model: str | None
    summary: str
    facts: list[ExplanationFact]
    cause: ExplanationCause
    advisory: list[str]
    cited_change_keys: list[str]
    cited_source_ids: list[uuid.UUID]
    warnings: list[str]
    generated_at: datetime


class RevisionComparisonResponse(StrictModel):
    id: uuid.UUID
    item_id: uuid.UUID
    from_revision_id: uuid.UUID
    to_revision_id: uuid.UUID
    status: ComparisonStatus
    comparison_engine_version: str
    metadata_schema_version: str
    rules_version: str
    requested_domains: list[ComparisonDomainName]
    summary: dict
    error_detail: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    domains: list[ComparisonDomainResponse]
