from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from OCC.Core.BRepGProp import brepgprop
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.GProp import GProp_GProps
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCC.Core.TopExp import topexp
from OCC.Core.TopTools import TopTools_IndexedMapOfShape


def extract_part_metadata(shape, source_name: str) -> dict:
    bounds = Bnd_Box()
    brepbndlib.Add(shape, bounds)
    xmin, ymin, zmin, xmax, ymax, zmax = bounds.Get()

    volume_props = GProp_GProps()
    surface_props = GProp_GProps()
    brepgprop.VolumeProperties(shape, volume_props)
    brepgprop.SurfaceProperties(shape, surface_props)

    counts: dict[str, int] = {}
    for name, kind in (
        ("solid_count", TopAbs_SOLID),
        ("face_count", TopAbs_FACE),
        ("edge_count", TopAbs_EDGE),
        ("vertex_count", TopAbs_VERTEX),
    ):
        indexed = TopTools_IndexedMapOfShape()
        topexp.MapShapes(shape, kind, indexed)
        counts[name] = indexed.Extent()

    return {
        "schema": "modelryx.part-metadata",
        "schema_version": "1.0",
        "source": {
            "system": "step",
            "worker": "modelryx-step-worker",
            "worker_version": os.environ.get("MODELRYX_STEP_WORKER_VERSION", "2.0.0"),
            "extracted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
        "identity": {"name": Path(source_name).stem, "kind": "part"},
        "geometry": {
            "bounding_box": {
                "x_mm": round(max(0.0, xmax - xmin), 6),
                "y_mm": round(max(0.0, ymax - ymin), 6),
                "z_mm": round(max(0.0, zmax - zmin), 6),
            },
            "shape": {
                "volume_mm3": round(max(0.0, volume_props.Mass()), 6),
                "surface_area_mm2": round(max(0.0, surface_props.Mass()), 6),
            },
            "topology": counts,
        },
        "assembly": {"is_assembly": False},
        "properties": {"source_format": "STEP"},
        "quality": {
            "geometry": "complete",
            "physical": "unavailable",
            "features": "unsupported",
            "assembly": "not_applicable",
            "warnings": [
                "STEP does not provide a reliable material or mass without an assigned density.",
                "Native parametric feature history is not available in STEP exchange files.",
            ],
        },
    }
