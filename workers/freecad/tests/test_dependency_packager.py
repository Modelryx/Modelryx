from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from typing import ClassVar

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "shared"))
from modelryx_freecad_shared import assembly_capability_statuses

MODULE_PATH = Path(__file__).parents[1] / "preprocessor" / "modelryx_freecad_preprocessor.py"
SPEC = importlib.util.spec_from_file_location("modelryx_freecad_preprocessor", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class FakeObject:
    TypeId = "App::Link"
    PropertiesList: ClassVar[list[str]] = ["LinkedObject"]

    def __init__(self, linked=None, name="LinkedObject"):
        self.LinkedObject = linked
        self.Name = name
        self.Label = name

    def getTypeIdOfProperty(self, _name):
        return "App::PropertyXLink"


class FakeDocument:
    def __init__(self, filename: Path, linked=None):
        self.FileName = str(filename)
        self.Objects = [FakeObject(linked)] if linked is not None else []

    def getObject(self, name):
        return next((obj for obj in self.Objects if getattr(obj, "Name", None) == name), None)


class LinkedObject:
    def __init__(self, document):
        self.Document = document


class FakeMatrix:
    def __init__(self, values: tuple[float, ...]):
        for index, value in enumerate(values):
            row, column = divmod(index, 4)
            setattr(self, f"A{row + 1}{column + 1}", value)


class FakePlacement:
    def __init__(self, values: tuple[float, ...]):
        self.values = values

    def toMatrix(self):
        return FakeMatrix(self.values)


def test_packages_only_transitive_dependencies_and_maps_external_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    external = tmp_path / "library"
    project.mkdir()
    external.mkdir()
    (project / "external1").mkdir()
    root = project / "root.FCStd"
    child = project / "parts" / "child.FCStd"
    outside = external / "bolt.FCStd"
    child.parent.mkdir()
    for path in (root, child, outside, project / "unused.FCStd"):
        path.write_bytes(path.name.encode())

    documents = {}
    outside_doc = FakeDocument(outside)
    child_doc = FakeDocument(child, LinkedObject(outside_doc))
    root_doc = FakeDocument(root, LinkedObject(child_doc))
    documents.update(
        {
            path.resolve(): doc
            for path, doc in ((root, root_doc), (child, child_doc), (outside, outside_doc))
        }
    )

    kind, files, missing, references, document_types, assembly_models, issues = module.analyze(
        root, lambda path: documents[path], lambda _doc: None
    )
    report = module.build_package(
        root,
        tmp_path / "out",
        kind,
        files,
        missing,
        references,
        document_types,
        assembly_models,
        issues,
    )

    assert kind == "ASSEMBLY"
    assert missing == []
    assert issues == []
    assert assembly_models[root.resolve()] == "APP_LINK"
    assert {row["logical_path"] for row in report["files"]} == {
        "root.FCStd",
        "parts/child.FCStd",
        "external2/bolt.FCStd",
    }
    assert [
        {
            key: row[key]
            for key in (
                "parent_logical_path",
                "child_logical_path",
                "instance_name",
                "occurrence_id",
                "occurrence_path",
                "occurrence_key",
            )
        }
        for row in report["references"]
    ] == [
        {
            "parent_logical_path": "parts/child.FCStd",
            "child_logical_path": "external2/bolt.FCStd",
            "instance_name": "LinkedObject",
            "occurrence_id": "LinkedObject.LinkedObject",
            "occurrence_path": "parts/child.FCStd::LinkedObject.LinkedObject",
            "occurrence_key": "parts/child.FCStd::LinkedObject.LinkedObject",
        },
        {
            "parent_logical_path": "root.FCStd",
            "child_logical_path": "parts/child.FCStd",
            "instance_name": "LinkedObject",
            "occurrence_id": "LinkedObject.LinkedObject",
            "occurrence_path": "root.FCStd::LinkedObject.LinkedObject",
            "occurrence_key": "root.FCStd::LinkedObject.LinkedObject",
        },
    ]
    assert next(row for row in report["files"] if row["is_root"])["document_type"] == "ASSEMBLY"
    with zipfile.ZipFile(report["package"]) as archive:
        assert "unused.FCStd" not in archive.namelist()
        embedded = json.loads(archive.read("_modelryx/dependency-report.json"))
        assert embedded["root_logical_path"] == "root.FCStd"


def test_reports_missing_nested_reference(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    child = tmp_path / "child.FCStd"
    missing = tmp_path / "missing.FCStd"
    root.write_bytes(b"root")
    child.write_bytes(b"child")
    missing_doc = FakeDocument(missing)
    child_doc = FakeDocument(child, LinkedObject(missing_doc))
    root_doc = FakeDocument(root, LinkedObject(child_doc))
    documents = {root.resolve(): root_doc, child.resolve(): child_doc}

    _kind, files, failures, references, _document_types, _models, issues = module.analyze(
        root, lambda path: documents[path], lambda _doc: None
    )

    assert set(files) == {root.resolve(), child.resolve()}
    assert [reference[:4] for reference in references] == [
        (
            root.resolve(),
            child.resolve(),
            "LinkedObject",
            "LinkedObject.LinkedObject",
        )
    ]
    assert failures[0]["reference"] == str(missing.resolve())
    assert failures[0]["referenced_by"] == str(child.resolve())
    assert "Place or relink" in failures[0]["suggestion"]
    assert failures[0]["assembly_model"] == "APP_LINK"
    assert failures[0]["object"] == "LinkedObject"
    assert issues == []


def test_recovers_unresolved_xlink_from_fcstd_document_xml(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    document_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document><Object name="Bracket001"><Properties>
<Property name="LinkedObject" type="App::PropertyXLink">
<XLink file="parts/missing.FCStd" name="Body"/>
</Property></Properties></Object></Document>"""
    with zipfile.ZipFile(root, "w") as archive:
        archive.writestr("Document.xml", document_xml)

    references = module.serialized_references(root)

    assert references == [
        module.Reference(
            (tmp_path / "parts" / "missing.FCStd").resolve(),
            "parts/missing.FCStd",
            root.resolve(),
            "Bracket001",
            "LinkedObject",
            "App::PropertyXLink",
            "Bracket001.LinkedObject",
        )
    ]


def test_extracts_fcstd_document_version_provenance(tmp_path: Path) -> None:
    source = tmp_path / "versioned.FCStd"
    document_xml = (
        '<Document SchemaVersion="4" ProgramVersion="1.1.0 (Git)" '
        'FileVersion="1" StringHasher="1"/>'
    )
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Document.xml", document_xml)

    assert module.fcstd_document_provenance(source) == {
        "format": "FCStd",
        "file_format_version": "1",
        "document_schema_version": "4",
        "authoring_freecad_version": "1.1.0 (Git)",
        "version_attributes": {
            "SchemaVersion": "4",
            "ProgramVersion": "1.1.0 (Git)",
            "FileVersion": "1",
        },
    }


def test_missing_fcstd_version_provenance_is_unknown_not_guessed(tmp_path: Path) -> None:
    source = tmp_path / "legacy.FCStd"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Document.xml", '<Document StringHasher="1"/>')

    assert module.fcstd_document_provenance(source) == {
        "format": "FCStd",
        "file_format_version": None,
        "document_schema_version": None,
        "authoring_freecad_version": None,
        "version_attributes": {},
    }


def test_extracts_fcstd_document_version_provenance(tmp_path: Path) -> None:
    source = tmp_path / "versioned.FCStd"
    document_xml = (
        '<Document SchemaVersion="4" ProgramVersion="1.1.0 (Git)" '
        'FileVersion="1" StringHasher="1"/>'
    )
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Document.xml", document_xml)

    assert module.fcstd_document_provenance(source) == {
        "format": "FCStd",
        "file_format_version": "1",
        "document_schema_version": "4",
        "authoring_freecad_version": "1.1.0 (Git)",
        "version_attributes": {
            "SchemaVersion": "4",
            "ProgramVersion": "1.1.0 (Git)",
            "FileVersion": "1",
        },
    }


def test_missing_fcstd_version_provenance_is_unknown_not_guessed(tmp_path: Path) -> None:
    source = tmp_path / "legacy.FCStd"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Document.xml", '<Document StringHasher="1"/>')

    assert module.fcstd_document_provenance(source) == {
        "format": "FCStd",
        "file_format_version": None,
        "document_schema_version": None,
        "authoring_freecad_version": None,
        "version_attributes": {},
    }


def test_rewrites_serialized_xlinks_to_packaged_relative_paths(tmp_path: Path) -> None:
    source_root = tmp_path / "source" / "root.FCStd"
    source_child = tmp_path / "library" / "child.FCStd"
    packaged_root = tmp_path / "package" / "root.FCStd"
    packaged_child = tmp_path / "package" / "external1" / "child.FCStd"
    source_root.parent.mkdir()
    source_child.parent.mkdir()
    packaged_root.parent.mkdir()
    packaged_child.parent.mkdir()
    document_xml = (
        '<?xml version="1.0"?><Document><Object name="Child"><Properties>'
        '<Property name="LinkedObject" type="App::PropertyXLink">'
        f'<XLink file="{source_child}" name="Body"/>'
        "</Property></Properties></Object></Document>"
    )
    with zipfile.ZipFile(packaged_root, "w") as archive:
        archive.writestr("Document.xml", document_xml)
        archive.writestr("keep.bin", b"preserved")

    module._rewrite_serialized_xlinks(
        source_root.resolve(),
        packaged_root.resolve(),
        {
            source_root.resolve(): packaged_root.resolve(),
            source_child.resolve(): packaged_child.resolve(),
        },
    )

    with zipfile.ZipFile(packaged_root) as archive:
        rewritten = archive.read("Document.xml").decode()
        assert 'file="external1/child.FCStd"' in rewritten
        assert archive.read("keep.bin") == b"preserved"


def test_preserves_repeated_link_occurrences(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    child = tmp_path / "child.FCStd"
    root.write_bytes(b"root")
    child.write_bytes(b"child")
    child_doc = FakeDocument(child)
    root_doc = FakeDocument(root)
    root_doc.Objects = [
        FakeObject(LinkedObject(child_doc), "Bracket001"),
        FakeObject(LinkedObject(child_doc), "Bracket002"),
    ]
    documents = {root.resolve(): root_doc, child.resolve(): child_doc}

    _kind, _files, _missing, references, _document_types, _models, _issues = module.analyze(
        root, lambda path: documents[path], lambda _doc: None
    )

    assert [reference[:4] for reference in references] == [
        (root.resolve(), child.resolve(), "Bracket001", "Bracket001.LinkedObject"),
        (root.resolve(), child.resolve(), "Bracket002", "Bracket002.LinkedObject"),
    ]


def test_occurrence_identity_does_not_collapse_duplicate_labels(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    child = tmp_path / "child.FCStd"
    root.write_bytes(b"root")
    child.write_bytes(b"child")
    child_doc = FakeDocument(child)
    first = FakeObject(LinkedObject(child_doc), "Bracket001")
    second = FakeObject(LinkedObject(child_doc), "Bracket002")
    first.Label = second.Label = "Bracket"
    root_doc = FakeDocument(root)
    root_doc.Objects = [first, second]
    documents = {root.resolve(): root_doc, child.resolve(): child_doc}

    result = module.analyze(root, lambda path: documents[path], lambda _doc: None)
    report = module.build_package(
        root,
        tmp_path / "out",
        result[0],
        result[1],
        result[2],
        result[3],
        result[4],
        result[5],
        result[6],
    )

    assert [row["instance_name"] for row in report["references"]] == ["Bracket", "Bracket"]
    assert {row["occurrence_id"] for row in report["references"]} == {
        "Bracket001.LinkedObject",
        "Bracket002.LinkedObject",
    }
    assert len({row["occurrence_key"] for row in report["references"]}) == 2
    assert all(row["transform"] == list(module.IDENTITY_TRANSFORM) for row in report["references"])
    assert all(row["is_visible"] is True for row in report["references"])
    assert all(row["is_suppressed"] is False for row in report["references"])


def test_preserves_occurrence_placement_visibility_and_suppression(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    child = tmp_path / "child.FCStd"
    root.write_bytes(b"root")
    child.write_bytes(b"child")
    child_doc = FakeDocument(child)
    link = FakeObject(LinkedObject(child_doc), "HiddenBracket")
    transform = tuple(float(value) for value in range(1, 17))
    link.Placement = FakePlacement(transform)
    link.ViewObject = type("ViewObject", (), {"Visibility": False})()
    link.Suppressed = True
    root_doc = FakeDocument(root)
    root_doc.Objects = [link]
    documents = {root.resolve(): root_doc, child.resolve(): child_doc}

    result = module.analyze(root, lambda path: documents[path], lambda _doc: None)
    report = module.build_package(
        root,
        tmp_path / "out",
        result[0],
        result[1],
        result[2],
        result[3],
        result[4],
        result[5],
        result[6],
    )

    reference = report["references"][0]
    assert reference["transform"] == list(transform)
    assert reference["is_visible"] is False
    assert reference["is_suppressed"] is True


def test_collapsed_link_array_expands_to_independent_occurrences(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    child = tmp_path / "child.FCStd"
    root.write_bytes(b"root")
    child.write_bytes(b"child")
    child_doc = FakeDocument(child)
    link = FakeObject(LinkedObject(child_doc), "Fasteners")
    link.ElementCount = 3
    link.ElementList = []
    link.PlacementList = [
        FakePlacement(tuple(float(value + offset) for value in range(16)))
        for offset in (10, 20, 30)
    ]
    link.VisibilityList = [True, False, True]
    root_doc = FakeDocument(root)
    root_doc.Objects = [link]

    references = module.document_references(root_doc)

    assert [reference.occurrence_id for reference in references] == [
        "Fasteners_i0.LinkedObject",
        "Fasteners_i1.LinkedObject",
        "Fasteners_i2.LinkedObject",
    ]
    assert [reference.occurrence_name for reference in references] == [
        "Fasteners [1]",
        "Fasteners [2]",
        "Fasteners [3]",
    ]
    assert [reference.visible for reference in references] == [True, False, True]
    assert [reference.transform[0] for reference in references] == [10.0, 20.0, 30.0]


def test_expanded_link_array_does_not_emit_container_occurrence(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    child = tmp_path / "child.FCStd"
    root.write_bytes(b"root")
    child.write_bytes(b"child")
    child_doc = FakeDocument(child)
    container = FakeObject(LinkedObject(child_doc), "Fasteners")
    container.ElementCount = 2
    first = FakeObject(LinkedObject(child_doc), "Fasteners_i0")
    first.TypeId = "App::LinkElement"
    second = FakeObject(LinkedObject(child_doc), "Fasteners_i1")
    second.TypeId = "App::LinkElement"
    container.ElementList = [first, second]
    root_doc = FakeDocument(root)
    root_doc.Objects = [container, first, second]

    references = module.document_references(root_doc)

    assert [reference.occurrence_id for reference in references] == [
        "Fasteners_i0.LinkedObject",
        "Fasteners_i1.LinkedObject",
    ]


def test_occurrence_key_is_stable_and_bounded() -> None:
    parent = f"assemblies/{'nested/' * 40}root.FCStd"
    occurrence_id = f"Component.{'LinkedObject' * 20}"

    first = module.occurrence_key(parent, occurrence_id)
    second = module.occurrence_key(parent, occurrence_id)

    assert first == second
    assert len(first) <= 255
    assert first.endswith(
        module.hashlib.sha256(f"{parent}::{occurrence_id}".encode()).hexdigest()[:16]
    )


def test_packages_shared_dependency_once_and_retains_each_edge(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    left = tmp_path / "left.FCStd"
    right = tmp_path / "right.FCStd"
    shared = tmp_path / "shared.FCStd"
    for path in (root, left, right, shared):
        path.write_bytes(path.name.encode())
    shared_doc = FakeDocument(shared)
    left_doc = FakeDocument(left, LinkedObject(shared_doc))
    right_doc = FakeDocument(right, LinkedObject(shared_doc))
    root_doc = FakeDocument(root)
    root_doc.Objects = [
        FakeObject(LinkedObject(left_doc), "Left"),
        FakeObject(LinkedObject(right_doc), "Right"),
    ]
    documents = {
        root.resolve(): root_doc,
        left.resolve(): left_doc,
        right.resolve(): right_doc,
        shared.resolve(): shared_doc,
    }

    kind, files, missing, references, document_types, models, issues = module.analyze(
        root, lambda path: documents[path], lambda _doc: None
    )
    report = module.build_package(
        root,
        tmp_path / "out",
        kind,
        files,
        missing,
        references,
        document_types,
        models,
        issues,
    )

    assert not missing
    assert not issues
    assert len(files) == 4
    assert [row["logical_path"] for row in report["files"]].count("shared.FCStd") == 1
    assert {
        (row["parent_logical_path"], row["child_logical_path"]) for row in report["references"]
    } == {
        ("root.FCStd", "left.FCStd"),
        ("root.FCStd", "right.FCStd"),
        ("left.FCStd", "shared.FCStd"),
        ("right.FCStd", "shared.FCStd"),
    }


def test_reports_cyclic_document_references_with_fix_suggestion(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    child = tmp_path / "child.FCStd"
    root.write_bytes(b"root")
    child.write_bytes(b"child")
    root_doc = FakeDocument(root)
    child_doc = FakeDocument(child)
    root_doc.Objects = [FakeObject(LinkedObject(child_doc), "Child")]
    child_doc.Objects = [FakeObject(LinkedObject(root_doc), "Root")]
    documents = {root.resolve(): root_doc, child.resolve(): child_doc}

    _kind, files, missing, references, _document_types, _models, issues = module.analyze(
        root, lambda path: documents[path], lambda _doc: None
    )

    assert set(files) == {root.resolve(), child.resolve()}
    assert not missing
    assert len(references) == 2
    assert issues == [
        {
            "document": str(child.resolve()),
            "code": "CYCLIC_FREECAD_REFERENCE",
            "severity": "ERROR",
            "message": "FreeCAD document references form a cycle: child.FCStd -> root.FCStd -> child.FCStd.",
            "cycle": [str(child.resolve()), str(root.resolve()), str(child.resolve())],
            "suggestion": (
                "Remove or replace one external link in the reported cycle, then analyze the root "
                "document again. Modelryx cannot create an isolated package from cyclic document "
                "references."
            ),
        }
    ]
    report = module.build_package(
        root,
        tmp_path / "out",
        "ASSEMBLY",
        files,
        missing,
        references,
        {root.resolve(): "ASSEMBLY", child.resolve(): "ASSEMBLY"},
        {root.resolve(): "APP_LINK", child.resolve(): "APP_LINK"},
        issues,
    )
    assert report["validation_issues"][0]["cycle"] == [
        "child.FCStd",
        "root.FCStd",
        "child.FCStd",
    ]
    assert str(tmp_path) not in json.dumps(report["validation_issues"])


def test_reports_dependency_count_depth_and_size_limits(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    child = tmp_path / "child.FCStd"
    grandchild = tmp_path / "grandchild.FCStd"
    for path in (root, child, grandchild):
        path.write_bytes(b"1234")
    grandchild_doc = FakeDocument(grandchild)
    child_doc = FakeDocument(child, LinkedObject(grandchild_doc))
    root_doc = FakeDocument(root, LinkedObject(child_doc))
    documents = {
        root.resolve(): root_doc,
        child.resolve(): child_doc,
        grandchild.resolve(): grandchild_doc,
    }

    count_result = module.analyze(
        root, lambda path: documents[path], lambda _doc: None, max_documents=2
    )
    depth_result = module.analyze(
        root, lambda path: documents[path], lambda _doc: None, max_depth=1
    )
    size_result = module.analyze(
        root, lambda path: documents[path], lambda _doc: None, max_total_bytes=7
    )

    assert count_result[6][0]["code"] == "FREECAD_DEPENDENCY_COUNT_LIMIT"
    assert "Split the assembly" in count_result[6][0]["suggestion"]
    assert depth_result[6][0]["code"] == "FREECAD_DEPENDENCY_DEPTH_LIMIT"
    assert "subassemblies" in depth_result[6][0]["suggestion"]
    assert size_result[6][0]["code"] == "FREECAD_DEPENDENCY_SIZE_LIMIT"
    assert "administrator" in size_result[6][0]["suggestion"]


def test_reports_analysis_timeout_without_opening_next_document(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    child = tmp_path / "child.FCStd"
    root.write_bytes(b"root")
    child.write_bytes(b"child")
    child_doc = FakeDocument(child)
    root_doc = FakeDocument(root, LinkedObject(child_doc))
    documents = {root.resolve(): root_doc, child.resolve(): child_doc}
    opened: list[Path] = []
    times = iter((0.0, 0.0, 2.0))

    result = module.analyze(
        root,
        lambda path: opened.append(path) or documents[path],
        lambda _doc: None,
        max_seconds=1,
        clock=lambda: next(times),
    )

    assert opened == [root.resolve()]
    assert result[6][0]["code"] == "FREECAD_ANALYSIS_TIME_LIMIT"
    assert "opens promptly" in result[6][0]["suggestion"]


def test_reports_unreadable_dependency_without_raw_native_error(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    child = tmp_path / "corrupt.FCStd"
    root.write_bytes(b"root")
    child.write_bytes(b"not-an-fcstd")
    child_doc = FakeDocument(child)
    root_doc = FakeDocument(root, LinkedObject(child_doc))

    def open_document(path: Path):
        if path == child.resolve():
            raise RuntimeError("sensitive native error and workstation path")
        return root_doc

    result = module.analyze(root, open_document, lambda _doc: None)

    issue = result[6][0]
    assert issue["code"] == "UNREADABLE_FREECAD_DOCUMENT"
    assert issue["message"] == "FreeCAD could not open dependency document corrupt.FCStd."
    assert "sensitive native error" not in json.dumps(issue)


class Assembly4Proxy:
    pass


class A2plusProxy:
    pass


def test_detects_supported_assembly_models(tmp_path: Path) -> None:
    document = FakeDocument(tmp_path / "assembly.FCStd")
    assembly = FakeObject(name="Model")
    assembly.TypeId = "App::Part"
    assembly.Proxy = Assembly4Proxy()
    document.Objects = [assembly]

    assert module.assembly_model(document) == "ASSEMBLY4"

    assembly.TypeId = "Assembly::AssemblyObject"
    assert module.assembly_model(document) == "BUILTIN_ASSEMBLY"

    assembly.TypeId = "App::FeaturePython"
    assembly.Proxy = A2plusProxy()
    assert module.assembly_model(document) == "A2PLUS"


def test_distinguishes_assembly_semantic_and_solver_capabilities() -> None:
    missing_module = {"code": "MISSING_FREECAD_WORKBENCH_MODULE"}
    assert assembly_capability_statuses(
        "ASSEMBLY4",
        is_assembly=True,
        module_issue=missing_module,
        joint_count=2,
        skipped_joint_count=0,
    ) == {
        "assembly_semantics": "partial",
        "assembly_constraints": "partial",
        "assembly_solver": "unavailable",
    }
    assert (
        assembly_capability_statuses(
            "APP_LINK",
            is_assembly=True,
            module_issue=None,
            joint_count=0,
            skipped_joint_count=0,
        )["assembly_solver"]
        == "not_applicable"
    )
    built_in = assembly_capability_statuses(
        "BUILTIN_ASSEMBLY", is_assembly=True, module_issue=None,
        joint_count=2, skipped_joint_count=0,
    )
    assert built_in["assembly_solver"] == "unavailable"
    assert built_in["assembly_constraints"] == "complete"


@pytest.mark.parametrize(
    "evidence",
    [
        {"unverified_joint_suppression": 1},
        {"incomplete_joint_references": 1},
        {"unresolved_joint_occurrences": 1},
    ],
)
def test_incomplete_joint_evidence_marks_constraints_partial(evidence) -> None:
    capabilities = assembly_capability_statuses(
        "BUILTIN_ASSEMBLY", is_assembly=True, module_issue=None,
        joint_count=2, skipped_joint_count=0, **evidence,
    )
    assert capabilities["assembly_constraints"] == "partial"
    assert capabilities["assembly_semantics"] == "partial"
    assert capabilities["assembly_solver"] == "unavailable"

    not_assembly = assembly_capability_statuses(
        "APP_LINK", is_assembly=False, module_issue=None,
        joint_count=0, skipped_joint_count=0, **evidence,
    )
    assert not_assembly["assembly_constraints"] == "not_applicable"


def test_classifies_linked_techdraw_document_as_drawing(tmp_path: Path) -> None:
    root = tmp_path / "drawing.FCStd"
    model = tmp_path / "model.FCStd"
    root.write_bytes(b"drawing")
    model.write_bytes(b"model")
    drawing = FakeDocument(root, LinkedObject(FakeDocument(model)))
    page = FakeObject(name="Page")
    page.TypeId = "TechDraw::DrawPage"
    page.PropertiesList = []
    drawing.Objects.append(page)

    assert module.classify_document(drawing) == "DRAWING"


def test_classifies_native_mesh_document_as_part(tmp_path: Path) -> None:
    source = tmp_path / "scan.FCStd"
    source.write_bytes(b"mesh")
    document = FakeDocument(source)
    mesh = FakeObject(name="Scan")
    mesh.TypeId = "Mesh::Feature"
    mesh.PropertiesList = []
    mesh.Mesh = object()
    document.Objects = [mesh]

    assert module.classify_document(document) == "PART"


def test_reports_missing_assembly4_module_with_actionable_details() -> None:
    issue = module.workbench_module_issue("ASSEMBLY4", lambda _name: False)

    assert issue == {
        "code": "MISSING_FREECAD_WORKBENCH_MODULE",
        "severity": "ERROR",
        "assembly_model": "ASSEMBLY4",
        "required_modules": ["freecad.asm4", "Asm4", "Asm4_libs"],
        "missing_modules": ["freecad.asm4", "Asm4", "Asm4_libs"],
        "message": "Assembly4 semantics were detected but its Python module is unavailable.",
        "suggestion": (
            "Install and enable Assembly4 in the FreeCAD worker image, then analyze the document "
            "again. Do not commit an assembly analyzed without its defining workbench."
        ),
    }
    assert module.workbench_module_issue("ASSEMBLY4", lambda name: name == "Asm4") is None
    assert module.workbench_module_issue("BUILTIN_ASSEMBLY", lambda _name: False) is None


def test_package_reports_workbench_requirements_and_missing_modules(tmp_path: Path) -> None:
    root = tmp_path / "assembly.FCStd"
    root.write_bytes(b"assembly")
    issue = {
        "document": str(root.resolve()),
        "code": "MISSING_FREECAD_WORKBENCH_MODULE",
        "severity": "ERROR",
        "assembly_model": "ASSEMBLY3",
        "missing_modules": ["freecad.asm3", "Asm3"],
    }

    report = module.build_package(
        root,
        tmp_path / "out",
        "ASSEMBLY",
        [root.resolve()],
        [],
        document_types={root.resolve(): "ASSEMBLY"},
        assembly_models={root.resolve(): "ASSEMBLY3"},
        validation_issues=[issue],
    )

    assert report["workbench_requirements"] == [
        {
            "assembly_model": "ASSEMBLY3",
            "required_modules": ["freecad.asm3", "Asm3"],
            "missing_modules": ["Asm3", "freecad.asm3"],
        }
    ]


def test_reports_unsupported_populated_workbench_link_property(tmp_path: Path) -> None:
    document = FakeDocument(tmp_path / "assembly.FCStd")
    obj = FakeObject(object(), name="FastenerArray")
    obj.TypeId = "App::FeaturePython"
    obj.getTypeIdOfProperty = lambda _name: "Assembly3::PropertyLinkArray"
    document.Objects = [obj]

    issues = module.validate_reference_properties(document, "ASSEMBLY3")

    assert issues == [
        {
            "code": "UNSUPPORTED_FREECAD_LINK_PROPERTY",
            "severity": "ERROR",
            "assembly_model": "ASSEMBLY3",
            "object": "FastenerArray",
            "property": "LinkedObject",
            "property_type": "Assembly3::PropertyLinkArray",
            "message": (
                "FastenerArray.LinkedObject uses unsupported FreeCAD link type "
                "Assembly3::PropertyLinkArray."
            ),
            "suggestion": (
                "Replace this reference with a standard App::Link/XLink, or install a "
                "Modelryx worker version that supports this assembly workbench before committing."
            ),
        }
    ]


def test_accepts_standard_freecad_link_property_variant(tmp_path: Path) -> None:
    document = FakeDocument(tmp_path / "assembly.FCStd")
    obj = FakeObject(object(), name="Bracket")
    obj.getTypeIdOfProperty = lambda _name: "App::PropertyLinkSubHidden"
    document.Objects = [obj]

    assert module.validate_reference_properties(document, "APP_LINK") == []


def test_package_reports_logical_document_path_for_validation_issue(tmp_path: Path) -> None:
    root = tmp_path / "root.FCStd"
    root.write_bytes(b"root")
    report = module.build_package(
        root,
        tmp_path / "out",
        "ASSEMBLY",
        [root],
        [],
        document_types={root.resolve(): "ASSEMBLY"},
        assembly_models={root.resolve(): "ASSEMBLY3"},
        validation_issues=[
            {
                "document": str(root.resolve()),
                "code": "UNSUPPORTED_FREECAD_LINK_PROPERTY",
                "severity": "ERROR",
            }
        ],
    )

    assert report["files"][0]["assembly_model"] == "ASSEMBLY3"
    assert report["validation_issues"][0]["document"] == "root.FCStd"


def test_replaces_link_targets_with_packaged_document_objects(tmp_path: Path) -> None:
    source = (tmp_path / "source.FCStd").resolve()
    packaged = (tmp_path / "package" / "source.FCStd").resolve()
    source_document = FakeDocument(source)
    source_object = FakeObject(name="Bracket")
    source_object.Document = source_document
    packaged_document = FakeDocument(packaged)
    packaged_object = FakeObject(name="Bracket")
    packaged_object.Document = packaged_document
    packaged_document.Objects = [packaged_object]

    replacement, changed = module._replace_link_targets(
        (source_object, [source_object]),
        {source: packaged},
        {},
        lambda path: packaged_document if path == packaged else None,
    )

    assert changed is True
    assert replacement == (packaged_object, [packaged_object])
