"""Publication search projections must fail independently of CAD processing."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from modelryx.core.config import Settings
from modelryx.services.processing import index_ai_best_effort


@pytest.mark.parametrize("failure", [None, "ai", "knowledge", "commit"])
def test_publication_refreshes_both_indexes_independently(monkeypatch, caplog, failure):
    db = Mock()
    revision = SimpleNamespace(id=uuid.uuid4(), state="READY")
    settings = Settings()
    source = SimpleNamespace(id=uuid.uuid4())
    ai = Mock(return_value=6)
    knowledge = Mock(return_value=(source, True))
    if failure == "ai":
        ai.side_effect = RuntimeError("AI index unavailable")
    if failure == "knowledge":
        knowledge.side_effect = RuntimeError("Knowledge index unavailable")
    if failure == "commit":
        db.commit.side_effect = RuntimeError("Knowledge commit failed")
    monkeypatch.setattr("modelryx.services.ai.index_revision", ai)
    monkeypatch.setattr("modelryx.knowledge.service.index_revision_knowledge", knowledge)

    index_ai_best_effort(db, revision, settings)

    ai.assert_called_once_with(db, revision, settings)
    knowledge.assert_called_once_with(db, revision, settings)
    assert revision.state == "READY"
    assert db.rollback.call_count == int(failure is not None)
    assert db.commit.call_count == int(failure != "knowledge")
    if failure:
        index_name = "ai" if failure == "ai" else "knowledge"
        assert f"{index_name}_index_failed" in caplog.text


def test_ai_failure_is_rolled_back_before_knowledge_indexing(monkeypatch):
    db = Mock()
    revision = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(
        "modelryx.services.ai.index_revision", Mock(side_effect=RuntimeError("failed"))
    )

    def index_knowledge(*_):
        db.rollback.assert_called_once()
        return SimpleNamespace(id=uuid.uuid4()), False

    monkeypatch.setattr("modelryx.knowledge.service.index_revision_knowledge", index_knowledge)
    index_ai_best_effort(db, revision, Settings())
    db.commit.assert_called_once()
