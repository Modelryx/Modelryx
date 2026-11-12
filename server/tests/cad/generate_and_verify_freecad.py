"""Generate the real FCStd corpus and verify normalized metadata in FreeCAD Python."""

from __future__ import annotations

# FreeCAD must initialize before standard-library imports.
# ruff: noqa: I001
import FreeCAD  # type: ignore
import Part  # type: ignore

import json
import math
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

MODELRYX_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MODELRYX_ROOT / "workers" / "freecad" / "converter"))

from fcstd_to_modelryx_glb import convert_fcstd_to_modelryx_package  # noqa: E402


def feature(document: Any, name: str, shape: Any) -> Any:
    obj = document.addObject("Part::Feature", name)
    obj.Label = name.replace("_", " ").title()
    obj.Shape = shape
    return obj


def simple_box(document: Any) -> None:
    feature(document, "SimpleBox", Part.makeBox(100, 60, 10))


def cylinder(document: Any) -> None:
    feature(document, "Cylinder", Part.makeCylinder(10, 50))


def plate_4_holes(document: Any) -> None:
    shape = Part.makeBox(100, 60, 10)
    for x, y in ((20, 15), (80, 15), (20, 45), (80, 45)):
        tool = Part.makeCylinder(4, 12, FreeCAD.Vector(x, y, -1))
        shape = shape.cut(tool)
    feature(document, "Plate4Holes", shape)


def no_material(document: Any) -> None:
    feature(document, "NoMaterial", Part.makeBox(30, 20, 5))


def small_assembly(document: Any) -> None:
    assembly = document.addObject("App::Part", "Assembly")
    first = feature(document, "Bracket1", Part.makeBox(20, 10, 5))
    second = feature(document, "Bracket2", Part.makeBox(20, 10, 5))
    second.Placement.Base = FreeCAD.Vector(30, 0, 0)
    assembly.addObject(first)
    assembly.addObject(second)


BUILDERS: dict[str, Callable[[Any], None]] = {
    "simple_box": simple_box,
    "cylinder": cylinder,
    "plate_4_holes": plate_4_holes,
    "no_material": no_material,
    "small_assembly": small_assembly,
}


def compare_expected(expected: Any, actual: Any, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(expected, dict):
        for key, value in expected.items():
            if key not in actual:
                errors.append(f"{path}.{key}: missing")
            else:
                errors.extend(compare_expected(value, actual[key], f"{path}.{key}"))
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if "bounding_box" in path:
            matches = math.isclose(expected, actual, rel_tol=0, abs_tol=0.01)
        elif path.endswith(("volume_mm3", "surface_area_mm2")):
            matches = math.isclose(expected, actual, rel_tol=0.001, abs_tol=1e-9)
        else:
            matches = expected == actual
        if not matches:
            errors.append(f"{path}: expected {expected}, got {actual}")
    elif expected != actual:
        errors.append(f"{path}: expected {expected!r}, got {actual!r}")
    return errors


def main() -> int:
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="modelryx-metadata-corpus-") as directory:
        output = Path(directory)
        for case in manifest["cases"]:
            name = case["name"]
            document = FreeCAD.newDocument(name)
            try:
                BUILDERS[name](document)
                document.recompute()
                source = output / f"{name}.FCStd"
                package = output / f"{name}.pfg"
                document.saveAs(str(source))
            finally:
                FreeCAD.closeDocument(document.Name)
            convert_fcstd_to_modelryx_package(str(source), str(package))
            actual = json.loads(package.with_suffix(".part-metadata.json").read_text())
            expected = json.loads((Path(__file__).parent / case["expected"]).read_text())
            failures.extend(f"{name}: {error}" for error in compare_expected(expected, actual))
    if failures:
        raise AssertionError("\n".join(failures))
    print(f"Verified {len(manifest['cases'])} FreeCAD metadata corpus cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
