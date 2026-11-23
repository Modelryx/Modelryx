"""Orchestration for validating and persisting revision-owned engineering metadata."""

from __future__ import annotations

import copy
import re
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from modelryx.metadata.derived import build_derived_metadata
from modelryx.metadata.engineering_validator import validate_engineering_metadata
from modelryx.metadata.validator import validate_part_metadata
from modelryx.models import (
    ArtifactFile,
    Item,
    MetadataExtraction,
    Revision,
    RevisionContentSnapshot,
)


def canonicalize_item_kind(metadata: dict, part_type: str) -> dict:
    """Make the managed PLM Item type authoritative over CAD geometry heuristics."""
    canonical = copy.deepcopy(metadata)
    managed_kind = "assembly" if part_type == "ASSEMBLY" else "part"
    canonical["identity"]["kind"] = managed_kind
    canonical.setdefault("assembly", {})["is_assembly"] = managed_kind == "assembly"
    if managed_kind == "part":
        for field in (
            "instance_count",
            "unique_component_count",
            "subassembly_count",
            "bom_row_count",
        ):
            canonical["assembly"].pop(field, None)
        canonical["quality"]["assembly"] = "not_applicable"
    return canonical


def add_drawing_identity_warnings(metadata: dict, item: Item, revision: Revision) -> dict:
    """Record title-block completeness and consistency with the managed revision."""
    canonical = copy.deepcopy(metadata)
    title_block = canonical.get("native", {}).get("title_block", {})
    if not isinstance(title_block, dict) or not title_block:
        return canonical

    identity = canonical.get("identity", {})
    warnings = canonical.setdefault("quality", {}).setdefault("warnings", [])
    properties = canonical.setdefault("properties", {})
    issues: list[str] = []

    def differs(left: object, right: object) -> bool:
        return str(left).strip().casefold() != str(right).strip().casefold()

    drawing_part_number = identity.get("part_number")
    managed_part_number = getattr(item, "part_number", None)
    if not drawing_part_number:
        issues.append("Drawing title block does not provide a mapped part number.")
    elif (
        drawing_part_number
        and managed_part_number
        and differs(drawing_part_number, managed_part_number)
    ):
        issues.append(
            "Drawing title-block part number "
            f"'{drawing_part_number}' does not match Modelryx item '{managed_part_number}'."
        )

    drawing_revision = identity.get("revision")
    managed_revision = getattr(revision, "revision_code", None)
    if not drawing_revision:
        issues.append("Drawing title block does not provide a mapped revision.")
    elif managed_revision and differs(drawing_revision, managed_revision):
        issues.append(
            "Drawing title-block revision "
            f"'{drawing_revision}' does not match Modelryx revision '{managed_revision}'."
        )

    mismatch = any("does not match" in issue for issue in issues)
    properties["title_block_validation_status"] = (
        "mismatch" if mismatch else "incomplete" if issues else "matched"
    )
    if issues:
        properties["title_block_validation_issues"] = issues
        warnings.extend(issue for issue in issues if issue not in warnings)
    else:
        properties.pop("title_block_validation_issues", None)
    return canonical


def normalize_search_properties(metadata: dict) -> dict:
    """Promote common CAD aliases into stable scalar fields used by search and AI."""
    canonical = metadata
    properties = canonical.setdefault("properties", {})
    native = canonical.get("native", {})
    title_block = native.get("title_block", {}) if isinstance(native, dict) else {}
    candidates = {str(key): value for key, value in properties.items()}
    if isinstance(title_block, dict):
        candidates.update({str(key): value for key, value in title_block.items()})

    keyed = {
        "".join(character for character in key.casefold() if character.isalnum()): value
        for key, value in candidates.items()
    }
    aliases = {
        "drawing_number": ("drawingnumber", "drawingno", "dwgnumber", "dwgno"),
        "stock_number": ("stocknumber", "stockno"),
        "project": ("project", "projectname"),
        "author": ("author", "designer", "drawnby", "createdby"),
        "finish": ("finish", "surfacefinish", "coating"),
        "treatment": ("treatment", "heattreatment", "thermalTreatment"),
        "supplier": ("supplier", "vendor", "manufacturer"),
        "configuration": ("configuration", "modelstate", "modelstatename", "membername"),
    }
    for target, names in aliases.items():
        if properties.get(target) not in (None, ""):
            continue
        value = next(
            (
                keyed[name.casefold()]
                for name in names
                if keyed.get(name.casefold()) not in (None, "")
            ),
            None,
        )
        if isinstance(value, (str, int, float, bool)):
            properties[target] = value
    if not properties.get("configuration") and isinstance(native, dict):
        value = (
            native.get("active_model_state")
            or native.get("active_iassembly_member")
            or native.get("active_arrangement")
        )
        if isinstance(value, str) and value.strip():
            properties["configuration"] = value.strip()
    return canonical


def enhance_extraction_metadata(metadata: dict) -> dict:
    """Build cross-CAD searchable evidence without discarding native metadata."""
    properties = metadata.setdefault("properties", {})
    native = metadata.get("native", {}) if isinstance(metadata.get("native"), dict) else {}
    physical = metadata.get("physical", {}) if isinstance(metadata.get("physical"), dict) else {}
    material = physical.get("material") if isinstance(physical.get("material"), dict) else None
    if material and material.get("name"):
        name = str(material["name"]).strip()
        normalized = re.sub(r"\s+", " ", name).casefold()
        material.setdefault("normalized_name", normalized)
        tokens = name.replace("-", " ").split()
        if tokens:
            material.setdefault("family", tokens[0])
        grade_match = re.search(r"\b(?:aisi\s*)?[A-Z]?\d{3,4}(?:-?T\d+)?\b", name, re.I)
        if grade_match:
            material.setdefault("grade", grade_match.group(0))
        temper_match = re.search(r"\b(?:T[1-9]\d?|H\d{2,3}|O)\b", name, re.I)
        if temper_match:
            material.setdefault("temper", temper_match.group(0).upper())

    manufacturing_aliases = {
        "process": ("manufacturing_process", "process"),
        "stock_form": ("stock_form", "stock_type"),
        "heat_treatment": ("heat_treatment", "treatment"),
        "surface_treatment": ("surface_treatment", "coating"),
        "surface_roughness_ra_um": ("surface_roughness_ra_um", "roughness_ra"),
        "finish": ("finish",),
        "supplier": ("supplier",),
        "manufacturer": ("manufacturer",),
    }
    manufacturing: dict = dict(metadata.get("manufacturing", {}))
    for target, aliases in manufacturing_aliases.items():
        value = next(
            (properties.get(alias) for alias in aliases if properties.get(alias) not in (None, "")),
            None,
        )
        if isinstance(value, (str, int, float)):
            manufacturing[target] = value
    if manufacturing:
        metadata["manufacturing"] = manufacturing

    raw_parameters = native.get("parameters", [])
    if not metadata.get("parameters") and isinstance(raw_parameters, list):
        parameters = []
        for value in raw_parameters:
            if not isinstance(value, dict) or not str(value.get("name") or "").strip():
                continue
            raw_value = value.get("value")
            numeric_value = (
                raw_value
                if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool)
                else None
            )
            parameters.append(
                {
                    "name": str(value["name"]),
                    "display_name": value.get("display_name") or value.get("name"),
                    "value": numeric_value,
                    "unit": value.get("unit"),
                    "expression": value.get("expression"),
                    "is_key": bool(value.get("is_key", False)),
                    "is_user": bool(value.get("is_user_parameter", value.get("is_user", False))),
                    "feature_id": value.get("feature_id"),
                    "configuration": value.get("configuration") or properties.get("configuration"),
                    "native_name": value.get("native_name") or value.get("name"),
                }
            )
        if parameters:
            metadata["parameters"] = parameters

    assembly = metadata.get("assembly") if isinstance(metadata.get("assembly"), dict) else None
    occurrences = native.get("occurrences", [])
    if assembly is not None and isinstance(occurrences, list):
        components = []
        for value in occurrences:
            if not isinstance(value, dict):
                continue
            occurrence_id = value.get("id") or value.get("occurrenceId")
            name = value.get("name") or occurrence_id
            if not occurrence_id or not name:
                continue
            component = {
                "occurrence_id": str(occurrence_id),
                "parent_occurrence_id": value.get("parent_id") or value.get("parentOccurrenceId"),
                "name": str(name),
                "part_number": value.get("part_number") or value.get("partNumber"),
                "revision": value.get("revision"),
                "description": value.get("description"),
                "quantity": _non_negative_int(value.get("quantity"), default=1),
                "bom_structure": value.get("bom_structure"),
                "suppressed": bool(value.get("suppressed", False)),
                "reference": bool(value.get("reference", False)),
                "substitute": bool(value.get("substitute", False)),
                "configuration": value.get("model_state") or value.get("configuration"),
                "dependency_status": (value.get("dependency") or {}).get("status")
                if isinstance(value.get("dependency"), dict)
                else None,
                "dependency_document": (value.get("dependency") or {}).get("document")
                if isinstance(value.get("dependency"), dict)
                else None,
                "dependency_external": bool((value.get("dependency") or {}).get("external"))
                if isinstance(value.get("dependency"), dict)
                else False,
                "material": value.get("material"),
                "mass_kg": (value.get("physical") or {}).get("mass_kg")
                if isinstance(value.get("physical"), dict)
                else None,
            }
            components.append(component)
        if components:
            assembly["components"] = components

    quality = metadata["quality"]
    drawings = metadata.get("drawings", []) if isinstance(metadata.get("drawings"), list) else []
    drawing_entities = sum(
        len(value.get("entities", [])) for value in drawings if isinstance(value, dict)
    )
    features = metadata.get("features", {}) if isinstance(metadata.get("features"), dict) else {}
    feature_items = features.get("items", []) if isinstance(features.get("items"), list) else []
    sections = {
        "geometry": _section_evidence(quality["geometry"], 1, "native_geometry"),
        "physical": _section_evidence(
            quality["physical"], 1 if physical else 0, "native_mass_properties"
        ),
        "features": _section_evidence(
            quality["features"], len(feature_items), features.get("source", "unavailable")
        ),
        "parameters": _section_evidence(
            "complete" if metadata.get("parameters") else "unavailable",
            len(metadata.get("parameters", [])),
            "native_parameters",
        ),
        "assembly": _section_evidence(
            quality["assembly"],
            len(assembly.get("components", [])) if assembly else 0,
            "native_occurrences",
        ),
        "drawings": _section_evidence(
            "complete" if drawings else "not_applicable",
            drawing_entities,
            "native_drawing_annotations",
        ),
    }
    capabilities = {
        "geometry": quality["geometry"],
        "physical_properties": quality["physical"],
        "feature_history": quality["features"],
        "native_bom": quality["assembly"],
        "drawing_dimensions": "complete"
        if any(
            entity.get("type") == "DIMENSION"
            for drawing in drawings
            if isinstance(drawing, dict)
            for entity in drawing.get("entities", [])
            if isinstance(entity, dict)
        )
        else "unavailable",
        "gdt": "complete"
        if any(
            entity.get("type") in {"GDT", "TOLERANCE"}
            for drawing in drawings
            if isinstance(drawing, dict)
            for entity in drawing.get("entities", [])
            if isinstance(entity, dict)
        )
        else "unavailable",
    }
    source = metadata["source"]
    existing_extraction = metadata.get("extraction", {})
    if not isinstance(existing_extraction, dict):
        existing_extraction = {}
    existing_sections = existing_extraction.get("sections", {})
    existing_capabilities = existing_extraction.get("capabilities", {})
    existing_provenance = existing_extraction.get("provenance", {})
    metadata["extraction"] = {
        "sections": {**sections, **existing_sections}
        if isinstance(existing_sections, dict)
        else sections,
        "capabilities": {**capabilities, **existing_capabilities}
        if isinstance(existing_capabilities, dict)
        else capabilities,
        "provenance": {
            "source_system": source["system"],
            "worker": source["worker"],
            "worker_version": source["worker_version"],
            "method": "native" if source["system"] != "step" else "neutral_geometry",
            "configuration": properties.get("configuration"),
            "confidence": 1.0 if quality["geometry"] == "complete" else 0.5,
            **(existing_provenance if isinstance(existing_provenance, dict) else {}),
        },
    }
    return metadata


def _section_evidence(status: str, count: int, method: str) -> dict:
    return {
        "status": status,
        "records_found": max(0, count),
        "records_skipped": 0,
        "method": str(method),
    }


def _non_negative_int(value: object, *, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return default


class MetadataService:
    """Own the CAD-neutral metadata ingestion workflow."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def status_for(metadata: dict) -> str:
        quality = metadata["quality"]
        if (
            metadata["identity"]["kind"] == "assembly"
            and metadata.get("assembly", {}).get("is_assembly") is True
            and metadata.get("assembly", {}).get("components")
            and quality["geometry"] in {"partial", "unavailable"}
        ):
            # Native occurrence/BOM evidence remains usable without aggregate assembly geometry.
            return "PARTIAL"
        if quality["geometry"] != "complete" and not (
            quality["geometry"] == "not_applicable" and metadata.get("drawings")
        ):
            return "INVALID"
        relevant = [quality["physical"], quality["features"]]
        if "metadata" in quality:
            relevant.append(quality["metadata"])
        if metadata["identity"]["kind"] == "assembly":
            relevant.append(quality["assembly"])
        complete = all(value in {"complete", "not_applicable"} for value in relevant)
        return "READY" if complete else "PARTIAL"

    def ingest(
        self,
        *,
        revision: Revision,
        snapshot: RevisionContentSnapshot | None = None,
        raw_artifact: ArtifactFile,
        metadata: dict,
    ) -> MetadataExtraction:
        """Validate and append one immutable extraction, then update current status."""
        # Reject malformed worker output before performing any persistence lookup.
        # This also keeps the validator usable with detached/test revision values.
        validate_part_metadata(metadata)
        validate_engineering_metadata(metadata)
        part_id = getattr(revision, "part_id", None)
        item = self.db.get(Item, part_id) if part_id is not None else None
        if item is not None:
            metadata = canonicalize_item_kind(metadata, item.part_type)
            metadata = add_drawing_identity_warnings(metadata, item, revision)
        metadata = normalize_search_properties(metadata)
        metadata = enhance_extraction_metadata(metadata)
        if isinstance(getattr(revision, "derived_metadata_json", None), dict) and (
            "partMetadata" in revision.derived_metadata_json
        ):
            revision.derived_metadata_json["partMetadata"] = metadata
        validate_part_metadata(metadata)
        validate_engineering_metadata(metadata)
        source = metadata["source"]
        quality = metadata["quality"]
        status = self.status_for(metadata)
        derived = build_derived_metadata(metadata)
        geometry = metadata["geometry"]
        bbox = geometry["bounding_box"]
        shape = geometry["shape"]
        topology = geometry.get("topology", {})
        physical = metadata.get("physical", {})
        feature_summary = metadata.get("features", {}).get("summary", {})
        hole_diameters = derived.get("hole_diameters_mm", [])
        extracted_at = datetime.fromisoformat(source["extracted_at"].replace("Z", "+00:00"))
        extraction = MetadataExtraction(
            id=uuid.uuid4(),
            revision_id=revision.id,
            snapshot_id=snapshot.id if snapshot is not None else None,
            raw_artifact_id=raw_artifact.id,
            schema_version=metadata["schema_version"],
            worker_type=source["worker"],
            worker_version=source["worker_version"],
            source_system=source["system"],
            source_system_version=source.get("system_version"),
            status=status,
            extracted_at=extracted_at,
            metadata_json=metadata,
            derived_json=derived,
            kind=metadata["identity"]["kind"],
            material=derived.get("material_search"),
            bbox_x_mm=bbox["x_mm"],
            bbox_y_mm=bbox["y_mm"],
            bbox_z_mm=bbox["z_mm"],
            bbox_min_mm=derived["bbox_min_mm"],
            bbox_mid_mm=derived["bbox_mid_mm"],
            bbox_max_mm=derived["bbox_max_mm"],
            volume_mm3=shape["volume_mm3"],
            surface_area_mm2=shape["surface_area_mm2"],
            mass_kg=physical.get("mass_kg"),
            density_kg_m3=physical.get("density_kg_m3"),
            hole_diameter_min_mm=min(hole_diameters) if hole_diameters else None,
            hole_diameter_max_mm=max(hole_diameters) if hole_diameters else None,
            drawing_count=derived.get("drawing_count"),
            tolerance_count=derived.get("tolerance_count"),
            instance_count=derived.get("instance_count"),
            solid_count=topology.get("solid_count"),
            face_count=topology.get("face_count"),
            edge_count=topology.get("edge_count"),
            hole_count=feature_summary.get("hole_count"),
            fillet_count=feature_summary.get("fillet_count"),
            chamfer_count=feature_summary.get("chamfer_count"),
            extrusion_count=feature_summary.get("extrusion_count"),
            revolution_count=feature_summary.get("revolution_count"),
            pocket_count=feature_summary.get("pocket_count"),
            pattern_count=feature_summary.get("pattern_count"),
            thread_count=feature_summary.get("thread_count"),
            warnings_json=list(quality["warnings"]),
            errors_json=[],
        )
        self.db.add(extraction)
        # ``revisions.current_metadata_extraction_id`` is a circular foreign key
        # declared with ``use_alter=True``. Persist the extraction first so unit-of-work
        # ordering cannot depend on the order in which model modules were imported.
        self.db.flush()
        revision.metadata_status = status
        revision.current_metadata_extraction_id = extraction.id
        return extraction
