from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from joint_occurrences import map_joint_occurrences


def occurrence(identifier, name, document="root.FCStd", parent=None, **source):
    return {"occurrenceId": identifier, "parentOccurrenceId": parent,
            "source": {"document": document, "objectName": name, **source}}


def test_repeated_definition_is_ambiguous_but_link_reference_is_exact() -> None:
    items = [occurrence("a", "Link1", linkedDocument="parts/bolt.FCStd", linkedObjectName="Body"),
             occurrence("b", "Link2", linkedDocument="parts/bolt.FCStd", linkedObjectName="Body"),
             occurrence("navigation", "Link2", occurrenceRole="navigation")]
    refs = [{"document": "parts/bolt.FCStd", "object": "Body", "subelements": ["Face1"]},
            {"document": "root.FCStd", "object": "Link2", "subelements": ["Face1"]}]
    assert map_joint_occurrences([{"references": refs}], items) == 1
    assert refs[0]["occurrence_resolution"] == {
        "status": "ambiguous", "candidate_occurrence_ids": ["a", "b"]}
    assert refs[1]["occurrence_resolution"] == {"status": "resolved", "occurrence_id": "b"}
    before = [dict(ref["occurrence_resolution"]) for ref in refs]
    map_joint_occurrences([{"references": refs}], list(reversed(items)))
    assert [ref["occurrence_resolution"] for ref in refs] == before


def test_nested_paths_and_equal_filenames_preserve_document_scope() -> None:
    items = [occurrence("assembly", "Sub"), occurrence("a", "Link", parent="assembly"),
             occurrence("b", "Link", document="other/root.FCStd")]
    refs = [{"document": "root.FCStd", "object": "Sub", "subelements": ["Link.Face1"]},
            {"document": "other/root.FCStd", "object": "Link", "subelements": []},
            {"document": "root.FCStd", "object": "Sub", "subelements": ["Missing.Face1"]}]
    assert map_joint_occurrences([{"references": refs}], items) == 1
    assert refs[0]["occurrence_resolution"]["occurrence_id"] == "a"
    assert refs[1]["occurrence_resolution"]["occurrence_id"] == "b"
    assert refs[2]["occurrence_resolution"] == {"status": "unresolved"}


def test_hidden_suppressed_occurrence_keeps_identity_and_missing_paths_are_not_guessed() -> None:
    item = occurrence("a", "Link")
    item.update(suppressed=True, display={"visible": False})
    refs = [{"document": "root.FCStd", "object": "Link", "subelements": ["Face1"]},
            {"document": "root.FCStd", "object": "Link", "subelements": ["Face1", "Missing.Face1"]}]
    assert map_joint_occurrences([{"references": refs}], [item]) == 1
    assert refs[0]["occurrence_resolution"] == {"status": "resolved", "occurrence_id": "a"}
    assert refs[1]["occurrence_resolution"] == {"status": "unresolved"}


def test_large_ambiguous_joint_candidates_are_bounded_without_guessing() -> None:
    items = [occurrence(str(index).zfill(3), f"Link{index}",
                        linkedDocument="part.FCStd", linkedObjectName="Body")
             for index in range(150)]
    reference = {"document": "part.FCStd", "object": "Body", "subelements": []}
    assert map_joint_occurrences([{"references": [reference]}], items, max_candidates=3) == 1
    assert reference["occurrence_resolution"] == {
        "status": "ambiguous", "candidate_occurrence_ids": ["000", "001", "002"],
        "candidate_count": 150, "candidates_truncated": True,
    }
