import math
from collections.abc import Mapping
from typing import Any

from modelryx.cad.models import (
    CADDocumentInspection,
    CADFeature,
    CADGeometryInspection,
    CADParameter,
)
from modelryx.metadata.array_quality import ARRAY_GEOMETRY_WARNING, has_unverified_array_geometry

EDITABLE_PARAMETERS = {
    "PAD": {"length_mm", "length"},
    "POCKET": {"depth_mm", "depth", "length_mm", "length"},
    "HOLE": {"diameter_mm", "diameter", "radius_mm", "radius"},
    "FILLET": {"radius_mm", "radius"},
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _parameters(value: Any, feature_type: str) -> list[CADParameter]:
    raw = _mapping(value)
    editable_names = EDITABLE_PARAMETERS.get(feature_type.upper(), set())
    return [
        CADParameter(
            name=str(name)[:255],
            value=item if isinstance(item, str | int | float | bool) or item is None else str(item),
            unit="deg"
            if "angle" in str(name).casefold()
            else "mm"
            if str(name) in editable_names
            else None,
            editable=(
                str(name) in editable_names
                and isinstance(item, int | float)
                and not isinstance(item, bool)
            ),
        )
        for name, item in sorted(raw.items())[:500]
    ]


def _sketch_parameters(native: Mapping[str, Any], name: str) -> list[CADParameter]:
    native_sketches = native.get("sketches", [])
    if not isinstance(native_sketches, list):
        return []
    sketches = [s for s in native_sketches
                if isinstance(s, Mapping) and s.get("name") == name]
    if len(sketches) != 1:
        return []
    constraints = sketches[0].get("constraints", [])
    if not isinstance(constraints, list):
        return []
    result = []
    for constraint in constraints:
        if not isinstance(constraint, Mapping):
            continue
        index, value, unit = (
            constraint.get("index"), constraint.get("value"), constraint.get("unit")
        )
        if (not isinstance(index, int) or isinstance(index, bool) or index < 0
                or not isinstance(value, int | float) or isinstance(value, bool)
                or not math.isfinite(value) or unit not in {"mm", "deg"}
                or constraint.get("type") not in {
                    "Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"
                }
                or constraint.get("driving") is not True or constraint.get("active") is not True
                or constraint.get("expression") is not None
                or constraint.get("expression_status") == "ambiguous"):
            continue
        if unit != ("deg" if constraint["type"] == "Angle" else "mm"):
            continue
        result.append(
            CADParameter(name=f"Constraints[{index}]", value=value, unit=unit, editable=True,
                         native_constraint_type=constraint["type"])
        )
    return result[:500]


def _material_name(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("name")
    return str(value) if value not in {None, ""} else None


def inspect_metadata_snapshot(
    metadata: Mapping[str, Any], *, revision_id: str, source_sha256: str | None
) -> CADDocumentInspection:
    source = _mapping(metadata.get("source"))
    native = _mapping(metadata.get("native"))
    geometry = _mapping(metadata.get("geometry"))
    shape = _mapping(geometry.get("shape"))
    topology = _mapping(geometry.get("topology"))
    bbox = _mapping(geometry.get("bounding_box"))
    uncertain_geometry = has_unverified_array_geometry(metadata)
    feature_section = _mapping(metadata.get("features"))
    warnings = (
        # Reserve room for geometry, feature-ID, and mutation-readiness warnings.
        list(metadata.get("quality", {}).get("warnings", []))[:97]
        if isinstance(metadata.get("quality"), dict)
        else []
    )
    if uncertain_geometry:
        warnings.append(ARRAY_GEOMETRY_WARNING)
    features: list[CADFeature] = []
    for raw_value in list(feature_section.get("items", []))[:10_000]:
        raw = _mapping(raw_value)
        persistent_id = raw.get("persistent_id")
        if not isinstance(persistent_id, str) or not persistent_id.strip():
            continue
        feature_type = str(raw.get("type") or "UNKNOWN_FEATURE")
        features.append(
            CADFeature(
                id=persistent_id,
                name=str(raw["name"]) if raw.get("name") is not None else None,
                type=feature_type,
                parameters=(
                    _sketch_parameters(native, persistent_id)
                    if feature_type.upper() == "SKETCH"
                    and str(source.get("system", "")).casefold() == "freecad"
                    else _parameters(raw.get("properties"), feature_type)
                ),
                native_type=None,
            )
        )
    if len(features) < len(list(feature_section.get("items", []))):
        warnings.append("Features without worker-supplied persistent IDs are not addressable.")
    mutation_ready = source_sha256 is not None
    if mutation_ready:
        warnings.append(
            "Editable targets are restricted to allow-listed parameters from the "
            "verified metadata snapshot."
        )
    else:
        warnings.append(
            "A verified native CAD artifact is required before a CAD session can be created."
        )
    physical = _mapping(metadata.get("physical"))
    material_value = physical.get("material") or metadata.get("material")
    return CADDocumentInspection(
        worker_id=str(source.get("worker_id") or "cad-metadata"),
        worker_version=str(source.get("worker_version") or "unknown"),
        cad_system=str(source.get("system") or "Unknown"),
        cad_version=str(source["system_version"]) if source.get("system_version") else None,
        source_revision_id=revision_id,
        source_artifact_sha256=source_sha256,
        document_label=str(native["document_label"]) if native.get("document_label") else None,
        body_count=int(native["body_count"]) if isinstance(native.get("body_count"), int) else None,
        features=features,
        geometry=CADGeometryInspection(
            bounding_box_mm={
                key: number
                for key in ("x_mm", "y_mm", "z_mm")
                if not uncertain_geometry and (number := _number(bbox.get(key))) is not None
            },
            volume_mm3=_number(shape.get("volume_mm3")) if not uncertain_geometry else None,
            surface_area_mm2=(
                _number(shape.get("surface_area_mm2")) if not uncertain_geometry else None
            ),
            solid_count=int(topology["solid_count"])
            if not uncertain_geometry and isinstance(topology.get("solid_count"), int)
            else None,
        ),
        material=_material_name(material_value),
        mutation_ready=mutation_ready,
        warnings=[str(value)[:1000] for value in warnings],
    )
