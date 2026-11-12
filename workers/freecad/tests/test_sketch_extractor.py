from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from sketch_extractor import SketchExtractor, _indexed_topology_references


def test_native_get_driving_and_active_methods_are_used() -> None:
    sketch = SimpleNamespace(
        TypeId="Sketcher::SketchObject", Name="Sketch", Geometry=[],
        Constraints=[SimpleNamespace(Type="Distance", Value=5.0)],
        getDriving=lambda _: False, getActive=lambda _: True,
    )
    result = SketchExtractor(
        lambda _: None, lambda value: (value, "mm") if value is not None else (None, None),
        lambda _: [],
    ).extract(SimpleNamespace(Objects=[sketch]))
    constraint = result.records[0]["constraints"][0]
    assert constraint["driving"] is False and constraint["active"] is True


def test_non_dimensional_constraint_without_driving_state_is_retained() -> None:
    def unavailable(_index):
        raise ValueError("constraint has no driving datum")

    sketch = SimpleNamespace(
        TypeId="Sketcher::SketchObject", Name="Sketch", Geometry=[],
        Constraints=[SimpleNamespace(Type="Horizontal")], getDriving=unavailable,
        getActive=lambda _: True,
    )
    result = SketchExtractor(lambda _: None, lambda _: (None, None), lambda _: []).extract(
        SimpleNamespace(Objects=[sketch])
    )
    assert result.records[0]["constraints"][0]["active"] is True
    assert "driving" not in result.records[0]["constraints"][0]


def test_sketch_provider_isolates_a_bad_sketch() -> None:
    sketch = SimpleNamespace(TypeId="Sketcher::SketchObject", Name="Bad", Geometry=[object()])

    def fail(_value):
        raise RuntimeError("unsupported geometry")

    result = SketchExtractor(fail, lambda _value: (None, None), lambda _value: []).extract(
        SimpleNamespace(Objects=[sketch])
    )
    assert result.records == []
    assert result.evidence["skipped"] == 1
    assert result.capability == "partial"
    assert result.diagnostics[0]["code"] == "SKETCH_EXTRACTION_FAILED"
    assert result.duration_ms >= 0


def test_constraint_expressions_are_attached_by_name_and_native_index() -> None:
    sketch = SimpleNamespace(
        TypeId="Sketcher::SketchObject",
        Name="Sketch",
        Geometry=[],
        Constraints=[
            SimpleNamespace(Type="Radius", Name="Driven"),
            SimpleNamespace(Type="Distance", Name=""),
        ],
        ExpressionEngine=[(".Constraints.Driven", "Parameters.Radius"), ("Constraints[1]", "2 mm")],
    )
    result = SketchExtractor(
        lambda _value: None, lambda _value: (None, None), lambda _value: []
    ).extract(SimpleNamespace(Objects=[sketch]))
    assert [item["expression"] for item in result.records[0]["constraints"]] == [
        "Parameters.Radius",
        "2 mm",
    ]


def test_solver_conflicts_override_up_to_date_fully_constrained_state() -> None:
    sketch = SimpleNamespace(
        TypeId="Sketcher::SketchObject",
        Name="Sketch",
        Geometry=[],
        Constraints=[],
        State=["Up-to-date"],
        FullyConstrained=True,
        ConflictingConstraints=[1, 2],
    )
    result = SketchExtractor(
        lambda _value: None, lambda _value: (None, None), lambda _value: []
    ).extract(SimpleNamespace(Objects=[sketch]))
    assert result.records[0]["solver_state"] == "error"
    assert result.records[0]["fully_constrained"] is None
    assert any(item["code"] == "SKETCH_SOLVER_ERROR" for item in result.diagnostics)


def test_negative_solver_dof_is_not_interpreted_as_fully_constrained() -> None:
    sketch = SimpleNamespace(
        TypeId="Sketcher::SketchObject",
        Name="Sketch",
        Geometry=[],
        Constraints=[],
        State=["Up-to-date"],
        getSolverDoFs=lambda: -1,
    )
    result = SketchExtractor(
        lambda _value: None, lambda _value: (None, None), lambda _value: []
    ).extract(SimpleNamespace(Objects=[sketch]))
    assert result.records[0]["degrees_of_freedom"] is None
    assert result.records[0]["fully_constrained"] is None
    assert result.records[0]["solver_state"] == "unknown"
    assert any(
        item["code"] == "SKETCH_DEGREES_OF_FREEDOM_UNAVAILABLE" for item in result.diagnostics
    )


def test_indexed_topology_references_do_not_claim_damage() -> None:
    support = [{"object": "Body", "document": "Doc", "subelements": ["Face1", "DatumPlane"]}]
    external = [
        {"object": "Part", "document": "Doc", "subelements": ["Body.Edge2", "Vertex1", "Edge0"]}
    ]
    references = _indexed_topology_references(support, external)
    assert [(item["role"], item["subelement"]) for item in references] == [
        ("attachment_support", "Face1"),
        ("external_geometry", "Body.Edge2"),
        ("external_geometry", "Vertex1"),
    ]
    assert all("status" not in item for item in references)
