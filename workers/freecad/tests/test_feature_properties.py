from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from feature_properties import normalized_feature_properties


def test_feature_registry_extracts_typed_dimensions_and_references() -> None:
    profile = SimpleNamespace(Name="Sketch")
    pad = SimpleNamespace(TypeId="PartDesign::Pad", PropertiesList=["Length", "Profile", "Reversed"],
                          Length=12.5, Profile=(profile, ["Face1"]), Reversed=True)
    result = normalized_feature_properties(pad)
    assert result["length_mm"] == 12.5
    assert result["profile_refs"] == ["Sketch"]
    assert result["reversed"] is True


def test_non_finite_dimensions_and_directions_are_not_reported() -> None:
    obj = SimpleNamespace(TypeId="Part::Box", PropertiesList=["Length", "Width", "Direction"],
                          Length=float("inf"), Width=5, Direction=SimpleNamespace(x=0, y=float("nan"), z=1))
    result = normalized_feature_properties(obj)
    assert "length_mm" not in result
    assert result["width_mm"] == 5
    assert "direction" not in result
