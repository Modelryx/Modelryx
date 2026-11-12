import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NumericPredicate(StrictModel):
    eq: float | None = None
    lt: float | None = None
    lte: float | None = None
    gt: float | None = None
    gte: float | None = None
    target: float | None = None
    tolerance: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_predicate(self) -> "NumericPredicate":
        supplied = [
            self.eq,
            self.lt,
            self.lte,
            self.gt,
            self.gte,
            self.target,
        ]
        if not any(value is not None for value in supplied):
            raise ValueError("at least one comparison is required")
        if self.tolerance is not None and self.target is None:
            raise ValueError("tolerance requires target")
        if any(value is not None and value < 0 for value in supplied):
            raise ValueError("engineering quantities cannot be negative")
        lower = self.gte if self.gte is not None else self.gt
        upper = self.lte if self.lte is not None else self.lt
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("lower bound cannot exceed upper bound")
        return self


class TextSearch(StrictModel):
    terms: list[str] = Field(default_factory=list, max_length=20)


class MaterialFilter(StrictModel):
    family: str
    grade: str | None = None
    temper: str | None = None
    raw: str | None = None


class FeatureFilter(StrictModel):
    feature: Literal[
        "hole",
        "fillet",
        "chamfer",
        "extrusion",
        "revolution",
        "pocket",
        "pattern",
        "thread",
    ]
    presence: Literal["present", "absent"]


class FeatureCountFilter(StrictModel):
    feature: Literal[
        "hole",
        "fillet",
        "chamfer",
        "extrusion",
        "revolution",
        "pocket",
        "pattern",
        "thread",
    ]
    count: NumericPredicate


class GeometryFilters(StrictModel):
    width_mm: NumericPredicate | None = None
    height_mm: NumericPredicate | None = None
    length_mm: NumericPredicate | None = None
    max_dimension_mm: NumericPredicate | None = None
    volume_mm3: NumericPredicate | None = None
    surface_area_mm2: NumericPredicate | None = None


class DatePredicate(StrictModel):
    after: datetime | None = None
    before: datetime | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "DatePredicate":
        if self.after is None and self.before is None:
            raise ValueError("after or before is required")
        if self.after and self.before and self.after > self.before:
            raise ValueError("after cannot be later than before")
        return self


class SearchFilters(StrictModel):
    identifiers: list[str] = Field(default_factory=list, max_length=20)
    item_number_regex: str | None = Field(default=None, max_length=100)
    name_regex: str | None = Field(default=None, max_length=100)
    classification: str | None = Field(default=None, max_length=100)
    material: MaterialFilter | None = None
    material_presence: Literal["specified", "missing"] | None = None
    feature_filters: list[FeatureFilter] = Field(default_factory=list, max_length=10)
    feature_count_filters: list[FeatureCountFilter] = Field(default_factory=list, max_length=10)
    geometry: GeometryFilters | None = None
    mass_kg: NumericPredicate | None = None
    density_kg_m3: NumericPredicate | None = None
    hole_diameter_mm: NumericPredicate | None = None
    drawing_count: NumericPredicate | None = None
    tolerance_count: NumericPredicate | None = None
    instance_count: NumericPredicate | None = None
    cad_system: str | None = Field(default=None, max_length=100)
    item_types: list[Literal["PART", "ASSEMBLY"]] | None = Field(default=None, max_length=2)
    cad_systems: list[str | None] | None = Field(default=None, max_length=100)
    item_statuses: list[str] | None = Field(default=None, max_length=100)
    artifact_format: str | None = Field(default=None, max_length=20)
    revision: str | None = Field(default=None, max_length=50)
    status: str | None = Field(default=None, max_length=32)
    lifecycle: Literal["WORKING", "RELEASED", "SUPERSEDED", "OBSOLETE"] | None = None
    owner: str | None = Field(default=None, max_length=255)
    created: DatePredicate | None = None
    modified: DatePredicate | None = None
    approval_state: Literal["PENDING", "APPROVED", "REJECTED"] | None = None
    approval_assigned_to_me: bool | None = None
    approval_due_before: datetime | None = None
    checkout_owner: str | None = Field(default=None, max_length=255)
    artifact_presence: Literal["drawing", "preview", "native_cad"] | None = None
    artifact_missing: bool | None = None
    extraction_status: Literal["available", "missing", "failed"] | None = None
    has_warnings: bool | None = None

    @field_validator("item_number_regex", "name_regex")
    @classmethod
    def validate_regex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        pattern = value.strip()
        if not pattern:
            return None
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc.msg}") from exc
        return pattern


class SearchSort(StrictModel):
    field: Literal[
        "relevance", "item_number", "name", "created", "modified", "item_type",
        "revision", "cad_system", "item_status",
    ] = "relevance"
    direction: Literal["asc", "desc"] = "desc"


class SimilaritySearch(StrictModel):
    source_item_revision_id: uuid.UUID
    method: Literal["metadata_embedding"] = "metadata_embedding"
    minimum_score: float = Field(default=0.0, ge=0, le=1)


class RelationshipSearch(StrictModel):
    type: Literal["contains", "where_used", "parent_assembly"]
    target_item_id: uuid.UUID
    depth: Literal["direct", "recursive"] = "direct"
    max_depth: int = Field(default=10, ge=1, le=20)


class SearchRequest(StrictModel):
    version: Literal["1"] = "1"
    entity_type: Literal["part", "assembly", "item"] = "item"
    text: TextSearch = Field(default_factory=TextSearch)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    relationships: list[RelationshipSearch] = Field(default_factory=list, max_length=10)
    similarity: SimilaritySearch | None = None
    sort: list[SearchSort] = Field(default_factory=lambda: [SearchSort()])
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    include_facets: bool = False


class SearchContext(StrictModel):
    current_item_id: uuid.UUID | None = None
    current_revision_id: uuid.UUID | None = None


class AISearchRequest(StrictModel):
    query: str = Field(min_length=2, max_length=2000)
    context: SearchContext = Field(default_factory=SearchContext)
    session_id: uuid.UUID | None = None
    limit: int = Field(default=50, ge=1, le=100)


class MatchReason(StrictModel):
    type: str
    query: str
    actual: str | float | None = None


class SearchScores(StrictModel):
    text: float = Field(ge=0, le=1)
    metadata: float = Field(ge=0, le=1)
    vector: float = Field(ge=0, le=1)
    relationship: float = Field(ge=0, le=1)
    final: float = Field(ge=0, le=1)


class EngineeringSearchItem(StrictModel):
    item_id: uuid.UUID
    revision_id: uuid.UUID | None
    item_number: str
    name: str
    item_type: str
    revision_code: str | None
    cad_system: str | None
    material: str | None
    dimensions_mm: list[float] | None
    mass_kg: float | None
    description: str | None = None
    category: str | None = None
    item_status: str | None = None
    metadata_status: str | None = None
    volume_mm3: float | None = None
    surface_area_mm2: float | None = None
    solid_count: int | None = None
    face_count: int | None = None
    edge_count: int | None = None
    hole_count: int | None = None
    fillet_count: int | None = None
    chamfer_count: int | None = None
    extrusion_count: int | None = None
    revolution_count: int | None = None
    pocket_count: int | None = None
    pattern_count: int | None = None
    thread_count: int | None = None
    score: float = Field(ge=0, le=1)
    scores: SearchScores
    match_reasons: list[MatchReason]


class SearchTrace(StrictModel):
    structured_matches: int
    returned: int
    parser: Literal["llm", "deterministic", "direct"]
    duration_ms: float = Field(ge=0)
    catalog_version: str
    catalog_digest: str


class SearchCoverage(StrictModel):
    matched: int = Field(ge=0)
    excluded_unknown: int = Field(ge=0)
    coverage_rules: list[
        Literal["authoritative_field", "successful_extraction", "snapshot_relation"]
    ] = Field(default_factory=list)


class EngineeringSearchResponse(StrictModel):
    interpretation: SearchRequest
    results: list[EngineeringSearchItem]
    total: int
    warnings: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    trace: SearchTrace
    session_id: uuid.UUID | None = None
    coverage: SearchCoverage
    facets: dict[str, list[str | None]] | None = None


class SearchSessionResponse(StrictModel):
    id: uuid.UUID
    current_request: SearchRequest
    history: list[dict]


class SearchFeedbackRequest(StrictModel):
    category: Literal[
        "incorrect_interpretation", "missing_result", "irrelevant_result", "other"
    ]
    comment: str | None = Field(default=None, max_length=1000)


class SearchFeedbackResponse(StrictModel):
    id: uuid.UUID
    session_id: uuid.UUID
    category: str
    created_at: datetime
