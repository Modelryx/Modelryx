"""FreeCAD selects an external registration or configured external service URL."""

import pytest
from fastapi import HTTPException
from modelryx.api.artifact_commits import _native_processing_type
from modelryx.api.previews import _native_processing_type as preview_processing_type
from modelryx.api.staging import _external_assembly_worker
from modelryx.api.worker_health import external_formats
from modelryx.services import external_workers

CAPABILITIES = external_workers.validate_capabilities(
    {
        "contractVersion": "1.0",
        "cadSystem": "freecad",
        "workerName": "FreeCAD",
        "workerVersion": "1.0",
        "documentKinds": ["part", "assembly"],
        "sourceExtensions": [".FCStd"],
        "outputRoles": [
            "native-snapshot",
            "part-metadata",
            "model-preview",
            "glb",
            "modelryx-preview",
        ],
        "dependencyAnalysis": True,
    }
)


@pytest.mark.parametrize("enabled", [False, True])
def test_freecad_registration_selects_generic_or_external_url_route(monkeypatch, enabled):
    worker = {
        "id": "freecad",
        "url": "https://freecad.example",
        "enabled": enabled,
        "capabilities": CAPABILITIES,
    }
    monkeypatch.setattr(external_workers, "registered_workers", lambda _db: [worker])
    monkeypatch.setattr(
        external_workers, "discover_workers",
        lambda db, _settings: external_workers.registered_workers(db),
    )
    expected = external_workers.JOB_TYPE if enabled else None
    for route in (_native_processing_type, preview_processing_type):
        assert route("Root.FCStd", "FreeCAD", object()) == expected
        assert route("Root.FCStd", "FreeCAD") is None
        assert route("Root.step", "FreeCAD", object()) == "STEP_TO_MODELRYX"
    assert _external_assembly_worker(object(), "Root.FCStd", "FreeCAD") == (
        worker if enabled else None
    )
    from types import SimpleNamespace

    formats = external_formats(
        object(), None, SimpleNamespace(internal_worker_key=None)
    )
    assert bool(formats) == enabled
    if enabled:
        assert formats[0]["sourceExtensions"] == [".fcstd"]
        assert "url" not in formats[0]


def test_freecad_conflicts_do_not_silently_select_another_route(monkeypatch):
    workers = [
        {
            "id": name,
            "url": "https://freecad.example",
            "enabled": True,
            "capabilities": CAPABILITIES,
        }
        for name in ("one", "two")
    ]
    monkeypatch.setattr(external_workers, "registered_workers", lambda _db: workers)
    monkeypatch.setattr(
        external_workers, "discover_workers",
        lambda db, _settings: external_workers.registered_workers(db),
    )
    for route in (_native_processing_type, preview_processing_type, _external_assembly_worker):
        with pytest.raises(HTTPException) as error:
            route_args = (
                (object(), "Root.FCStd", "FreeCAD")
                if route is _external_assembly_worker
                else ("Root.FCStd", "FreeCAD", object())
            )
            route(*route_args)
        assert error.value.status_code == 409


@pytest.mark.parametrize("mode", ["environment", "override", "external"])
@pytest.mark.parametrize("health", ["ready", "offline", "unauthorized", "changed"])
def test_upload_preflight_probes_selected_remote_without_embedded_heartbeat(
    monkeypatch, mode, health
):
    import asyncio
    from types import SimpleNamespace

    import httpx
    from modelryx.api import worker_health
    from modelryx.core.config import get_settings

    calls = []
    capabilities = {**CAPABILITIES, "adapterVersion": "1.0"}
    expected_url = "http://override" if mode == "override" else "http://remote"
    external = {"id": "freecad", "url": expected_url, "capabilities": CAPABILITIES}
    monkeypatch.setattr(
        external_workers, "discover_workers", lambda *_args: [{**external, "enabled": True}]
    )
    monkeypatch.setattr(
        worker_health,
        "_best_heartbeat",
        lambda *_args: pytest.fail("Remote processing must not depend on an embedded heartbeat"),
    )
    settings = get_settings().model_copy(
        update={"internal_worker_key": "test-secret", "freecad_worker_url": "http://remote"}
    )
    db = SimpleNamespace(
        get=lambda *_args: SimpleNamespace(value=expected_url) if mode == "override" else None
    )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, url, *, headers=None):
            calls.append(url)
            assert url.startswith(expected_url + "/")
            status = 503 if health == "offline" else 200
            body = capabilities
            if url.endswith("/api/v1/capabilities"):
                assert headers == {"X-Modelryx-Worker-Key": "test-secret"}
                if health == "unauthorized":
                    status = 401
                elif health == "changed":
                    body = {**capabilities, "cadSystem": "other"}
            return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(worker_health.httpx, "AsyncClient", lambda **_kwargs: Client())
    result = asyncio.run(worker_health.worker_preflight("FREECAD", db, None, settings))
    assert result.available == (health == "ready")
    assert result.compatible == (health == "ready")
    assert result.status == ("IDLE" if health == "ready" else None)
    assert calls[0] == expected_url + "/ready"
    assert "wait for an online worker" not in result.message


def test_upload_preflight_requires_external_service_even_with_heartbeat(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from modelryx.api import worker_health
    from modelryx.core.config import get_settings

    monkeypatch.setattr(external_workers, "discover_workers", lambda *_args: [])
    heartbeat = SimpleNamespace(
        compatibility_state="COMPATIBLE",
        status="BUSY",
        detail=None,
        application_version="1.1.3",
        adapter_version="v1",
        capabilities_json={},
    )
    monkeypatch.setattr(worker_health, "_best_heartbeat", lambda *_args: (heartbeat, True))
    monkeypatch.setattr(
        worker_health.httpx,
        "AsyncClient",
        lambda **_kwargs: pytest.fail("Disabled remote URL must not be probed"),
    )
    settings = get_settings().model_copy(
        update={"freecad_worker_url": "http://environment-default"}
    )
    db = SimpleNamespace(get=lambda *_args: SimpleNamespace(value=""))
    result = asyncio.run(worker_health.worker_preflight("FREECAD", db, None, settings))
    assert not result.available and not result.compatible
    assert result.status is None
