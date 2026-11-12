"""Retries must retain their snapshot and reject stale work before queuing."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from modelryx.api import previews


@pytest.mark.parametrize("snapshot_matches", [True, False])
def test_retry_rejects_obsolete_snapshot_without_changing_revision(monkeypatch, snapshot_matches):
    snapshot_id = uuid.uuid4()
    previous = SimpleNamespace(
        id=uuid.uuid4(),
        revision_id=uuid.uuid4(),
        snapshot_id=snapshot_id,
        state="FAILED",
        type="INVENTOR_TO_MODELRYX",
    )
    revision = SimpleNamespace(id=previous.revision_id, state="READY")
    db = Mock()
    db.get.side_effect = [previous, revision]
    active = SimpleNamespace(id=uuid.uuid4())
    db.scalar.return_value = active
    monkeypatch.setattr(previews, "_require_processing_control", Mock())
    monkeypatch.setattr(
        previews,
        "latest_content_snapshot_id",
        lambda *_: snapshot_id if snapshot_matches else uuid.uuid4(),
    )
    monkeypatch.setattr(previews, "_job_response_with_files", lambda _, job, **__: job)
    if snapshot_matches:
        assert previews.retry_processing_job(previous.id, Mock(), db, Mock()) is active
    else:
        with pytest.raises(HTTPException) as error:
            previews.retry_processing_job(previous.id, Mock(), db, Mock())
        assert error.value.status_code == 409
        assert "earlier content version" in error.value.detail
        db.scalar.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()
    assert revision.state == "READY"
