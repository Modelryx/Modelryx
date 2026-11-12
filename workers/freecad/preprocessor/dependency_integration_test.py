"""Exercise nested external FreeCAD packaging and conversion in the native runtime."""

# FreeCAD rewrites module globals while initializing, so it must be imported first.
# ruff: noqa: I001

from __future__ import annotations

import FreeCAD  # type: ignore
import Part  # type: ignore

import json
import sys
import tempfile
import zipfile
from pathlib import Path

import trimesh

worker_root = Path(__file__).parents[1]
sys.path.insert(0, str(worker_root / "shared"))
converter_root = worker_root / "converter"
if not converter_root.is_dir():
    converter_root = worker_root / "modelryx-converter"
sys.path.insert(0, str(converter_root))

from modelryx_freecad_preprocessor import analyze, build_package, logical_paths, rebase_package_copies
from fcstd_to_modelryx_glb import convert_fcstd_to_modelryx_package


def close_all_documents() -> None:
    for name in list(FreeCAD.listDocuments()):
        FreeCAD.closeDocument(name)


def inject_shape_appearances(path: Path, colors: dict[str, tuple[int, int, int]]) -> None:
    """Add the saved GUI appearance records consumed by the headless converter."""
    providers = []
    with zipfile.ZipFile(path, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        for object_name, (red, green, blue) in colors.items():
            material_file = f"{object_name}.ShapeAppearance.bin"
            providers.append(
                f'<ViewProvider name="{object_name}"><Properties>'
                '<Property name="ShapeAppearance" type="App::PropertyMaterialList">'
                f'<MaterialList file="{material_file}"/>'
                "</Property></Properties></ViewProvider>"
            )
            archive.writestr(
                material_file,
                (1).to_bytes(4, "little")
                # Modern FreeCAD writes zero ambient alpha even for opaque faces.
                + bytes((0, 0, 0, 0))
                + bytes((255, blue, green, red)),
            )
        archive.writestr(
            "GuiDocument.xml",
            '<?xml version="1.0" encoding="UTF-8"?><Document>'
            + "".join(providers)
            + "</Document>",
        )


def save_component(path: Path) -> None:
    document = FreeCAD.newDocument("ReleaseGateComponent")
    try:
        body = document.addObject("PartDesign::Feature", "Pin")
        body.Label = "Red pin"
        body.Shape = Part.makeCylinder(5, 12)
        document.recompute()
        document.saveAs(str(path))
    finally:
        close_all_documents()


def save_subassembly(path: Path, component_path: Path) -> None:
    component = FreeCAD.open(str(component_path))
    document = FreeCAD.newDocument("ReleaseGateSubassembly")
    try:
        document.saveAs(str(path))
        assembly = document.addObject("App::Part", "PinSet")
        assembly.Label = "Pin set"
        target = component.getObject("Pin")
        link = document.addObject("App::Link", "Pins")
        link.Label = "Pins"
        link.LinkedObject = target
        link.ElementCount = 2
        document.recompute()
        for element, x_position in zip(link.ElementList, (0, 20), strict=True):
            element.Placement.Base = FreeCAD.Vector(x_position, 0, 0)
        link.ShowElement = False
        assembly.addObject(link)
        document.recompute()
        document.saveAs(str(path))
    finally:
        close_all_documents()


def save_root(path: Path, subassembly_path: Path) -> None:
    subassembly = FreeCAD.open(str(subassembly_path))
    document = FreeCAD.newDocument("ReleaseGateRoot")
    try:
        document.saveAs(str(path))
        assembly = document.addObject("App::Part", "RootAssembly")
        assembly.Label = "Release gate assembly"
        target = subassembly.getObject("PinSet")
        for name, label, y_position in (
            ("ModuleA", "Module A", 0),
            ("ModuleB", "Module B", 40),
        ):
            link = document.addObject("App::Link", name)
            link.Label = label
            link.LinkedObject = target
            link.Placement.Base = FreeCAD.Vector(0, y_position, 0)
            assembly.addObject(link)
        document.recompute()
        document.saveAs(str(path))
    finally:
        close_all_documents()
    inject_shape_appearances(path, {"ModuleA": (204, 26, 26), "ModuleB": (204, 26, 26)})


def open_document(path: Path):
    document = FreeCAD.open(str(path))
    document.recompute()
    return document


def close_document(document) -> None:
    if document.Name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(document.Name)


def analyze_graph(root: Path):
    try:
        return analyze(root, open_document, close_document)
    finally:
        close_all_documents()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="modelryx-freecad-release-gate-") as directory:
        workspace = Path(directory)
        project = workspace / "project"
        external = workspace / "component-library"
        (project / "assemblies").mkdir(parents=True)
        external.mkdir()
        root = project / "root.FCStd"
        subassembly = project / "assemblies" / "pin-set.FCStd"
        component = external / "red-pin.FCStd"
        unused = project / "unused.FCStd"

        save_component(component)
        save_subassembly(subassembly, component)
        save_root(root, subassembly)
        save_component(unused)

        kind, files, missing, references, document_types, models, issues = analyze_graph(root)
        assert kind == "ASSEMBLY"
        assert not missing
        assert not issues
        assert set(files) == {root.resolve(), subassembly.resolve(), component.resolve()}
        assert document_types[root.resolve()] == "ASSEMBLY"
        assert document_types[subassembly.resolve()] == "ASSEMBLY"
        assert models[root.resolve()] == "APP_LINK"
        assert models[subassembly.resolve()] == "APP_LINK"
        assert {
            (parent.name, child.name, occurrence)
            for parent, child, occurrence, *_occurrence_data in references
        } == {
            ("root.FCStd", "pin-set.FCStd", "Module A"),
            ("root.FCStd", "pin-set.FCStd", "Module B"),
            ("pin-set.FCStd", "red-pin.FCStd", "Pins [1]"),
            ("pin-set.FCStd", "red-pin.FCStd", "Pins [2]"),
        }

        paths = logical_paths(root.resolve(), files)
        assert paths[subassembly.resolve()] == "assemblies/pin-set.FCStd"
        assert paths[component.resolve()] == "external1/red-pin.FCStd"
        package_directory = workspace / "package-output"
        with tempfile.TemporaryDirectory(prefix="modelryx-freecad-rebased-") as rebased_directory:
            packaged_sources = rebase_package_copies(
                root.resolve(),
                paths,
                Path(rebased_directory),
                open_document,
                close_document,
            )
            report = build_package(
                root.resolve(),
                package_directory,
                kind,
                files,
                missing,
                references,
                document_types,
                models,
                issues,
                packaged_sources,
                isolated_reopen_validated=True,
            )
        close_all_documents()

        isolated = workspace / "isolated"
        with zipfile.ZipFile(report["package"]) as archive:
            archive.extractall(isolated)
            names = set(archive.namelist())
        assert "unused.FCStd" not in names
        assert report["isolated_reopen_validated"] is True
        assert {row["logical_path"] for row in report["files"]} == {
            "root.FCStd",
            "assemblies/pin-set.FCStd",
            "external1/red-pin.FCStd",
        }

        isolated_result = analyze_graph(isolated / "root.FCStd")
        assert not isolated_result[2], isolated_result[2]
        isolated_root = isolated / "root.FCStd"
        output = workspace / "nested-external.pfg"
        convert_fcstd_to_modelryx_package(str(isolated_root), str(output))
        close_all_documents()
        metadata = json.loads(output.with_suffix(".part-metadata.json").read_text(encoding="utf-8"))
        evidence = metadata["native"]["dependency_evidence"]
        assert evidence["available"] is True
        assert evidence["status"] == "complete"
        assert evidence["isolated_reopen_validated"] is True
        assert {record["document"] for record in evidence["records"]} == {
            "assemblies/pin-set.FCStd", "external1/red-pin.FCStd"
        }
        assert metadata["extraction"]["sections"]["dependencies"]["status"] == "complete"
        with zipfile.ZipFile(output) as archive:
            preview = json.loads(archive.read("model-preview.json"))
            diagnostics = json.loads(archive.read("diagnostics.json"))
            glb = workspace / "nested-external.glb"
            glb.write_bytes(archive.read("model.glb"))
        modules = [item for item in preview["items"] if item["name"] in {"Module A", "Module B"}]
        assert len(modules) == 2
        assert {item["display"]["color"] for item in modules} == {"#CC1A1A"}
        assert {item["display"]["opacity"] for item in modules} == {1.0}
        assert sorted(round(item["placement"]["position"][1], 3) for item in modules) == [0.0, 0.04]
        scene = trimesh.load_scene(glb)
        assert diagnostics["statistics"]["meshes"] == 1, diagnostics["statistics"]
        assert diagnostics["statistics"]["tessellationCacheHits"] == 1
        assert len(scene.graph.nodes_geometry) == 2, list(scene.graph.nodes_geometry)
        assert scene.bounds.round(6).tolist() == [
            [-0.005, -0.005, 0.0],
            [0.025, 0.045, 0.012],
        ], scene.bounds.round(6).tolist()

        component.unlink()
        broken = analyze_graph(root)
        assert broken[2]
        assert any(
            Path(row["referenced_by"]).name == "pin-set.FCStd"
            and "red-pin.FCStd" in row["reference"]
            and "Place or relink" in row["suggestion"]
            for row in broken[2]
        )

    print("FreeCAD nested external dependency release gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
