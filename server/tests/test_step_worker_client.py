from pathlib import Path
from types import SimpleNamespace

import pytest
from modelryx.services import processing


def _minimal_glb() -> bytes:
    return b"glTF" + (2).to_bytes(4, "little") + (12).to_bytes(4, "little")


def test_step_processing_downloads_complete_worker_output(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "bracket.step"
    source.write_bytes(b"step")
    output = tmp_path / "output"
    requested: list[str] = []
    record = {
        "state": "READY",
        "links": {
            "self": "/api/v1/jobs/job-1",
            "glb": "/api/v1/jobs/job-1/glb",
            "metadata": "/api/v1/jobs/job-1/metadata",
            "preview": "/api/v1/jobs/job-1/preview",
            "raw_metadata": "/api/v1/jobs/job-1/raw-metadata",
        },
        "worker_version": "2.0.0",
    }

    class Response:
        is_success = True
        status_code = 200
        text = ""

        def __init__(self, *, body: bytes = b"", payload=None):
            self.content = body
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, **_kwargs):
            requested.append(url)
            return Response(payload=record)

        def get(self, url, **_kwargs):
            requested.append(url)
            if url.endswith("/glb"):
                return Response(body=_minimal_glb())
            return Response(body=b"{}")

    monkeypatch.setattr(processing.httpx, "Client", Client)
    settings = SimpleNamespace(
        internal_worker_key="worker-key",
        step_worker_url="http://step-worker:8080",
        converter_timeout_seconds=30,
        worker_poll_seconds=0.01,
    )

    result = processing.run_step_processing_job(source, output, settings)

    assert (output / "model.glb").read_bytes() == _minimal_glb()
    assert (output / "part-metadata.json").read_bytes() == b"{}"
    assert (output / "model-preview.json").read_bytes() == b"{}"
    assert (output / "step-raw-metadata.json").read_bytes() == b"{}"
    assert result["worker_version"] == "2.0.0"


def test_cancelled_step_conversion_is_terminal(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "cancelled.step"
    source.write_bytes(b"step")
    record = {
        "state": "CANCELLED",
        "error": "job cancelled by request",
        "links": {
            "self": "/api/v1/jobs/job-1",
            "glb": "/api/v1/jobs/job-1/glb",
            "metadata": "/api/v1/jobs/job-1/metadata",
            "preview": "/api/v1/jobs/job-1/preview",
            "raw_metadata": "/api/v1/jobs/job-1/raw-metadata",
        },
        "worker_version": "2.0.0",
    }

    class Response:
        is_success = True
        status_code = 202
        text = ""

        def json(self):
            return record

        def raise_for_status(self) -> None:
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _url, **_kwargs):
            return Response()

    monkeypatch.setattr(processing.httpx, "Client", Client)
    settings = SimpleNamespace(
        internal_worker_key="worker-key",
        step_worker_url="http://step-worker:8080",
        converter_timeout_seconds=30,
        worker_poll_seconds=0.01,
    )

    with pytest.raises(processing.ProcessingCancelled, match="cancelled"):
        processing.run_step_processing_job(source, tmp_path / "output", settings)
