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


def test_native_conversion_requires_matching_worker(monkeypatch):
    from modelryx.services import cad_routing, external_workers

    monkeypatch.setattr(external_workers, "discover_workers", lambda *_: [])
    assert cad_routing.processing_type_for_source("source.FCStd", "FreeCAD", object()) is None
