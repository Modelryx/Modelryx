"""The first release exposes analysis but no controlled CAD editing endpoints."""

from modelryx.api.main import app


def test_controlled_cad_editing_routes_are_not_published():
    paths = set(app.openapi()["paths"])
    assert "/api/v1/cad/analyze" in paths
    assert not any(
        token in path
        for path in paths
        for token in (
            "cad-sessions", "cad-plans", "cad-executions", "cad-commit-plans", "cad-inspection"
        )
    )


def test_processing_rejects_controlled_editing_before_accessing_native_runtime():
    from types import SimpleNamespace

    import pytest
    from modelryx.services.processing import process_job

    with pytest.raises(RuntimeError, match="Controlled CAD editing is disabled"):
        process_job(None, SimpleNamespace(type="CAD_SESSION_EXECUTE"), None)


def test_freecad_conversion_requires_external_url_before_accessing_source():
    from types import SimpleNamespace

    import pytest
    from modelryx.services.processing import process_job

    with pytest.raises(RuntimeError, match="FreeCAD requires an external worker"):
        process_job(
            None, SimpleNamespace(type="FCSTD_TO_MODELRYX"),
            SimpleNamespace(freecad_worker_url=None),
        )
