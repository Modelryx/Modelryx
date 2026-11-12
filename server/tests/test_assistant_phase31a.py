import random
import uuid
from datetime import UTC, datetime, timedelta

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.db import SessionLocal
from modelryx.engineering_search.interpreter import AISearchInterpreter, deterministic_interpret
from modelryx.models import (
    AiConversation,
    AiDocument,
    AiMessage,
    AiToolExecution,
    Artifact,
    ArtifactFile,
    BomLine,
    BomViewRevision,
    Item,
    MetadataExtraction,
    ProcessingJob,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    User,
    WorkingVersion,
)
from sqlalchemy import select

client = TestClient(app)


def _assistant_item() -> tuple[str, str]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        item = Item(
            part_number=f"PFAI-{int(uuid.uuid4().hex[:10], 16)}",
            name="Assistant Motor Housing",
            description="A compact motor housing used for assistant tests.",
            category="Housing",
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
            metadata_json={},
            derived_metadata_json={},
            state="READY",
            metadata_status="READY",
            created_by=user.id,
        )
        db.add(revision)
        db.flush()
        extraction = MetadataExtraction(
            revision_id=revision.id,
            schema_version="part-metadata-v1",
            worker_type="test",
            worker_version="1",
            source_system="FreeCAD",
            status="READY",
            extracted_at=datetime.now(UTC),
            metadata_json={},
            derived_json={},
            kind="PART",
            material="Aluminum 6061-T6",
            bbox_x_mm=100,
            bbox_y_mm=80,
            bbox_z_mm=50,
            bbox_min_mm=50,
            bbox_mid_mm=80,
            bbox_max_mm=100,
            volume_mm3=1000,
            surface_area_mm2=500,
        )
        db.add(extraction)
        db.flush()
        revision.current_metadata_extraction_id = extraction.id
        item.current_revision_id = revision.id
        db.commit()
        return str(item.id), str(revision.id)


def _comparison_item() -> tuple[str, str, str]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        item = Item(
            part_number=f"PF-COMPARE-{uuid.uuid4().hex[:8]}",
            name="Revision Comparison Housing",
            part_type="PART",
            created_by=user.id,
        )
        db.add(item)
        db.flush()
        created = datetime.now(UTC) - timedelta(days=2)
        revision_b = Revision(
            part_id=item.id,
            revision_code="B",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={"properties": {"material": "Aluminum"}, "depth_mm": 45},
            derived_metadata_json={},
            state="READY",
            metadata_status="READY",
            created_by=user.id,
            created_at=created,
        )
        revision_c = Revision(
            part_id=item.id,
            revision_code="C",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={
                "properties": {"material": "Aluminum"},
                "depth_mm": 48,
                "change_reason": "Increase rear-wall thickness",
            },
            derived_metadata_json={},
            state="READY_WITH_WARNINGS",
            metadata_status="READY",
            created_by=user.id,
            created_at=created + timedelta(days=1),
        )
        db.add_all([revision_b, revision_c])
        db.flush()
        version_b = WorkingVersion(
            revision_id=revision_b.id,
            version_number=1,
            state="READY",
            metadata_json=revision_b.metadata_json,
            created_by=user.id,
        )
        version_c = WorkingVersion(
            revision_id=revision_c.id,
            version_number=1,
            state="READY_WITH_WARNINGS",
            metadata_json=revision_c.metadata_json,
            created_by=user.id,
        )
        db.add_all([version_b, version_c])
        db.flush()
        revision_b.current_working_version_id = version_b.id
        revision_c.current_working_version_id = version_c.id
        db.add_all(
            [
                ArtifactFile(
                    revision_id=revision_b.id,
                    working_version_id=version_b.id,
                    kind="NATIVE",
                    storage_key=f"assistant/{uuid.uuid4()}/housing.FCStd",
                    original_filename="housing.FCStd",
                    mime_type="application/octet-stream",
                    byte_size=10,
                    sha256="a" * 64,
                    state="VERIFIED",
                ),
                ArtifactFile(
                    revision_id=revision_c.id,
                    working_version_id=version_c.id,
                    kind="NATIVE",
                    storage_key=f"assistant/{uuid.uuid4()}/housing.FCStd",
                    original_filename="housing.FCStd",
                    mime_type="application/octet-stream",
                    byte_size=12,
                    sha256="b" * 64,
                    state="VERIFIED",
                ),
            ]
        )
        item.current_revision_id = revision_c.id
        db.commit()
        return str(item.id), str(revision_b.id), str(revision_c.id)


def _assembly_item() -> tuple[str, str, str, str, str]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        component = Item(
            part_number=f"PF-COMP-{uuid.uuid4().hex[:8]}",
            name="Selected Bearing",
            part_type="PART",
            created_by=user.id,
        )
        assembly = Item(
            part_number=f"PF-ASM-{uuid.uuid4().hex[:8]}",
            name="Motor Assembly",
            part_type="ASSEMBLY",
            created_by=user.id,
        )
        db.add_all([component, assembly])
        db.flush()
        component_revision = Revision(
            part_id=component.id,
            revision_code="A",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={},
            state="READY",
            metadata_status="READY",
            created_by=user.id,
        )
        assembly_revision = Revision(
            part_id=assembly.id,
            revision_code="B",
            cad_system="FreeCAD",
            native_format="FCStd",
            metadata_json={},
            state="READY",
            metadata_status="READY",
            created_by=user.id,
        )
        db.add_all([component_revision, assembly_revision])
        db.flush()
        component_version = WorkingVersion(
            revision_id=component_revision.id,
            version_number=1,
            state="READY",
            metadata_json={},
            created_by=user.id,
        )
        assembly_version = WorkingVersion(
            revision_id=assembly_revision.id,
            version_number=1,
            state="READY",
            metadata_json={},
            created_by=user.id,
        )
        db.add_all([component_version, assembly_version])
        db.flush()
        component_revision.current_working_version_id = component_version.id
        assembly_revision.current_working_version_id = assembly_version.id
        component.current_revision_id = component_revision.id
        assembly.current_revision_id = assembly_revision.id
        snapshot = RevisionContentSnapshot(
            revision_id=assembly_revision.id,
            sequence=1,
            commit_id=uuid.uuid4(),
            created_by=user.id,
        )
        db.add(snapshot)
        db.flush()
        bom_view = BomViewRevision(
            revision_id=assembly_revision.id,
            snapshot_id=snapshot.id,
            sequence=1,
            created_by=user.id,
        )
        db.add(bom_view)
        db.flush()
        occurrence = BomLine(
            bom_view_revision_id=bom_view.id,
            parent_revision_id=assembly_revision.id,
            child_revision_id=component_revision.id,
            occurrence_key=f"bearing-{uuid.uuid4().hex}",
            quantity=2,
            sequence=0,
        )
        db.add(occurrence)
        db.commit()
        return (
            str(assembly.id),
            str(assembly_revision.id),
            str(component.id),
            str(component_revision.id),
            str(occurrence.id),
        )


def test_assistant_summary_creates_owned_auditable_conversation() -> None:
    item_id, revision_id = _assistant_item()
    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers={**AUTH, "X-Request-ID": "assistant-summary-test"},
        json={
            "message": "Summarize this part",
            "context": {
                "workspace": {"page": "item-revision"},
                "item": {"item_id": item_id, "revision_id": revision_id},
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intent"] == "ITEM_SUMMARY"
    assert "Assistant Motor Housing" in body["answer"]
    assert body["trace_id"] == "assistant-summary-test"
    assert body["references"][0]["item_id"] == item_id
    assert body["actions"] == [
        {"type": "OPEN_REVISION", "item_id": item_id, "revision_id": revision_id}
    ]

    stored = client.get(
        f"/api/v1/ai/assistant/conversations/{body['conversation_id']}", headers=AUTH
    )
    assert stored.status_code == 200
    assert [message["role"] for message in stored.json()["messages"]] == [
        "user",
        "assistant",
    ]
    assert stored.json()["messages"][0]["context"]["item"]["item_id"] == item_id

    with SessionLocal() as db:
        conversation = db.get(AiConversation, uuid.UUID(body["conversation_id"]))
        assert conversation is not None
        messages = list(
            db.scalars(select(AiMessage).where(AiMessage.conversation_id == conversation.id))
        )
        execution = db.scalar(
            select(AiToolExecution).where(
                AiToolExecution.message_id.in_([message.id for message in messages])
            )
        )
        assert execution.tool_name == "get_item"
        assert execution.status == "COMPLETED"


def test_assistant_material_follow_up_uses_current_context() -> None:
    item_id, revision_id = _assistant_item()
    first = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Summarize this part",
            "context": {"item": {"item_id": item_id}},
        },
    )
    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "conversation_id": first.json()["conversation_id"],
            "message": "What material is this?",
            "context": {"item": {"item_id": item_id, "revision_id": revision_id}},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["intent"] == "ITEM_METADATA"
    assert "Aluminum 6061-T6" in response.json()["answer"]


def test_assistant_rejects_arbitrary_context_and_unsupported_intent() -> None:
    item_id, _ = _assistant_item()
    invalid = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Summarize this part",
            "context": {"item": {"item_id": item_id, "secret": "not-allowed"}},
        },
    )
    assert invalid.status_code == 422

    unsupported = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Delete this part",
            "context": {"item": {"item_id": item_id}},
        },
    )
    assert unsupported.status_code == 422


def test_assistant_requires_authentication() -> None:
    response = client.post(
        "/api/v1/ai/assistant/messages",
        json={"message": "Summarize this part", "context": {}},
    )
    assert response.status_code == 401


def test_assistant_lists_revision_history() -> None:
    item_id, revision_b_id, revision_c_id = _comparison_item()
    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Show revision history",
            "context": {"item": {"item_id": item_id, "revision_id": revision_c_id}},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intent"] == "REVISION_HISTORY"
    assert "Revision B" in body["answer"]
    assert "Revision C" in body["answer"]
    assert {row["revision_id"] for row in body["references"]} == {
        revision_b_id,
        revision_c_id,
    }


def test_assistant_compares_revisions_with_grounded_differences() -> None:
    item_id, revision_b_id, revision_c_id = _comparison_item()
    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "What changed from B to C?",
            "context": {"item": {"item_id": item_id, "revision_id": revision_c_id}},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intent"] == "COMPARE_REVISIONS"
    assert "1 metadata field(s) changed" in body["answer"]
    assert "1 file(s)" in body["answer"]
    assert "Increase rear-wall thickness" in body["answer"]

    with SessionLocal() as db:
        execution = db.scalar(
            select(AiToolExecution).where(
                AiToolExecution.message_id == uuid.UUID(body["message_id"])
            )
        )
        assert execution.tool_name == "compare_revisions"
        assert execution.result_summary_json["from_revision_id"] == revision_b_id
        assert execution.result_summary_json["to_revision_id"] == revision_c_id
        assert execution.result_summary_json["file_changes"] == [
            {"kind": "NATIVE", "filename": "housing.FCStd", "change": "modified"}
        ]


def test_phase56_assistant_explains_stored_comparison_with_change_citations() -> None:
    item_id, revision_b_id, revision_c_id = _comparison_item()
    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Explain why revision B changed to C",
            "context": {"item": {"item_id": item_id, "revision_id": revision_c_id}},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intent"] == "COMPARE_REVISIONS"
    assert "[change:" in body["answer"]

    with SessionLocal() as db:
        execution = db.scalar(
            select(AiToolExecution).where(
                AiToolExecution.message_id == uuid.UUID(body["message_id"])
            )
        )
        assert execution.tool_name == "explain_revision_comparison"
        assert execution.result_summary_json["comparison_id"]
        assert execution.result_summary_json["explanation_id"]
        assert execution.result_summary_json["cited_change_keys"]
        assert execution.result_summary_json["from_revision_id"] == revision_b_id
        assert execution.result_summary_json["to_revision_id"] == revision_c_id


def test_assistant_summarizes_bom_and_where_used() -> None:
    assembly_id, assembly_revision_id, component_id, component_revision_id, _ = _assembly_item()
    bom = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "What components are in this assembly?",
            "context": {
                "item": {"item_id": assembly_id, "revision_id": assembly_revision_id},
                "assembly": {"item_id": assembly_id, "revision_id": assembly_revision_id},
            },
        },
    )
    assert bom.status_code == 201, bom.text
    assert bom.json()["intent"] == "BOM_QUERY"
    assert "Selected Bearing" in bom.json()["answer"]
    assert "quantity 2" in bom.json()["answer"]

    used = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Where is this used?",
            "context": {"item": {"item_id": component_id, "revision_id": component_revision_id}},
        },
    )
    assert used.status_code == 201, used.text
    assert used.json()["intent"] == "WHERE_USED"
    assert "Motor Assembly" in used.json()["answer"]
    assert used.json()["references"][0]["type"] == "ASSEMBLY"


def test_assistant_resolves_selected_occurrence_inside_current_assembly() -> None:
    assembly_id, assembly_revision_id, component_id, component_revision_id, occurrence_id = (
        _assembly_item()
    )
    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "What is this selected component?",
            "context": {
                "item": {"item_id": assembly_id, "revision_id": assembly_revision_id},
                "assembly": {
                    "item_id": assembly_id,
                    "revision_id": assembly_revision_id,
                    "selected_occurrence_id": occurrence_id,
                },
            },
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["intent"] == "SELECTED_COMPONENT"
    assert "Selected Bearing" in response.json()["answer"]
    assert "quantity is 2" in response.json()["answer"]
    assert response.json()["actions"] == [
        {
            "type": "OPEN_REVISION",
            "item_id": component_id,
            "revision_id": component_revision_id,
        }
    ]


def _add_assistant_embedding(item_id: str, values: list[float]) -> str:
    with SessionLocal() as db:
        item = db.get(Item, uuid.UUID(item_id))
        revision = db.get(Revision, item.current_revision_id)
        db.add(
            AiDocument(
                item_id=item.id,
                revision_id=revision.id,
                chunk_key="part-metadata-v1",
                title=item.name,
                content=item.description or item.name,
                metadata_json={"kind": "part-metadata"},
                embedding=values,
                embedding_model="phase34-test",
                content_sha256=uuid.uuid4().hex * 2,
                indexed_at=datetime.now(UTC),
            )
        )
        db.commit()
        return str(revision.id)


def test_assistant_runs_natural_language_engineering_search(monkeypatch) -> None:
    def deterministic_interpreter(self, payload, previous_request=None):
        request, warnings, confidence = deterministic_interpret(payload, previous_request)
        return request, warnings, confidence, "deterministic"

    monkeypatch.setattr(AISearchInterpreter, "interpret", deterministic_interpreter)
    context_item_id, context_revision_id = _assistant_item()
    target_item_id, _ = _assistant_item()
    with SessionLocal() as db:
        target = db.get(Item, uuid.UUID(target_item_id))
        query = f"Find {target.part_number}"

    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": query,
            "context": {
                "item": {
                    "item_id": context_item_id,
                    "revision_id": context_revision_id,
                }
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intent"] == "ENGINEERING_SEARCH"
    assert target_item_id in [row["item_id"] for row in body["references"]]
    assert all(row["type"] == "SEARCH_RESULT" for row in body["references"])

    with SessionLocal() as db:
        execution = db.scalar(
            select(AiToolExecution).where(
                AiToolExecution.message_id == uuid.UUID(body["message_id"])
            )
        )
        assert execution.tool_name == "search_parts"
        assert execution.result_summary_json["interpretation"]["filters"]["identifiers"] == [
            target.part_number
        ]


def test_assistant_finds_similar_parts_from_current_revision() -> None:
    source_item_id, source_revision_id = _assistant_item()
    neighbor_item_id, _ = _assistant_item()
    generator = random.Random(uuid.uuid4().int)
    vector = [generator.uniform(-1.0, 1.0) for _ in range(1536)]
    _add_assistant_embedding(source_item_id, vector)
    _add_assistant_embedding(neighbor_item_id, vector)

    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Find similar parts",
            "context": {
                "item": {
                    "item_id": source_item_id,
                    "revision_id": source_revision_id,
                }
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intent"] == "SIMILAR_PART_SEARCH"
    assert neighbor_item_id in [row["item_id"] for row in body["references"]]
    assert source_item_id not in [row["item_id"] for row in body["references"]]


def _failed_validation_job(revision_id: str) -> str:
    with SessionLocal() as db:
        revision = db.get(Revision, uuid.UUID(revision_id))
        source_file = db.scalar(
            select(ArtifactFile).where(
                ArtifactFile.revision_id == revision.id,
                ArtifactFile.kind == "NATIVE",
            )
        )
        container = Artifact(
            artifact_type="CAD_MODEL",
            name=source_file.original_filename,
            classification="AUTHORITATIVE",
            source="USER",
            state="READY",
            created_by=revision.created_by,
        )
        db.add(container)
        db.flush()
        source_file.artifact_id = container.id
        source_file.reference_name = "MODEL"
        source_file.role = "PRIMARY"
        relation = RevisionArtifactRelation(
            revision_id=revision.id,
            artifact_id=container.id,
            relation_type="SPECIFICATION",
            sequence=0,
            is_primary=True,
            created_by=revision.created_by,
        )
        db.add(relation)
        snapshot = RevisionContentSnapshot(
            revision_id=revision.id,
            sequence=1,
            commit_id=uuid.uuid4(),
            created_by=revision.created_by,
        )
        db.add(snapshot)
        db.flush()
        db.add(
            RevisionContentSnapshotArtifact(
                snapshot_id=snapshot.id,
                relation_id=relation.id,
                sequence=0,
            )
        )
        job = ProcessingJob(
            revision_id=revision.id,
            working_version_id=revision.current_working_version_id,
            snapshot_id=snapshot.id,
            type="FCSTD_TO_MODELRYX",
            state="FAILED",
            stage="FAILED",
            progress_percent=70,
            diagnostics_json={
                "errors": [
                    {
                        "stage": "VALIDATING",
                        "message": "GLB triangle index is out of range",
                    }
                ]
            },
            attempt_count=3,
            idempotency_key=f"phase35:{uuid.uuid4()}",
            error_detail="GLB triangle index is out of range",
            finished_at=datetime.now(UTC),
        )
        db.add(job)
        db.commit()
        return str(job.id)


def test_assistant_diagnoses_validation_failure_from_recorded_evidence() -> None:
    item_id, _, revision_id = _comparison_item()
    job_id = _failed_validation_job(revision_id)
    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Why did validation fail and which file caused the problem?",
            "context": {
                "item": {"item_id": item_id, "revision_id": revision_id},
                "validation": {"job_id": job_id},
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intent"] == "VALIDATION_DIAGNOSIS"
    assert "GLB triangle index is out of range" in body["answer"]
    assert "housing.FCStd" in body["answer"]
    assert "Recommended action" in body["answer"]
    assert body["references"][0]["type"] == "VALIDATION"

    with SessionLocal() as db:
        execution = db.scalar(
            select(AiToolExecution).where(
                AiToolExecution.message_id == uuid.UUID(body["message_id"])
            )
        )
        assert execution.tool_name == "get_validation_result"
        assert execution.result_summary_json["job_id"] == job_id
        assert execution.result_summary_json["source_files"] == ["housing.FCStd"]


def test_assistant_rejects_validation_job_from_another_revision() -> None:
    item_id, _, revision_id = _comparison_item()
    other_item_id, _, other_revision_id = _comparison_item()
    job_id = _failed_validation_job(other_revision_id)
    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Why did validation fail?",
            "context": {
                "item": {"item_id": item_id, "revision_id": revision_id},
                "validation": {"job_id": job_id},
            },
        },
    )
    assert response.status_code == 404
    assert other_item_id != item_id


def test_assistant_highlights_matching_readable_occurrences() -> None:
    assembly_id, assembly_revision_id, _, _, occurrence_id = _assembly_item()
    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Highlight all bearings",
            "context": {
                "item": {"item_id": assembly_id, "revision_id": assembly_revision_id},
                "assembly": {
                    "item_id": assembly_id,
                    "revision_id": assembly_revision_id,
                },
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intent"] == "VIEWER_HIGHLIGHT"
    assert body["actions"] == [{"type": "HIGHLIGHT_OCCURRENCES", "occurrence_ids": [occurrence_id]}]
    assert "Selected Bearing" in body["references"][0]["label"]

    isolated = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Isolate bearing components",
            "context": {
                "item": {"item_id": assembly_id, "revision_id": assembly_revision_id},
                "assembly": {
                    "item_id": assembly_id,
                    "revision_id": assembly_revision_id,
                },
            },
        },
    )
    assert isolated.status_code == 201, isolated.text
    assert isolated.json()["intent"] == "VIEWER_ISOLATE"
    assert isolated.json()["actions"] == [
        {"type": "ISOLATE_OCCURRENCES", "occurrence_ids": [occurrence_id]}
    ]


def test_assistant_selected_component_where_used_uses_component_revision() -> None:
    assembly_id, assembly_revision_id, _, _, occurrence_id = _assembly_item()
    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Where else is this selected component used?",
            "context": {
                "item": {"item_id": assembly_id, "revision_id": assembly_revision_id},
                "assembly": {
                    "item_id": assembly_id,
                    "revision_id": assembly_revision_id,
                    "selected_occurrence_id": occurrence_id,
                },
            },
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["intent"] == "SELECTED_WHERE_USED"
    assert "Motor Assembly" in response.json()["answer"]


def _modified_assembly_item() -> tuple[str, str, str]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        component = Item(
            part_number=f"PF-MOD-COMP-{uuid.uuid4().hex[:8]}",
            name="Modified Bracket",
            part_type="PART",
            created_by=user.id,
        )
        assembly = Item(
            part_number=f"PF-MOD-ASM-{uuid.uuid4().hex[:8]}",
            name="Modified Assembly",
            part_type="ASSEMBLY",
            created_by=user.id,
        )
        db.add_all([component, assembly])
        db.flush()
        component_revisions = [
            Revision(
                part_id=component.id,
                revision_code=code,
                cad_system="FreeCAD",
                native_format="FCStd",
                metadata_json={},
                state="READY",
                metadata_status="READY",
                created_by=user.id,
            )
            for code in ("A", "B")
        ]
        assembly_revisions = [
            Revision(
                part_id=assembly.id,
                revision_code=code,
                cad_system="FreeCAD",
                native_format="FCStd",
                metadata_json={},
                state="READY",
                metadata_status="READY",
                created_by=user.id,
                created_at=datetime.now(UTC) + timedelta(minutes=index),
            )
            for index, code in enumerate(("A", "B"))
        ]
        db.add_all(component_revisions + assembly_revisions)
        db.flush()
        component_versions = [
            WorkingVersion(
                revision_id=row.id,
                version_number=1,
                state="READY",
                metadata_json={},
                created_by=user.id,
            )
            for row in component_revisions
        ]
        assembly_versions = [
            WorkingVersion(
                revision_id=row.id,
                version_number=1,
                state="READY",
                metadata_json={},
                created_by=user.id,
            )
            for row in assembly_revisions
        ]
        db.add_all(component_versions + assembly_versions)
        db.flush()
        for row, version in zip(component_revisions, component_versions, strict=True):
            row.current_working_version_id = version.id
        for row, version in zip(assembly_revisions, assembly_versions, strict=True):
            row.current_working_version_id = version.id
        component.current_revision_id = component_revisions[1].id
        assembly.current_revision_id = assembly_revisions[1].id
        snapshots = [
            RevisionContentSnapshot(
                revision_id=row.id,
                sequence=1,
                commit_id=uuid.uuid4(),
                created_by=user.id,
            )
            for row in assembly_revisions
        ]
        db.add_all(snapshots)
        db.flush()
        views = [
            BomViewRevision(
                revision_id=assembly_revisions[index].id,
                snapshot_id=snapshots[index].id,
                sequence=1,
                created_by=user.id,
            )
            for index in range(2)
        ]
        db.add_all(views)
        db.flush()
        occurrences = [
            BomLine(
                bom_view_revision_id=views[index].id,
                parent_revision_id=assembly_revisions[index].id,
                child_revision_id=component_revisions[index].id,
                occurrence_key="bracket-1",
                quantity=1,
                sequence=0,
            )
            for index in range(2)
        ]
        db.add_all(occurrences)
        db.commit()
        return str(assembly.id), str(assembly_revisions[1].id), str(occurrences[1].id)


def test_assistant_highlights_components_modified_since_previous_revision() -> None:
    assembly_id, revision_id, occurrence_id = _modified_assembly_item()
    response = client.post(
        "/api/v1/ai/assistant/messages",
        headers=AUTH,
        json={
            "message": "Show the modified components",
            "context": {
                "item": {"item_id": assembly_id, "revision_id": revision_id},
                "assembly": {"item_id": assembly_id, "revision_id": revision_id},
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intent"] == "VIEWER_MODIFIED_COMPONENTS"
    assert body["actions"] == [{"type": "HIGHLIGHT_OCCURRENCES", "occurrence_ids": [occurrence_id]}]
    assert "since assembly revision A" in body["answer"]


def test_assistant_answers_hole_question_from_revision_metadata() -> None:
    item_id, revision_id = _assistant_item()
    with SessionLocal() as db:
        revision = db.get(Revision, uuid.UUID(revision_id))
        extraction = db.get(MetadataExtraction, revision.current_metadata_extraction_id)
        extraction.hole_count = 4
        extraction.metadata_json = {
            "quality": {"features": "complete"},
            "features": {"holes": [{"diameter_mm": 6, "through": True, "count": 4}]},
        }
        db.commit()
    response = client.post("/api/v1/ai/assistant/messages", headers=AUTH, json={
        "message": "Is there a hole in the part?",
        "context": {"item": {"item_id": item_id, "revision_id": revision_id}},
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intent"] == "ITEM_METADATA"
    assert "4 hole(s)" in body["answer"]
    assert "6 mm" in body["answer"]
    assert body["references"][0]["revision_id"] == revision_id
    with SessionLocal() as db:
        execution = db.scalar(select(AiToolExecution).where(
            AiToolExecution.message_id == uuid.UUID(body["message_id"]),
        ))
        assert execution.result_summary_json["hole_presence"] == "present"


def test_assistant_missing_assembly_hole_count_is_unknown() -> None:
    item_id, revision_id = _assistant_item()
    with SessionLocal() as db:
        item = db.get(Item, uuid.UUID(item_id))
        item.part_type = "ASSEMBLY"
        revision = db.get(Revision, uuid.UUID(revision_id))
        extraction = db.get(MetadataExtraction, revision.current_metadata_extraction_id)
        extraction.metadata_json = {"quality": {"features": "complete"},
                                    "features": {"items": [], "summary": {}}}
        db.commit()
    response = client.post("/api/v1/ai/assistant/messages", headers=AUTH, json={
        "message": "Is there a hole in the part?",
        "context": {"item": {"item_id": item_id, "revision_id": revision_id}},
    })
    assert response.status_code == 201, response.text
    answer = response.json()["answer"]
    assert "unknown" in answer
    assert "assembly-level" in answer


def test_assistant_answers_multiple_feature_questions_independently() -> None:
    item_id, revision_id = _assistant_item()
    with SessionLocal() as db:
        revision = db.get(Revision, uuid.UUID(revision_id))
        extraction = db.get(MetadataExtraction, revision.current_metadata_extraction_id)
        extraction.fillet_count = 3
        extraction.chamfer_count = 0
        extraction.pocket_count = None
        extraction.metadata_json = {"quality": {"features": "complete"}}
        db.commit()
    response = client.post("/api/v1/ai/assistant/messages", headers=AUTH, json={
        "message": "How many fillets, chamfers, and pockets are there?",
        "context": {"item": {"item_id": item_id, "revision_id": revision_id}},
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intent"] == "ITEM_METADATA"
    assert "3 fillet(s)" in body["answer"]
    assert "zero chamfers" in body["answer"]
    assert "has pockets from the current metadata" in body["answer"]
    assert body["references"][0]["revision_id"] == revision_id
    with SessionLocal() as db:
        execution = db.scalar(select(AiToolExecution).where(
            AiToolExecution.message_id == uuid.UUID(body["message_id"]),
        ))
        assert execution.result_summary_json["fillet_presence"] == "present"
        assert execution.result_summary_json["chamfer_presence"] == "absent"
        assert execution.result_summary_json["pocket_presence"] == "unknown"
