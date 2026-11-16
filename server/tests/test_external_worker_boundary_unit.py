"""Product-independent native routing and package safety checks."""

import uuid
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from modelryx.api.artifact_commits import _native_processing_type
from modelryx.api.previews import _native_processing_type as preview_processing_type
from modelryx.services import external_workers
from modelryx.services.processing import prepare_external_source_upload


@pytest.mark.parametrize("route", [_native_processing_type, preview_processing_type])
def test_native_routing_requires_registration_and_rejects_ambiguity(monkeypatch, route):
    capabilities = external_workers.validate_capabilities(
        {
            "contractVersion": "1.0",
            "cadSystem": "Example CAD",
            "workerName": "Example",
            "workerVersion": "1.0",
            "sourceExtensions": [".part", ".assembly"],
            "documentKinds": ["part", "assembly"],
            "outputRoles": ["native-snapshot", "part-metadata", "model-preview"],
        }
    )
    workers = []
    monkeypatch.setattr(external_workers, "registered_workers", lambda _db: workers)
    assert route("root.part", "Example CAD", object()) is None
    worker = {"id": "example", "enabled": True, "capabilities": capabilities}
    workers.append(worker)
    assert route("root.part", "Example CAD", object()) == external_workers.JOB_TYPE
    assert route("root.assembly", "Example CAD", object()) == external_workers.JOB_TYPE
    worker["enabled"] = False
    assert route("root.part", "Example CAD", object()) is None
    worker["enabled"] = True
    workers.append({**worker, "id": "other"})
    with pytest.raises(HTTPException) as error:
        route("root.part", "Example CAD", object())
    assert error.value.status_code == 409


def test_native_package_preserves_support_files_and_rejects_traversal(tmp_path):
    stored = tmp_path / "stored"
    stored.mkdir()
    (stored / "root").write_bytes(b"root")
    (stored / "support").write_bytes(b"support")
    root = SimpleNamespace(
        id=uuid.uuid4(),
        storage_key="root",
        sha256="root-hash",
        original_filename="root.assembly",
        reference_name="root.assembly",
    )
    support = SimpleNamespace(
        id=uuid.uuid4(),
        storage_key="support",
        sha256="support-hash",
        original_filename="material.dat",
        reference_name="refs/material.dat",
    )
    storage = SimpleNamespace(path_for=lambda key: stored / key)
    package, root_path = prepare_external_source_upload(
        storage, root, [root, support], tmp_path, "example_cad"
    )
    with zipfile.ZipFile(package) as archive:
        assert archive.read(root_path) == b"root"
        assert archive.read("refs/material.dat") == b"support"
    support.reference_name = "../material.dat"
    with pytest.raises(RuntimeError, match="unsafe component path"):
        prepare_external_source_upload(storage, root, [root, support], tmp_path, "example_cad")
