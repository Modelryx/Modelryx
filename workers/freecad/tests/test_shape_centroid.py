from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from shape_centroid import shape_center_of_mass_mm


def test_compound_centroid_requires_all_weighted_solids() -> None:
    first = SimpleNamespace(Volume=2, CenterOfMass=SimpleNamespace(x=0, y=0, z=0))
    second = SimpleNamespace(Volume=1, CenterOfMass=SimpleNamespace(x=9, y=0, z=0))
    face = SimpleNamespace(Area=1, CenterOfMass=SimpleNamespace(x=100, y=0, z=0))
    compound = SimpleNamespace(Solids=[first, second], Faces=[face])
    assert shape_center_of_mass_mm(compound) == [3.0, 0.0, 0.0]
    del second.CenterOfMass
    assert shape_center_of_mass_mm(compound) is None


def test_surface_only_centroid_uses_all_faces() -> None:
    face = SimpleNamespace(Area=2, CenterOfMass=SimpleNamespace(x=2, y=1, z=0))
    compound = SimpleNamespace(Solids=[], Faces=[face])
    assert shape_center_of_mass_mm(compound) == [2.0, 1.0, 0.0]
    face.Area = float("nan")
    assert shape_center_of_mass_mm(compound) is None
