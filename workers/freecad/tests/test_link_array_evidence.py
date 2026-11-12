from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from assembly_extractor import AssemblyExtractor, native_assembly_joints
from link_array_evidence import _definition_evidence, compose_world_transform, link_arrays


def test_array_definition_evidence_rejects_configured_or_different_targets() -> None:
    target = object()

    def link(mode, linked):
        return SimpleNamespace(
            LinkCopyOnChange=mode, LinkedObject=linked,
            getTypeIdOfProperty=lambda _: "App::PropertyEnumeration",
        )

    assert _definition_evidence(link("Disabled", target), target, [], 2)["status"] == "uniform"
    assert _definition_evidence(link("Enabled", target), target, [], 2)["status"] == "configured"
    assert _definition_evidence(
        link("Disabled", target), target, [link("Owned", target)], 1, expanded=True
    )["status"] == "configured"
    assert _definition_evidence(
        link("Disabled", target), target, [link("Disabled", object())], 1, expanded=True
    )["status"] == "heterogeneous"
    assert _definition_evidence(link("Disabled", target), target, [], 0)["status"] == "uniform"
    assert _definition_evidence(SimpleNamespace(), target, [], 2)["status"] == "unavailable"
    assert _definition_evidence(link("Disabled", target), target, [], 2, expanded=True)["status"] == "unavailable"


def test_owned_array_requires_native_storage_and_uniform_effective_definition() -> None:
    doc = object()
    source = SimpleNamespace(Name="Source", Document=doc)
    target = SimpleNamespace(Name="Copy", Document=doc)
    group = SimpleNamespace(TypeId="App::LinkGroup", Document=doc, ElementList=[target])
    property_types = {
        "LinkCopyOnChange": "App::PropertyEnumeration",
        "LinkCopyOnChangeSource": "App::PropertyXLink",
        "LinkCopyOnChangeGroup": "App::PropertyLink",
    }
    owner = SimpleNamespace(
        Document=doc, LinkCopyOnChange="Owned", LinkCopyOnChangeSource=source,
        LinkCopyOnChangeGroup=group, getTypeIdOfProperty=property_types.get,
    )
    element = SimpleNamespace(
        LinkedObject=target, LinkCopyOnChange="Disabled", getTypeIdOfProperty=property_types.get,
    )
    assert _definition_evidence(owner, target, [element], 1, expanded=True)["status"] == "owned_uniform"
    group.ElementList = []
    assert _definition_evidence(owner, target, [element], 1, expanded=True)["status"] == "configured"
    group.ElementList = [target]
    element.LinkedObject = source
    assert _definition_evidence(owner, target, [element], 1, expanded=True)["status"] == "heterogeneous"


def test_joint_group_is_not_a_constraint() -> None:
    group = SimpleNamespace(TypeId="Assembly::JointGroup", Name="Joints", PropertiesList=[])
    joints, skipped = native_assembly_joints(
        SimpleNamespace(Objects=[group]), lambda _: None, lambda _: None, lambda _: (None, None)
    )
    assert joints == []
    assert skipped == 0


def test_solver_observation_is_unavailable_without_calling_mutating_solve() -> None:
    def solve():
        raise AssertionError("metadata extraction must not run the assembly solver")

    assembly = SimpleNamespace(
        TypeId="Assembly::AssemblyObject", Name="Assembly", PropertiesList=[], solve=solve,
    )
    extractor = AssemblyExtractor(
        lambda _: None, lambda _: None, lambda _: (None, None), lambda _: None, 10,
    )
    result = extractor.extract(SimpleNamespace(Objects=[assembly]))
    assert any(
        issue["code"] == "ASSEMBLY_SOLVER_OBSERVATION_UNAVAILABLE"
        and issue["object_name"] == "Assembly"
        for issue in result.diagnostics
    )


def test_joint_with_missing_reference_is_partial() -> None:
    joint = SimpleNamespace(TypeId="Assembly::Joint", Name="Joint", JointType="Fixed",
                            PropertiesList=["Reference1", "Reference2", "Suppressed"],
                            Reference1=None, Reference2=None, Suppressed=False,
                            hasExtension=lambda _: True)
    result = AssemblyExtractor(lambda _: None, lambda _: None, lambda _: (None, None),
                               lambda _: "doc", 10).extract(SimpleNamespace(Objects=[joint]))
    assert result.records[0]["references_complete"] is False
    assert result.evidence["incomplete_joint_references"] == 1
    assert result.capability == "partial"
    assert any(d["code"] == "ASSEMBLY_JOINT_REFERENCES_INCOMPLETE" for d in result.diagnostics)


def test_fixed_joint_does_not_ground_its_components() -> None:
    fixed = SimpleNamespace(
        TypeId="Assembly::Joint", Name="Fixed", JointType="Fixed", PropertiesList=[]
    )
    grounded = SimpleNamespace(
        TypeId="Assembly::Joint", Name="Ground", JointType="Grounded", PropertiesList=[]
    )
    joints, skipped = native_assembly_joints(
        SimpleNamespace(Objects=[fixed, grounded]),
        lambda _value: None,
        lambda _value: None,
        lambda _value: (None, None),
    )
    assert skipped == 0
    assert {joint["name"]: joint["grounded"] for joint in joints} == {
        "Fixed": False,
        "Ground": True,
    }


def test_joint_suppression_requires_native_extension() -> None:
    def joint(name, suppressed, native):
        return SimpleNamespace(
            TypeId="Assembly::Joint", Name=name, JointType="Fixed",
            PropertiesList=["Suppressed"], Suppressed=suppressed,
            hasExtension=lambda extension: native and extension == "App::SuppressibleExtension",
        )

    document = SimpleNamespace(Objects=[
        joint("Active", False, True),
        joint("Suppressed", True, True),
        joint("Custom", True, False),
        SimpleNamespace(TypeId="Assembly::Joint", Name="Missing", JointType="Fixed", PropertiesList=[]),
    ])
    extractor = AssemblyExtractor(
        lambda _value: None, lambda _value: None, lambda _value: (None, None),
        lambda _document: None, 10,
    )
    result = extractor.extract(document)
    joints = {item["name"]: item for item in result.records}
    assert joints["Active"]["suppressed"] is False
    assert joints["Suppressed"]["suppressed"] is True
    assert joints["Active"]["suppression_source"] == "native"
    assert "suppressed" not in joints["Custom"]
    assert joints["Custom"]["suppression_source"] == "unverified"
    assert "suppressed" not in joints["Missing"]
    assert joints["Missing"]["suppression_source"] == "not_exposed"
    assert result.evidence["unverified_joint_suppression"] == 2
    assert result.capability == "partial"
    assert any(item["code"] == "ASSEMBLY_JOINT_SUPPRESSION_UNVERIFIED" for item in result.diagnostics)


def test_link_array_evidence_is_bounded_and_does_not_invent_visibility() -> None:
    obj = SimpleNamespace(
        TypeId="App::Link",
        Name="Array",
        ElementCount=3,
        ElementList=[],
        PlacementList=[1, 2, 3],
        ScaleList=[SimpleNamespace(x=2, y=1, z=0.5)],
        VisibilityList=[True],
        LinkedObject=None,
        Document=None,
    )
    arrays = link_arrays(
        SimpleNamespace(Objects=[obj]),
        placement_value=lambda value: {"position_mm": [value, 0, 0]},
        document_identity=lambda _doc: "Doc",
        max_elements=2,
    )
    assert arrays[0]["element_count"] == 3
    assert arrays[0]["truncated"] is True
    assert arrays[0]["suppression_summary"] == {
        "explicit_suppressed_count": 0,
        "explicit_active_count": 0,
        "unknown_inspected_count": 2,
        "uninspected_count": 1,
    }
    assert [item["placement"]["position_mm"][0] for item in arrays[0]["elements"]] == [1, 2]
    assert [item["visibility"] for item in arrays[0]["elements"]] == [True, None]
    assert [item["suppression"] for item in arrays[0]["elements"]] == [
        {"state": "unknown", "source": "collapsed_array"},
        {"state": "unknown", "source": "collapsed_array"},
    ]
    assert [item["scale"] for item in arrays[0]["elements"]] == [[2, 1, 0.5], None]


def test_link_array_does_not_convert_non_boolean_visibility_to_true() -> None:
    obj = SimpleNamespace(TypeId="App::Link", Name="Array", ElementCount=2, ElementList=[],
                          PlacementList=[], ScaleList=[], VisibilityList=["False", 1], LinkedObject=None)
    result = link_arrays(SimpleNamespace(Objects=[obj]), placement_value=lambda _: None,
                         document_identity=lambda _: "doc", max_elements=2)
    assert [element["visibility"] for element in result[0]["elements"]] == [None, None]


def test_truncated_collapsed_array_retains_verified_definition_but_not_activity() -> None:
    target = SimpleNamespace(Name="Source", TypeId="Part::Feature", Document=None)
    array = SimpleNamespace(
        TypeId="App::Link", Name="Array", ElementCount=3, ElementList=[], ShowElement=False,
        LinkedObject=target, Document=None, LinkCopyOnChange="Disabled",
        getTypeIdOfProperty=lambda _: "App::PropertyEnumeration",
    )
    evidence = link_arrays(
        SimpleNamespace(Objects=[array]), placement_value=lambda _: None,
        document_identity=lambda _: "doc", max_elements=1,
    )[0]
    assert evidence["definition_evidence"] == {"copy_on_change": "Disabled", "status": "uniform"}
    assert evidence["truncated"] is True
    assert evidence["suppression_summary"]["uninspected_count"] == 2
    assert evidence["elements"][0]["suppression"]["state"] == "unknown"


def test_expanded_link_array_reports_only_finite_native_scales() -> None:
    elements = [
        SimpleNamespace(Name="A", ScaleVector=SimpleNamespace(x=1, y=2, z=3)),
        SimpleNamespace(Name="B", ScaleVector=SimpleNamespace(x=float("nan"), y=1, z=1)),
    ]
    obj = SimpleNamespace(TypeId="App::Link", Name="Array", ElementCount=2, ElementList=elements)
    arrays = link_arrays(
        SimpleNamespace(Objects=[obj]),
        placement_value=lambda _value: None,
        document_identity=lambda _doc: "Doc",
        max_elements=2,
    )
    assert [item["scale"] for item in arrays[0]["elements"]] == [[1, 2, 3], None]


def test_link_array_records_native_transform_per_element() -> None:
    obj = SimpleNamespace(TypeId="App::Link", Name="Array", ElementCount=2, ElementList=[])
    arrays = link_arrays(
        SimpleNamespace(Objects=[obj]),
        placement_value=lambda _value: None,
        document_identity=lambda _doc: "Doc",
        max_elements=2,
        element_transform=lambda _obj, index: {"matrix": [[index]], "translation_unit": "mm"},
    )
    assert [item["transform_to_array_parent"]["matrix"] for item in arrays[0]["elements"]] == [
        [[0]],
        [[1]],
    ]


def test_composes_world_transform_and_rejects_invalid_evidence() -> None:
    parent = [[1, 0, 0, 100], [0, 1, 0, 20], [0, 0, 1, 0], [0, 0, 0, 1]]
    local = {
        "matrix": [[2, 0, 0, 10], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        "translation_unit": "mm",
    }
    assert compose_world_transform(parent, local)["matrix"] == [
        [2, 0, 0, 110],
        [0, 1, 0, 20],
        [0, 0, 1, 0],
        [0, 0, 0, 1],
    ]
    assert compose_world_transform(parent, {"matrix": [[1]], "translation_unit": "mm"}) is None
    assert compose_world_transform(parent, None) is None
    extreme = [[1e308, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    assert compose_world_transform(extreme, {"matrix": extreme, "translation_unit": "mm"}) is None


def test_assembly_provider_marks_unexpanded_arrays_partial() -> None:
    obj = SimpleNamespace(
        TypeId="App::Link",
        Name="Array",
        ElementCount=1,
        ElementList=[],
        PlacementList=[],
        VisibilityList=[],
        LinkedObject=None,
        Document=None,
    )
    result = AssemblyExtractor(
        lambda _value: None,
        lambda _value: None,
        lambda _value: (None, None),
        lambda _document: "Doc",
        10,
    ).extract(SimpleNamespace(Objects=[obj]))
    assert result.capability == "partial"
    assert result.evidence["arrays"][0]["element_count"] == 1
    assert {item["section"] for item in result.diagnostics} == {"assembly", "geometry"}
    issue = next(item for item in result.diagnostics if item["code"] == "LINK_ARRAY_ELEMENT_ACTIVITY_UNKNOWN")
    assert issue["object_name"] == "Array"
    assert "1 elements with unknown suppression" in issue["message"]


def test_assembly_provider_reports_truncation_and_missing_transforms() -> None:
    obj = SimpleNamespace(TypeId="App::Link", Name="Array", ElementCount=3, ElementList=[])
    result = AssemblyExtractor(
        lambda _value: None,
        lambda _value: None,
        lambda _value: (None, None),
        lambda _document: "Doc",
        2,
        lambda _obj, _index: None,
    ).extract(SimpleNamespace(Objects=[obj]))
    issues = {item["code"]: item["message"] for item in result.diagnostics}
    assert "1 link-array elements" in issues["LINK_ARRAY_EVIDENCE_TRUNCATED"]
    assert "2 link-array elements" in issues["LINK_ARRAY_TRANSFORM_UNAVAILABLE"]
    assert "2 elements with unknown suppression and 1 elements not inspected" in issues["LINK_ARRAY_ELEMENT_ACTIVITY_UNKNOWN"]


def test_array_element_suppression_is_independent_of_visibility() -> None:
    active = SimpleNamespace(Name="Hidden", PropertiesList=[], hasExtension=lambda _name: False)
    suppressed = SimpleNamespace(
        Name="Suppressed",
        PropertiesList=["Suppressed"],
        Suppressed=True,
        hasExtension=lambda _name: True,
    )
    obj = SimpleNamespace(
        TypeId="App::Link",
        Name="Array",
        ElementCount=2,
        ElementList=[active, suppressed],
        VisibilityList=[False, True],
    )
    result = AssemblyExtractor(
        lambda _value: None,
        lambda _value: None,
        lambda _value: (None, None),
        lambda _document: "Doc",
        10,
    ).extract(SimpleNamespace(Objects=[obj]))
    elements = result.evidence["arrays"][0]["elements"]
    assert elements[0]["visibility"] is False
    assert elements[0]["suppression"]["state"] == "unknown"
    assert elements[1]["visibility"] is True
    assert elements[1]["suppression"] == {"state": "suppressed", "source": "native"}
    assert result.evidence["arrays"][0]["suppression_summary"] == {
        "explicit_suppressed_count": 1,
        "explicit_active_count": 0,
        "unknown_inspected_count": 1,
        "uninspected_count": 0,
    }
    assert any(
        item["code"] == "LINK_ARRAY_SUPPRESSED_GEOMETRY_UNVERIFIED" for item in result.diagnostics
    )
