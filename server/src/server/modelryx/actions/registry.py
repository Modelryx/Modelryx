from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from modelryx.actions.models import RequestValidationArguments, ToolDescription


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    category: str
    mode: Literal["READ", "PROPOSE", "EXECUTE"]
    risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    requires_confirmation: bool
    required_roles: frozenset[str]
    input_model: type[BaseModel]

    def public(self) -> ToolDescription:
        return ToolDescription(
            name=self.name,
            version=self.version,
            description=self.description,
            category=self.category,
            mode=self.mode,
            risk=self.risk,
            requires_confirmation=self.requires_confirmation,
            required_roles=sorted(self.required_roles),
            input_schema=self.input_model.model_json_schema(),
        )


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "request_validation": ToolDefinition(
        name="request_validation",
        version="1.0",
        description="Create a new deterministic validation run for an editable staging session.",
        category="validation",
        mode="PROPOSE",
        risk="LOW",
        requires_confirmation=True,
        required_roles=frozenset({"engineer", "reviewer", "administrator"}),
        input_model=RequestValidationArguments,
    )
}


def tool_for(name: str) -> ToolDefinition:
    try:
        return TOOL_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"Unknown or disabled tool: {name}") from exc
