"""Execute one server-authored, allowlisted CAD plan in FreeCAD."""

# FreeCAD must initialize before stdlib imports in its bundled Python runtime.
# ruff: noqa: I001

from __future__ import annotations

import FreeCAD  # type: ignore
import Part  # type: ignore
import Sketcher  # type: ignore

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

source_shared = Path(__file__).resolve().parents[1] / "shared"
if source_shared.is_dir():
    sys.path.insert(0, str(source_shared))

from freecad_material import material_name
from document_health import check_document_object_limit
from modelryx_freecad_shared import close_document_safely
from worker_io import check_native_document_size, sha256_file

PROPERTY_MAP = {
    "PAD": {"length_mm": "Length", "length": "Length"},
    "POCKET": {
        "depth_mm": "Length",
        "depth": "Length",
        "length_mm": "Length",
        "length": "Length",
    },
    "HOLE": {
        "diameter_mm": "Diameter",
        "diameter": "Diameter",
        "radius_mm": "Radius",
        "radius": "Radius",
    },
    "FILLET": {"radius_mm": "Radius", "radius": "Radius"},
}
TYPE_MAP = {
    "PAD": "PartDesign::Pad",
    "POCKET": "PartDesign::Pocket",
    "HOLE": "PartDesign::Hole",
    "FILLET": "PartDesign::Fillet",
}


def numeric(value: Any) -> float:
    result = float(getattr(value, "Value", value))
    if not math.isfinite(result):
        raise ValueError("Native CAD numeric values must be finite")
    return result


def recompute_checked(document: Any) -> None:
    check_document_object_limit(document)
    if document.recompute() is False:
        raise ValueError("FreeCAD rejected the controlled recompute")


def sketch_constraint(document: Any, plan: dict[str, Any]) -> tuple[Any, int, str]:
    sketch = document.getObject(plan["target_id"])
    match = re.fullmatch(r"Constraints\[(\d+)\]", str(plan["parameter"]))
    if sketch is None or sketch.TypeId != "Sketcher::SketchObject" or not match:
        raise ValueError("Confirmed sketch constraint target is invalid")
    index = int(match.group(1))
    if index >= len(sketch.Constraints):
        raise ValueError("Confirmed sketch constraint no longer exists")
    constraint = sketch.Constraints[index]
    unit = plan.get("unit")
    kind = str(constraint.Type)
    expected_unit = "deg" if kind == "Angle" else "mm" if kind in {
        "Distance", "DistanceX", "DistanceY", "Radius", "Diameter"
    } else None
    if unit != expected_unit or expected_unit is None:
        raise ValueError("Sketch constraint type or unit is unsupported")
    if not sketch.getDriving(index) or not sketch.getActive(index):
        raise ValueError("Sketch constraint must be active and driving")
    paths = {f"Constraints[{index}]"}
    if getattr(constraint, "Name", ""):
        paths.add(f"Constraints.{constraint.Name}")
    if any(str(path).lstrip(".") in paths for path, _ in sketch.ExpressionEngine):
        raise ValueError("Expression-driven constraints cannot be edited directly")
    datum = sketch.getDatum(index)
    if str(datum.Unit.Type) != ("Angle" if unit == "deg" else "Length"):
        raise ValueError("Sketch constraint datum unit does not match")
    expected, new_value = float(plan["expected_value"]), float(plan["new_value"])
    if not math.isfinite(expected) or not math.isfinite(new_value):
        raise ValueError("Sketch constraint values must be finite")
    if kind in {"Radius", "Diameter", "Distance"} and new_value <= 0:
        raise ValueError("Sketch constraint value must be positive")
    if abs(numeric(datum) - expected) > 1e-7:
        raise ValueError("Native sketch constraint precondition failed")
    return sketch, index, unit


def feature_errors(document: Any) -> list[dict[str, str]]:
    errors = []
    for obj in document.Objects:
        state = [str(value) for value in getattr(obj, "State", [])]
        if any(value not in {"Up-to-date", "Touched"} for value in state):
            errors.append({"object": obj.Name, "state": ", ".join(state)})
    return errors


def shape_report(document: Any) -> dict[str, Any]:
    shapes = [
        obj.Shape
        for obj in document.Objects
        if getattr(obj, "Shape", None) is not None and not obj.Shape.isNull()
    ]
    solids = sum(len(shape.Solids) for shape in shapes)
    return {
        "shape_count": len(shapes),
        "solid_count": solids,
        "volume_mm3": sum(float(shape.Volume) for shape in shapes),
        "face_count": sum(len(shape.Faces) for shape in shapes),
        "edge_count": sum(len(shape.Edges) for shape in shapes),
        "all_shapes_valid": bool(shapes) and all(shape.isValid() for shape in shapes),
    }


def _new_feature(document: Any, feature_id: str) -> None:
    if document.getObject(feature_id) is not None:
        raise ValueError("Generated feature ID already exists")


def _body(document: Any, body_id: str) -> Any:
    body = document.getObject(body_id)
    if body is None or body.TypeId != "PartDesign::Body":
        raise ValueError("Confirmed parent is not a PartDesign body")
    return body


def _profile_body(profile: Any) -> Any:
    body = profile.getParentGeoFeatureGroup()
    if body is None or body.TypeId != "PartDesign::Body":
        raise ValueError("Confirmed profile is not owned by a PartDesign body")
    return body


CREATION_FIELDS = {
    "sketch.create": {"feature_id", "plane", "geometry", "width_mm", "height_mm", "radius_mm"},
    "feature.create_pad": {"feature_id", "length_mm"},
    "feature.create_pocket": {"feature_id", "depth_mm"},
    "feature.create_hole": {
        "feature_id",
        "plane",
        "x_mm",
        "y_mm",
        "diameter_mm",
        "depth_mm",
    },
    "feature.create_fillet": {"feature_id", "edge_indices", "radius_mm"},
}


def _place_on_principal_plane(sketch: Any, plane: str) -> None:
    if plane == "XZ":
        sketch.Placement.Rotation = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 90)
    elif plane == "YZ":
        sketch.Placement.Rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), 90)


def create_feature(
    document: Any, operation: str, target_id: str, values: dict[str, Any]
) -> dict[str, Any]:
    allowed = CREATION_FIELDS.get(operation)
    if allowed is None or not set(values) <= allowed or "feature_id" not in values:
        raise ValueError("Feature creation values contain unsupported fields")
    feature_id = str(values["feature_id"])
    _new_feature(document, feature_id)
    if operation == "sketch.create":
        sketch = _body(document, target_id).newObject("Sketcher::SketchObject", feature_id)
        _place_on_principal_plane(sketch, values["plane"])
        if values["geometry"] == "circle":
            sketch.addGeometry(
                Part.Circle(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), values["radius_mm"]),
                False,
            )
        else:
            half_width = values["width_mm"] / 2
            half_height = values["height_mm"] / 2
            points = [
                FreeCAD.Vector(-half_width, -half_height, 0),
                FreeCAD.Vector(half_width, -half_height, 0),
                FreeCAD.Vector(half_width, half_height, 0),
                FreeCAD.Vector(-half_width, half_height, 0),
            ]
            lines = [
                sketch.addGeometry(
                    Part.LineSegment(points[index], points[(index + 1) % 4]), False
                )
                for index in range(4)
            ]
            for index in range(4):
                sketch.addConstraint(
                    Sketcher.Constraint(
                        "Coincident", lines[index], 2, lines[(index + 1) % 4], 1
                    )
                )
    elif operation in {"feature.create_pad", "feature.create_pocket"}:
        profile = document.getObject(target_id)
        if profile is None or profile.TypeId != "Sketcher::SketchObject":
            raise ValueError("Confirmed profile is not a sketch")
        type_id = "PartDesign::Pad" if operation.endswith("pad") else "PartDesign::Pocket"
        feature = _profile_body(profile).newObject(type_id, feature_id)
        feature.Profile = profile
        feature.Length = float(values.get("length_mm", values.get("depth_mm")))
    elif operation == "feature.create_hole":
        body = _body(document, target_id)
        sketch = body.newObject("Sketcher::SketchObject", f"{feature_id}_Profile")
        _place_on_principal_plane(sketch, values["plane"])
        sketch.addGeometry(
            Part.Circle(
                FreeCAD.Vector(values["x_mm"], values["y_mm"], 0),
                FreeCAD.Vector(0, 0, 1),
                values["diameter_mm"] / 2,
            ),
            False,
        )
        feature = body.newObject("PartDesign::Pocket", feature_id)
        feature.Profile = sketch
        feature.Length = float(values["depth_mm"])
    elif operation == "feature.create_fillet":
        base = document.getObject(target_id)
        if base is None or getattr(base, "Shape", None) is None:
            raise ValueError("Confirmed fillet base is unavailable")
        edge_names = []
        for index in values["edge_indices"]:
            if index < 1 or index > len(base.Shape.Edges):
                raise ValueError("Confirmed fillet edge index is outside the native shape")
            edge_names.append(f"Edge{index}")
        feature = _profile_body(base).newObject("PartDesign::Fillet", feature_id)
        feature.Base = (base, edge_names)
        feature.Radius = float(values["radius_mm"])
    else:
        raise ValueError("Feature creation operation is not supported")
    recompute_checked(document)
    return creation_postconditions(document, operation, values)


def creation_postconditions(
    document: Any, operation: str, values: dict[str, Any]
) -> dict[str, Any]:
    feature_id = str(values["feature_id"])
    created = document.getObject(feature_id)
    if created is None or created.Name != feature_id:
        raise ValueError("Feature creation postcondition failed")
    postconditions: dict[str, Any] = {"feature_id": feature_id, "type_id": created.TypeId}
    if operation == "sketch.create":
        expected_geometry = 1 if values["geometry"] == "circle" else 4
        if created.TypeId != "Sketcher::SketchObject" or created.GeometryCount != expected_geometry:
            raise ValueError("Sketch geometry postcondition failed")
        if values["geometry"] == "circle":
            if abs(numeric(created.Geometry[0].Radius) - float(values["radius_mm"])) > 1e-7:
                raise ValueError("Sketch radius postcondition failed")
        else:
            width, height = float(values["width_mm"]), float(values["height_mm"])
            points = [(-width / 2, -height / 2), (width / 2, -height / 2),
                      (width / 2, height / 2), (-width / 2, height / 2)]
            for index, geometry in enumerate(created.Geometry):
                for point, expected in ((geometry.StartPoint, points[index]),
                                        (geometry.EndPoint, points[(index + 1) % 4])):
                    if any(abs(numeric(actual) - wanted) > 1e-7
                           for actual, wanted in zip((point.x, point.y, point.z), (*expected, 0))):
                        raise ValueError("Sketch rectangle dimension postcondition failed")
        postconditions["geometry_count"] = created.GeometryCount
        postconditions["plane"] = values["plane"]
    elif operation in {"feature.create_pad", "feature.create_pocket"}:
        expected_type = "PartDesign::Pad" if operation.endswith("pad") else "PartDesign::Pocket"
        expected_length = float(values.get("length_mm", values.get("depth_mm")))
        if created.TypeId != expected_type or abs(numeric(created.Length) - expected_length) > 1e-7:
            raise ValueError("Extrusion dimension postcondition failed")
        postconditions["length_mm"] = numeric(created.Length)
    elif operation == "feature.create_hole":
        profile = document.getObject(f"{feature_id}_Profile")
        radius = (
            numeric(profile.Geometry[0].Radius)
            if profile and profile.GeometryCount == 1
            else -1
        )
        if (
            created.TypeId != "PartDesign::Pocket"
            or abs(numeric(created.Length) - float(values["depth_mm"])) > 1e-7
            or abs(radius * 2 - float(values["diameter_mm"])) > 1e-7
        ):
            raise ValueError("Hole dimension postcondition failed")
        postconditions.update(
            {
                "diameter_mm": radius * 2,
                "depth_mm": numeric(created.Length),
                "plane": values["plane"],
            }
        )
    elif operation == "feature.create_fillet":
        if created.TypeId != "PartDesign::Fillet" or abs(
            numeric(created.Radius) - float(values["radius_mm"])
        ) > 1e-7:
            raise ValueError("Fillet radius postcondition failed")
        postconditions["radius_mm"] = numeric(created.Radius)
        postconditions["edge_indices"] = values["edge_indices"]
    return postconditions


def execute(plan_path: Path, source: Path, output: Path, report_path: Path) -> None:
    check_native_document_size(source)
    if source.resolve() in {output.resolve(), report_path.resolve()} or output.resolve() == report_path.resolve():
        raise ValueError("CAD source, result and report must use distinct paths")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    required = {
        "operation",
        "target_id",
        "parameter",
        "expected_value",
        "new_value",
        "source_sha256",
    }
    if set(plan) != (required | ({"unit"} if plan.get("operation") == "sketch.constraint.set_value" else set())):
        raise ValueError("Plan contains unsupported fields")
    if sha256_file(source) != plan["source_sha256"]:
        raise ValueError("Working source checksum does not match the confirmed plan")
    document = FreeCAD.open(str(source))
    try:
        check_document_object_limit(document)
        operation = plan["operation"]
        observed_before: float | str | None
        observed_after: Any
        geometry_before = shape_report(document)
        requested_postconditions: dict[str, Any] = {}
        if operation == "sketch.constraint.set_value":
            sketch, index, unit = sketch_constraint(document, plan)
            observed_before = numeric(sketch.getDatum(index))
            sketch.setDatum(index, FreeCAD.Units.Quantity(f"{float(plan['new_value'])} {unit}"))
            recompute_checked(document)
            observed_after = numeric(sketch.getDatum(index))
            if abs(observed_after - float(plan["new_value"])) > 1e-7:
                raise ValueError("Native sketch constraint postcondition failed")
            requested_postconditions = {"constraint_index": index, "unit": unit}
        elif operation == "feature.parameter.set_value":
            numeric(plan["expected_value"])
            numeric(plan["new_value"])
            obj = document.getObject(plan["target_id"])
            if obj is None:
                raise ValueError("Confirmed target no longer exists")
            feature_type = next(
                (name for name, type_id in TYPE_MAP.items() if obj.TypeId == type_id), None
            )
            property_name = PROPERTY_MAP.get(feature_type or "", {}).get(plan["parameter"])
            if property_name is None or property_name not in obj.PropertiesList:
                raise ValueError("Confirmed parameter is not supported by the native target")
            observed_before = numeric(getattr(obj, property_name))
            if abs(observed_before - float(plan["expected_value"])) > 1e-7:
                raise ValueError("Native parameter precondition failed")
            setattr(obj, property_name, float(plan["new_value"]))
            recompute_checked(document)
            observed_after = numeric(getattr(obj, property_name))
            if abs(observed_after - float(plan["new_value"])) > 1e-7:
                raise ValueError("Native parameter postcondition failed")
        elif operation == "material.set":
            current_materials = {
                material
                for obj in document.Objects
                if (shape := getattr(obj, "Shape", None)) is not None
                and not shape.isNull()
                if (material := material_name(obj)) is not None
            }
            expected_material = plan["expected_value"]
            if expected_material is None:
                if current_materials:
                    raise ValueError("Native material precondition failed")
                observed_before = None
            else:
                if current_materials != {str(expected_material)}:
                    raise ValueError("Native material precondition failed")
                observed_before = current_materials.pop()
            changed = 0
            for obj in document.Objects:
                shape = getattr(obj, "Shape", None)
                if shape is None or shape.isNull():
                    continue
                if "Material" not in obj.PropertiesList:
                    obj.addProperty("App::PropertyString", "Material", "Modelryx")
                obj.Material = str(plan["new_value"])
                changed += 1
            if not changed:
                raise ValueError("Document has no material-capable shapes")
            recompute_checked(document)
            observed_after = str(plan["new_value"])
        elif operation in {
            "sketch.create",
            "feature.create_pad",
            "feature.create_pocket",
            "feature.create_hole",
            "feature.create_fillet",
        }:
            if plan["expected_value"] != {} or not isinstance(plan["new_value"], dict):
                raise ValueError("Feature creation preconditions are malformed")
            observed_before = len(document.Objects)
            requested_postconditions = create_feature(
                document, operation, plan["target_id"], plan["new_value"]
            )
            observed_after = requested_postconditions
        else:
            raise ValueError("Operation is not supported by the controlled worker")
        errors = feature_errors(document)
        geometry = shape_report(document)
        if operation != "sketch.create" and operation in CREATION_FIELDS:
            before_solids = geometry_before["solid_count"]
            if geometry == geometry_before or geometry["solid_count"] < 1 or before_solids < 1:
                raise ValueError("Feature creation did not produce an observable solid change")
        if errors:
            raise ValueError(f"FreeCAD recompute reported feature errors: {errors}")
        if not geometry["all_shapes_valid"] or geometry["solid_count"] < 1:
            raise ValueError("FreeCAD result has no valid solid")
        document.saveAs(str(output))
        if not output.is_file():
            raise ValueError("FreeCAD did not save the result")
        # Verify the persisted native artifact for every supported edit, not just
        # the in-memory result. Reopening must not repair it through recompute.
        close_document_safely(document, FreeCAD)
        document = None
        reopened = FreeCAD.openDocument(str(output))
        try:
            check_document_object_limit(reopened)
            saved_geometry = shape_report(reopened)
            if feature_errors(reopened) or not saved_geometry["all_shapes_valid"]:
                raise ValueError("Saved native result health postcondition failed")
            if any(
                not math.isclose(saved_geometry[key], value, rel_tol=1e-9, abs_tol=1e-7)
                if isinstance(value, float) else saved_geometry[key] != value
                for key, value in geometry.items()
            ):
                raise ValueError("Saved native geometry postcondition failed")
            if operation == "sketch.constraint.set_value":
                saved = reopened.getObject(plan["target_id"])
                if saved is None or abs(numeric(saved.getDatum(index)) - float(plan["new_value"])) > 1e-7:
                    raise ValueError("Saved sketch constraint postcondition failed")
            elif operation == "feature.parameter.set_value":
                saved = reopened.getObject(plan["target_id"])
                if saved is None or abs(numeric(getattr(saved, property_name)) - float(plan["new_value"])) > 1e-7:
                    raise ValueError("Saved native parameter postcondition failed")
            elif operation == "material.set":
                if any(
                    material_name(obj) != str(plan["new_value"])
                    for obj in reopened.Objects
                    if (shape := getattr(obj, "Shape", None)) is not None and not shape.isNull()
                ):
                    raise ValueError("Saved native material postcondition failed")
            elif creation_postconditions(reopened, operation, plan["new_value"]) != requested_postconditions:
                raise ValueError("Saved feature creation postcondition failed")
            geometry = saved_geometry
        finally:
            close_document_safely(reopened, FreeCAD)
        report_path.write_text(
            json.dumps(
                {
                    "observed_before": observed_before,
                    "observed_after": observed_after,
                    "geometry": geometry,
                    "geometry_before": geometry_before,
                    "requested_postconditions": requested_postconditions,
                    "requested_postconditions_passed": True,
                    "feature_errors": errors,
                    "output_sha256": sha256_file(output),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    finally:
        if document is not None:
            close_document_safely(document, FreeCAD)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    execute(
        Path(args.plan).resolve(),
        Path(args.source).resolve(),
        Path(args.output).resolve(),
        Path(args.report).resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
