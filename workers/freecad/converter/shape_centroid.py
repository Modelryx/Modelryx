"""Compute shape centroids only from complete native subshape evidence."""

from __future__ import annotations

import math
from typing import Any


def weighted_subshape_center(shape: Any, collection_name: str, weight_attribute: str) -> list[float] | None:
    weighted = [0.0, 0.0, 0.0]
    total = 0.0
    try:
        subshapes = getattr(shape, collection_name, ())
        for subshape in subshapes:
            weight = float(getattr(subshape, weight_attribute))
            if not math.isfinite(weight) or weight < 0:
                return None
            if weight == 0:
                continue
            center = subshape.CenterOfMass
            coordinates = [float(center.x), float(center.y), float(center.z)]
            if not all(math.isfinite(value) for value in coordinates):
                return None
            total += weight
            for index, value in enumerate(coordinates):
                weighted[index] += value * weight
    except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
        return None
    if total <= 0 or not math.isfinite(total) or not all(math.isfinite(value) for value in weighted):
        return None
    return [round(value / total, 6) for value in weighted]


def shape_center_of_mass_mm(shape: Any) -> list[float] | None:
    try:
        center = shape.CenterOfMass
        coordinates = [float(center.x), float(center.y), float(center.z)]
        if all(math.isfinite(value) for value in coordinates):
            return [round(value, 6) for value in coordinates]
    except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
        pass
    try:
        solids = getattr(shape, "Solids", ()) or ()
        if solids:
            return weighted_subshape_center(shape, "Solids", "Volume")
        return weighted_subshape_center(shape, "Faces", "Area")
    except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
        return None
