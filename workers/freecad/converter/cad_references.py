"""Serialize FreeCAD object references using a caller-supplied document identity."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def object_reference(value: Any, document_identity: Callable[[Any], str]) -> dict[str, Any] | None:
    if value is None:
        return None
    target = value
    subelements: list[str] = []
    if isinstance(value, (tuple, list)) and value:
        target = value[0]
        if len(value) > 1:
            raw_subelements = value[1]
            if isinstance(raw_subelements, str):
                subelements = [raw_subelements]
            elif isinstance(raw_subelements, (tuple, list)):
                subelements = [str(item) for item in raw_subelements]
    name = getattr(target, "Name", None)
    if not name:
        return None
    return {
        "object": str(name),
        "document": document_identity(getattr(target, "Document", None)),
        "subelements": subelements,
    }


def object_references(value: Any, document_identity: Callable[[Any], str]) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, (tuple, list)) and value and getattr(value[0], "Name", None):
        items = [value]
    elif isinstance(value, (tuple, list)):
        items = value
    else:
        items = [value]
    return [
        reference for item in items
        if (reference := object_reference(item, document_identity)) is not None
    ]
