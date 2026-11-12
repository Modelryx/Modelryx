from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from modelryx.metadata.array_quality import ARRAY_GEOMETRY_WARNING, has_unverified_array_geometry
from modelryx.services.ai import AiGateway

from .models import ScoreBreakdown

SYSTEM_PROMPT = """You are evaluating Modelryx duplicate candidates.
Use only supplied normalized engineering metadata and comparison scores.
Do not invent geometry or CAD information. A duplicate means the same engineering part,
not merely a semantically similar part. A variant has intentional engineering differences.
Do not classify based only on names. Do not treat missing metadata as a mismatch.
Return the required structured result."""


class AiDuplicateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_item_id: str
    classification: str = Field(
        pattern="^(engineering_duplicate|likely_duplicate|variant|similar|different|insufficient_data)$"
    )
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(max_length=10)
    differences: list[str] = Field(max_length=10)
    recommendation: str = Field(
        pattern="^(reuse_existing|review_existing|review_variant|show_related|continue_creation|review)$"
    )


class DuplicateReasoner(Protocol):
    def classify(
        self,
        *,
        candidate_item_id: str,
        source: dict[str, Any],
        candidate: dict[str, Any],
        scores: ScoreBreakdown,
    ) -> AiDuplicateResult: ...


def _compact(metadata: dict[str, Any]) -> dict[str, Any]:
    identity = metadata.get("identity", {})
    geometry = metadata.get("geometry", {})
    bbox = geometry.get("bounding_box", {})
    material = metadata.get("physical", {}).get("material", {})
    uncertain_geometry = has_unverified_array_geometry(metadata)
    return {
        "name": identity.get("name"),
        "kind": identity.get("kind"),
        "material": material.get("normalized_name") or material.get("name"),
        "bbox_sorted_mm": (
            sorted(bbox.values()) if not uncertain_geometry and len(bbox) == 3 else None
        ),
        "volume_mm3": (
            geometry.get("shape", {}).get("volume_mm3") if not uncertain_geometry else None
        ),
        "surface_area_mm2": (
            geometry.get("shape", {}).get("surface_area_mm2") if not uncertain_geometry else None
        ),
        "topology": geometry.get("topology") if not uncertain_geometry else None,
        "geometry_warning": ARRAY_GEOMETRY_WARNING if uncertain_geometry else None,
        "features": metadata.get("features", {}).get("summary"),
        "assembly": metadata.get("assembly"),
        "quality": metadata.get("quality"),
    }


class GatewayDuplicateReasoner:
    def __init__(self, gateway: AiGateway):
        self.gateway = gateway

    def classify(
        self,
        *,
        candidate_item_id: str,
        source: dict[str, Any],
        candidate: dict[str, Any],
        scores: ScoreBreakdown,
    ) -> AiDuplicateResult:
        result = self.gateway.structured_chat(
            system=SYSTEM_PROMPT,
            payload={
                "source": _compact(source),
                "candidate": {"item_id": candidate_item_id, **_compact(candidate)},
                "scores": scores.__dict__,
            },
            schema=AiDuplicateResult.model_json_schema(),
        )
        return AiDuplicateResult.model_validate(result)
