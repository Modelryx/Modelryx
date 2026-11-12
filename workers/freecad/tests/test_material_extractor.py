from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from material_extractor import MaterialExtractor


def test_partial_material_does_not_become_assembly_material() -> None:
    result = MaterialExtractor().extract(
        [{"material": "Steel", "density_kg_m3": 7850, "mass_kg": 1},
         {"material": None, "density_kg_m3": None, "mass_kg": None}], lambda _: {})
    assert result.records == [{}]
    assert result.evidence["material_complete"] is False
    assert result.capability == "partial"
    assert len(result.evidence["warnings"]) == 2


def test_mass_requires_all_components_to_have_known_positive_mass() -> None:
    shapes = [{"material": "Steel", "density_kg_m3": 7850, "mass_kg": 1},
              {"material": "Steel", "density_kg_m3": 7850, "mass_kg": 0}]
    result = MaterialExtractor().extract(shapes, lambda _: {"x_mm": 1})
    assert result.records[0] == {"material": {"name": "Steel"}, "density_kg_m3": 7850}
    shapes[1]["mass_kg"] = 2
    result = MaterialExtractor().extract(shapes, lambda _: {"x_mm": 1})
    assert result.records[0]["mass_kg"] == 3
    assert "center_of_mass" not in result.records[0]
    assert result.capability == "partial"
    assert any("Center of mass" in warning for warning in result.evidence["warnings"])
    for shape in shapes:
        shape["center_of_gravity_mm"] = [0, 0, 0]
    result = MaterialExtractor().extract(shapes, lambda _: {"x_mm": 1})
    assert result.records[0]["center_of_mass"] == {"x_mm": 1}
    shapes[1]["center_of_gravity_mm"] = [float("nan"), 0, 0]
    result = MaterialExtractor().extract(shapes, lambda _: {"x_mm": 1})
    assert "center_of_mass" not in result.records[0]
    assert result.capability == "partial"


def test_unverified_array_geometry_does_not_produce_mass_or_center_of_mass() -> None:
    shape = {
        "material": "Steel", "density_kg_m3": 7850, "mass_kg": 1,
        "center_of_gravity_mm": [0, 0, 0], "geometry_evidence_verified": False,
    }
    result = MaterialExtractor().extract([shape], lambda _: {"x_mm": 1})
    assert result.records[0] == {"material": {"name": "Steel"}, "density_kg_m3": 7850}
    assert result.capability == "partial"
    assert any("array element geometry" in warning for warning in result.evidence["warnings"])
    shape["geometry_evidence_verified"] = True
    verified = MaterialExtractor().extract([shape], lambda _: {"x_mm": 1})
    assert verified.records[0]["mass_kg"] == 1
