import asyncio

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api import admin
from modelryx.api.main import app

client = TestClient(app)


def test_freecad_settings_survive_partial_update() -> None:
    response = client.put(
        "/api/v1/admin/cad-workers",
        headers=AUTH,
        json={"freecad_url": "http://worker:8090/", "freecad_analyzer_url": "http://worker:8090/"},
    )
    assert response.status_code == 200
    assert response.json()["freecad_url"] == "http://worker:8090"
    assert response.json()["freecad_analyzer_url"] == "http://worker:8090"
    legacy = client.put(
        "/api/v1/admin/cad-workers",
        headers=AUTH,
        json={},
    )
    assert legacy.json()["freecad_url"] == "http://worker:8090"
    assert legacy.json()["freecad_analyzer_url"] == "http://worker:8090"
    client.put(
        "/api/v1/admin/cad-workers",
        headers=AUTH,
        json={"freecad_url": None, "freecad_analyzer_url": None},
    )


def test_freecad_probe_checks_readiness_and_authenticated_capabilities(monkeypatch) -> None:
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "contractVersion": "1.0",
                "cadSystem": "freecad",
                "documentKinds": ["part", "assembly"],
                "dependencyAnalysis": True,
                "sourceExtensions": [".custom"],
                "outputRoles": [
                    "modelryx-preview",
                    "model-preview",
                    "part-metadata",
                    "native-snapshot",
                ],
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **_kwargs: Client())
    result = asyncio.run(admin._probe_freecad_worker("freecad", "http://worker", "secret"))
    assert result.availability == "ONLINE"
    assert calls[0][0] == "http://worker/ready"
    assert calls[1][1]["headers"]["X-Modelryx-Worker-Key"] == "secret"


def test_cad_server_urls_require_authentication() -> None:
    assert client.get("/api/v1/admin/cad-workers").status_code == 401
    assert client.get("/api/v1/admin/cad-workers/status").status_code == 401


def test_freecad_server_urls_reject_credentials_and_non_http_schemes() -> None:
    for url in ("https://user:secret@worker.example", "file:///runtime"):
        response = client.put("/api/v1/admin/cad-workers", headers=AUTH, json={"freecad_url": url})
        assert response.status_code == 422
