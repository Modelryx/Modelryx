import hashlib
import json
import math
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.api.items import readable_item_revision
from modelryx.core.audit import user_for_principal
from modelryx.core.auth import Principal
from modelryx.metadata.array_quality import ARRAY_GEOMETRY_WARNING, has_unverified_array_geometry
from modelryx.models import (
    ArtifactFile,
    BomLine,
    BomViewRevision,
    Item,
    Revision,
    RevisionComparison,
    RevisionComparisonChange,
    RevisionComparisonDomain,
)
from modelryx.services.artifact_content import current_artifact_files

from .models import (
    BomComparisonResult,
    BomMatch,
    ComparisonChangeResponse,
    ComparisonDomainName,
    ComparisonDomainResponse,
    DrawingComparisonResult,
    FeatureComparisonResult,
    FeatureMatch,
    GeometryAlignment,
    GeometryArtifactReferences,
    GeometryComparisonResult,
    NormalizedFeature,
    RevisionComparisonResponse,
)

COMPARISON_ENGINE_VERSION = "phase5-v6"
METADATA_SCHEMA_VERSION = "part-metadata-v1"
RULES_VERSION = "phase5-rules-v1"
SYNC_DOMAINS = {
    "METADATA",
    "PHYSICAL_PROPERTIES",
    "FILES",
    "GEOMETRY",
    "FEATURES",
    "BOM",
    "DRAWING",
}
TRANSFORM_TOLERANCE = 1e-6
REASON_FIELDS = {"change_reason", "revision_reason", "reason", "comment"}
PHYSICAL_TOKENS = {
    "material",
    "mass",
    "volume",
    "surface_area",
    "density",
    "center_of_mass",
    "moments_of_inertia",
    "bounding_box",
}


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _artifacts(db: Session, revision_id: uuid.UUID) -> list[ArtifactFile]:
    current = current_artifact_files(
        db,
        revision_id,
        states={"VERIFIED", "READY"},
    )
    if current:
        return current
    # Compatibility for revisions created before Artifact snapshots.
    return list(
        db.scalars(
            select(ArtifactFile)
            .where(
                ArtifactFile.revision_id == revision_id,
                ArtifactFile.state.in_(["VERIFIED", "READY"]),
            )
            .order_by(ArtifactFile.kind, ArtifactFile.original_filename, ArtifactFile.id)
        )
    )


def _bom_occurrences(db: Session, revision: Revision) -> list[dict]:
    occurrences: list[dict] = []

    def visit(
        parent_revision: Revision,
        parent_path: str | None,
        ancestor_revision_ids: frozenset[uuid.UUID],
    ) -> None:
        bom_view = db.scalar(
            select(BomViewRevision)
            .where(BomViewRevision.revision_id == parent_revision.id)
            .order_by(BomViewRevision.sequence.desc())
            .limit(1)
        )
        if bom_view is None:
            return
        rows = db.execute(
            select(BomLine, Revision, Item)
            .join(Revision, Revision.id == BomLine.child_revision_id)
            .join(Item, Item.id == Revision.part_id)
            .where(BomLine.bom_view_revision_id == bom_view.id)
            .order_by(BomLine.sequence, BomLine.occurrence_key, BomLine.id)
        ).all()
        for line, child_revision, component in rows:
            hierarchy_path = (
                f"{parent_path}/{line.occurrence_key}"
                if parent_path
                else line.occurrence_key
            )
            occurrences.append(
                {
                    # Hierarchy-qualified keys remain unique when the same
                    # native occurrence key is reused by different subassemblies.
                    "occurrence_key": hierarchy_path,
                    "native_occurrence_key": line.occurrence_key,
                    "hierarchy_path": hierarchy_path,
                    "parent_occurrence_key": parent_path,
                    "component_item_id": str(component.id),
                    "component_part_number": component.part_number,
                    "component_revision_id": str(child_revision.id),
                    "component_revision_code": child_revision.revision_code,
                    "source_reference": line.source_reference or line.occurrence_key,
                    "reference_designator": line.reference_designator or line.occurrence_key,
                    "transform": line.transform,
                    "is_suppressed": line.is_suppressed,
                    "is_visible": line.is_visible,
                    "properties": line.properties_json or {},
                    "quantity": line.quantity,
                }
            )
            if child_revision.id not in ancestor_revision_ids:
                visit(
                    child_revision,
                    hierarchy_path,
                    ancestor_revision_ids | {child_revision.id},
                )

    visit(revision, None, frozenset({revision.id}))
    return occurrences


def _has_bom_view(db: Session, revision_id: uuid.UUID) -> bool:
    return db.scalar(
        select(BomViewRevision.id).where(BomViewRevision.revision_id == revision_id).limit(1)
    ) is not None


def revision_fingerprint(db: Session, revision: Revision) -> str:
    artifacts = [
        {
            "id": str(row.id),
            "kind": row.kind,
            "filename": row.original_filename,
            "sha256": row.sha256,
            "size": row.byte_size,
            "state": row.state,
        }
        for row in _artifacts(db, revision.id)
    ]
    return _canonical_hash(
        {
            "id": str(revision.id),
            "state": revision.state,
            "metadata": revision.metadata_json,
            "derived_metadata": revision.derived_metadata_json,
            "package_sha256": revision.package_sha256,
            "metadata_status": revision.metadata_status,
            "artifacts": artifacts,
            "bom": _bom_occurrences(db, revision),
        }
    )


def _flatten(value: object, prefix: str = "") -> dict[str, object]:
    if not isinstance(value, dict):
        return {prefix or "value": value}
    flattened: dict[str, object] = {}
    for key, entry in sorted(value.items()):
        if str(key).casefold() in REASON_FIELDS:
            continue
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(entry, dict):
            flattened.update(_flatten(entry, path))
        else:
            flattened[path] = entry
    return flattened


def _revision_metadata(revision: Revision) -> dict:
    value = revision.derived_metadata_json or revision.metadata_json or {}
    nested = value.get("partMetadata") if isinstance(value, dict) else None
    return nested if isinstance(nested, dict) else value


def _uncertain_array_measurements(before: Revision, after: Revision) -> bool:
    return any(
        has_unverified_array_geometry(_revision_metadata(revision)) for revision in (before, after)
    )


def _unit(path: str) -> str | None:
    lowered = path.casefold()
    for suffix, unit in (
        ("_mm3", "mm3"),
        ("_mm2", "mm2"),
        ("_mm", "mm"),
        ("mass_kg", "kg"),
        ("density_kg_m3", "kg/m3"),
    ):
        if lowered.endswith(suffix):
            return unit
    return None


def _is_physical(path: str) -> bool:
    lowered = path.casefold()
    return any(token in lowered for token in PHYSICAL_TOKENS)


def _change_values(
    old: object | None, new: object | None
) -> tuple[str, float | None, float | None]:
    change_type = "ADDED" if old is None else "REMOVED" if new is None else "MODIFIED"
    if (
        isinstance(old, (int, float))
        and not isinstance(old, bool)
        and isinstance(new, (int, float))
        and not isinstance(new, bool)
        and math.isfinite(float(old))
        and math.isfinite(float(new))
    ):
        delta = float(new) - float(old)
        percent = delta / abs(float(old)) * 100 if float(old) != 0 else None
        return change_type, delta, percent
    return change_type, None, None


def _metadata_changes(before: Revision, after: Revision, domain: str) -> list[dict]:
    old = _flatten(_revision_metadata(before))
    new = _flatten(_revision_metadata(after))
    uncertain = _uncertain_array_measurements(before, after)
    rows = []
    for path in sorted(old.keys() | new.keys()):
        if uncertain and path.startswith("geometry."):
            continue
        physical = _is_physical(path)
        if (domain == "PHYSICAL_PROPERTIES") != physical or old.get(path) == new.get(path):
            continue
        change_type, delta, percent = _change_values(old.get(path), new.get(path))
        material = "material" in path.casefold()
        rows.append(
            {
                "change_key": f"{domain.casefold()}:{path}",
                "change_type": change_type,
                "property_path": path,
                "old_value": old.get(path),
                "new_value": new.get(path),
                "unit": _unit(path),
                "delta": delta,
                "percent_change": percent,
                "significance": "MAJOR" if material else "MINOR",
                "categories": (
                    ["MATERIAL"] if material else ["DIMENSIONAL" if physical else "METADATA"]
                ),
                "confidence": 1.0,
                "provenance": "EXACT",
                "metadata": {},
            }
        )
    return rows


def _geometry_changes(before: Revision, after: Revision) -> list[dict]:
    if _uncertain_array_measurements(before, after):
        return []
    old = _flatten(_revision_metadata(before).get("geometry", {}), "geometry")
    new = _flatten(_revision_metadata(after).get("geometry", {}), "geometry")
    rows = []
    for path in sorted(old.keys() | new.keys()):
        if old.get(path) == new.get(path):
            continue
        change_type, delta, percent = _change_values(old.get(path), new.get(path))
        rows.append(
            {
                "change_key": f"geometry:{path}",
                "change_type": change_type,
                "property_path": path,
                "old_value": old.get(path),
                "new_value": new.get(path),
                "unit": _unit(path),
                "delta": delta,
                "percent_change": percent,
                "significance": "MODERATE",
                "categories": ["GEOMETRY", "DIMENSIONAL"],
                "confidence": 1.0,
                "provenance": "EXACT_METADATA",
                "metadata": {},
            }
        )
    return rows


def _preview_url(db: Session, revision: Revision) -> str | None:
    artifact = next(
        iter(
            current_artifact_files(
                db,
                revision.id,
                artifact_types={"GLB"},
                states={"VERIFIED", "READY"},
            )
        ),
        None,
    )
    if artifact is None:
        artifact = db.scalar(
            select(ArtifactFile)
            .where(
                ArtifactFile.revision_id == revision.id,
                ArtifactFile.kind == "GLB",
                ArtifactFile.state.in_(["VERIFIED", "READY"]),
            )
            .order_by(ArtifactFile.created_at.desc())
        )
    return f"/api/v1/revisions/{revision.id}/preview/model" if artifact else None


def _geometry_result(
    db: Session, before: Revision, after: Revision, changes: list[dict]
) -> GeometryComparisonResult:
    uncertain = _uncertain_array_measurements(before, after)
    available = not uncertain and bool(_revision_metadata(before).get("geometry")) and bool(
        _revision_metadata(after).get("geometry")
    )
    return GeometryComparisonResult(
        alignment=GeometryAlignment(
            method="CAD_COORDINATE_SYSTEM" if available else "UNAVAILABLE",
            confidence=1.0 if available else 0.0,
        ),
        geometry_changed=bool(changes) if available else None,
        comparison_level="METADATA_METRICS" if available else "UNAVAILABLE",
        artifacts=GeometryArtifactReferences(
            from_preview_url=_preview_url(db, before),
            to_preview_url=_preview_url(db, after),
        ),
        limitations=(
            [
                "Boolean common, added, and removed geometry is unavailable; "
                "the result compares normalized geometry metrics"
            ]
            if available
            else [ARRAY_GEOMETRY_WARNING] if uncertain
            else ["Normalized geometry metadata is unavailable for one or both revisions"]
        ),
    )


def _feature_type(value: object) -> str:
    normalized = str(value or "UNKNOWN_FEATURE").upper()
    aliases = {"EXTRUSION": "EXTRUSION", "REVOLUTION": "REVOLVE", "REVOLVE": "REVOLVE"}
    allowed = {
        "HOLE",
        "POCKET",
        "PAD",
        "EXTRUSION",
        "FILLET",
        "CHAMFER",
        "BOSS",
        "SLOT",
        "PATTERN",
        "SHELL",
        "REVOLVE",
        "THREAD",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in allowed else "UNKNOWN_FEATURE"


def _normalized_features(revision: Revision) -> list[NormalizedFeature]:
    features = _revision_metadata(revision).get("features", {})
    items = features.get("items", []) if isinstance(features, dict) else []
    return [
        NormalizedFeature(
            persistent_id=str(row["persistent_id"]) if row.get("persistent_id") else None,
            name=str(row["name"]) if row.get("name") else None,
            type=_feature_type(row.get("type")),
            properties=(
                row.get("properties", {}) if isinstance(row.get("properties", {}), dict) else {}
            ),
        )
        for row in items
        if isinstance(row, dict)
    ]


def _feature_signature(feature: NormalizedFeature) -> str:
    return _canonical_hash(
        {
            "type": feature.type,
            "name": feature.name.casefold() if feature.name else None,
            "properties": feature.properties,
        }
    )


def _match_features(
    before: list[NormalizedFeature], after: list[NormalizedFeature]
) -> tuple[list[tuple[NormalizedFeature, NormalizedFeature, str, float]], list, list]:
    matches = []
    remaining_before = list(before)
    remaining_after = list(after)

    def match_by(key, method: str, confidence: float) -> None:
        nonlocal remaining_before, remaining_after
        after_by_key: dict[str, list[NormalizedFeature]] = {}
        for feature in remaining_after:
            value = key(feature)
            if value:
                after_by_key.setdefault(value, []).append(feature)
        consumed_before = []
        consumed_after = []
        for feature in remaining_before:
            value = key(feature)
            candidates = after_by_key.get(value, []) if value else []
            if len(candidates) != 1:
                continue
            candidate = candidates.pop()
            matches.append((feature, candidate, method, confidence))
            consumed_before.append(feature)
            consumed_after.append(candidate)
        remaining_before = [row for row in remaining_before if row not in consumed_before]
        remaining_after = [row for row in remaining_after if row not in consumed_after]

    match_by(lambda row: row.persistent_id, "PERSISTENT_ID", 1.0)
    match_by(_feature_signature, "SIGNATURE", 1.0)
    match_by(
        lambda row: f"{row.type}:{row.name.casefold()}" if row.name else None,
        "TYPE_AND_NAME",
        0.9,
    )
    match_by(lambda row: row.type, "TYPE_ONLY", 0.65)
    return matches, remaining_before, remaining_after


def _feature_changes(
    before: Revision, after: Revision
) -> tuple[list[dict], FeatureComparisonResult]:
    old_features = _normalized_features(before)
    new_features = _normalized_features(after)
    old_metadata = _revision_metadata(before).get("features", {})
    new_metadata = _revision_metadata(after).get("features", {})
    source = new_metadata.get("source") if isinstance(new_metadata, dict) else None
    if not old_features and not new_features:
        old_summary = old_metadata.get("summary", {}) if isinstance(old_metadata, dict) else {}
        new_summary = new_metadata.get("summary", {}) if isinstance(new_metadata, dict) else {}
        changes = []
        for key in sorted(old_summary.keys() | new_summary.keys()):
            if old_summary.get(key) == new_summary.get(key):
                continue
            change_type, delta, percent = _change_values(old_summary.get(key), new_summary.get(key))
            changes.append(
                {
                    "change_key": f"features:summary:{key}",
                    "change_type": change_type,
                    "property_path": f"features.summary.{key}",
                    "old_value": old_summary.get(key),
                    "new_value": new_summary.get(key),
                    "unit": None,
                    "delta": delta,
                    "percent_change": percent,
                    "significance": "MODERATE",
                    "categories": ["FEATURE", "GEOMETRY"],
                    "confidence": 1.0,
                    "provenance": "EXACT_SUMMARY",
                    "metadata": {"feature_type": key.removesuffix("_count").upper()},
                }
            )
        level = "SUMMARY" if old_summary or new_summary else "UNAVAILABLE"
        return changes, FeatureComparisonResult(
            comparison_level=level,
            source=source,
            limitations=(
                [
                    "Only aggregate feature counts are available; "
                    "individual features were not matched"
                ]
                if level == "SUMMARY"
                else ["Normalized feature data is unavailable for both revisions"]
            ),
        )

    matches, removed, added = _match_features(old_features, new_features)
    changes = []
    result_matches = []
    modified_count = 0
    for old, new, method, confidence in matches:
        result_matches.append(
            FeatureMatch(
                from_persistent_id=old.persistent_id,
                to_persistent_id=new.persistent_id,
                method=method,
                confidence=confidence,
            )
        )
        if old == new:
            continue
        modified_count += 1
        key = old.persistent_id or new.persistent_id or _feature_signature(old)
        changes.append(
            {
                "change_key": f"features:modified:{key}",
                "change_type": "MODIFIED",
                "property_path": f"features.{new.type}.{new.name or key}",
                "old_value": old.model_dump(mode="json"),
                "new_value": new.model_dump(mode="json"),
                "unit": None,
                "delta": None,
                "percent_change": None,
                "significance": "MODERATE",
                "categories": ["FEATURE", "GEOMETRY"],
                "confidence": confidence,
                "provenance": f"MATCH_{method}",
                "metadata": {"match_method": method, "feature_type": new.type},
            }
        )
    for change_type, rows in (("REMOVED", removed), ("ADDED", added)):
        for index, feature in enumerate(rows):
            key = feature.persistent_id or _feature_signature(feature)
            changes.append(
                {
                    "change_key": f"features:{change_type.casefold()}:{key}:{index}",
                    "change_type": change_type,
                    "property_path": f"features.{feature.type}.{feature.name or key}",
                    "old_value": (
                        feature.model_dump(mode="json") if change_type == "REMOVED" else None
                    ),
                    "new_value": (
                        feature.model_dump(mode="json") if change_type == "ADDED" else None
                    ),
                    "unit": None,
                    "delta": None,
                    "percent_change": None,
                    "significance": "MODERATE",
                    "categories": ["FEATURE", "GEOMETRY"],
                    "confidence": 1.0,
                    "provenance": "UNMATCHED",
                    "metadata": {"feature_type": feature.type},
                }
            )
    return changes, FeatureComparisonResult(
        comparison_level="INDIVIDUAL",
        source=source,
        added_count=len(added),
        removed_count=len(removed),
        modified_count=modified_count,
        matches=result_matches,
        limitations=(
            ["Feature matches below 0.8 confidence are possible matches and require review"]
            if any(row.confidence < 0.8 for row in result_matches)
            else []
        ),
    )


def _bom_match_key(row: dict) -> str | None:
    reference = str(row.get("reference_designator") or "").strip().casefold()
    component = row.get("component_item_id")
    return f"{component}:{reference}" if component and reference else None


def _bom_component_key(row: dict) -> str | None:
    component = row.get("component_item_id")
    return str(component) if component else None


def _bom_native_occurrence_key(row: dict) -> str | None:
    properties = row.get("properties")
    if not isinstance(properties, dict):
        return None
    occurrence_ids = properties.get("occurrence_ids")
    if not isinstance(occurrence_ids, list):
        return None
    normalized = sorted(
        {str(value).strip().casefold() for value in occurrence_ids if str(value).strip()}
    )
    return "\x1f".join(normalized) if normalized else None


def _transform_changed(before: object, after: object) -> bool:
    if not isinstance(before, list) or not isinstance(after, list) or len(before) != len(after):
        return before != after
    return any(
        not isinstance(left, (int, float))
        or not isinstance(right, (int, float))
        or abs(float(left) - float(right)) > TRANSFORM_TOLERANCE
        for left, right in zip(before, after, strict=True)
    )


def _bom_changes(
    db: Session, before: Revision, after: Revision
) -> tuple[list[dict], BomComparisonResult]:
    old_rows = _bom_occurrences(db, before)
    new_rows = _bom_occurrences(db, after)
    if not _has_bom_view(db, before.id) or not _has_bom_view(db, after.id):
        return [], BomComparisonResult(
            comparison_level="UNAVAILABLE",
            limitations=["A committed BOM is unavailable for one or both revisions"],
        )

    old_by_key = {row["occurrence_key"]: row for row in old_rows}
    new_by_key = {row["occurrence_key"]: row for row in new_rows}
    matches: list[tuple[dict, dict, str, float]] = []
    consumed_old: set[str] = set()
    consumed_new: set[str] = set()
    for key in sorted(old_by_key.keys() & new_by_key.keys()):
        matches.append((old_by_key[key], new_by_key[key], "OCCURRENCE_KEY", 1.0))
        consumed_old.add(key)
        consumed_new.add(key)

    remaining_old = [row for row in old_rows if row["occurrence_key"] not in consumed_old]
    remaining_new = [row for row in new_rows if row["occurrence_key"] not in consumed_new]
    old_fallback: dict[str, list[dict]] = {}
    new_fallback: dict[str, list[dict]] = {}
    for row in remaining_old:
        if key := _bom_match_key(row):
            old_fallback.setdefault(key, []).append(row)
    for row in remaining_new:
        if key := _bom_match_key(row):
            new_fallback.setdefault(key, []).append(row)

    ambiguous: list[tuple[str, list[dict], list[dict]]] = []
    for key in sorted(old_fallback.keys() & new_fallback.keys()):
        old_candidates = old_fallback[key]
        new_candidates = new_fallback[key]
        if len(old_candidates) == len(new_candidates) == 1:
            old_row, new_row = old_candidates[0], new_candidates[0]
            matches.append((old_row, new_row, "COMPONENT_AND_REFERENCE", 0.85))
            consumed_old.add(old_row["occurrence_key"])
            consumed_new.add(new_row["occurrence_key"])
        # Repeated component/reference pairs are not declared ambiguous yet.
        # A stronger native occurrence-ID match may still resolve them below.

    # AutoCAD grouped BOM rows retain the source occurrence IDs even when a
    # block definition, hierarchy path, or display label is renamed. Match an
    # exact ID set only when it is unique on both sides so repeated components
    # can be resolved without guessing.
    remaining_old = [row for row in old_rows if row["occurrence_key"] not in consumed_old]
    remaining_new = [row for row in new_rows if row["occurrence_key"] not in consumed_new]
    old_native: dict[str, list[dict]] = {}
    new_native: dict[str, list[dict]] = {}
    for row in remaining_old:
        if key := _bom_native_occurrence_key(row):
            old_native.setdefault(key, []).append(row)
    for row in remaining_new:
        if key := _bom_native_occurrence_key(row):
            new_native.setdefault(key, []).append(row)
    for key in sorted(old_native.keys() & new_native.keys()):
        old_candidates = old_native[key]
        new_candidates = new_native[key]
        if len(old_candidates) == len(new_candidates) == 1:
            old_row, new_row = old_candidates[0], new_candidates[0]
            matches.append((old_row, new_row, "NATIVE_OCCURRENCE_IDS", 0.95))
            consumed_old.add(old_row["occurrence_key"])
            consumed_new.add(new_row["occurrence_key"])

    # Inventor commonly derives an occurrence/reference name from the component
    # filename.  A renamed or relocated definition can therefore change both the
    # occurrence key and reference designator even though it is still the same
    # Modelryx component.  Match by component only when it is unique on both
    # sides; repeated instances remain explicitly ambiguous.
    remaining_old = [row for row in old_rows if row["occurrence_key"] not in consumed_old]
    remaining_new = [row for row in new_rows if row["occurrence_key"] not in consumed_new]
    old_components: dict[str, list[dict]] = {}
    new_components: dict[str, list[dict]] = {}
    for row in remaining_old:
        if key := _bom_component_key(row):
            old_components.setdefault(key, []).append(row)
    for row in remaining_new:
        if key := _bom_component_key(row):
            new_components.setdefault(key, []).append(row)
    for key in sorted(old_components.keys() & new_components.keys()):
        old_candidates = old_components[key]
        new_candidates = new_components[key]
        if len(old_candidates) == len(new_candidates) == 1:
            old_row, new_row = old_candidates[0], new_candidates[0]
            matches.append((old_row, new_row, "COMPONENT_ONLY", 0.8))
            consumed_old.add(old_row["occurrence_key"])
            consumed_new.add(new_row["occurrence_key"])
        else:
            ambiguous.append((f"component:{key}", old_candidates, new_candidates))
            consumed_old.update(row["occurrence_key"] for row in old_candidates)
            consumed_new.update(row["occurrence_key"] for row in new_candidates)

    removed = [row for row in old_rows if row["occurrence_key"] not in consumed_old]
    added = [row for row in new_rows if row["occurrence_key"] not in consumed_new]
    changes: list[dict] = []
    result_matches: list[BomMatch] = []
    modified_occurrences: set[str] = set()
    moved_occurrences: set[str] = set()
    replaced_occurrences: set[str] = set()

    def add_change(
        old: dict,
        new: dict,
        field: str,
        change_type: str,
        confidence: float,
        method: str,
    ) -> None:
        key = new["occurrence_key"]
        changes.append(
            {
                "change_key": f"bom:{key}:{field}",
                "change_type": change_type,
                "property_path": f"bom.{new['hierarchy_path']}.{field}",
                "old_value": old.get(field),
                "new_value": new.get(field),
                "unit": None,
                "delta": None,
                "percent_change": None,
                "significance": "MAJOR" if change_type == "REPLACED" else "MODERATE",
                "categories": ["BOM", "ASSEMBLY"],
                "confidence": confidence,
                "provenance": f"MATCH_{method}",
                "metadata": {
                    "occurrence_key": key,
                    "hierarchy_path": new["hierarchy_path"],
                    "from_occurrence_key": old["occurrence_key"],
                    "to_occurrence_key": new["occurrence_key"],
                    "from_native_occurrence_key": old.get("native_occurrence_key"),
                    "to_native_occurrence_key": new.get("native_occurrence_key"),
                    "match_method": method,
                },
            }
        )

    for old, new, method, confidence in matches:
        result_matches.append(
            BomMatch(
                from_occurrence_key=old["occurrence_key"],
                to_occurrence_key=new["occurrence_key"],
                method=method,
                confidence=confidence,
            )
        )
        identity_fields = (
            "component_item_id",
            "component_revision_id",
        )
        if any(old[field] != new[field] for field in identity_fields):
            add_change(old, new, "component", "REPLACED", confidence, method)
            changes[-1]["old_value"] = {field: old[field] for field in identity_fields}
            changes[-1]["new_value"] = {field: new[field] for field in identity_fields}
            replaced_occurrences.add(new["occurrence_key"])
        if old["hierarchy_path"] != new["hierarchy_path"]:
            add_change(old, new, "hierarchy_path", "MOVED", confidence, method)
            moved_occurrences.add(new["occurrence_key"])
        if _transform_changed(old.get("transform"), new.get("transform")):
            add_change(old, new, "transform", "MOVED", confidence, method)
            moved_occurrences.add(new["occurrence_key"])
        for field in (
            "quantity",
            "reference_designator",
            "is_suppressed",
            "is_visible",
            "properties",
        ):
            if old.get(field) != new.get(field):
                add_change(old, new, field, "MODIFIED", confidence, method)
                modified_occurrences.add(new["occurrence_key"])

    for change_type, rows in (("REMOVED", removed), ("ADDED", added)):
        for row in rows:
            key = row["occurrence_key"]
            changes.append(
                {
                    "change_key": f"bom:{change_type.casefold()}:{key}",
                    "change_type": change_type,
                    "property_path": f"bom.{row['hierarchy_path']}",
                    "old_value": row if change_type == "REMOVED" else None,
                    "new_value": row if change_type == "ADDED" else None,
                    "unit": None,
                    "delta": None,
                    "percent_change": None,
                    "significance": "MAJOR",
                    "categories": ["BOM", "ASSEMBLY"],
                    "confidence": 1.0,
                    "provenance": "UNMATCHED_OCCURRENCE",
                    "metadata": {
                        "occurrence_key": key,
                        "hierarchy_path": row["hierarchy_path"],
                        "from_occurrence_key": key if change_type == "REMOVED" else None,
                        "to_occurrence_key": key if change_type == "ADDED" else None,
                        "from_native_occurrence_key": (
                            row.get("native_occurrence_key") if change_type == "REMOVED" else None
                        ),
                        "to_native_occurrence_key": (
                            row.get("native_occurrence_key") if change_type == "ADDED" else None
                        ),
                    },
                }
            )
            result_matches.append(
                BomMatch(
                    from_occurrence_key=key if change_type == "REMOVED" else None,
                    to_occurrence_key=key if change_type == "ADDED" else None,
                    method="UNMATCHED",
                    confidence=1.0,
                )
            )

    for index, (key, old_candidates, new_candidates) in enumerate(ambiguous):
        changes.append(
            {
                "change_key": f"bom:ambiguous:{index}:{_canonical_hash(key)[:12]}",
                "change_type": "UNKNOWN",
                "property_path": "bom.ambiguous_match",
                "old_value": [row["occurrence_key"] for row in old_candidates],
                "new_value": [row["occurrence_key"] for row in new_candidates],
                "unit": None,
                "delta": None,
                "percent_change": None,
                "significance": "MODERATE",
                "categories": ["BOM", "ASSEMBLY"],
                "confidence": 0.0,
                "provenance": "AMBIGUOUS_COMPONENT_AND_REFERENCE",
                "metadata": {"fallback_identity": key},
            }
        )
        result_matches.append(BomMatch(method="AMBIGUOUS", confidence=0.0))

    limitations = (
        ["Some changed occurrence keys have ambiguous component/reference matches"]
        if ambiguous
        else []
    )
    return changes, BomComparisonResult(
        comparison_level="AUTHORITATIVE_OCCURRENCES",
        added_count=len(added),
        removed_count=len(removed),
        modified_count=len(modified_occurrences),
        moved_count=len(moved_occurrences),
        replaced_count=len(replaced_occurrences),
        ambiguous_count=len(ambiguous),
        matches=result_matches,
        limitations=limitations,
    )


def _drawing_metadata(revision: Revision) -> list[dict]:
    value = _revision_metadata(revision).get("drawings", [])
    if isinstance(value, dict):
        value = value.get("items", [])
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _drawing_records(db: Session, revision: Revision) -> list[dict]:
    metadata = _drawing_metadata(revision)
    metadata_by_artifact = {
        str(row.get("artifact_id")): row for row in metadata if row.get("artifact_id")
    }
    metadata_by_filename = {
        str(row.get("filename", "")).strip().casefold(): row
        for row in metadata
        if row.get("filename")
    }
    records = []
    seen_metadata: set[int] = set()
    for artifact in _artifacts(db, revision.id):
        if artifact.kind not in {"DRAWING", "PDF"} and artifact.mime_type != "application/pdf":
            continue
        drawing = metadata_by_artifact.get(str(artifact.id)) or metadata_by_filename.get(
            artifact.original_filename.casefold()
        )
        if drawing is not None:
            seen_metadata.add(id(drawing))
        records.append(
            {
                "identity": str(
                    (drawing or {}).get("stable_id") or artifact.original_filename.casefold()
                ),
                "artifact_id": str(artifact.id),
                "filename": artifact.original_filename,
                "sha256": artifact.sha256,
                "mime_type": artifact.mime_type,
                "byte_size": artifact.byte_size,
                "page_count": (drawing or {}).get("page_count"),
                "worker_contract_version": (drawing or {}).get("worker_contract_version"),
                "sections": (drawing or {}).get("sections", []),
                "entities": (drawing or {}).get("entities", []),
            }
        )
    for drawing in metadata:
        if id(drawing) in seen_metadata:
            continue
        stable_id = drawing.get("stable_id") or drawing.get("filename")
        if not stable_id:
            continue
        records.append(
            {
                "identity": str(stable_id).casefold(),
                "artifact_id": drawing.get("artifact_id"),
                "filename": drawing.get("filename"),
                "sha256": drawing.get("sha256"),
                "mime_type": drawing.get("mime_type"),
                "byte_size": drawing.get("byte_size"),
                "page_count": drawing.get("page_count"),
                "worker_contract_version": drawing.get("worker_contract_version"),
                "sections": drawing.get("sections", []),
                "entities": drawing.get("entities", []),
            }
        )
    return sorted(records, key=lambda row: (row["identity"], str(row.get("artifact_id") or "")))


def _normalized_drawing_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _drawing_changes(
    db: Session, before: Revision, after: Revision
) -> tuple[list[dict], DrawingComparisonResult]:
    old_records = _drawing_records(db, before)
    new_records = _drawing_records(db, after)
    if not old_records and not new_records:
        return [], DrawingComparisonResult(
            comparison_level="UNAVAILABLE",
            capabilities={"file": False, "text": False, "structured": False, "visual": False},
            limitations=["No verified drawing artifact or normalized drawing record is available"],
        )
    old_by_id = {row["identity"]: row for row in old_records}
    new_by_id = {row["identity"]: row for row in new_records}
    changes: list[dict] = []
    file_count = 0
    text_count = 0
    structured_count = 0
    matched_count = 0
    has_text = False
    has_structured = False

    def append_change(
        identity: str,
        field: str,
        old_value: object,
        new_value: object,
        change_type: str,
        provenance: str,
        metadata: dict,
        significance: str = "MODERATE",
    ) -> None:
        changes.append(
            {
                "change_key": f"drawing:{identity}:{field}",
                "change_type": change_type,
                "property_path": f"drawings.{identity}.{field}",
                "old_value": old_value,
                "new_value": new_value,
                "unit": metadata.get("unit"),
                "delta": None,
                "percent_change": None,
                "significance": significance,
                "categories": ["DRAWING"],
                "confidence": 1.0,
                "provenance": provenance,
                "metadata": {"drawing_identity": identity, **metadata},
            }
        )

    for identity in sorted(old_by_id.keys() | new_by_id.keys()):
        old, new = old_by_id.get(identity), new_by_id.get(identity)
        if old is None or new is None:
            row = new or old
            change_type = "ADDED" if old is None else "REMOVED"
            append_change(
                identity,
                "file",
                old,
                new,
                change_type,
                "EXACT_DRAWING_ARTIFACT",
                {"filename": row.get("filename")},
                "MAJOR",
            )
            file_count += 1
            continue
        matched_count += 1
        for field in ("sha256", "mime_type", "byte_size", "page_count"):
            if old.get(field) != new.get(field):
                append_change(
                    identity,
                    field,
                    old.get(field),
                    new.get(field),
                    "MODIFIED",
                    (
                        "EXACT_DRAWING_ARTIFACT"
                        if field != "page_count"
                        else "EXACT_WORKER_PAGE_COUNT"
                    ),
                    {"filename": new.get("filename")},
                )
                file_count += 1

        old_sections = {
            str(row.get("stable_id")): row
            for row in old.get("sections", [])
            if isinstance(row, dict) and row.get("stable_id")
        }
        new_sections = {
            str(row.get("stable_id")): row
            for row in new.get("sections", [])
            if isinstance(row, dict) and row.get("stable_id")
        }
        has_text = has_text or bool(old_sections or new_sections)
        for section_id in sorted(old_sections.keys() | new_sections.keys()):
            old_section, new_section = old_sections.get(section_id), new_sections.get(section_id)
            old_text = _normalized_drawing_text((old_section or {}).get("text"))
            new_text = _normalized_drawing_text((new_section or {}).get("text"))
            if old_text == new_text:
                continue
            change_type = (
                "ADDED" if old_section is None else "REMOVED" if new_section is None else "MODIFIED"
            )
            citation = new_section or old_section
            append_change(
                identity,
                f"sections.{section_id}",
                (old_section or {}).get("text") if old_section else None,
                (new_section or {}).get("text") if new_section else None,
                change_type,
                "NORMALIZED_DRAWING_TEXT",
                {
                    "section_id": section_id,
                    "section_kind": citation.get("kind"),
                    "page": citation.get("page"),
                    "bbox": citation.get("bbox"),
                },
            )
            text_count += 1

        old_entities = {
            str(row.get("stable_id")): row
            for row in old.get("entities", [])
            if isinstance(row, dict) and row.get("stable_id")
        }
        new_entities = {
            str(row.get("stable_id")): row
            for row in new.get("entities", [])
            if isinstance(row, dict) and row.get("stable_id")
        }
        has_structured = has_structured or bool(old_entities or new_entities)
        for entity_id in sorted(old_entities.keys() | new_entities.keys()):
            old_entity, new_entity = old_entities.get(entity_id), new_entities.get(entity_id)
            if old_entity == new_entity:
                continue
            change_type = (
                "ADDED" if old_entity is None else "REMOVED" if new_entity is None else "MODIFIED"
            )
            citation = new_entity or old_entity
            append_change(
                identity,
                f"entities.{entity_id}",
                old_entity,
                new_entity,
                change_type,
                "EXACT_NORMALIZED_DRAWING_ENTITY",
                {
                    "entity_id": entity_id,
                    "entity_type": citation.get("type"),
                    "page": citation.get("page"),
                    "bbox": citation.get("bbox"),
                    "unit": citation.get("unit"),
                    "worker_contract_version": new.get("worker_contract_version")
                    or old.get("worker_contract_version"),
                },
                "MAJOR" if citation.get("type") in {"DIMENSION", "TOLERANCE"} else "MODERATE",
            )
            structured_count += 1

    versions = sorted(
        {
            str(row["worker_contract_version"])
            for row in old_records + new_records
            if row.get("worker_contract_version")
        }
    )
    level = "STRUCTURED" if has_structured else "TEXT" if has_text else "FILE"
    limitations = ["Visual page diff is not implemented"]
    if not has_structured:
        limitations.append(
            "Dimensions, tolerances, annotations, views, GD&T, weld, and finish "
            "symbols are unsupported without normalized worker entities"
        )
    return changes, DrawingComparisonResult(
        comparison_level=level,
        file_change_count=file_count,
        text_change_count=text_count,
        structured_change_count=structured_count,
        matched_drawing_count=matched_count,
        worker_contract_versions=versions,
        capabilities={
            "file": True,
            "text": has_text,
            "structured": has_structured,
            "visual": False,
        },
        limitations=limitations,
    )


def _file_changes(db: Session, before: Revision, after: Revision) -> list[dict]:
    old = {(row.kind, row.original_filename): row for row in _artifacts(db, before.id)}
    new = {(row.kind, row.original_filename): row for row in _artifacts(db, after.id)}
    rows = []
    for key in sorted(old.keys() | new.keys()):
        left, right = old.get(key), new.get(key)
        if left is not None and right is not None and left.sha256 == right.sha256:
            continue
        change_type = "ADDED" if left is None else "REMOVED" if right is None else "MODIFIED"
        rows.append(
            {
                "change_key": f"file:{key[0]}:{key[1]}",
                "change_type": change_type,
                "property_path": f"files.{key[0]}.{key[1]}",
                "old_value": (
                    {
                        "artifact_id": str(left.id),
                        "sha256": left.sha256,
                        "byte_size": left.byte_size,
                    }
                    if left
                    else None
                ),
                "new_value": (
                    {
                        "artifact_id": str(right.id),
                        "sha256": right.sha256,
                        "byte_size": right.byte_size,
                    }
                    if right
                    else None
                ),
                "unit": None,
                "delta": None,
                "percent_change": None,
                "significance": "MODERATE",
                "categories": ["FILE"],
                "confidence": 1.0,
                "provenance": "EXACT_HASH",
                "metadata": {"kind": key[0], "filename": key[1]},
            }
        )
    return rows


def _store_domain(
    db: Session,
    comparison: RevisionComparison,
    domain_name: str,
    changes: list[dict],
    result: dict | None = None,
    warnings: list[str] | None = None,
) -> RevisionComparisonDomain:
    now = datetime.now(UTC)
    result = result or {"has_changes": bool(changes), "change_count": len(changes)}
    domain = RevisionComparisonDomain(
        comparison_id=comparison.id,
        domain=domain_name,
        status="COMPLETED" if changes else "UNCHANGED",
        engine_version=COMPARISON_ENGINE_VERSION,
        result_json=result,
        result_hash=_canonical_hash({"result": result, "changes": changes}),
        change_count=len(changes),
        warnings_json=warnings or [],
        started_at=now,
        finished_at=now,
    )
    db.add(domain)
    db.flush()
    for row in changes:
        db.add(
            RevisionComparisonChange(
                domain_id=domain.id,
                change_key=row["change_key"],
                change_type=row["change_type"],
                property_path=row["property_path"],
                old_value_json=row["old_value"],
                new_value_json=row["new_value"],
                unit=row["unit"],
                delta=row["delta"],
                percent_change=row["percent_change"],
                significance=row["significance"],
                categories_json=row["categories"],
                confidence=row["confidence"],
                provenance=row["provenance"],
                metadata_json=row["metadata"],
            )
        )
    return domain


def create_comparison(
    db: Session,
    principal: Principal,
    item_id: uuid.UUID,
    from_revision_id: uuid.UUID,
    to_revision_id: uuid.UUID,
    domains: list[ComparisonDomainName],
) -> tuple[RevisionComparison, bool]:
    if from_revision_id == to_revision_id:
        raise HTTPException(status_code=422, detail="Comparison revisions must differ")
    before = readable_item_revision(db, item_id, from_revision_id, principal)
    after = readable_item_revision(db, item_id, to_revision_id, principal)
    requested = sorted(set(domains))
    before_hash = revision_fingerprint(db, before)
    after_hash = revision_fingerprint(db, after)
    cache_key = _canonical_hash(
        {
            "item_id": str(item_id),
            "from": before_hash,
            "to": after_hash,
            "domains": requested,
            "engine": COMPARISON_ENGINE_VERSION,
            "rules": RULES_VERSION,
        }
    )
    existing = db.scalar(
        select(RevisionComparison).where(RevisionComparison.cache_key == cache_key)
    )
    if existing is not None:
        return existing, False
    user = user_for_principal(db, principal)
    now = datetime.now(UTC)
    comparison = RevisionComparison(
        item_id=item_id,
        from_revision_id=before.id,
        to_revision_id=after.id,
        status="RUNNING",
        cache_key=cache_key,
        from_fingerprint=before_hash,
        to_fingerprint=after_hash,
        comparison_engine_version=COMPARISON_ENGINE_VERSION,
        metadata_schema_version=METADATA_SCHEMA_VERSION,
        rules_version=RULES_VERSION,
        requested_domains_json=requested,
        created_by=user.id,
        started_at=now,
    )
    db.add(comparison)
    db.flush()
    supported = set(requested) & SYNC_DOMAINS
    for domain in requested:
        if domain == "FILES":
            _store_domain(db, comparison, domain, _file_changes(db, before, after))
        elif domain in {"METADATA", "PHYSICAL_PROPERTIES"}:
            uncertain = _uncertain_array_measurements(before, after)
            if uncertain and domain == "PHYSICAL_PROPERTIES":
                result = {"has_changes": None, "change_count": 0}
                db.add(RevisionComparisonDomain(
                    comparison_id=comparison.id, domain=domain, status="UNSUPPORTED",
                    engine_version=COMPARISON_ENGINE_VERSION, result_json=result,
                    result_hash=_canonical_hash(result), change_count=0,
                    warnings_json=[ARRAY_GEOMETRY_WARNING], finished_at=now,
                ))
                supported.discard(domain)
            else:
                changes = _metadata_changes(before, after, domain)
                stored = _store_domain(
                    db, comparison, domain, changes,
                    warnings=[ARRAY_GEOMETRY_WARNING] if uncertain else None,
                )
                if uncertain and not changes:
                    stored.status = "UNSUPPORTED"
                    stored.result_json = {"has_changes": None, "change_count": 0}
                    stored.result_hash = _canonical_hash(stored.result_json)
                    supported.discard(domain)
        elif domain == "GEOMETRY":
            geometry_changes = _geometry_changes(before, after)
            geometry_result = _geometry_result(db, before, after, geometry_changes)
            if geometry_result.comparison_level == "UNAVAILABLE":
                result = geometry_result.model_dump(mode="json")
                db.add(
                    RevisionComparisonDomain(
                        comparison_id=comparison.id,
                        domain=domain,
                        status="UNSUPPORTED",
                        engine_version=COMPARISON_ENGINE_VERSION,
                        result_json=result,
                        result_hash=_canonical_hash(result),
                        change_count=0,
                        warnings_json=geometry_result.limitations,
                        finished_at=now,
                    )
                )
                supported.discard(domain)
            else:
                _store_domain(
                    db,
                    comparison,
                    domain,
                    geometry_changes,
                    geometry_result.model_dump(mode="json"),
                )
        elif domain == "FEATURES":
            feature_changes, feature_result = _feature_changes(before, after)
            if feature_result.comparison_level == "UNAVAILABLE":
                result = feature_result.model_dump(mode="json")
                db.add(
                    RevisionComparisonDomain(
                        comparison_id=comparison.id,
                        domain=domain,
                        status="UNSUPPORTED",
                        engine_version=COMPARISON_ENGINE_VERSION,
                        result_json=result,
                        result_hash=_canonical_hash(result),
                        change_count=0,
                        warnings_json=feature_result.limitations,
                        finished_at=now,
                    )
                )
                supported.discard(domain)
            else:
                _store_domain(
                    db,
                    comparison,
                    domain,
                    feature_changes,
                    feature_result.model_dump(mode="json"),
                    warnings=feature_result.limitations,
                )
        elif domain == "BOM":
            bom_changes, bom_result = _bom_changes(db, before, after)
            if bom_result.comparison_level == "UNAVAILABLE":
                result = bom_result.model_dump(mode="json")
                db.add(
                    RevisionComparisonDomain(
                        comparison_id=comparison.id,
                        domain=domain,
                        status="UNSUPPORTED",
                        engine_version=COMPARISON_ENGINE_VERSION,
                        result_json=result,
                        result_hash=_canonical_hash(result),
                        change_count=0,
                        warnings_json=bom_result.limitations,
                        finished_at=now,
                    )
                )
                supported.discard(domain)
            else:
                _store_domain(
                    db,
                    comparison,
                    domain,
                    bom_changes,
                    bom_result.model_dump(mode="json"),
                    warnings=bom_result.limitations,
                )
        elif domain == "DRAWING":
            drawing_changes, drawing_result = _drawing_changes(db, before, after)
            if drawing_result.comparison_level == "UNAVAILABLE":
                result = drawing_result.model_dump(mode="json")
                db.add(
                    RevisionComparisonDomain(
                        comparison_id=comparison.id,
                        domain=domain,
                        status="UNSUPPORTED",
                        engine_version=COMPARISON_ENGINE_VERSION,
                        result_json=result,
                        result_hash=_canonical_hash(result),
                        change_count=0,
                        warnings_json=drawing_result.limitations,
                        finished_at=now,
                    )
                )
                supported.discard(domain)
            else:
                _store_domain(
                    db,
                    comparison,
                    domain,
                    drawing_changes,
                    drawing_result.model_dump(mode="json"),
                    warnings=drawing_result.limitations,
                )
        else:
            result = {"has_changes": False, "change_count": 0}
            db.add(
                RevisionComparisonDomain(
                    comparison_id=comparison.id,
                    domain=domain,
                    status="UNSUPPORTED",
                    engine_version=COMPARISON_ENGINE_VERSION,
                    result_json=result,
                    result_hash=_canonical_hash(result),
                    change_count=0,
                    warnings_json=["This comparison domain is not implemented yet"],
                    finished_at=now,
                )
            )
    comparison.status = "PARTIAL" if len(supported) != len(requested) else "COMPLETED"
    comparison.finished_at = now
    comparison.summary_json = {
        "has_changes": db.scalar(
            select(RevisionComparisonChange.id)
            .join(RevisionComparisonDomain)
            .where(RevisionComparisonDomain.comparison_id == comparison.id)
            .limit(1)
        )
        is not None,
        "completed_domains": sorted(supported),
        "unsupported_domains": sorted(set(requested) - supported),
    }
    db.flush()
    return comparison, True


def authorize_comparison(
    db: Session, principal: Principal, comparison: RevisionComparison
) -> RevisionComparison:
    readable_item_revision(db, comparison.item_id, comparison.from_revision_id, principal)
    readable_item_revision(db, comparison.item_id, comparison.to_revision_id, principal)
    return comparison


def previous_revision(
    db: Session, principal: Principal, item_id: uuid.UUID, revision_id: uuid.UUID
) -> Revision:
    current = readable_item_revision(db, item_id, revision_id, principal)
    revisions = list(
        db.scalars(
            select(Revision)
            .where(Revision.part_id == item_id, Revision.created_at < current.created_at)
            .order_by(Revision.created_at.desc(), Revision.id.desc())
        )
    )
    for candidate in revisions:
        try:
            return readable_item_revision(db, item_id, candidate.id, principal)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
    raise HTTPException(
        status_code=409, detail="Current revision has no previous readable revision"
    )


def comparison_response(db: Session, comparison: RevisionComparison) -> RevisionComparisonResponse:
    domains = list(
        db.scalars(
            select(RevisionComparisonDomain)
            .where(RevisionComparisonDomain.comparison_id == comparison.id)
            .order_by(RevisionComparisonDomain.domain)
        )
    )
    responses = []
    for domain in domains:
        changes = list(
            db.scalars(
                select(RevisionComparisonChange)
                .where(RevisionComparisonChange.domain_id == domain.id)
                .order_by(RevisionComparisonChange.change_key)
            )
        )
        responses.append(
            ComparisonDomainResponse(
                id=domain.id,
                domain=domain.domain,
                status=domain.status,
                engine_version=domain.engine_version,
                result=domain.result_json,
                result_hash=domain.result_hash,
                change_count=domain.change_count,
                warnings=domain.warnings_json,
                error_detail=domain.error_detail,
                changes=[
                    ComparisonChangeResponse(
                        id=row.id,
                        change_key=row.change_key,
                        change_type=row.change_type,
                        property_path=row.property_path,
                        old_value=row.old_value_json,
                        new_value=row.new_value_json,
                        unit=row.unit,
                        delta=row.delta,
                        percent_change=row.percent_change,
                        significance=row.significance,
                        categories=row.categories_json,
                        confidence=row.confidence,
                        provenance=row.provenance,
                        metadata=row.metadata_json,
                    )
                    for row in changes
                ],
            )
        )
    return RevisionComparisonResponse(
        id=comparison.id,
        item_id=comparison.item_id,
        from_revision_id=comparison.from_revision_id,
        to_revision_id=comparison.to_revision_id,
        status=comparison.status,
        comparison_engine_version=comparison.comparison_engine_version,
        metadata_schema_version=comparison.metadata_schema_version,
        rules_version=comparison.rules_version,
        requested_domains=comparison.requested_domains_json,
        summary=comparison.summary_json,
        error_detail=comparison.error_detail,
        created_at=comparison.created_at,
        started_at=comparison.started_at,
        finished_at=comparison.finished_at,
        domains=responses,
    )
