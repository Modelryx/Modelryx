import hashlib
import json
import re
from datetime import UTC, datetime

from modelryx.cad.models import (
    CADDocumentInspection,
    CADFeatureCreateRequest,
    CADFilletCreateRequest,
    CADHoleCreateRequest,
    CADPadCreateRequest,
    CADParameterChangeRequest,
    CADPocketCreateRequest,
    CADSketchCreateRequest,
)
from modelryx.models import CADOperationPlan, CADSession

EDITABLE_PARAMETERS = {
    "PAD": {"length_mm", "length"},
    "POCKET": {"depth_mm", "depth", "length_mm", "length"},
    "HOLE": {"diameter_mm", "diameter", "radius_mm", "radius"},
    "FILLET": {"radius_mm", "radius"},
}
POSITIVE_PARAMETERS = {
    "length_mm",
    "length",
    "depth_mm",
    "depth",
    "diameter_mm",
    "diameter",
    "radius_mm",
    "radius",
}


def _unique_feature(inspection: CADDocumentInspection, target_id: str):
    matches = [feature for feature in inspection.features if feature.id == target_id]
    if len(matches) != 1:
        raise ValueError("CAD parent does not resolve uniquely in the authoritative inspection")
    return matches[0]


def resolve_feature_creation(
    request: CADFeatureCreateRequest,
    inspection: CADDocumentInspection,
    feature_id: str,
) -> tuple[str, str, dict, dict, str]:
    if isinstance(request, CADSketchCreateRequest):
        parent = _unique_feature(inspection, request.body_id)
        if parent.type.upper() != "BODY" and parent.native_type != "PartDesign::Body":
            raise ValueError("Sketch parent must be an inspected PartDesign body")
        if request.geometry == "rectangle":
            if (
                request.width_mm is None
                or request.height_mm is None
                or request.radius_mm is not None
            ):
                raise ValueError("Rectangle sketches require only width_mm and height_mm")
        elif (
            request.radius_mm is None
            or request.width_mm is not None
            or request.height_mm is not None
        ):
            raise ValueError("Circle sketches require only radius_mm")
        values = request.model_dump(exclude={"operation", "body_id"}, exclude_none=True)
        return request.body_id, "sketch", {}, {"feature_id": feature_id, **values}, "SKETCH"
    if isinstance(request, CADPadCreateRequest | CADPocketCreateRequest):
        profile = _unique_feature(inspection, request.profile_id)
        if profile.type.upper() != "SKETCH" and profile.native_type != "Sketcher::SketchObject":
            raise ValueError("Pad and pocket profiles must be inspected sketches")
        dimension = (
            {"length_mm": request.length_mm}
            if isinstance(request, CADPadCreateRequest)
            else {"depth_mm": request.depth_mm}
        )
        return (
            request.profile_id,
            "profile",
            {},
            {"feature_id": feature_id, **dimension},
            profile.type,
        )
    if isinstance(request, CADHoleCreateRequest):
        parent = _unique_feature(inspection, request.body_id)
        if parent.type.upper() != "BODY" and parent.native_type != "PartDesign::Body":
            raise ValueError("Hole parent must be an inspected PartDesign body")
        values = request.model_dump(exclude={"operation", "body_id"})
        return request.body_id, "body", {}, {"feature_id": feature_id, **values}, "HOLE"
    if isinstance(request, CADFilletCreateRequest):
        base = _unique_feature(inspection, request.base_feature_id)
        invalid_edges = any(index < 1 for index in request.edge_indices)
        duplicate_edges = len(set(request.edge_indices)) != len(request.edge_indices)
        if invalid_edges or duplicate_edges:
            raise ValueError("Fillet edge indices must be unique one-based values")
        return (
            request.base_feature_id,
            "edges",
            {},
            {
                "feature_id": feature_id,
                "edge_indices": request.edge_indices,
                "radius_mm": request.radius_mm,
            },
            base.type,
        )
    raise ValueError("Unsupported controlled feature creation request")


def state_token(session: CADSession, inspection: CADDocumentInspection) -> str:
    payload = {
        "session_id": str(session.id),
        "source_sha256": session.source_sha256,
        "worker_id": session.worker_id,
        "lock_version": session.lock_version,
        "inspection": inspection.model_dump(mode="json"),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def resolve_parameter_change(
    request: CADParameterChangeRequest,
    inspection: CADDocumentInspection,
) -> tuple[str, float, str | None, str]:
    matches = [feature for feature in inspection.features if feature.id == request.target_id]
    if len(matches) != 1:
        raise ValueError("CAD target does not resolve uniquely in the authoritative inspection")
    feature = matches[0]
    if feature.type.upper() == "SKETCH" and re.fullmatch(r"Constraints\[\d+\]", request.parameter):
        parameters = [value for value in feature.parameters if value.name == request.parameter]
        if (len(parameters) != 1 or not parameters[0].editable
                or parameters[0].unit not in {"mm", "deg"}
                or isinstance(parameters[0].value, bool)
                or not isinstance(parameters[0].value, int | float)):
            raise ValueError("Sketch constraint is unavailable or not editable")
        if request.unit and request.unit != parameters[0].unit:
            raise ValueError("Sketch constraint unit does not match the native unit")
        if (parameters[0].native_constraint_type in {"Radius", "Diameter", "Distance"}
                and request.value <= 0):
            raise ValueError("Sketch constraint value must be greater than zero")
        expected = float(parameters[0].value)
        if abs(request.value - expected) <= 1e-12:
            raise ValueError("Requested parameter value is already current")
        return feature.type, expected, parameters[0].unit, "sketch.constraint.set_value"
    allowed = EDITABLE_PARAMETERS.get(feature.type.upper(), set())
    if request.parameter not in allowed:
        raise ValueError(f"Parameter {request.parameter} is not editable for {feature.type}")
    parameters = [value for value in feature.parameters if value.name == request.parameter]
    if (
        len(parameters) != 1
        or isinstance(parameters[0].value, bool)
        or not isinstance(parameters[0].value, int | float)
    ):
        raise ValueError("Current parameter value is unavailable or ambiguous")
    expected = float(parameters[0].value)
    if request.parameter in POSITIVE_PARAMETERS and request.value <= 0:
        raise ValueError("CAD dimensions must be greater than zero")
    if abs(request.value - expected) <= 1e-12:
        raise ValueError("Requested parameter value is already current")
    unit = request.unit or parameters[0].unit or ("deg" if "angle" in request.parameter else "mm")
    return feature.type, expected, unit, "feature.parameter.set_value"


def refresh_plan_state(
    plan: CADOperationPlan,
    session: CADSession,
    inspection: CADDocumentInspection,
) -> None:
    now = datetime.now(UTC)
    if plan.status != "PENDING":
        raise ValueError(f"CAD plan is {plan.status.lower()}")
    expires_at = plan.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        plan.status = "EXPIRED"
        plan.decided_at = now
        raise ValueError("CAD plan has expired")
    if session.status != "READY" or plan.state_token != state_token(session, inspection):
        plan.status = "STALE"
        plan.decided_at = now
        raise ValueError("CAD session state changed; create a new plan")
