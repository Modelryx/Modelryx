"""Resolve joint references against explicit document-qualified occurrence evidence."""

from __future__ import annotations

import re
from typing import Any


def map_joint_occurrences(
    joints: list[dict[str, Any]], occurrences: list[dict[str, Any]], *, max_candidates: int = 100
) -> int:
    """Annotate references without choosing between repeated component occurrences."""
    objects: dict[tuple[str, str], set[str]] = {}
    definitions: dict[tuple[str, str], set[str]] = {}
    children: dict[str, list[dict[str, Any]]] = {}
    for item in occurrences:
        identifier = item.get("occurrenceId")
        if not identifier:
            continue
        source = item.get("source", {})
        if source.get("occurrenceRole") == "navigation":
            continue
        for index, document, name in (
            (objects, source.get("document"), source.get("objectName")),
            (definitions, source.get("linkedDocument"), source.get("linkedObjectName")),
        ):
            if document and name:
                index.setdefault((document, name), set()).add(identifier)
        children.setdefault(item.get("parentOccurrenceId"), []).append(item)
    unresolved = 0
    for joint in joints:
        for reference in joint.get("references", []):
            key = (reference.get("document"), reference.get("object"))
            candidates = set(objects.get(key, definitions.get(key, set())))
            paths = reference.get("subelements", []) or [""]
            selected: set[str] = set()
            for path in paths:
                current = set(candidates)
                segments = str(path).split(".")
                if segments[-1] == "" or re.fullmatch(r"(?:Face|Edge|Vertex)[0-9]+", segments[-1]):
                    segments = segments[:-1]
                for segment in segments:
                    current = {
                        child["occurrenceId"]
                        for parent in current for child in children.get(parent, [])
                        if child.get("source", {}).get("objectName") == segment
                    }
                # Every explicit subelement path must resolve to the same occurrence.
                if not current:
                    selected.clear()
                    break
                selected.update(current)
            mapping: dict[str, Any] = {"status": "unresolved"}
            if len(selected) == 1:
                mapping = {"status": "resolved", "occurrence_id": next(iter(selected))}
            elif selected:
                candidates = sorted(selected)
                mapping = {"status": "ambiguous", "candidate_occurrence_ids": candidates[:max(0, max_candidates)]}
                if len(candidates) > max(0, max_candidates):
                    mapping["candidate_count"] = len(candidates)
                    mapping["candidates_truncated"] = True
            if mapping["status"] != "resolved":
                unresolved += 1
            reference["occurrence_resolution"] = mapping
    return unresolved
