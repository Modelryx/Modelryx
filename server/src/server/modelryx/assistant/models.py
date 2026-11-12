import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceContext(StrictModel):
    page: str = Field(min_length=1, max_length=100)


class ItemContext(StrictModel):
    item_id: uuid.UUID
    revision_id: uuid.UUID | None = None


class AssemblyContext(StrictModel):
    item_id: uuid.UUID
    revision_id: uuid.UUID | None = None
    selected_occurrence_id: uuid.UUID | None = None


class RepresentationContext(StrictModel):
    type: Literal["MODEL", "DRAWING"] | None = None
    id: uuid.UUID | None = None


class SelectionContext(StrictModel):
    object_ids: list[str] = Field(default_factory=list, max_length=100)


class ValidationContext(StrictModel):
    job_id: uuid.UUID


class SearchContext(StrictModel):
    session_id: uuid.UUID


class AssistantContext(StrictModel):
    workspace: WorkspaceContext | None = None
    item: ItemContext | None = None
    assembly: AssemblyContext | None = None
    representation: RepresentationContext | None = None
    selection: SelectionContext | None = None
    validation: ValidationContext | None = None
    search: SearchContext | None = None


class AssistantMessageRequest(StrictModel):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=2, max_length=4000)
    context: AssistantContext

    @model_validator(mode="after")
    def reject_blank_message(self) -> "AssistantMessageRequest":
        if not self.message.strip():
            raise ValueError("message must contain non-whitespace characters")
        return self


class AssistantReference(StrictModel):
    type: Literal["ITEM", "REVISION", "METADATA", "ASSEMBLY", "SEARCH_RESULT", "VALIDATION"]
    item_id: uuid.UUID
    revision_id: uuid.UUID | None = None
    item_number: str
    revision_code: str | None = None
    label: str


class AssistantAction(StrictModel):
    type: Literal["OPEN_ITEM", "OPEN_REVISION"]
    item_id: uuid.UUID
    revision_id: uuid.UUID | None = None


class AssistantComparisonAction(StrictModel):
    type: Literal["OPEN_COMPARISON"] = "OPEN_COMPARISON"
    item_id: uuid.UUID
    from_revision_id: uuid.UUID
    to_revision_id: uuid.UUID
    comparison_id: uuid.UUID


class AssistantViewerAction(StrictModel):
    type: Literal["HIGHLIGHT_OCCURRENCES", "ISOLATE_OCCURRENCES"]
    occurrence_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class AssistantMessageResponse(StrictModel):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    answer: str
    intent: Literal[
        "ITEM_SUMMARY",
        "ITEM_METADATA",
        "REVISION_HISTORY",
        "COMPARE_REVISIONS",
        "BOM_QUERY",
        "WHERE_USED",
        "SELECTED_COMPONENT",
        "ENGINEERING_SEARCH",
        "SIMILAR_PART_SEARCH",
        "VALIDATION_DIAGNOSIS",
        "VIEWER_HIGHLIGHT",
        "VIEWER_ISOLATE",
        "VIEWER_MODIFIED_COMPONENTS",
        "SELECTED_WHERE_USED",
    ]
    references: list[AssistantReference] = Field(default_factory=list)
    actions: list[AssistantAction | AssistantComparisonAction | AssistantViewerAction] = Field(
        default_factory=list
    )
    warnings: list[str] = Field(default_factory=list)
    trace_id: str


class AssistantStoredMessage(StrictModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    context: dict
    response: dict
    created_at: datetime


class AssistantConversationResponse(StrictModel):
    id: uuid.UUID
    title: str
    messages: list[AssistantStoredMessage]
    created_at: datetime
    updated_at: datetime
