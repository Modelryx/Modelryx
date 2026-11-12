"""Cross-format equivalence checks for normalized PartMetadata geometry."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose
from typing import Any


@dataclass(frozen=True)
class EquivalenceDifference:
    field: str
    left: int | float
    right: int | float
    tolerance: str


def compare_part_metadata(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    bounding_box_abs_mm: float = 0.01,
    shape_relative: float = 0.001,
) -> list[EquivalenceDifference]:
    """Return deterministic geometry differences outside Phase 0 tolerances."""
    differences: list[EquivalenceDifference] = []
    left_geometry = left["geometry"]
    right_geometry = right["geometry"]
    for axis in ("x_mm", "y_mm", "z_mm"):
        left_value = left_geometry["bounding_box"][axis]
        right_value = right_geometry["bounding_box"][axis]
        if not isclose(left_value, right_value, rel_tol=0, abs_tol=bounding_box_abs_mm):
            differences.append(
                EquivalenceDifference(
                    f"geometry.bounding_box.{axis}",
                    left_value,
                    right_value,
                    f"absolute ±{bounding_box_abs_mm} mm",
                )
            )
    for field in ("volume_mm3", "surface_area_mm2"):
        left_value = left_geometry["shape"][field]
        right_value = right_geometry["shape"][field]
        if not isclose(left_value, right_value, rel_tol=shape_relative, abs_tol=1e-9):
            differences.append(
                EquivalenceDifference(
                    f"geometry.shape.{field}",
                    left_value,
                    right_value,
                    f"relative ±{shape_relative:.3%}",
                )
            )
    left_topology = left_geometry.get("topology", {})
    right_topology = right_geometry.get("topology", {})
    for field in ("solid_count", "face_count"):
        if field in left_topology and field in right_topology:
            left_value = left_topology[field]
            right_value = right_topology[field]
            if left_value != right_value:
                differences.append(
                    EquivalenceDifference(
                        f"geometry.topology.{field}", left_value, right_value, "exact"
                    )
                )
    return differences
