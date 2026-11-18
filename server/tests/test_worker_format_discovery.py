"""URL-only external CAD servers supply the same neutral format contract as registrations."""

from types import SimpleNamespace

import httpx
import pytest
from modelryx.api.worker_health import external_formats
from modelryx.services import external_workers

CAPABILITIES = {
    "contractVersion": "1.0",
    "cadSystem": "freecad",
    "workerName": "Native CAD service",
    "workerVersion": "42",
    "sourceExtensions": [".FCStd", ".fcnext"],
    "documentKinds": ["part", "assembly"],
    "dependencyAnalysis": True,
    "outputRoles": ["native-snapshot", "part-metadata", "model-preview", "glb"],
}


@pytest.fixture
def discovery(monkeypatch):
    calls = []
    workers = []
    monkeypatch.setattr(external_workers, "registered_workers", lambda _db: workers)

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return httpx.Response(200, json=CAPABILITIES, request=httpx.Request("GET", url))

    monkeypatch.setattr(external_workers.httpx, "Client", lambda **_kwargs: Client())
    settings = SimpleNamespace(
        freecad_worker_url="http://native:8090",
        internal_worker_key="private-worker-key",
    )
    db = SimpleNamespace(get=lambda *_args: None)
    return db, settings, calls, workers


def test_url_only_server_advertises_its_actual_formats_without_internal_defaults(discovery):
    db, settings, calls, _workers = discovery
    formats = external_formats(db, None, settings)
    assert formats == [
        {
            "id": "freecad",
            "cadSystem": "freecad",
            "workerName": "Native CAD service",
            "workerVersion": "42",
            "sourceExtensions": [".fcnext", ".fcstd"],
            "documentKinds": ["assembly", "part"],
            "dependencyAnalysis": True,
        }
    ]
    assert calls == [
        (
            "http://native:8090/api/v1/capabilities",
            {
                "headers": {"X-Modelryx-Worker-Key": "private-worker-key"},
            },
        )
    ]
    assert "url" not in formats[0]


def test_registered_endpoint_is_not_duplicated(discovery):
    db, settings, calls, workers = discovery
    workers.append(
        {
            "id": "custom-id",
            "enabled": True,
            "url": settings.freecad_worker_url,
            "capabilities": external_workers.validate_capabilities(CAPABILITIES),
        }
    )
    assert [w["id"] for w in external_formats(db, None, settings)] == ["custom-id"]
    assert calls == []


def test_explicitly_disabled_environment_url_does_not_advertise_invented_extensions(discovery):
    db, settings, calls, _workers = discovery
    db.get = lambda *_args: SimpleNamespace(value="")
    assert external_formats(db, None, settings) == []
    assert calls == []


@pytest.mark.parametrize("mode", ["offline", "invalid"])
def test_unverifiable_server_does_not_restore_hardcoded_freecad_format(
    discovery, monkeypatch, mode
):
    db, settings, _calls, _workers = discovery

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, url, **_kwargs):
            if mode == "offline":
                raise httpx.ConnectError("unavailable")
            return httpx.Response(
                200, json={"cadSystem": "freecad"}, request=httpx.Request("GET", url)
            )

    monkeypatch.setattr(external_workers.httpx, "Client", lambda **_kwargs: Client())
    assert external_formats(db, None, settings) == []


def test_upload_and_processing_use_the_same_advertised_extension_catalog(discovery):
    db, settings, _calls, _workers = discovery
    worker = external_workers.matching_worker(db, "part.fcnext", "freecad", settings)
    assert worker["id"] == "freecad"
    assert worker["capabilities"]["workerVersion"] == "42"
    assert external_workers.matching_worker(db, "part.notadvertised", "freecad", settings) is None
