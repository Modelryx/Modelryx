from __future__ import annotations

import sys
from pathlib import Path

CONVERTER = Path(__file__).resolve().parents[1] / "converter"
sys.path.insert(0, str(CONVERTER))

from techdraw_semantics import (
    capability_status,
    dimension_kind,
    entity_semantics,
    techdraw_entity_kind,
)


def test_cosmetic_geometry_is_not_gdt() -> None:
    assert techdraw_entity_kind("TechDraw::DrawViewCosmetic") == "COSMETIC"
    assert techdraw_entity_kind("TechDraw::DrawViewCosmeticVertex") == "COSMETIC"


def test_classifies_supported_engineering_annotations() -> None:
    assert techdraw_entity_kind("TechDraw::DrawViewDimension") == "DIMENSION"
    assert techdraw_entity_kind("TechDraw::DrawViewBalloon") == "BALLOON"
    assert techdraw_entity_kind("TechDraw::DrawViewSurfaceFinish") == "GDT"
    assert techdraw_entity_kind("TechDraw::DrawViewPart") == "VIEW"
    assert techdraw_entity_kind("TechDraw::DrawViewFutureFeature") is None


def test_dimension_capability_requires_dimension_evidence() -> None:
    assert capability_status(["VIEW"], target_kind="DIMENSION") == "not_applicable"
    assert capability_status(["DIMENSION"], target_kind="DIMENSION") == "complete"
    assert capability_status(["DIMENSION"], target_kind="DIMENSION", unknown_count=1) == "partial"
    assert capability_status(["VIEW"], target_kind="DIMENSION", unknown_count=1) == "partial"


def test_normalizes_dimension_types_conservatively() -> None:
    assert dimension_kind({"Type": "DistanceX"}) == "linear"
    assert dimension_kind({"Type": "Diameter"}) == "diameter"
    assert dimension_kind({"Type": "Angle3Pt"}) == "angular"
    assert dimension_kind({"FormatSpec": "R %value"}) is None
    assert dimension_kind({"Type": "NotDiameter"}) is None
    assert dimension_kind({"Type": "Area"}) is None


def test_extracts_dimension_tolerance_from_native_properties() -> None:
    semantics = entity_semantics(
        "TechDraw::DrawViewDimension",
        "DIMENSION",
        {
            "Type": "Diameter",
            "Tolerance": True,
            "OverTolerance": 0.2,
            "UnderTolerance": -0.1,
            "ToleranceFormatSpec": "%value %tolerance",
            "EqualTolerance": False,
            "ArbitraryTolerances": True,
            "FormatSpecOverTolerance": "+%.2f",
            "FormatSpecUnderTolerance": "-%.2f",
        },
    )
    assert semantics == {
        "dimension_kind": "diameter",
        "tolerance": {
            "enabled": True,
            "upper": 0.2,
            "lower": -0.1,
            "format": "%value %tolerance",
            "symmetric": False,
            "arbitrary": True,
            "upper_format": "+%.2f",
            "lower_format": "-%.2f",
        },
    }


def test_extracts_section_view_and_balloon_semantics() -> None:
    assert entity_semantics(
        "TechDraw::DrawViewSection",
        "VIEW",
        {"Scale": 2.0, "Direction": "Vector (0, 0, 1)"},
    ) == {
        "view_kind": "section",
        "scale": 2.0,
        "projection_direction": "Vector (0, 0, 1)",
    }
    assert entity_semantics("TechDraw::DrawViewBalloon", "BALLOON", {"BalloonText": "12"}) == {
        "item_reference": "12"
    }


def test_extracts_explicit_gdt_symbol_kind() -> None:
    assert entity_semantics("TechDraw::DrawViewWeldSymbol", "GDT", {}) == {
        "symbol_kind": "weld_symbol"
    }
    assert techdraw_entity_kind("TechDraw::DrawTileWeld") == "GDT"
    assert entity_semantics("TechDraw::DrawTileWeld", "GDT", {}) == {
        "symbol_kind": "weld_symbol_tile"
    }
