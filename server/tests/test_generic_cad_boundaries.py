"""Native CAD behavior depends on capabilities and lifecycle, never vendor names."""

import ast
import asyncio
import copy
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from modelryx.api import admin, previews, worker_health
from modelryx.cad.inspection import inspect_metadata_snapshot
from modelryx.core.config import get_settings
from modelryx.models import Artifact, Item, Revision, RevisionContentSnapshot
from modelryx.services import cad_routing, external_workers, processing
from modelryx.services.cad_worker_settings import (
    CAD_WORKER_KEY,
    LEGACY_SETTING_KEYS,
    configured_url,
    worker_default,
)


def caps(system="Example CAD"):
    return external_workers.validate_capabilities(
        {
            "contractVersion": "1.0",
            "cadSystem": system,
            "workerName": system,
            "workerVersion": "1.0",
            "sourceExtensions": [".custom", ".assembly"],
            "documentKinds": ["part", "assembly"],
            "dependencyAnalysis": True,
            "outputRoles": ["native-snapshot", "model-preview", "part-metadata", "glb"],
        }
    )


@pytest.mark.parametrize(
    "system", ["FreeCAD", "Inventor", "AutoCAD", "NX", "solidworks", "Acme CAD"]
)
def test_every_native_system_requires_a_matching_worker(monkeypatch, system):
    worker = {"id": "arbitrary-worker-17", "enabled": True, "capabilities": caps(system)}
    workers = []
    monkeypatch.setattr(external_workers, "discover_workers", lambda *_: workers)
    assert cad_routing.processing_type_for_source("part.custom", system, object()) is None
    workers.append(worker)
    assert (
        cad_routing.processing_type_for_source("part.custom", system, object())
        == external_workers.JOB_TYPE
    )
    worker["enabled"] = False
    assert cad_routing.processing_type_for_source("part.custom", system, object()) is None
    assert cad_routing.processing_type_for_source("part.fcstd", system) is None


@pytest.mark.parametrize(
    "system", ["FreeCAD", "Inventor", "AutoCAD", "NX", "solidworks", "Acme CAD"]
)
def test_generic_admin_probe_and_preflight_accept_any_cad_system(monkeypatch, system):
    capabilities = caps(system)
    worker = {
        "id": "arbitrary-worker-17",
        "enabled": True,
        "url": "http://worker",
        "capabilities": capabilities,
    }
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return httpx.Response(200, json=capabilities, request=httpx.Request("GET", url))

    monkeypatch.setattr(worker_health.httpx, "AsyncClient", lambda **_: Client())
    monkeypatch.setattr(external_workers, "discover_workers", lambda *_: [worker])
    result = asyncio.run(admin._probe_cad_worker("worker", "http://worker", "test-key"))
    assert result.availability == "ONLINE" and result.cad_system == system
    result = asyncio.run(
        worker_health.worker_preflight(
            system,
            object(),
            None,
            SimpleNamespace(internal_worker_key="test-key"),
            worker_id="arbitrary-worker-17",
        )
    )
    assert result.available and result.compatible
    assert calls[-1][1]["headers"]["X-Modelryx-Worker-Key"] == "test-key"


def test_configured_endpoint_and_legacy_alias_have_defined_precedence():
    rows = {LEGACY_SETTING_KEYS[CAD_WORKER_KEY]: SimpleNamespace(value="http://legacy")}
    db = SimpleNamespace(get=lambda _, key: rows.get(key))
    assert configured_url(db, CAD_WORKER_KEY, "http://default") == "http://legacy"
    rows[CAD_WORKER_KEY] = SimpleNamespace(value="http://generic")
    assert configured_url(db, CAD_WORKER_KEY, "http://default") == "http://generic"
    rows[CAD_WORKER_KEY].value = ""
    assert configured_url(db, CAD_WORKER_KEY, "http://default") is None
    assert (
        worker_default(SimpleNamespace(cad_worker_url="", freecad_worker_url="http://old")) is None
    )
    assert worker_default(SimpleNamespace(freecad_worker_url="http://old")) == "http://old"


def test_admin_wire_aliases_do_not_change_the_configured_cad_system():
    value = admin.CadWorkerSettingsUpdate(freecad_url="http://vendor")
    assert value.worker_url == "http://vendor"
    assert value.model_fields_set == {"worker_url"}
    response = admin.CadWorkerSettingsResponse(worker_url=value.worker_url).model_dump()
    assert response["worker_url"] == response["freecad_url"] == "http://vendor"


@pytest.mark.parametrize("extension", [".sldprt", ".ipt", ".prt", ".dwg", ".custom.part"])
def test_identity_matching_uses_declared_extensions_and_preserves_occurrences(extension):
    assert cad_routing.identity_token(f"refs/Bracket{extension}:2", [extension]) == "bracket2"
    assert (
        cad_routing.identity_token(f"refs/Bracket{extension}:2", [extension], child=True)
        == "bracket"
    )
    assert cad_routing.identity_token(
        f"Bracket{extension}:1", [extension]
    ) != cad_routing.identity_token(f"Bracket{extension}:2", [extension])
    assert cad_routing.identity_token("Bracket.v2", [extension]) == "bracketv2"


@pytest.mark.parametrize(
    "job_type", sorted(cad_routing.NATIVE_JOB_TYPES | cad_routing.NEUTRAL_JOB_TYPES)
)
def test_running_processing_cancellation_is_independent_of_worker_identity(monkeypatch, job_type):
    job = SimpleNamespace(
        id=uuid.uuid4(),
        revision_id=uuid.uuid4(),
        type=job_type,
        state="RUNNING",
        stage="EXTRACTING",
        diagnostics_json={},
    )
    revision = SimpleNamespace(id=job.revision_id)
    db = SimpleNamespace(
        scalar=lambda _: job, get=lambda *_: revision, commit=lambda: None, refresh=lambda *_: None
    )
    monkeypatch.setattr(previews, "_require_processing_control", lambda *_: None)
    monkeypatch.setattr(previews, "user_for_principal", lambda *_: SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(previews, "record_audit", lambda *_, **__: None)
    monkeypatch.setattr(previews, "_job_response_with_files", lambda _, value, **__: value)
    request = SimpleNamespace(state=SimpleNamespace(request_id="test-request"))
    result = previews.cancel_processing_job(job.id, request, db, object())
    assert result.stage == "CANCELLING"
    assert result.diagnostics_json["cancellation"]["requested"] is True
    job.stage = "PUBLISHING"
    with pytest.raises(HTTPException) as error:
        previews.cancel_processing_job(job.id, request, db, object())
    assert error.value.status_code == 409


def test_publication_locks_the_job_and_checks_cancellation_before_commit():
    trace = []
    job = SimpleNamespace(
        id=uuid.uuid4(),
        stage="VALIDATING",
        progress_percent=80,
        diagnostics_json={"cancellation": {"requested": True}},
    )

    def lock(query):
        assert "FOR UPDATE" in str(query)
        trace.append("lock")

    db = SimpleNamespace(
        scalar=lock,
        refresh=lambda *_, **__: trace.append("refresh"),
        commit=lambda: trace.append("commit"),
    )
    with pytest.raises(processing.ProcessingCancelled):
        processing.update_stage(db, job, "PUBLISHING", 90)
    assert trace == ["lock", "refresh"] and job.stage == "VALIDATING"
    job.diagnostics_json = {}
    trace.clear()
    processing.update_stage(db, job, "PUBLISHING", 90)
    assert trace == ["lock", "refresh", "commit"] and job.stage == "PUBLISHING"


@pytest.mark.parametrize("job_type", sorted(cad_routing.NATIVE_JOB_TYPES))
def test_legacy_and_new_native_jobs_use_one_dispatcher(monkeypatch, job_type):
    calls = []
    job = SimpleNamespace(type=job_type)
    monkeypatch.setattr(processing, "process_external_cad_job", lambda *args: calls.append(args))
    processing.process_job("db", job, "settings")
    assert calls == [("db", job, "settings")]


def test_source_does_not_branch_on_cad_vendor_or_worker_names():
    root = Path(processing.__file__).parents[1]
    forbidden = {
        "freecad",
        "inventor",
        "autocad",
        "nx",
        "solidworks",
        "freecad-controlled",
        ".fcstd",
    }
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.IfExp, ast.Compare)):
                expression = node.test if isinstance(node, (ast.If, ast.IfExp)) else node
                literals = [
                    value.value.casefold()
                    for value in ast.walk(expression)
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                ]
                assert not forbidden.intersection(literals), (
                    f"CAD-specific branch in {path}:{node.lineno}"
                )


@pytest.mark.parametrize(
    "system", ["FreeCAD", "Inventor", "AutoCAD", "NX", "solidworks", "Acme CAD"]
)
def test_sketch_inspection_uses_reported_structure_instead_of_cad_name(system):
    metadata = {
        "source": {"system": system},
        "native": {
            "sketches": [
                {
                    "name": "Sketch",
                    "constraints": [
                        {
                            "index": 0,
                            "type": "Distance",
                            "value": 5,
                            "unit": "mm",
                            "active": True,
                            "driving": True,
                        }
                    ],
                }
            ]
        },
        "features": {"items": [{"persistent_id": "Sketch", "type": "SKETCH"}]},
    }
    result = inspect_metadata_snapshot(
        copy.deepcopy(metadata), revision_id=str(uuid.uuid4()), source_sha256="a" * 64
    )
    assert result.features[0].parameters[0].value == 5
    assert result.features[0].parameters[0].editable


def test_fast_completed_native_conversion_cannot_publish_after_cancellation(tmp_path, monkeypatch):
    source = b"native test source"
    (tmp_path / "source").write_bytes(source)
    job = SimpleNamespace(
        id=uuid.uuid4(),
        revision_id=uuid.uuid4(),
        snapshot_id=uuid.uuid4(),
        attempt_count=1,
        diagnostics_json={},
    )
    revision = SimpleNamespace(id=job.revision_id, part_id=uuid.uuid4(), cad_system="Example CAD")
    snapshot = SimpleNamespace(id=job.snapshot_id)
    artifact = SimpleNamespace(
        id=uuid.uuid4(), artifact_type="CAD_MODEL", classification="AUTHORITATIVE"
    )
    file = SimpleNamespace(
        id=uuid.uuid4(),
        artifact_id=artifact.id,
        role="PRIMARY",
        reference_name="model.custom",
        original_filename="model.custom",
        storage_key="source",
        byte_size=len(source),
        sha256=hashlib.sha256(source).hexdigest(),
        mime_type="application/octet-stream",
    )
    objects = {
        Revision: revision,
        RevisionContentSnapshot: snapshot,
        Artifact: artifact,
        Item: SimpleNamespace(part_type="PART"),
    }
    db = SimpleNamespace(
        get=lambda model, _: objects[model], commit=lambda: None, refresh=lambda *_, **__: None
    )
    capabilities = caps()
    monkeypatch.setattr(processing, "artifact_files_for_snapshot", lambda *_, **__: [file])
    monkeypatch.setattr(
        processing,
        "matching_worker",
        lambda *_: {
            "id": "anything",
            "url": "http://native",
            "capabilities": capabilities,
        },
    )
    downloads = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, url, **_):
            return httpx.Response(
                200,
                json=capabilities if url.endswith("/capabilities") else {},
                request=httpx.Request("GET", url),
            )

        def post(self, url, *, files, **_):
            request = json.load(files["request"][1])
            job.diagnostics_json = {"cancellation": {"requested": True}}
            result = {
                key: request[key]
                for key in ("contractVersion", "jobId", "attempt", "cadSystem", "configuration")
            }
            result.update(
                status="complete",
                warnings=[],
                adapterVersion="1",
                sourceSha256=request["source"]["sha256"],
                outputs=[
                    {
                        "role": role,
                        "mediaType": "application/json",
                        "size": 2,
                        "sha256": hashlib.sha256(b"{}").hexdigest(),
                        "downloadUrl": f"/api/v1/jobs/{request['jobId']}/outputs/{role}",
                        "provenance": {"generator": "test", "source_role": "native-source"},
                    }
                    for role in request["requiredOutputs"]
                ],
            )
            return httpx.Response(
                200,
                json={"state": "ReadyToPublish", "result": result},
                request=httpx.Request("POST", url),
            )

        def stream(self, *_, **__):
            downloads.append(True)
            pytest.fail("Cancelled native output must not be downloaded or published")

    monkeypatch.setattr(processing.httpx, "Client", lambda **_: Client())
    settings = get_settings().model_copy(
        update={"storage_root": tmp_path, "internal_worker_key": "test-key"}
    )
    with pytest.raises(processing.ProcessingCancelled):
        processing.process_external_cad_job(db, job, settings)
    assert downloads == []


@pytest.mark.parametrize(
    "system", ["FreeCAD", "Inventor", "AutoCAD", "NX", "solidworks", "Acme CAD"]
)
def test_analysis_only_endpoint_does_not_need_converter_output_roles(monkeypatch, system):
    advertised = {"contractVersion": "1.0", "cadSystem": system, "dependencyAnalysis": True}

    def respond(request):
        return httpx.Response(
            200, json=advertised if request.url.path.endswith("capabilities") else {}
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        admin.httpx,
        "AsyncClient",
        lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(respond)),
    )
    result = asyncio.run(
        admin._probe_cad_worker("analyzer", "http://analysis", "test", dependency_analysis=True)
    )
    assert result.availability == "ONLINE" and result.cad_system == system
    advertised["dependencyAnalysis"] = False
    result = asyncio.run(
        admin._probe_cad_worker("analyzer", "http://analysis", "test", dependency_analysis=True)
    )
    assert result.availability == "OFFLINE"


@pytest.mark.parametrize("can_control", [True, False])
@pytest.mark.parametrize(
    "job_type,state,stage,expected",
    [
        (external_workers.JOB_TYPE, "PENDING", "QUEUED", True),
        (external_workers.JOB_TYPE, "RUNNING", "EXTRACTING", True),
        ("STEP_TO_MODELRYX", "RUNNING", "CONVERTING", True),
        (external_workers.JOB_TYPE, "RUNNING", "PUBLISHING", False),
        ("CAD_SESSION_EXECUTE", "RUNNING", "EXECUTING", True),
        ("CAD_SESSION_EXECUTE", "RUNNING", "PUBLISHING", False),
        ("UNKNOWN_JOB", "RUNNING", "EXECUTING", False),
        (external_workers.JOB_TYPE, "SUCCEEDED", "COMPLETED", False),
        (external_workers.JOB_TYPE, "FAILED", "CANCELLED", False),
    ],
)
def test_response_reports_cancellation_capability_and_enforces_ownership(
    can_control, job_type, state, stage, expected
):
    job = SimpleNamespace(
        id=uuid.uuid4(),
        revision_id=uuid.uuid4(),
        snapshot_id=None,
        type=job_type,
        state=state,
        stage=stage,
        progress_percent=0,
        diagnostics_json={},
        converter_version=None,
        cache_key=None,
        attempt_count=1,
        error_detail=None,
        available_at=datetime.now(UTC),
        started_at=None,
        finished_at=None,
    )
    response = previews._job_response_with_files(
        SimpleNamespace(get=lambda *_: None), job, can_control=can_control
    )
    assert response.can_cancel == (can_control and expected)
