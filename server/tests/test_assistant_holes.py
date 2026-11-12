import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from modelryx.assistant.models import AssistantContext
from modelryx.assistant.service import AssistantToolGateway, classify_intent


@pytest.mark.parametrize(
    "question",
    [
        "Is there a hole in the part?",
        "Does this part have holes?",
        "Are there any holes?",
        "How many holes are there?",
        "What are the hole diameters?",
        "Is this part hole-free?",
    ],
)
def test_hole_questions_use_current_revision_metadata(question):
    assert classify_intent(question) == "ITEM_METADATA"


@pytest.mark.parametrize(
    "question,intent",
    [
        ("Find parts without holes", "ENGINEERING_SEARCH"),
        ("Have the holes changed between revisions?", "COMPARE_REVISIONS"),
        ("What material is this?", "ITEM_METADATA"),
    ],
)
def test_hole_routing_preserves_other_intents(question, intent):
    assert classify_intent(question) == intent


@pytest.mark.parametrize(
    "count,quality,status,current,presence",
    [
        (4, "complete", "READY", True, "present"),
        (0, "complete", "PARTIAL", True, "absent"),
        (4, "partial", "PARTIAL", True, "present"),
        (0, "partial", "PARTIAL", True, "unknown"),
        (None, "complete", "PARTIAL", True, "unknown"),
        (0, "unsupported", "PARTIAL", True, "unknown"),
        (0, "complete", "FAILED", True, "unknown"),
        (4, "complete", "READY", False, "unknown"),
    ],
)
def test_hole_evidence_distinguishes_present_absent_and_unknown(
    count,
    quality,
    status,
    current,
    presence,
):
    item = SimpleNamespace(id=uuid.uuid4(), part_number="ASM-52", name="Assembly", part_type="PART")
    snapshot_id = uuid.uuid4()
    revision = SimpleNamespace(
        id=uuid.uuid4(), revision_code="A", current_metadata_extraction_id=uuid.uuid4()
    )
    extraction = SimpleNamespace(
        revision_id=revision.id,
        snapshot_id=snapshot_id if current else uuid.uuid4(),
        hole_count=count,
        status=status,
        metadata_json={
            "quality": {"features": quality},
            "features": {
                "holes": [{"diameter_mm": 6, "through": True, "count": 4}] if count == 4 else [],
            },
        },
    )
    db = Mock()
    db.get.return_value = extraction
    db.scalar.return_value = snapshot_id
    gateway = AssistantToolGateway(db, Mock())
    gateway.resolve_item_revision = Mock(return_value=(item, revision))
    tool, result, _ = gateway.execute(
        "ITEM_METADATA",
        AssistantContext(item={"item_id": item.id, "revision_id": revision.id}),
        "Is there a hole in the part?",
    )
    gateway.resolve_item_revision.assert_called_once()
    assert tool == "get_item_metadata"
    assert result.summary["hole_presence"] == presence
    assert result.references[0].revision_id == revision.id
    assert "assembly-level" not in result.answer
    if presence == "unknown":
        assert "cannot determine" in result.answer
    if presence == "present" and quality == "partial":
        assert "at least 4" in result.answer
    if presence == "present":
        assert "6 mm" in result.answer
