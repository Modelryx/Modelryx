import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from modelryx.api.artifacts import list_revision_artifacts


def test_selected_artifact_snapshot_is_scoped_to_revision() -> None:
    revision_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(id=snapshot_id)
    db.execute.return_value.all.return_value = []

    with patch("modelryx.api.artifacts._readable_revision"):
        assert list_revision_artifacts(revision_id, db, None, snapshot_id) == []

    params = db.scalar.call_args.args[0].compile().params
    assert revision_id in params.values()
    assert snapshot_id in params.values()


def test_unknown_artifact_snapshot_returns_not_found() -> None:
    db = MagicMock()
    db.scalar.return_value = None

    with patch("modelryx.api.artifacts._readable_revision"):
        with pytest.raises(HTTPException) as error:
            list_revision_artifacts(uuid.uuid4(), db, None, uuid.uuid4())

    assert error.value.status_code == 404
    db.execute.assert_not_called()
