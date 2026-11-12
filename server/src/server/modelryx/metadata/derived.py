"""Server-owned deterministic fields derived from normalized PartMetadata."""

from __future__ import annotations

from typing import Any


def _drawing_tolerance_count(
    metadata: dict[str, Any], entities: list[dict[str, Any]]
) -> int | None:
    """Count verified tolerance annotations only when drawing coverage is complete."""
    extraction = metadata.get("extraction")
    capabilities = extraction.get("capabilities", {}) if isinstance(extraction, dict) else {}
    if isinstance(capabilities, dict) and capabilities and any(
        capabilities.get(key) not in {"complete", "not_applicable"}
        for key in ("drawing_dimensions", "gdt")
    ):
        return None
    count = 0
    for entity in entities:
        kind = entity.get("type")
        if kind == "TOLERANCE":
            count += 1
            continue
        if kind == "GDT":
            symbol = entity.get("symbol_kind")
            if symbol == "geometric_tolerance":
                count += 1
            elif symbol not in {
                "datum_feature", "surface_finish", "weld_symbol", "weld_symbol_tile"
            }:
                return None
            continue
        tolerance = entity.get("tolerance")
        if kind != "DIMENSION" or tolerance is None:
            continue
        if isinstance(tolerance, dict):
            enabled = tolerance.get("enabled")
            if enabled is True:
                count += 1
            elif enabled is not False:
                return None
        elif not isinstance(tolerance, bool) and tolerance != "":
            count += 1
        else:
            return None
    return count


def build_derived_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    geometry = metadata["geometry"]
    bbox = geometry["bounding_box"]
    shape = geometry["shape"]
    dimensions = [bbox["x_mm"], bbox["y_mm"], bbox["z_mm"]]
    sorted_dimensions = sorted(dimensions)
    bounding_volume = dimensions[0] * dimensions[1] * dimensions[2]
    volume = shape["volume_mm3"]

    derived: dict[str, Any] = {
        "bbox_sorted_mm": sorted_dimensions,
        "bbox_min_mm": sorted_dimensions[0],
        "bbox_mid_mm": sorted_dimensions[1],
        "bbox_max_mm": sorted_dimensions[2],
        "bounding_box_volume_mm3": bounding_volume,
        "fill_ratio": volume / bounding_volume if bounding_volume > 0 else None,
        "aspect_xy": dimensions[0] / dimensions[1] if dimensions[1] > 0 else None,
        "aspect_yz": dimensions[1] / dimensions[2] if dimensions[2] > 0 else None,
        "aspect_xz": dimensions[0] / dimensions[2] if dimensions[2] > 0 else None,
    }
    ratios = [
        sorted_dimensions[0] / sorted_dimensions[2] if sorted_dimensions[2] else 0,
        sorted_dimensions[1] / sorted_dimensions[2] if sorted_dimensions[2] else 0,
    ]
    if ratios[0] <= 0.1:
        geometry_class = "sheet_like"
    elif ratios[1] <= 0.25:
        geometry_class = "slender"
    elif derived["fill_ratio"] is not None and derived["fill_ratio"] >= 0.75:
        geometry_class = "prismatic"
    else:
        geometry_class = "complex"
    derived["geometry_classification"] = {
        "value": geometry_class,
        "method": "bounding_box_and_fill_ratio",
        "confidence": 0.65,
        "inferred": True,
    }
    derived["principal_dimensions_mm"] = sorted_dimensions
    material = metadata.get("physical", {}).get("material", {})
    material_name = material.get("normalized_name") or material.get("name")
    if material_name:
        derived["material_search"] = str(material_name).strip().casefold()
    physical = metadata.get("physical", {})
    if physical.get("density_kg_m3") is not None:
        derived["density_kg_m3"] = physical["density_kg_m3"]
    features = metadata.get("features", {})
    holes = features.get("holes", []) if isinstance(features, dict) else []
    derived["hole_diameters_mm"] = sorted(
        {
            float(hole["diameter_mm"])
            for hole in holes
            if isinstance(hole, dict) and hole.get("diameter_mm") is not None
        }
    )
    drawings = metadata.get("drawings", [])
    entities = [
        entity
        for drawing in drawings
        if isinstance(drawing, dict)
        for entity in drawing.get("entities", [])
        if isinstance(entity, dict)
    ]
    derived["drawing_count"] = len(drawings)
    derived["drawing_entity_count"] = len(entities)
    derived["tolerance_count"] = _drawing_tolerance_count(metadata, entities)
    assembly = metadata.get("assembly", {})
    for field in ("instance_count", "unique_component_count", "subassembly_count", "bom_row_count"):
        if assembly.get(field) is not None:
            derived[field] = assembly[field]
    derived["quality_search"] = dict(metadata.get("quality", {}))
    return derived
