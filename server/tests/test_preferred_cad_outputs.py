from modelryx.services.processing import preferred_cad_outputs


def test_external_preferred_outputs_follow_document_kind_and_capabilities():
    supported = ['glb', 'drawing-pdf', 'drawing-representations', 'component-geometry']
    assert preferred_cad_outputs('inventor', 'part', False, supported) == ['glb']
    assert preferred_cad_outputs('inventor', 'drawing', False, supported) == ['glb', 'drawing-pdf']
    assert preferred_cad_outputs('inventor', 'assembly', True, supported) == [
        'glb', 'drawing-representations', 'component-geometry'
    ]
    assert preferred_cad_outputs('inventor', 'drawing', False, ['drawing-pdf']) == ['drawing-pdf']
