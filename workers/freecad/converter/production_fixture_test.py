"""Native production-release fixtures for the supported FreeCAD runtime."""

# FreeCAD must initialize before most standard-library imports in its bundled runtime.
# ruff: noqa: I001

import FreeCAD  # type: ignore
import Mesh  # type: ignore
import Part  # type: ignore
import Sketcher  # type: ignore

import argparse
import json
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import trimesh
from modelryx.metadata import validate_part_metadata
from modelryx.cad.inspection import inspect_metadata_snapshot
from modelryx.cad.models import CADParameterChangeRequest
from modelryx.cad.plans import resolve_parameter_change
from fcstd_to_modelryx_glb import convert_fcstd_to_modelryx_package, extract_fcstd_part_metadata
from execute_cad_plan import execute, sha256_file
from document_health import recompute_for_extraction


SUPPORTED_FREECAD_VERSION = ("1", "1", "3")


def save_document(path: Path, builder) -> None:
    document = FreeCAD.newDocument(path.stem.replace("-", "_"))
    try:
        builder(document)
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def convert(root: Path, name: str) -> tuple[dict, dict, trimesh.Scene]:
    source = root / f"{name}.FCStd"
    package_path = root / f"{name}.pfg"
    convert_fcstd_to_modelryx_package(str(source), str(package_path))
    with zipfile.ZipFile(package_path) as package:
        preview = json.loads(package.read("model-preview.json"))
        diagnostics = json.loads(package.read("diagnostics.json"))
        glb = root / f"{name}.glb"
        glb.write_bytes(package.read("model.glb"))
    return preview, diagnostics, trimesh.load_scene(glb)


def multi_body(document) -> None:
    for index, offset in enumerate((0, 25), start=1):
        body = document.addObject("PartDesign::Body", f"Body{index}")
        feature = body.newObject("PartDesign::Feature", f"Feature{index}")
        feature.Shape = Part.makeBox(10, 10, 10)
        body.Placement.Base = FreeCAD.Vector(offset, 0, 0)


def sketch_edit_fixture(document) -> None:
    document.addObject("Part::Box", "Solid")
    sketch = document.addObject("Sketcher::SketchObject", "Sketch")
    circle = sketch.addGeometry(Part.Circle(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), 5), False)
    sketch.addConstraint(Sketcher.Constraint("Radius", circle, 5))


def controlled_pad_fixture(document) -> None:
    body = document.addObject("PartDesign::Body", "Body")
    profile = body.newObject("Sketcher::SketchObject", "Profile")
    points = [FreeCAD.Vector(x, y, 0) for x, y in ((0, 0), (10, 0), (10, 10), (0, 10))]
    for index in range(4):
        profile.addGeometry(Part.LineSegment(points[index], points[(index + 1) % 4]), False)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = profile
    pad.Length = 10


def verify_reliability_fixtures(root: Path) -> None:
    source = root / "controlled-pad.FCStd"
    save_document(source, controlled_pad_fixture)
    source_hash = sha256_file(source)
    reversed_source = root / "controlled-reversed-pad.FCStd"
    def reversed_pad(document):
        controlled_pad_fixture(document)
        document.getObject("Pad").Reversed = True

    save_document(reversed_source, reversed_pad)
    reversed_hash = sha256_file(reversed_source)
    operations = [
        ("feature.parameter.set_value", "Pad", "length_mm", 10, 12),
        ("material.set", "document", "material", None, "Steel"),
        ("sketch.create", "Body", "feature", {}, {
            "feature_id": "NewSketch", "geometry": "circle", "plane": "XY", "radius_mm": 2,
        }),
        ("feature.create_pad", "Profile", "feature", {}, {
            "feature_id": "NewPad", "length_mm": 12,
        }),
        ("feature.create_pocket", "Profile", "feature", {}, {
            "feature_id": "NewPocket", "depth_mm": 3,
        }),
        ("feature.create_hole", "Body", "feature", {}, {
            "feature_id": "NewHole", "plane": "XY", "x_mm": 5, "y_mm": 5,
            "diameter_mm": 2, "depth_mm": 3,
        }),
        ("feature.create_fillet", "Pad", "feature", {}, {
            "feature_id": "NewFillet", "edge_indices": [1], "radius_mm": 1,
        }),
    ]
    for index, (operation, target, parameter, expected, value) in enumerate(operations):
        native_source = reversed_source if operation in {
            "feature.create_pocket", "feature.create_hole",
        } else source
        plan = root / f"reliability-plan-{index}.json"
        output, report = root / f"reliability-{index}.FCStd", root / f"reliability-{index}.json"
        plan.write_text(json.dumps({
            "operation": operation, "target_id": target, "parameter": parameter,
            "expected_value": expected, "new_value": value, "source_sha256": sha256_file(native_source),
        }))
        try:
            execute(plan, native_source, output, report)
        except (ValueError, RuntimeError) as error:
            raise AssertionError(f"Native reliability fixture failed: {operation}: {error}") from error
        assert json.loads(report.read_text())["requested_postconditions_passed"] is True
        assert sha256_file(source) == source_hash
        assert sha256_file(reversed_source) == reversed_hash

    original_open = FreeCAD.openDocument
    def incorrect_saved_value(path):
        native_document = original_open(path)
        native_document.getObject("Pad").Length = 999
        return native_document

    rejected_report = root / "rejected-saved-result.json"
    rejected_plan = root / "rejected-saved-plan.json"
    rejected_plan.write_text(json.dumps({
        "operation": "feature.parameter.set_value", "target_id": "Pad", "parameter": "length_mm",
        "expected_value": 10, "new_value": 12, "source_sha256": source_hash,
    }))
    FreeCAD.openDocument = incorrect_saved_value
    try:
        try:
            execute(rejected_plan, source, root / "rejected-saved.FCStd", rejected_report)
        except ValueError as error:
            assert "Saved native parameter postcondition failed" in str(error)
        else:
            raise AssertionError("An incorrect reopened parameter produced a success report")
        assert not rejected_report.exists()
    finally:
        FreeCAD.openDocument = original_open

    document = FreeCAD.openDocument(str(source))
    try:
        def observed_recompute():
            document.recompute()
            document.getObject("Body").Placement.Base = FreeCAD.Vector(10, 0, 0)
            return True

        # Use native placement matrices with a controlled recompute side effect;
        # this does not claim availability of a native assembly solver-status API.
        succeeded, error, diagnostics = recompute_for_extraction(SimpleNamespace(
            Objects=document.Objects, recompute=observed_recompute,
        ))
        assert succeeded and error is None
        assert any(d["code"] == "RECOMPUTE_CHANGED_PLACEMENTS" for d in diagnostics)
    finally:
        FreeCAD.closeDocument(document.Name)
    assert sha256_file(source) == source_hash

    import fcstd_to_modelryx_glb as converter

    original_recompute = converter.recompute_for_extraction
    def moved_recompute(native_document):
        def recompute():
            native_document.recompute()
            native_document.getObject("Body").Placement.Base = FreeCAD.Vector(10, 0, 0)
            return True

        return recompute_for_extraction(SimpleNamespace(
            Objects=native_document.Objects, recompute=recompute,
        ))

    converter.recompute_for_extraction = moved_recompute
    try:
        metadata_path = root / "moved-metadata.json"
        extract_fcstd_part_metadata(str(source), str(metadata_path))
        metadata = json.loads(metadata_path.read_text())
        assert metadata["quality"]["geometry"] == "partial"
        assert any("Recompute changed" in warning for warning in metadata["quality"]["warnings"])
        package_path = root / "moved.pfg"
        convert_fcstd_to_modelryx_package(str(source), str(package_path))
        with zipfile.ZipFile(package_path) as package:
            diagnostics = json.loads(package.read("diagnostics.json"))
        assert any(w["code"] == "RECOMPUTE_CHANGED_PLACEMENTS" for w in diagnostics["warnings"])
    finally:
        converter.recompute_for_extraction = original_recompute
    assert sha256_file(source) == source_hash


def sketch_angle_edit_fixture(document) -> None:
    document.addObject("Part::Box", "Solid")
    sketch = document.addObject("Sketcher::SketchObject", "Sketch")
    line = sketch.addGeometry(
        Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(5, 5, 0)), False
    )
    sketch.addConstraint(Sketcher.Constraint("Angle", line, 0.7853981633974483))


def open_surface(document) -> None:
    surface = document.addObject("Part::Feature", "OpenSurface")
    surface.Shape = Part.makePlane(30, 20)


def subtractive_prism(document) -> None:
    body = document.addObject("PartDesign::Body", "Body")
    base = body.newObject("PartDesign::AdditiveBox", "Base")
    base.Length = base.Width = base.Height = 20
    document.recompute()
    cut = body.newObject("PartDesign::SubtractivePrism", "PrismCut")
    cut.Circumradius = 2
    cut.Height = 20
    cut.Placement.Base = FreeCAD.Vector(10, 10, 0)


def wire_only(document) -> None:
    wire = document.addObject("Part::Feature", "WireOnly")
    wire.Shape = Part.makePolygon(
        [
            FreeCAD.Vector(0, 0, 0),
            FreeCAD.Vector(10, 0, 0),
            FreeCAD.Vector(10, 10, 0),
        ]
    )


def mesh_only(document) -> None:
    feature = document.addObject("Mesh::Feature", "ScanMesh")
    mesh = Mesh.Mesh()
    mesh.addFacet(
        FreeCAD.Vector(0, 0, 0),
        FreeCAD.Vector(20, 0, 0),
        FreeCAD.Vector(0, 10, 5),
    )
    feature.Mesh = mesh


def techdraw_page(document) -> None:
    feature = document.addObject("Part::Feature", "DrawingSource")
    feature.Shape = Part.makeBox(20, 10, 5)
    page = document.addObject("TechDraw::DrawPage", "Page")
    template = document.addObject("TechDraw::DrawSVGTemplate", "Template")
    template.Template = str(
        Path(FreeCAD.getResourceDir())
        / "Mod"
        / "TechDraw"
        / "Templates"
        / "Default_Template_A4_Landscape.svg"
    )
    page.Template = template
    view = document.addObject("TechDraw::DrawViewPart", "DrawingView")
    view.Source = [feature]
    page.addView(view)
    document.recompute()
    dimension = document.addObject("TechDraw::DrawViewDimension", "LengthDimension")
    dimension.Type = "DistanceX"
    dimension.References2D = [(view, ["Edge0"])]
    dimension.EqualTolerance = False
    dimension.OverTolerance = 0.1
    dimension.UnderTolerance = -0.05
    page.addView(dimension)
    angular = document.addObject("TechDraw::DrawViewDimension", "AngleDimension")
    angular.Type = "Angle"
    angular.EqualTolerance = False
    angular.OverTolerance = 1.25
    angular.UnderTolerance = -0.5
    angular.References2D = [(view, ["Edge0", "Edge2"])]
    page.addView(angular)


def techdraw_circular_dimensions(document) -> None:
    cylinder = document.addObject("Part::Feature", "Cylinder")
    cylinder.Shape = Part.makeCylinder(5, 10)
    page = document.addObject("TechDraw::DrawPage", "Page")
    template = document.addObject("TechDraw::DrawSVGTemplate", "Template")
    template.Template = str(
        Path(FreeCAD.getResourceDir())
        / "Mod"
        / "TechDraw"
        / "Templates"
        / "Default_Template_A4_Landscape.svg"
    )
    page.Template = template
    view = document.addObject("TechDraw::DrawViewPart", "CircleView")
    view.Source = [cylinder]
    view.Direction = FreeCAD.Vector(0, 0, 1)
    page.addView(view)
    document.recompute()
    for name, dimension_type in (("Radius", "Radius"), ("Diameter", "Diameter")):
        dimension = document.addObject("TechDraw::DrawViewDimension", name)
        dimension.Type = dimension_type
        dimension.References2D = [(view, ["Edge0"])]
        if name == "Radius":
            dimension.EqualTolerance = True
            dimension.OverTolerance = 0.25
        page.addView(dimension)
    for name, type_id in (
        ("Section", "TechDraw::DrawViewSection"),
        ("Detail", "TechDraw::DrawViewDetail"),
    ):
        derived = document.addObject(type_id, name)
        derived.BaseView = view
        page.addView(derived)
    balloon = document.addObject("TechDraw::DrawViewBalloon", "Balloon")
    balloon.SourceView = view
    balloon.Text = "42"
    page.addView(balloon)
    weld = document.addObject("TechDraw::DrawWeldSymbol", "Weld")
    weld.FieldWeld = True
    weld.AllAround = True
    weld.AlternatingWeld = True
    weld.TailText = "WELD SPEC"
    page.addView(weld)
    symbol = document.addObject("TechDraw::DrawViewSymbol", "GenericSymbol")
    symbol.Symbol = '<svg xmlns="http://www.w3.org/2000/svg"><text>Ra 3.2</text></svg>'
    page.addView(symbol)
    symbols = Path(FreeCAD.getResourceDir()) / "Mod" / "TechDraw" / "Symbols"
    for name, path in (("DatumSymbol", "misc/Datum_Right.svg"),
                       ("SurfaceSymbol", "surface-roughness-symbols/Roughness.svg"),
                       ("ToleranceFrame", "gd-and-t/FlatnessFrame.svg"),
                       ("ToleranceSymbol", "gd-and-t/position.svg")):
        native_symbol = document.addObject("TechDraw::DrawViewSymbol", name)
        native_symbol.Symbol = (symbols / path).read_text(encoding="utf-8")
        page.addView(native_symbol)
    tiles = [obj for obj in document.Objects if getattr(obj, "TileParent", None) == weld]
    assert tiles
    tiles[0].LeftText = "8"


def large_assembly(document) -> None:
    source = document.addObject("Part::Feature", "SharedFastener")
    source.Shape = Part.makeCylinder(2, 8)
    source.Label = "Shared fastener source"
    assembly = document.addObject("App::Part", "LargeAssembly")
    assembly.Label = "Large assembly"
    for index in range(300):
        link = document.addObject("App::Link", f"Fastener{index:04d}")
        link.LinkedObject = source
        link.Label = f"Fastener {index + 1}"
        link.Placement.Base = FreeCAD.Vector((index % 30) * 6, (index // 30) * 6, 0)
        assembly.addObject(link)


def scaled_link_assembly(document) -> None:
    source = document.addObject("Part::Feature", "Source")
    source.Shape = Part.makeBox(2, 3, 4)
    assembly = document.addObject("App::Part", "ScaledAssembly")
    for index in range(3):
        link = document.addObject("App::Link", f"Link{index}")
        link.LinkedObject = source
        link.Placement.Base = FreeCAD.Vector(index * 10, 0, 0)
        if index == 2:
            link.Scale = 2
        assembly.addObject(link)


def workbench_marker(model: str):
    def build(document) -> None:
        assembly = document.addObject("App::Part", f"{model}Assembly")
        assembly.Label = f"{model} fixture"
        component = document.addObject("Part::Feature", f"{model}Component")
        component.Shape = Part.makeBox(5, 5, 5)
        assembly.addObject(component)

    return build


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compatibility-candidate",
        action="store_true",
        help="Run the release fixtures on a non-production FreeCAD runtime without changing support policy.",
    )
    args = parser.parse_args()
    installed = tuple(str(value) for value in FreeCAD.Version()[:3])
    assert args.compatibility_candidate or installed == SUPPORTED_FREECAD_VERSION, (
        f"Release fixture image uses FreeCAD {installed}, expected {SUPPORTED_FREECAD_VERSION}."
    )
    with tempfile.TemporaryDirectory(prefix="modelryx-production-fixtures-") as directory:
        root = Path(directory)
        verify_reliability_fixtures(root)

        edit_source = root / "sketch-edit.FCStd"
        save_document(edit_source, sketch_edit_fixture)
        inspection_path = root / "sketch-edit-metadata.json"
        extract_fcstd_part_metadata(str(edit_source), str(inspection_path))
        inspected = inspect_metadata_snapshot(
            json.loads(inspection_path.read_text()), revision_id="fixture",
            source_sha256=sha256_file(edit_source),
        )
        edit_request = CADParameterChangeRequest(
            target_id="Sketch", parameter="Constraints[0]", value=7, unit="mm"
        )
        assert resolve_parameter_change(edit_request, inspected) == (
            "SKETCH", 5.0, "mm", "sketch.constraint.set_value"
        )
        plan = {"operation": "sketch.constraint.set_value", "target_id": "Sketch",
                "parameter": "Constraints[0]", "expected_value": 5.0, "new_value": 7.0,
                "unit": "mm", "source_sha256": sha256_file(edit_source)}
        edit_plan, edit_output, edit_report = root / "plan.json", root / "edited.FCStd", root / "edit-report.json"
        edit_plan.write_text(json.dumps(plan), encoding="utf-8")
        execute(edit_plan, edit_source, edit_output, edit_report)
        report = json.loads(edit_report.read_text())
        assert report["observed_before"] == 5.0 and report["observed_after"] == 7.0
        assert report["output_sha256"] == sha256_file(edit_output)
        assert sha256_file(edit_source) == plan["source_sha256"]
        angle_source = root / "sketch-angle.FCStd"
        save_document(angle_source, sketch_angle_edit_fixture)
        angle_plan = {**plan, "source_sha256": sha256_file(angle_source), "unit": "deg",
                      "expected_value": 45.0, "new_value": 60.0}
        edit_plan.write_text(json.dumps(angle_plan), encoding="utf-8")
        execute(edit_plan, angle_source, root / "angle-result.FCStd", root / "angle-report.json")
        angle_report = json.loads((root / "angle-report.json").read_text())
        assert abs(angle_report["observed_after"] - 60) < 1e-7
        for change in ({"unit": "deg"}, {"expected_value": 9.0}, {"parameter": "Constraints[99]"}):
            edit_plan.write_text(json.dumps({**plan, **change}), encoding="utf-8")
            try:
                execute(edit_plan, edit_source, root / "invalid.FCStd", root / "invalid.json")
            except (ValueError, RuntimeError):
                pass
            else:
                raise AssertionError(f"Invalid sketch edit was accepted: {change}")
        assert not (root / "invalid.FCStd").exists()
        for state in ("reference", "inactive", "expression"):
            def rejected_sketch(document):
                sketch_edit_fixture(document)
                sketch = document.getObject("Sketch")
                if state == "reference":
                    sketch.setDriving(0, False)
                elif state == "inactive":
                    sketch.setActive(0, False)
                else:
                    sketch.setExpression("Constraints[0]", "5 mm")

            rejected_source = root / f"{state}.FCStd"
            save_document(rejected_source, rejected_sketch)
            edit_plan.write_text(json.dumps({**plan, "source_sha256": sha256_file(rejected_source)}))
            try:
                execute(edit_plan, rejected_source, root / "invalid.FCStd", root / "invalid.json")
            except (ValueError, RuntimeError):
                pass
            else:
                raise AssertionError(f"{state} sketch constraint was editable")
        assert not (root / "invalid.FCStd").exists()

        save_document(root / "multi-body.FCStd", multi_body)
        preview, diagnostics, scene = convert(root, "multi-body")
        bodies = [item for item in preview["items"] if item["nodeType"] == "body"]
        assert len(bodies) == 2
        assert diagnostics["statistics"]["meshes"] == 1
        assert len(scene.graph.nodes_geometry) == 2

        save_document(root / "open-surface.FCStd", open_surface)
        convert(root, "open-surface")
        metadata = json.loads((root / "open-surface.part-metadata.json").read_text())
        assert metadata["geometry"]["topology"]["solid_count"] == 0
        assert metadata["geometry"]["topology"]["face_count"] == 1

        save_document(root / "subtractive-prism.FCStd", subtractive_prism)
        convert(root, "subtractive-prism")
        prism_metadata = json.loads((root / "subtractive-prism.part-metadata.json").read_text())
        validate_part_metadata(prism_metadata)
        prism = next(
            item
            for item in prism_metadata["features"]["items"]
            if item["persistent_id"] == "PrismCut"
        )
        assert prism["type"] == "POCKET"
        assert prism["properties"]["circumradius_mm"] == 2
        assert prism["properties"]["height_mm"] == 20

        save_document(root / "wire-only.FCStd", wire_only)
        _, diagnostics, _ = convert(root, "wire-only")
        metadata = json.loads((root / "wire-only.part-metadata.json").read_text())
        assert diagnostics["statistics"]["triangles"] > 0
        assert metadata["geometry"]["topology"]["face_count"] == 0
        assert metadata["geometry"]["topology"]["edge_count"] == 2
        extract_fcstd_part_metadata(str(root / "wire-only.FCStd"), str(root / "wire-metadata.json"))
        wire_metadata = json.loads((root / "wire-metadata.json").read_text())
        metadata["source"].pop("extracted_at")
        wire_metadata["source"].pop("extracted_at")
        assert wire_metadata == metadata

        save_document(root / "mesh-only.FCStd", mesh_only)
        _, diagnostics, scene = convert(root, "mesh-only")
        metadata = json.loads((root / "mesh-only.part-metadata.json").read_text())
        assert diagnostics["statistics"]["triangles"] == 1
        assert len(scene.graph.nodes_geometry) == 1
        assert metadata["geometry"]["topology"]["face_count"] == 1
        assert metadata["geometry"]["topology"]["vertex_count"] == 3
        assert metadata["geometry"]["bounding_box"] == {
            "x_mm": 20.0,
            "y_mm": 10.0,
            "z_mm": 5.0,
        }
        extract_fcstd_part_metadata(str(root / "mesh-only.FCStd"), str(root / "mesh-metadata.json"))
        mesh_metadata = json.loads((root / "mesh-metadata.json").read_text())
        metadata["source"].pop("extracted_at")
        mesh_metadata["source"].pop("extracted_at")
        assert mesh_metadata == metadata

        save_document(root / "techdraw.FCStd", techdraw_page)
        convert(root, "techdraw")
        drawing_metadata = json.loads((root / "techdraw.part-metadata.json").read_text())
        validate_part_metadata(drawing_metadata)
        dimension = next(
            item
            for item in drawing_metadata["drawings"][0]["entities"]
            if item["stable_id"] == "LengthDimension"
        )
        assert dimension["dimension_kind"] == "linear"
        assert dimension["tolerance"]["symmetric"] is False
        assert dimension["tolerance"]["upper"] == 0.1
        assert dimension["tolerance"]["lower"] == -0.05
        assert "enabled" not in dimension["tolerance"]
        assert "value" in dimension, "TechDraw could not resolve the projected length dimension."
        assert isinstance(dimension["value"], (int, float))
        assert not isinstance(dimension["value"], bool)
        assert dimension["unit"] == "mm"
        assert dimension["drawing_references"][0]["object"] == "DrawingView"
        assert dimension["drawing_references"][0]["subelements"] == ["Edge0"]
        assert "model_references" not in dimension
        view = next(
            item
            for item in drawing_metadata["drawings"][0]["entities"]
            if item["stable_id"] == "DrawingView"
        )
        assert view["model_references"][0]["object"] == "DrawingSource"
        angular = next(
            item
            for item in drawing_metadata["drawings"][0]["entities"]
            if item["stable_id"] == "AngleDimension"
        )
        assert angular["dimension_kind"] == "angular"
        assert angular["value"] == 90, angular["value"]
        assert angular["unit"] == "deg"
        assert angular["tolerance"]["upper"] == 1.25
        assert angular["tolerance"]["lower"] == -0.5
        with zipfile.ZipFile(root / "techdraw.pfg") as package:
            manifest = json.loads(package.read("manifest.json"))
            assert len(manifest["drawings"]) == 1
            drawing = manifest["drawings"][0]
            assert drawing["edgeCount"] > 0
            assert package.read(drawing["file"]).startswith(b"\x89PNG\r\n\x1a\n")

        circular_source = root / "circular-dimensions.FCStd"
        circular_metadata_path = root / "circular-dimensions.json"
        save_document(circular_source, techdraw_circular_dimensions)
        extract_fcstd_part_metadata(str(circular_source), str(circular_metadata_path))
        circular_metadata = json.loads(circular_metadata_path.read_text())
        validate_part_metadata(circular_metadata)
        dimensions = {
            item["stable_id"]: item
            for item in circular_metadata["drawings"][0]["entities"]
            if item["type"] == "DIMENSION"
        }
        for name, kind, value in (("Radius", "radial", 5), ("Diameter", "diameter", 10)):
            assert dimensions[name]["dimension_kind"] == kind
            assert dimensions[name]["value"] == value
            assert dimensions[name]["unit"] == "mm"
            assert dimensions[name]["drawing_references"][0]["subelements"] == ["Edge0"]
        assert dimensions["Radius"]["tolerance"]["symmetric"] is True
        assert dimensions["Radius"]["tolerance"]["upper"] == 0.25
        assert dimensions["Radius"]["tolerance"]["lower"] == -0.25
        derived_entities = {
            item["stable_id"]: item for item in circular_metadata["drawings"][0]["entities"]
        }
        assert derived_entities["CircleView"]["projection_direction"] == [0, 0, 1]
        for name in ("Section", "Detail", "Balloon"):
            assert derived_entities[name]["drawing_references"][0]["object"] == "CircleView"
        assert derived_entities["Balloon"]["item_reference"] == "42"
        assert derived_entities["Weld"]["symbol_kind"] == "weld_symbol"
        assert derived_entities["GenericSymbol"]["type"] == "ANNOTATION"
        from techdraw_semantics import KNOWN_SYMBOL_HASHES
        for name, kind in (("DatumSymbol", "datum_feature"), ("SurfaceSymbol", "surface_finish"),
                           ("ToleranceFrame", "geometric_tolerance"),
                           ("ToleranceSymbol", "geometric_tolerance")):
            entity = derived_entities[name]
            if entity["properties"]["symbol_sha256"] in KNOWN_SYMBOL_HASHES:
                assert entity["type"] == "GDT" and entity["symbol_kind"] == kind
            else:
                assert entity["type"] == "ANNOTATION"
        assert len(derived_entities["GenericSymbol"]["properties"]["symbol_sha256"]) == 64
        assert "Symbol" not in derived_entities["GenericSymbol"]["properties"]
        assert derived_entities["Weld"]["properties"]["FieldWeld"] is True
        assert derived_entities["Weld"]["weld_details"] == {
            "field_weld": True, "all_around": True, "alternating": True, "tail_text": "WELD SPEC"
        }
        weld_tiles = [
            item
            for item in derived_entities.values()
            if item.get("symbol_kind") == "weld_symbol_tile"
        ]
        assert any(item["properties"].get("LeftText") == "8" for item in weld_tiles)
        assert any(item["weld_details"].get("left_text") == "8" for item in weld_tiles)
        assert all(item["drawing_references"][0]["object"] == "Weld" for item in weld_tiles)
        assert circular_metadata["extraction"]["capabilities"]["gdt"] == "partial"

        corrupt = root / "corrupt.FCStd"
        corrupt.write_bytes(b"PK\x03\x04not-a-valid-freecad-document")
        try:
            convert_fcstd_to_modelryx_package(str(corrupt), str(root / "corrupt.pfg"))
        except Exception:  # noqa: BLE001, S110 - native failures vary by FreeCAD release
            pass
        else:
            raise AssertionError("Corrupt FCStd input was accepted")

        save_document(root / "large-assembly.FCStd", large_assembly)
        preview, diagnostics, scene = convert(root, "large-assembly")
        fasteners = [item for item in preview["items"] if item["name"].startswith("Fastener ")]
        assert len(fasteners) == 300
        assert len({item["occurrenceId"] for item in fasteners}) == 300
        assert diagnostics["statistics"]["meshes"] == 1
        assert diagnostics["statistics"]["tessellationCacheHits"] == 299
        assert diagnostics["statistics"]["edgeCacheHits"] == 299
        assert diagnostics["statistics"]["objectCount"] >= 301
        assert diagnostics["statistics"]["tessellationMs"] >= 0
        assert diagnostics["statistics"]["edgeDiscretizationMs"] >= 0
        provider_durations = diagnostics["statistics"]["providerDurationsMs"]
        assert {"documentHealth", "parameters", "sketches", "assembly", "techdraw"} <= set(
            provider_durations
        )
        assert all(duration >= 0 for duration in provider_durations.values())
        # The headless source visibility is unknown, so retain the source as a
        # possible independent occurrence in addition to the 300 links.
        assert len(scene.graph.nodes_geometry) == 301

        save_document(root / "scaled-links.FCStd", scaled_link_assembly)
        _, scaled_diagnostics, scaled_scene = convert(root, "scaled-links")
        assert scaled_diagnostics["statistics"]["tessellationCacheHits"] == 1
        assert scaled_diagnostics["statistics"]["edgeCacheHits"] == 1
        assert scaled_diagnostics["statistics"]["meshes"] == 2
        assert len(scaled_scene.graph.nodes_geometry) == 4
        assert scaled_scene.bounds.round(6).tolist() == [[0.0, 0.0, 0.0], [0.024, 0.006, 0.008]]

        for model in ("Assembly3", "Assembly4"):
            name = model.casefold()
            save_document(root / f"{name}.FCStd", workbench_marker(model))
            convert(root, name)
            metadata = json.loads((root / f"{name}.part-metadata.json").read_text())
            assert metadata["native"]["assembly_model"] == model.upper()
            requirement = metadata["native"]["workbench_requirements"][0]
            assert requirement["assembly_model"] == model.upper()
            assert requirement["required_modules"]
            assert "missing_modules" in requirement

    print("FreeCAD production fixture suite passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
