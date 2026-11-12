"""Extract native link-array element evidence without inventing assembly occurrences."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from assembly_suppression import native_suppression


def compose_world_transform(parent_matrix: Any, local_transform: Any) -> dict[str, Any] | None:
    """Combine finite 4x4 matrices expressed in millimetres without assuming unit scale."""
    try:
        local_matrix = local_transform["matrix"]
        if local_transform["translation_unit"] != "mm":
            return None
        matrices = (parent_matrix, local_matrix)
        if any(len(matrix) != 4 or any(len(row) != 4 for row in matrix) for matrix in matrices):
            return None
        if not all(
            math.isfinite(float(value)) for matrix in matrices for row in matrix for value in row
        ):
            return None
        result = [
            [
                round(
                    sum(float(parent_matrix[i][k]) * float(local_matrix[k][j]) for k in range(4)),
                    12,
                )
                for j in range(4)
            ]
            for i in range(4)
        ]
        if not all(math.isfinite(value) for row in result for value in row):
            return None
        return {"matrix": result, "translation_unit": "mm"}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _scale_value(value: Any) -> list[float] | None:
    if value is None:
        return None
    try:
        components = [float(getattr(value, axis)) for axis in ("x", "y", "z")]
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return components if all(math.isfinite(component) for component in components) else None


def _copy_on_change_mode(obj: Any) -> str | None:
    """Read the native link policy only when its property type is verified."""
    try:
        if obj.getTypeIdOfProperty("LinkCopyOnChange") != "App::PropertyEnumeration":
            return None
        value = obj.LinkCopyOnChange
        return value if isinstance(value, str) else None
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _definition_evidence(
    obj: Any, target: Any, elements: list[Any], count: int, *, expanded: bool = False,
) -> dict[str, Any]:
    """Do not treat configured links or heterogeneous elements as one definition."""
    mode = _copy_on_change_mode(obj)
    result: dict[str, Any] = {"copy_on_change": mode, "status": "unavailable"}
    if mode is None or target is None:
        return result
    owned = mode == "Owned"
    if mode != "Disabled" and not owned:
        result["status"] = "configured"
        return result
    if owned:
        try:
            source = obj.LinkCopyOnChangeSource
            group = obj.LinkCopyOnChangeGroup
            if (
                not expanded
                or obj.getTypeIdOfProperty("LinkCopyOnChangeSource") != "App::PropertyXLink"
                or obj.getTypeIdOfProperty("LinkCopyOnChangeGroup") != "App::PropertyLink"
                or source is None or source == target or group is None
                or getattr(group, "TypeId", "") != "App::LinkGroup"
                or getattr(source, "Document", None) is not obj.Document
                or getattr(target, "Document", None) is not obj.Document
                or getattr(group, "Document", None) is not obj.Document
                or target not in group.ElementList
            ):
                result["status"] = "configured"
                return result
        except (AttributeError, RuntimeError, TypeError, ValueError):
            result["status"] = "configured"
            return result
    if (expanded and len(elements) != count) or (not expanded and elements):
        return result
    for element in elements:
        element_mode = _copy_on_change_mode(element)
        if element_mode is None:
            return result
        if element_mode != "Disabled":
            result["status"] = "configured"
            return result
        element_target = getattr(element, "LinkedObject", None)
        if element_target is None:
            return result
        if element_target != target:
            result["status"] = "heterogeneous"
            return result
    result["status"] = "owned_uniform" if owned else "uniform"
    return result


def link_arrays(
    document: Any,
    *,
    placement_value: Callable[[Any], dict[str, Any] | None],
    document_identity: Callable[[Any], str],
    max_elements: int,
    element_transform: Callable[[Any, int], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    remaining = max(0, max_elements)
    for obj in sorted(getattr(document, "Objects", ()), key=lambda item: str(item.Name).casefold()):
        if str(getattr(obj, "TypeId", "")) != "App::Link":
            continue
        try:
            count = max(0, int(getattr(obj, "ElementCount", 0) or 0))
            if not count:
                continue
            elements = list(getattr(obj, "ElementList", ()) or ())
            show_elements = getattr(obj, "ShowElement", None)
            expanded = show_elements if isinstance(show_elements, bool) else bool(elements)
            placements = list(getattr(obj, "PlacementList", ()) or ())
            scales = list(getattr(obj, "ScaleList", ()) or ())
            visibility = list(getattr(obj, "VisibilityList", ()) or ())
            target = getattr(obj, "LinkedObject", None)
            copy_source = getattr(obj, "LinkCopyOnChangeSource", None)
            copy_group = getattr(obj, "LinkCopyOnChangeGroup", None)
            records = []
            limit = min(count, remaining)
            for index in range(limit):
                element = elements[index] if index < len(elements) else None
                suppression, suppression_source = (
                    native_suppression(element) if element is not None else (None, None)
                )
                placement = (
                    getattr(element, "Placement", None)
                    if element is not None
                    else (placements[index] if index < len(placements) else None)
                )
                records.append(
                    {
                        "index": index,
                        "name": str(getattr(element, "Name", "") or f"{obj.Name}_i{index}"),
                        "placement": placement_value(placement) if placement is not None else None,
                        "scale": _scale_value(
                            getattr(element, "ScaleVector", None)
                            if element is not None
                            else (scales[index] if index < len(scales) else None)
                        ),
                        "visibility": (
                            visibility[index]
                            if index < len(visibility) and isinstance(visibility[index], bool)
                            else None
                        ),
                        "suppression": {
                            "state": (
                                "suppressed"
                                if suppression is True
                                else "active"
                                if suppression is False
                                else "unknown"
                            ),
                            "source": suppression_source
                            or ("collapsed_array" if element is None else "not_exposed"),
                        },
                    }
                )
                if element is not None:
                    element_target = getattr(element, "LinkedObject", None)
                    records[-1]["definition"] = {
                        "target": str(getattr(element_target, "Name", "") or "") or None,
                        "document": document_identity(getattr(element_target, "Document", None))
                        if element_target is not None else None,
                        "copy_on_change": _copy_on_change_mode(element),
                    }
                if element_transform is not None:
                    records[-1]["transform_to_array_parent"] = element_transform(obj, index)
            remaining -= limit
            explicit_suppressed = sum(
                record["suppression"]["state"] == "suppressed" for record in records
            )
            explicit_active = sum(record["suppression"]["state"] == "active" for record in records)
            result.append(
                {
                    "name": str(obj.Name),
                    "document": document_identity(getattr(obj, "Document", None)),
                    "target": str(getattr(target, "Name", "") or "") or None,
                    "target_type": str(getattr(target, "TypeId", "") or "") or None,
                    "target_document": document_identity(getattr(target, "Document", None))
                    if target is not None
                    else None,
                    "copy_on_change_source": {
                        "object": str(copy_source.Name),
                        "document": document_identity(getattr(copy_source, "Document", None)),
                    } if copy_source is not None else None,
                    "copy_on_change_group": str(getattr(copy_group, "Name", "") or "") or None,
                    "element_count": count,
                    "representation": "expanded" if expanded else "collapsed",
                    "definition_evidence": (
                        _definition_evidence(obj, target, elements, count, expanded=expanded)
                        if limit == count or (not expanded and not elements) else {
                            "copy_on_change": _copy_on_change_mode(obj),
                            "status": "unavailable", "reason": "element_limit",
                        }
                    ),
                    "elements": records,
                    "truncated": limit < count,
                    "suppression_summary": {
                        "explicit_suppressed_count": explicit_suppressed,
                        "explicit_active_count": explicit_active,
                        "unknown_inspected_count": limit - explicit_suppressed - explicit_active,
                        "uninspected_count": count - limit,
                    },
                }
            )
        except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
            result.append({"name": str(obj.Name), "status": "unavailable"})
    return result
