import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from modelryx.api.previews import preview_files
from modelryx.models import Revision, RevisionContentSnapshot


def test_preview_files_use_selected_revision_snapshot() -> None:
    revision_id, snapshot_id = uuid.uuid4(), uuid.uuid4()
    db = MagicMock()
    db.get.side_effect = lambda model, _id: (
        SimpleNamespace(id=revision_id)
        if model is Revision else SimpleNamespace(revision_id=revision_id)
        if model is RevisionContentSnapshot else None
    )
    with patch("modelryx.api.previews.artifact_files_for_snapshot", return_value=[]) as files:
        assert preview_files(db, revision_id, snapshot_id) == []
    files.assert_called_once_with(db, revision_id, snapshot_id)


def test_preview_files_reject_foreign_snapshot() -> None:
    revision_id, snapshot_id = uuid.uuid4(), uuid.uuid4()
    db = MagicMock()
    db.get.side_effect = lambda model, _id: (
        SimpleNamespace(id=revision_id) if model is Revision
        else SimpleNamespace(revision_id=uuid.uuid4())
    )
    with pytest.raises(HTTPException) as error:
        preview_files(db, revision_id, snapshot_id)
    assert error.value.status_code == 404
