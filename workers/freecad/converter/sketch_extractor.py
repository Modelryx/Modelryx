"""Native FreeCAD Sketcher extraction provider."""

from __future__ import annotations

import math
import re
import time
from typing import Any

from extraction_provider import ProviderResult


def _indexed_topology_references(
    supports: list[dict[str, Any]], external: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Identify indexed topology links without asserting that the links are damaged."""
    result: list[dict[str, str]] = []
    for role, references in (("attachment_support", supports), ("external_geometry", external)):
        for reference in references:
            for subelement in reference.get("subelements", []):
                if re.fullmatch(r"(?:.*\.)?(?:Face|Edge|Vertex)[1-9]\d*", str(subelement)):
                    item = {
                        "role": role,
                        "document": str(reference.get("document", "")),
                        "object": str(reference.get("object", "")),
                        "subelement": str(subelement),
                    }
                    if item not in result:
                        result.append(item)
    return sorted(
        result,
        key=lambda item: (item["role"], item["document"], item["object"], item["subelement"]),
    )


def native_sketches(
    document: Any, to_vector: Any, to_numeric: Any, to_references: Any, to_placement: Any = None
) -> tuple[list[dict[str, Any]], int]:
    """Extract deterministic Sketcher geometry, constraints, support, and solver evidence."""
    sketches: list[dict[str, Any]] = []
    skipped = 0
    for sketch in sorted(
        (
            obj
            for obj in getattr(document, "Objects", ())
            if str(getattr(obj, "TypeId", "")) == "Sketcher::SketchObject"
        ),
        key=lambda item: str(item.Name).casefold(),
    ):
        try:
            geometry_records = []
            for index, geometry in enumerate(getattr(sketch, "Geometry", ()) or ()):
                record: dict[str, Any] = {
                    "index": index,
                    "type": str(getattr(geometry, "TypeId", "") or type(geometry).__name__),
                }
                construction_method = getattr(sketch, "getConstruction", None)
                if callable(construction_method):
                    record["construction"] = bool(construction_method(index))
                for field, attribute in {
                    "center": "Center",
                    "start": "StartPoint",
                    "end": "EndPoint",
                    "location": "Location",
                    "axis": "Axis",
                }.items():
                    if (vector := to_vector(getattr(geometry, attribute, None))) is not None:
                        record[field] = vector
                for field, attribute in {
                    "radius": "Radius",
                    "major_radius": "MajorRadius",
                    "minor_radius": "MinorRadius",
                    "first_parameter": "FirstParameter",
                    "last_parameter": "LastParameter",
                }.items():
                    raw_value = getattr(geometry, attribute, None)
                    if raw_value is None:
                        continue
                    try:
                        value = float(raw_value)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if math.isfinite(value):
                        record[field] = round(value, 9)
                geometry_records.append(record)

            expressions = {
                str(name): str(expression)
                for name, expression in (getattr(sketch, "ExpressionEngine", ()) or ())
                if name and expression
            }
            normalized_expressions = {
                name.lstrip("."): value for name, value in expressions.items()
            }
            constraint_records = []
            for index, constraint in enumerate(getattr(sketch, "Constraints", ()) or ()):
                record = {
                    "index": index,
                    "type": str(getattr(constraint, "Type", "") or type(constraint).__name__),
                }
                name = str(getattr(constraint, "Name", "") or "").strip()
                if name:
                    record["name"] = name
                paths = [f"Constraints[{index}]"]
                if name:
                    paths.append(f"Constraints.{name}")
                matches = {
                    normalized_expressions[path] for path in paths if path in normalized_expressions
                }
                if len(matches) == 1:
                    record["expression"] = next(iter(matches))
                elif len(matches) > 1:
                    record["expression_status"] = "ambiguous"
                driving_method = getattr(sketch, "isDriving", None) or getattr(sketch, "getDriving", None)
                if callable(driving_method):
                    try:
                        record["driving"] = bool(driving_method(index))
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        pass
                active_method = getattr(sketch, "isActive", None) or getattr(sketch, "getActive", None)
                if callable(active_method):
                    try:
                        record["active"] = bool(active_method(index))
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        pass
                virtual_method = getattr(sketch, "getVirtualSpace", None)
                if callable(virtual_method):
                    record["virtual_space"] = bool(virtual_method(index))
                for field, attribute in {
                    "first_geometry": "First",
                    "first_position": "FirstPos",
                    "second_geometry": "Second",
                    "second_position": "SecondPos",
                    "third_geometry": "Third",
                    "third_position": "ThirdPos",
                }.items():
                    raw_value = getattr(constraint, attribute, None)
                    # Sketcher uses -3, -4, ... for successive external geometry;
                    # -2000 denotes an unused geometry/position slot.
                    if isinstance(raw_value, int) and raw_value != -2000:
                        record[field] = raw_value
                raw_value = getattr(constraint, "Value", None)
                datum = getattr(sketch, "getDatum", None)
                if callable(datum):
                    try:
                        raw_value = datum(index)
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        raw_value = getattr(constraint, "Value", None)
                value, unit = to_numeric(raw_value)
                if value is not None:
                    record["value"] = round(value, 9)
                if unit:
                    record["unit"] = unit
                constraint_records.append(record)

            dof = None
            dof_method = getattr(sketch, "getSolverDoFs", None)
            if callable(dof_method):
                raw_dof = dof_method()
                if isinstance(raw_dof, int) and not isinstance(raw_dof, bool) and raw_dof >= 0:
                    dof = raw_dof
            sketch_states = {
                str(value).casefold() for value in (getattr(sketch, "State", ()) or ())
            }
            solver_constraints = {
                field: list(getattr(sketch, native, ()) or ())
                for field, native in (
                    ("conflicting_constraints", "ConflictingConstraints"),
                    ("redundant_constraints", "RedundantConstraints"),
                    ("partially_redundant_constraints", "PartiallyRedundantConstraints"),
                    ("malformed_constraints", "MalformedConstraints"),
                )
            }
            solver_error = bool(sketch_states - {"up-to-date", "touched"}) or any(
                solver_constraints.values()
            )
            native_fully_constrained = getattr(sketch, "FullyConstrained", None)
            fully_constrained = (
                None
                if solver_error
                else dof == 0
                if dof is not None
                else native_fully_constrained
                if isinstance(native_fully_constrained, bool)
                else None
            )
            support_value = getattr(sketch, "AttachmentSupport", None)
            if not support_value:
                support_value = getattr(sketch, "Support", None)
            supports = to_references(support_value)
            external = getattr(sketch, "ExternalGeometry", ()) or ()
            external_references = to_references(external)
            map_mode = str(getattr(sketch, "MapMode", "") or "Deactivated")
            attachment_status = (
                "not_attached"
                if map_mode.casefold() == "deactivated"
                else "supported"
                if supports
                else "unresolved"
                if support_value
                else "support_not_specified"
            )
            record = {
                "name": str(sketch.Name),
                "label": str(getattr(sketch, "Label", sketch.Name)),
                "geometry_count": len(geometry_records),
                "construction_geometry_count": sum(
                    bool(item.get("construction")) for item in geometry_records
                ),
                "external_geometry_count": sum(
                    max(1, len(reference["subelements"])) for reference in external_references
                )
                if external_references
                else len(external),
                "external_geometry_references": external_references,
                "indexed_topology_references": _indexed_topology_references(
                    supports, external_references
                ),
                "constraint_count": len(constraint_records),
                "degrees_of_freedom": dof,
                "fully_constrained": fully_constrained,
                "solver_state": (
                    "error"
                    if solver_error
                    else "fully_constrained"
                    if fully_constrained is True
                    else "under_constrained"
                    if fully_constrained is False
                    else "unknown"
                ),
                "map_mode": map_mode,
                "attachment_status": attachment_status,
                "support": supports[0] if supports else None,
                "supports": supports,
                **solver_constraints,
                "attachment_offset": str(getattr(sketch, "AttachmentOffset", "") or ""),
                "expressions": expressions,
                "geometry": geometry_records,
                "constraints": constraint_records,
            }
            if callable(to_placement):
                offset = to_placement(getattr(sketch, "AttachmentOffset", None))
                if offset is not None:
                    record["attachment_offset_placement"] = offset
            sketches.append(record)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            skipped += 1
    return sketches, skipped


class SketchExtractor:
    def __init__(
        self, to_vector: Any, to_numeric: Any, to_references: Any, to_placement: Any = None
    ):
        self.to_vector = to_vector
        self.to_numeric = to_numeric
        self.to_references = to_references
        self.to_placement = to_placement

    def extract(self, document: Any) -> ProviderResult:
        started = time.monotonic()
        diagnostics: list[dict[str, Any]] = []
        try:
            sketches, skipped = native_sketches(
                document, self.to_vector, self.to_numeric, self.to_references, self.to_placement
            )
            if skipped:
                diagnostics.append(
                    {
                        "section": "sketches",
                        "code": "SKETCH_EXTRACTION_FAILED",
                        "message": f"{skipped} FreeCAD sketch records could not be extracted.",
                    }
                )
            for sketch in sketches:
                name = str(sketch["name"])
                if sketch.get("solver_state") == "error":
                    diagnostics.append(
                        {
                            "section": "sketches",
                            "code": "SKETCH_SOLVER_ERROR",
                            "message": f"FreeCAD reports a solver error for sketch '{name}'.",
                            "object_name": name,
                        }
                    )
                if sketch.get("degrees_of_freedom") is None:
                    diagnostics.append(
                        {
                            "section": "sketches",
                            "code": "SKETCH_DEGREES_OF_FREEDOM_UNAVAILABLE",
                            "message": f"FreeCAD did not expose a degrees-of-freedom count for sketch '{name}'.",
                            "object_name": name,
                        }
                    )
                if sketch.get("attachment_status") in {"support_not_specified", "unresolved"}:
                    diagnostics.append(
                        {
                            "section": "sketches",
                            "code": "SKETCH_ATTACHMENT_SUPPORT_UNAVAILABLE",
                            "message": f"Sketch '{name}' has an active attachment mode without a resolved support.",
                            "object_name": name,
                        }
                    )
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            sketches, skipped = [], 1
            diagnostics.append(
                {
                    "section": "sketches",
                    "code": "SKETCH_EXTRACTION_FAILED",
                    "message": f"Sketch semantics could not be extracted: {error}",
                }
            )
        return ProviderResult(
            records=sketches,
            diagnostics=diagnostics,
            evidence={"skipped": skipped},
            capability="partial" if diagnostics else "complete" if sketches else "not_applicable",
            duration_ms=(time.monotonic() - started) * 1000,
        )
