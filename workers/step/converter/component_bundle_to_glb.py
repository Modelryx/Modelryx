#!/usr/bin/env python3
"""Convert a trusted Modelryx component geometry bundle into an occurrence-aware GLB."""

import argparse
import json
import re
import tempfile
import zipfile
from pathlib import Path

import trimesh
import numpy as np
from glb_utils import export_double_sided_glb


def component_color(component):
    raw = component.get("color")
    if not (isinstance(raw, list) and len(raw) in {3, 4}
            and all(isinstance(value, (int, float)) for value in raw)):
        return None
    values = np.array([*raw[:3], raw[3] if len(raw) == 4 else 1.0], dtype=float)
    if not np.all(np.isfinite(values)):
        return None
    return np.rint(np.clip(values, 0.0, 1.0) * 255.0).astype(np.uint8)


_STEP_COLOR = re.compile(
    r"COLOUR_RGB\s*\(\s*'[^']*'\s*,\s*"
    r"([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*\)",
    re.IGNORECASE,
)


def step_file_color(path):
    """Return the most distinctive color embedded in an component STEP part."""
    try:
        text = Path(path).read_text(encoding="latin-1")
    except OSError:
        return None
    colors = []
    for match in _STEP_COLOR.finditer(text):
        rgb = np.clip(np.array([float(value) for value in match.groups()]), 0.0, 1.0)
        colors.append(rgb)
    if not colors:
        return None
    # A component STEP can also declare neutral edge/display colors. Prefer the
    # color with the greatest chroma, then the darker color for neutral assets.
    rgb = max(colors, key=lambda value: (float(np.ptp(value)), -float(np.mean(value))))
    return np.rint(np.append(rgb, 1.0) * 255.0).astype(np.uint8)


def component_transform(component, by_id=None, resolved=None):
    occurrence_id = str(component.get("occurrenceId") or "")
    resolved = {} if resolved is None else resolved
    if occurrence_id in resolved:
        return resolved[occurrence_id]
    raw = component.get("transform")
    if isinstance(raw, list) and len(raw) == 16 and all(isinstance(value, (int, float)) for value in raw):
        # The bundle contract serializes matrix cells row-by-row and stores translations in cm.
        transform = np.array(raw, dtype=float).reshape((4, 4))
        if component.get("transformUnits") == "centimeter":
            transform[:3, 3] *= 10.0
    else:
        translation = component.get("translation", [0.0, 0.0, 0.0])
        if not (isinstance(translation, list) and len(translation) == 3
                and all(isinstance(value, (int, float)) for value in translation)):
            raise ValueError(f"component transform is invalid: {component.get('file', '')}")
        transform = trimesh.transformations.translation_matrix(translation)
    # The bundle stores every occurrence transform in root-assembly space.
    # parentId describes navigation only and must not be multiplied into geometry.
    resolved[occurrence_id] = transform
    return transform


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert component STL bundle to GLB")
    parser.add_argument("input")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    source = Path(args.input)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(source) as archive:
            for entry in archive.infolist():
                target = (root / entry.filename).resolve()
                if root.resolve() not in target.parents:
                    raise ValueError("component bundle contains an unsafe path")
            archive.extractall(root)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        geometry_units = manifest.get("geometryUnits")
        if geometry_units is not None and geometry_units != "millimeter":
            raise ValueError("unsupported component geometry units")
        components = manifest.get("components")
        if not isinstance(components, list) or not components:
            raise ValueError("component bundle manifest contains no components")
        if geometry_units is not None:
            identities = set()
            for component in components:
                if not isinstance(component, dict):
                    raise ValueError("component identity is invalid")
                identity = component.get("occurrenceId")
                part = component.get("partId")
                raw = component.get("transform")
                if (not isinstance(identity, str) or not identity.strip() or identity in identities
                        or not isinstance(part, str) or not part.strip()
                        or component.get("transformUnits") != "millimeter"
                        or not isinstance(raw, list) or len(raw) != 16
                        or any(type(value) not in {int, float} for value in raw)
                        or not np.all(np.isfinite(raw)) or raw[12:] != [0, 0, 0, 1]):
                    raise ValueError("component identity or world transform is invalid")
                identities.add(identity)
        by_id = {str(item.get("occurrenceId")): item for item in components}
        # Avoid the special `world` name used by common glTF readers as their implicit base frame.
        # With an added unit-scale parent, such a child name can overwrite the reader's base frame.
        base_frame = "modelryx-components"
        while base_frame in by_id:
            base_frame += "-root"
        scene = trimesh.Scene(base_frame=base_frame)
        resolved = {}
        # Repeated placements share source geometry, not occurrence metadata or appearance.
        # Keep this cache local to the bundle so another job cannot reuse stale CAD data.
        geometry_cache = {}
        for index, component in enumerate(components):
            filename = component.get("file", "")
            path = (root / filename).resolve()
            if root.resolve() not in path.parents or path.suffix.lower() not in {".stl", ".step", ".stp"}:
                raise ValueError("component bundle references an invalid geometry path")
            if path not in geometry_cache:
                edge_segments = None
                source_color = None
                if path.suffix.lower() in {".step", ".stp"}:
                    from step_to_glb import StepToGlbConverter

                    converter = StepToGlbConverter(export_edges=True)
                    shape = converter.read_step_file(path)
                    vertices, faces = converter.tessellate_shape(shape)
                    source_mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
                    edge_vertices, edge_indices = converter.extract_edges(shape)
                    edge_segments = converter.edge_segments(edge_vertices, edge_indices)
                    source_color = step_file_color(path)
                else:
                    source_mesh = trimesh.load(path, force="mesh", process=True)
                if not isinstance(source_mesh, trimesh.Trimesh) or source_mesh.is_empty:
                    raise ValueError(f"component geometry is empty: {filename}")
                geometry_cache[path] = (source_mesh, edge_segments, source_color)
            source_mesh, edge_segments, source_color = geometry_cache[path]
            mesh = source_mesh.copy()
            color = component_color(component)
            if color is None:
                color = source_color
            if color is not None:
                mesh.visual.vertex_colors = np.tile(color, (len(mesh.vertices), 1))
            name = str(component.get("occurrenceId") or f"component-{index + 1}")
            metadata = {
                "partId": str(component.get("partId") or name),
                "occurrenceId": name,
                "nodeType": str(component.get("nodeType") or "body"),
            }
            if edge_segments:
                metadata["edgeSegments"] = list(edge_segments)
            transform = component_transform(component, by_id, resolved)
            mesh.metadata["extras"] = {"modelryx": metadata}
            scene.add_geometry(
                mesh,
                node_name=name,
                geom_name=name,
                transform=transform,
                metadata={"modelryx": metadata},
            )
        export_double_sided_glb(scene, args.output, source_units=geometry_units)


if __name__ == "__main__":
    main()
