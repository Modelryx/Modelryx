"""FreeCAD feature semantic property registry and extraction."""

from __future__ import annotations

import math
from typing import Any


def _feature_vector(value: Any) -> list[float] | None:
    try:
        coordinates = [float(value.x), float(value.y), float(value.z)]
        return [round(item, 9) for item in coordinates] if all(map(math.isfinite, coordinates)) else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


FEATURE_PROPERTY_MAP = {
    "Part::Box": (
        ("length_mm", "Length"),
        ("width_mm", "Width"),
        ("height_mm", "Height"),
    ),
    "Part::Cylinder": (
        ("radius_mm", "Radius"),
        ("height_mm", "Height"),
        ("angle_deg", "Angle"),
    ),
    "Part::Cone": (
        ("radius1_mm", "Radius1"),
        ("radius2_mm", "Radius2"),
        ("height_mm", "Height"),
        ("angle_deg", "Angle"),
    ),
    "Part::Sphere": (
        ("radius_mm", "Radius"),
        ("angle1_deg", "Angle1"),
        ("angle2_deg", "Angle2"),
        ("angle3_deg", "Angle3"),
    ),
    "Part::Torus": (
        ("major_radius_mm", "Radius1"),
        ("minor_radius_mm", "Radius2"),
        ("angle1_deg", "Angle1"),
        ("angle2_deg", "Angle2"),
        ("angle3_deg", "Angle3"),
    ),
    "Part::Prism": (
        ("side_count", "Polygon"),
        ("circumradius_mm", "Circumradius"),
        ("height_mm", "Height"),
        ("first_angle_deg", "FirstAngle"),
        ("second_angle_deg", "SecondAngle"),
    ),
    "Part::Wedge": (
        ("x_min_mm", "Xmin"),
        ("x_max_mm", "Xmax"),
        ("x2_min_mm", "X2min"),
        ("x2_max_mm", "X2max"),
        ("y_min_mm", "Ymin"),
        ("y_max_mm", "Ymax"),
        ("z_min_mm", "Zmin"),
        ("z_max_mm", "Zmax"),
        ("z2_min_mm", "Z2min"),
        ("z2_max_mm", "Z2max"),
    ),
    "Part::Ellipsoid": (
        ("radius1_mm", "Radius1"),
        ("radius2_mm", "Radius2"),
        ("radius3_mm", "Radius3"),
        ("angle1_deg", "Angle1"),
        ("angle2_deg", "Angle2"),
        ("angle3_deg", "Angle3"),
    ),
    "PartDesign::AdditiveBox": (
        ("length_mm", "Length"),
        ("width_mm", "Width"),
        ("height_mm", "Height"),
    ),
    "PartDesign::SubtractiveBox": (
        ("length_mm", "Length"),
        ("width_mm", "Width"),
        ("height_mm", "Height"),
    ),
    "PartDesign::AdditiveCylinder": (
        ("radius_mm", "Radius"),
        ("height_mm", "Height"),
        ("angle_deg", "Angle"),
    ),
    "PartDesign::SubtractiveCylinder": (
        ("radius_mm", "Radius"),
        ("height_mm", "Height"),
        ("angle_deg", "Angle"),
    ),
    "PartDesign::AdditiveCone": (
        ("radius1_mm", "Radius1"),
        ("radius2_mm", "Radius2"),
        ("height_mm", "Height"),
        ("angle_deg", "Angle"),
    ),
    "PartDesign::SubtractiveCone": (
        ("radius1_mm", "Radius1"),
        ("radius2_mm", "Radius2"),
        ("height_mm", "Height"),
        ("angle_deg", "Angle"),
    ),
    "PartDesign::AdditiveSphere": (
        ("radius_mm", "Radius"),
        ("angle1_deg", "Angle1"),
        ("angle2_deg", "Angle2"),
        ("angle3_deg", "Angle3"),
    ),
    "PartDesign::SubtractiveSphere": (
        ("radius_mm", "Radius"),
        ("angle1_deg", "Angle1"),
        ("angle2_deg", "Angle2"),
        ("angle3_deg", "Angle3"),
    ),
    "PartDesign::AdditiveTorus": (
        ("major_radius_mm", "Radius1"),
        ("minor_radius_mm", "Radius2"),
        ("angle1_deg", "Angle1"),
        ("angle2_deg", "Angle2"),
        ("angle3_deg", "Angle3"),
    ),
    "PartDesign::SubtractiveTorus": (
        ("major_radius_mm", "Radius1"),
        ("minor_radius_mm", "Radius2"),
        ("angle1_deg", "Angle1"),
        ("angle2_deg", "Angle2"),
        ("angle3_deg", "Angle3"),
    ),
    "PartDesign::AdditiveWedge": (
        ("x_min_mm", "Xmin"),
        ("x_max_mm", "Xmax"),
        ("x2_min_mm", "X2min"),
        ("x2_max_mm", "X2max"),
        ("y_min_mm", "Ymin"),
        ("y_max_mm", "Ymax"),
        ("z_min_mm", "Zmin"),
        ("z_max_mm", "Zmax"),
        ("z2_min_mm", "Z2min"),
        ("z2_max_mm", "Z2max"),
    ),
    "PartDesign::SubtractiveWedge": (
        ("x_min_mm", "Xmin"),
        ("x_max_mm", "Xmax"),
        ("x2_min_mm", "X2min"),
        ("x2_max_mm", "X2max"),
        ("y_min_mm", "Ymin"),
        ("y_max_mm", "Ymax"),
        ("z_min_mm", "Zmin"),
        ("z_max_mm", "Zmax"),
        ("z2_min_mm", "Z2min"),
        ("z2_max_mm", "Z2max"),
    ),
    "PartDesign::AdditiveEllipsoid": (
        ("radius1_mm", "Radius1"),
        ("radius2_mm", "Radius2"),
        ("radius3_mm", "Radius3"),
        ("angle1_deg", "Angle1"),
        ("angle2_deg", "Angle2"),
        ("angle3_deg", "Angle3"),
    ),
    "PartDesign::SubtractiveEllipsoid": (
        ("radius1_mm", "Radius1"),
        ("radius2_mm", "Radius2"),
        ("radius3_mm", "Radius3"),
        ("angle1_deg", "Angle1"),
        ("angle2_deg", "Angle2"),
        ("angle3_deg", "Angle3"),
    ),
    "PartDesign::AdditivePrism": (
        ("side_count", "Polygon"),
        ("circumradius_mm", "Circumradius"),
        ("height_mm", "Height"),
        ("first_angle_deg", "FirstAngle"),
        ("second_angle_deg", "SecondAngle"),
    ),
    "PartDesign::SubtractivePrism": (
        ("side_count", "Polygon"),
        ("circumradius_mm", "Circumradius"),
        ("height_mm", "Height"),
        ("first_angle_deg", "FirstAngle"),
        ("second_angle_deg", "SecondAngle"),
    ),
    "PartDesign::AdditiveHelix": (
        ("pitch_mm", "Pitch"),
        ("height_mm", "Height"),
        ("turn_count", "Turns"),
        ("cone_angle_deg", "Angle"),
    ),
    "PartDesign::SubtractiveHelix": (
        ("pitch_mm", "Pitch"),
        ("height_mm", "Height"),
        ("turn_count", "Turns"),
        ("cone_angle_deg", "Angle"),
    ),
    "PartDesign::Pad": (("length_mm", "Length"),),
    "PartDesign::Pocket": (("depth_mm", "Length"),),
    "PartDesign::Hole": (
        ("diameter_mm", "Diameter"),
        ("radius_mm", "Radius"),
        ("depth_mm", "Depth"),
        ("counterbore_diameter_mm", "HoleCutDiameter"),
        ("counterbore_depth_mm", "HoleCutDepth"),
        ("countersink_angle_deg", "HoleCutAngle"),
        ("thread_pitch_mm", "ThreadPitch"),
        ("tapped_depth_mm", "ThreadDepth"),
    ),
    "PartDesign::Fillet": (("radius_mm", "Radius"),),
    "PartDesign::Chamfer": (("size_mm", "Size"),),
    "PartDesign::Revolution": (("angle_deg", "Angle"),),
    "PartDesign::LinearPattern": (
        ("occurrence_count", "Occurrences"),
        ("length_mm", "Length"),
    ),
    "PartDesign::PolarPattern": (
        ("occurrence_count", "Occurrences"),
        ("angle_deg", "Angle"),
    ),
    "PartDesign::Groove": (("angle_deg", "Angle"),),
    "PartDesign::Draft": (("angle_deg", "Angle"),),
    "PartDesign::Thickness": (("thickness_mm", "Value"),),
    "Part::Extrusion": (("length_forward_mm", "LengthFwd"), ("length_reverse_mm", "LengthRev")),
    "Part::Revolution": (("angle_deg", "Angle"),),
    "Part::Thickness": (("thickness_mm", "Value"),),
    "Part::Loft": (("max_degree", "MaxDegree"),),
}


def normalized_feature_properties(obj: Any) -> dict[str, Any]:
    """Expose stable, CAD-neutral semantics for common FreeCAD modeling operations."""
    type_id = str(getattr(obj, "TypeId", ""))
    result = {}
    for semantic_name, native_name in FEATURE_PROPERTY_MAP.get(type_id, ()):
        if native_name not in getattr(obj, "PropertiesList", []):
            continue
        try:
            value = float(getattr(obj, native_name))
            if math.isfinite(value):
                result[semantic_name] = round(value, 9)
        except (TypeError, ValueError, OverflowError):
            continue
    proxy = getattr(obj, "Proxy", None)
    proxy_type = str(getattr(proxy, "Type", "") or "")
    if type_id == "Part::FeaturePython" and proxy_type.casefold() == "array":
        array_type = str(getattr(obj, "ArrayType", "") or "").casefold()
        if array_type:
            result["pattern_kind"] = array_type
        if array_type == "polar":
            count = getattr(obj, "NumberPolar", None)
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                result["occurrence_count"] = count
            try:
                angle = float(obj.Angle)
                if math.isfinite(angle):
                    result["angle_deg"] = round(angle, 9)
            except (AttributeError, TypeError, ValueError):
                pass
            for semantic_name, native_name in (("axis", "Axis"), ("center_mm", "Center")):
                if (vector := _feature_vector(getattr(obj, native_name, None))) is not None:
                    result[semantic_name] = vector
        elif array_type == "ortho":
            counts = []
            for axis in ("x", "y", "z"):
                count = getattr(obj, f"Number{axis.upper()}", None)
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    result[f"count_{axis}"] = count
                    counts.append(count)
                interval = _feature_vector(getattr(obj, f"Interval{axis.upper()}", None))
                if interval is not None:
                    result[f"interval_{axis}_mm"] = interval
            if len(counts) == 3:
                result["occurrence_count"] = math.prod(counts)
        elif array_type == "circular":
            for semantic_name, native_name in (
                ("occurrence_count", "Count"),
                ("circle_count", "NumberCircles"),
                ("polar_count", "NumberPolar"),
                ("symmetry", "Symmetry"),
            ):
                value = getattr(obj, native_name, None)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    result[semantic_name] = value
            for semantic_name, native_name in (
                ("radial_distance_mm", "RadialDistance"),
                ("tangential_distance_mm", "TangentialDistance"),
            ):
                try:
                    value = float(getattr(obj, native_name))
                    if math.isfinite(value):
                        result[semantic_name] = round(value, 9)
                except (AttributeError, TypeError, ValueError):
                    pass
            for semantic_name, native_name in (("axis", "Axis"), ("center_mm", "Center")):
                if (vector := _feature_vector(getattr(obj, native_name, None))) is not None:
                    result[semantic_name] = vector
        fuse = getattr(obj, "Fuse", None)
        if isinstance(fuse, bool):
            result["fuse"] = fuse
    elif type_id == "Part::FeaturePython" and proxy_type.casefold() == "patharray":
        result["pattern_kind"] = "path"
        count = getattr(obj, "Count", None)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            result["occurrence_count"] = count
        for semantic_name, native_name in (
            ("align", "Align"),
            ("reverse_path", "ReversePath"),
            ("force_vertical", "ForceVertical"),
        ):
            value = getattr(obj, native_name, None)
            if isinstance(value, bool):
                result[semantic_name] = value
        align_mode = str(getattr(obj, "AlignMode", "") or "").strip()
        if align_mode:
            result["align_mode"] = align_mode
        for semantic_name, native_name in (
            ("start_offset_mm", "StartOffset"),
            ("end_offset_mm", "EndOffset"),
        ):
            try:
                value = float(getattr(obj, native_name))
                if math.isfinite(value):
                    result[semantic_name] = round(value, 9)
            except (AttributeError, TypeError, ValueError):
                pass
        for semantic_name, native_name in (
            ("extra_translation_mm", "ExtraTranslation"),
            ("tangent_vector", "TangentVector"),
            ("vertical_vector", "VerticalVector"),
        ):
            if (vector := _feature_vector(getattr(obj, native_name, None))) is not None:
                result[semantic_name] = vector
        fuse = getattr(obj, "Fuse", None)
        if isinstance(fuse, bool):
            result["fuse"] = fuse
    elif type_id == "Part::FeaturePython" and proxy_type.casefold() == "pointarray":
        result["pattern_kind"] = "point"
        count = getattr(obj, "Count", None)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            result["occurrence_count"] = count
        fuse = getattr(obj, "Fuse", None)
        if isinstance(fuse, bool):
            result["fuse"] = fuse
    reference_properties = {
        "profile_refs": ("Profile",),
        "base_refs": ("Base", "BaseFeature"),
        "support_refs": ("Support",),
        "spine_refs": ("Spine", "PathObject"),
        "section_refs": ("Sections",),
        "original_feature_refs": ("OriginalFeature", "Originals", "OriginalFeatures"),
        "transformation_refs": ("Transformations",),
        "tool_refs": ("Tool", "Tools"),
        "point_refs": ("PointObject",),
    }

    def reference_names(value: Any) -> list[str]:
        values = value if isinstance(value, (tuple, list)) else [value]
        names = []
        for entry in values:
            target = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
            name = getattr(target, "Name", None)
            if name:
                names.append(str(name))
        return list(dict.fromkeys(names))

    for semantic_name, candidates in reference_properties.items():
        references: list[str] = []
        for native_name in candidates:
            if native_name in getattr(obj, "PropertiesList", []):
                references.extend(reference_names(getattr(obj, native_name, None)))
        if references:
            result[semantic_name] = list(dict.fromkeys(references))

    for semantic_name, candidates in {
        "reversed": ("Reversed",),
        "midplane": ("Midplane", "MidPlane"),
        "symmetric": ("SymmetricToPlane",),
        "closed": ("Closed",),
        "solid": ("Solid",),
        "ruled": ("Ruled",),
        "refine": ("Refine",),
        "left_handed": ("LeftHanded",),
        "linearize": ("Linearize",),
    }.items():
        value = next(
            (
                getattr(obj, native_name)
                for native_name in candidates
                if native_name in getattr(obj, "PropertiesList", [])
            ),
            None,
        )
        if isinstance(value, bool):
            result[semantic_name] = value

    termination = next(
        (
            getattr(obj, native_name)
            for native_name in ("Type", "LengthType", "DepthType", "HoleCutType")
            if native_name in getattr(obj, "PropertiesList", [])
        ),
        None,
    )
    if termination is not None:
        result["termination"] = str(termination)
    for native_name in ("Direction", "Dir", "Axis"):
        if native_name not in getattr(obj, "PropertiesList", []):
            continue
        if (direction := _feature_vector(getattr(obj, native_name, None))) is not None:
            result["direction"] = direction
            break
    return result

