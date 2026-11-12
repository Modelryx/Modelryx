"""Feature answers share one evidence policy across the supported CAD vocabulary."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from modelryx.assistant.models import AssistantContext
from modelryx.assistant.service import AssistantToolGateway, classify_intent

FEATURES = ["hole", "fillet", "chamfer", "extrusion", "revolution", "pocket", "pattern", "thread"]


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize(
    "count,quality,current,expected",
    [
        (3, "complete", True, "present"),
        (0, "complete", True, "absent"),
        (None, "complete", True, "unknown"),
        (0, "partial", True, "unknown"),
        (3, "partial", True, "present"),
        (3, "unsupported", True, "unknown"),
        (3, "complete", False, "unknown"),
    ],
)
def test_feature_answers_respect_count_coverage_and_snapshot(
    feature, count, quality, current, expected
):
    question = f"Does this part have {feature}s?"
    assert classify_intent(question) == "ITEM_METADATA"
    item = SimpleNamespace(id=uuid.uuid4(), part_number="PF-1", name="Part", part_type="PART")
    revision = SimpleNamespace(
        id=uuid.uuid4(), revision_code="B", current_metadata_extraction_id=uuid.uuid4()
    )
    snapshot_id = uuid.uuid4()
    extraction = SimpleNamespace(
        revision_id=revision.id,
        status="PARTIAL",
        snapshot_id=snapshot_id if current else uuid.uuid4(),
        metadata_json={"quality": {"features": quality}},
        **{f"{feature}_count": count},
    )
    db = Mock()
    db.get.return_value = extraction
    db.scalar.return_value = snapshot_id
    gateway = AssistantToolGateway(db, Mock())
    gateway.resolve_item_revision = Mock(return_value=(item, revision))
    _, result, _ = gateway.execute(
        "ITEM_METADATA",
        AssistantContext(item={"item_id": item.id, "revision_id": revision.id}),
        question,
    )
    assert result.summary[f"{feature}_presence"] == expected
    assert result.references[0].revision_id == revision.id
    if expected == "unknown":
        assert "cannot determine" in result.answer
    elif quality == "partial":
        assert "at least 3" in result.answer


def test_combined_features_keep_independent_unknown_and_zero_counts():
    item = SimpleNamespace(id=uuid.uuid4(), part_number="PF-2", name="Assembly", part_type="PART")
    revision = SimpleNamespace(
        id=uuid.uuid4(), revision_code="A", current_metadata_extraction_id=uuid.uuid4()
    )
    extraction = SimpleNamespace(
        revision_id=revision.id,
        status="READY",
        snapshot_id=None,
        metadata_json={"quality": {"features": "complete"}},
        fillet_count=4,
        chamfer_count=0,
        pocket_count=None,
    )
    db = Mock()
    db.get.return_value = extraction
    db.scalar.return_value = None
    gateway = AssistantToolGateway(db, Mock())
    gateway.resolve_item_revision = Mock(return_value=(item, revision))
    _, result, _ = gateway.execute(
        "ITEM_METADATA",
        AssistantContext(item={"item_id": item.id, "revision_id": revision.id}),
        "Are there fillets, chamfers, pockets, or ribs?",
    )
    assert result.summary["fillet_presence"] == "present"
    assert result.summary["chamfer_presence"] == "absent"
    assert result.summary["pocket_presence"] == "unknown"
    assert result.summary["rib_presence"] == "unknown"
    assert "not supported" in result.answer
    assert "assembly-level" not in result.answer


@pytest.mark.parametrize("feature", ["slots", "bosses", "ribs"])
def test_unsupported_features_are_disclosed_without_inventing_a_count(feature):
    assert classify_intent(f"Does this part have {feature}?") == "ITEM_METADATA"


@pytest.mark.parametrize(
    "question,intent",
    [
        ("Find parts with threads", "ENGINEERING_SEARCH"),
        ("Compare the fillets between revisions", "COMPARE_REVISIONS"),
        ("Show the BOM", "BOM_QUERY"),
        ("Highlight the pocket", "VIEWER_HIGHLIGHT"),
    ],
)
def test_feature_questions_do_not_override_existing_tools(question, intent):
    assert classify_intent(question) == intent
