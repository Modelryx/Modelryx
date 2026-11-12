"""Normalize FreeCAD extraction data to the CAD-neutral PartMetadata v1 contract.

This module deliberately does not import FreeCAD.  Extraction and normalization
are separate worker stages, which also makes the contract straightforward to
unit test in the normal CPython test environment.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from math import isfinite
from typing import Any

SCHEMA = "modelryx.part-metadata"
SCHEMA_VERSION = "1.0"
QUALITY_STATES = {"complete", "partial", "unavailable", "unsupported", "not_applicable"}
KINDS = {"part", "assembly", "unknown"}
FEATURE_NAMES = (
    "hole",
    "fillet",
    "chamfer",
    "extrusion",
    "revolution",
    "pocket",
    "pattern",
    "thread",
)

FREECAD_REFERENCE_GEOMETRY = {
    "PartDesign::Plane": "plane",
    "PartDesign::Line": "axis",
    "PartDesign::Point": "point",
    "PartDesign::CoordinateSystem": "coordinate_system",
    "PartDesign::ShapeBinder": "shape_binder",
    "PartDesign::SubShapeBinder": "shape_binder",
    "App::Plane": "plane",
    "App::Line": "axis",
    "App::Point": "point",
}

FREECAD_FEATURE_TYPES = {
    **{
        f"PartDesign::{name}": "pattern"
        for name in ("LinearPattern", "PolarPattern", "Mirrored", "MultiTransform", "Scaled")
    },
    **{
        f"PartDesign::{name}": "boss"
        for name in (
            "AdditiveBox",
            "AdditivePrism",
            "AdditiveCylinder",
            "AdditiveCone",
            "AdditiveSphere",
            "AdditiveTorus",
            "AdditiveWedge",
            "AdditiveEllipsoid",
        )
    },
    **{
        f"PartDesign::{name}": "pocket"
        for name in (
            "Pocket",
            "SubtractiveBox",
            "SubtractivePrism",
            "SubtractiveCylinder",
            "SubtractiveCone",
            "SubtractiveSphere",
            "SubtractiveTorus",
            "SubtractiveWedge",
            "SubtractiveEllipsoid",
        )
    },
    **{f"PartDesign::{name}": "loft" for name in ("AdditiveLoft", "SubtractiveLoft")},
    **{
        f"PartDesign::{name}": "sweep"
        for name in ("AdditivePipe", "SubtractivePipe", "AdditiveHelix", "SubtractiveHelix")
    },
    **{
        f"Part::{name}": "boss"
        for name in ("Box", "Cylinder", "Cone", "Sphere", "Torus", "Prism", "Wedge", "Ellipsoid")
    },
    **{f"Part::{name}": "boolean" for name in ("Common", "Fuse", "Cut")},
    "PartDesign::Hole": "hole",
    "PartDesign::Fillet": "fillet",
    "PartDesign::Chamfer": "chamfer",
    "PartDesign::Pad": "extrusion",
    "PartDesign::Revolution": "revolution",
    "PartDesign::Groove": "revolution",
    "PartDesign::Thickness": "shell",
    "PartDesign::Draft": "draft",
    "PartDesign::Boolean": "boolean",
    "Part::Fillet": "fillet",
    "Part::Chamfer": "chamfer",
    "Part::Extrusion": "extrusion",
    "Part::Revolution": "revolution",
    "Part::Loft": "loft",
    "Part::Thickness": "shell",
}


def freecad_reference_geometry_kind(type_id: str) -> str | None:
    """Classify construction/reference objects that are not modeling features."""
    if "Origin" in type_id:
        return "origin"
    return FREECAD_REFERENCE_GEOMETRY.get(type_id)


def normalized_freecad_feature_name(type_id: str, proxy_type: str | None = None) -> str | None:
    """Map a FreeCAD history type to a PartMetadata engineering feature class."""
    if type_id == "Part::FeaturePython" and str(proxy_type or "").casefold() in {
        "array",
        "patharray",
        "pointarray",
    }:
        return "pattern"
    return FREECAD_FEATURE_TYPES.get(type_id)


def normalized_freecad_feature_names(
    type_id: str,
    profile_geometry_types: tuple[str, ...] = (),
    closed_profile_count: int = 0,
    *,
    proxy_type: str | None = None,
    suppressed: bool = False,
) -> list[str]:
    """Map one history operation to the physical features it creates.

    A single circular pocket profile can represent a hole. Multiple wires can
    contain islands, so their circles alone do not establish separate holes.
    """
    if suppressed:
        return []
    feature = normalized_freecad_feature_name(type_id, proxy_type)
    if feature == "hole" and profile_geometry_types:
        circles = sum(
            geometry_type.casefold() in {"part::geomcircle", "part.circle"}
            for geometry_type in profile_geometry_types
        )
        points = sum(
            geometry_type.casefold() in {"part::geompoint", "part.point", "point"}
            for geometry_type in profile_geometry_types
        )
        return ["hole"] * (circles or points or 1)
    if feature != "pocket" or not profile_geometry_types:
        return [feature] if feature else []
    circles = sum(
        geometry_type.casefold() in {"part::geomcircle", "part.circle"}
        for geometry_type in profile_geometry_types
    )
    if circles:
        if len(profile_geometry_types) == 1 and closed_profile_count == 1:
            return ["hole"]
        return ["pocket"]
    if closed_profile_count:
        return ["pocket"] * closed_profile_count
    return ["pocket"]


def freecad_feature_suppressed(properties: Mapping[str, Any]) -> bool | None:
    """Return an explicit native suppression state without inferring it from visibility.

    FreeCAD hides earlier Part Design history objects as part of normal tip rendering, so
    ``Visibility = false`` does not mean that a feature is suppressed. Only a populated,
    boolean suppression property is authoritative enough to affect effective feature counts.
    """
    keyed = {_property_key(str(name)): value for name, value in properties.items()}
    for name in ("suppressed", "issuppressed"):
        value = keyed.get(name)
        if isinstance(value, bool):
            return value
    return None


class PartMetadataNormalizationError(ValueError):
    """Raised when raw extraction data cannot form valid PartMetadata v1."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(
    value: Any, field: str, *, allow_zero: bool = True, allow_negative: bool = False
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise PartMetadataNormalizationError(f"{field} must be a finite number")
    if (not allow_negative and value < 0) or (not allow_zero and value == 0):
        qualifier = "positive" if not allow_zero else "non-negative"
        raise PartMetadataNormalizationError(f"{field} must be {qualifier}")
    return value


def _count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PartMetadataNormalizationError(f"{field} must be a non-negative integer")
    return value


def _first(source: Mapping[str, Any], *names: str) -> Any:
    return next((source[name] for name in names if source.get(name) is not None), None)


def _property_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _string_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        items = [item.strip() for item in value.replace(";", ",").split(",")]
        return [item for item in items if item]
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return [item.strip() for item in value if item.strip()]
    return None


def identity_from_properties(properties: Mapping[str, Any]) -> dict[str, Any]:
    """Map common FreeCAD property aliases into CAD-neutral identity fields."""
    aliases = {
        "display_name": ("displayname", "title"),
        "description": ("description", "desc"),
        "part_number": (
            "partnumber",
            "drawingnumber",
            "partno",
            "partnum",
            "stockcode",
        ),
        "revision": ("revision", "revisionnumber", "rev"),
    }
    keyed = {_property_key(str(name)): value for name, value in properties.items()}
    result: dict[str, Any] = {}
    for field, names in aliases.items():
        value = next((keyed[name] for name in names if keyed.get(name) is not None), None)
        if isinstance(value, str) and value.strip():
            result[field] = value.strip()
    for field, names in {
        "classification": ("classification", "class", "category", "categories"),
        "tags": ("tags", "keywords"),
    }.items():
        value = next((keyed[name] for name in names if keyed.get(name) is not None), None)
        if items := _string_list(value):
            result[field] = items
    return result


class PartMetadataNormalizer:
    """Translate a FreeCAD worker's raw, JSON-like extraction result."""

    def __init__(self, worker: str = "modelryx-freecad-worker", worker_version: str = "unknown"):
        self.worker = worker
        self.worker_version = worker_version

    def normalize(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise PartMetadataNormalizationError("raw metadata must be a mapping")

        source_raw = _mapping(raw.get("source"))
        document_properties = _mapping(raw.get("properties"))
        identity_raw = {
            **identity_from_properties(document_properties),
            **dict(_mapping(raw.get("identity"))),
        }
        geometry_raw = _mapping(raw.get("geometry"))
        quality_raw = _mapping(raw.get("quality"))
        warnings = list(quality_raw.get("warnings", raw.get("warnings", [])) or [])
        if not all(isinstance(item, str) for item in warnings):
            raise PartMetadataNormalizationError("quality.warnings must contain only strings")

        name = _first(identity_raw, "name") or _first(
            raw, "name", "document_label", "document_name", "source_filename"
        )
        if not isinstance(name, str) or not name.strip():
            raise PartMetadataNormalizationError("identity.name is required")
        kind = identity_raw.get("kind", raw.get("kind", "part"))
        if kind not in KINDS:
            raise PartMetadataNormalizationError(f"identity.kind must be one of {sorted(KINDS)}")

        metadata: dict[str, Any] = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "source": {
                "system": "freecad",
                "system_version": source_raw.get("system_version", raw.get("freecad_version")),
                "worker": source_raw.get("worker", raw.get("worker", self.worker)),
                "worker_version": source_raw.get(
                    "worker_version", raw.get("worker_version", self.worker_version)
                ),
                "extracted_at": source_raw.get("extracted_at", raw.get("extracted_at"))
                or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
            "identity": {"name": name.strip(), "kind": kind},
            "geometry": self._geometry(raw, geometry_raw),
            "quality": {
                "geometry": self._quality(quality_raw, "geometry", "complete"),
                "physical": self._quality(quality_raw, "physical", "unavailable"),
                "features": self._quality(quality_raw, "features", "unsupported"),
                "assembly": self._quality(
                    quality_raw, "assembly", "complete" if kind == "assembly" else "not_applicable"
                ),
                "warnings": warnings,
            },
        }
        self._copy_identity(identity_raw, metadata["identity"])
        self._add_optional_sections(raw, metadata)
        return metadata

    def _geometry(self, raw: Mapping[str, Any], geometry_raw: Mapping[str, Any]) -> dict[str, Any]:
        bbox_raw = _mapping(geometry_raw.get("bounding_box", raw.get("bounding_box")))
        dimensions = {
            "x_mm": _first(bbox_raw, "x_mm", "x", "XLength"),
            "y_mm": _first(bbox_raw, "y_mm", "y", "YLength"),
            "z_mm": _first(bbox_raw, "z_mm", "z", "ZLength"),
        }
        if any(value is None for value in dimensions.values()):
            raise PartMetadataNormalizationError("geometry.bounding_box requires x, y, and z")
        bbox = {
            key: _number(value, f"geometry.bounding_box.{key}") for key, value in dimensions.items()
        }

        shape_raw = _mapping(geometry_raw.get("shape"))
        volume = _first(shape_raw, "volume_mm3")
        if volume is None:
            volume = _first(raw, "shape_volume", "volume_mm3", "volumeMm3")
        area = _first(shape_raw, "surface_area_mm2")
        if area is None:
            area = _first(raw, "shape_area", "surface_area_mm2", "surfaceAreaMm2")
        if volume is None or area is None:
            raise PartMetadataNormalizationError(
                "geometry.shape requires volume_mm3 and surface_area_mm2"
            )

        result: dict[str, Any] = {
            "bounding_box": bbox,
            "shape": {
                "volume_mm3": _number(volume, "geometry.shape.volume_mm3"),
                "surface_area_mm2": _number(area, "geometry.shape.surface_area_mm2"),
            },
        }
        for section in ("center", "topology"):
            value = geometry_raw.get(section, raw.get(section))
            if value is not None:
                result[section] = self._normalize_geometry_section(section, _mapping(value))
        return result

    def _normalize_geometry_section(self, section: str, value: Mapping[str, Any]) -> dict[str, Any]:
        if section == "center":
            return {
                key: _number(
                    _first(value, key, key[0]),
                    f"geometry.center.{key}",
                    allow_negative=True,
                )
                for key in ("x_mm", "y_mm", "z_mm")
            }
        fields = ("solid_count", "shell_count", "face_count", "edge_count", "vertex_count")
        return {
            key: _count(value[key], f"geometry.topology.{key}") for key in fields if key in value
        }

    @staticmethod
    def _copy_identity(source: Mapping[str, Any], target: dict[str, Any]) -> None:
        for key in (
            "display_name",
            "description",
            "part_number",
            "revision",
            "classification",
            "tags",
        ):
            if key in source and source[key] is not None:
                target[key] = deepcopy(source[key])

    def _add_optional_sections(self, raw: Mapping[str, Any], metadata: dict[str, Any]) -> None:
        for section in (
            "physical",
            "assembly",
            "parameters",
            "manufacturing",
            "extraction",
            "properties",
            "drawings",
        ):
            if section in raw and raw[section] is not None:
                metadata[section] = deepcopy(raw[section])

        feature_raw = _mapping(raw.get("features"))
        if feature_raw:
            features = deepcopy(dict(feature_raw))
            summary = _mapping(features.get("summary"))
            features["summary"] = {
                f"{name}_count": _count(value, f"features.summary.{name}_count")
                for name in FEATURE_NAMES
                if (value := summary.get(f"{name}_count")) is not None
            }
            metadata["features"] = features

        native = deepcopy(dict(_mapping(raw.get("native"))))
        for key in ("body", "tip", "object_count", "document_label", "source_document"):
            if key in raw and key not in native:
                native[key] = deepcopy(raw[key])
        if native:
            metadata["native"] = native

    @staticmethod
    def _quality(raw: Mapping[str, Any], key: str, default: str) -> str:
        value = raw.get(key, default)
        if value not in QUALITY_STATES:
            raise PartMetadataNormalizationError(
                f"quality.{key} must be one of {sorted(QUALITY_STATES)}"
            )
        return value


def normalize_freecad_metadata(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Convenience entry point using the default FreeCAD worker identity."""
    return PartMetadataNormalizer().normalize(raw)
