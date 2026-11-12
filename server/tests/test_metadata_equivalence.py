from copy import deepcopy

from modelryx.metadata import compare_part_metadata
from test_metadata_schema import valid_metadata


def test_equivalent_cross_format_geometry_passes_phase0_tolerances() -> None:
    freecad = valid_metadata()
    step = deepcopy(freecad)
    step["source"]["system"] = "step"
    step["geometry"]["bounding_box"]["x_mm"] += 0.009
    step["geometry"]["shape"]["volume_mm3"] *= 1.0009
    step["geometry"]["shape"]["surface_area_mm2"] *= 0.9991
    step["quality"]["features"] = "unsupported"
    step.pop("features", None)
    assert compare_part_metadata(freecad, step) == []


def test_reports_geometry_outside_tolerance_with_field_paths() -> None:
    freecad = valid_metadata()
    step = deepcopy(freecad)
    step["geometry"]["bounding_box"]["z_mm"] += 0.02
    step["geometry"]["shape"]["volume_mm3"] *= 1.002
    step["geometry"]["topology"]["solid_count"] = 2
    differences = compare_part_metadata(freecad, step)
    assert [difference.field for difference in differences] == [
        "geometry.bounding_box.z_mm",
        "geometry.shape.volume_mm3",
        "geometry.topology.solid_count",
    ]


def test_ignores_format_specific_feature_availability() -> None:
    freecad = valid_metadata()
    freecad["features"] = {"source": "history", "summary": {"hole_count": 4}}
    step = deepcopy(freecad)
    step.pop("features")
    step["quality"]["features"] = "unsupported"
    assert compare_part_metadata(freecad, step) == []
