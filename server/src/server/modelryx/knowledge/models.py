import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

KnowledgeSourceType = Literal[
    "ITEM",
    "ITEM_REVISION",
    "PART_METADATA",
    "ASSEMBLY",
    "BOM_OCCURRENCE",
    "DRAWING",
    "SPECIFICATION",
    "CHANGE_REQUEST",
    "CHANGE_NOTICE",
    "REVISION_NOTE",
    "VALIDATION_REPORT",
    "WORKER_REPORT",
    "ENGINEERING_DOCUMENT",
    "PROCEDURE",
    "STANDARD",
    "MATERIAL_REFERENCE",
    "MODELRYX_DOCUMENTATION",
    "CAD_DOCUMENTATION",
    "KNOWN_ISSUE",
    "BEST_PRACTICE",
]

KnowledgeRelationType = Literal[
    "DESCRIBES",
    "BELONGS_TO",
    "PRECEDES",
    "USES",
    "USED_IN",
    "DOCUMENTS",
    "CHANGE_CAUSED_BY",
    "CHANGE_IMPLEMENTED_IN",
    "CHANGE_VERIFIED_BY",
    "DOCUMENT_REFERENCES",
    "FAILURE_RESOLVED_BY",
    "REQUIREMENT_APPLIES_TO",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeFilter(StrictModel):
    source_types: list[KnowledgeSourceType] = Field(default_factory=list, max_length=20)
    item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    revision_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    lifecycle_states: list[str] = Field(default_factory=list, max_length=20)
    include_stale: bool = False


class KnowledgeSearchRequest(StrictModel):
    query: str = Field(min_length=2, max_length=4000)
    filters: KnowledgeFilter = Field(default_factory=KnowledgeFilter)
    limit: int = Field(default=10, ge=1, le=50)


class EvidenceLocation(StrictModel):
    page: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, max_length=255)
    table: str | None = Field(default=None, max_length=255)


class KnowledgeSearchResult(StrictModel):
    source_id: uuid.UUID
    chunk_id: uuid.UUID
    source_type: KnowledgeSourceType
    source_object_type: str
    source_object_id: uuid.UUID | None
    title: str
    snippet: str
    item_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    lifecycle_state: str | None = None
    authority_rank: int
    location: EvidenceLocation = Field(default_factory=EvidenceLocation)
    updated_at: datetime
    metadata: dict = Field(default_factory=dict)


class KnowledgeSearchResponse(StrictModel):
    results: list[KnowledgeSearchResult]
    total: int
    parser: Literal["hybrid", "lexical"]
    warnings: list[str] = Field(default_factory=list)


class KnowledgeChunkDetail(StrictModel):
    id: uuid.UUID
    section: str
    ordinal: int
    content: str
    location: EvidenceLocation = Field(default_factory=EvidenceLocation)
    metadata: dict = Field(default_factory=dict)


class KnowledgeSourceDetail(StrictModel):
    id: uuid.UUID
    source_type: KnowledgeSourceType
    source_object_type: str
    source_object_id: uuid.UUID | None
    title: str
    status: str
    lifecycle_state: str | None
    authority_rank: int
    item_id: uuid.UUID | None
    revision_id: uuid.UUID | None
    index_version: str
    is_stale: bool
    metadata: dict = Field(default_factory=dict)
    chunks: list[KnowledgeChunkDetail]


class EvidenceFact(StrictModel):
    type: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=512)
    fact: str = Field(min_length=1, max_length=4000)
    source_id: uuid.UUID | None = None


class EvidenceDocument(StrictModel):
    source_id: uuid.UUID
    chunk_id: uuid.UUID
    source_type: KnowledgeSourceType
    title: str
    snippet: str
    location: EvidenceLocation = Field(default_factory=EvidenceLocation)
    authority_rank: int


class EvidenceBundle(StrictModel):
    question: str
    context: dict = Field(default_factory=dict)
    facts: list[EvidenceFact] = Field(default_factory=list, max_length=50)
    documents: list[EvidenceDocument] = Field(default_factory=list, max_length=50)
    evidence_quality: Literal["STRONG", "PARTIAL", "NONE", "CONTRADICTORY"]
    warnings: list[str] = Field(default_factory=list)


class RetrievalPlan(StrictModel):
    intent: Literal[
        "KNOWLEDGE_SEARCH",
        "EXPLAIN_CHANGE",
        "FAILURE_HISTORY",
        "RELATED_DOCUMENTS",
    ]
    scope: dict = Field(default_factory=dict)
    steps: list[
        Literal[
            "AUTHORIZE_CONTEXT",
            "COMPARE_PREVIOUS_REVISION",
            "EXPAND_RELATIONSHIPS",
            "SEARCH_KEYWORDS",
            "SEARCH_VECTORS",
            "RERANK_EVIDENCE",
            "REAUTHORIZE_RESULTS",
        ]
    ] = Field(max_length=10)


class KnowledgeChunkInput(StrictModel):
    section: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=100_000)
    location: EvidenceLocation = Field(default_factory=EvidenceLocation)
    metadata: dict = Field(default_factory=dict)


class KnowledgePublishRequest(StrictModel):
    source_type: Literal[
        "CHANGE_REQUEST",
        "CHANGE_NOTICE",
        "ENGINEERING_DOCUMENT",
        "PROCEDURE",
        "STANDARD",
        "KNOWN_ISSUE",
        "BEST_PRACTICE",
    ]
    title: str = Field(min_length=1, max_length=512)
    lifecycle_state: str = Field(default="DRAFT", max_length=64)
    authority_rank: int = Field(default=60, ge=0, le=100)
    item_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    metadata: dict = Field(default_factory=dict)
    chunks: list[KnowledgeChunkInput] = Field(min_length=1, max_length=100)


class KnowledgeRelationInput(StrictModel):
    to_source_id: uuid.UUID
    relation_type: KnowledgeRelationType
    metadata: dict = Field(default_factory=dict)
