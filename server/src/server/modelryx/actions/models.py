import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RequestValidationArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    staging_id: uuid.UUID


class ProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any]


class ProposalConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]
    idempotency_key: str = Field(
        min_length=8, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )


class ToolDescription(BaseModel):
    name: str
    version: str
    description: str
    category: str
    mode: str
    risk: str
    requires_confirmation: bool
    required_roles: list[str]
    input_schema: dict[str, Any]


class ProposalResponse(BaseModel):
    id: uuid.UUID
    tool_name: str
    tool_version: str
    risk: str
    status: str
    arguments: dict[str, Any]
    preview: dict[str, Any]
    target_type: str
    target_id: uuid.UUID
    requested_by: uuid.UUID
    confirmed_by: uuid.UUID | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None


class ExecutionResponse(BaseModel):
    id: uuid.UUID
    proposal_id: uuid.UUID
    idempotency_key: str
    status: str
    result: dict[str, Any]
    error: dict[str, Any]
    executed_by: uuid.UUID
    started_at: datetime
    completed_at: datetime | None
