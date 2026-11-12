"""Opt-in native FreeCAD remote publication test using a generated linked assembly."""

import os
import uuid
from pathlib import Path

import pytest
from artifact_helpers import commit_item
from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import BomLine, ProcessingJob, Revision
from modelryx.services.processing import process_job
from sqlalchemy import select

AUTH = AUTH
WORKER_URL = os.environ.get("MODELRYX_TEST_FREECAD_WORKER_URL")
pytestmark = pytest.mark.skipif(not WORKER_URL, reason="standalone FreeCAD worker not configured")


def test_real_worker_publishes_linked_assembly(monkeypatch):
    fixture = Path(os.environ["MODELRYX_TEST_FREECAD_FIXTURES"])
    with TestClient(app) as client:
        committed = commit_item(
            client,
            item_number=f"REMOTE-{uuid.uuid4().hex}",
            name="Remote FreeCAD assembly",
            item_type="ASSEMBLY",
            files=[
                ("Root.FCStd", (fixture / "Root.FCStd").read_bytes()),
                ("Child.FCStd", (fixture / "Child.FCStd").read_bytes()),
            ],
            bom=[("Root.FCStd", "Child.FCStd", "ExternalPin")],
        )
        revision_id = uuid.UUID(committed["revision_id"])
        settings = get_settings()
        monkeypatch.setattr(settings, "freecad_worker_url", WORKER_URL)
        with SessionLocal() as db:
            before = list(
                db.execute(
                    select(BomLine.occurrence_key, BomLine.child_revision_id).where(
                        BomLine.parent_revision_id == revision_id
                    )
                )
            )
            assert len(before) == 1
            job = db.scalar(select(ProcessingJob).where(ProcessingJob.revision_id == revision_id))
            assert job is not None
            job.state = "RUNNING"
            job.attempt_count = 1
            db.commit()
            process_job(db, job, settings)
            revision = db.get(Revision, revision_id)
            assert job.state == "SUCCEEDED"
            assert revision.state in {"READY", "READY_WITH_WARNINGS"}
            assert any(
                "ExternalPin" in str(item) for item in revision.derived_metadata_json["items"]
            )
            after = list(
                db.execute(
                    select(BomLine.occurrence_key, BomLine.child_revision_id).where(
                        BomLine.parent_revision_id == revision_id
                    )
                )
            )
            assert after == before
        response = client.get(f"/api/v1/item-revisions/{revision_id}/artifacts", headers=AUTH)
        assert response.status_code == 200
        kinds = {entry["artifact"]["artifact_type"] for entry in response.json()}
        assert {"CAD_ASSEMBLY", "GLB", "METADATA", "OTHER"} <= kinds
