import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "freecad_material", Path(__file__).resolve().parents[1] / "converter/freecad_material.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize(
    ("properties", "material", "expected"),
    [
        ({}, SimpleNamespace(Name="Aluminum-6061-T6", UUID="assigned"), "Aluminum-6061-T6"),
        ({}, SimpleNamespace(Name="Default", UUID="7f9fd73b-50c9-41d8-b7b2-575a030c1eeb"), None),
        ({}, None, None),
        ({"Material": " Steel "}, SimpleNamespace(Name="Aluminum-6061-T6"), "Steel"),
        ({"Material": {"Name": "Aluminum-6061-T6"}}, None, "Aluminum-6061-T6"),
        ({"Material": ["not", "a material"]}, None, None),
        ({"Material": " "}, SimpleNamespace(Name="Aluminum-6061-T6"), "Aluminum-6061-T6"),
    ],
)
def test_material_assignment(properties, material, expected):
    obj = SimpleNamespace(PropertiesList=list(properties), ShapeMaterial=material, **properties)
    assert module.material_name(obj) == expected


@pytest.mark.parametrize(
    ("properties", "material", "expected"),
    [
        ({"DensityKgM3": 7850}, None, 7850.0),
        ({"Density": "2.70 g/cm^3"}, None, 2700.0),
        ({}, SimpleNamespace(Card={"Density": "7850 kg/m^3"}), 7850.0),
        ({"Density": 7.85}, None, None),
        ({"Density": "unknown"}, None, None),
        ({"DensityKgM3": float("inf")}, None, None),
        ({"DensityKgM3": float("nan")}, None, None),
        ({"Density": "0 kg/m^3"}, None, None),
    ],
)
def test_material_density_requires_explicit_units(properties, material, expected):
    obj = SimpleNamespace(PropertiesList=list(properties), ShapeMaterial=material, **properties)
    assert module.material_density_kg_m3(obj) == expected
