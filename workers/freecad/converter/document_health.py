"""Conservative FreeCAD document-state checks independent of the native runtime."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from assembly_suppression import native_suppression
from extraction_provider import ProviderResult

_PROFILE_REQUIRED = {
    "PartDesign::Pad",
    "PartDesign::Pocket",
    "PartDesign::Revolution",
    "PartDesign::Groove",
    "PartDesign::Hole",
}

MAX_DOCUMENT_OBJECTS = 100_000


def check_document_object_limit(document: Any) -> None:
    """Bound native document work before recompute, health checks, or tessellation."""
    if len(document.Objects) > MAX_DOCUMENT_OBJECTS:
        raise ValueError(f"FreeCAD document exceeds the configured object limit ({MAX_DOCUMENT_OBJECTS}).")


def recompute_for_extraction(document: Any) -> tuple[bool, str | None, list[dict[str, str]]]:
    """Detect observed placement changes without saving or claiming shape equivalence."""
    check_document_object_limit(document)
    diagnostics: list[dict[str, str]] = []

    def placements() -> tuple[dict[str, tuple[float, ...]], set[str]]:
        observed: dict[str, tuple[float, ...]] = {}
        unreadable: set[str] = set()
        for obj in document.Objects:
            name = str(obj.Name)
            try:
                if "Placement" not in obj.PropertiesList:
                    continue
                values = tuple(float(value) for value in obj.Placement.toMatrix().A)
                if len(values) != 16 or not all(math.isfinite(value) for value in values):
                    raise ValueError("invalid placement matrix")
                observed[name] = values
            except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
                unreadable.add(name)
        return observed, unreadable

    before, unreadable_before = placements()
    succeeded, error_message = True, None
    try:
        succeeded = document.recompute() is not False
    except Exception as error:  # noqa: BLE001 - preserve available shapes from damaged documents
        succeeded, error_message = False, str(error)
    check_document_object_limit(document)
    after, unreadable_after = placements()
    changed = sorted(
        name for name in before.keys() & after.keys()
        if any(not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-8)
               for a, b in zip(before[name], after[name]))
    )
    if changed:
        diagnostics.append({
            "section": "geometry", "code": "RECOMPUTE_CHANGED_PLACEMENTS",
            "message": (
                f"Recompute changed {len(changed)} observed root-document placements "
                f"({', '.join(changed[:20])}). Preview and measurements use the in-memory "
                "recomputed state, which differs from the opened document. The source was not saved."
            ),
        })
    unknown = unreadable_before | unreadable_after | (before.keys() ^ after.keys())
    if unknown:
        diagnostics.append({
            "section": "geometry", "code": "RECOMPUTE_PLACEMENT_COMPARISON_INCOMPLETE",
            "message": (
                f"Placement changes across recompute could not be fully compared for {len(unknown)} "
                "root-document objects. Preview and measurements use the in-memory state; "
                "equivalence to the opened document is unverified. The source was not saved."
            ),
        })
    return succeeded, error_message, diagnostics


def object_health(
    obj: Any, *, feature_history: bool = False
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Return section diagnostics and features following a body's active tip."""
    name = str(getattr(obj, "Name", "") or "unknown")
    type_id = str(getattr(obj, "TypeId", "") or "")
    feature_history = feature_history or type_id.startswith("PartDesign::")
    issues: list[tuple[str, str, str]] = []
    after_tip: list[str] = []
    properties = set(getattr(obj, "PropertiesList", ()) or ())
    states = [str(value) for value in (getattr(obj, "State", ()) or ())]
    abnormal = [state for state in states if state.casefold() not in {"up-to-date", "touched"}]
    if abnormal:
        message = f"FreeCAD object '{name}' reports state: {', '.join(abnormal)}."
        issues.append(
            (
                "features" if feature_history else "geometry",
                "OBJECT_EXECUTION_ERROR",
                message,
            )
        )
        if "Shape" in properties and feature_history:
            issues.append(("geometry", "OBJECT_EXECUTION_ERROR", message))
        status_method = getattr(obj, "getStatusString", None)
        try:
            status = str(status_method() or "") if callable(status_method) else ""
        except (AttributeError, RuntimeError, TypeError, ValueError):
            status = ""
        if "property binding" in status.casefold():
            issues.append(
                (
                    "parameters",
                    "BROKEN_EXPRESSION",
                    f"FreeCAD reports an expression binding error for object '{name}'.",
                )
            )
    if "Shape" in properties and any(state.casefold() == "touched" for state in states):
        issues.append(
            (
                "geometry",
                "STALE_OBJECT_SHAPE",
                f"FreeCAD object '{name}' remains touched after recompute; its saved shape may be stale.",
            )
        )
    if type_id == "App::Link" and getattr(obj, "LinkedObject", None) is None:
        issues.append(
            ("assembly", "UNRESOLVED_LINK_TARGET", f"FreeCAD link '{name}' has no resolved target.")
        )
        issues.append(
            ("geometry", "UNRESOLVED_LINK_TARGET", f"FreeCAD link '{name}' has no resolved target.")
        )
    if type_id == "PartDesign::Body":
        group = list(getattr(obj, "Group", ()) or ())
        tip = getattr(obj, "Tip", None)
        if group and tip is None:
            issues.append(
                (
                    "features",
                    "NULL_BODY_TIP",
                    f"PartDesign body '{name}' contains features but has no active tip.",
                )
            )
            issues.append(
                (
                    "geometry",
                    "NULL_BODY_TIP",
                    f"PartDesign body '{name}' contains features but has no active tip.",
                )
            )
        elif tip is not None:
            if tip not in group:
                issues.append(
                    (
                        "features",
                        "INVALID_BODY_TIP",
                        f"PartDesign body '{name}' has an active tip outside its feature group.",
                    )
                )
                issues.append(
                    (
                        "geometry",
                        "INVALID_BODY_TIP",
                        f"PartDesign body '{name}' has an active tip outside its feature group.",
                    )
                )
            else:
                after_tip = [
                    str(getattr(item, "Name", ""))
                    for item in group[group.index(tip) + 1 :]
                    if getattr(item, "Name", None)
                ]
    return issues, after_tip


class DocumentInspector:
    """Inspect recompute, object states, body tips, links, and native shape validity."""

    def __init__(self, feature_history: Callable[[str], bool]):
        self.feature_history = feature_history

    def inspect(
        self, document: Any, *, recompute_succeeded: bool, recompute_error: str | None = None
    ) -> ProviderResult:
        started = time.monotonic()
        diagnostics: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        after_tip_records: list[dict[str, Any]] = []

        def issue(section: str, code: str, message: str, name: str | None = None) -> None:
            item = {"section": section, "code": code, "message": message, "severity": "warning"}
            if name:
                item["object_name"] = name
            if item not in diagnostics:
                diagnostics.append(item)

        if not recompute_succeeded:
            message = "FreeCAD could not recompute the complete document."
            if recompute_error:
                message = f"{message} {recompute_error}"
            issue("geometry", "DOCUMENT_RECOMPUTE_FAILED", message)
        inactive_or_uncertain: set[str] = set()
        after_active_tip: set[str] = set()
        body_membership_unavailable = False
        for body in getattr(document, "Objects", ()):
            if str(getattr(body, "TypeId", "")) != "PartDesign::Body":
                continue
            try:
                group = list(getattr(body, "Group", ()) or ())
                tip = getattr(body, "Tip", None)
                suppressed, _source = native_suppression(body)
                if suppressed is True or tip is None or tip not in group:
                    inactive = group
                else:
                    inactive = group[group.index(tip) + 1 :]
                    after_active_tip.update(str(getattr(item, "Name", "")) for item in inactive)
                inactive_or_uncertain.update(str(getattr(item, "Name", "")) for item in inactive)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                body_membership_unavailable = True
        for obj in sorted(
            getattr(document, "Objects", ()),
            key=lambda value: str(getattr(value, "Name", "")).casefold(),
        ):
            name = str(getattr(obj, "Name", "") or "unknown")
            type_id = str(getattr(obj, "TypeId", "") or "")
            records.append({"name": name, "type_id": type_id})
            try:
                issues, after_tip = object_health(
                    obj, feature_history=self.feature_history(type_id)
                )
            except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
                issues, after_tip = [], []
                if name not in after_active_tip:
                    issue(
                        "geometry",
                        "OBJECT_HEALTH_INSPECTION_FAILED",
                        f"FreeCAD object '{name}' could not be fully inspected.",
                        name,
                    )
                if self.feature_history(type_id):
                    issue(
                        "features",
                        "OBJECT_HEALTH_INSPECTION_FAILED",
                        f"FreeCAD object '{name}' could not be fully inspected.",
                        name,
                    )
            for section, code, message in issues:
                if section == "geometry" and name in after_active_tip:
                    continue
                issue(section, code, message, name)
            if (
                type_id in _PROFILE_REQUIRED
                and not body_membership_unavailable
                and name not in inactive_or_uncertain
            ):
                try:
                    properties = set(getattr(obj, "PropertiesList", ()) or ())
                    suppressed, _source = native_suppression(obj)
                    if "Profile" not in properties and suppressed is not True:
                        message = f"The driving profile for '{name}' is not exposed by FreeCAD."
                        issue("features", "FEATURE_SUPPORT_INSPECTION_FAILED", message, name)
                        issue("geometry", "FEATURE_SUPPORT_INSPECTION_FAILED", message, name)
                    elif suppressed is not True:
                        profile = getattr(obj, "Profile", None)
                        target = (
                            (profile[0] if profile else None)
                            if isinstance(profile, (tuple, list))
                            else profile
                        )
                        if target is None:
                            message = f"Modeling feature '{name}' has no resolved driving profile."
                            issue("features", "MISSING_FEATURE_SUPPORT", message, name)
                            issue("geometry", "MISSING_FEATURE_SUPPORT", message, name)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    issue(
                        "features",
                        "FEATURE_SUPPORT_INSPECTION_FAILED",
                        f"The driving profile for '{name}' could not be inspected.",
                        name,
                    )
                    issue(
                        "geometry",
                        "FEATURE_SUPPORT_INSPECTION_FAILED",
                        f"The driving profile for '{name}' could not be inspected.",
                        name,
                    )
            if after_tip:
                after_tip_records.append({"body": name, "features": after_tip})
            if name in after_active_tip:
                continue
            if "Shape" not in (getattr(obj, "PropertiesList", ()) or ()):
                continue
            try:
                shape = getattr(obj, "Shape", None)
            except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
                issue(
                    "geometry",
                    "SHAPE_VALIDATION_FAILED",
                    f"Shape health could not be inspected for '{name}'.",
                    name,
                )
                continue
            if shape is None:
                continue
            try:
                is_null = bool(shape.isNull())
            except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError) as error:
                issue(
                    "geometry",
                    "SHAPE_VALIDATION_FAILED",
                    f"Shape health could not be inspected for '{name}': {error}",
                    name,
                )
                continue
            if is_null:
                if type_id.startswith("PartDesign::") and type_id not in {
                    "PartDesign::Body",
                    "PartDesign::FeatureBase",
                }:
                    issue(
                        "geometry",
                        "NULL_FEATURE_SHAPE",
                        f"Modeling feature '{name}' has no result shape after recompute.",
                        name,
                    )
                continue
            try:
                valid = bool(shape.isValid())
            except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError) as error:
                issue(
                    "geometry",
                    "SHAPE_VALIDATION_FAILED",
                    f"Shape validity could not be checked for '{name}': {error}",
                    name,
                )
            else:
                if not valid:
                    issue(
                        "geometry",
                        "INVALID_SHAPE",
                        f"FreeCAD object '{name}' contains an invalid result shape.",
                        name,
                    )
        evidence = {
            "recompute_succeeded": recompute_succeeded,
            "objects_checked": len(records),
            "features_after_body_tip": after_tip_records,
            "bodies_with_invalid_tips": sorted(
                {
                    str(item["object_name"])
                    for item in diagnostics
                    if item.get("code") in {"NULL_BODY_TIP", "INVALID_BODY_TIP"}
                    and item.get("object_name")
                },
                key=str.casefold,
            ),
        }
        return ProviderResult(
            records=records,
            diagnostics=diagnostics,
            evidence=evidence,
            capability="partial" if diagnostics else "complete",
            duration_ms=(time.monotonic() - started) * 1000,
        )
