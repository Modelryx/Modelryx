import json
from pathlib import Path


def test_cad_corpus_manifest_has_valid_expected_documents() -> None:
    root = Path("tests/cad")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 1
    assert len(manifest["cases"]) >= 5
    names = [case["name"] for case in manifest["cases"]]
    assert len(names) == len(set(names))
    for case in manifest["cases"]:
        expected_path = root / case["expected"]
        assert expected_path.is_file(), expected_path
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        assert expected["identity"]["kind"] in {"part", "assembly"}
        assert set(expected["geometry"]["bounding_box"]) == {"x_mm", "y_mm", "z_mm"}
        assert expected["geometry"]["shape"]["volume_mm3"] >= 0
        assert expected["geometry"]["shape"]["surface_area_mm2"] >= 0


def test_corpus_cases_have_freecad_builders() -> None:
    source = Path("tests/cad/generate_and_verify_freecad.py").read_text(encoding="utf-8")
    manifest = json.loads(Path("tests/cad/manifest.json").read_text(encoding="utf-8"))
    for case in manifest["cases"]:
        assert f'"{case["name"]}":' in source
