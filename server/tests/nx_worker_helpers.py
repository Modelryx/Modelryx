"""Exercise native result publication against an authenticated SDK-free NX worker double."""

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import httpx
from modelryx.core.config import get_settings
from modelryx.models import Artifact, ArtifactFile, BomLine, ProcessingJob, Revision
from modelryx.services import processing
from sqlalchemy import select


def assert_nx_publication(db, revision_id, monkeypatch):
    revision = db.get(Revision, revision_id)
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.revision_id == revision_id))
    before = [
        (line.id, line.child_revision_id, line.occurrence_key, line.quantity)
        for line in db.scalars(
            select(BomLine).where(BomLine.parent_revision_id == revision_id).order_by(BomLine.id)
        )
    ]
    assert before, "the committed assembly must have explicit managed child revisions"
    fixtures = Path(__file__).resolve().parent / "fixtures" / "nx"
    payloads = {
        "native-snapshot": (fixtures / "simple-assembly.snapshot.json").read_bytes(),
        "part-metadata": (fixtures / "simple-assembly.part-metadata.json").read_bytes(),
        "model-preview": json.dumps(
            {
                "schemaVersion": 1,
                "rootOccurrenceId": "nx-root",
                "items": [
                    {
                        "occurrenceId": "nx-root",
                        "partId": "nx-root",
                        "name": "NX assembly",
                        "nodeType": "assembly",
                    }
                ],
            }
        ).encode(),
    }
    observed = {}

    class Worker:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        @staticmethod
        def response(url, payload):
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

        def get(self, url, *, headers):
            assert headers["X-Modelryx-Worker-Key"] == "test-nx-key"
            if url.endswith("/ready"):
                return httpx.Response(503, request=httpx.Request("GET", url))
            return self.response(
                url,
                {
                    "cadSystem": "siemens_nx",
                    "development": {
                        "enabled": True,
                        "documentKinds": ["part", "assembly"],
                        "capabilities": ["metadata-v1", "model-preview-v1"],
                    },
                },
            )

        def post(self, url, *, headers, files):
            assert headers["X-Modelryx-Worker-Key"] == "test-nx-key"
            request = json.load(files["request"][1])
            native = json.loads(payloads["native-snapshot"])
            native["source_filename"] = Path(request["source"]["rootPath"].replace("\\", "/")).name
            payloads["native-snapshot"] = json.dumps(native).encode()
            assert (
                request["source"]["sha256"] == hashlib.sha256(files["source"][1].read()).hexdigest()
            )
            assert request["documentKind"] == "assembly"
            observed["request"] = request
            result = {
                **{
                    key: request[key]
                    for key in ("contractVersion", "jobId", "attempt", "cadSystem", "configuration")
                },
                "sourceSha256": request["source"]["sha256"],
                "adapterVersion": "sdk-free-test",
                "status": "partial",
                "warnings": ["Optional GLB unavailable"],
                "outputs": [
                    {
                        "role": role,
                        "mediaType": "application/json",
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size": len(data),
                        "downloadUrl": f"/api/v1/jobs/{request['jobId']}/outputs/{role}",
                        "provenance": {
                            "source_role": "native-snapshot",
                            "generator": "sdk-free-nx-test",
                        },
                    }
                    for role, data in payloads.items()
                ],
            }
            observed["result"] = result
            return self.response(url, {"state": "Partial", "result": result})

        @contextmanager
        def stream(self, _method, url, *, headers):
            assert headers["X-Modelryx-Worker-Key"] == "test-nx-key"
            response = httpx.Response(
                200, content=payloads[url.rsplit("/", 1)[1]], request=httpx.Request("GET", url)
            )
            try:
                yield response
            finally:
                response.close()

    monkeypatch.setattr(processing.httpx, "Client", lambda **_kwargs: Worker())
    monkeypatch.setattr(
        processing, "_configured_cad_server_url", lambda *_args: "https://nx-worker.test"
    )
    monkeypatch.setattr(processing, "index_ai_best_effort", lambda *_args: None)
    monkeypatch.setattr(processing, "duplicate_check_best_effort", lambda *_args: None)
    settings = get_settings().model_copy(
        update={"internal_worker_key": "test-nx-key", "nx_allow_unqualified_processing": True}
    )
    source_snapshot = job.snapshot_id
    job.state = "RUNNING"
    job.attempt_count = 1
    db.commit()
    processing.process_licensed_cad_job(db, job, settings)
    assert job.state == "SUCCEEDED" and job.snapshot_id == source_snapshot
    assert revision.current_metadata_extraction_id is not None
    after = [
        (line.id, line.child_revision_id, line.occurrence_key, line.quantity)
        for line in db.scalars(
            select(BomLine).where(BomLine.parent_revision_id == revision_id).order_by(BomLine.id)
        )
    ]
    assert after == before, (
        "worker publication must not retarget or rewrite committed managed BOM lines"
    )
    result_file = db.scalar(
        select(ArtifactFile).where(
            ArtifactFile.revision_id == revision_id,
            ArtifactFile.original_filename == "siemens_nx.result-manifest.json",
        )
    )
    assert result_file is not None
    container = db.get(Artifact, result_file.artifact_id)
    assert container.classification == "DERIVED" and container.derived_from_artifact_id is not None
    stored = json.loads(
        processing.LocalStorageProvider(settings.storage_root)
        .path_for(result_file.storage_key)
        .read_text()
    )
    assert stored == observed["result"], (
        "conversion provenance must survive immutable platform publication"
    )
