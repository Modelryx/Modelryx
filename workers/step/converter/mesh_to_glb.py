#!/usr/bin/env python3
"""Convert an STL mesh to GLB without pretending the tessellated input is STEP."""

import argparse
from pathlib import Path

import trimesh
from glb_utils import export_double_sided_glb


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert STL mesh geometry to GLB")
    parser.add_argument("input")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()

    source = Path(args.input)
    if source.suffix.lower() != ".stl":
        raise ValueError("mesh converter accepts STL input only")
    # Processing welds STL's duplicated per-triangle vertices, which is required
    # for connected-component detection to identify solids rather than faces.
    loaded = trimesh.load(source, force="mesh", process=True)
    if not isinstance(loaded, trimesh.Trimesh) or loaded.is_empty:
        raise ValueError("STL contains no mesh geometry")
    # STL has no assembly graph, but disconnected solids remain separable by
    # triangle connectivity. Preserve each connected component as its own GLB
    # node so downstream CAD metadata can bind navigation occurrences to
    # independently selectable render objects.
    components = loaded.split(only_watertight=False)
    if not components:
        raise ValueError("STL contains no connected mesh components")
    scene = trimesh.Scene()
    for index, component in enumerate(components):
        name = f"component-{index + 1}"
        scene.add_geometry(component, node_name=name, geom_name=name)
    export_double_sided_glb(scene, args.output)


if __name__ == "__main__":
    main()
