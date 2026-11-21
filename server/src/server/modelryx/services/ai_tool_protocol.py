"""Provider-neutral tool definitions and validated model call envelopes."""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolOutput:
    call: ToolCall
    result: dict[str, Any]
    is_error: bool = False


@dataclass(frozen=True)
class ToolTurn:
    text: str
    calls: list[ToolCall] = field(default_factory=list)
    # Retain native continuation fields, including Gemini thought signatures.
    message: dict[str, Any] = field(default_factory=dict)
