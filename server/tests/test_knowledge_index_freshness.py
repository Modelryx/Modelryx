"""Knowledge projection regressions independent of a running database."""

import hashlib
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from modelryx.core.config import Settings
from modelryx.knowledge.service import KNOWLEDGE_INDEX_VERSION, index_revision_knowledge
from modelryx.models import KnowledgeChunk
from modelryx.services.ai import AiGateway


@pytest.mark.parametrize(
    "vector,model,rebuild,enabled",
    [
        (None, "current", True, True),
        ([0.0] * 1536, "old", True, True),
        ([0.0] * 3, "current", True, True),
        ([0.0] * 1536, "current", False, True),
        (None, "current", False, False),
    ],
)
def test_index_checks_embedding_completeness_and_model(
    monkeypatch, vector, model, rebuild, enabled
):
    document = {
        "chunk_key": "revision-summary",
        "content": "Lifecycle state: RELEASED",
        "metadata": {},
    }
    monkeypatch.setattr(
        "modelryx.knowledge.service.build_revision_documents", lambda *_: [document]
    )
    embed = Mock(return_value=[[0.0] * 1536])
    monkeypatch.setattr(AiGateway, "embed", embed)
    source = SimpleNamespace(
        id=uuid.uuid4(),
        content_hash=hashlib.sha256(document["content"].encode()).hexdigest(),
        index_version=KNOWLEDGE_INDEX_VERSION,
        is_stale=False,
    )
    revision = SimpleNamespace(
        id=uuid.uuid4(),
        part_id=uuid.uuid4(),
        revision_code="A",
        state="READY",
        lifecycle_state="RELEASED",
        committed_at=None,
        created_at=None,
        cad_system="FreeCAD",
        native_format="FCStd",
    )
    db = Mock()
    db.get.return_value = SimpleNamespace(id=revision.part_id, part_number="P1", part_type="PART")
    db.execute.return_value.all.return_value = []
    db.scalar.return_value = source
    db.scalars.side_effect = [[], [SimpleNamespace(embedding=vector, embedding_model=model)]]
    settings = Settings(
        ai_gateway_base_url="http://gateway.invalid/v1" if enabled else "",
        ai_embedding_model="current",
    )
    result, changed = index_revision_knowledge(db, revision, settings)
    assert result is source
    assert changed is rebuild
    assert embed.call_count == int(rebuild)
    if rebuild:
        assert source.lifecycle_state == "RELEASED"
        chunk = db.add.call_args.args[0]
        assert isinstance(chunk, KnowledgeChunk)
        assert chunk.embedding_model == "current"


def test_embedding_failure_preserves_lexical_fallback(monkeypatch):
    # Simulate the stored lexical-only projection after an unavailable provider.
    monkeypatch.setattr(AiGateway, "embed", Mock(side_effect=HTTPException(502, "offline")))
    from modelryx.knowledge.service import _embed

    settings = Settings(
        ai_gateway_base_url="http://gateway.invalid/v1", ai_embedding_model="current"
    )
    assert _embed(["evidence"], settings) == [None]
