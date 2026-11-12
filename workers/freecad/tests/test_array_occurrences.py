from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from array_occurrences import verified_array_occurrences


def test_verified_array_elements_keep_visibility_and_suppression_separate() -> None:
    container = {
        "partId": "part",
        "occurrenceId": "array",
        "display": {"visible": True},
        "source": {"linkedObjectName": "Body", "linkedDocument": "Doc"},
    }
    matrix = {"matrix": [[1, 0, 0, 0]] * 4, "translation_unit": "mm"}
    elements = [
        {
            "name": "A",
            "index": 0,
            "visibility": False,
            "suppression": {"source": "native", "state": "active"},
        },
        {
            "name": "B",
            "index": 1,
            "visibility": True,
            "suppression": {"source": "native", "state": "suppressed"},
        },
    ]
    for element in elements:
        element["transform_to_array_parent"] = matrix
        element["world_transforms"] = [{"container_occurrence_id": "array", "transform": matrix}]
    array = {
        "name": "Array",
        "representation": "expanded",
        "element_count": 2,
        "target": "Body",
        "target_document": "Doc",
        "target_type": "Part::Feature",
        "definition_evidence": {"status": "uniform"},
        "elements": elements,
    }
    records = verified_array_occurrences(array, [container], lambda *parts: ":".join(parts))
    assert records is not None
    assert records[0]["suppressed"] is False and records[0]["display"]["visible"] is False
    assert records[1]["suppressed"] is True and records[1]["display"]["visible"] is False
    assert records[0]["parentOccurrenceId"] == "array"
    assert records[0]["source"]["identityBasis"] == "array_index"
    unknown = deepcopy(array)
    unknown["elements"][0]["suppression"] = {"source": "not_exposed", "state": "unknown"}
    assert verified_array_occurrences(unknown, [container], lambda *_parts: "id") is None
    partial = verified_array_occurrences(
        unknown, [container], lambda *parts: ":".join(parts), allow_partial=True
    )
    assert partial is not None
    assert "suppressed" not in partial[0]
    assert partial[0]["display"]["visible"] is False
    collapsed = deepcopy(unknown)
    collapsed["representation"] = "collapsed"
    collapsed["elements"][0]["visibility"] = True
    partial = verified_array_occurrences(
        collapsed, [container], lambda *parts: ":".join(parts), allow_partial=True
    )
    assert partial is not None
    assert partial[0]["source"]["objectName"] == "Array"
    assert partial[0]["source"]["typeId"] == "App::Link"
    assert partial[0]["source"]["arrayIndex"] == 0
    assert "visible" not in partial[0]["display"]
    missing = deepcopy(array)
    missing["elements"][0]["world_transforms"] = []
    assert verified_array_occurrences(missing, [container], lambda *_parts: "id") is None
    truncated = deepcopy(array)
    truncated["truncated"] = True
    assert verified_array_occurrences(truncated, [container], lambda *_parts: "id") is None
    assert verified_array_occurrences(array, [container, container], lambda *_parts: "id") is None
    ambiguous = {**container, "properties": {"Suppressed": True}}
    assert verified_array_occurrences(array, [ambiguous], lambda *_parts: "id") is None
    variant = {**container, "properties": {"Configuration": "A"}}
    assert verified_array_occurrences(array, [variant], lambda *_parts: "id") is None
    configured = {**array, "definition_evidence": {"status": "configured"}}
    assert verified_array_occurrences(configured, [container], lambda *_parts: "id") is None
    assert verified_array_occurrences(
        configured, [container], lambda *_parts: "id", allow_partial=True
    ) is None
    owned = {**array, "definition_evidence": {"status": "owned_uniform"}}
    configured_container = {**container, "properties": {"Variant": "A"}}
    assert verified_array_occurrences(owned, [configured_container], lambda *parts: ":".join(parts))
    assert verified_array_occurrences(owned, [configured_container], lambda *_parts: "id", allow_partial=True) is None
    owned_unknown = deepcopy(owned)
    owned_unknown["elements"][0]["suppression"] = {"source": "unavailable", "state": "unknown"}
    assert verified_array_occurrences(owned_unknown, [configured_container], lambda *_parts: "id") is None
