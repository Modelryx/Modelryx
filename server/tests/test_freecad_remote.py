import hashlib
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from modelryx.core.config import Settings
from modelryx.services.cad_remote import convert_remote

ROLES = {
    "modelryx-preview": ("application/zip", b"preview"),
    "part-metadata": ("application/json", b"metadata"),
    "native-snapshot": ("application/json", b"native"),
}


@pytest.mark.parametrize("system", ["freecad", "Other CAD", "NX"])
@pytest.mark.parametrize("endpoint_setting", ["cad_worker_url", "freecad_worker_url"])
def test_remote_conversion_checks_and_downloads_outputs(
    tmp_path, monkeypatch, system, endpoint_setting
):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "Root.FCStd"
    source.write_bytes(b"root")
    (source_dir / "Child.FCStd").write_bytes(b"child")
    job = SimpleNamespace(id=uuid.uuid4(), attempt_count=1, snapshot_id=uuid.uuid4())
    settings_values = {"cad_worker_url": None, endpoint_setting: "http://worker"}
    settings = Settings(
        **settings_values, internal_worker_key="secret", converter_version="test"
    )
    result = {}

    def handle(request):
        assert request.headers["X-Modelryx-Worker-Key"] == "secret"
        if request.url.path == "/api/v1/capabilities":
            return httpx.Response(
                200,
                json={
                    "contractVersion": "1.0",
                    "cadSystem": system,
                    "documentKinds": ["assembly"],
                    "outputRoles": list(ROLES),
                },
            )
        if request.method == "POST":
            body = request.read()
            assert b"Child.FCStd" in body
            assert b"application/zip" in body
            start = body.index(b'{"contractVersion"')
            end = body.index(b"\r\n--", start)
            envelope = json.loads(body[start:end])
            result.update(
                {
                    **{
                        key: envelope[key]
                        for key in (
                            "contractVersion",
                            "jobId",
                            "attempt",
                            "cadSystem",
                            "configuration",
                        )
                    },
                    "sourceSha256": envelope["source"]["sha256"],
                    "adapterVersion": "test",
                    "status": "complete",
                    "warnings": [],
                    "outputs": [
                        {
                            "role": role,
                            "mediaType": media,
                            "size": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                            "downloadUrl": f"/api/v1/jobs/{envelope['jobId']}/outputs/{role}",
                            "provenance": {"generator": "test", "source_role": "PRIMARY"},
                        }
                        for role, (media, content) in ROLES.items()
                    ],
                }
            )
            return httpx.Response(202, json={})
        if "/outputs/" in request.url.path:
            return httpx.Response(200, content=ROLES[request.url.path.rsplit("/", 1)[-1]][1])
        return httpx.Response(
            200, json={"id": result["jobId"], "state": "ReadyToPublish", "result": result}
        )

    original = httpx.Client
    monkeypatch.setattr(
        "modelryx.services.cad_remote.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    output = tmp_path / "preview.pfg"
    convert_remote(
        source, source_dir, output, settings, job, "assembly", lambda: None, lambda *_: None
    )
    assert output.read_bytes() == b"preview"
    assert output.with_suffix(".cad-raw-metadata.json").read_bytes() == b"native"
    assert output.with_suffix(".part-metadata.json").read_bytes() == b"metadata"


def test_remote_result_rejects_external_download_url():
    from modelryx.services.cad_remote import validate_result

    request = {
        "contractVersion": "1.0",
        "jobId": str(uuid.uuid4()),
        "attempt": 1,
        "cadSystem": "freecad",
        "configuration": None,
        "source": {"sha256": "a" * 64},
        "requiredOutputs": list(ROLES),
    }
    result = {
        **{
            key: request[key]
            for key in ("contractVersion", "jobId", "attempt", "cadSystem", "configuration")
        },
        "sourceSha256": "a" * 64,
        "adapterVersion": "test",
        "status": "complete",
        "warnings": [],
        "outputs": [
            {
                "role": "modelryx-preview",
                "mediaType": "application/zip",
                "size": 1,
                "sha256": "b" * 64,
                "downloadUrl": "https://attacker.invalid/file",
                "provenance": {"generator": "test", "source_role": "PRIMARY"},
            }
        ],
    }
    with pytest.raises(RuntimeError, match="manifest"):
        validate_result(request, result, "ReadyToPublish", Settings(converter_version="test"))


@pytest.mark.parametrize(
    ("state", "version"),
    [("Partial", "test"), ("ReadyToPublish", "different")],
)
def test_remote_result_enforces_server_status_and_version(state, version):
    from modelryx.services.cad_remote import validate_result

    request = {
        "contractVersion": "1.0",
        "jobId": str(uuid.uuid4()),
        "attempt": 1,
        "cadSystem": "freecad",
        "configuration": None,
        "source": {"sha256": "a" * 64},
        "requiredOutputs": list(ROLES),
    }
    result = {
        **{
            key: request[key]
            for key in ("contractVersion", "jobId", "attempt", "cadSystem", "configuration")
        },
        "sourceSha256": "a" * 64,
        "adapterVersion": version,
        "status": "complete",
        "warnings": [],
        "outputs": [
            {
                "role": role,
                "mediaType": media_type,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "downloadUrl": f"/api/v1/jobs/{request['jobId']}/outputs/{role}",
                "provenance": {"generator": "test", "source_role": "PRIMARY"},
            }
            for role, (media_type, content) in ROLES.items()
        ],
    }
    with pytest.raises(RuntimeError, match="status or converter version"):
        validate_result(request, result, state, Settings(converter_version="test"), "test")
