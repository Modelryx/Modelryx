"""Read-only domain tools; model arguments never carry a principal or SQL."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from modelryx.api.items import readable_item_revision
from modelryx.assistant.models import AssistantAction, AssistantContext, AssistantReference
from modelryx.core.auth import Principal
from modelryx.engineering_search.models import SearchRequest
from modelryx.engineering_search.service import execute_search
from modelryx.models import Item
from modelryx.services.ai import build_revision_documents
from modelryx.services.ai_tool_protocol import ToolDefinition
from modelryx.services.artifact_content import latest_content_snapshot_id


class RevisionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: uuid.UUID
    revision_id: uuid.UUID


class ToolSearchRequest(SearchRequest):
    limit: int = Field(default=20, ge=1, le=20)
    offset: int = Field(default=0, ge=0, le=1000)

    # No nested model calls or unbounded facet collection in a tool handler.
    similarity: None = None
    include_facets: Literal[False] = False


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: ToolSearchRequest


@dataclass
class EvidenceResult:
    data: dict
    summary: str
    references: list[AssistantReference] = field(default_factory=list)
    actions: list[AssistantAction] = field(default_factory=list)
    # A revision alone does not identify mutable working content.
    versions: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]] = field(default_factory=list)


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    arguments: type[BaseModel]
    handler: Callable[[BaseModel], EvidenceResult]


class EngineeringToolRegistry:
    def __init__(self, db: Session, principal: Principal):
        self.db = db
        self.principal = principal
        self.tools = {
            "search_items": RegisteredTool(
                ToolDefinition(
                    "search_items",
                    "Search readable items/revisions using exact engineering filters. "
                    "Supply request.limit <=20, offset <=1000. "
                    "Similarity and facets are unsupported. "
                    "Disclose applied filters and partial pages.",
                    SearchArguments.model_json_schema(),
                ),
                SearchArguments,
                self.search_items,
            ),
            "get_item_revision": RegisteredTool(
                ToolDefinition(
                    "get_item_revision",
                    "Get recorded metadata for an explicit readable item and revision UUID. "
                    "Use workspace IDs or IDs returned by search; never invent an identifier.",
                    RevisionArguments.model_json_schema(),
                ),
                RevisionArguments,
                self.get_item_revision,
            ),
        }

    @property
    def definitions(self) -> list[ToolDefinition]:
        return [tool.definition for tool in self.tools.values()]

    def validate_context(self, context: AssistantContext) -> dict:
        # Resolve only authorized IDs before exposing browser context to a provider.
        resolved = {}
        for key in ("item", "assembly"):
            pointer = getattr(context, key)
            if pointer is None:
                continue
            item = self.db.get(Item, pointer.item_id)
            revision_id = pointer.revision_id or (item.current_revision_id if item else None)
            if revision_id is None:
                raise HTTPException(404, "Item revision not found")
            readable_item_revision(self.db, pointer.item_id, revision_id, self.principal)
            resolved[key] = {"item_id": str(pointer.item_id), "revision_id": str(revision_id)}
        return resolved

    def execute(self, name: str, arguments: BaseModel, remaining: float) -> EvidenceResult:
        # SQL timeouts bound individual statements. The orchestrator also stops
        # starting work after its wall-clock deadline.
        with self.db.begin_nested():
            prior_timeout = self.db.scalar(text("SHOW statement_timeout"))
            self.db.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {
                    "timeout": f"{max(1, min(10000, int(remaining * 1000)))}ms",
                },
            )
            result = self.tools[name].handler(arguments)
            self.db.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {
                    "timeout": prior_timeout,
                },
            )
            return result

    def verify(self, results: list[EvidenceResult]) -> None:
        # Expire the identity map to avoid using stale ACL/state from earlier calls.
        self.db.expire_all()
        for result in results:
            for item_id, revision_id, snapshot_id in result.versions:
                readable_item_revision(self.db, item_id, revision_id, self.principal)
                if latest_content_snapshot_id(self.db, revision_id) != snapshot_id:
                    raise HTTPException(409, "Engineering content changed; retry the question")

    def _reference(self, item, revision, kind="REVISION") -> AssistantReference:
        return AssistantReference(
            type=kind,
            item_id=item.id,
            revision_id=revision.id,
            item_number=item.part_number,
            revision_code=revision.revision_code,
            label=f"{item.part_number} revision {revision.revision_code}",
        )

    def get_item_revision(self, arguments: RevisionArguments) -> EvidenceResult:
        revision = readable_item_revision(
            self.db,
            arguments.item_id,
            arguments.revision_id,
            self.principal,
        )
        item = self.db.get(Item, arguments.item_id)
        snapshot = latest_content_snapshot_id(self.db, revision.id)
        content = build_revision_documents(item, revision)[0]["content"]
        summary = f"{item.part_number} revision {revision.revision_code}: {item.name}."
        return EvidenceResult(
            data={
                "item_id": str(item.id),
                "revision_id": str(revision.id),
                "content_snapshot_id": str(snapshot) if snapshot else None,
                "item_number": item.part_number,
                "revision_code": revision.revision_code,
                "metadata_excerpt": content[:8000],
                "truncated": len(content) > 8000,
            },
            summary=summary,
            references=[self._reference(item, revision)],
            actions=[
                AssistantAction(type="OPEN_REVISION", item_id=item.id, revision_id=revision.id)
            ],
            versions=[(item.id, revision.id, snapshot)],
        )

    def search_items(self, arguments: SearchArguments) -> EvidenceResult:
        response = execute_search(self.db, self.principal, arguments.request)
        references, actions, versions, rows = [], [], [], []
        for row in response.results:
            # Recheck visibility before returning domain search candidates.
            if row.revision_id is None:
                continue
            revision = readable_item_revision(self.db, row.item_id, row.revision_id, self.principal)
            item = self.db.get(Item, row.item_id)
            snapshot = latest_content_snapshot_id(self.db, revision.id)
            references.append(self._reference(item, revision, "SEARCH_RESULT"))
            actions.append(
                AssistantAction(
                    type="OPEN_REVISION",
                    item_id=item.id,
                    revision_id=revision.id,
                )
            )
            versions.append((item.id, revision.id, snapshot))
            rows.append(
                {
                    "item_id": str(item.id),
                    "revision_id": str(revision.id),
                    "content_snapshot_id": str(snapshot) if snapshot else None,
                    "item_number": item.part_number,
                    "name": item.name[:500],
                    "revision_code": revision.revision_code,
                    "material": row.material,
                    "mass_kg": row.mass_kg,
                    "dimensions_mm": row.dimensions_mm,
                }
            )
        return EvidenceResult(
            data={
                "applied_request": response.interpretation.model_dump(mode="json"),
                "results": rows,
                "total": response.total,
                "returned": len(rows),
                "truncated": response.total > len(rows) or arguments.request.offset > 0,
                "warnings": response.warnings,
                "coverage": response.coverage.model_dump(mode="json"),
            },
            summary=f"Search found {response.total} matching revision(s); returned {len(rows)}. "
            + "; ".join(ref.label for ref in references),
            references=references,
            actions=actions,
            versions=versions,
        )
