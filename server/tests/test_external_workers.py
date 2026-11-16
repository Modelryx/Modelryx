import base64
import hashlib
import json
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from artifact_helpers import commit_item
from auth_helpers import AUTH
from fastapi import HTTPException
from fastapi.testclient import TestClient
from modelryx.api import admin
from modelryx.api.artifact_commits import _native_processing_type
from modelryx.api.main import app
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import (
    ApplicationSetting,
    Artifact,
    ArtifactFile,
    BomLine,
    BomViewRevision,
    Item,
    ProcessingJob,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    User,
)
from modelryx.services import processing
from modelryx.services.bom_cycles import ensure_no_bom_cycle, unresolved_native_occurrences
from modelryx.services.external_workers import (
    JOB_TYPE,
    AmbiguousWorkerError,
    matching_worker,
    validate_capabilities,
)
from sqlalchemy import select

client = TestClient(app)
CAPABILITIES = {
    "workerName": "Example CAD",
    "workerVersion": "2.1.0",
    "contractVersion": "1.0",
    "cadSystem": "example_cad",
    "documentKinds": ["part"],
    "sourceExtensions": [".GENERIC", ".FCStd"],
    "outputRoles": ["native-snapshot", "part-metadata", "model-preview"],
}


class CapabilityClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, *, headers):
        assert headers["X-Modelryx-Worker-Key"]
        return httpx.Response(200, json=CAPABILITIES, request=httpx.Request("GET", url))


def test_external_worker_availability_tracks_registration_and_readiness(monkeypatch):
    status = {"offline": False, "changed": False, "calls": 0}

    class Probe(CapabilityClient):
        async def get(self, url, *, headers=None):
            status["calls"] += 1
            if status["offline"]:
                raise httpx.ConnectError("unavailable")
            if url.endswith("/ready"):
                return httpx.Response(
                    200, json={"status": "ready"}, request=httpx.Request("GET", url)
                )
            assert headers["X-Modelryx-Worker-Key"]
            capabilities = {**CAPABILITIES}
            if status["changed"]:
                capabilities["outputRoles"] = [*CAPABILITIES["outputRoles"], "glb"]
            return httpx.Response(200, json=capabilities, request=httpx.Request("GET", url))

    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **_kwargs: Probe())
    worker_id = "status_" + uuid.uuid4().hex
    assert client.get("/api/v1/admin/external-workers/status").status_code == 401
    registered = client.put(
        f"/api/v1/admin/external-workers/{worker_id}",
        headers=AUTH,
        json={"url": "https://worker.example", "enabled": True},
    )
    assert registered.status_code == 200, registered.text

    def current():
        response = client.get("/api/v1/admin/external-workers/status", headers=AUTH)
        assert response.status_code == 200, response.text
        return next(row for row in response.json() if row["id"] == worker_id)

    try:
        assert current()["availability"] == "ONLINE"
        status["changed"] = True
        assert current()["availability"] == "CHANGED"
        status["offline"] = True
        assert current()["availability"] == "OFFLINE"
        disabled = client.put(
            f"/api/v1/admin/external-workers/{worker_id}",
            headers=AUTH,
            json={"url": "https://worker.example", "enabled": False},
        )
        assert disabled.status_code == 200, disabled.text
        previous = status["calls"]
        assert current()["availability"] == "DISABLED"
        assert status["calls"] == previous
    finally:
        client.delete(f"/api/v1/admin/external-workers/{worker_id}", headers=AUTH)


def test_rejects_indirect_subassembly_cycle():
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        revisions = []
        for index in range(3):
            item = Item(
                part_number=f"CYCLE-{index}-{uuid.uuid4().hex}",
                name="Assembly",
                part_type="ASSEMBLY",
                created_by=user.id,
            )
            db.add(item)
            db.flush()
            revision = Revision(
                part_id=item.id,
                revision_code="A",
                cad_system="example_cad",
                native_format="GENERIC",
                metadata_json={},
                created_by=user.id,
            )
            db.add(revision)
            db.flush()
            snapshot = RevisionContentSnapshot(
                revision_id=revision.id, sequence=1, commit_id=uuid.uuid4(), created_by=user.id
            )
            db.add(snapshot)
            db.flush()
            view = BomViewRevision(
                revision_id=revision.id, snapshot_id=snapshot.id, sequence=1, created_by=user.id
            )
            db.add(view)
            db.flush()
            revisions.append((revision, view))
        for index in range(2):
            db.add(
                BomLine(
                    bom_view_revision_id=revisions[index][1].id,
                    parent_revision_id=revisions[index][0].id,
                    child_revision_id=revisions[index + 1][0].id,
                    occurrence_key=f"child-{index}",
                )
            )
        db.flush()
        ensure_no_bom_cycle(db, revisions[0][0].id, {revisions[1][0].id})
        occurrences = [
            {"occurrence_id": "child-0", "parent_occurrence_id": None},
            {"occurrence_id": "child-1", "parent_occurrence_id": "child-0"},
        ]
        assert not unresolved_native_occurrences(db, revisions[0][0].id, occurrences)
        occurrences.append({"occurrence_id": "missing", "parent_occurrence_id": "child-0"})
        assert unresolved_native_occurrences(db, revisions[0][0].id, occurrences)
        with pytest.raises(HTTPException, match="cycle"):
            ensure_no_bom_cycle(db, revisions[2][0].id, {revisions[0][0].id})
        db.rollback()


def test_admin_registers_and_removes_external_worker(monkeypatch):
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **_kwargs: CapabilityClient())
    worker_id = "example_" + uuid.uuid4().hex
    assert client.get("/api/v1/admin/external-workers").status_code == 401
    response = client.put(
        f"/api/v1/admin/external-workers/{worker_id}",
        headers=AUTH,
        json={"url": "https://worker.example", "enabled": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["capabilities"]["sourceExtensions"] == [".fcstd", ".generic"]
    try:
        assert any(
            w["id"] == worker_id
            for w in client.get("/api/v1/admin/external-workers", headers=AUTH).json()
        )
        with SessionLocal() as db:
            assert matching_worker(db, "file.GENERIC", "Unknown")["id"] == worker_id
            assert _native_processing_type("file.generic", "Unknown", db) == JOB_TYPE
        formats = client.get("/api/v1/workers/external-formats", headers=AUTH).json()
        registered_format = next(w for w in formats if w["id"] == worker_id)
        assert registered_format["workerName"] == "Example CAD"
        assert registered_format["workerVersion"] == "2.1.0"
        assert any(w["id"] == worker_id and "url" not in w for w in formats)
        assert ".fcstd" in registered_format["sourceExtensions"]

        async def offline(*_args, **_kwargs):
            raise AssertionError("Disabling must work when the worker is offline")

        monkeypatch.setattr(CapabilityClient, "get", offline)
        disabled = client.put(
            f"/api/v1/admin/external-workers/{worker_id}",
            headers=AUTH,
            json={"url": "https://worker.example", "enabled": False},
        )
        assert disabled.status_code == 200
        assert not any(
            w["id"] == worker_id
            for w in client.get("/api/v1/workers/external-formats", headers=AUTH).json()
        )
    finally:
        assert (
            client.delete(f"/api/v1/admin/external-workers/{worker_id}", headers=AUTH).status_code
            == 204
        )


def test_external_dependency_analysis_is_authenticated_and_capability_checked(monkeypatch):
    capabilities = {**CAPABILITIES, "documentKinds": ["assembly"], "dependencyAnalysis": True}
    calls = []

    class Analyzer(CapabilityClient):
        async def get(self, url, *, headers):
            calls.append(("capabilities", headers["X-Modelryx-Worker-Key"]))
            return httpx.Response(200, json=capabilities, request=httpx.Request("GET", url))

        async def post(self, url, *, headers, content):
            calls.append(("analyze", headers["X-Modelryx-Worker-Key"]))
            assert content == b"archive"
            assert base64.b64decode(headers["X-Modelryx-Root-Path-B64"]) == b"root.generic"
            return httpx.Response(
                200, json={"document_type": "ASSEMBLY"}, request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **_kwargs: Analyzer())
    worker_id = "analyzer_" + uuid.uuid4().hex
    response = client.put(
        f"/api/v1/admin/external-workers/{worker_id}",
        headers=AUTH,
        json={"url": "https://worker.example", "enabled": True},
    )
    assert response.status_code == 200, response.text
    try:
        response = client.post(
            f"/api/v1/cad/analyze?worker_id={worker_id}",
            headers={
                **AUTH,
                "X-Modelryx-Root-Path-B64": base64.b64encode(b"root.generic").decode(),
            },
            content=b"archive",
        )
        assert response.status_code == 200, response.text
        assert [call[0] for call in calls] == ["capabilities", "capabilities", "analyze"]
        assert all(call[1] for call in calls)
        capabilities["outputRoles"] = [*CAPABILITIES["outputRoles"], "glb"]
        changed = client.post(
            f"/api/v1/cad/analyze?worker_id={worker_id}",
            headers={
                **AUTH,
                "X-Modelryx-Root-Path-B64": base64.b64encode(b"root.generic").decode(),
            },
            content=b"archive",
        )
        assert changed.status_code == 409, changed.text
        assert [call[0] for call in calls].count("analyze") == 1
    finally:
        client.delete(f"/api/v1/admin/external-workers/{worker_id}", headers=AUTH)


def test_ambiguous_workers_never_select_an_arbitrary_endpoint():
    suffix = uuid.uuid4().hex
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        keys = [f"external_worker.{suffix}_{number}" for number in range(2)]
        for key in keys:
            db.add(
                ApplicationSetting(
                    key=key,
                    value=json.dumps(
                        {
                            "url": "https://worker.example",
                            "enabled": True,
                            "capabilities": validate_capabilities(CAPABILITIES),
                        }
                    ),
                    updated_by=user.id,
                )
            )
        db.commit()
        try:
            with pytest.raises(AmbiguousWorkerError):
                matching_worker(db, "part.generic", "Unknown")
        finally:
            for key in keys:
                db.delete(db.get(ApplicationSetting, key))
            db.commit()


def test_staged_external_file_queues_generic_job(monkeypatch):
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **_kwargs: CapabilityClient())
    worker_id = "staging_" + uuid.uuid4().hex
    response = client.put(
        f"/api/v1/admin/external-workers/{worker_id}",
        headers=AUTH,
        json={"url": "https://worker.example", "enabled": True},
    )
    assert response.status_code == 200, response.text
    try:
        item_number = "EXT-" + uuid.uuid4().hex
        committed = commit_item(
            client,
            item_number=item_number,
            name="External source",
            files=[("part.generic", b"external source")],
            cad_system="EXAMPLE_CAD",
            native_format="GENERIC",
        )
        with SessionLocal() as db:
            assert db.scalar(
                select(ProcessingJob)
                .join(Revision, ProcessingJob.revision_id == Revision.id)
                .join(Item, Revision.part_id == Item.id)
                .where(ProcessingJob.type == JOB_TYPE, Item.part_number == item_number)
            )
            revision = db.get(Revision, uuid.UUID(committed["revision_id"]))
            revision.state = "READY"
            db.commit()
        copied = client.post(
            f"/api/v1/items/{committed['item_id']}/revisions/copy",
            headers=AUTH,
            json={"source_revision_id": committed["revision_id"], "revision_code": "B"},
        )
        assert copied.status_code == 201, copied.text
        assert copied.json()["state"] == "PROCESSING"
        with SessionLocal() as db:
            assert db.scalar(
                select(ProcessingJob).where(
                    ProcessingJob.revision_id == uuid.UUID(copied.json()["id"]),
                    ProcessingJob.type == JOB_TYPE,
                )
            )
    finally:
        client.delete(f"/api/v1/admin/external-workers/{worker_id}", headers=AUTH)


def test_external_assembly_package_staging(monkeypatch):
    capabilities = {**CAPABILITIES, "documentKinds": ["assembly"]}

    async def get_capabilities(self, url, *, headers):
        return httpx.Response(200, json=capabilities, request=httpx.Request("GET", url))

    monkeypatch.setattr(CapabilityClient, "get", get_capabilities)
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **_kwargs: CapabilityClient())
    worker_id = "assembly_" + uuid.uuid4().hex
    response = client.put(
        f"/api/v1/admin/external-workers/{worker_id}",
        headers=AUTH,
        json={"url": "https://worker.example", "enabled": True},
    )
    assert response.status_code == 200, response.text
    try:
        files = [("assembly/root.generic", b"assembly"), ("assembly/support/data.dat", b"data")]
        response = client.post(
            "/api/v1/staging",
            headers=AUTH,
            json={
                "client_staging_id": str(uuid.uuid4()),
                "files": [
                    {
                        "logical_path": path,
                        "filename": path.rsplit("/", 1)[-1],
                        "mime_type": "application/octet-stream",
                        "byte_size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                    for path, data in files
                ],
            },
        )
        assert response.status_code == 201, response.text
        stage = response.json()
        staged = {entry["logical_path"]: entry for entry in stage["files"]}
        proposal = {
            "expected_snapshot_version": stage["snapshot_version"],
            "operation": "CREATE_ITEM",
            "item": {
                "item_number": "EXT-" + uuid.uuid4().hex,
                "name": "Assembly",
                "item_type": "ASSEMBLY",
            },
            "revision": {
                "revision_code": "A",
                "cad_system": "example_cad",
                "native_format": "GENERIC",
            },
            "artifacts": [
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "artifact_type": "CAD_ASSEMBLY",
                    "name": "Assembly",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": staged[path]["id"],
                            "reference_name": path,
                            "role": "PRIMARY" if index == 0 else "SECONDARY",
                        }
                        for index, (path, _) in enumerate(files)
                    ],
                }
            ],
            "identity_decisions": [
                {"staged_file_id": staged[files[0][0]]["id"], "decision": "CREATE_NEW"}
            ],
            "bom": [],
        }
        response = client.put(
            f"/api/v1/staging/{stage['id']}/proposal", headers=AUTH, json=proposal
        )
        assert response.status_code == 200, response.text
        for path, data in files:
            response = client.put(
                staged[path]["upload_url"],
                headers={**AUTH, "Content-Type": "application/octet-stream"},
                content=data,
            )
            assert response.status_code == 200, response.text
        response = client.post(f"/api/v1/staging/{stage['id']}/validate", headers=AUTH)
        assert response.status_code == 200, response.text
        response = client.post(
            f"/api/v1/staging/{stage['id']}/ready",
            headers=AUTH,
            json={"expected_snapshot_version": stage["snapshot_version"] + 1},
        )
        assert response.status_code == 200, response.text
        response = client.post(
            f"/api/v1/uploads/{stage['id']}/commit",
            headers=AUTH,
            json={
                "snapshot_version": response.json()["snapshot_version"],
                "commit_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 200, response.text
        assembly = response.json()
        with SessionLocal() as db:
            assert db.scalar(
                select(ProcessingJob)
                .join(Revision, ProcessingJob.revision_id == Revision.id)
                .join(Item, Revision.part_id == Item.id)
                .where(
                    ProcessingJob.type == JOB_TYPE,
                    Item.part_number == proposal["item"]["item_number"],
                )
            )
        part = commit_item(
            client,
            item_number="PART-" + uuid.uuid4().hex,
            name="Managed part",
            files=[("part.fcstd", b"managed part")],
        )
        with SessionLocal() as db:
            db.get(Revision, uuid.UUID(assembly["revision_id"])).state = "READY_WITH_WARNINGS"
            db.get(Revision, uuid.UUID(part["revision_id"])).state = "READY"
            db.commit()
        edit = client.post(
            f"/api/v1/staging/items/{assembly['item_id']}/revisions/{assembly['revision_id']}/edit",
            headers=AUTH,
            json={"client_staging_id": str(uuid.uuid4())},
        )
        assert edit.status_code == 201, edit.text
        edit_stage = edit.json()
        edit_proposal = {
            **edit_stage["proposal"],
            "expected_snapshot_version": edit_stage["snapshot_version"],
        }
        edit_proposal["bom"] = [
            {
                "parent_revision_id": assembly["revision_id"],
                "child_revision_id": part["revision_id"],
                "instance_name": "occurrence:1",
                "quantity": 1,
                "properties": {"native_placement": "unavailable"},
            }
        ]
        updated = client.put(
            f"/api/v1/staging/{edit_stage['id']}/proposal", headers=AUTH, json=edit_proposal
        )
        assert updated.status_code == 200, updated.text
        validated = client.post(f"/api/v1/staging/{edit_stage['id']}/validate", headers=AUTH)
        assert validated.status_code == 200, validated.text
        ready = client.post(
            f"/api/v1/staging/{edit_stage['id']}/ready",
            headers=AUTH,
            json={"expected_snapshot_version": updated.json()["snapshot_version"]},
        )
        assert ready.status_code == 200, ready.text
        committed = client.post(
            f"/api/v1/uploads/{edit_stage['id']}/commit",
            headers=AUTH,
            json={
                "snapshot_version": ready.json()["snapshot_version"],
                "commit_id": str(uuid.uuid4()),
            },
        )
        assert committed.status_code == 200, committed.text
        with SessionLocal() as db:
            assert db.scalar(
                select(ProcessingJob).where(
                    ProcessingJob.snapshot_id == uuid.UUID(committed.json()["content_snapshot_id"]),
                    ProcessingJob.type == JOB_TYPE,
                )
            )
            db.get(Revision, uuid.UUID(assembly["revision_id"])).state = "READY_WITH_WARNINGS"
            db.commit()
        edit = client.post(
            f"/api/v1/staging/items/{assembly['item_id']}/revisions/{assembly['revision_id']}/edit",
            headers=AUTH,
            json={"client_staging_id": str(uuid.uuid4())},
        )
        assert edit.status_code == 201, edit.text
        edit_stage = edit.json()
        package = [
            ("model/root.generic", b"updated assembly"),
            ("model/part.generic", b"new component"),
        ]
        manifest = [
            {
                "logical_path": path,
                "filename": path.rsplit("/", 1)[-1],
                "byte_size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for path, data in package
        ]
        report = {
            "document_type": "ASSEMBLY",
            "root_logical_path": package[0][0],
            "files": [
                {
                    "logical_path": path,
                    "is_root": index == 0,
                    "document_type": "ASSEMBLY" if index == 0 else "PART",
                }
                for index, (path, _) in enumerate(package)
            ],
            "missing_references": [],
            "references": [
                {
                    "parent_logical_path": package[0][0],
                    "child_logical_path": package[1][0],
                    "instance_name": name,
                }
                for name in ("new-one", "new-two")
            ],
        }
        analyzed = client.put(
            f"/api/v1/staging/{edit_stage['id']}/assembly-analysis",
            headers=AUTH,
            json={
                "expected_snapshot_version": edit_stage["snapshot_version"],
                "report": report,
                "files": manifest,
            },
        )
        assert analyzed.status_code == 200, analyzed.text
        current = analyzed.json()
        identity = {
            "item_number": "NEW-" + uuid.uuid4().hex,
            "name": "New component",
            "item_type": "PART",
        }
        for occurrence in ("new-one", "new-two"):
            decided = client.put(
                f"/api/v1/staging/{edit_stage['id']}/assembly-identity",
                headers=AUTH,
                json={
                    "expected_snapshot_version": current["snapshot_version"],
                    "parent_logical_path": package[0][0],
                    "occurrence_key": occurrence,
                    "decision": "CREATE_NEW",
                    "item": identity,
                    "revision": {
                        "revision_code": "A",
                        "cad_system": "example_cad",
                        "native_format": "GENERIC",
                    },
                },
            )
            assert decided.status_code == 200, decided.text
            current = decided.json()
        for expected, (_, data) in zip(manifest, package, strict=True):
            added = client.post(
                f"/api/v1/staging/{edit_stage['id']}/files",
                headers=AUTH,
                json={
                    **expected,
                    "mime_type": "application/octet-stream",
                    "expected_snapshot_version": current["snapshot_version"],
                },
            )
            assert added.status_code == 201, added.text
            current = added.json()
            entry = next(
                row for row in current["files"] if row["logical_path"] == expected["logical_path"]
            )
            uploaded = client.put(
                entry["upload_url"],
                headers={**AUTH, "Content-Type": "application/octet-stream"},
                content=data,
            )
            assert uploaded.status_code == 200, uploaded.text
            current = uploaded.json()
        staged = client.post(
            f"/api/v1/staging/{edit_stage['id']}/assembly-reconciliation/stage",
            headers=AUTH,
            json={"expected_snapshot_version": current["snapshot_version"]},
        )
        assert staged.status_code == 200, staged.text
        assert len(staged.json()["proposal"]["identity_decisions"]) == 1
        assert len(staged.json()["proposal"]["bom"]) == 2
        validated = client.post(f"/api/v1/staging/{edit_stage['id']}/validate", headers=AUTH)
        assert validated.status_code == 200, validated.text
        ready = client.post(
            f"/api/v1/staging/{edit_stage['id']}/ready",
            headers=AUTH,
            json={"expected_snapshot_version": staged.json()["snapshot_version"]},
        )
        assert ready.status_code == 200, ready.text
        result = client.post(
            f"/api/v1/uploads/{edit_stage['id']}/commit",
            headers=AUTH,
            json={
                "snapshot_version": ready.json()["snapshot_version"],
                "commit_id": str(uuid.uuid4()),
            },
        )
        assert result.status_code == 200, result.text
        assert len(result.json()["created_item_ids"]) == 1
        with SessionLocal() as db:
            lines = list(
                db.scalars(
                    select(BomLine).where(
                        BomLine.bom_view_revision_id
                        == uuid.UUID(result.json()["bom_view_revision_id"])
                    )
                )
            )
            assert len(lines) == 2 and lines[0].child_revision_id == lines[1].child_revision_id
    finally:
        client.delete(f"/api/v1/admin/external-workers/{worker_id}", headers=AUTH)


@pytest.mark.parametrize("tamper,package", [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("system", ["example_cad", "freecad"])
def test_external_worker_dispatch_validates_and_publishes(
    monkeypatch, tmp_path, tamper, package, system
):
    native = b"independent-worker-source"
    capabilities = {
        **CAPABILITIES, "cadSystem": system,
        "documentKinds": ["assembly"] if package else ["part"],
    }
    metadata = (
        Path(__file__).parent / "fixtures/external-worker/assembly.part-metadata.json"
    ).read_bytes()
    preview = json.dumps(
        {
            "schemaVersion": 1,
            "rootOccurrenceId": "root",
            "items": [
                {"occurrenceId": "root", "partId": "root", "name": "Root", "nodeType": "assembly"}
            ],
        }
    ).encode()
    payloads = {"native-snapshot": b"{}", "part-metadata": metadata, "model-preview": preview}
    observed = {"downloads": 0}

    class Worker:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            body = capabilities if url.endswith("/capabilities") else {"status": "ready"}
            return httpx.Response(200, json=body, request=httpx.Request("GET", url))

        def post(self, url, *, headers, files):
            request = json.load(files["request"][1])
            observed["request"] = request
            if package:
                assert request["documentKind"] == "assembly"
                assert files["source"][2] == "application/zip"
                with zipfile.ZipFile(files["source"][1]) as archive:
                    assert archive.read(request["source"]["rootPath"]) == native
                    assert archive.read("references/material.dat") == b"support data"
            else:
                assert files["source"][1].read() == native
            outputs = []
            for role, data in payloads.items():
                outputs.append(
                    {
                        "role": role,
                        "mediaType": "application/json",
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "downloadUrl": f"/api/v1/jobs/{request['jobId']}/outputs/{role}",
                        "provenance": {
                            "generator": "example-worker",
                            "source_role": "native-snapshot",
                        },
                    }
                )
            if tamper:
                outputs[0]["downloadUrl"] = "https://attacker.example/secret"
            result = {
                **{
                    key: request[key]
                    for key in ("contractVersion", "jobId", "attempt", "cadSystem", "configuration")
                },
                "sourceSha256": request["source"]["sha256"],
                "adapterVersion": "1.0",
                "status": "complete",
                "warnings": [],
                "outputs": outputs,
            }
            return httpx.Response(
                200,
                json={"state": "ReadyToPublish", "result": result},
                request=httpx.Request("POST", url),
            )

        @contextmanager
        def stream(self, _method, url, *, headers):
            observed["downloads"] += 1
            yield httpx.Response(
                200, content=payloads[url.rsplit("/", 1)[1]], request=httpx.Request("GET", url)
            )

    monkeypatch.setattr(processing.httpx, "Client", lambda **_kwargs: Worker())
    monkeypatch.setattr(processing, "index_ai_best_effort", lambda *_args: None)
    monkeypatch.setattr(processing, "duplicate_check_best_effort", lambda *_args: None)
    worker_id = "test_" + uuid.uuid4().hex
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        db.add(
            ApplicationSetting(
                key=f"external_worker.{worker_id}",
                value=json.dumps(
                    {
                        "url": "https://worker.example",
                        "enabled": True,
                        "capabilities": validate_capabilities(capabilities),
                    }
                ),
                updated_by=user.id,
            )
        )
        item = Item(
            part_number="EXT-" + uuid.uuid4().hex,
            name="External",
            created_by=user.id,
            part_type="ASSEMBLY" if package else "PART",
        )
        db.add(item)
        db.flush()
        revision = Revision(
            part_id=item.id,
            revision_code="A",
            cad_system=system,
            native_format="FCStd" if system == "freecad" else "GENERIC",
            metadata_json={},
            state="PROCESSING",
            created_by=user.id,
        )
        db.add(revision)
        db.flush()
        artifact = Artifact(
            artifact_type="CAD_MODEL",
            name="Source",
            classification="AUTHORITATIVE",
            created_by=user.id,
        )
        db.add(artifact)
        db.flush()
        storage_key = f"{worker_id}.generic"
        source = tmp_path / storage_key
        source.write_bytes(native)
        db.add(
            ArtifactFile(
                artifact_id=artifact.id,
                revision_id=revision.id,
                reference_name="MODEL",
                role="PRIMARY",
                kind="NATIVE",
                storage_key=storage_key,
                original_filename="source.FCStd" if system == "freecad" else "source.generic",
                mime_type="application/octet-stream",
                byte_size=len(native),
                sha256=hashlib.sha256(native).hexdigest(),
                state="VERIFIED",
            )
        )
        if package:
            support = b"support data"
            support_key = f"{worker_id}.dat"
            (tmp_path / support_key).write_bytes(support)
            db.add(
                ArtifactFile(
                    artifact_id=artifact.id,
                    revision_id=revision.id,
                    reference_name="references/material.dat",
                    role="SECONDARY",
                    kind="NATIVE",
                    storage_key=support_key,
                    original_filename="material.dat",
                    mime_type="application/octet-stream",
                    byte_size=len(support),
                    sha256=hashlib.sha256(support).hexdigest(),
                    state="VERIFIED",
                )
            )
        relation = RevisionArtifactRelation(
            revision_id=revision.id,
            artifact_id=artifact.id,
            relation_type="SPECIFICATION",
            is_primary=True,
            created_by=user.id,
        )
        db.add(relation)
        snapshot = RevisionContentSnapshot(
            revision_id=revision.id, sequence=1, commit_id=uuid.uuid4(), created_by=user.id
        )
        db.add(snapshot)
        db.flush()
        db.add(RevisionContentSnapshotArtifact(snapshot_id=snapshot.id, relation_id=relation.id))
        job = ProcessingJob(
            revision_id=revision.id,
            snapshot_id=snapshot.id,
            type=JOB_TYPE,
            state="RUNNING",
            attempt_count=1,
            idempotency_key=str(uuid.uuid4()),
        )
        db.add(job)
        db.commit()
        try:
            settings = get_settings().model_copy(
                update={"storage_root": tmp_path, "internal_worker_key": "test-key"}
            )
            if tamper:
                with pytest.raises(ValueError, match="Invalid manifest"):
                    processing.process_job(db, job, settings)
                assert observed["downloads"] == 0
            else:
                processing.process_job(db, job, settings)
                assert job.state == "SUCCEEDED"
                assert observed["request"]["cadSystem"] == system
                assert observed["downloads"] == 3
                if package:
                    assert revision.state == "READY_WITH_WARNINGS"
                    assert any(
                        "without managed BOM" in warning
                        for warning in job.diagnostics_json["warnings"]
                    )
        finally:
            db.rollback()
            record = db.get(ApplicationSetting, f"external_worker.{worker_id}")
            if record:
                db.delete(record)
                db.commit()
