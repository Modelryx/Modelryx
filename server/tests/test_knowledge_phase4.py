import json
import uuid
from pathlib import Path

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.auth import Principal, create_access_token
from modelryx.core.db import SessionLocal
from modelryx.knowledge.models import EvidenceBundle, RetrievalPlan
from modelryx.models import Item, KnowledgeSource, Revision, User
from sqlalchemy import select

client = TestClient(app)


def _knowledge_revision(*, state: str = "READY", owner_subject: str = "local-development-user"):
    token = uuid.uuid4().hex
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == owner_subject))
        item = Item(
            part_number=f"PF-KNOW-{token[:8]}",
            name=f"Knowledge Housing {token}",
            description=f"Recorded engineering evidence {token}",
            part_type="PART",
            created_by=user.id,
        )
        db.add(item)
        db.flush()
        revision = Revision(
            part_id=item.id,
            revision_code="C",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={"change_reason": f"Clearance requirement {token}"},
            derived_metadata_json={},
            state=state,
            metadata_status="READY",
            created_by=user.id,
        )
        db.add(revision)
        db.flush()
        item.current_revision_id = revision.id
        db.commit()
        return str(item.id), str(revision.id), token


def test_phase40_contracts_and_golden_questions_are_strict() -> None:
    corpus = json.loads((Path(__file__).parent / "knowledge" / "golden_questions.json").read_text())
    assert len(corpus) >= 4
    assert {row["intent"] for row in corpus} >= {
        "EXPLAIN_CHANGE",
        "FAILURE_HISTORY",
        "KNOWLEDGE_SEARCH",
    }
    plan = RetrievalPlan(
        intent="EXPLAIN_CHANGE",
        scope={"revision_id": str(uuid.uuid4())},
        steps=[
            "AUTHORIZE_CONTEXT",
            "COMPARE_PREVIOUS_REVISION",
            "REAUTHORIZE_RESULTS",
        ],
    )
    assert plan.steps[-1] == "REAUTHORIZE_RESULTS"
    bundle = EvidenceBundle(
        question="Why?",
        evidence_quality="NONE",
        warnings=["No recorded evidence"],
    )
    assert bundle.documents == []


def test_phase41_indexes_revision_idempotently_and_retrieves_evidence() -> None:
    item_id, revision_id, token = _knowledge_revision()
    indexed = client.post(
        f"/api/v1/knowledge/revisions/{revision_id}/index",
        headers={**AUTH, "X-Request-ID": "phase41-index"},
    )
    assert indexed.status_code == 200, indexed.text
    source = indexed.json()
    assert source["source_type"] == "ITEM_REVISION"
    assert source["revision_id"] == revision_id
    assert source["chunks"]

    repeated = client.post(f"/api/v1/knowledge/revisions/{revision_id}/index", headers=AUTH)
    assert repeated.status_code == 200
    assert repeated.json()["id"] == source["id"]
    assert [row["id"] for row in repeated.json()["chunks"]] == [
        row["id"] for row in source["chunks"]
    ]

    search = client.post(
        "/api/v1/knowledge/search",
        headers=AUTH,
        json={
            "query": f"clearance requirement {token}",
            "filters": {"item_ids": [item_id]},
        },
    )
    assert search.status_code == 200, search.text
    assert search.json()["results"][0]["source_id"] == source["id"]
    assert "score" not in search.json()["results"][0]

    evidence = client.post(
        "/api/v1/knowledge/retrieve",
        headers=AUTH,
        json={
            "query": f"Why clearance requirement {token}?",
            "filters": {"revision_ids": [revision_id]},
        },
    )
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["evidence_quality"] == "STRONG"
    assert evidence.json()["documents"][0]["source_id"] == source["id"]

    detail = client.get(f"/api/v1/knowledge/sources/{source['id']}", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json()["chunks"][0]["location"]["section"]


def test_phase41_reauthorizes_results_and_rejects_unknown_filters() -> None:
    with SessionLocal() as db:
        owner = User(
            external_subject=f"knowledge-owner-{uuid.uuid4()}",
            display_name="Knowledge Owner",
            role="engineer",
        )
        reader = User(
            external_subject=f"knowledge-reader-{uuid.uuid4()}",
            display_name="Knowledge Reader",
            role="reader",
        )
        db.add_all([owner, reader])
        db.commit()
        owner_subject = owner.external_subject
        reader_subject = reader.external_subject
    _, revision_id, token = _knowledge_revision(state="DRAFT", owner_subject=owner_subject)
    indexed = client.post(f"/api/v1/knowledge/revisions/{revision_id}/index", headers=AUTH)
    assert indexed.status_code == 200
    reader_auth = {
        "Authorization": "Bearer "
        + create_access_token(
            Principal(subject=reader_subject, display_name="Knowledge Reader", role="reader")
        )
    }
    hidden = client.post(
        "/api/v1/knowledge/search",
        headers=reader_auth,
        json={"query": f"engineering evidence {token}"},
    )
    assert hidden.status_code == 200
    assert indexed.json()["id"] not in [row["source_id"] for row in hidden.json()["results"]]
    forbidden = client.get(f"/api/v1/knowledge/sources/{indexed.json()['id']}", headers=reader_auth)
    assert forbidden.status_code == 404

    invalid = client.post(
        "/api/v1/knowledge/search",
        headers=AUTH,
        json={"query": "revision", "filters": {"organization_id": str(uuid.uuid4())}},
    )
    assert invalid.status_code == 422

    with SessionLocal() as db:
        stored = db.get(KnowledgeSource, uuid.UUID(indexed.json()["id"]))
        assert stored.content_hash
        assert stored.index_version == "phase4-v2"


def test_phase42_to_47_publish_change_context_and_relationships() -> None:
    item_id, revision_id, token = _knowledge_revision()
    indexed = client.post(f"/api/v1/knowledge/revisions/{revision_id}/index", headers=AUTH)
    assert indexed.status_code == 200
    change = client.post(
        "/api/v1/knowledge/sources",
        headers={**AUTH, "X-Request-ID": "phase4-change"},
        json={
            "source_type": "CHANGE_REQUEST",
            "title": f"Clearance change {token}",
            "lifecycle_state": "APPROVED",
            "authority_rank": 90,
            "item_id": item_id,
            "revision_id": revision_id,
            "metadata": {"approval_state": "APPROVED"},
            "chunks": [
                {
                    "section": "reason",
                    "content": f"Approved because interference was observed {token}",
                }
            ],
        },
    )
    assert change.status_code == 201, change.text
    relation = client.post(
        f"/api/v1/knowledge/sources/{change.json()['id']}/relations",
        headers=AUTH,
        json={
            "to_source_id": indexed.json()["id"],
            "relation_type": "CHANGE_IMPLEMENTED_IN",
        },
    )
    assert relation.status_code == 201, relation.text
    context = client.get(
        f"/api/v1/knowledge/revisions/{revision_id}/context",
        headers=AUTH,
        params={"question": f"Why was clearance changed {token}?"},
    )
    assert context.status_code == 200, context.text
    body = context.json()
    assert body["facts"][0]["type"] == "REVISION_STATE"
    assert any(row["source_type"] == "CHANGE_REQUEST" for row in body["documents"])
    assert "No recorded change reason was found" not in body["warnings"]


def test_knowledge_lifecycle_filter_tracks_released_revision() -> None:
    item_id, revision_id, token = _knowledge_revision()
    with SessionLocal() as db:
        revision = db.get(Revision, uuid.UUID(revision_id))
        revision.lifecycle_state = "RELEASED"
        db.commit()
    indexed = client.post(f"/api/v1/knowledge/revisions/{revision_id}/index", headers=AUTH)
    assert indexed.status_code == 200, indexed.text
    response = client.post("/api/v1/knowledge/search", headers=AUTH, json={
        "query": token, "filters": {"item_ids": [item_id], "lifecycle_states": ["RELEASED"]},
    })
    assert response.status_code == 200, response.text
    assert indexed.json()["id"] in {row["source_id"] for row in response.json()["results"]}
