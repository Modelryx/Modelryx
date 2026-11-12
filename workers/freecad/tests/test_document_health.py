from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from document_health import DocumentInspector, object_health, recompute_for_extraction


def placement_object(name="Link", offset=0):
    matrix = [1.0, 0, 0, offset, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    return SimpleNamespace(
        Name=name, PropertiesList=["Placement"],
        Placement=SimpleNamespace(toMatrix=lambda: SimpleNamespace(A=list(matrix))),
    ), matrix


def test_recompute_reports_changed_placements_without_saving():
    obj, matrix = placement_object()

    def recompute():
        matrix[3] = 10
        return True

    succeeded, error, diagnostics = recompute_for_extraction(
        SimpleNamespace(Objects=[obj], recompute=recompute)
    )
    assert succeeded and error is None
    assert diagnostics[0]["code"] == "RECOMPUTE_CHANGED_PLACEMENTS"
    assert "source was not saved" in diagnostics[0]["message"]


def test_unchanged_placement_does_not_claim_geometry_equivalence():
    obj, _ = placement_object()
    assert recompute_for_extraction(SimpleNamespace(Objects=[obj], recompute=lambda: True)) == (
        True, None, [],
    )


def test_unreadable_placement_and_recompute_failure_remain_explicit():
    obj, matrix = placement_object()
    matrix[0] = float("nan")

    def recompute():
        raise RuntimeError("solver failure")

    succeeded, error, diagnostics = recompute_for_extraction(
        SimpleNamespace(Objects=[obj], recompute=recompute)
    )
    assert succeeded is False and error == "solver failure"
    assert diagnostics[0]["code"] == "RECOMPUTE_PLACEMENT_COMPARISON_INCOMPLETE"


def test_object_budget_is_enforced_before_recompute(monkeypatch):
    import document_health

    monkeypatch.setattr(document_health, "MAX_DOCUMENT_OBJECTS", 1)
    def recompute():
        pytest.fail("over-budget document was recomputed")

    with pytest.raises(ValueError, match="object limit"):
        recompute_for_extraction(SimpleNamespace(Objects=[object(), object()], recompute=recompute))


def test_feature_after_tip_does_not_degrade_geometry() -> None:
    active = SimpleNamespace(Name="Tip", TypeId="PartDesign::Pad", PropertiesList=["Profile"],
                             Profile=SimpleNamespace(Name="Sketch"), State=[])
    later = SimpleNamespace(Name="Later", TypeId="PartDesign::Pad", PropertiesList=["Shape"],
                            State=["Invalid"], Shape=SimpleNamespace(isNull=lambda: True))
    body = SimpleNamespace(Name="Body", TypeId="PartDesign::Body", Group=[active, later], Tip=active)
    result = DocumentInspector(lambda _: True).inspect(
        SimpleNamespace(Objects=[body, active, later]), recompute_succeeded=True
    )
    assert not any(d["section"] == "geometry" for d in result.diagnostics)
    assert any(d["section"] == "features" and d["object_name"] == "Later" for d in result.diagnostics)


def test_missing_profile_property_marks_active_feature_unverified() -> None:
    obj = SimpleNamespace(Name="Pad", TypeId="PartDesign::Pad", PropertiesList=[], State=[])
    result = DocumentInspector(lambda _: True).inspect(
        SimpleNamespace(Objects=[obj]), recompute_succeeded=True
    )
    assert {(item["section"], item["code"]) for item in result.diagnostics} == {
        ("features", "FEATURE_SUPPORT_INSPECTION_FAILED"),
        ("geometry", "FEATURE_SUPPORT_INSPECTION_FAILED"),
    }


def test_unreadable_inactive_feature_state_does_not_degrade_geometry() -> None:
    class Inactive:
        Name = "Later"
        TypeId = "PartDesign::Pad"
        PropertiesList = ()

        @property
        def State(self):
            raise RuntimeError("unreadable state")

    active = SimpleNamespace(Name="Tip", TypeId="PartDesign::Feature", PropertiesList=[], State=[])
    later = Inactive()
    body = SimpleNamespace(Name="Body", TypeId="PartDesign::Body", Group=[active, later], Tip=active)
    result = DocumentInspector(lambda _: True).inspect(
        SimpleNamespace(Objects=[body, active, later]), recompute_succeeded=True
    )
    assert not any(item["section"] == "geometry" for item in result.diagnostics)
    assert any(item["section"] == "features" and item["code"] == "OBJECT_HEALTH_INSPECTION_FAILED"
               for item in result.diagnostics)


def test_feature_execution_error_degrades_feature_and_geometry() -> None:
    feature = SimpleNamespace(
        Name="Pad", TypeId="PartDesign::Pad", PropertiesList=["Shape"], State=["Invalid"]
    )
    issues, _ = object_health(feature)
    assert {(section, code) for section, code, _ in issues} == {
        ("features", "OBJECT_EXECUTION_ERROR"),
        ("geometry", "OBJECT_EXECUTION_ERROR"),
    }
    feature.TypeId = "Part::Cut"
    issues, _ = object_health(feature, feature_history=True)
    assert {section for section, _, _ in issues} == {"features", "geometry"}


def test_touched_shape_and_unresolved_link_are_reported() -> None:
    obj = SimpleNamespace(
        Name="Link",
        TypeId="App::Link",
        PropertiesList=["Shape"],
        State=["Touched"],
        LinkedObject=None,
    )
    issues, _ = object_health(obj)
    assert {(section, code) for section, code, _ in issues} == {
        ("geometry", "STALE_OBJECT_SHAPE"),
        ("geometry", "UNRESOLVED_LINK_TARGET"),
        ("assembly", "UNRESOLVED_LINK_TARGET"),
    }


def test_body_tip_health_and_later_features() -> None:
    first = SimpleNamespace(Name="Pad")
    later = SimpleNamespace(Name="Pocket")
    body = SimpleNamespace(Name="Body", TypeId="PartDesign::Body", Group=[first, later], Tip=first)
    issues, after_tip = object_health(body)
    assert not issues
    assert after_tip == ["Pocket"]
    body.Tip = None
    assert {code for _, code, _ in object_health(body)[0]} == {"NULL_BODY_TIP"}
    body.Tip = SimpleNamespace(Name="Other")
    assert {code for _, code, _ in object_health(body)[0]} == {"INVALID_BODY_TIP"}


def test_expression_binding_errors_require_native_error_evidence() -> None:
    obj = SimpleNamespace(
        Name="Box",
        TypeId="Part::Box",
        PropertiesList=["Shape"],
        State=["Invalid"],
        getStatusString=lambda: "Document object not found\nin property binding 'Box.Length'",
    )
    issues, _ = object_health(obj, feature_history=True)
    assert ("parameters", "BROKEN_EXPRESSION") in {(section, code) for section, code, _ in issues}
    obj.getStatusString = lambda: "Shape is invalid"
    assert "BROKEN_EXPRESSION" not in {code for _, code, _ in object_health(obj)[0]}
    obj.State = ["Up-to-date"]
    obj.getStatusString = lambda: "in property binding"
    assert not object_health(obj)[0]


def test_document_inspector_returns_records_evidence_and_diagnostics() -> None:
    shape = SimpleNamespace(isNull=lambda: False, isValid=lambda: False)
    obj = SimpleNamespace(
        Name="Broken", TypeId="Part::Feature", PropertiesList=["Shape"], State=[], Shape=shape
    )
    result = DocumentInspector(lambda _type: True).inspect(
        SimpleNamespace(Objects=[obj]), recompute_succeeded=True
    )
    assert result.records == [{"name": "Broken", "type_id": "Part::Feature"}]
    assert result.evidence["objects_checked"] == 1
    assert result.capability == "partial"
    assert result.diagnostics[0]["code"] == "INVALID_SHAPE"
    assert result.duration_ms >= 0


def test_document_inspector_records_each_body_with_an_invalid_tip_once() -> None:
    feature = SimpleNamespace(Name="Feature")
    missing = SimpleNamespace(
        Name="MissingTip", TypeId="PartDesign::Body", Group=[feature], Tip=None
    )
    invalid = SimpleNamespace(
        Name="InvalidTip",
        TypeId="PartDesign::Body",
        Group=[feature],
        Tip=SimpleNamespace(Name="Other"),
    )
    result = DocumentInspector(lambda _type: True).inspect(
        SimpleNamespace(Objects=[missing, invalid]), recompute_succeeded=True
    )
    assert result.evidence["bodies_with_invalid_tips"] == ["InvalidTip", "MissingTip"]


def test_missing_profile_reports_only_active_unsuppressed_features() -> None:
    active = SimpleNamespace(
        Name="Pad", TypeId="PartDesign::Pad", PropertiesList=["Profile"], Profile=None
    )
    later = SimpleNamespace(
        Name="Pocket", TypeId="PartDesign::Pocket", PropertiesList=["Profile"], Profile=None
    )
    suppressed = SimpleNamespace(
        Name="SuppressedPad",
        TypeId="PartDesign::Pad",
        PropertiesList=["Profile", "Suppressed"],
        Profile=None,
        Suppressed=True,
        hasExtension=lambda _name: True,
    )
    body = SimpleNamespace(
        Name="Body", TypeId="PartDesign::Body", Group=[active, later], Tip=active
    )
    result = DocumentInspector(lambda _type: True).inspect(
        SimpleNamespace(Objects=[body, active, later, suppressed]), recompute_succeeded=True
    )
    issues = [item for item in result.diagnostics if item["code"] == "MISSING_FEATURE_SUPPORT"]
    assert {(item["object_name"], item["section"]) for item in issues} == {
        ("Pad", "features"),
        ("Pad", "geometry"),
    }
    active.Profile = []
    result = DocumentInspector(lambda _type: True).inspect(
        SimpleNamespace(Objects=[body, active, later, suppressed]), recompute_succeeded=True
    )
    assert any(item["code"] == "MISSING_FEATURE_SUPPORT" for item in result.diagnostics)


def test_document_inspector_isolates_unreadable_object_and_checks_remaining_shapes() -> None:
    class BrokenObject:
        Name = "Broken"
        TypeId = "PartDesign::Pad"
        PropertiesList = ("Shape",)

        @property
        def State(self):
            raise RuntimeError("unreadable state")

        @property
        def Shape(self):
            raise RuntimeError("unreadable shape")

    valid = SimpleNamespace(
        Name="Other",
        TypeId="Part::Feature",
        PropertiesList=["Shape"],
        State=[],
        Shape=SimpleNamespace(isNull=lambda: False, isValid=lambda: True),
    )
    result = DocumentInspector(lambda type_id: type_id.startswith("PartDesign::")).inspect(
        SimpleNamespace(Objects=[BrokenObject(), valid]), recompute_succeeded=True
    )
    assert result.evidence["objects_checked"] == 2
    assert {(item["section"], item["code"]) for item in result.diagnostics} == {
        ("geometry", "OBJECT_HEALTH_INSPECTION_FAILED"),
        ("features", "OBJECT_HEALTH_INSPECTION_FAILED"),
        ("geometry", "SHAPE_VALIDATION_FAILED"),
        ("features", "FEATURE_SUPPORT_INSPECTION_FAILED"),
        ("geometry", "FEATURE_SUPPORT_INSPECTION_FAILED"),
    }
