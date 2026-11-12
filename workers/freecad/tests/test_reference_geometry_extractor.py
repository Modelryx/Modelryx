from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from reference_geometry_extractor import ReferenceGeometryExtractor


def test_provider_retains_valid_datum_when_another_support_is_unreadable() -> None:
    class Broken:
        Name = "Broken"
        TypeId = "PartDesign::Plane"
        PropertiesList = ()

        @property
        def AttachmentSupport(self):
            raise RuntimeError("unreadable support")

    valid = SimpleNamespace(Name="Plane", TypeId="PartDesign::Plane", PropertiesList=["Length"],
                            Length=20, AttachmentSupport=("body", ["Face1"]))
    result = ReferenceGeometryExtractor(
        lambda kind: "plane" if kind == "PartDesign::Plane" else None,
        lambda value: {"object": value[0], "subelements": value[1]} if value else None,
        lambda _: None, lambda value: (value, "mm"), lambda _: False,
    ).extract(SimpleNamespace(Objects=[valid, Broken()]))
    assert result.records == [{"name": "Plane", "label": "Plane", "kind": "plane",
                               "source_type": "PartDesign::Plane", "visible": False,
                               "map_mode": "Deactivated", "map_reversed": False,
                               "support": [{"object": "body", "subelements": ["Face1"]}],
                               "dimensions": {"length_mm": 20}}]
    assert result.evidence["skipped"] == 1 and result.capability == "partial"
    assert result.diagnostics[0]["code"] == "REFERENCE_GEOMETRY_EXTRACTION_FAILED"
