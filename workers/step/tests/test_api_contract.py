import json
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MODELRYX_INTERNAL_WORKER_KEY", "test-worker-key")
os.environ.setdefault(
    "STEP_WORKER_DATA_ROOT", str(Path(tempfile.gettempdir()) / "step-worker-test-jobs")
)

import app.main as worker  # noqa: E402
from app.main import app, job_dir, write_job  # noqa: E402


client = TestClient(app)
AUTH_HEADER = {"X-Modelryx-Worker-Key": worker.WORKER_KEY}


def test_capabilities_endpoint_reports_contract_surface(monkeypatch) -> None:
    converter = Path(__file__).resolve().parents[1] / "converter"
    monkeypatch.setattr(worker, "SCRIPT", converter / "step_to_glb.py")
    monkeypatch.setattr(worker, "COMPONENT_SCRIPT", converter / "component_bundle_to_glb.py")
    response = client.get("/capabilities", headers=AUTH_HEADER)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["worker_type"] == "STEP"
    assert payload["capabilities"]["step_processing"] is True
    assert payload["capabilities"]["component_bundle_input_units"] == ["millimeter"]
    assert "1.0" in payload["contract_versions"]
    assert ".step" in payload["source_extensions"]
    assert payload["endpoints"]["cancel_job"] == "/api/v1/jobs/{jobId}/cancel"
    assert payload["endpoints"]["preview"] == "/api/v1/jobs/{jobId}/preview"
    assert payload["endpoints"]["raw_metadata"] == "/api/v1/jobs/{jobId}/raw-metadata"


def test_missing_component_processor_does_not_advertise_unit_support(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(worker, "COMPONENT_SCRIPT", tmp_path / "missing-converter.py")
    payload = client.get("/capabilities", headers=AUTH_HEADER).json()
    assert payload["capabilities"]["component_bundle_to_glb"] is False
    assert payload["capabilities"]["component_bundle_input_units"] == []
    assert worker.heartbeat_payload()["capabilities"]["component_bundle_input_units"] == []


def test_prepare_step_outputs_uses_original_filename(tmp_path: Path) -> None:
    metadata = {
        "schema": "modelryx.part-metadata",
        "identity": {"name": "model", "kind": "part"},
    }
    (tmp_path / "part-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    worker.prepare_step_outputs(tmp_path, "Front Hub Carrier.step")

    normalized = json.loads((tmp_path / "part-metadata.json").read_text(encoding="utf-8"))
    preview = json.loads((tmp_path / "model-preview.json").read_text(encoding="utf-8"))
    assert normalized["identity"]["name"] == "Front Hub Carrier"
    assert preview["items"][0]["source"]["filename"] == "Front Hub Carrier.step"
    assert (tmp_path / "step-raw-metadata.json").is_file()


def test_cancel_queued_job_marks_terminal_cancelled_state() -> None:
    job_id = str(uuid.uuid4())
    directory = job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "id": job_id,
        "state": "QUEUED",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "input_name": "model.step",
        "input_bytes": 10,
        "input_sha256": "0" * 64,
        "error": None,
        "cancel_reason": None,
    }
    write_job(directory, record)

    response = client.post(
        f"/api/v1/jobs/{job_id}/cancel?reason=user-requested",
        headers=AUTH_HEADER,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "CANCELLED"
    assert payload["cancel_reason"] == "user-requested"
    assert payload["links"]["cancel"].endswith("/cancel")

    persisted = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    assert persisted["state"] == "CANCELLED"


def test_processing_cancellation_cannot_be_overwritten(monkeypatch) -> None:
    job_id = str(uuid.uuid4())
    directory = job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model.step").write_bytes(b"step")
    write_job(
        directory,
        {
            "id": job_id,
            "state": "QUEUED",
            "created_at": "2026-01-01T00:00:00Z",
            "input_name": "model.step",
            "input_bytes": 4,
            "input_sha256": "0" * 64,
            "error": None,
            "cancel_reason": None,
        },
    )
    started = threading.Event()
    pause = threading.Event()

    def wait_for_cancel(*_args):
        started.set()
        while not worker.is_cancel_requested(job_id):
            pause.wait(0.01)
        raise worker.JobCancelled("job cancelled by request")

    monkeypatch.setattr(worker, "run_converter_process", wait_for_cancel)
    thread = threading.Thread(target=worker.convert, args=(job_id,))
    thread.start()
    assert started.wait(5)

    response = client.post(
        f"/api/v1/jobs/{job_id}/cancel?reason=race-test",
        headers=AUTH_HEADER,
    )
    thread.join(timeout=5)

    assert response.status_code == 200
    assert not thread.is_alive()
    persisted = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    assert persisted["state"] == "CANCELLED"
    assert persisted["cancel_reason"] == "race-test"


def test_converter_process_drains_large_output(monkeypatch, tmp_path: Path) -> None:
    script = tmp_path / "verbose_converter.py"
    script.write_text("print('x' * 250000)\n", encoding="utf-8")
    source = tmp_path / "model.step"
    source.write_bytes(b"step")
    monkeypatch.setattr(worker, "SCRIPT", script)
    monkeypatch.setattr(worker, "TIMEOUT", 5)

    stdout, _stderr = worker.run_converter_process("verbose-job", tmp_path, source)

    assert len(stdout.strip()) == 250000
