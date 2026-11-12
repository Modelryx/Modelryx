from unittest.mock import Mock

import pytest
from modelryx.duplicate_detection.models import ScoreBreakdown
from modelryx.duplicate_detection.reasoner import AiDuplicateResult, GatewayDuplicateReasoner
from pydantic import ValidationError


def metadata() -> dict:
    return {
        "source": {"worker_version": "must-not-leak"},
        "identity": {"name": "Motor Housing", "kind": "part"},
        "geometry": {
            "bounding_box": {"x_mm": 120, "y_mm": 85, "z_mm": 45},
            "shape": {"volume_mm3": 400000, "surface_area_mm2": 30000},
        },
        "physical": {"material": {"normalized_name": "aluminum_6061"}},
        "features": {"summary": {"hole_count": 4}},
        "quality": {"geometry": "complete", "features": "complete"},
    }


def test_gateway_reasoner_uses_compact_metadata_and_validates_result() -> None:
    gateway = Mock()
    gateway.structured_chat.return_value = {
        "candidate_item_id": "item-1",
        "classification": "likely_duplicate",
        "confidence": 0.93,
        "reasons": ["Geometry matches."],
        "differences": ["Volume differs slightly."],
        "recommendation": "review_existing",
    }
    result = GatewayDuplicateReasoner(gateway).classify(
        candidate_item_id="item-1",
        source=metadata(),
        candidate=metadata(),
        scores=ScoreBreakdown(metadata=0.95, combined=0.95),
    )
    assert result.classification == "likely_duplicate"
    payload = gateway.structured_chat.call_args.kwargs["payload"]
    assert "source" not in payload["source"]
    assert "must-not-leak" not in str(payload)


def test_ai_result_rejects_unknown_classification_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AiDuplicateResult.model_validate(
            {
                "candidate_item_id": "item-1",
                "classification": "same-ish",
                "confidence": 2,
                "reasons": [],
                "differences": [],
                "recommendation": "merge_automatically",
                "invented": True,
            }
        )
