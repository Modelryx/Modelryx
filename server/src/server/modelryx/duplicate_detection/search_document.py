"""Stable, controlled embedding input; deliberately excludes operational fields."""

from __future__ import annotations

from typing import Any

from modelryx.metadata.array_quality import ARRAY_GEOMETRY_WARNING, has_unverified_array_geometry

SEARCH_DOCUMENT_VERSION = "part-metadata-semantic-v15"


def build_semantic_text(metadata: dict[str, Any]) -> str:
    identity = metadata.get("identity", {})
    geometry = metadata.get("geometry", {})
    shape = geometry.get("shape", {})
    bbox = geometry.get("bounding_box", {})
    physical = metadata.get("physical", {})
    material = physical.get("material", {})
    summary = metadata.get("features", {}).get("summary", {})
    uncertain_geometry = has_unverified_array_geometry(metadata)
    lines = [f"{identity.get('display_name') or identity.get('name', 'Unnamed part')}."]
    lines.append(f"{str(identity.get('kind', 'unknown')).title()}.")
    if identity.get("description"):
        lines.append(f"Description: {identity['description']}.")
    classifications = identity.get("classification") or []
    if classifications:
        lines.append(f"Classification: {', '.join(map(str, classifications))}.")
    if material.get("normalized_name") or material.get("name"):
        lines.append(f"Material: {material.get('normalized_name') or material['name']}.")
    if uncertain_geometry:
        lines.append(ARRAY_GEOMETRY_WARNING)
    if not uncertain_geometry and all(key in bbox for key in ("x_mm", "y_mm", "z_mm")):
        dimensions = sorted(float(bbox[key]) for key in ("x_mm", "y_mm", "z_mm"))
        lines.append(
            "Dimensions: " + " by ".join(f"{value:g}" for value in dimensions) + " millimeters."
        )
    if not uncertain_geometry and "volume_mm3" in shape:
        lines.append(f"Volume: approximately {float(shape['volume_mm3']):g} cubic millimeters.")
    if not uncertain_geometry and "surface_area_mm2" in shape:
        lines.append(
            f"Surface area: approximately {float(shape['surface_area_mm2']):g} square millimeters."
        )
    if not uncertain_geometry and physical.get("mass_kg") is not None:
        lines.append(f"Mass: {float(physical['mass_kg']):g} kilograms.")
    if physical.get("density_kg_m3") is not None:
        lines.append(f"Density: {float(physical['density_kg_m3']):g} kilograms per cubic meter.")
    labels = {
        "hole_count": "holes",
        "fillet_count": "fillets",
        "chamfer_count": "chamfers",
        "pocket_count": "pockets",
        "thread_count": "threads",
        "pattern_count": "patterns",
    }
    for key, label in labels.items():
        if key in summary:
            lines.append(f"{summary[key]} {label}.")
    holes = metadata.get("features", {}).get("holes", [])
    for hole in holes:
        if isinstance(hole, dict) and hole.get("diameter_mm") is not None:
            termination = "through" if hole.get("through") else "blind"
            lines.append(
                f"{hole.get('count', 1)} {termination} holes, diameter "
                f"{float(hole['diameter_mm']):g} millimeters."
            )
    feature_items = metadata.get("features", {}).get("items", [])
    for feature in feature_items[:100]:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        if properties.get("suppressed") is True:
            continue
        details = []
        for key in (
            "termination",
            "length_mm",
            "width_mm",
            "height_mm",
            "depth_mm",
            "radius_mm",
            "radius1_mm",
            "radius2_mm",
            "radius3_mm",
            "major_radius_mm",
            "minor_radius_mm",
            "circumradius_mm",
            "angle_deg",
            "first_angle_deg",
            "second_angle_deg",
            "cone_angle_deg",
            "pitch_mm",
            "turn_count",
            "side_count",
            "thickness_mm",
            "occurrence_count",
            "pattern_kind",
            "count_x",
            "count_y",
            "count_z",
            "interval_x_mm",
            "interval_y_mm",
            "interval_z_mm",
            "axis",
            "center_mm",
            "fuse",
            "circle_count",
            "polar_count",
            "symmetry",
            "radial_distance_mm",
            "tangential_distance_mm",
            "align",
            "align_mode",
            "reverse_path",
            "force_vertical",
            "start_offset_mm",
            "end_offset_mm",
            "extra_translation_mm",
            "tangent_vector",
            "vertical_vector",
            "reversed",
            "midplane",
            "symmetric",
            "left_handed",
            "closed",
            "solid",
            "ruled",
            "refine",
            "linearize",
            "max_degree",
            "x_min_mm",
            "x_max_mm",
            "x2_min_mm",
            "x2_max_mm",
            "y_min_mm",
            "y_max_mm",
            "z_min_mm",
            "z_max_mm",
            "z2_min_mm",
            "z2_max_mm",
        ):
            if properties.get(key) is not None:
                details.append(f"{key.replace('_', ' ')}={properties[key]}")
        references = []
        for key in (
            "profile_refs",
            "base_refs",
            "support_refs",
            "spine_refs",
            "section_refs",
            "original_feature_refs",
            "transformation_refs",
            "tool_refs",
            "point_refs",
        ):
            if properties.get(key):
                references.extend(map(str, properties[key]))
        line = f"Feature: {feature.get('name')}; type={feature.get('type')}"
        if details:
            line += "; " + "; ".join(details)
        if references:
            line += f"; references={', '.join(sorted(set(references)))}"
        lines.append(line + ".")
    quality = metadata.get("quality", {})
    if quality:
        lines.append(
            "Extraction quality: "
            + ", ".join(f"{key}={value}" for key, value in quality.items() if key != "warnings")
            + "."
        )
    manufacturing = metadata.get("manufacturing", {})
    for key, value in manufacturing.items():
        if value not in (None, ""):
            lines.append(f"Manufacturing {key.replace('_', ' ')}: {value}.")
    parameters = metadata.get("parameters", [])
    for parameter in parameters[:50]:
        if not isinstance(parameter, dict):
            continue
        lines.append(
            "Parameter: "
            f"{parameter.get('name')}={parameter.get('value')} {parameter.get('unit') or ''}; "
            f"expression={parameter.get('expression') or 'none'}; "
            f"key={parameter.get('is_key', False)}."
        )
    sketches = metadata.get("native", {}).get("sketches", [])
    for sketch in sketches[:50]:
        if not isinstance(sketch, dict):
            continue
        lines.append(
            "Sketch: "
            f"{sketch.get('label') or sketch.get('name')}; "
            f"geometry={sketch.get('geometry_count', 0)}; "
            f"construction geometry={sketch.get('construction_geometry_count', 0)}; "
            f"constraints={sketch.get('constraint_count', 0)}; "
            f"degrees of freedom={sketch.get('degrees_of_freedom')}; "
            f"fully constrained={sketch.get('fully_constrained')}; "
            f"attachment={sketch.get('map_mode') or 'Deactivated'}."
        )
        constraint_types = sorted(
            {
                str(value.get("type"))
                for value in sketch.get("constraints", [])
                if isinstance(value, dict) and value.get("type")
            }
        )
        if constraint_types:
            lines.append(f"Sketch constraint types: {', '.join(constraint_types)}.")
    parameter_graph = metadata.get("native", {}).get("parameter_graph", {})
    if isinstance(parameter_graph, dict):
        for edge in parameter_graph.get("edges", [])[:100]:
            if not isinstance(edge, dict):
                continue
            lines.append(
                f"Parameter dependency: {edge.get('source')} controls {edge.get('target')}."
            )
        key_parameters = parameter_graph.get("key_parameters", [])
        if key_parameters:
            lines.append(f"Key driving parameters: {', '.join(map(str, key_parameters[:50]))}.")
        cycles = parameter_graph.get("cycles", [])
        if cycles:
            lines.append(f"Parameter dependency cycles: {len(cycles)}.")
    configurations = metadata.get("native", {}).get("configurations", {})
    if isinstance(configurations, dict):
        active_values = configurations.get("active_values", [])
        variants = configurations.get("variants", [])
        if active_values:
            lines.append(f"Active configurations: {', '.join(map(str, active_values[:50]))}.")
        if variants:
            lines.append(f"Available configurations: {', '.join(map(str, variants[:100]))}.")
        for selector in configurations.get("selectors", [])[:50]:
            if isinstance(selector, dict):
                lines.append(
                    "Configuration selector: "
                    f"{selector.get('label') or selector.get('object')}."
                    f"{selector.get('property')}={selector.get('active')}."
                )
    assembly_joints = metadata.get("native", {}).get("assembly_joints", [])
    for joint in assembly_joints[:100]:
        if not isinstance(joint, dict):
            continue
        references = [
            f"{reference.get('role')}={reference.get('object')}"
            for reference in joint.get("references", [])
            if isinstance(reference, dict) and reference.get("object")
        ]
        line = (
            f"Assembly joint: {joint.get('label') or joint.get('name')}; "
            f"type={joint.get('type')}; grounded={joint.get('grounded', False)}; "
            f"suppressed={joint.get('suppressed', False)}; "
            f"degrees of freedom={joint.get('degrees_of_freedom')}"
        )
        if references:
            line += f"; connected objects={', '.join(references)}"
        lines.append(line + ".")
    reference_geometry = metadata.get("native", {}).get("reference_geometry", [])
    for datum in reference_geometry[:100]:
        if not isinstance(datum, dict):
            continue
        placement = datum.get("placement", {})
        position = placement.get("position_mm") if isinstance(placement, dict) else None
        line = (
            f"Reference geometry: {datum.get('label') or datum.get('name')}; "
            f"kind={datum.get('kind')}; map mode={datum.get('map_mode') or 'Deactivated'}"
        )
        if position:
            line += f"; position millimeters={position}"
        support = datum.get("support", [])
        support_names = [
            str(reference.get("object"))
            for reference in support
            if isinstance(reference, dict) and reference.get("object")
        ]
        if support_names:
            line += f"; attached to={', '.join(support_names)}"
        lines.append(line + ".")
    components = metadata.get("assembly", {}).get("components", [])
    for component in components[:100]:
        if not isinstance(component, dict):
            continue
        lines.append(
            "Assembly component: "
            f"{component.get('name')}; part number={component.get('part_number') or 'unknown'}; "
            f"quantity={component.get('quantity', 1)}; "
            f"configuration={component.get('configuration') or 'default'}; "
            f"dependency={component.get('dependency_status') or 'native'}."
        )
    extraction = metadata.get("extraction", {})
    capabilities = extraction.get("capabilities", {}) if isinstance(extraction, dict) else {}
    if capabilities:
        lines.append(
            "Extraction capabilities: "
            + ", ".join(f"{key}={value}" for key, value in sorted(capabilities.items()))
            + "."
        )
    return "\n".join(lines)
