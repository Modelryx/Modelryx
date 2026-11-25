from modelryx.services.processing import preferred_cad_outputs


def test_external_preferred_outputs_follow_document_kind_and_capabilities():
    supported = ["glb", "drawing-pdf", "drawing-representations", "component-geometry"]
    assert preferred_cad_outputs("part", False, supported) == ["glb"]
    assert preferred_cad_outputs("drawing", False, supported) == [
        "glb",
        "drawing-pdf",
    ]
    assert preferred_cad_outputs("assembly", True, supported) == ["glb"]
    assert preferred_cad_outputs("drawing", False, ["drawing-pdf"]) == [
        "drawing-pdf"
    ]


def test_unpublished_bundle_roles_are_never_requested():
    bundles = ["drawing-representations", "component-geometry"]
    assert preferred_cad_outputs("assembly", True, bundles) == []
    assert preferred_cad_outputs("assembly", True) == ["glb"]


def test_worker_outputs_follow_declared_publication_profile():
    roles = ["glb", "drawing-pdf", "drawing-representations", "component-geometry", "manufacturing-outputs"]
    profile = {
        "preferredOutputs": {"drawing": ["drawing-pdf"], "assembly": ["glb", "drawing-representations", "component-geometry"], "part": ["glb", "manufacturing-outputs"]},
        "outputContracts": {"component-geometry": "component-geometry-step-v1", "drawing-representations": "related-drawing-v1", "manufacturing-outputs": "manufacturing-outputs-v1"},
    }
    assert preferred_cad_outputs("drawing", False, roles, profile) == ["drawing-pdf"]
    assert preferred_cad_outputs("assembly", True, roles, profile) == ["glb", "drawing-representations", "component-geometry"]
    assert preferred_cad_outputs("part", False, roles, profile) == ["glb", "manufacturing-outputs"]
    assert preferred_cad_outputs("assembly", True, ["glb"], profile) == ["glb"]
