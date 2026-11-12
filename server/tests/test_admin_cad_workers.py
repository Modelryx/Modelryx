import asyncio
from types import SimpleNamespace

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api import admin
from modelryx.api.main import app

client = TestClient(app)


def test_admin_can_manage_licensed_cad_server_urls() -> None:
    response = client.put(
        "/api/v1/admin/cad-workers",
        headers=AUTH,
        json={
            "autocad_url": "https://autocad.internal.example:5080/",
            "inventor_url": "http://inventor.internal.example:5091/api/",
            "nx_url": "https://nx.internal.example:5092/",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["autocad_url"] == "https://autocad.internal.example:5080"
    assert response.json()["inventor_url"] == "http://inventor.internal.example:5091/api"
    assert response.json()["nx_url"] == "https://nx.internal.example:5092"
    assert client.get("/api/v1/admin/cad-workers", headers=AUTH).json() == response.json()

    cleared = client.put(
        "/api/v1/admin/cad-workers",
        headers=AUTH,
        json={"autocad_url": "", "inventor_url": None, "nx_url": None},
    )
    assert cleared.status_code == 200
    assert all(cleared.json()[name] is None for name in ("autocad_url", "inventor_url", "nx_url"))


def test_freecad_settings_survive_legacy_settings_update() -> None:
    response = client.put(
        "/api/v1/admin/cad-workers", headers=AUTH,
        json={"freecad_url": "http://worker:8090/", "freecad_analyzer_url": "http://worker:8090/"},
    )
    assert response.status_code == 200
    assert response.json()["freecad_url"] == "http://worker:8090"
    assert response.json()["freecad_analyzer_url"] == "http://worker:8090"
    legacy = client.put(
        "/api/v1/admin/cad-workers", headers=AUTH,
        json={"autocad_url": None, "inventor_url": None, "nx_url": None},
    )
    assert legacy.json()["freecad_url"] == "http://worker:8090"
    assert legacy.json()["freecad_analyzer_url"] == "http://worker:8090"
    client.put(
        "/api/v1/admin/cad-workers", headers=AUTH,
        json={"freecad_url": None, "freecad_analyzer_url": None},
    )


def test_freecad_probe_checks_readiness_and_authenticated_capabilities(monkeypatch) -> None:
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"contractVersion": "1.0", "cadSystem": "freecad",
                    "documentKinds": ["part", "assembly"], "dependencyAnalysis": True,
                    "outputRoles": ["modelryx-preview", "part-metadata", "native-snapshot"]}

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


def test_cad_server_urls_reject_credentials_and_non_http_schemes() -> None:
    credentials = client.put(
        "/api/v1/admin/cad-workers",
        headers=AUTH,
        json={
            "autocad_url": "https://user:secret@autocad.example",
            "inventor_url": None,
            "nx_url": None,
        },
    )
    assert credentials.status_code == 422
    invalid_scheme = client.put(
        "/api/v1/admin/cad-workers",
        headers=AUTH,
        json={"autocad_url": None, "inventor_url": "file:///C:/Inventor", "nx_url": None},
    )
    assert invalid_scheme.status_code == 422


def test_cad_server_urls_require_authentication() -> None:
    assert client.get("/api/v1/admin/cad-workers").status_code == 401
    assert client.get("/api/v1/admin/cad-workers/status").status_code == 401


def test_unconfigured_cad_servers_report_not_configured() -> None:
    client.put(
        "/api/v1/admin/cad-workers",
        headers=AUTH,
        json={"autocad_url": None, "inventor_url": None, "nx_url": None},
    )
    response = client.get("/api/v1/admin/cad-workers/status", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["autocad"]["availability"] == "NOT_CONFIGURED"
    assert response.json()["inventor"]["availability"] == "NOT_CONFIGURED"
    assert response.json()["nx"]["availability"] == "NOT_CONFIGURED"
    assert response.json()["nx"]["configuration_source"] == "ADMIN_DISABLED"


def test_cad_worker_configuration_reports_environment_default() -> None:
    class Db:
        def scalars(self, _query):
            return []

    settings = SimpleNamespace(
        autocad_worker_url=None,
        inventor_worker_url=None,
        nx_worker_url="https://nx.internal.example:5092",
    )
    configurations = admin._cad_worker_configurations(Db(), settings)
    assert configurations["autocad_url"] == (None, "NOT_CONFIGURED")
    assert configurations["nx_url"] == (
        "https://nx.internal.example:5092",
        "ENVIRONMENT_DEFAULT",
    )


def test_probe_rejects_reachable_worker_with_wrong_contract(monkeypatch) -> None:
    class Response:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        @property
        def is_success(self) -> bool:
            return 200 <= self.status_code < 300

        def json(self) -> dict:
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str, **_kwargs):
            if url.endswith("/health"):
                return Response(200, {"status": "ok"})
            return Response(
                200,
                {"cadSystem": "inventor", "contractVersions": ["1.0"]},
            )

    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **_kwargs: Client())
    status = asyncio.run(
        admin._probe_cad_worker("autocad", "http://worker.example", "secret")
    )
    assert status.availability == "OFFLINE"
    assert "different CAD system" in (status.detail or "")


def test_probe_accepts_siemens_nx_worker_contract(monkeypatch) -> None:
    class Response:
        def __init__(self, payload: dict, status_code: int = 200):
            self._payload = payload
            self.status_code = status_code

        @property
        def is_success(self) -> bool:
            return True

        def json(self) -> dict:
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str, **_kwargs):
            if url.endswith("/health"):
                return Response({"status": "ok", "cadSystem": "siemens_nx"})
            if url.endswith("/ready"):
                return Response({"status": "ready"})
            return Response({"cadSystem": "siemens_nx", "contractVersions": ["1.0"]})

    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **_kwargs: Client())
    status = asyncio.run(
        admin._probe_cad_worker("siemens_nx", "http://nx.example:5092", "secret")
    )
    assert status.availability == "ONLINE"
    assert status.cad_system == "siemens_nx"
    assert status.processing_readiness == "READY"


def test_probe_reports_unqualified_nx_without_marking_endpoint_offline(monkeypatch) -> None:
    class Response:
        def __init__(self, payload: dict, status_code: int = 200):
            self._payload = payload
            self.status_code = status_code

        @property
        def is_success(self) -> bool:
            return 200 <= self.status_code < 300

        def json(self) -> dict:
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str, **_kwargs):
            if url.endswith("/health"):
                return Response({"status": "ok", "cadSystem": "siemens_nx"})
            if url.endswith("/ready"):
                return Response(
                    {
                        "status": "not_ready",
                        "reasonCode": "NX_NATIVE_PROCESSING_UNQUALIFIED",
                        "reason": "NX native processing has not been qualified on this host.",
                    },
                    503,
                )
            return Response({"cadSystem": "siemens_nx", "contractVersions": ["1.0"]})

    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **_kwargs: Client())
    status = asyncio.run(
        admin._probe_cad_worker("siemens_nx", "http://nx.example:5092", "secret")
    )
    assert status.availability == "ONLINE"
    assert status.processing_readiness == "NOT_READY"
    assert status.readiness_code == "NX_NATIVE_PROCESSING_UNQUALIFIED"
    assert status.readiness_detail == "NX native processing has not been qualified on this host."


def test_probe_requires_authenticated_capabilities(monkeypatch) -> None:
    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url: str, **_kwargs):
            return Response()

    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **_kwargs: Client())
    status = asyncio.run(
        admin._probe_cad_worker("inventor", "http://worker.example", None)
    )
    assert status.availability == "OFFLINE"
    assert "worker key" in (status.detail or "")
