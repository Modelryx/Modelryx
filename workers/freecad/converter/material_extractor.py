"""Aggregate physical evidence without assigning a partial material to the whole model."""

from __future__ import annotations

import math
import time
from typing import Any

from extraction_provider import ProviderResult


def _valid_center(value: Any) -> bool:
    try:
        return value is not None and len(value) == 3 and all(
            math.isfinite(float(component)) for component in value
        )
    except (TypeError, ValueError, OverflowError):
        return False


class MaterialExtractor:
    def extract(self, shapes: list[dict[str, Any]], weighted_center: Any) -> ProviderResult:
        started = time.monotonic()
        assigned = [str(shape["material"]) for shape in shapes if shape.get("material")]
        materials = sorted(set(assigned))
        densities = [float(shape["density_kg_m3"]) for shape in shapes
                     if shape.get("density_kg_m3") is not None
                     and math.isfinite(float(shape["density_kg_m3"]))
                     and float(shape["density_kg_m3"]) > 0]
        complete = bool(shapes) and len(assigned) == len(shapes) and len(materials) == 1
        physical: dict[str, Any] = {}
        warnings = []
        if not assigned:
            warnings.append("Material was not available from source.")
        elif not complete:
            warnings.append("Material metadata differs or is missing across effective shapes.")
        if complete:
            physical["material"] = {"name": materials[0]}
        if densities and len(densities) != len(shapes):
            warnings.append("Density metadata is missing from some effective shapes; mass was omitted.")
        if len(densities) == len(shapes) and densities and len(set(densities)) == 1:
            physical["density_kg_m3"] = densities[0]
        mass_values = [shape.get("mass_kg") for shape in shapes]
        geometry_verified = all(shape.get("geometry_evidence_verified", True) for shape in shapes)
        if not geometry_verified:
            warnings.append("Mass was omitted because array element geometry could not be verified.")
        valid_mass = bool(shapes) and len(densities) == len(shapes) and all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0 for value in mass_values
        ) and geometry_verified
        if valid_mass:
            physical["mass_kg"] = round(sum(mass_values), 9)
            if all(_valid_center(shape.get("center_of_gravity_mm")) for shape in shapes):
                physical["center_of_mass"] = weighted_center("mass_kg")
            else:
                warnings.append("Center of mass could not be determined for every effective shape.")
        elif geometry_verified and densities and len(densities) == len(shapes):
            warnings.append("Mass could not be calculated for every effective shape; mass was omitted.")
        return ProviderResult(
            records=[physical], diagnostics=[], evidence={"warnings": warnings, "material_complete": complete},
            capability=("complete" if complete and not warnings
                        else "partial" if physical or assigned or densities else "unavailable"),
            duration_ms=(time.monotonic() - started) * 1000,
        )
