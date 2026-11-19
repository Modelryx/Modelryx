import base64
import hashlib
import io
import stat
import time
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4
from zipfile import ZipFile, ZipInfo

import pytest
from fastapi.testclient import TestClient

from standalone.api import create_app
from standalone.conversion import OUTPUTS, ConversionRequest, materialize_source
from standalone.jobs import ACTIVE, JobConflict, JobStore

KEY = {"X-Modelryx-Worker-Key": "test-key"}


class Converter:
    generator = "test-freecad"
    worker_version = "test"

    def __init__(self):
        self.sources = []
        self.entered = Event()
        self.block = False

    def compatibility(self):
        return "COMPATIBLE", None

    def application_version(self):
        return "1.1.3"

    def convert(self, source, preview, *, cancellation_callback, **_kwargs):
        self.entered.set()
        while self.block:
            cancellation_callback()
            time.sleep(0.01)
        self.sources.append((source.read_bytes(), (source.parent / "child.FCStd").read_bytes()))
        with ZipFile(preview, "w") as package:
            package.writestr("model-preview.json", '{"schemaVersion":1,"items":[]}')
            package.writestr("model.glb", b"glTF" + (2).to_bytes(4,"little") + (12).to_bytes(4,"little"))
            package.writestr("diagnostics.json", '{"warnings":[{"message":"Example warning"}]}')
        for role, (name, _, _) in OUTPUTS.items():
            if role not in {"modelryx-preview", "model-preview", "glb", "drawing-pdf"}:
                (preview.parent / name).write_text("{}", encoding="utf-8")

    def export_drawing_pdf(self, source, output, **_kwargs):
        output.write_bytes(b"%PDF-1.4\n%%EOF\n")


def source_package():
    source = io.BytesIO()
    with ZipFile(source, "w") as package:
        package.writestr("Components/Root.FCStd", b"native")
        package.writestr("Components/child.FCStd", b"dependency")
        package.writestr("_modelryx/dependency-report.json", "{}")
    return source.getvalue()


def request_for(source):
    return ConversionRequest(
        contractVersion="1.0",
        jobId=str(uuid4()),
        attempt=1,
        cadSystem="freecad",
        documentKind="assembly",
        source={
            "snapshotId": str(uuid4()),
            "rootPath": "Components/Root.FCStd",
            "size": len(source),
            "sha256": hashlib.sha256(source).hexdigest(),
        },
        requiredOutputs=list(OUTPUTS),
        expiresAt=datetime.now(UTC) + timedelta(minutes=1),
    )


def wait_for_terminal(store, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with store.lock:
            record = store.get(job_id)
        if record["state"] not in ACTIVE:
            return record
        time.sleep(0.01)
    pytest.fail(f"Job did not finish: {record}")


@pytest.mark.parametrize("required", [False, True])
def test_missing_drawing_pdf_respects_required_outputs(tmp_path, required):
    class NoDrawing(Converter):
        def export_drawing_pdf(self, source, output, **_kwargs):
            pass

    source = source_package()
    request = request_for(source)
    if not required:
        request = request.model_copy(update={
            "requiredOutputs": [role for role in request.requiredOutputs if role != "drawing-pdf"],
            "preferredOutputs": ["drawing-pdf"],
        })
    jobs = JobStore(tmp_path, NoDrawing())
    jobs.submit(request, io.BytesIO(source), "application/zip")
    jobs.start()
    try:
        result = wait_for_terminal(jobs, request.jobId)
        if required:
            assert result["state"] == "Failed"
        else:
            assert result["state"] == "Partial"
            assert "FreeCAD document contains no exportable drawing pages." in result["result"]["warnings"]
            assert "drawing-pdf" not in {output["role"] for output in result["result"]["outputs"]}
    finally:
        jobs.close()


def test_recovers_a_job_and_replays_without_reprocessing(tmp_path):
    converter = Converter()
    jobs = JobStore(tmp_path, converter)
    source = source_package()
    job = request_for(source)
    record = jobs.submit(job, io.BytesIO(source), "application/zip")
    jobs.write(record, state="Extracting")
    jobs.start()
    try:
        result = wait_for_terminal(jobs, job.jobId)
        assert result["state"] == "Partial"
        assert result["result"]["warnings"] == ["Example warning"]
        assert {role["role"] for role in result["result"]["outputs"]} == set(OUTPUTS)
        replay = jobs.submit(job, io.BytesIO(source), "application/zip")
        assert replay == result
        assert converter.sources == [(b"native", b"dependency")]
        with pytest.raises(JobConflict):
            jobs.submit(
                job.model_copy(update={"attempt": 2}), io.BytesIO(source), "application/zip"
            )
    finally:
        jobs.close()


def test_active_cancel_never_exposes_outputs(tmp_path):
    converter = Converter()
    converter.block = True
    jobs = JobStore(tmp_path, converter)
    source = source_package()
    job = request_for(source)
    jobs.start()
    try:
        jobs.submit(job, io.BytesIO(source), "application/zip")
        assert converter.entered.wait(5)
        assert jobs.cancel(job.jobId)
        assert wait_for_terminal(jobs, job.jobId)["state"] == "Cancelled"
        assert not any((tmp_path / job.jobId / name).exists() for name, _, _ in OUTPUTS.values())
    finally:
        jobs.close()


@pytest.mark.parametrize(
    "entry", ["../escape.FCStd", "components/child.FCStd", "Components/CON.FCStd"]
)
def test_rejects_unsafe_dependency_paths(tmp_path, entry):
    path = tmp_path / "source.zip"
    with ZipFile(path, "w") as package:
        package.writestr("Components/Root.FCStd", b"native")
        package.writestr(entry, b"unsafe")
    with pytest.raises(ValueError):
        materialize_source(
            path, tmp_path / "input", "Components/Root.FCStd", "application/zip", lambda: None
        )


def test_rejects_symlink_before_extraction(tmp_path):
    path = tmp_path / "source.zip"
    symlink = ZipInfo("Child.FCStd")
    symlink.create_system = 3
    symlink.external_attr = stat.S_IFLNK << 16
    with ZipFile(path, "w") as package:
        package.writestr("Root.FCStd", b"native")
        package.writestr(symlink, b"../secret")
    with pytest.raises(ValueError, match="regular files"):
        materialize_source(path, tmp_path / "input", "Root.FCStd", "application/zip", lambda: None)


def test_worker_http_contract(tmp_path):
    app = create_app(adapter=Converter(), root=tmp_path, worker_key="test-key")
    source = source_package()
    job = request_for(source)
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200
        assert client.get("/api/v1/capabilities").status_code == 401
        assert client.get("/api/v1/capabilities", headers=KEY).json()["cadSystem"] == "freecad"
        files = {
            "request": ("request.json", job.model_dump_json(), "application/json"),
            "source": ("source.zip", source, "application/zip"),
        }
        rejected = {**files, "source": ("source.zip", b"wrong bytes", "application/zip")}
        assert client.post("/api/v1/jobs", files=rejected, headers=KEY).status_code == 400
        submission = client.post("/api/v1/jobs", files=files, headers=KEY)
        assert submission.status_code == 202
        path = submission.headers["location"]
        wait_for_terminal(app.state.store, job.jobId)
        status = client.get(path, headers=KEY).json()
        for manifest in status["result"]["outputs"]:
            assert client.get(manifest["downloadUrl"]).status_code == 401
            output = client.get(manifest["downloadUrl"], headers=KEY)
            assert output.status_code == 200
            assert len(output.content) == manifest["size"]
            assert hashlib.sha256(output.content).hexdigest() == manifest["sha256"]
        assert client.post("/api/v1/jobs", files=files, headers=KEY).json() == status
        assert client.post(f"{path}/cancel", headers=KEY).status_code == 202
        assert client.get(path, headers=KEY).json() == status


def test_standalone_worker_exposes_authenticated_analyzer(tmp_path, monkeypatch):
    import preprocessor_api

    monkeypatch.setenv("MODELRYX_INTERNAL_WORKER_KEY", "test-key")
    monkeypatch.setattr(
        preprocessor_api,
        "_analyze_archive",
        lambda _archive, root: {"root_file": str(root), "missing_references": []},
    )
    app = create_app(adapter=Converter(), root=tmp_path, worker_key="test-key")
    headers = {
        **KEY,
        "X-Modelryx-Root-Path-B64": base64.b64encode(b"Components/Root.FCStd").decode(),
    }
    with TestClient(app) as client:
        assert client.post("/analyze", content=source_package()).status_code == 401
        response = client.post("/analyze", content=source_package(), headers=headers)
        assert response.status_code == 200
        assert response.json()["root_file"] == "Components/Root.FCStd"


def test_generic_external_profile_and_legacy_profile(tmp_path):
    """Generic registration and the embedded server's legacy remote client both work."""
    app = create_app(adapter=Converter(), root=tmp_path, worker_key="test-key")
    source = source_package()
    with TestClient(app) as client:
        capabilities = client.get("/api/v1/capabilities", headers=KEY).json()
        assert capabilities["workerName"] == "FreeCAD"
        assert capabilities["workerVersion"] == "test"
        assert {"native-snapshot", "part-metadata", "model-preview", "glb", "drawing-pdf"} <= set(capabilities["outputRoles"])
        for required, preferred in [
            (["native-snapshot", "part-metadata", "model-preview"], ["glb"]),
            (["native-snapshot", "part-metadata", "modelryx-preview"], []),
        ]:
            job = request_for(source).model_copy(update={"requiredOutputs": required, "preferredOutputs": preferred})
            response = client.post("/api/v1/jobs", headers=KEY, files={
                "request": ("request.json", job.model_dump_json(), "application/json"),
                "source": ("source.zip", source, "application/zip"),
            })
            assert response.status_code == 202
            record = wait_for_terminal(app.state.store, job.jobId)
            assert record["state"] == "Partial"
            outputs = record["result"]["outputs"]
            assert {output["role"] for output in outputs} == set(required + preferred)
            for output in outputs:
                raw = client.get(output["downloadUrl"], headers=KEY).content
                assert hashlib.sha256(raw).hexdigest() == output["sha256"]
                assert output["provenance"]["generator"] == "test-freecad"


@pytest.mark.parametrize("role,payload", [("glb", b"invalid geometry"), ("model-preview", b"[]")])
def test_invalid_generic_preview_is_not_published(tmp_path, role, payload):
    from standalone.jobs import export_preview_members

    archive = tmp_path / "preview.pfg"
    with ZipFile(archive, "w") as package:
        package.writestr(OUTPUTS[role][0], payload)
    with ZipFile(archive) as package, pytest.raises(ValueError):
        export_preview_members(package, tmp_path, {role})


def test_techdraw_document_kind_is_advertised_and_accepted():
    source = source_package()
    request = request_for(source).model_dump(mode="json")
    request["documentKind"] = "drawing"
    assert ConversionRequest.model_validate(request).documentKind == "drawing"
    from standalone.conversion import DOCUMENT_KINDS

    assert "drawing" in DOCUMENT_KINDS
