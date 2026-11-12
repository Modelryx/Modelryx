import hashlib
import uuid

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.db import SessionLocal
from modelryx.models import Item, ProcessingJob, Revision
from sqlalchemy import select

client = TestClient(app)


def test_nx_assembly_commits_the_isolated_native_package_and_queues_processing(monkeypatch) -> None:
    suffix = uuid.uuid4().hex
    contents = {
        "toycar_assy.prt": f"nx-root-{suffix}".encode(),
        "components/body.prt": f"nx-body-{suffix}".encode(),
    }
    created = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "operation": "CREATE_ITEM",
            "files": [
                {
                    "logical_path": path,
                    "filename": path.rsplit("/", 1)[-1],
                    "byte_size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "mime_type": "application/octet-stream",
                }
                for path, content in contents.items()
            ],
        },
    )
    assert created.status_code == 201, created.text
    staging = created.json()
    staged = {row["logical_path"]: row for row in staging["files"]}
    for path, content in contents.items():
        uploaded = client.put(
            staged[path]["upload_url"],
            headers={
                **AUTH,
                "Content-Type": "application/octet-stream",
            },
            content=content,
        )
        assert uploaded.status_code == 200, uploaded.text

    root_number = f"NX-ASM-{suffix}"
    body_number = f"NX-PART-{suffix}"
    proposed = client.put(
        f"/api/v1/staging/{staging['id']}/proposal",
        headers=AUTH,
        json={
            "expected_snapshot_version": staging["snapshot_version"],
            "operation": "CREATE_ITEM",
            "item": {"item_number": root_number, "name": "NX assembly", "item_type": "ASSEMBLY"},
            "revision": {"revision_code": "A", "cad_system": "Siemens NX", "native_format": "PRT"},
            "artifacts": [
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "owner_staged_file_id": None,
                    "artifact_type": "CAD_ASSEMBLY",
                    "name": "NX assembly",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": staged["toycar_assy.prt"]["id"],
                            "reference_name": "MODEL",
                            "role": "PRIMARY",
                        },
                        {
                            "staged_file_id": staged["components/body.prt"]["id"],
                            "reference_name": "components/body.prt",
                            "role": "SECONDARY",
                        },
                    ],
                },
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "owner_staged_file_id": staged["components/body.prt"]["id"],
                    "artifact_type": "CAD_MODEL",
                    "name": "NX body",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": staged["components/body.prt"]["id"],
                            "reference_name": "MODEL",
                            "role": "PRIMARY",
                        }
                    ],
                },
            ],
            "identity_decisions": [
                {"staged_file_id": staged["toycar_assy.prt"]["id"], "decision": "CREATE_NEW"},
                {
                    "staged_file_id": staged["components/body.prt"]["id"],
                    "decision": "CREATE_NEW",
                    "item": {"item_number": body_number, "name": "NX body", "item_type": "PART"},
                    "revision": {
                        "revision_code": "A",
                        "cad_system": "Siemens NX",
                        "native_format": "PRT",
                    },
                },
            ],
            "bom": [
                {
                    "parent_staged_file_id": staged["toycar_assy.prt"]["id"],
                    "child_staged_file_id": staged["components/body.prt"]["id"],
                    "instance_name": "BODY(1)",
                    "quantity": 1,
                }
            ],
        },
    )
    assert proposed.status_code == 200, proposed.text
    version = proposed.json()["snapshot_version"]
    assert client.post(f"/api/v1/staging/{staging['id']}/validate", headers=AUTH).status_code == 200
    ready = client.post(
        f"/api/v1/staging/{staging['id']}/ready",
        headers=AUTH,
        json={
            "expected_snapshot_version": version,
        },
    )
    assert ready.status_code == 200, ready.text
    committed = client.post(
        f"/api/v1/uploads/{staging['id']}/commit",
        headers=AUTH,
        json={
            "snapshot_version": ready.json()["snapshot_version"],
            "commit_id": str(uuid.uuid4()),
        },
    )
    assert committed.status_code == 200, committed.text

    with SessionLocal() as db:
        revisions = list(
            db.scalars(
                select(Revision)
                .join(Item, Revision.part_id == Item.id)
                .where(Item.part_number.in_([root_number, body_number]))
            )
        )
        assert len(revisions) == 2
        assert {
            (revision.cad_system, revision.native_format, revision.state) for revision in revisions
        } == {
            ("Siemens NX", "PRT", "PROCESSING"),
        }
        jobs = list(
            db.scalars(
                select(ProcessingJob).where(
                    ProcessingJob.revision_id.in_([revision.id for revision in revisions])
                )
            )
        )
        assert len(jobs) == 2
        assert all(job.type == "NX_TO_MODELRYX" and job.state == "PENDING" for job in jobs)
        root_revision = next(
            revision
            for revision in revisions
            if db.get(Item, revision.part_id).part_number == root_number
        )
        from nx_worker_helpers import assert_nx_publication

        assert_nx_publication(db, root_revision.id, monkeypatch)
