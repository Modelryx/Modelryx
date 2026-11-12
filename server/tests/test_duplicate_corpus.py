import json
from pathlib import Path


def test_ground_truth_manifest_covers_phase1_relationships() -> None:
    manifest = json.loads(
        (Path(__file__).parent / "duplicates" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schemaVersion"] == 1
    cases = manifest["cases"]
    assert len({case["id"] for case in cases}) == len(cases)
    assert {case["expected"] for case in cases} == {
        "exact_file_duplicate",
        "engineering_duplicate",
        "variant",
        "similar",
        "different",
    }
