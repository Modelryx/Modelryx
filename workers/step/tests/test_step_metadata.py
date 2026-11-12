from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

from converter.step_metadata import extract_part_metadata


def test_step_metadata_preserves_required_quality_contract() -> None:
    metadata = extract_part_metadata(BRepPrimAPI_MakeBox(10, 20, 30).Shape(), "box.step")

    assert metadata["source"]["worker"] == "modelryx-step-worker"
    assert metadata["source"]["worker_version"] == "2.0.0"
    assert metadata["quality"] == {
        "geometry": "complete",
        "physical": "unavailable",
        "features": "unsupported",
        "assembly": "not_applicable",
        "warnings": [
            "STEP does not provide a reliable material or mass without an assigned density.",
            "Native parametric feature history is not available in STEP exchange files.",
        ],
    }
