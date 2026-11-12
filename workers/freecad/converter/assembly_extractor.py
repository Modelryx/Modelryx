"""Native FreeCAD assembly joint and link-array evidence provider."""

from __future__ import annotations

import time
from typing import Any

from assembly_suppression import native_suppression
from extraction_provider import ProviderResult
from link_array_evidence import link_arrays


def _assembly_joint_kind(obj: Any) -> str | None:
    """Recognize built-in FreeCAD joints, including their FeaturePython wrappers."""
    type_id = str(getattr(obj, "TypeId", ""))
    if type_id == "Assembly::JointGroup":
        return None
    proxy = getattr(obj, "Proxy", None)
    proxy_module = str(getattr(type(proxy), "__module__", ""))
    proxy_name = str(getattr(type(proxy), "__name__", ""))
    properties = set(getattr(obj, "PropertiesList", ()))
    if proxy_module == "JointObject" and proxy_name == "GroundedJoint":
        return "Grounded"
    if proxy_module == "JointObject" and proxy_name == "Joint":
        return str(getattr(obj, "JointType", "Unknown") or "Unknown")
    if "Joint" in type_id or {"JointType", "Reference1", "Reference2"} <= properties:
        return str(getattr(obj, "JointType", type_id.rsplit("::", 1)[-1]) or "Unknown")
    if "ObjectToGround" in properties:
        return "Grounded"
    return None


def native_assembly_joints(
    document: Any, to_reference: Any, to_placement: Any, to_numeric: Any
) -> tuple[list[dict[str, Any]], int]:
    """Extract native assembly constraints without loading optional Assembly3/4 modules."""
    joints: list[dict[str, Any]] = []
    skipped = 0
    dof_by_type = {
        "fixed": 0,
        "grounded": 0,
        "revolute": 1,
        "slider": 1,
        "cylindrical": 2,
        "ball": 3,
        "distance": 5,
        "parallel": 4,
        "perpendicular": 4,
        "angle": 4,
        "rackpinion": 1,
        "screw": 1,
        "gears": 1,
        "belt": 1,
    }
    for obj in sorted(getattr(document, "Objects", ()), key=lambda item: str(item.Name).casefold()):
        joint_type = _assembly_joint_kind(obj)
        if joint_type is None:
            continue
        try:
            properties = set(getattr(obj, "PropertiesList", ()))
            references = []
            for role, property_name in (
                ("first", "Reference1"),
                ("second", "Reference2"),
                ("grounded", "ObjectToGround"),
            ):
                if property_name not in properties:
                    continue
                reference = to_reference(getattr(obj, property_name, None))
                if reference is not None:
                    references.append({"role": role, **reference})
            record: dict[str, Any] = {
                "name": str(obj.Name),
                "label": str(getattr(obj, "Label", obj.Name)),
                "type": joint_type,
                "grounded": joint_type.casefold() == "grounded",
                "degrees_of_freedom": dof_by_type.get(joint_type.casefold()),
                "degrees_of_freedom_basis": "nominal_joint_type",
                "solver_status": "unavailable",
                "references": references,
            }
            required_roles = {"grounded"} if joint_type.casefold() == "grounded" else {"first", "second"}
            record["references_complete"] = required_roles <= {ref["role"] for ref in references}
            suppressed, source = native_suppression(obj)
            if suppressed is not None:
                record["suppressed"] = suppressed
            record["suppression_source"] = source or "not_exposed"
            frames = {}
            for field, property_name in (
                ("first", "Placement1"),
                ("second", "Placement2"),
                ("first_offset", "Offset1"),
                ("second_offset", "Offset2"),
            ):
                if property_name in properties:
                    placement = to_placement(getattr(obj, property_name, None))
                    if placement is not None:
                        frames[field] = placement
            if frames:
                record["frames"] = frames
            values = {}
            for field, property_name in (
                ("distance_mm", "Distance"),
                ("secondary_distance_mm", "Distance2"),
                ("angle_deg", "Angle"),
                ("length_min_mm", "LengthMin"),
                ("length_max_mm", "LengthMax"),
                ("angle_min_deg", "AngleMin"),
                ("angle_max_deg", "AngleMax"),
            ):
                if property_name not in properties:
                    continue
                value, _unit = to_numeric(getattr(obj, property_name, None))
                if value is not None:
                    values[field] = round(value, 9)
            if values:
                record["values"] = values
            limits = {}
            for field, property_name in (
                ("length_min", "EnableLengthMin"),
                ("length_max", "EnableLengthMax"),
                ("angle_min", "EnableAngleMin"),
                ("angle_max", "EnableAngleMax"),
            ):
                if property_name in properties:
                    limits[field] = bool(getattr(obj, property_name, False))
            if limits:
                record["limits_enabled"] = limits
            joints.append(record)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            skipped += 1
    return joints, skipped


class AssemblyExtractor:
    def __init__(
        self,
        to_reference: Any,
        to_placement: Any,
        to_numeric: Any,
        document_identity: Any,
        max_elements: int,
        element_transform: Any = None,
    ):
        self.to_reference = to_reference
        self.to_placement = to_placement
        self.to_numeric = to_numeric
        self.document_identity = document_identity
        self.max_elements = max_elements
        self.element_transform = element_transform

    def extract(self, document: Any) -> ProviderResult:
        started = time.monotonic()
        diagnostics: list[dict[str, Any]] = []
        # solve() changes component placements, and its return code does not
        # provide the observed freedom or a complete constraint diagnosis.
        # Never invoke it during metadata extraction.
        for obj in getattr(document, "Objects", ()):
            if getattr(obj, "TypeId", "") == "Assembly::AssemblyObject":
                diagnostics.append({
                    "section": "assembly",
                    "code": "ASSEMBLY_SOLVER_OBSERVATION_UNAVAILABLE",
                    "message": (
                        "Modelryx cannot currently read assembly solver status or observed "
                        "degrees of freedom. Joint freedom is nominal."
                    ),
                    "object_name": str(obj.Name),
                })
        try:
            joints, skipped = native_assembly_joints(
                document, self.to_reference, self.to_placement, self.to_numeric
            )
            uncertain = sum("suppressed" not in joint for joint in joints)
            incomplete = sum(not joint["references_complete"] for joint in joints)
            if incomplete:
                diagnostics.append({
                    "section": "assembly",
                    "code": "ASSEMBLY_JOINT_REFERENCES_INCOMPLETE",
                    "message": f"Required references could not be resolved for {incomplete} assembly joints.",
                })
            if uncertain:
                diagnostics.append(
                    {
                        "section": "assembly",
                        "code": "ASSEMBLY_JOINT_SUPPRESSION_UNVERIFIED",
                        "message": f"Suppression could not be verified for {uncertain} assembly joints.",
                    }
                )
            if skipped:
                diagnostics.append(
                    {
                        "section": "assembly",
                        "code": "ASSEMBLY_JOINT_EXTRACTION_FAILED",
                        "message": f"{skipped} FreeCAD assembly joint records could not be extracted.",
                    }
                )
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            joints, skipped = [], 1
            diagnostics.append(
                {
                    "section": "assembly",
                    "code": "ASSEMBLY_JOINT_EXTRACTION_FAILED",
                    "message": f"Assembly joint semantics could not be extracted: {error}",
                }
            )
        try:
            arrays = link_arrays(
                document,
                placement_value=self.to_placement,
                document_identity=self.document_identity,
                max_elements=self.max_elements,
                element_transform=self.element_transform,
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            arrays = []
            diagnostics.append(
                {
                    "section": "assembly",
                    "code": "LINK_ARRAY_EXTRACTION_FAILED",
                    "message": f"FreeCAD link-array evidence could not be extracted: {error}",
                }
            )
        if arrays:
            for array in arrays:
                summary = array.get("suppression_summary", {})
                unknown = summary.get("unknown_inspected_count", 0)
                uninspected = summary.get("uninspected_count", 0)
                if unknown or uninspected:
                    name = str(array.get("name") or "")
                    diagnostics.append({
                        "section": "assembly",
                        "code": "LINK_ARRAY_ELEMENT_ACTIVITY_UNKNOWN",
                        "object_name": name,
                        "message": (
                            f"Array '{name}' has {unknown} elements with unknown suppression "
                            f"and {uninspected} elements not inspected; visibility does not establish activity."
                        ),
                    })
            diagnostics.extend(
                [
                    {
                        "section": "assembly",
                        "code": "LINK_ARRAY_OCCURRENCES_UNSUPPORTED",
                        "message": "Link-array evidence was retained; occurrence and BOM support remains limited to verified native cases.",
                    },
                    {
                        "section": "geometry",
                        "code": "LINK_ARRAY_OCCURRENCES_UNSUPPORTED",
                        "message": "Link-array visibility and per-element geometry have not been fully validated.",
                    },
                ]
            )
            unavailable_arrays = sum(array.get("status") == "unavailable" for array in arrays)
            if unavailable_arrays:
                diagnostics.append(
                    {
                        "section": "assembly",
                        "code": "LINK_ARRAY_EXTRACTION_FAILED",
                        "message": f"Evidence for {unavailable_arrays} link arrays could not be inspected.",
                    }
                )
            truncated_elements = sum(
                max(0, int(array.get("element_count", 0)) - len(array.get("elements", [])))
                for array in arrays
                if array.get("truncated")
            )
            if truncated_elements:
                diagnostics.append(
                    {
                        "section": "assembly",
                        "code": "LINK_ARRAY_EVIDENCE_TRUNCATED",
                        "message": f"Evidence for {truncated_elements} link-array elements exceeded the extraction limit.",
                    }
                )
            if self.element_transform is not None:
                unavailable = sum(
                    element.get("transform_to_array_parent") is None
                    for array in arrays
                    for element in array.get("elements", [])
                )
                if unavailable:
                    diagnostics.append(
                        {
                            "section": "assembly",
                            "code": "LINK_ARRAY_TRANSFORM_UNAVAILABLE",
                            "message": f"FreeCAD could not resolve parent-relative transforms for {unavailable} link-array elements.",
                        }
                    )
            suppressed_elements = sum(
                element.get("suppression", {}).get("state") == "suppressed"
                for array in arrays
                for element in array.get("elements", [])
            )
            if suppressed_elements:
                diagnostics.append(
                    {
                        "section": "geometry",
                        "code": "LINK_ARRAY_SUPPRESSED_GEOMETRY_UNVERIFIED",
                        "message": f"{suppressed_elements} suppressed array elements require per-element geometry verification; FreeCAD's combined shape may include them.",
                    }
                )
            unverified_suppression = sum(
                element.get("suppression", {}).get("source") in {"unverified", "unavailable"}
                for array in arrays
                for element in array.get("elements", [])
            )
            if unverified_suppression:
                diagnostics.append(
                    {
                        "section": "assembly",
                        "code": "LINK_ARRAY_SUPPRESSION_UNVERIFIED",
                        "message": f"Suppression could not be verified for {unverified_suppression} array elements.",
                    }
                )
        return ProviderResult(
            records=joints,
            diagnostics=diagnostics,
            evidence={
                "skipped": skipped,
                "arrays": arrays,
                "unverified_joint_suppression": sum("suppressed" not in joint for joint in joints),
                "incomplete_joint_references": sum(not joint["references_complete"] for joint in joints),
            },
            capability="partial" if diagnostics else "complete" if joints else "not_applicable",
            duration_ms=(time.monotonic() - started) * 1000,
        )
