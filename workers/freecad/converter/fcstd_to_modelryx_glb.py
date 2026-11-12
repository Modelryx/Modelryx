"""Export an FCStd document to a Modelryx Graphics (.pfg) package.

Unlike the FCStd -> STEP -> GLB pipeline, this exporter traverses the FreeCAD
document directly.  It consequently retains assembly/Body relationships and
gives every selectable logical item a stable Modelryx occurrence ID.

The package is a ZIP container with only preview data:
  manifest.json       package schema and source information
  model.glb           rendering hierarchy and mesh data
  model-preview.json  navigation tree and viewer presentation data

Raw FreeCAD metadata and normalized PartMetadata are emitted as adjacent worker
sidecars.  They are server/AI data and never belong in the preview package.
"""

from __future__ import annotations

# FreeCAD's Python module rewrites the importing module's globals during initialization and removes
# module bindings created before this import. It must therefore load before
# stdlib and local modules.
try:
    import FreeCAD  # type: ignore
except ImportError as exc:
    raise SystemExit(
        "This script must run in the FreeCAD Python environment with numpy and trimesh installed.\n"
        "Install them with: conda install -n freecad -c conda-forge numpy trimesh"
    ) from exc

import argparse
import hashlib
import itertools
import json
import logging
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import time
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

source_shared = Path(__file__).resolve().parents[1] / "shared"
if source_shared.is_dir():
    sys.path.insert(0, str(source_shared))

from assembly_extractor import AssemblyExtractor
from assembly_suppression import native_suppression
from array_occurrences import verified_array_occurrences
from cad_references import object_reference, object_references
from joint_occurrences import map_joint_occurrences
from dependency_evidence import dependency_report_evidence, find_dependency_report
from document_health import DocumentInspector, recompute_for_extraction
from fcstd_visibility import read_saved_visibility
from link_array_evidence import compose_world_transform
from link_array_geometry import filtered_expanded_array_shape, filtered_face_appearances
from freecad_material import material_density_kg_m3, material_name
from feature_properties import normalized_feature_properties
from material_extractor import MaterialExtractor
from reference_geometry_extractor import ReferenceGeometryExtractor
from parameter_graph import (
    BoundedParameterLoader, ParameterExtractor, add_external_graph_edges, cross_document_cycles, external_parameter_evidence,
    external_upstream_graph, native_parameters,
)
from part_metadata_normalizer import (
    FEATURE_NAMES,
    PartMetadataNormalizer,
    freecad_feature_suppressed,
    freecad_reference_geometry_kind,
    identity_from_properties,
    normalized_freecad_feature_name,
    normalized_freecad_feature_names,
)
from modelryx_freecad_shared import (
    WORKBENCH_MODULES,
    assembly_capability_statuses,
    assembly_model,
    fcstd_document_provenance,
    workbench_module_issue,
)
from shape_centroid import shape_center_of_mass_mm
from sketch_extractor import SketchExtractor
from techdraw_extractor import TechDrawExtractor
from worker_io import atomic_write_json, check_native_document_size, peak_memory_bytes, sha256_file

try:
    import MeshPart  # type: ignore
    import Part  # type: ignore
    import numpy as np
except ImportError as exc:
    raise SystemExit(
        "This script must run in the FreeCAD Python environment with numpy and trimesh installed.\n"
        "Install them with: conda install -n freecad -c conda-forge numpy trimesh"
    ) from exc

try:
    import trimesh
except ImportError:
    trimesh = None

try:
    from PIL import Image, ImageDraw
except ImportError:
    # Local metadata extraction deliberately does not require preview dependencies.
    Image = None
    ImageDraw = None


SCHEMA_VERSION = 1
CONVERTER_VERSION = "modelryx-fcstd-29"
logger = logging.getLogger(__name__)
_warned_conditions: set[str] = set()


def warn_once(key: str, message: str, *args: object) -> None:
    if key in _warned_conditions:
        return
    _warned_conditions.add(key)
    logger.warning(message, *args)


TESSELLATION_PROFILES = {
    "draft": {"linear_deflection": 0.5, "angular_deflection": 25.0},
    "standard": {"linear_deflection": 0.2, "angular_deflection": 15.0},
    "fine": {"linear_deflection": 0.05, "angular_deflection": 7.5},
}


def tessellation_settings(
    profile: str,
    linear_deflection: float | None = None,
    angular_deflection: float | None = None,
) -> tuple[float, float, str]:
    """Resolve a named tessellation profile with optional explicit overrides."""
    if profile not in TESSELLATION_PROFILES:
        choices = ", ".join(sorted(TESSELLATION_PROFILES))
        raise ValueError(f"Unknown tessellation profile {profile!r}; expected one of: {choices}.")
    defaults = TESSELLATION_PROFILES[profile]
    linear = float(
        defaults["linear_deflection"] if linear_deflection is None else linear_deflection
    )
    angular = float(
        defaults["angular_deflection"] if angular_deflection is None else angular_deflection
    )
    if linear <= 0 or angular <= 0:
        raise ValueError("Tessellation deflections must be greater than zero.")
    source = "profile" if linear_deflection is None and angular_deflection is None else "override"
    return linear, angular, source


def empty_glb() -> bytes:
    """Return a standards-valid GLB containing one empty scene."""
    document = json.dumps(
        {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": []}], "nodes": []},
        separators=(",", ":"),
    ).encode("utf-8")
    document += b" " * (-len(document) % 4)
    chunk = struct.pack("<I4s", len(document), b"JSON") + document
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunk)) + chunk


def stable_id(*parts: str) -> str:
    """Return a deterministic ID; labels are deliberately not used as identity."""
    value = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:24]


def document_identity(document: Any) -> str:
    """Return a stable key independent of the worker's temporary directory."""
    filename = str(getattr(document, "FileName", "") or "")
    return Path(filename).name if filename else str(getattr(document, "Name", "") or "Document")


def is_group(obj: Any) -> bool:
    # App::Link exposes the linked object's children through Group, but it is an
    # occurrence with its own resolved Shape and Placement. Treating it as a
    # container skips that shape and walks the source document's feature
    # history instead, which can export the wrong component entirely.
    if getattr(obj, "TypeId", "") == "App::Link":
        return False
    return bool(getattr(obj, "Group", None)) or getattr(obj, "TypeId", "") in {
        "App::Part",
        "App::DocumentObjectGroup",
        "App::DocumentObjectGroupExtension",
    }


def hierarchy_children(obj: Any) -> list[Any]:
    """Return FreeCAD tree children, including claimed dependency children.

    Containers expose their direct children through ``Group``. FeaturePython,
    Part, and sketch-driven objects commonly claim their linked inputs in the
    GUI tree instead; those relationships are available headlessly through
    ``OutList``. App::Link is deliberately kept as a leaf occurrence because
    walking its linked source would duplicate component feature history.
    """
    group = list(getattr(obj, "Group", None) or [])
    if group:
        return group
    type_id = str(getattr(obj, "TypeId", ""))
    if type_id in {"App::Link", "App::LinkElement", "PartDesign::FeatureBase"} or type_id.startswith(
        "TechDraw::"
    ):
        return []
    document = getattr(obj, "Document", None)
    return [
        child
        for child in (getattr(obj, "OutList", None) or [])
        if child is not obj
        and getattr(child, "Document", None) is document
        and (is_group(child) or has_renderable_geometry(child))
    ]


def linked_object_for(obj: Any) -> Any | None:
    """Resolve saved App::Link targets across FreeCAD versions and reopened documents."""
    if getattr(obj, "TypeId", "") != "App::Link":
        return None
    linked = getattr(obj, "LinkedObject", None)
    if linked is not None:
        return linked
    resolver = getattr(obj, "getLinkedObject", None)
    if callable(resolver):
        try:
            linked = resolver(True)
        except (RuntimeError, TypeError):
            try:
                linked = resolver()
            except (RuntimeError, TypeError):
                return None
    return linked if linked is not obj else None


def has_shape(obj: Any) -> bool:
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return False
    try:
        return not shape.isNull() and shape.Vertexes
    except Exception as exc:
        warn_once("has-shape", "Failed to inspect FreeCAD shape geometry: %s", exc)
        return False


def same_native_shape(first: Any, second: Any) -> bool:
    """Compare shared B-Rep topology and orientation while ignoring occurrence placement."""
    try:
        return bool(first.Orientation == second.Orientation and first.isPartner(second))
    except (AttributeError, RuntimeError, TypeError):
        return False


def has_native_mesh(obj: Any) -> bool:
    """Return whether an object owns a non-empty FreeCAD mesh."""
    mesh = getattr(obj, "Mesh", None)
    if mesh is None:
        return False
    try:
        topology = mesh.Topology
        return bool(topology[0]) and bool(topology[1])
    except Exception as exc:
        warn_once("has-native-mesh", "Failed to inspect FreeCAD native mesh geometry: %s", exc)
        return False


def has_renderable_geometry(obj: Any) -> bool:
    return has_shape(obj) or has_native_mesh(obj)


def visible(obj: Any) -> bool:
    view = getattr(obj, "ViewObject", None)
    return bool(getattr(view, "Visibility", True))


def node_type(obj: Any) -> str:
    type_id = getattr(obj, "TypeId", "")
    if type_id == "PartDesign::Body":
        return "body"
    if freecad_reference_geometry_kind(str(type_id)) is not None:
        return "reference"
    if is_group(obj):
        return "assembly"
    proxy = getattr(obj, "Proxy", None)
    proxy_module = str(getattr(type(proxy), "__module__", ""))
    proxy_name = str(getattr(type(proxy), "__name__", ""))
    if (
        "Joint" in type_id
        or type_id.startswith("Assembly::")
        or (proxy_module == "JointObject" and proxy_name in {"Joint", "GroundedJoint"})
    ):
        return "joint"
    if type_id.startswith("PartDesign::"):
        return "feature"
    return "part"


def cad_type(obj: Any) -> str:
    """Return the native FreeCAD type users recognize in the model tree."""
    type_id = str(getattr(obj, "TypeId", ""))
    if type_id in {"App::DocumentObjectGroup", "App::DocumentObjectGroupPython"}:
        return "Group"
    if is_group(obj):
        return "Assembly"
    if type_id == "Sketcher::SketchObject":
        return "Sketch"
    proxy = getattr(obj, "Proxy", None)
    proxy_type = getattr(proxy, "Type", None)
    if isinstance(proxy_type, str) and proxy_type.strip():
        return {
            "CompoundFilter": "Compound",
            "FeatureSlice": "Slice",
        }.get(proxy_type.strip(), proxy_type.strip())
    if type_id == "Part::FeaturePython":
        label = str(getattr(obj, "Label", "") or getattr(obj, "Name", ""))
        shape = getattr(obj, "Shape", None)
        shape_type = str(getattr(shape, "ShapeType", "") or "")
        if re.search(r"\.\d+$", label) and shape_type:
            return shape_type
        if label.casefold().startswith("slice"):
            return "Slice"
        if shape_type:
            return shape_type
    if "::" in type_id:
        return type_id.rsplit("::", 1)[1]
    return type_id or node_type(obj).replace("_", " ").title()


def is_feature_history_type(type_id: str) -> bool:
    """Identify editable/modeling history without treating plain result shapes as history."""
    if type_id == "Sketcher::SketchObject":
        return True
    if freecad_reference_geometry_kind(type_id) is not None:
        return False
    if type_id.startswith("PartDesign::") and type_id != "PartDesign::Body":
        return True
    return type_id in {
        "Part::Box",
        "Part::Cone",
        "Part::Cylinder",
        "Part::Chamfer",
        "Part::Common",
        "Part::Cut",
        "Part::Extrusion",
        "Part::Ellipsoid",
        "Part::FeaturePython",
        "Part::Fillet",
        "Part::Fuse",
        "Part::Loft",
        "Part::Revolution",
        "Part::Prism",
        "Part::Sphere",
        "Part::Thickness",
        "Part::Torus",
        "Part::Wedge",
    }


def primitive_property(value: Any) -> Any:
    """Return values that are safe and useful in JSON; skip FreeCAD internals."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        if all(isinstance(entry, str) for entry in value):
            return list(value)
        if all(
            isinstance(entry, (int, float))
            and not isinstance(entry, bool)
            and (not isinstance(entry, float) or math.isfinite(entry))
            for entry in value
        ):
            return list(value)
    return str(value) if type(value).__name__ in {"Quantity", "Vector", "Placement"} else None


def custom_properties(obj: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in getattr(obj, "PropertiesList", []):
        if name in {"Shape", "Placement", "Group", "Tip", "Origin", "Links", "LinkTransform"}:
            continue
        try:
            value = primitive_property(getattr(obj, name))
        except Exception as exc:
            warn_once(
                "custom-properties", "Failed to read one or more FreeCAD custom properties: %s", exc
            )
            continue
        if value is not None:
            result[name] = value
    return result


def document_properties(document: Any, roots: list[Any]) -> dict[str, Any]:
    """Collect document and single-root properties without guessing their semantics."""
    result: dict[str, Any] = {}
    if len(roots) == 1:
        result.update(custom_properties(roots[0]))
    result.update(custom_properties(document))
    metadata = getattr(document, "Meta", None)
    if isinstance(metadata, dict):
        for name, raw_value in metadata.items():
            value = primitive_property(raw_value)
            if value is not None:
                result[str(name)] = value
    # These values identify the current machine/run, not the CAD document.
    # Excluding them keeps local preflight and server extraction deterministic.
    result.pop("FileName", None)
    result.pop("TransientDir", None)
    return result


def _numeric_parameter_value(value: Any) -> tuple[float | None, str | None]:
    """Return a finite numeric value and unit without serializing FreeCAD internals."""
    raw_value = getattr(value, "Value", value)
    try:
        number = float(raw_value)
    except (TypeError, ValueError, OverflowError, NotImplementedError):
        try:
            rendered = str(value)
        except (TypeError, ValueError, RuntimeError, NotImplementedError):
            return None, None
        match = re.match(r"^\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)", rendered)
        if not match:
            return None, None
        number = float(match.group(1))
    if not np.isfinite(number):
        return None, None
    native_unit = getattr(value, "Unit", None)
    unit_type = str(getattr(native_unit, "Type", "") or "")
    unit = {"Length": "mm", "Angle": "deg"}.get(unit_type)
    if unit is None:
        unit = str(native_unit or "").strip() or None
    if unit is None:
        rendered = str(value).strip()
        match = re.match(r"^\s*[-+\d.eE]+\s*(.*)$", rendered)
        unit = match.group(1).strip() if match and match.group(1).strip() else None
    return number, unit




def native_configurations(document: Any) -> tuple[dict[str, Any], int]:
    """Extract explicit configuration selectors and their allowed values."""
    selectors: list[dict[str, Any]] = []
    skipped = 0
    selector_names = {"configuration", "variant", "config", "modelstate", "model_state"}
    option_names = {"configurations", "variants", "modelstates", "model_states"}
    for obj in sorted(getattr(document, "Objects", ()), key=lambda item: str(item.Name).casefold()):
        properties = tuple(getattr(obj, "PropertiesList", ()))
        keyed = {str(name).casefold(): str(name) for name in properties}
        for normalized_name in sorted(selector_names):
            property_name = keyed.get(normalized_name)
            if property_name is None:
                continue
            try:
                active = str(getattr(obj, property_name, "") or "").strip()
                options: list[str] = []
                property_type = str(
                    getattr(obj, "getTypeIdOfProperty", lambda _name: "")(property_name) or ""
                )
                enumeration_method = getattr(obj, "getEnumerationsOfProperty", None)
                if property_type == "App::PropertyEnumeration" and callable(enumeration_method):
                    options = [
                        str(value).strip()
                        for value in enumeration_method(property_name)
                        if str(value).strip()
                    ]
                for choices_name in option_names:
                    choices_property = keyed.get(choices_name)
                    if choices_property is None:
                        continue
                    raw_choices = getattr(obj, choices_property, ()) or ()
                    if isinstance(raw_choices, (tuple, list)):
                        options.extend(str(value).strip() for value in raw_choices if str(value).strip())
                if active and active not in options:
                    options.append(active)
                selectors.append(
                    {
                        "object": str(obj.Name),
                        "label": str(getattr(obj, "Label", obj.Name)),
                        "property": property_name,
                        "active": active or None,
                        "options": sorted(set(options), key=str.casefold),
                        "source_type": str(getattr(obj, "TypeId", "")),
                    }
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                skipped += 1
    active_values = sorted(
        {str(item["active"]) for item in selectors if item.get("active")}, key=str.casefold
    )
    variants = sorted(
        {str(value) for item in selectors for value in item.get("options", [])}, key=str.casefold
    )
    return {
        "active": active_values[0] if len(active_values) == 1 else None,
        "active_values": active_values,
        "variants": variants,
        "selectors": selectors,
    }, skipped




def _sketch_vector(value: Any) -> list[float] | None:
    try:
        return [round(float(value.x), 9), round(float(value.y), 9), round(float(value.z), 9)]
    except (AttributeError, TypeError, ValueError):
        return None


def _sketch_reference(value: Any, identity: Any = document_identity) -> dict[str, Any] | None:
    return object_reference(value, identity)


def _sketch_references(value: Any, identity: Any = document_identity) -> list[dict[str, Any]]:
    return object_references(value, identity)




def _placement_value(value: Any) -> dict[str, list[float]] | None:
    """Serialize a Placement without depending on its owning document object."""
    try:
        base = value.Base
        quaternion = value.Rotation.Q
        return {
            "position_mm": [round(float(base.x), 9), round(float(base.y), 9), round(float(base.z), 9)],
            "quaternion_xyzw": [round(float(component), 12) for component in quaternion],
        }
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _link_array_element_transform(obj: Any, index: int) -> dict[str, Any] | None:
    """Get FreeCAD's element transform in the coordinate system of the array's parent."""
    try:
        target, matrix = obj.getSubObject(f"{index}.", 1, FreeCAD.Matrix())
        if target is None:
            return None
        if getattr(target, "TypeId", "") == "App::LinkElement":
            # The subobject matrix has the element placement but stops at the
            # expanded LinkElement, before its own scale has been applied.
            target, matrix = target.getLinkedObject(False, matrix, False)
            if target is None:
                return None
        rows = [
            [float(getattr(matrix, f"A{row}{column}")) for column in range(1, 5)]
            for row in range(1, 5)
        ]
        if not all(math.isfinite(value) for row in rows for value in row):
            return None
        return {
            "matrix": [[round(value, 12) for value in row] for row in rows],
            "translation_unit": "mm",
        }
    except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
        return None




def bom_rows(component_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group component occurrences by stable definition identity."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in component_items:
        grouped.setdefault(str(item["partId"]), []).append(item)
    rows = []
    for part_id, items in grouped.items():
        representative = items[0]
        properties = representative.get("properties", {})
        row = {
            "stable_id": part_id,
            "part_id": part_id,
            "name": representative.get("name"),
            "quantity": len(items),
            "occurrence_ids": [item["occurrenceId"] for item in items],
            "source": representative.get("source", {}),
            "properties": properties,
        }
        row.update(identity_from_properties(properties))
        if representative.get("material"):
            row["material"] = representative["material"]
        rows.append(row)
    return rows


def physical_properties(obj: Any, *, shape_override: Any = None) -> dict[str, Any]:
    """Return normalized geometry facts in millimetres for AI/search consumers."""
    shape = shape_override if shape_override is not None else getattr(obj, "Shape", None)
    if shape is None or getattr(shape, "isNull", lambda: True)():
        return {}
    result: dict[str, Any] = {}
    try:
        bounds = shape.BoundBox
        result["boundingBoxMm"] = {
            "x": round(float(bounds.XLength), 6),
            "y": round(float(bounds.YLength), 6),
            "z": round(float(bounds.ZLength), 6),
        }
    except Exception as exc:
        warn_once("physical-bounds", "Failed to compute FreeCAD bounding box metadata: %s", exc)
    for key, attribute in (("volumeMm3", "Volume"), ("surfaceAreaMm2", "Area")):
        try:
            result[key] = round(float(getattr(shape, attribute)), 6)
        except Exception as exc:
            warn_once(
                "physical-metrics",
                "Failed to compute one or more FreeCAD physical metrics: %s",
                exc,
            )
    center = shape_center_of_mass_mm(shape)
    if center is not None:
        result["centerOfMassMm"] = center
    return result



def profile_geometry(obj: Any, *, include_construction: bool = True) -> tuple[Any, ...]:
    """Return driving sketch geometry, optionally excluding construction geometry."""
    profile = getattr(obj, "Profile", None)
    if isinstance(profile, (tuple, list)) and profile:
        profile = profile[0]
    geometry = getattr(profile, "Geometry", ())
    construction = getattr(profile, "getConstruction", None)
    return tuple(
        entry
        for index, entry in enumerate(geometry)
        if include_construction or not callable(construction) or not construction(index)
    )


def profile_geometry_types(obj: Any, *, include_construction: bool = True) -> tuple[str, ...]:
    """Return stable geometry type IDs from a feature's driving sketch."""
    geometry = profile_geometry(obj, include_construction=include_construction)
    return tuple(str(getattr(entry, "TypeId", "") or type(entry).__name__) for entry in geometry)


def profile_closed_wire_count(obj: Any) -> int:
    """Count separate closed regions in a feature's driving sketch."""
    profile = getattr(obj, "Profile", None)
    if isinstance(profile, (tuple, list)) and profile:
        profile = profile[0]
    shape = getattr(profile, "Shape", None)
    count = 0
    for wire in getattr(shape, "Wires", ()):
        try:
            closed = bool(wire.isClosed())
        except (AttributeError, TypeError):
            closed = bool(getattr(wire, "Closed", False))
        if closed:
            count += 1
    return count


def feature_holes(obj: Any, type_id: str, properties: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract schema-safe hole dimensions from native holes or circular pockets."""
    diameters: list[float] = []
    if type_id == "PartDesign::Hole":
        diameter = properties.get("diameter_mm")
        if diameter is None and properties.get("radius_mm") is not None:
            diameter = float(properties["radius_mm"]) * 2.0
        if isinstance(diameter, (int, float)) and diameter > 0:
            profile_types = profile_geometry_types(obj)
            circles = sum(
                geometry_type.casefold() in {"part::geomcircle", "part.circle"}
                for geometry_type in profile_types
            )
            points = sum(
                geometry_type.casefold() in {"part::geompoint", "part.point", "point"}
                for geometry_type in profile_types
            )
            diameters.extend([float(diameter)] * (circles or points or 1))
    elif type_id == "PartDesign::Pocket":
        geometry_items = profile_geometry(obj, include_construction=False)
        if len(geometry_items) != 1 or profile_closed_wire_count(obj) != 1:
            return []
        for geometry in geometry_items:
            geometry_type = str(getattr(geometry, "TypeId", "") or type(geometry).__name__)
            if geometry_type.casefold() not in {"part::geomcircle", "part.circle"}:
                continue
            try:
                radius = float(geometry.Radius)
            except (TypeError, ValueError, AttributeError):
                continue
            if radius > 0:
                diameters.append(radius * 2.0)
    if not diameters:
        return []
    termination = str(
        properties.get("Type")
        or properties.get("LengthType")
        or properties.get("DepthType")
        or properties.get("HoleCutType")
        or ""
    ).casefold()
    through = "through" in termination
    depth = properties.get("depth_mm")
    result = []
    for diameter in sorted(set(diameters)):
        hole_cut_type = str(getattr(obj, "HoleCutType", "") or termination).casefold()
        tapped = bool(getattr(obj, "Threaded", False) or getattr(obj, "TappedHole", False))
        if tapped:
            kind = "tapped"
        elif "counterbore" in hole_cut_type:
            kind = "counterbore"
        elif "countersink" in hole_cut_type:
            kind = "countersink"
        else:
            kind = "simple"
        item: dict[str, Any] = {
            "diameter_mm": round(diameter, 9),
            "through": through,
            "count": sum(abs(candidate - diameter) < 1e-9 for candidate in diameters),
            "kind": kind,
            "confidence": 1.0 if type_id == "PartDesign::Hole" else 0.75,
        }
        if isinstance(depth, (int, float)) and depth >= 0 and not through:
            item["depth_mm"] = depth
        for key in (
            "counterbore_diameter_mm",
            "counterbore_depth_mm",
            "countersink_angle_deg",
            "thread_pitch_mm",
            "tapped_depth_mm",
        ):
            value = properties.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                item[key] = value
        if kind == "countersink" and "counterbore_diameter_mm" in item:
            item["countersink_diameter_mm"] = item.pop("counterbore_diameter_mm")
        for key, native_names in {
            "thread_designation": ("ThreadSize", "ThreadDesignation", "ThreadType"),
            "thread_class": ("ThreadClass", "ThreadFit"),
        }.items():
            value = next(
                (getattr(obj, name, None) for name in native_names if getattr(obj, name, None)),
                None,
            )
            if value is not None:
                item[key] = str(value)
        result.append(item)
    return result


def placement_metadata(obj: Any) -> dict[str, list[float]] | None:
    """Serialize a FreeCAD placement for non-rendered joint markers (metres)."""
    if "Placement" not in getattr(obj, "PropertiesList", ()):
        return None
    try:
        placement = obj.Placement
        base = placement.Base
        rotation = placement.Rotation.Q
        return {
            "position": [float(base.x) / 1000.0, float(base.y) / 1000.0, float(base.z) / 1000.0],
            "rotation": [
                float(rotation[0]),
                float(rotation[1]),
                float(rotation[2]),
                float(rotation[3]),
            ],
        }
    except Exception as exc:
        warn_once("placement-metadata", "Failed to serialize FreeCAD placement metadata: %s", exc)
        return None


def placement_matrix(obj: Any) -> Any:
    """Return a glTF-space local transform (FreeCAD millimetres to metres)."""
    if "Placement" not in getattr(obj, "PropertiesList", ()):
        return np.eye(4, dtype=np.float64)


    try:
        matrix = obj.Placement.toMatrix()
        return np.asarray(
            [
                [matrix.A11, matrix.A12, matrix.A13, matrix.A14 / 1000.0],
                [matrix.A21, matrix.A22, matrix.A23, matrix.A24 / 1000.0],
                [matrix.A31, matrix.A32, matrix.A33, matrix.A34 / 1000.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
    except Exception as exc:
        warn_once(
            "placement-matrix", "Failed to read FreeCAD placement matrix; using identity: %s", exc
        )
        return np.eye(4, dtype=np.float64)


def _reliable_group_placement(obj: Any) -> bool:
    """Avoid inferring world transforms through unknown or scaled containers."""
    type_id = str(getattr(obj, "TypeId", ""))
    if type_id not in {"App::Part", "App::DocumentObjectGroup", "App::DocumentObjectGroupExtension"}:
        return False
    try:
        properties = set(getattr(obj, "PropertiesList", ()) or ())
        if "Placement" in properties:
            matrix = obj.Placement.toMatrix()
            if not all(math.isfinite(float(getattr(matrix, f"A{row}{column}")))
                       for row in range(1, 5) for column in range(1, 5)):
                return False
        elif type_id == "App::Part":
            return False
        if "Scale" in properties and float(obj.Scale) != 1.0:
            return False
        if "ScaleVector" in properties:
            if any(float(getattr(obj.ScaleVector, axis)) != 1.0 for axis in ("x", "y", "z")):
                return False
    except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
        return False
    return True


def local_shape(shape: Any) -> Any:
    """Remove occurrence placement so one mesh can be instanced by scene transforms."""
    result = shape.copy()
    result.Placement = FreeCAD.Placement()
    return result


def world_center_of_gravity_mm(shape: Any, world_matrix: Any) -> Any:
    """Transform an exact local shape centroid into assembly world coordinates."""
    local = local_shape(shape)
    center = shape_center_of_mass_mm(local)
    if center is None:
        return None
    point = np.asarray([center[0] / 1000.0, center[1] / 1000.0, center[2] / 1000.0, 1.0])
    transformed = (world_matrix @ point)[:3] * 1000.0
    return transformed if np.isfinite(transformed).all() else None


def read_fcstd_shape_appearances(fcstd_path: Path) -> dict[str, list[tuple[list[int], float]]]:
    """Read saved GUI Shape Appearance colors without starting the GUI.

    In an FCStd, a Body's `ViewObject.ShapeColor` can remain the default gray
    while FreeCAD displays its blue `ShapeAppearance` diffuse material. The
    latter is stored in GuiDocument.xml and an associated MaterialList binary.
    """
    appearances: dict[str, list[tuple[list[int], float]]] = {}
    try:
        with zipfile.ZipFile(fcstd_path) as archive:
            gui_document = ElementTree.fromstring(archive.read("GuiDocument.xml"))
            for provider in gui_document.iter("ViewProvider"):
                object_name = provider.get("name")
                material = provider.find(".//Property[@name='ShapeAppearance']/MaterialList")
                material_file = material.get("file") if material is not None else None
                if not object_name or not material_file:
                    continue
                raw = archive.read(material_file)
                # uint32 material count, then ambient RGBA (stored A,B,G,R),
                # then diffuse RGBA (stored A,B,G,R). Opacity belongs to the
                # diffuse color. Modern FreeCAD materials commonly store zero
                # in the ambient alpha byte even when the face is fully opaque.
                material_count = int.from_bytes(raw[:4], "little") if len(raw) >= 4 else 0
                if material_count < 1 or len(raw) < 4 + material_count * 8:
                    continue
                appearances[object_name] = []
                for index in range(material_count):
                    offset = 4 + index * 8
                    diffuse_alpha, blue, green, red = raw[offset + 4 : offset + 8]
                    appearances[object_name].append(([red, green, blue], diffuse_alpha / 255.0))
    except (KeyError, OSError, ValueError, ElementTree.ParseError, zipfile.BadZipFile):
        # ShapeColor remains a safe fallback for documents with no GUI data.
        pass
    return appearances


def color_and_alpha(
    obj: Any, shape_appearances: dict[str, list[tuple[list[int], float]]]
) -> tuple[list[int], float]:
    saved_appearance = shape_appearances.get(getattr(obj, "Name", ""))
    if saved_appearance is not None:
        return saved_appearance[0]
    view = getattr(obj, "ViewObject", None)
    color = getattr(view, "ShapeColor", (0.70, 0.70, 0.70))
    transparency = float(getattr(view, "Transparency", 0.0))
    return [max(0, min(255, round(float(value) * 255))) for value in color[:3]], max(
        0.0, min(1.0, 1.0 - transparency / 100.0)
    )


def face_appearances(
    obj: Any,
    shape_appearances: dict[str, list[tuple[list[int], float]]],
) -> list[tuple[list[int], float]]:
    """Return one saved material per BRep face when FreeCAD persisted that detail."""
    appearances = shape_appearances.get(getattr(obj, "Name", ""), [])
    face_count = len(getattr(getattr(obj, "Shape", None), "Faces", []))
    return appearances if face_count > 1 and len(appearances) == face_count else []


def mesh_for_shape(
    shape: Any, linear_deflection: float, angular_deflection: float
) -> tuple[Any, Any] | None:
    mesh = MeshPart.meshFromShape(
        Shape=local_shape(shape),
        LinearDeflection=linear_deflection,
        AngularDeflection=angular_deflection,
        Relative=False,
    )
    topology = mesh.Topology
    # FreeCAD shapes use millimetres; glTF uses metres by convention.
    vertices = (
        np.asarray([[vertex.x, vertex.y, vertex.z] for vertex in topology[0]], dtype=np.float64)
        / 1000.0
    )
    # Mesh.Topology faces reference the vertices tuple using Python/FreeCAD's
    # zero-based indexing. Subtracting one makes index 0 become -1, which
    # creates the long, crossed triangles seen in a corrupted export.
    faces = np.asarray([list(face) for face in topology[1] if len(face) == 3], dtype=np.int64)
    if len(faces) and (faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError(
            f"FreeCAD tessellation returned invalid face indices "
            f"({faces.min()}..{faces.max()} for {len(vertices)} vertices)."
        )
    return (vertices, faces) if len(vertices) and len(faces) else None


def mesh_for_native_mesh(mesh: Any) -> tuple[Any, Any] | None:
    """Read an existing FreeCAD mesh without B-Rep retessellation."""
    topology = mesh.Topology
    vertices = (
        np.asarray([[vertex.x, vertex.y, vertex.z] for vertex in topology[0]], dtype=np.float64)
        / 1000.0
    )
    faces = np.asarray([list(face) for face in topology[1] if len(face) == 3], dtype=np.int64)
    if len(faces) and (faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError(
            f"FreeCAD mesh contains invalid face indices "
            f"({faces.min()}..{faces.max()} for {len(vertices)} vertices)."
        )
    return (vertices, faces) if len(vertices) and len(faces) else None


def _quantity_value(value: Any, default: float) -> float:
    try:
        return float(getattr(value, "Value", value))
    except (TypeError, ValueError):
        return default


def render_techdraw_page(page: Any, output: Path, dpi: int = 144) -> dict[str, Any] | None:
    """Render a TechDraw page headlessly from its projected edge geometry."""
    width_mm = max(1.0, _quantity_value(getattr(page, "PageWidth", None), 297.0))
    height_mm = max(1.0, _quantity_value(getattr(page, "PageHeight", None), 210.0))
    pixels_per_mm = dpi / 25.4
    image = Image.new(
        "RGB",
        (max(1, round(width_mm * pixels_per_mm)), max(1, round(height_mm * pixels_per_mm))),
        "white",
    )
    drawing = ImageDraw.Draw(image)
    border = max(1, round(0.25 * pixels_per_mm))
    drawing.rectangle(
        (border, border, image.width - border - 1, image.height - border - 1),
        outline=(150, 150, 150),
        width=border,
    )
    rendered_edges = 0

    def draw_edges(view: Any, method_name: str, color: tuple[int, int, int], width: int) -> None:
        nonlocal rendered_edges
        method = getattr(view, method_name, None)
        if not callable(method):
            return
        scale = _quantity_value(getattr(view, "Scale", None), 1.0)
        x_offset = _quantity_value(getattr(view, "X", None), width_mm / 2)
        y_offset = _quantity_value(getattr(view, "Y", None), height_mm / 2)
        rotation = np.deg2rad(_quantity_value(getattr(view, "Rotation", None), 0.0))
        cosine, sine = float(np.cos(rotation)), float(np.sin(rotation))
        for edge in method():
            try:
                points = edge.discretize(Deflection=0.15)
            except Exception:  # noqa: BLE001 - projected FreeCAD curves vary by view type
                points = [vertex.Point for vertex in getattr(edge, "Vertexes", [])]
            pixels = []
            for point in points:
                local_x = float(point.x) * scale
                local_y = float(point.y) * scale
                page_x = x_offset + local_x * cosine - local_y * sine
                page_y = y_offset + local_x * sine + local_y * cosine
                pixels.append((page_x * pixels_per_mm, (height_mm - page_y) * pixels_per_mm))
            if len(pixels) >= 2:
                drawing.line(pixels, fill=color, width=width, joint="curve")
                rendered_edges += 1

    for view in getattr(page, "Views", []) or []:
        draw_edges(view, "getHiddenEdges", (160, 160, 160), max(1, round(pixels_per_mm * 0.15)))
        draw_edges(view, "getVisibleEdges", (15, 15, 15), max(1, round(pixels_per_mm * 0.25)))
    if rendered_edges == 0:
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True, dpi=(dpi, dpi))
    return {
        "file": f"drawings/{output.name}",
        "displayName": str(getattr(page, "Label", "") or getattr(page, "Name", "Drawing")),
        "widthMm": round(width_mm, 3),
        "heightMm": round(height_mm, 3),
        "edgeCount": rendered_edges,
    }


def render_techdraw_pages(document: Any, output_directory: Path) -> list[dict[str, Any]]:
    pages = [
        obj
        for obj in getattr(document, "Objects", [])
        if getattr(obj, "TypeId", "") == "TechDraw::DrawPage"
    ]
    rendered = []
    for index, page in enumerate(pages, start=1):
        label = re.sub(
            r"[^A-Za-z0-9._-]+",
            "-",
            str(getattr(page, "Label", "") or getattr(page, "Name", "Drawing")),
        ).strip("-._")[:60]
        path = output_directory / f"drawing-{index:04d}-{label or 'drawing'}.png"
        result = render_techdraw_page(page, path)
        if result is not None:
            rendered.append(result)
    return rendered




def topological_edge_segments(shape: Any, linear_deflection: float) -> list[float]:
    """Tessellate native B-Rep edges for FreeCAD-faithful shaded-edge display."""
    segments: list[float] = []
    local = local_shape(shape)
    for edge in getattr(local, "Edges", []):
        try:
            points = list(edge.discretize(Deflection=max(float(linear_deflection), 0.01)))
        except Exception as exc:
            warn_once(
                "edge-discretization-fallback",
                "FreeCAD edge discretization failed for at least one edge; falling back to vertices: %s",
                exc,
            )
            points = [vertex.Point for vertex in getattr(edge, "Vertexes", [])]
        for start, end in zip(points, points[1:]):
            segments.extend(
                (
                    float(start.x) / 1000.0,
                    float(start.y) / 1000.0,
                    float(start.z) / 1000.0,
                    float(end.x) / 1000.0,
                    float(end.y) / 1000.0,
                    float(end.z) / 1000.0,
                )
            )
    return segments


def mesh_for_colored_faces(
    shape: Any,
    appearances: list[tuple[list[int], float]],
    linear_deflection: float,
    angular_deflection: float,
) -> tuple[Any, Any, Any] | None:
    """Tessellate BRep faces separately so their native materials survive in glTF."""
    vertices_parts = []
    faces_parts = []
    vertex_colors = []
    vertex_offset = 0
    for face, (color, alpha) in zip(local_shape(shape).Faces, appearances, strict=True):
        mesh_data = mesh_for_shape(face, linear_deflection, angular_deflection)
        if mesh_data is None:
            continue
        vertices, faces = mesh_data
        vertices_parts.append(vertices)
        faces_parts.append(faces + vertex_offset)
        vertex_colors.extend([[*color, round(alpha * 255)]] * len(vertices))
        vertex_offset += len(vertices)
    if not faces_parts:
        return None
    return (
        np.concatenate(vertices_parts),
        np.concatenate(faces_parts),
        np.asarray(vertex_colors, dtype=np.uint8),
    )


def mesh_for_edges_and_points(shape: Any, linear_deflection: float) -> tuple[Any, Any] | None:
    """Create thin renderable geometry for shapes that have no tessellated faces."""
    local = local_shape(shape)
    radius = max(float(linear_deflection) / 2000.0, 0.00005)
    pieces = []
    represented_vertices: set[tuple[float, float, float]] = set()
    for edge in getattr(local, "Edges", []):
        try:
            samples = edge.discretize(Deflection=max(float(linear_deflection), 0.05))
        except Exception:  # noqa: BLE001 - FreeCAD curve implementations vary
            samples = [vertex.Point for vertex in getattr(edge, "Vertexes", [])]
        points = (
            np.asarray([[point.x, point.y, point.z] for point in samples], dtype=np.float64)
            / 1000.0
        )
        for point in points:
            represented_vertices.add(tuple(np.round(point, 12)))
        for start, end in itertools.pairwise(points):
            vector = end - start
            length = float(np.linalg.norm(vector))
            if length <= 1e-12:
                continue
            cylinder = trimesh.creation.cylinder(radius=radius, height=length, sections=8)
            transform = trimesh.geometry.align_vectors([0, 0, 1], vector / length)
            transform[:3, 3] = (start + end) / 2
            cylinder.apply_transform(transform)
            pieces.append(cylinder)
    point_radius = radius * 1.6
    for vertex in getattr(local, "Vertexes", []):
        point = (
            np.asarray([vertex.Point.x, vertex.Point.y, vertex.Point.z], dtype=np.float64) / 1000.0
        )
        if tuple(np.round(point, 12)) in represented_vertices:
            continue
        sphere = trimesh.creation.icosphere(subdivisions=1, radius=point_radius)
        sphere.apply_translation(point)
        pieces.append(sphere)
    if not pieces:
        return None
    combined = trimesh.util.concatenate(pieces)
    return np.asarray(combined.vertices), np.asarray(combined.faces)


class ModelryxExporter:
    def __init__(
        self,
        document: Any,
        linear_deflection: float,
        angular_deflection: float,
        shape_appearances: dict[str, list[tuple[list[int], float]]],
        max_nodes: int = 100_000,
        max_triangles: int = 5_000_000,
        source_document: dict[str, Any] | None = None,
        dependency_report: dict[str, Any] | None = None,
        metadata_only: bool = False,
    ):
        self.document = document
        self.linear_deflection = linear_deflection
        self.angular_deflection = angular_deflection
        self.shape_appearances = shape_appearances
        self.source_document = source_document or {}
        source_path = str(getattr(document, "FileName", "") or "")
        self.saved_visibility = read_saved_visibility(Path(source_path)) if source_path else {}
        self.dependency_report = dependency_report
        source_file = str(getattr(document, "FileName", "") or "")
        self.source_root = Path(source_file).resolve().parent if source_file else None
        if self.source_root is not None and dependency_report:
            logical_root = str(dependency_report.get("root_logical_path") or "").replace("\\", "/")
            parts = [part for part in logical_root.split("/") if part]
            for _ in parts[:-1]:
                self.source_root = self.source_root.parent
        self.max_nodes = max_nodes
        self.max_triangles = max_triangles
        self.metadata_only = metadata_only
        if not metadata_only and (trimesh is None or Image is None or ImageDraw is None):
            raise RuntimeError("Full FreeCAD conversion requires the trimesh and Pillow packages.")
        self.scene = None if metadata_only else trimesh.Scene()
        self.bom_items: list[dict[str, Any]] = []
        self.link_container_occurrences: dict[str, list[dict[str, str]]] = {}
        self.uncertain_array_occurrence_ids: set[str] = set()
        self.link_parent_world_frames: dict[str, list[list[float]]] = {}
        self.triangle_count = 0
        self.mesh_count = 0
        self.warnings: list[dict[str, str]] = []
        self.mesh_cache: dict[str, str] = {}
        self.link_tessellation_cache: dict[str, list[tuple[Any, Any, Any, Any]]] = {}
        self.tessellation_cache_hits = 0
        self.link_edge_cache: dict[str, list[tuple[Any, list[float]]]] = {}
        self.edge_cache_hits = 0
        self.external_document_hashes: dict[str, str | None] = {}
        self.tessellation_seconds = 0.0
        self.edge_discretization_seconds = 0.0
        self.node_count = 0
        # FreeCAD's OutList is a dependency graph, not a strict ownership tree.
        # A feature can therefore be reachable through many later features.  A
        # path-local ancestor guard prevents cycles but still expands every path
        # through a dense graph, which can grow exponentially.  Object names are
        # unique within a document, while genuine repeated assembly occurrences
        # are represented by distinct App::Link objects with distinct names.
        self.visited_object_names: set[str] = set()
        self.effective_shapes: list[dict[str, Any]] = []
        self.hidden_component_occurrence_ids: set[str] = set()
        self.suppressed_component_occurrence_ids: set[str] = set()
        self.raw_metadata_document: dict[str, Any] | None = None
        self.part_metadata_document: dict[str, Any] | None = None
        self.exact_bound_points: list[Any] = []
        self.construction_only = False
        self.section_diagnostics: list[dict[str, Any]] = []
        self.document_health: dict[str, Any] = {
            "recompute_succeeded": True,
            "objects_checked": 0,
            "issues": [],
        }
        self.provider_durations_ms: dict[str, int] = {}
        self.started_at = time.monotonic()

    def linked_document_identity(self, document: Any) -> str:
        """Use a package-relative document identity without exposing machine paths."""
        filename = str(getattr(document, "FileName", "") or "")
        if filename:
            path = Path(filename).resolve()
            if self.source_root is not None:
                try:
                    return path.relative_to(self.source_root).as_posix()
                except ValueError:
                    pass
            path_key = os.path.normcase(str(path))
            self.record_section_issue(
                "assembly",
                "EXTERNAL_DOCUMENT_IDENTITY_LOCAL",
                "An external document is outside the source root; its identity depends on its local path.",
            )
            return f"external/{hashlib.sha256(path_key.encode('utf-8')).hexdigest()}/{path.name}"
        return str(getattr(document, "Name", "") or document_identity(self.document))

    def object_visible(self, obj: Any) -> bool:
        if getattr(obj, "Document", None) is self.document:
            name = str(getattr(obj, "Name", "") or "")
            if name in self.saved_visibility:
                return self.saved_visibility[name]
        return visible(obj)

    def record_section_issue(
        self,
        section: str,
        code: str,
        message: str,
        *,
        object_name: str | None = None,
    ) -> None:
        """Record a stable, machine-readable partial-extraction diagnostic."""
        diagnostic = {
            "section": section,
            "code": code,
            "message": message,
            "severity": "warning",
        }
        if object_name:
            diagnostic["object_name"] = object_name
        if diagnostic not in self.section_diagnostics:
            self.section_diagnostics.append(diagnostic)
        warning = {"code": code, "message": message}
        if warning not in self.warnings:
            self.warnings.append(warning)

    def inspect_document_health(
        self,
        *,
        recompute_succeeded: bool,
        recompute_error: str | None = None,
        recompute_diagnostics: list[dict[str, str]] | None = None,
    ) -> None:
        """Inspect document health and merge provider diagnostics into extraction evidence."""
        result = DocumentInspector(is_feature_history_type).inspect(
            self.document,
            recompute_succeeded=recompute_succeeded,
            recompute_error=recompute_error,
        )
        self.provider_durations_ms["documentHealth"] = round(result.duration_ms)
        self.document_health = result.evidence
        for item in result.diagnostics + (recompute_diagnostics or []):
            self.record_section_issue(
                item["section"], item["code"], item["message"],
                object_name=item.get("object_name"),
            )
        self.document_health["issues"] = sorted(
            (
                dict(item)
                for item in self.section_diagnostics
                if item.get("section") in {"geometry", "features", "assembly", "parameters"}
            ),
            key=lambda item: (str(item.get("code", "")), str(item.get("object_name", ""))),
        )


    def export(self, glb_path: Path, preview_path: Path) -> None:
        if trimesh is None:
            raise RuntimeError("Full FreeCAD conversion requires the trimesh Python package.")
        roots, root_id = self.traverse_document()
        empty_scene = not self.scene.geometry
        if empty_scene:
            self.construction_only = not (
                self.hidden_component_occurrence_ids or self.suppressed_component_occurrence_ids
            )
            self.warnings.append(
                {
                    "code": "CONSTRUCTION_ONLY_DOCUMENT" if self.construction_only else "NO_VISIBLE_GEOMETRY",
                    "message": (
                        "The FreeCAD document contains no visible renderable geometry; "
                        "its hierarchy and properties were preserved."
                    ),
                }
            )
        glb_path.write_bytes(
            empty_glb() if empty_scene else self.scene.export(file_type="glb")
        )
        self.build_metadata(roots)
        preview_path.write_text(
            json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "rootOccurrenceId": root_id,
                    "items": [
                        self.preview_item(item)
                        for item in self.bom_items
                        if self.is_navigation_item(item)
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def extract_metadata(self) -> dict[str, Any]:
        """Run the worker's traversal and normalizer without producing preview files."""
        roots, _ = self.traverse_document()
        self.construction_only = not (
            self.effective_shapes or self.hidden_component_occurrence_ids
            or self.suppressed_component_occurrence_ids
        )
        self.build_metadata(roots)
        assert self.part_metadata_document is not None
        return self.part_metadata_document

    def build_metadata(self, roots: list[Any]) -> None:
        self.raw_metadata_document = self.raw_metadata(roots)
        self.part_metadata_document = PartMetadataNormalizer(
            worker_version=CONVERTER_VERSION
        ).normalize(self.raw_metadata_document)

    def traverse_document(self) -> tuple[list[Any], str]:
        roots = self.root_objects()
        root_id = stable_id(document_identity(self.document), "root")
        root_part_id = stable_id("part", document_identity(self.document), "root")
        # FreeCAD often derives Document.Name/Label from the temporary worker
        # filename.  The uploaded FCStd filename is the stable user-facing root
        # label and matches the label shown by FreeCAD for ordinary documents.
        source_filename = Path(str(getattr(self.document, "FileName", "") or "")).stem
        root_name = source_filename or str(
            getattr(self.document, "Label", "") or getattr(self.document, "Name", "") or "Model"
        )
        root_metadata = {
            "schemaVersion": SCHEMA_VERSION,
            "nodeType": "assembly",
            "cadType": "Document",
            "partId": root_part_id,
            "occurrenceId": root_id,
            "parentOccurrenceId": None,
            "renderOccurrenceId": root_id,
            "source": {
                "system": "FreeCAD",
                "objectName": str(getattr(self.document, "Name", "") or "Document"),
                "objectLabel": root_name,
                "typeId": "App::Document",
            },
            "display": {"visible": True},
        }
        if self.scene is not None:
            self.scene.graph.update(
                frame_to="modelryx-root",
                frame_from="world",
                metadata={"modelryx": root_metadata},
            )
        self.bom_items.append({**root_metadata, "name": root_name, "properties": {}})
        for obj in roots:
            self.visit(
                obj,
                "modelryx-root",
                root_id,
                document_identity(self.document),
                world_matrix=np.eye(4),
            )
        return roots, root_id

    @staticmethod
    def is_navigation_item(item: dict[str, Any]) -> bool:
        """Expose the FreeCAD model tree while omitting coordinate-system internals."""
        if re.fullmatch(r"origin\d*", str(item.get("name", "")).strip(), re.IGNORECASE):
            return False
        return item.get("nodeType") != "reference"

    @staticmethod
    def preview_item(item: dict[str, Any]) -> dict[str, Any]:
        """Keep only fields consumed by tree navigation and 3D interaction."""
        fields = (
            "schemaVersion",
            "nodeType",
            "cadType",
            "partId",
            "occurrenceId",
            "parentOccurrenceId",
            "renderOccurrenceId",
            "name",
            "display",
            "placement",
        )
        result = {field: item[field] for field in fields if field in item}
        properties = item.get("properties", {})
        if isinstance(properties, dict):
            keyed = {str(key).casefold(): value for key, value in properties.items()}
            configuration = next(
                (
                    keyed[key]
                    for key in ("configuration", "variant", "config", "modelstate")
                    if keyed.get(key) not in (None, "")
                ),
                None,
            )
            if configuration is not None:
                result["configuration"] = configuration
        source = item.get("source", {})
        if isinstance(source, dict) and source.get("linkedDocument"):
            result["dependency"] = {
                "status": "resolved",
                "document": str(source["linkedDocument"]),
                "object": str(source.get("linkedObjectName") or ""),
                "external": bool(source.get("externalDocument", False)),
            }
        return result

    def raw_metadata(self, roots: list[Any]) -> dict[str, Any]:
        """Build the FreeCAD-owned extraction record consumed by the normalizer."""
        model = assembly_model(self.document)
        module_issue = workbench_module_issue(model)
        workbench_requirements = (
            [
                {
                    "assembly_model": model,
                    "required_modules": list(WORKBENCH_MODULES[model]),
                    "missing_modules": (
                        module_issue.get("missing_modules", []) if module_issue else []
                    ),
                }
            ]
            if model in WORKBENCH_MODULES
            else []
        )
        points = np.asarray(self.exact_bound_points, dtype=np.float64)
        bounds = (
            np.asarray([points.min(axis=0), points.max(axis=0)])
            if len(points)
            else np.zeros((2, 3), dtype=np.float64)
        )
        dimensions = (bounds[1] - bounds[0]) * 1000.0
        is_assembly = model in {
            "BUILTIN_ASSEMBLY",
            "ASSEMBLY3",
            "ASSEMBLY4",
            "A2PLUS",
            "APP_LINK",
        } or len(roots) > 1 or (
            len(self.effective_shapes) > 1 and any(is_group(obj) for obj in roots)
        )
        topology_fields = ("solid_count", "shell_count", "face_count", "edge_count", "vertex_count")
        material_result = MaterialExtractor().extract(
            self.effective_shapes, self.aggregate_weighted_center
        )
        self.provider_durations_ms["materials"] = round(material_result.duration_ms)
        component_occurrence_ids = {
            str(shape["occurrence_id"]) for shape in self.effective_shapes
        } | self.hidden_component_occurrence_ids
        array_occurrence_ids = {
            occurrence["occurrence_id"]
            for containers in self.link_container_occurrences.values()
            for occurrence in containers
        } | self.uncertain_array_occurrence_ids
        unresolved_array_ids = {
            str(item.get("occurrenceId", ""))
            for item in self.bom_items
            if str(item.get("occurrenceId", "")) in array_occurrence_ids
            and item.get("suppressed") is not True
        }
        component_items = [
            item
            for item in self.bom_items
            if str(item.get("occurrenceId", "")) in component_occurrence_ids
            and str(item.get("occurrenceId", "")) not in array_occurrence_ids
        ]
        subassembly_count = sum(
            item.get("nodeType") == "assembly" and item.get("parentOccurrenceId") is not None
            for item in self.bom_items
        )
        occurrences = sorted(
            (self.metadata_occurrence(item) for item in self.bom_items),
            key=lambda item: (
                str(item.get("parentOccurrenceId") or ""),
                str(item.get("occurrenceId") or ""),
            ),
        )
        dependencies = sorted(
            {
                (
                    str(dependency.get("document", "")),
                    str(dependency.get("object", "")),
                    bool(dependency.get("external", False)),
                )
                for occurrence in occurrences
                for dependency in [occurrence.get("dependency")]
                if isinstance(dependency, dict) and dependency.get("document")
            }
        )
        dependency_records = [
            {
                "document": document,
                "object": object_name,
                "external": external,
                "status": "resolved",
            }
            for document, object_name, external in dependencies
        ]
        preprocessor_dependencies = dependency_report_evidence(self.dependency_report)
        dependency_keys = {
            (
                str(record.get("document", "")),
                str(record.get("object", "")),
                str(record.get("status", "")),
            )
            for record in dependency_records
        }
        for record in preprocessor_dependencies["records"]:
            key = (
                str(record.get("document", "")),
                str(record.get("object", "")),
                str(record.get("status", "")),
            )
            if key not in dependency_keys:
                dependency_records.append(record)
                dependency_keys.add(key)
        dependency_records.sort(
            key=lambda item: (
                str(item.get("document", "")).casefold(),
                str(item.get("object", "")).casefold(),
                str(item.get("status", "")).casefold(),
            )
        )
        dependency_status = (
            str(preprocessor_dependencies["status"])
            if preprocessor_dependencies["available"]
            else "partial"
            if dependency_records
            else "unavailable"
        )
        unique_bom_items = list(
            {
                str(item.get("source", {}).get("objectName", "")): item
                for item in self.bom_items
                if item.get("source", {}).get("objectName")
            }.values()
        )
        history_items = [
            item
            for item in unique_bom_items
            if is_feature_history_type(str(item.get("source", {}).get("typeId", "")))
        ]
        history_types = [str(item.get("source", {}).get("typeId", "")) for item in history_items]
        after_tip_names = {
            str(name)
            for body in self.document_health.get("features_after_body_tip", [])
            for name in body.get("features", [])
        }
        unresolved_profile_names = {
            str(issue.get("object_name"))
            for issue in self.section_diagnostics
            if issue.get("section") == "features"
            and issue.get("code") in {"MISSING_FEATURE_SUPPORT", "FEATURE_SUPPORT_INSPECTION_FAILED"}
            and issue.get("object_name")
        }
        invalid_body_names = self.document_health.get("bodies_with_invalid_tips", [])
        uncertain_history_names: set[str] = set()
        body_membership_unavailable = False
        for body_name in invalid_body_names:
            body = self.document.getObject(str(body_name))
            if body is None:
                body_membership_unavailable = True
                continue
            try:
                uncertain_history_names.update(
                    str(getattr(feature, "Name", "")) for feature in (getattr(body, "Group", ()) or ())
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                body_membership_unavailable = True
        recognized_features = []
        holes: list[dict[str, Any]] = []
        skipped_features = 0
        for item, type_id in zip(history_items, history_types, strict=True):
            source = item.get("source", {})
            object_name = str(source.get("objectName", ""))
            obj = self.document.getObject(object_name)
            proxy_type = str(item.get("cadType", "") or "")
            suppressed = (
                True if item.get("suppressed") is True
                else freecad_feature_suppressed(item.get("properties", {}))
            )
            if body_membership_unavailable or object_name in after_tip_names or object_name in uncertain_history_names:
                continue
            if object_name in unresolved_profile_names:
                skipped_features += 1
                continue
            try:
                geometry_types = (
                    profile_geometry_types(obj, include_construction=type_id != "PartDesign::Pocket")
                    if obj is not None
                    else ()
                )
                wire_count = profile_closed_wire_count(obj) if obj is not None else 0
                if (
                    type_id == "PartDesign::Pocket"
                    and suppressed is not True
                    and any(
                        value.casefold() in {"part::geomcircle", "part.circle"}
                        for value in geometry_types
                    )
                    and (len(geometry_types) != 1 or wire_count != 1)
                ):
                    self.record_section_issue(
                        "features",
                        "POCKET_PROFILE_TOPOLOGY_AMBIGUOUS",
                        f"Pocket '{obj.Name}' contains multiple or unresolved profile regions; individual holes cannot be determined.",
                        object_name=str(obj.Name),
                    )
                recognized_features.extend(
                    normalized_freecad_feature_names(
                        type_id,
                        geometry_types,
                        wire_count,
                        proxy_type=proxy_type,
                        suppressed=suppressed is True,
                    )
                )
                if obj is not None and suppressed is not True:
                    holes.extend(feature_holes(obj, type_id, item.get("properties", {})))
            except (AttributeError, RuntimeError, TypeError, ValueError) as error:
                skipped_features += 1
                object_name = str(source.get("objectName") or item.get("name") or "unknown")
                self.record_section_issue(
                    "features",
                    "FEATURE_EXTRACTION_FAILED",
                    f"Feature '{object_name}' could not be normalized: {error}",
                    object_name=object_name,
                )
        feature_summary = {} if invalid_body_names or unresolved_profile_names else {
            f"{name}_count": recognized_features.count(name) for name in FEATURE_NAMES
        }
        feature_items = []
        for item in unique_bom_items:
            source = item.get("source", {})
            type_id = str(source.get("typeId", "")) if isinstance(source, dict) else ""
            if type_id == "PartDesign::Body":
                normalized_type = "BODY"
            elif type_id == "Sketcher::SketchObject":
                normalized_type = "SKETCH"
            else:
                normalized_type = normalized_freecad_feature_name(
                    type_id, str(item.get("cadType", "") or "")
                )
            if not normalized_type and not is_feature_history_type(type_id):
                continue
            if not normalized_type:
                normalized_type = "UNKNOWN_FEATURE"
            if normalized_type not in {"BODY", "SKETCH"}:
                normalized_type = {
                    "revolution": "REVOLVE",
                    "extrusion": "EXTRUSION",
                }.get(normalized_type, normalized_type.upper())
            properties = {
                key: value
                for key, value in item.get("properties", {}).items()
                if value is None
                or isinstance(value, (bool, int, float, str))
                or (
                    isinstance(value, list)
                    and (
                        all(isinstance(entry, str) for entry in value)
                        or all(
                            isinstance(entry, (int, float)) and not isinstance(entry, bool)
                            for entry in value
                        )
                    )
                )
            }
            properties["sourceType"] = type_id
            if str(source.get("objectName", "")) in after_tip_names:
                properties["active_in_body"] = False
            suppressed = (
                True if item.get("suppressed") is True else freecad_feature_suppressed(properties)
            )
            if suppressed is not None:
                properties["suppressed"] = suppressed
            feature_items.append(
                {
                    "persistent_id": source.get("objectName") or item.get("sourceObjectId"),
                    "name": item.get("name"),
                    "type": normalized_type,
                    "properties": properties,
                }
            )
        warnings: list[str] = list(material_result.evidence["warnings"])
        if module_issue:
            warnings.append(str(module_issue["message"]))
        if self.construction_only:
            warnings.append("Document contains construction objects but no visible final geometry.")
        normalized_history_count = sum(
            type_id == "Sketcher::SketchObject"
            or normalized_freecad_feature_name(type_id, str(item.get("cadType", "") or ""))
            is not None
            for item, type_id in zip(history_items, history_types, strict=True)
        )
        if not history_types:
            warnings.append("Parametric feature history was not available from source.")
        elif normalized_history_count != len(history_types) or skipped_features:
            warnings.append("Some FreeCAD history objects could not be normalized as features.")
        feature_health_issues = any(
            item["section"] == "features" for item in self.section_diagnostics
        )
        feature_quality = (
            "unavailable"
            if not history_types
            else "complete"
            if normalized_history_count == len(history_types)
            and not skipped_features
            and not feature_health_issues
            else "partial"
        )
        drawing_result = TechDrawExtractor(
            custom_properties, stable_id, self.linked_document_identity, _quantity_value
        ).extract(self.document)
        self.provider_durations_ms["techdraw"] = round(drawing_result.duration_ms)
        drawings = drawing_result.records
        drawing_sheets = drawing_result.evidence["sheets"]
        title_block = drawing_result.evidence["title_block"]
        drawing_evidence = drawing_result.evidence["drawing"]
        skipped_drawings = drawing_result.evidence["skipped"]
        drawing_extraction_failed = drawing_result.evidence["failed"]
        for issue in drawing_result.diagnostics:
            self.record_section_issue(issue["section"], issue["code"], issue["message"])

        try:
            properties = {**title_block, **document_properties(self.document, roots)}
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            properties = dict(title_block)
            self.record_section_issue(
                "properties",
                "DOCUMENT_PROPERTIES_EXTRACTION_FAILED",
                f"Document properties could not be extracted: {error}",
            )
        parameter_result = ParameterExtractor(
            lambda document: native_parameters(document, _numeric_parameter_value)
        ).extract(self.document)
        self.provider_durations_ms["parameters"] = round(parameter_result.duration_ms)
        parameters = parameter_result.records
        spreadsheets = parameter_result.evidence["spreadsheets"]
        parameter_graph = parameter_result.evidence["graph"]
        external_loader = BoundedParameterLoader(
            lambda document: native_parameters(document, _numeric_parameter_value)
        )
        parameter_graph["external_references"] = external_parameter_evidence(
            self.document, parameter_graph,
            external_loader,
            self.linked_document_identity,
        )
        add_external_graph_edges(parameter_graph, self.linked_document_identity(self.document))
        parameter_graph["external_upstream"] = external_upstream_graph(
            self.document, parameter_graph["external_references"],
            external_loader,
            self.linked_document_identity,
        )
        parameter_graph["external_documents_inspected"] = len(external_loader.cache)
        parameter_graph["external_document_limit_reached"] = external_loader.limit_reached
        if external_loader.limit_reached:
            self.record_section_issue(
                "parameters", "PARAMETER_EXTERNAL_DOCUMENT_LIMIT",
                "External parameter extraction exceeded its document limit; dependency evidence is partial.",
            )
        parameter_graph["cross_document_cycles"] = cross_document_cycles(
            parameter_graph, self.linked_document_identity(self.document)
        )
        if parameter_graph["cross_document_cycles"]:
            self.record_section_issue(
                "parameters", "PARAMETER_CROSS_DOCUMENT_CYCLE",
                f"Detected {len(parameter_graph['cross_document_cycles'])} cross-document parameter cycles.",
            )
        for issue in parameter_result.diagnostics:
            self.record_section_issue(issue["section"], issue["code"], issue["message"])

        try:
            configurations, skipped_configurations = native_configurations(self.document)
            if skipped_configurations:
                self.record_section_issue(
                    "configurations",
                    "CONFIGURATION_EXTRACTION_FAILED",
                    f"{skipped_configurations} FreeCAD configuration selectors could not be extracted.",
                )
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            configurations, skipped_configurations = {
                "active": None,
                "active_values": [],
                "variants": [],
                "selectors": [],
            }, 1
            self.record_section_issue(
                "configurations",
                "CONFIGURATION_EXTRACTION_FAILED",
                f"Configuration semantics could not be extracted: {error}",
            )
        sketch_result = SketchExtractor(
            _sketch_vector, _numeric_parameter_value,
            lambda value: _sketch_references(value, self.linked_document_identity), _placement_value
        ).extract(self.document)
        self.provider_durations_ms["sketches"] = round(sketch_result.duration_ms)
        sketches = sketch_result.records
        skipped_sketches = sketch_result.evidence["skipped"]
        for issue in sketch_result.diagnostics:
            self.record_section_issue(
                issue["section"], issue["code"], issue["message"],
                object_name=issue.get("object_name"),
            )

        assembly_result = AssemblyExtractor(
            lambda value: object_reference(value, self.linked_document_identity),
            _placement_value, _numeric_parameter_value,
            self.linked_document_identity, self.max_nodes, _link_array_element_transform,
        ).extract(self.document)
        self.provider_durations_ms["assembly"] = round(assembly_result.duration_ms)
        assembly_joints = assembly_result.records
        skipped_assembly_joints = assembly_result.evidence["skipped"]
        array_evidence = assembly_result.evidence["arrays"]
        remaining_world_transforms = max(0, self.max_nodes)
        container_items = {str(item.get("occurrenceId", "")): item for item in self.bom_items}
        array_element_occurrences: list[dict[str, Any]] = []
        for array in array_evidence:
            containers = self.link_container_occurrences.get(str(array.get("name", "")), [])
            if not containers:
                try:
                    array_object = self.document.getObject(str(array.get("name", "")))
                    state, _source = native_suppression(array_object)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    state = None
                if state is not True:
                    unresolved_array_ids.add(f"unvisited:{array.get('name', '')}")
            if containers:
                array["container_occurrences"] = sorted(
                    containers, key=lambda item: item["occurrence_id"]
                )
                missing_world = 0
                omitted_world = 0
                for element in array.get("elements", []):
                    element["world_transforms"] = []
                    selected = array["container_occurrences"][:remaining_world_transforms]
                    omitted_world += len(array["container_occurrences"]) - len(selected)
                    remaining_world_transforms -= len(selected)
                    for container in selected:
                        occurrence_id = container["occurrence_id"]
                        parent = self.link_parent_world_frames.get(occurrence_id)
                        transform = compose_world_transform(
                            parent, element.get("transform_to_array_parent")
                        ) if parent is not None else None
                        element["world_transforms"].append(
                            {"container_occurrence_id": occurrence_id, "transform": transform}
                        )
                        if transform is None:
                            missing_world += 1
                if missing_world:
                    self.record_section_issue(
                        "assembly", "LINK_ARRAY_WORLD_TRANSFORM_UNAVAILABLE",
                        f"World transforms for {missing_world} link-array elements could not be resolved.",
                        object_name=str(array.get("name", "")),
                    )
                if omitted_world:
                    array["world_transforms_truncated"] = True
                    self.record_section_issue(
                        "assembly", "LINK_ARRAY_WORLD_EVIDENCE_TRUNCATED",
                        f"World-transform evidence for {omitted_world} link-array elements exceeded the extraction limit.",
                        object_name=str(array.get("name", "")),
                    )
                native_containers = [
                    container_items[container["occurrence_id"]]
                    for container in array["container_occurrences"]
                    if container["occurrence_id"] in container_items
                ]
                owned_uniform = array.get("definition_evidence", {}).get("status") == "owned_uniform"
                owned_scope = {
                    array.get("name"), array.get("target"),
                    (array.get("copy_on_change_source") or {}).get("object"),
                }
                owned_selectors: dict[str, dict[str, str | None]] = {}
                for selector in configurations["selectors"]:
                    name = str(selector.get("object") or "")
                    key = str(selector.get("property") or "").casefold()
                    if name in {array.get("name"), array.get("target")} and key:
                        owned_selectors.setdefault(name, {})[key] = selector.get("active")
                owner_selectors = owned_selectors.get(str(array.get("name") or ""), {})
                target_selectors = owned_selectors.get(str(array.get("target") or ""), {})
                selectors_supported = not skipped_configurations and (
                    not configurations["selectors"] or (
                        owned_uniform
                        and all(selector.get("object") in owned_scope for selector in configurations["selectors"])
                        and bool(owner_selectors) and owner_selectors == target_selectors
                        and all(value is not None for value in owner_selectors.values())
                    )
                )
                eligible = (
                    model == "APP_LINK" and selectors_supported
                    and not any(
                        issue["code"] == "ASSEMBLY_SUPPRESSION_UNVERIFIED"
                        for issue in self.section_diagnostics
                    )
                    and len(native_containers) == len(containers)
                )
                records = verified_array_occurrences(array, native_containers, stable_id) if eligible else None
                if records is not None:
                    array["occurrence_resolution"] = "verified_snapshot_index"
                    array_element_occurrences.extend(records)
                    component_items.extend(item for item in records if not item["suppressed"])
                    unresolved_array_ids.difference_update(
                        container["occurrenceId"] for container in native_containers
                    )
                elif eligible:
                    records = verified_array_occurrences(
                        array, native_containers, stable_id, allow_partial=True
                    )
                    if records is not None:
                        array["occurrence_resolution"] = "partial_snapshot_index"
                        array_element_occurrences.extend(records)
        for issue in assembly_result.diagnostics:
            self.record_section_issue(
                issue["section"], issue["code"], issue["message"],
                object_name=issue.get("object_name"),
            )
        if unresolved_array_ids:
            self.record_section_issue(
                "assembly", "LINK_ARRAY_BOM_QUANTITY_UNAVAILABLE",
                f"BOM quantities for {len(unresolved_array_ids)} link-array occurrences or unresolved arrays are unavailable; array containers were omitted from BOM rows.",
            )
        occurrences.extend(self.metadata_occurrence(item) for item in array_element_occurrences)
        occurrences.sort(key=lambda item: (str(item.get("parentOccurrenceId") or ""), str(item.get("occurrenceId") or "")))
        unresolved_joint_occurrences = map_joint_occurrences(assembly_joints, occurrences)
        if unresolved_joint_occurrences:
            self.record_section_issue(
                "assembly", "ASSEMBLY_JOINT_OCCURRENCE_UNRESOLVED",
                f"{unresolved_joint_occurrences} joint references could not be mapped to a unique occurrence.",
            )
        unique_component_ids = {
            str(item.get("partId", "")) for item in component_items if item.get("partId")
        }
        grouped_bom_rows = sorted(
            bom_rows(component_items),
            key=lambda item: str(item.get("stable_id") or item.get("partId") or item.get("name") or ""),
        )

        assembly_capabilities = assembly_capability_statuses(
            model,
            is_assembly=is_assembly,
            module_issue=module_issue,
            joint_count=len(assembly_joints),
            skipped_joint_count=skipped_assembly_joints,
            unverified_joint_suppression=assembly_result.evidence["unverified_joint_suppression"],
            incomplete_joint_references=assembly_result.evidence["incomplete_joint_references"],
            unresolved_joint_occurrences=unresolved_joint_occurrences,
        )
        if array_evidence or assembly_result.capability == "partial":
            assembly_capabilities["assembly_semantics"] = "partial"
        reference_result = ReferenceGeometryExtractor(
            freecad_reference_geometry_kind,
            lambda value: _sketch_reference(value, self.linked_document_identity),
            _placement_value, _numeric_parameter_value, self.object_visible,
        ).extract(self.document)
        self.provider_durations_ms["referenceGeometry"] = round(reference_result.duration_ms)
        reference_geometry = reference_result.records
        skipped_reference_geometry = reference_result.evidence["skipped"]
        for issue in reference_result.diagnostics:
            self.record_section_issue(issue["section"], issue["code"], issue["message"])
        property_keys = {str(key).casefold(): key for key in properties}
        for candidate in ("configuration", "variant", "config", "modelstate"):
            source_key = property_keys.get(candidate)
            if source_key is not None and properties.get(source_key) not in (None, ""):
                properties.setdefault("configuration", properties[source_key])
                break
        physical = material_result.records[0]
        geometry_quality = (
            "unavailable"
            if not self.effective_shapes
            else "partial"
            if any(item["section"] == "geometry" for item in self.section_diagnostics)
            else "complete"
        )
        warnings.extend(
            issue["message"] for issue in self.section_diagnostics
            if str(issue.get("code", "")).startswith("RECOMPUTE_")
        )
        result = {
            "document_label": self.document.Label or self.document.Name,
            "kind": "assembly" if is_assembly else "part",
            "freecad_version": ".".join(str(value) for value in FreeCAD.Version()),
            "worker_version": CONVERTER_VERSION,
            "source_document": self.source_document,
            "native": {
                "assembly_model": model,
                "workbench_requirements": workbench_requirements,
                "drawing_sheets": drawing_sheets,
                "techdraw_evidence": drawing_evidence,
                "occurrences": occurrences,
                "bom_rows": grouped_bom_rows,
                "parameters": parameters,
                "parameter_graph": parameter_graph,
                "parameter_extraction_skipped": parameter_result.evidence["skipped"],
                "configurations": configurations,
                "spreadsheets": spreadsheets,
                "sketches": sketches,
                "assembly_joints": assembly_joints,
                "link_arrays": array_evidence,
                "assembly_resolution": {
                    "dependency": dependency_status,
                    "geometry": geometry_quality,
                    **assembly_capabilities,
                },
                "reference_geometry": reference_geometry,
                "dependencies": dependency_records,
                "dependency_evidence": preprocessor_dependencies,
                "document_health": self.document_health,
                "extraction_diagnostics": sorted(
                    self.section_diagnostics,
                    key=lambda item: (
                        str(item.get("section", "")),
                        str(item.get("code", "")),
                        str(item.get("object_name", "")),
                    ),
                ),
            },
            "extracted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "bounding_box": dict(
                zip(("x", "y", "z"), (round(float(value), 6) for value in dimensions), strict=True)
            ),
            "shape_volume": round(
                sum(float(shape["volume_mm3"]) for shape in self.effective_shapes), 6
            ),
            "shape_area": round(
                sum(float(shape["surface_area_mm2"]) for shape in self.effective_shapes), 6
            ),
            "center": self.bounding_box_center(bounds),
            "topology": {
                field: sum(int(shape[field]) for shape in self.effective_shapes)
                for field in topology_fields
            },
            "physical": physical,
            "assembly": {
                "is_assembly": is_assembly,
                "subassembly_count": subassembly_count if is_assembly else 0,
                **(
                    {
                        "instance_count": len(component_items) if is_assembly else 0,
                        "unique_component_count": len(unique_component_ids) if is_assembly else 0,
                        "bom_row_count": len(grouped_bom_rows) if is_assembly else 0,
                    }
                    if not unresolved_array_ids else {}
                ),
            },
            "properties": properties,
            "parameters": parameters,
            "quality": {
                "geometry": geometry_quality,
                "physical": material_result.capability,
                "features": feature_quality,
                "assembly": (
                    "partial"
                    if is_assembly
                    and (
                        assembly_capabilities["assembly_semantics"] != "complete"
                        or any(item["section"] == "assembly" for item in self.section_diagnostics)
                    )
                    else "complete"
                    if is_assembly
                    else "not_applicable"
                ),
                "warnings": warnings,
            },
            "object_count": len(self.document.Objects),
        }
        if title_block:
            result["native"]["title_block"] = title_block
        if drawings:
            result["drawings"] = drawings
        if history_types:
            result["features"] = {
                "source": "history",
                "summary": feature_summary,
                "items": feature_items,
            }
            if holes:
                result["features"]["holes"] = sorted(
                    holes,
                    key=lambda item: (
                        float(item.get("diameter_mm", 0)),
                        str(item.get("kind", "")),
                        float(item.get("depth_mm") or 0),
                    ),
                )
        result["extraction"] = {
            "sections": {
                "geometry": self.section_evidence(
                    result["quality"]["geometry"],
                    len(self.effective_shapes),
                    sum(item["section"] == "geometry" for item in self.section_diagnostics),
                    "native_shape",
                ),
                "physical": self.section_evidence(
                    result["quality"]["physical"], len(physical), 0, "native_mass_properties"
                ),
                "features": self.section_evidence(
                    result["quality"]["features"],
                    len(feature_items),
                    skipped_features,
                    "native_history",
                ),
                "assembly": self.section_evidence(
                    result["quality"]["assembly"],
                    len(occurrences),
                    sum(item["section"] == "assembly" for item in self.section_diagnostics),
                    "native_document_graph",
                ),
                "dependencies": self.section_evidence(
                    dependency_status,
                    len(dependency_records),
                    int(preprocessor_dependencies.get("missing_count", 0)),
                    (
                        "dependency_preprocessor_and_native_links"
                        if preprocessor_dependencies["available"]
                        else "resolved_app_links"
                    ),
                ),
                "drawings": self.section_evidence(
                    "partial"
                    if skipped_drawings
                    else str(drawing_evidence["drawing_status"]),
                    sum(len(drawing.get("entities", [])) for drawing in drawings),
                    skipped_drawings,
                    "native_techdraw",
                ),
                "parameters": self.section_evidence(
                    "partial"
                    if any(item["section"] == "parameters" for item in self.section_diagnostics)
                    else "complete"
                    if parameters
                    else "unavailable",
                    len(parameters),
                    sum(item["section"] == "parameters" for item in self.section_diagnostics),
                    "native_properties_and_spreadsheets",
                ),
                "sketches": self.section_evidence(
                    "partial"
                    if skipped_sketches
                    or any(item["section"] == "sketches" for item in self.section_diagnostics)
                    else "complete"
                    if sketches
                    else "not_applicable",
                    len(sketches),
                    skipped_sketches,
                    "native_sketcher",
                ),
                "assembly_joints": self.section_evidence(
                    assembly_capabilities["assembly_constraints"],
                    len(assembly_joints),
                    skipped_assembly_joints,
                    "native_assembly_joint_objects",
                ),
                "configurations": self.section_evidence(
                    "partial"
                    if skipped_configurations
                    else "complete"
                    if configurations["selectors"]
                    else "not_applicable",
                    len(configurations["selectors"]),
                    skipped_configurations,
                    "native_configuration_properties",
                ),
                "reference_geometry": self.section_evidence(
                    "partial"
                    if skipped_reference_geometry
                    else "complete"
                    if reference_geometry
                    else "not_applicable",
                    len(reference_geometry),
                    skipped_reference_geometry,
                    "native_datum_objects",
                ),
            },
            "capabilities": {
                "geometry": result["quality"]["geometry"],
                "physical_properties": result["quality"]["physical"],
                "feature_history": result["quality"]["features"],
                "native_bom": result["quality"]["assembly"],
                "external_dependencies": dependency_status,
                "drawing_dimensions": (
                    "unavailable"
                    if drawing_extraction_failed
                    else str(drawing_evidence["dimension_status"])
                ),
                "gdt": (
                    "unavailable"
                    if drawing_extraction_failed
                    else str(drawing_evidence["gdt_status"])
                ),
                "parameters": (
                    "partial"
                    if any(item["section"] == "parameters" for item in self.section_diagnostics)
                    else "complete"
                    if parameters
                    else "unavailable"
                ),
                "spreadsheet_aliases": "complete" if spreadsheets else "not_applicable",
                "parameter_dependencies": (
                    "partial"
                    if parameter_graph["cycles"]
                    or any(item["section"] == "parameters" for item in self.section_diagnostics)
                    else "complete"
                    if parameters
                    else "not_applicable"
                ),
                "sketch_constraints": (
                    "partial"
                    if skipped_sketches
                    or any(item["section"] == "sketches" for item in self.section_diagnostics)
                    else "complete"
                    if sketches
                    else "not_applicable"
                ),
                "sketch_degrees_of_freedom": (
                    "not_applicable"
                    if not sketches and not skipped_sketches
                    else "unavailable"
                    if not any(sketch.get("degrees_of_freedom") is not None for sketch in sketches)
                    else "partial"
                    if skipped_sketches
                    or any(sketch.get("degrees_of_freedom") is None for sketch in sketches)
                    else "complete"
                ),
                "assembly_constraints": (
                    assembly_capabilities["assembly_constraints"]
                ),
                "assembly_semantics": assembly_capabilities["assembly_semantics"],
                "assembly_solver": assembly_capabilities["assembly_solver"],
                "configurations": (
                    "partial"
                    if skipped_configurations
                    else "complete"
                    if configurations["selectors"]
                    else "not_applicable"
                ),
                "reference_geometry": (
                    "partial"
                    if skipped_reference_geometry
                    else "complete"
                    if reference_geometry
                    else "not_applicable"
                ),
            },
            "provenance": {
                "source_system": "freecad",
                "worker": "modelryx-freecad-worker",
                "worker_version": CONVERTER_VERSION,
                "method": "native",
                "confidence": 1.0,
            },
        }
        section_confidence = {
            name: float(evidence["confidence"])
            for name, evidence in result["extraction"]["sections"].items()
        }
        result["extraction"]["provenance"]["section_confidence"] = section_confidence
        applicable_confidence = [
            confidence
            for name, confidence in section_confidence.items()
            if result["extraction"]["sections"][name]["status"] != "not_applicable"
        ]
        result["extraction"]["provenance"]["confidence"] = round(
            sum(applicable_confidence) / len(applicable_confidence), 3
        ) if applicable_confidence else 1.0
        return result

    @staticmethod
    def section_evidence(
        status: str, records_found: int, records_skipped: int, method: str
    ) -> dict[str, Any]:
        confidence = {
            "complete": 1.0,
            "not_applicable": 1.0,
            "partial": 0.65,
            "unavailable": 0.0,
        }.get(status, 0.0)
        return {
            "status": status,
            "records_found": max(0, records_found),
            "records_skipped": max(0, records_skipped),
            "method": method,
            "confidence": confidence,
        }

    @staticmethod
    def metadata_occurrence(item: dict[str, Any]) -> dict[str, Any]:
        """Return loss-minimized, JSON-safe FreeCAD occurrence evidence."""
        fields = (
            "partId",
            "occurrenceId",
            "parentOccurrenceId",
            "renderOccurrenceId",
            "nodeType",
            "cadType",
            "name",
            "source",
            "placement",
            "display",
            "material",
            "densityKgM3",
            "properties",
            "suppressed",
            "suppressionSource",
        )
        result = {field: item[field] for field in fields if field in item}
        preview = ModelryxExporter.preview_item(item)
        for field in ("dependency", "configuration"):
            if field in preview:
                result[field] = preview[field]
        return result

    @staticmethod
    def bounding_box_center(bounds: Any) -> dict[str, float]:
        """Return the center of the aggregate axis-aligned bounding box in millimetres."""
        center = (bounds[0] + bounds[1]) * 500.0
        return {axis: round(float(center[index]), 6) for index, axis in enumerate(("x", "y", "z"))}

    def aggregate_weighted_center(self, weight_field: str) -> dict[str, float]:
        total = sum(float(shape.get(weight_field) or 0) for shape in self.effective_shapes)
        center = (
            sum(
                np.asarray(shape["center_of_gravity_mm"], dtype=np.float64)
                * float(shape.get(weight_field) or 0)
                for shape in self.effective_shapes
            )
            / total
        )
        return {
            axis: round(float(center[index]), 6)
            for index, axis in enumerate(("x_mm", "y_mm", "z_mm"))
        }

    def root_objects(self) -> Iterable[Any]:
        # A link's native copy-on-change group owns its generated definitions.
        # It is storage for the link, not an additional assembly occurrence.
        self.copy_on_change_groups: set[str] = set()
        for owner in self.document.Objects:
            if getattr(owner, "TypeId", "") != "App::Link":
                continue
            try:
                if "LinkCopyOnChangeGroup" not in getattr(owner, "PropertiesList", ()):
                    continue
                if owner.getTypeIdOfProperty("LinkCopyOnChangeGroup") != "App::PropertyLink":
                    continue
                group = owner.LinkCopyOnChangeGroup
                if (
                    group is not None
                    and getattr(group, "TypeId", "") == "App::LinkGroup"
                    and getattr(group, "Document", None) is self.document
                ):
                    self.copy_on_change_groups.add(str(group.Name))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                self.record_section_issue(
                    "assembly", "LINK_COPY_STORAGE_UNAVAILABLE",
                    f"Copy-on-change storage ownership for '{owner.Name}' could not be inspected.",
                    object_name=str(owner.Name),
                )
        children = {
            child.Name for obj in self.document.Objects for child in hierarchy_children(obj)
        }
        # Link targets may themselves be visible occurrences. In headless
        # FreeCAD their saved GUI visibility can be unavailable.
        linked_definitions = {
            linked.Name
            for obj in self.document.Objects
            if getattr(obj, "TypeId", "") == "App::Link"
            for linked in [linked_object_for(obj)]
            if linked is not None and getattr(linked, "Document", None) is self.document
        }
        for obj in self.document.Objects:
            if obj.Name in linked_definitions and obj.Name not in children:
                view = getattr(obj, "ViewObject", None)
                if obj.Name not in self.saved_visibility and (
                    view is None or not hasattr(view, "Visibility")
                ):
                    for section in ("geometry", "assembly"):
                        self.record_section_issue(
                            section,
                            "LINK_TARGET_VISIBILITY_UNAVAILABLE",
                            f"Visibility of link target '{obj.Name}' is unavailable; it was retained as a possible occurrence.",
                            object_name=str(obj.Name),
                        )
        # Expanded link-array elements are owned by their array container. The
        # container already exposes the combined visible shape, so rendering the
        # document-level element objects again would duplicate that geometry.
        array_elements = {
            str(element.Name)
            for obj in self.document.Objects
            if getattr(obj, "TypeId", "") == "App::Link"
            and int(getattr(obj, "ElementCount", 0) or 0) > 0
            for element in (getattr(obj, "ElementList", ()) or ())
            if getattr(element, "Name", None)
        }
        return [
            obj
            for obj in self.document.Objects
            if obj.Name not in children
            and obj.Name not in self.copy_on_change_groups
            and (
                obj.Name not in linked_definitions
                or self.object_visible(obj)
            )
            and obj.Name not in array_elements
            and (is_group(obj) or has_renderable_geometry(obj))
        ]

    def visit(
        self,
        obj: Any,
        parent_node: str,
        parent_occurrence_id: str,
        path: str,
        render_geometry: bool = True,
        render_occurrence_id: str | None = None,
        world_matrix: Any | None = None,
        ancestors: frozenset[str] = frozenset(),
        claimed_children: dict[str, list[Any]] | None = None,
        allow_shared_navigation: bool = False,
        world_transform_reliable: bool = True,
        parent_visible: bool = True,
        parent_suppressed: bool = False,
    ) -> None:
        if (
            getattr(obj, "Document", None) is self.document
            and str(getattr(obj, "Name", "")) in self.copy_on_change_groups
        ):
            return
        object_key = f"{document_identity(getattr(obj, 'Document', self.document))}|{obj.Name}"
        if object_key in ancestors:
            self.record_section_issue(
                "assembly",
                "REFERENCE_CYCLE_SKIPPED",
                f"Cyclic object reference was skipped at '{obj.Name}'.",
                object_name=str(obj.Name),
            )
            return
        if object_key in self.visited_object_names and not allow_shared_navigation:
            return
        if self.node_count >= self.max_nodes:
            raise ValueError(f"Model exceeds the configured node limit ({self.max_nodes}).")
        self.visited_object_names.add(object_key)
        self.node_count += 1
        child_ancestors = ancestors | {object_key}
        type_name = node_type(obj)
        source_key = f"{document_identity(self.document)}|{obj.Name}|{path}"
        occurrence_id = stable_id("occurrence", source_key)
        is_array_link = False
        if getattr(obj, "TypeId", "") == "App::Link" and getattr(obj, "Document", None) is self.document:
            try:
                is_array_link = int(getattr(obj, "ElementCount", 0) or 0) > 0
            except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
                is_array_link = False
                self.uncertain_array_occurrence_ids.add(occurrence_id)
        if is_array_link:
            self.link_container_occurrences.setdefault(str(obj.Name), []).append(
                {"occurrence_id": occurrence_id, "parent_occurrence_id": parent_occurrence_id}
            )
        linked_object = linked_object_for(obj)
        definition = linked_object or obj
        definition_document = getattr(definition, "Document", None)
        definition_document_id = self.linked_document_identity(definition_document)
        part_id = stable_id("part", str(definition_document_id), str(definition.Name))
        node_name = f"{type_name}-{occurrence_id}"
        local_matrix = placement_matrix(obj)
        parent_world_matrix = world_matrix if world_matrix is not None else np.eye(4)
        object_world_matrix = parent_world_matrix @ local_matrix
        effective_visibility = parent_visible and self.object_visible(obj)
        own_suppression, suppression_source = native_suppression(obj)
        effective_suppression = parent_suppressed or own_suppression is True
        array_geometry_status, array_geometry = "unavailable", None
        if is_array_link and render_geometry and not effective_suppression:
            array_geometry_status, array_geometry = filtered_expanded_array_shape(
                obj, Part, self.max_nodes
            )
        if suppression_source in {"unverified", "unavailable"} and render_geometry:
            self.record_section_issue(
                "assembly", "ASSEMBLY_SUPPRESSION_UNVERIFIED",
                f"Suppression state for '{obj.Name}' could not be verified from a native extension.",
                object_name=str(obj.Name),
            )
        if is_array_link and render_geometry and world_transform_reliable:
            parent_frame = np.array(parent_world_matrix, dtype=float, copy=True)
            parent_frame[:3, 3] *= 1000.0
            self.link_parent_world_frames[occurrence_id] = parent_frame.tolist()
        color, alpha = color_and_alpha(obj, self.shape_appearances)
        try:
            feature_properties = normalized_feature_properties(obj)
        except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError) as error:
            feature_properties = {}
            self.record_section_issue(
                "features", "FEATURE_PROPERTIES_EXTRACTION_FAILED",
                f"Feature properties for '{obj.Name}' could not be extracted: {error}",
                object_name=str(obj.Name),
            )
        properties = {
            **custom_properties(obj),
            **(
                {} if array_geometry_status == "empty"
                else physical_properties(obj, shape_override=array_geometry)
            ),
            **feature_properties,
        }
        material = material_name(obj)
        density_kg_m3 = material_density_kg_m3(obj)
        metadata = {
            "schemaVersion": SCHEMA_VERSION,
            "nodeType": type_name,
            "cadType": cad_type(obj),
            "partId": part_id,
            "occurrenceId": occurrence_id,
            "parentOccurrenceId": parent_occurrence_id,
            "renderOccurrenceId": render_occurrence_id or occurrence_id,
            "source": {
                "system": "FreeCAD",
                "document": self.linked_document_identity(getattr(obj, "Document", None)),
                "occurrenceRole": "structural" if render_geometry else "navigation",
                "objectName": obj.Name,
                "objectLabel": obj.Label,
                "typeId": obj.TypeId,
            },
            "display": {
                "color": "#{:02X}{:02X}{:02X}".format(*color),
                "opacity": alpha,
                "visible": effective_visibility and not effective_suppression,
            },
        }
        if parent_suppressed:
            metadata["suppressed"] = True
            metadata["suppressionSource"] = "ancestor"
        elif suppression_source == "native":
            metadata["suppressed"] = own_suppression
            metadata["suppressionSource"] = "native"
        if linked_object is not None:
            metadata["source"]["linkedObjectName"] = str(linked_object.Name)
            metadata["source"]["linkedDocument"] = str(definition_document_id)
            metadata["source"]["externalDocument"] = definition_document is not self.document
            if definition_document is not self.document:
                filename = str(getattr(definition_document, "FileName", "") or "")
                if filename not in self.external_document_hashes:
                    digest = None
                    if filename:
                        try:
                            hasher = hashlib.sha256()
                            with Path(filename).open("rb") as source_file:
                                for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                                    hasher.update(chunk)
                            digest = hasher.hexdigest()
                        except OSError:
                            digest = None
                    self.external_document_hashes[filename] = digest
                digest = self.external_document_hashes[filename]
                if digest is not None:
                    metadata["source"]["linkedDocumentContentSha256"] = digest
                    metadata["source"]["linkedDocumentHashBasis"] = "loaded_file_bytes"
        if (
            not self.metadata_only
            and render_geometry
            and type_name != "reference"
            and has_shape(obj)
            and array_geometry_status != "empty"
            and effective_visibility
            and not effective_suppression
        ):
            edge_shape = array_geometry if array_geometry is not None else obj.Shape
            edge_segments = None
            if linked_object is not None:
                for cached_shape, cached_edges in self.link_edge_cache.get(part_id, []):
                    if same_native_shape(edge_shape, cached_shape):
                        edge_segments = cached_edges
                        self.edge_cache_hits += 1
                        break
            if edge_segments is None:
                edge_started = time.monotonic()
                edge_segments = topological_edge_segments(edge_shape, self.linear_deflection)
                self.edge_discretization_seconds += time.monotonic() - edge_started
                if linked_object is not None:
                    self.link_edge_cache.setdefault(part_id, []).append((edge_shape, edge_segments))
            if edge_segments:
                metadata["edgeSegments"] = edge_segments
        if material is not None:
            metadata["material"] = material
        if density_kg_m3 is not None:
            metadata["densityKgM3"] = density_kg_m3
        placement = placement_metadata(obj)
        if placement is not None:
            metadata["placement"] = placement
        if self.scene is not None:
            self.scene.graph.update(
                frame_to=node_name,
                frame_from=parent_node,
                matrix=local_matrix,
                metadata={"modelryx": metadata},
            )
        self.bom_items.append({**metadata, "name": obj.Label or obj.Name, "properties": properties})
        if (
            render_geometry and effective_suppression and type_name != "reference"
            and (has_renderable_geometry(obj) or linked_object is not None)
            and (type_name == "body" or not is_group(obj))
        ):
            self.suppressed_component_occurrence_ids.add(occurrence_id)
        if (
            render_geometry
            and not effective_visibility
            and not effective_suppression
            and type_name != "reference"
            and has_renderable_geometry(obj)
            and array_geometry_status != "empty"
            and (type_name == "body" or not is_group(obj))
        ):
            self.hidden_component_occurrence_ids.add(occurrence_id)
            self.record_section_issue(
                "geometry", "HIDDEN_COMPONENT_GEOMETRY_OMITTED",
                f"Hidden component '{obj.Name}' is retained in the BOM but omitted from visible geometry.",
                object_name=str(obj.Name),
            )
            if getattr(obj, "TypeId", "") != "App::Link":
                self.record_section_issue(
                    "assembly", "HIDDEN_COMPONENT_BOM_MEMBERSHIP_UNVERIFIED",
                    f"Hidden object '{obj.Name}' was retained in the BOM; its component ownership is not explicit.",
                    object_name=str(obj.Name),
                )
        if (
            render_geometry
            and type_name != "reference"
            and has_renderable_geometry(obj)
            and array_geometry_status != "empty"
            and effective_visibility
            and not effective_suppression
            and (type_name == "body" or not is_group(obj))
        ):
            shape = (
                array_geometry if array_geometry is not None
                else getattr(obj, "Shape", None) if has_shape(obj) else None
            )
            native_mesh = getattr(obj, "Mesh", None) if shape is None else None
            native_face_appearances = (
                face_appearances(obj, self.shape_appearances)
                if shape is not None and not self.metadata_only else []
            )
            if array_geometry is not None and native_face_appearances:
                mapped_appearances = filtered_face_appearances(obj.Shape, array_geometry, native_face_appearances)
                if mapped_appearances is None:
                    native_face_appearances = []
                    self.record_section_issue(
                        "geometry", "LINK_ARRAY_FILTERED_FACE_APPEARANCE_UNAVAILABLE",
                        f"Per-face appearance for filtered array '{obj.Name}' could not be mapped to its retained elements.",
                        object_name=str(obj.Name),
                    )
                else:
                    native_face_appearances = mapped_appearances
            cached_tessellation = None
            if not self.metadata_only and shape is not None and linked_object is not None:
                for cached_shape, appearances, cached_mesh, cached_colored in (
                    self.link_tessellation_cache.get(part_id, [])
                ):
                    if same_native_shape(shape, cached_shape) and appearances == native_face_appearances:
                        cached_tessellation = (cached_mesh, cached_colored)
                        self.tessellation_cache_hits += 1
                        break
            if cached_tessellation is not None:
                mesh_data, colored_mesh_data = cached_tessellation
            else:
                tessellation_started = time.monotonic()
                colored_mesh_data = (
                    mesh_for_colored_faces(
                        shape,
                        native_face_appearances,
                        self.linear_deflection,
                        self.angular_deflection,
                    )
                    if native_face_appearances
                    else None
                )
                mesh_data = (
                    colored_mesh_data[:2]
                    if colored_mesh_data is not None
                    else (np.zeros((1, 3), dtype=np.float64), np.empty((0, 3), dtype=np.int64))
                    if shape is not None and self.metadata_only
                    else mesh_for_shape(shape, self.linear_deflection, self.angular_deflection)
                    if shape is not None
                    else mesh_for_native_mesh(native_mesh)
                )
                if mesh_data is None and shape is not None and trimesh is not None:
                    mesh_data = mesh_for_edges_and_points(shape, self.linear_deflection)
                if not self.metadata_only and shape is not None:
                    self.tessellation_seconds += time.monotonic() - tessellation_started
                if not self.metadata_only and shape is not None and linked_object is not None:
                    self.link_tessellation_cache.setdefault(part_id, []).append(
                        (shape, native_face_appearances, mesh_data, colored_mesh_data)
                    )
            if mesh_data:
                vertices, faces = mesh_data
                if shape is not None:
                    local_bounds = local_shape(shape).BoundBox
                    corners = np.asarray(
                        [
                            [x, y, z, 1.0]
                            for x in (local_bounds.XMin, local_bounds.XMax)
                            for y in (local_bounds.YMin, local_bounds.YMax)
                            for z in (local_bounds.ZMin, local_bounds.ZMax)
                        ],
                        dtype=np.float64,
                    )
                    corners[:, :3] /= 1000.0
                else:
                    mesh_bounds = np.asarray([vertices.min(axis=0), vertices.max(axis=0)])
                    corners = np.asarray(
                        [
                            [x, y, z, 1.0]
                            for x in mesh_bounds[:, 0]
                            for y in mesh_bounds[:, 1]
                            for z in mesh_bounds[:, 2]
                        ],
                        dtype=np.float64,
                    )
                self.exact_bound_points.extend((object_world_matrix @ corners.T).T[:, :3])
                mesh_center_mm = (
                    object_world_matrix
                    @ np.asarray([*vertices.mean(axis=0), 1.0], dtype=np.float64)
                )[:3] * 1000.0
                self.effective_shapes.append(
                    {
                        "part_id": part_id,
                        "occurrence_id": occurrence_id,
                        "parent_occurrence_id": parent_occurrence_id,
                        "node_type": type_name,
                        "geometry_evidence_verified": (
                            not is_array_link or array_geometry_status in {"filtered", "unchanged"}
                        ),
                        "volume_mm3": properties.get("volumeMm3", 0),
                        "surface_area_mm2": properties.get("surfaceAreaMm2", 0),
                        "solid_count": len(getattr(shape, "Solids", [])) if shape else 0,
                        "shell_count": len(getattr(shape, "Shells", [])) if shape else 0,
                        "face_count": len(getattr(shape, "Faces", [])) if shape else len(faces),
                        "edge_count": len(getattr(shape, "Edges", [])) if shape else 0,
                        "vertex_count": (
                            len(getattr(shape, "Vertexes", [])) if shape else len(vertices)
                        ),
                        "material": material,
                        "density_kg_m3": density_kg_m3,
                        "mass_kg": (
                            float(properties.get("volumeMm3", 0)) * density_kg_m3 * 1e-9
                            if density_kg_m3 is not None
                            else None
                        ),
                        "center_of_gravity_mm": (
                            world_center_of_gravity_mm(shape, object_world_matrix)
                            if shape is not None
                            else mesh_center_mm
                        ),
                    }
                )
                if not self.metadata_only and self.triangle_count + len(faces) > self.max_triangles:
                    raise ValueError(
                        f"Model exceeds the configured triangle limit ({self.max_triangles})."
                    )
                if not self.metadata_only:
                    self.triangle_count += len(faces)
                if not self.metadata_only:
                    mesh_name = f"mesh-{occurrence_id}"
                    fingerprint = hashlib.sha256()
                    fingerprint.update(vertices.tobytes())
                    fingerprint.update(faces.tobytes())
                    fingerprint.update(bytes(color))
                    fingerprint.update(str(alpha).encode())
                    if colored_mesh_data is not None:
                        fingerprint.update(colored_mesh_data[2].tobytes())
                    fingerprint.update(str(self.linear_deflection).encode())
                    fingerprint.update(str(self.angular_deflection).encode())
                    shape_key = fingerprint.hexdigest()[:24]
                    geometry_name = self.mesh_cache.get(shape_key)
                    if geometry_name is not None:
                        self.scene.graph.update(
                            frame_to=mesh_name,
                            frame_from=node_name,
                            geometry=geometry_name,
                        )
                    else:
                        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
                        if colored_mesh_data is not None:
                            mesh.visual.vertex_colors = colored_mesh_data[2]
                        else:
                            mesh.visual.vertex_colors = np.tile(
                                np.array([*color, round(alpha * 255)], dtype=np.uint8),
                                (len(vertices), 1),
                            )
                        geometry_name = f"geometry-{shape_key}"
                        self.scene.add_geometry(
                            mesh,
                            node_name=mesh_name,
                            geom_name=geometry_name,
                            parent_node_name=node_name,
                        )
                        self.mesh_cache[shape_key] = geometry_name
                        self.mesh_count += 1
        # Keep PartDesign history for normalized engineering metadata, but the
        # viewer preview filters it out because Body.Shape is the final solid.
        if type_name == "body":
            group = list(getattr(obj, "Group", []) or [])
            body_claimed_children: dict[str, list[Any]] = {}
            claimed_names: set[str] = set()
            # PartDesign Body.Group is an ordered feature-history list.  The
            # FreeCAD tree nests each driving sketch below the first later
            # feature that consumes it, rather than showing every item flat or
            # expanding the complete OutList dependency graph.
            for index, child in enumerate(group):
                if str(getattr(child, "TypeId", "")) != "Sketcher::SketchObject":
                    continue
                for owner in group[index + 1 :]:
                    if child in (getattr(owner, "OutList", None) or []):
                        body_claimed_children.setdefault(owner.Name, []).append(child)
                        claimed_names.add(child.Name)
                        break
            for child in group:
                # Origin and its axes/planes are FreeCAD implementation details
                # and do not belong in final-body viewer navigation.
                if node_type(child) == "reference" or child.Name in claimed_names:
                    continue
                self.visit(
                    child,
                    node_name,
                    occurrence_id,
                    f"{path}/{obj.Name}",
                    render_geometry=False,
                    render_occurrence_id=occurrence_id,
                    world_matrix=object_world_matrix,
                    ancestors=child_ancestors,
                    claimed_children=body_claimed_children,
                    world_transform_reliable=False,
                    parent_visible=effective_visibility,
                    parent_suppressed=effective_suppression,
                )
        elif type_name != "reference" and getattr(obj, "TypeId", "") != "App::Link":
            group_children = list(getattr(obj, "Group", None) or [])
            children = (
                claimed_children.get(obj.Name, [])
                if claimed_children is not None
                else hierarchy_children(obj)
            )
            for child in children:
                self.visit(
                    child,
                    node_name,
                    occurrence_id,
                    f"{path}/{obj.Name}",
                    render_geometry=render_geometry if child in group_children else False,
                    render_occurrence_id=render_occurrence_id,
                    world_matrix=object_world_matrix,
                    ancestors=child_ancestors,
                    claimed_children=claimed_children,
                    world_transform_reliable=(
                        world_transform_reliable and child in group_children
                        and _reliable_group_placement(obj)
                    ),
                    parent_visible=effective_visibility,
                    parent_suppressed=effective_suppression,
                    # A dependency subtree claimed below two distinct group
                    # occurrences belongs below both in navigation, even
                    # though its geometry remains represented only by the
                    # placed result shapes. Keep the global guard for ordinary
                    # feature-history graphs, which can otherwise explode.
                    allow_shared_navigation=(
                        allow_shared_navigation or (render_geometry and child in group_children)
                    ),
                )


def extract_fcstd_part_metadata(input_path: str, output_path: str) -> str:
    """Extract normalized metadata with the same traversal used by worker conversion."""
    fcstd_path = Path(input_path).resolve()
    if not fcstd_path.is_file() or fcstd_path.suffix.lower() != ".fcstd":
        raise ValueError("Input must be an existing .FCStd file.")
    metadata_path = Path(output_path).resolve()
    check_native_document_size(fcstd_path)
    document = FreeCAD.open(str(fcstd_path))
    try:
        recompute_succeeded, recompute_error, recompute_diagnostics = recompute_for_extraction(document)
        exporter = ModelryxExporter(
            document,
            TESSELLATION_PROFILES["standard"]["linear_deflection"],
            TESSELLATION_PROFILES["standard"]["angular_deflection"],
            read_fcstd_shape_appearances(fcstd_path),
            source_document=fcstd_document_provenance(fcstd_path),
            dependency_report=find_dependency_report(fcstd_path),
            metadata_only=True,
        )
        exporter.inspect_document_health(
            recompute_succeeded=recompute_succeeded,
            recompute_error=recompute_error,
            recompute_diagnostics=recompute_diagnostics,
        )
        metadata = exporter.extract_metadata()
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    finally:
        FreeCAD.closeDocument(document.Name)
    return str(metadata_path)


def convert_fcstd_to_modelryx_package(
    input_path: str,
    output_path: str | None = None,
    linear_deflection: float | None = None,
    angular_deflection: float | None = None,
    keep_exploded: bool = False,
    max_nodes: int = 100_000,
    max_triangles: int = 5_000_000,
    progress_file: str | None = None,
    tessellation_profile: str = "standard",
) -> str:
    """Create one `.pfg` file; optionally retain adjacent GLB/BOM debug files."""
    linear_deflection, angular_deflection, tessellation_source = tessellation_settings(
        tessellation_profile, linear_deflection, angular_deflection
    )
    fcstd_path = Path(input_path).resolve()
    if not fcstd_path.is_file() or fcstd_path.suffix.lower() != ".fcstd":
        raise ValueError("Input must be an existing .FCStd file.")
    check_native_document_size(fcstd_path)
    pfg_path = Path(output_path).resolve() if output_path else fcstd_path.with_suffix(".pfg")
    if pfg_path.suffix.lower() != ".pfg":
        raise ValueError("Output must use the .pfg extension.")
    progress_path = Path(progress_file).resolve() if progress_file else None
    stage_durations_ms: dict[str, int] = {}

    def report_progress(stage: str, progress: int, message: str) -> None:
        if progress_path is None:
            return
        atomic_write_json(
            progress_path,
            {"stage": stage, "progress": progress, "message": message},
        )

    report_progress("OPENING_DOCUMENT", 40, "Opening the FreeCAD document.")
    stage_started = time.monotonic()
    document = FreeCAD.open(str(fcstd_path))
    stage_durations_ms["openDocument"] = round((time.monotonic() - stage_started) * 1000)
    try:
        report_progress("RECOMPUTING_DOCUMENT", 45, "Recomputing the FreeCAD document.")
        stage_started = time.monotonic()
        recompute_succeeded, recompute_error, recompute_diagnostics = recompute_for_extraction(document)
        stage_durations_ms["recompute"] = round((time.monotonic() - stage_started) * 1000)
        with tempfile.TemporaryDirectory(prefix="modelryx-pfg-") as temp_dir:
            temp_path = Path(temp_dir)
            glb_path = temp_path / "model.glb"
            preview_path = temp_path / "model-preview.json"
            report_progress("READING_APPEARANCES", 50, "Reading source colors and appearances.")
            stage_started = time.monotonic()
            appearances = read_fcstd_shape_appearances(fcstd_path)
            source_document = fcstd_document_provenance(fcstd_path)
            stage_durations_ms["sourceInspection"] = round(
                (time.monotonic() - stage_started) * 1000
            )
            exporter = ModelryxExporter(
                document,
                linear_deflection,
                angular_deflection,
                appearances,
                max_nodes,
                max_triangles,
                source_document=source_document,
                dependency_report=find_dependency_report(fcstd_path),
            )
            exporter.inspect_document_health(
                recompute_succeeded=recompute_succeeded,
                recompute_error=recompute_error,
                recompute_diagnostics=recompute_diagnostics,
            )
            report_progress(
                "EXPORTING_GEOMETRY",
                55,
                "Exporting and tessellating visible geometry.",
            )
            stage_started = time.monotonic()
            exporter.export(glb_path, preview_path)
            stage_durations_ms["geometryAndMetadata"] = round(
                (time.monotonic() - stage_started) * 1000
            )
            report_progress("EXPORTING_DRAWINGS", 62, "Rendering TechDraw pages.")
            stage_started = time.monotonic()
            drawing_previews = render_techdraw_pages(document, temp_path / "drawings")
            stage_durations_ms["drawingPreviews"] = round(
                (time.monotonic() - stage_started) * 1000
            )
            report_progress("WRITING_METADATA", 65, "Writing engineering metadata.")
            stage_started = time.monotonic()
            raw_metadata_path = pfg_path.with_suffix(".freecad-raw-metadata.json")
            part_metadata_path = pfg_path.with_suffix(".part-metadata.json")
            raw_metadata_path.write_text(
                json.dumps(exporter.raw_metadata_document, indent=2), encoding="utf-8"
            )
            part_metadata_path.write_text(
                json.dumps(exporter.part_metadata_document, indent=2), encoding="utf-8"
            )
            stage_durations_ms["metadataSidecars"] = round(
                (time.monotonic() - stage_started) * 1000
            )
            source_sha256 = sha256_file(fcstd_path)
            diagnostics = {
                "converterVersion": CONVERTER_VERSION,
                "warnings": exporter.warnings,
                "sectionDiagnostics": exporter.section_diagnostics,
                "sections": exporter.part_metadata_document.get("extraction", {}).get(
                    "sections", {}
                ),
                "statistics": {
                    "bomItems": len(exporter.bom_items),
                    "meshes": exporter.mesh_count,
                    "triangles": exporter.triangle_count,
                    "tessellationCacheHits": exporter.tessellation_cache_hits,
                    "edgeCacheHits": exporter.edge_cache_hits,
                    "objectCount": len(document.Objects),
                    "tessellationMs": round(exporter.tessellation_seconds * 1000),
                    "edgeDiscretizationMs": round(exporter.edge_discretization_seconds * 1000),
                    "glbBytes": glb_path.stat().st_size,
                    "durationMs": round((time.monotonic() - exporter.started_at) * 1000),
                    "peakMemoryBytes": peak_memory_bytes(),
                    "stageDurationsMs": stage_durations_ms,
                    "providerDurationsMs": exporter.provider_durations_ms,
                },
            }
            manifest = {
                "format": "Modelryx Graphics",
                "version": 1,
                "units": "m",
                "model": "model.glb",
                "modelPreview": "model-preview.json",
                "source": {
                    "system": "FreeCAD",
                    "fileName": fcstd_path.name,
                    "sha256": source_sha256,
                    "document": source_document,
                },
                "converter": {
                    "version": CONVERTER_VERSION,
                    "tessellationProfile": tessellation_profile,
                    "tessellationSource": tessellation_source,
                    "linearDeflection": linear_deflection,
                    "angularDeflection": angular_deflection,
                    "maxNodes": max_nodes,
                    "maxTriangles": max_triangles,
                },
                "outputs": {
                    "modelSha256": sha256_file(glb_path),
                    "modelPreviewSha256": sha256_file(preview_path),
                },
                "drawings": drawing_previews,
                "diagnostics": "diagnostics.json",
            }
            report_progress("PACKAGING_PREVIEW", 68, "Packaging preview and diagnostics.")
            with zipfile.ZipFile(pfg_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
                package.writestr("manifest.json", json.dumps(manifest, indent=2))
                package.write(glb_path, "model.glb")
                package.write(preview_path, "model-preview.json")
                for drawing_preview in drawing_previews:
                    package.write(temp_path / drawing_preview["file"], drawing_preview["file"])
                package.writestr("diagnostics.json", json.dumps(diagnostics, indent=2))
            if keep_exploded:
                shutil.copyfile(glb_path, fcstd_path.with_suffix(".glb"))
                shutil.copyfile(preview_path, fcstd_path.with_suffix(".model-preview.json"))
            report_progress("CONVERSION_COMPLETE", 70, "Native FreeCAD conversion completed.")
    finally:
        FreeCAD.closeDocument(document.Name)
    return str(pfg_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export an FCStd model as a Modelryx Graphics (.pfg) package."
    )
    parser.add_argument("input", help="Input .FCStd file")
    parser.add_argument("-o", "--output", help="Output .pfg file (defaults to the input filename)")
    parser.add_argument(
        "--tessellation-profile",
        choices=sorted(TESSELLATION_PROFILES),
        default="standard",
        help="Named tessellation quality profile (default: standard)",
    )
    parser.add_argument(
        "--linear-deflection",
        type=float,
        help="Override the profile's tessellation deviation in FreeCAD millimetres",
    )
    parser.add_argument(
        "--angular-deflection",
        type=float,
        help="Override the profile's tessellation angular deviation in degrees",
    )
    parser.add_argument(
        "--keep-exploded",
        action="store_true",
        help="Also write adjacent .glb and .model-preview.json debug files",
    )
    parser.add_argument("--max-nodes", type=int, default=100_000)
    parser.add_argument("--max-triangles", type=int, default=5_000_000)
    parser.add_argument(
        "--progress-file",
        help="Write the current native conversion stage as a small JSON document",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Run the worker metadata traversal/normalizer without creating a PFG preview",
    )
    parser.add_argument(
        "--metadata-output",
        help="Output JSON path used with --metadata-only",
    )
    args = parser.parse_args()
    if args.metadata_only:
        if not args.metadata_output:
            parser.error("--metadata-output is required with --metadata-only")
        metadata = extract_fcstd_part_metadata(args.input, args.metadata_output)
        print(f"PartMetadata: {metadata}")
        return 0
    package = convert_fcstd_to_modelryx_package(
        args.input,
        args.output,
        linear_deflection=args.linear_deflection,
        angular_deflection=args.angular_deflection,
        tessellation_profile=args.tessellation_profile,
        keep_exploded=args.keep_exploded,
        max_nodes=args.max_nodes,
        max_triangles=args.max_triangles,
        progress_file=args.progress_file,
    )
    print(f"PFG: {package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
