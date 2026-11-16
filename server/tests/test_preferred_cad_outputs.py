from modelryx.services.processing import preferred_cad_outputs


def test_external_preferred_outputs_follow_document_kind_and_capabilities():
    supported = ["glb", "drawing-pdf", "drawing-representations", "component-geometry"]
    assert preferred_cad_outputs("example_cad", "part", False, supported) == ["glb"]
    assert preferred_cad_outputs("example_cad", "drawing", False, supported) == [
        "glb",
        "drawing-pdf",
    ]
    assert preferred_cad_outputs("example_cad", "assembly", True, supported) == [
        "glb",
        "drawing-representations",
        "component-geometry",
    ]
    assert preferred_cad_outputs("example_cad", "drawing", False, ["drawing-pdf"]) == [
        "drawing-pdf"
    ]
