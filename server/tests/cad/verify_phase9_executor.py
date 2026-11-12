"""Exercise the controlled Phase 9 executor inside the real FreeCAD runtime."""

import FreeCAD  # type: ignore
import Part  # type: ignore

import hashlib
import json
import tempfile
from pathlib import Path

from execute_cad_plan import execute
from fcstd_to_modelryx_glb import convert_fcstd_to_modelryx_package


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="modelryx-phase9-") as directory:
        root = Path(directory)
        source = root / "source.fcstd"
        result = root / "result.fcstd"
        report = root / "report.json"
        plan = root / "plan.json"
        package = root / "result.pfg"
        document = FreeCAD.newDocument("Phase9ControlledMaterial")
        try:
            feature = document.addObject("Part::Feature", "Plate")
            feature.Shape = Part.makeBox(100, 80, 10)
            feature.addProperty("App::PropertyString", "Material", "Modelryx")
            feature.Material = "Aluminum 6061-T6"
            document.recompute()
            document.saveAs(str(source))
        finally:
            FreeCAD.closeDocument(document.Name)
        source_hash = digest(source)
        plan.write_text(
            json.dumps(
                {
                    "operation": "material.set",
                    "target_id": "document",
                    "parameter": "material",
                    "expected_value": "Aluminum 6061-T6",
                    "new_value": "Aluminum 7075-T6",
                    "source_sha256": source_hash,
                }
            ),
            encoding="utf-8",
        )
        execute(plan, source, result, report)
        assert digest(source) == source_hash
        observed = json.loads(report.read_text(encoding="utf-8"))
        assert observed["observed_after"] == "Aluminum 7075-T6"
        assert observed["geometry"]["all_shapes_valid"] is True
        assert observed["geometry"]["solid_count"] == 1
        convert_fcstd_to_modelryx_package(str(result), str(package))
        metadata = json.loads(package.with_suffix(".part-metadata.json").read_text())
        assert metadata["physical"]["material"]["name"] == "Aluminum 7075-T6"
        creation_source = root / "creation-source.fcstd"
        creation_result = root / "creation-result.fcstd"
        creation_report = root / "creation-report.json"
        creation_plan = root / "creation-plan.json"
        document = FreeCAD.newDocument("Phase9ControlledCreation")
        try:
            body = document.addObject("PartDesign::Body", "Body")
            base = body.newObject("PartDesign::Feature", "BaseSolid")
            base.Shape = Part.makeBox(20, 20, 5)
            document.recompute()
            document.saveAs(str(creation_source))
        finally:
            FreeCAD.closeDocument(document.Name)
        creation_hash = digest(creation_source)
        creation_plan.write_text(
            json.dumps(
                {
                    "operation": "sketch.create",
                    "target_id": "Body",
                    "parameter": "sketch",
                    "expected_value": {},
                    "new_value": {
                        "feature_id": "Modelryx_TestSketch",
                        "plane": "XY",
                        "geometry": "circle",
                        "radius_mm": 3,
                    },
                    "source_sha256": creation_hash,
                }
            ),
            encoding="utf-8",
        )
        execute(creation_plan, creation_source, creation_result, creation_report)
        assert digest(creation_source) == creation_hash
        creation_observed = json.loads(creation_report.read_text(encoding="utf-8"))
        assert creation_observed["observed_after"]["feature_id"] == "Modelryx_TestSketch"
        assert creation_observed["requested_postconditions_passed"] is True
        assert creation_observed["geometry"]["all_shapes_valid"] is True
        result_document = FreeCAD.open(str(creation_result))
        try:
            assert result_document.getObject("Modelryx_TestSketch") is not None
        finally:
            FreeCAD.closeDocument(result_document.Name)
        creation_package = root / "creation-result.pfg"
        convert_fcstd_to_modelryx_package(str(creation_result), str(creation_package))
        creation_metadata = json.loads(
            creation_package.with_suffix(".part-metadata.json").read_text()
        )
        creation_features = creation_metadata["features"]["items"]
        assert any(item["persistent_id"] == "Body" for item in creation_features)
        assert any(item["persistent_id"] == "Modelryx_TestSketch" for item in creation_features)
        unsafe = json.loads(creation_plan.read_text(encoding="utf-8"))
        unsafe["new_value"]["script"] = "arbitrary-code"
        creation_plan.write_text(json.dumps(unsafe), encoding="utf-8")
        try:
            execute(creation_plan, creation_source, root / "unsafe.fcstd", root / "unsafe.json")
        except ValueError as exc:
            assert "unsupported fields" in str(exc)
        else:
            raise AssertionError("Nested creation fields were not rejected")
    print("Phase 9 controlled executor integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
