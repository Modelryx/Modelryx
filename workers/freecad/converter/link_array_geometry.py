"""Build visible array geometry only from verified native element state."""

from __future__ import annotations

import math
from typing import Any

from assembly_suppression import native_suppression


def filtered_face_appearances(original: Any, filtered: Any, appearances: list[Any]) -> list[Any] | None:
    """Match each filtered face to one equivalent original face, without guessing.

    Bounding boxes narrow the candidates; bidirectional native cuts verify that
    their surfaces coincide. Ambiguous or expensive mappings stay unavailable.
    """
    try:
        originals = original.Faces
        retained = filtered.Faces
        if not retained or len(originals) != len(appearances) or len(originals) > 128:
            return None

        def signature(face: Any) -> tuple[float, ...]:
            bounds = face.BoundBox
            values = [getattr(bounds, name) for name in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")]
            values.append(face.Area)
            if not all(math.isfinite(float(value)) for value in values):
                raise ValueError("Non-finite face geometry")
            return tuple(round(float(value), 7) for value in values)

        candidates: dict[tuple[float, ...], list[int]] = {}
        for index, face in enumerate(originals):
            candidates.setdefault(signature(face), []).append(index)
        used: set[int] = set()
        result = []
        for face in retained:
            options = candidates.get(signature(face), [])
            area = float(face.Area)
            if len(options) > 8 or area <= 1e-12 or not face.isValid():
                return None
            matches = []
            for index in options:
                other = originals[index]
                if not other.isValid():
                    return None
                tolerance = area * 1e-9
                forward = face.cut(other)
                reverse = other.cut(face)
                if not forward.isValid() or not reverse.isValid():
                    return None
                if float(forward.Area) <= tolerance and float(reverse.Area) <= tolerance:
                    matches.append(index)
            if len(matches) != 1 or matches[0] in used:
                return None
            used.add(matches[0])
            result.append(appearances[matches[0]])
        return result
    except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
        return None


def filtered_expanded_array_shape(array: Any, part_module: Any, max_elements: int) -> tuple[str, Any]:
    """Return visible geometry in array coordinates, or an explicit unavailable state.

    Collapsed arrays are known empty only when every native visibility entry is
    false and the combined shape is absent or null. Their BOM activity remains
    unknown. Other filtering requires expanded elements with native state.
    """
    try:
        count = int(array.ElementCount)
        if count <= 0 or count > max_elements:
            return "unavailable", None
        visibility = list(array.VisibilityList)
        if len(visibility) == count and all(value is False for value in visibility):
            shape = getattr(array, "Shape", None)
            if shape is None or shape.isNull():
                return "empty", None
        if array.ShowElement is not True:
            return "unavailable", None
        elements = list(array.ElementList)
        if len(elements) != count or len(visibility) < count:
            return "unavailable", None
        states = [native_suppression(element) for element in elements]
        if any(source != "native" or not isinstance(state, bool) for state, source in states):
            return "unavailable", None
        if any(not isinstance(visible, bool) for visible in visibility[:count]):
            return "unavailable", None
        # Preserve the existing combined shape unless filtering is necessary.
        if not any(state for state, _ in states):
            return "unchanged", None
        inverse = array.Placement.inverse()
        shapes = []
        for index, ((suppressed, _), visible) in enumerate(zip(states, visibility)):
            if suppressed or not visible:
                continue
            shape = array.getSubObject(f"{index}.", 0)
            if shape is None or shape.isNull():
                return "unavailable", None
            shape = shape.copy()
            shape.Placement = inverse.multiply(shape.Placement)
            shapes.append(shape)
        if not shapes:
            return "empty", None
        compound = part_module.makeCompound(shapes)
        if compound.isNull():
            return "unavailable", None
        compound.Placement = array.Placement
        return "filtered", compound
    except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
        return "unavailable", None
