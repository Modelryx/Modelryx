"""Exercise the actual bundle subprocess through the authenticated worker API without OCC."""

import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MODELRYX_INTERNAL_WORKER_KEY", "test-worker-key")
os.environ.setdefault("STEP_WORKER_DATA_ROOT", str(Path(tempfile.gettempdir()) / "step-worker-test-jobs"))
import app.main as worker  # noqa: E402


@pytest.mark.parametrize("units", ["millimeter", "unsupported"])
def test_component_bundle_job_converts_or_reports_invalid_units(monkeypatch, tmp_path, units):
    monkeypatch.setattr(worker, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(worker, "COMPONENT_SCRIPT",
                        Path(__file__).resolve().parents[1] / "converter" / "component_bundle_to_glb.py")
    monkeypatch.setattr(worker, "MAX_ATTEMPTS", 1)
    monkeypatch.setattr(worker, "TIMEOUT", 15)
    client = TestClient(worker.app)
    headers = {"X-Modelryx-Worker-Key": worker.WORKER_KEY}
    components = [
        {"file": "shared.stl", "partId": "prototype", "occurrenceId": f"assembly/part-{index}",
         "nodeType": "part", "transformUnits": "millimeter",
         "transform": [1, 0, 0, x, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}
        for index, x in enumerate([-100, 100])
    ]
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"geometryUnits": units, "components": components}))
        archive.writestr("shared.stl", trimesh.creation.box(extents=[100, 20, 10]).export(file_type="stl"))
    submitted = client.post("/api/v1/jobs", headers=headers,
                            files={"source": ("components.zip", bundle.getvalue(), "application/zip")})
    assert submitted.status_code == 202, submitted.text
    status_url = submitted.json()["links"]["self"]
    job = submitted.json()
    terminal = {"READY", "FAILED", "CANCELLED"}
    try:
        deadline = time.monotonic() + 30
        while job["state"] not in terminal and time.monotonic() < deadline:
            time.sleep(0.05)
            job = client.get(status_url, headers=headers).json()
        if units == "unsupported":
            assert job["state"] == "FAILED", job
            assert "unsupported component geometry units" in job["error"]
            assert client.get(job["links"]["glb"], headers=headers).status_code == 404
            return
        assert job["state"] == "READY", job
        downloaded = client.get(job["links"]["glb"], headers=headers)
        assert downloaded.status_code == 200
        data = downloaded.content
        assert len(data) == job["output_bytes"]
        assert hashlib.sha256(data).hexdigest() == job["output_sha256"]
        document = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
        bindings = [node["extras"]["modelryx"] for node in document["nodes"]
                    if "modelryx" in node.get("extras", {})]
        assert {item["occurrenceId"] for item in bindings} == {item["occurrenceId"] for item in components}
        assert all(item["partId"] == "prototype" for item in bindings)
        scene = trimesh.load(io.BytesIO(data), file_type="glb", force="scene")
        np.testing.assert_allclose(scene.bounds, [[-0.15, -0.01, -0.005], [0.15, 0.01, 0.005]], atol=1e-7)
        # The worker key protects both the source upload and generated geometry.
        assert client.get(job["links"]["glb"]).status_code == 401
    finally:
        if job["state"] not in terminal:
            client.post(status_url + "/cancel", headers=headers)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if client.get(status_url, headers=headers).json()["state"] in terminal:
                    break
                time.sleep(0.05)
