from copy import deepcopy

from modelryx.metadata import build_derived_metadata
from test_metadata_schema import valid_metadata


def test_builds_orientation_independent_geometry_fields() -> None:
    metadata = valid_metadata()
    derived = build_derived_metadata(metadata)
    assert derived["bbox_sorted_mm"] == [12, 80, 120]
    assert derived["bbox_min_mm"] == 12
    assert derived["bbox_mid_mm"] == 80
    assert derived["bbox_max_mm"] == 120
    assert derived["bounding_box_volume_mm3"] == 115200
    assert derived["fill_ratio"] == 45000 / 115200
    assert derived["aspect_xy"] == 1.5
    assert derived["aspect_yz"] == 80 / 12
    assert derived["aspect_xz"] == 10


def test_preserves_original_bounding_box_axis_order() -> None:
    metadata = valid_metadata()
    original = deepcopy(metadata["geometry"]["bounding_box"])
    build_derived_metadata(metadata)
    assert metadata["geometry"]["bounding_box"] == original


def test_prefers_normalized_material_for_search() -> None:
    metadata = valid_metadata()
    metadata["physical"] = {
        "material": {"name": "Al 6061 T6", "normalized_name": "aluminum_6061_t6"}
    }
    assert build_derived_metadata(metadata)["material_search"] == "aluminum_6061_t6"


def test_zero_bounding_volume_has_no_fill_or_invalid_ratios() -> None:
    metadata = valid_metadata()
    metadata["geometry"]["bounding_box"]["z_mm"] = 0
    derived = build_derived_metadata(metadata)
    assert derived["fill_ratio"] is None
    assert derived["aspect_yz"] is None
    assert derived["aspect_xz"] is None


def test_tolerance_count_excludes_weld_datum_surface_and_disabled_tolerances() -> None:
    metadata = valid_metadata()
    metadata["drawings"] = [{"entities": [
        *({"type": "GDT", "symbol_kind": kind} for kind in (
            "datum_feature", "surface_finish", "weld_symbol", "weld_symbol_tile"
        )),
        {"type": "GDT", "symbol_kind": "geometric_tolerance"},
        {"type": "DIMENSION", "tolerance": {"enabled": False, "upper": 0.0}},
        {"type": "DIMENSION", "tolerance": {"enabled": True, "upper": 0.1}},
        {"type": "TOLERANCE"},
    ]}]
    assert build_derived_metadata(metadata)["tolerance_count"] == 3


def test_tolerance_count_is_unknown_for_incomplete_or_ambiguous_drawing_evidence() -> None:
    metadata = valid_metadata()
    metadata["drawings"] = [{"entities": [{"type": "GDT"}]}]
    assert build_derived_metadata(metadata)["tolerance_count"] is None
    metadata["drawings"][0]["entities"] = [{"type": "DIMENSION", "tolerance": {"upper": 0.1}}]
    assert build_derived_metadata(metadata)["tolerance_count"] is None
    metadata["drawings"][0]["entities"] = [{"type": "GDT", "symbol_kind": "geometric_tolerance"}]
    metadata["extraction"] = {"capabilities": {"gdt": "partial", "drawing_dimensions": "complete"}}
    assert build_derived_metadata(metadata)["tolerance_count"] is None
