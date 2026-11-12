from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from parameter_graph import (
    BoundedParameterLoader,
    ParameterExtractor,
    add_external_graph_edges,
    cross_document_cycles,
    external_parameter_evidence,
    external_upstream_graph,
    native_parameters,
    parameter_dependency_graph,
)


def test_external_loader_caches_documents_and_enforces_shared_limit() -> None:
    first, broken, overflow = object(), object(), object()
    calls = []

    def extract(doc):
        calls.append(doc)
        if doc is broken:
            raise RuntimeError("bad native object")
        return ([{"name": "value"}], [], [])

    loader = BoundedParameterLoader(extract, max_documents=2)
    assert loader(first) == loader(first) == ([{"name": "value"}], [], [])
    assert loader(broken)[2] == [{"property": "document_extraction_failed"}]
    assert loader(broken)[2] == [{"property": "document_extraction_failed"}]
    assert loader(overflow)[2] == [{"property": "document_limit"}]
    assert loader.limit_reached is True
    assert calls == [first, broken]


def test_document_limit_preserves_verified_reference_and_marks_other_unresolved() -> None:
    first = SimpleNamespace(Name="First", Label="First", Objects=[])
    second = SimpleNamespace(Name="Second", Label="Second", Objects=[])
    for doc in (first, second):
        doc.Objects.append(SimpleNamespace(Name="Sheet", Label="Sheet", Document=doc))
    root = SimpleNamespace(Objects=[SimpleNamespace(Name="Box", OutList=[first.Objects[0], second.Objects[0]])])
    graph = {"nodes": [{"id": "Box.Length", "feature_id": "Box",
                        "expression": "First#Sheet.Width + Second#Sheet.Width"}]}
    loader = BoundedParameterLoader(
        lambda _: ([parameter("Sheet", "Sheet.A1", "Width")], [], []), max_documents=1
    )
    evidence = external_parameter_evidence(root, graph, loader, lambda doc: doc.Name)
    assert [item["status"] for item in evidence] == ["resolved", "unresolved"]
    upstream = external_upstream_graph(root, evidence, loader, lambda doc: doc.Name)
    assert upstream["status"] == "partial"
    assert loader.limit_reached is True


def test_observed_cross_document_cycles_are_scoped_and_deterministic() -> None:
    graph = {"edges": [], "external_edges": [{
        "source": {"document": "other.FCStd", "object": "Sheet", "parameter": "Sheet.A1"},
        "target": {"document": "root.FCStd", "node_id": "Box.Length"}, "reference": "Other#Sheet.X"}],
        "external_upstream": {"edges": [], "cross_document_edges": [{
            "source": {"document": "root.FCStd", "node_id": "Box.Length"},
            "target": {"document": "other.FCStd", "node_id": "Sheet.A1"}, "reference": "Root#Box.Length"}]}}
    assert cross_document_cycles(graph, "root.FCStd") == [[
        {"document": "other.FCStd", "node_id": "Sheet.A1"},
        {"document": "root.FCStd", "node_id": "Box.Length"}]]
    graph["external_upstream"]["cross_document_edges"] = []
    assert cross_document_cycles(graph, "root.FCStd") == []


def test_cross_document_cycle_is_found_inside_overlapping_local_cycle() -> None:
    graph = {
        "edges": [{"source": "A", "target": "B"}, {"source": "B", "target": "A"}],
        "external_edges": [{"source": {"document": "z.FCStd", "object": "P", "parameter": "P.X"},
                            "target": {"document": "root.FCStd", "node_id": "B"}}],
        "external_upstream": {"cross_document_edges": [{
            "source": {"document": "root.FCStd", "node_id": "A"},
            "target": {"document": "z.FCStd", "node_id": "P.X"}}]},
    }
    assert cross_document_cycles(graph, "root.FCStd") == [[
        {"document": "root.FCStd", "node_id": "A"},
        {"document": "z.FCStd", "node_id": "P.X"},
        {"document": "root.FCStd", "node_id": "B"},
    ]]


def test_unreadable_outlist_is_isolated_and_does_not_guess_document() -> None:
    class BrokenOwner:
        Name = "Bad"

        @property
        def OutList(self):
            raise RuntimeError("broken native property")

    remote = SimpleNamespace(Name="Remote", Label="Remote")
    sheet = SimpleNamespace(Name="Sheet", Label="Sheet", Document=remote)
    remote.Objects = [sheet]
    good = SimpleNamespace(Name="Good", OutList=[sheet])
    root = SimpleNamespace(Objects=[BrokenOwner(), good])
    graph = {"nodes": [
        {"id": "Bad.X", "feature_id": "Bad", "expression": "Remote#Sheet.Width"},
        {"id": "Good.X", "feature_id": "Good", "expression": "Remote#Sheet.Width"},
    ]}
    extract = lambda _: ([parameter("Sheet", "Sheet.A1", "Width")], [], [])
    result = external_parameter_evidence(root, graph, extract, lambda _: "remote.FCStd")
    assert [item["status"] for item in result] == ["unresolved", "resolved"]


def test_external_upstream_trace_is_bounded_and_reports_unsupported_expression() -> None:
    sheet = SimpleNamespace(Name="Sheet", Label="Sheet")
    remote = SimpleNamespace(Name="Remote", Label="Remote", Objects=[sheet])
    sheet.Document = remote
    root = SimpleNamespace(Objects=[SimpleNamespace(Name="Box", OutList=[sheet])])
    evidence = [{"status": "resolved", "source_document": "remote.FCStd",
                 "source_object": "Sheet", "source_parameter": "Sheet.A2"}]
    params = [parameter("Sheet", "Sheet.A1", "Base"),
              parameter("Sheet", "Sheet.A2", "Driven", "=Base * 2")]
    extract = lambda _: (params, [], [])
    graph = external_upstream_graph(root, evidence, extract, lambda _: "remote.FCStd")
    assert graph["status"] == "complete"
    assert graph["edges"] == [{"document": "remote.FCStd", "source": "Sheet.A1",
                               "target": "Sheet.A2", "reference": "Base"}]
    limited = external_upstream_graph(root, evidence, extract, lambda _: "remote.FCStd", max_nodes=1)
    assert limited["truncated"] is True and limited["status"] == "partial"
    params[1]["expression"] = "=Other#Sheet.A1"
    assert external_upstream_graph(root, evidence, extract, lambda _: "remote.FCStd")["status"] == "partial"


def test_external_upstream_traces_multiple_documents_and_obeys_document_limit() -> None:
    last_sheet = SimpleNamespace(Name="Sheet", Label="Sheet", OutList=[])
    last = SimpleNamespace(Name="Last", Label="Last", Objects=[last_sheet], path="last.FCStd")
    last_sheet.Document = last
    middle_sheet = SimpleNamespace(Name="Sheet", Label="Sheet", OutList=[last_sheet])
    middle = SimpleNamespace(Name="Middle", Label="Middle", Objects=[middle_sheet], path="middle.FCStd")
    middle_sheet.Document = middle
    root = SimpleNamespace(Objects=[SimpleNamespace(Name="Box", OutList=[middle_sheet])])
    evidence = [{"status": "resolved", "source_document": "middle.FCStd",
                 "source_object": "Sheet", "source_parameter": "Sheet.A1"}]
    def extract(doc):
        params = ([parameter("Sheet", "Sheet.A1", "Thickness", "=Last#Sheet.Base")]
                  if doc is middle else [parameter("Sheet", "Sheet.A2", "Base")])
        return params, [], []

    graph = external_upstream_graph(root, evidence, extract, lambda doc: doc.path)
    assert {(n["document"], n["id"]) for n in graph["nodes"]} == {
        ("middle.FCStd", "Sheet.A1"), ("last.FCStd", "Sheet.A2")}
    assert graph["cross_document_edges"] == [{
        "source": {"document": "last.FCStd", "node_id": "Sheet.A2"},
        "target": {"document": "middle.FCStd", "node_id": "Sheet.A1"},
        "reference": "Last#Sheet.Base"}]
    assert graph["status"] == "partial"
    limited = external_upstream_graph(root, evidence, extract, lambda doc: doc.path, max_documents=1)
    assert limited["truncated"] is True and limited["status"] == "partial"


def test_external_edges_keep_document_identity_and_unresolved_evidence() -> None:
    graph = {"nodes": [{"id": "Box.Length", "expression": "Remote#Sheet.Width"}],
             "external_references": [
                 {"status": "resolved", "source_document": "a/part.FCStd", "source_object": "Sheet",
                  "source_parameter": "Sheet.A1", "target": "Box.Length", "reference": "Remote#Sheet.Width", "value": 8},
                 {"status": "unresolved", "target": "Box.Length", "reference": "Missing#Sheet.Width"},
             ]}
    add_external_graph_edges(graph, "root.FCStd")
    assert graph["external_edges"] == [{
        "source": {"document": "a/part.FCStd", "object": "Sheet", "parameter": "Sheet.A1"},
        "target": {"document": "root.FCStd", "node_id": "Box.Length"},
        "reference": "Remote#Sheet.Width",
    }]
    assert graph["cross_document_cycles_status"] == "partial"
    assert len(graph["external_references"]) == 2


def test_external_alias_requires_unique_native_document_and_parameter() -> None:
    sheet = SimpleNamespace(Name="Sheet", Label="Sheet")
    remote = SimpleNamespace(Name="Remote", Label="Remote", Objects=[sheet])
    sheet.Document = remote
    owner = SimpleNamespace(Name="Box", OutList=[sheet])
    root = SimpleNamespace(Objects=[owner])
    graph = {"nodes": [{"id": "Box.Length", "feature_id": "Box",
                        "expression": 'Remote#Sheet.Thickness'}]}
    extract = lambda _: ([{"feature_id": "Sheet", "name": "Thickness", "native_name": "Sheet.A1",
                          "value": 12, "unit": "mm"}], [], [])
    result = external_parameter_evidence(root, graph, extract, lambda _: "parts/remote.FCStd")
    assert result[0] == {"target": "Box.Length", "reference": "Remote#Sheet.Thickness",
                         "status": "resolved", "source_document": "parts/remote.FCStd",
                         "source_object": "Sheet", "source_parameter": "Sheet.A1",
                         "value": 12, "unit": "mm", "source_expression": None}
    other = SimpleNamespace(Name="Remote", Label="Other", Objects=[])
    owner.OutList.append(SimpleNamespace(Document=other))
    assert external_parameter_evidence(root, graph, extract, lambda _: "x")[0]["status"] == "ambiguous"
    graph["nodes"][0]["expression"] = '"Remote#Sheet.Thickness"'
    assert external_parameter_evidence(root, graph, extract, lambda _: "x") == []


def parameter(scope: str, native: str, name: str, expression: str = "") -> dict:
    return {"feature_id": scope, "native_name": native, "name": name, "expression": expression}


def test_scoped_references_do_not_fall_back_to_other_objects() -> None:
    graph = parameter_dependency_graph(
        [
            parameter("Other", "Length", "Other.Length"),
            parameter("Box", "Width", "Box.Width", "Missing.Length + Other.Length"),
        ]
    )
    assert graph["edges"] == [
        {"source": "Other.Length", "target": "Box.Width", "reference": "Other.Length"}
    ]
    assert graph["unresolved_references"] == [
        {"target": "Box.Width", "reference": "Missing.Length"}
    ]


def test_local_spreadsheet_alias_is_scoped() -> None:
    graph = parameter_dependency_graph(
        [
            parameter("First", "First.A1", "Thickness"),
            parameter("Second", "Second.A1", "Thickness"),
            parameter("Second", "Second.A2", "Result", "=Thickness * 2"),
        ]
    )
    assert graph["edges"] == [
        {"source": "Second.A1", "target": "Second.A2", "reference": "Thickness"}
    ]


def test_ambiguous_alias_is_not_assigned_an_arbitrary_edge() -> None:
    graph = parameter_dependency_graph(
        [
            parameter("Sheet", "Sheet.A1", "Value"),
            parameter("Sheet", "Sheet.A2", "Value"),
            parameter("Sheet", "Sheet.A3", "Result", "=Value"),
        ]
    )
    assert graph["edges"] == []
    assert graph["ambiguous_references"] == [{"target": "Sheet.A3", "reference": "Value"}]


def test_unsupported_quoted_reference_is_not_falsely_resolved() -> None:
    graph = parameter_dependency_graph(
        [
            parameter("Part", "Length", "Part.Length"),
            parameter("Box", "Width", "Box.Width", "<<Part.Length>> + 1 mm"),
        ]
    )
    assert graph["edges"] == []
    assert graph["unsupported_expressions"] == ["Box.Width"]


def test_label_reference_resolves_only_when_object_label_is_unique() -> None:
    parameters = [
        parameter("Part", "Length", "Part.Length"),
        parameter("Box", "Width", "Box.Width", "<<Design Plate>>.Length"),
    ]
    graph = parameter_dependency_graph(parameters, object_labels={"Design Plate": ["Part"]})
    assert graph["edges"] == [
        {"source": "Part.Length", "target": "Box.Width", "reference": "<<Design Plate>>.Length"}
    ]
    assert graph["unsupported_expressions"] == []
    graph = parameter_dependency_graph(
        parameters, object_labels={"Design Plate": ["Part", "Other"]}
    )
    assert graph["edges"] == []
    assert graph["ambiguous_references"] == [
        {"target": "Box.Width", "reference": "<<Design Plate>>.Length"}
    ]


def test_cross_document_reference_does_not_resolve_against_local_object() -> None:
    graph = parameter_dependency_graph(
        [
            parameter("Part", "Length", "Part.Length"),
            parameter("Box", "Width", "Box.Width", "OtherDoc#Part.Length"),
        ]
    )
    assert graph["edges"] == []
    assert graph["unsupported_expressions"] == ["Box.Width"]


def test_cross_document_label_property_does_not_create_local_dependency() -> None:
    graph = parameter_dependency_graph(
        [
            parameter("Box", "Length", "Box.Length"),
            parameter("Box", "Width", "Box.Width", "OtherDoc#<<Remote Part>>.Length"),
        ],
        object_labels={"Remote Part": ["Box"]},
    )
    assert graph["edges"] == []
    assert graph["unsupported_expressions"] == ["Box.Width"]


def test_unindexed_local_reference_is_not_silently_ignored() -> None:
    graph = parameter_dependency_graph([parameter("Box", "Width", "Box.Width", "Length * 2")])
    assert graph["unresolved_references"] == [{"target": "Box.Width", "reference": "Length"}]


def test_constraint_property_paths_are_scoped_to_their_sketch() -> None:
    graph = parameter_dependency_graph(
        [
            parameter("Sheet", "Sheet.A1", "Length"),
            parameter("Sketch", "Constraints.Width", "Sketch.Constraints.Width", "Sheet.Length"),
        ]
    )
    assert graph["edges"] == [
        {"source": "Sheet.A1", "target": "Sketch.Constraints.Width", "reference": "Sheet.Length"}
    ]


def test_local_constraint_references_do_not_resolve_to_another_sketch() -> None:
    graph = parameter_dependency_graph(
        [
            parameter("First", "Constraints.Width", "First.Constraints.Width"),
            parameter("Second", "Constraints.Width", "Second.Constraints.Width"),
            parameter("Second", "Constraints[1]", "Second.Constraints[1]", "Constraints.Width"),
            parameter("Second", "Constraints[2]", "Second.Constraints[2]", "Constraints[1]"),
        ]
    )
    assert {(edge["source"], edge["target"]) for edge in graph["edges"]} == {
        ("Second.Constraints.Width", "Second.Constraints[1]"),
        ("Second.Constraints[1]", "Second.Constraints[2]"),
    }


def test_native_sketch_constraint_quantity_and_expression_are_extracted() -> None:
    class Datum:
        def __str__(self):
            return "12.0 mm"

    sketch = SimpleNamespace(
        TypeId="Sketcher::SketchObject",
        Name="Sketch",
        Label="Sketch",
        ExpressionEngine=[(".Constraints.Width", "Sheet.Length")],
        PropertiesList=[],
        Constraints=[SimpleNamespace(Name="Width")],
        getDatum=lambda _index: Datum(),
        isDriving=lambda _index: True,
    )
    parameters, _, skipped = native_parameters(
        SimpleNamespace(Objects=[sketch]),
        lambda _value: (12.0, "Unit: mm (1,0,0,0,0,0,0,0) [Length]"),
    )
    assert skipped == []
    assert parameters[0]["native_name"] == "Constraints.Width"
    assert parameters[0]["expression"] == "Sheet.Length"
    assert parameters[0]["unit"] == "mm"
    assert parameters[0]["is_key"] is False


def test_native_sketch_get_driving_marks_reference_constraints_as_not_key() -> None:
    sketch = SimpleNamespace(
        TypeId="Sketcher::SketchObject", Name="Sketch", Label="Sketch", ExpressionEngine=[],
        PropertiesList=[], Constraints=[SimpleNamespace(Name="Length")],
        getDatum=lambda _: 5.0, getDriving=lambda _: False,
    )
    parameters, _, _ = native_parameters(SimpleNamespace(Objects=[sketch]), lambda value: (value, "mm"))
    assert parameters[0]["is_key"] is False
    sketch.getDriving = lambda _: True
    parameters, _, _ = native_parameters(SimpleNamespace(Objects=[sketch]), lambda value: (value, "mm"))
    assert parameters[0]["is_key"] is True


def test_indexed_constraint_reference_by_object_label() -> None:
    graph = parameter_dependency_graph(
        [
            parameter("Sketch", "Constraints[0]", "Sketch.Constraints[0]"),
            parameter("Box", "Length", "Box.Length", "<<Design Sketch>>.Constraints[0]"),
        ],
        object_labels={"Design Sketch": ["Sketch"]},
    )
    assert graph["edges"] == [
        {
            "source": "Sketch.Constraints[0]",
            "target": "Box.Length",
            "reference": "<<Design Sketch>>.Constraints[0]",
        }
    ]


def test_large_dependency_chain_does_not_exceed_recursion_limit() -> None:
    graph = parameter_dependency_graph(
        [
            parameter("Sheet", f"Sheet.A{i}", f"Value{i}", f"=Value{i - 1}" if i else "")
            for i in range(1500)
        ]
    )
    assert len(graph["edges"]) == 1499
    assert graph["cycles"] == []


def test_cycle_is_reported_deterministically() -> None:
    parameters = [
        parameter("Sheet", "Sheet.A1", "First", "=Second"),
        parameter("Sheet", "Sheet.A2", "Second", "=First"),
    ]
    assert parameter_dependency_graph(parameters)["cycles"] == [["Sheet.A1", "Sheet.A2"]]
    assert parameter_dependency_graph(parameters) == parameter_dependency_graph(
        list(reversed(parameters))
    )


def test_large_cycle_is_reported_without_recursion() -> None:
    count = 1500
    parameters = [parameter("Sheet", f"Sheet.A{i}", f"Value{i}",
                            f"=Value{(i + 1) % count}") for i in range(count)]
    graph = parameter_dependency_graph(parameters)
    assert len(graph["cycles"]) == 1
    assert len(graph["cycles"][0]) == count
    assert graph["cycles"][0][0] == "Sheet.A0"


def test_parameter_provider_isolates_extraction_failure() -> None:
    def fail(_document):
        raise RuntimeError("bad property")

    result = ParameterExtractor(fail).extract(SimpleNamespace(Objects=[]))
    assert result.capability == "partial"
    assert result.records == []
    assert result.evidence["graph"]["edges"] == []
    assert result.diagnostics[0]["code"] == "PARAMETER_EXTRACTION_FAILED"
    assert result.duration_ms >= 0


def test_parameter_provider_preserves_other_values_when_a_property_is_unreadable() -> None:
    class Object:
        Name = "Part"
        Label = "Part"
        TypeId = "App::FeaturePython"
        ExpressionEngine = ()
        PropertiesList = ("Bad", "Good")
        Good = 3.0

        @property
        def Bad(self):
            raise RuntimeError("unsupported property")

        def getGroupOfProperty(self, _name):
            return "Design"

    provider = ParameterExtractor(
        lambda document: native_parameters(document, lambda value: (float(value), None))
    )
    result = provider.extract(SimpleNamespace(Objects=[Object()]))
    assert [parameter["name"] for parameter in result.records] == ["Part.Good"]
    assert result.evidence["skipped"] == [{"object": "Part", "property": "Bad"}]
    assert result.capability == "partial"
    assert result.diagnostics[0]["code"] == "PARAMETER_PARTIAL_EXTRACTION"
