from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
ValidationStatus = Literal["READY", "READY_WITH_WARNINGS", "BLOCKED", "ERROR"]


class FindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    category: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    severity: Severity
    blocking: bool
    title: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=10000)
    source: dict[str, Any]
    location: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    actions: list[str] = Field(default_factory=list)


class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    label: str = Field(min_length=1, max_length=255)


class ExplainedIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_id: UUID
    explanation: str = Field(min_length=1, max_length=4000)
    impact: str = Field(min_length=1, max_length=2000)
    recommended_actions: list[RecommendedAction]


class AIOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=2000)
    issues: list[ExplainedIssue]


AI_OUTPUT_SCHEMA = AIOutput.model_json_schema()

ALLOWED_ACTIONS = frozenset(
    {
        "upload_missing_file",
        "replace_file",
        "remove_file",
        "retry_worker",
        "edit_metadata",
        "set_material",
        "link_existing_item",
        "unlink_reference",
        "review_duplicate",
        "create_new_revision",
        "use_existing_item",
        "regenerate_drawing",
        "open_in_cad",
        "ignore_warning",
        "revalidate",
    }
)
