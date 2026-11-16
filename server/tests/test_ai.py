import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException
from modelryx.api import ai as ai_api
from modelryx.core.config import Settings
from modelryx.duplicate_detection.search_document import build_semantic_text
from modelryx.metadata.derived import build_derived_metadata
from modelryx.metadata.service import normalize_search_properties
from modelryx.models import Item, Revision
from modelryx.services.ai import (
    AiGateway,
    _lexical_terms,
    build_revision_documents,
    index_revision,
)


def revision_with_bom() -> tuple[Item, Revision]:
    item_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    item = Item(
        id=item_id,
        part_number="PART-AI-001",
        name="Motor bracket",
        description="Supports the drive motor",
        category="Bracket",
        status="ACTIVE",
        part_type="PART",
        created_by=uuid.uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    revision = Revision(
        id=revision_id,
        part_id=item_id,
        revision_code="B",
        cad_system="FreeCAD",
        native_format="FCStd",
        metadata_json={"schema_version": 1, "properties": {"finish": "anodized"}},
        derived_metadata_json={
            "schemaVersion": 1,
            "partMetadata": {
                "schema": "modelryx.part-metadata",
                "schema_version": "1.0",
                "identity": {"name": "Bracket Body", "kind": "part"},
                "geometry": {
                    "bounding_box": {"x_mm": 120, "y_mm": 80, "z_mm": 12},
                    "shape": {"volume_mm3": 45000, "surface_area_mm2": 22000},
                },
                "quality": {"geometry": "complete"},
                "physical": {"mass_kg": 0.12, "density_kg_m3": 2700},
                "features": {
                    "source": "history",
                    "summary": {"hole_count": 4},
                    "holes": [{"diameter_mm": 6, "through": True, "count": 4}],
                },
            },
            "rootOccurrenceId": "root",
            "items": [
                {
                    "name": "Bracket Body",
                    "nodeType": "body",
                    "occurrenceId": "body-1",
                    "parentOccurrenceId": "root",
                    "material": "Aluminum 6061",
                    "properties": {
                        "boundingBoxMm": {"x": 120, "y": 80, "z": 12},
                        "volumeMm3": 45000,
                    },
                    "source": {
                        "objectLabel": "Bracket Body",
                        "typeId": "PartDesign::Body",
                    },
                }
            ],
        },
        state="READY",
        created_by=item.created_by,
        created_at=datetime.now(UTC),
    )
    return item, revision


def test_revision_documents_include_summary_geometry_and_provenance() -> None:
    item, revision = revision_with_bom()
    documents = build_revision_documents(item, revision)
    assert [document["chunk_key"] for document in documents] == [
        "revision-summary",
        "part-metadata-v1",
        "physical-properties",
        "features-parameters",
        "extraction-quality",
        "bom-node:body-1",
    ]
    assert "Aluminum 6061" in documents[0]["content"]
    assert "boundingBoxMm=x=120, y=80, z=12" in documents[5]["content"]
    assert "PART-AI-001" in documents[2]["content"]
    assert "Volume: approximately 45000 cubic millimeters." in documents[1]["content"]
    assert documents[1]["metadata"]["searchDocumentVersion"] == "part-metadata-semantic-v15"
    assert "worker_version" not in documents[1]["content"]
    assert "mass_kg=0.12" in documents[2]["content"]
    assert "diameter_mm=6" in documents[3]["content"]


def test_revision_documents_qualify_unverified_array_geometry_without_mutation() -> None:
    item, revision = revision_with_bom()
    metadata = revision.derived_metadata_json["partMetadata"]
    metadata["native"] = {
        "extraction_diagnostics": [{"code": "LINK_ARRAY_ELEMENT_ACTIVITY_UNKNOWN"}]
    }
    original = json.dumps(metadata, sort_keys=True)
    documents = {
        document["chunk_key"]: document for document in build_revision_documents(item, revision)
    }
    warning = "Array geometry or element activity is unverified"
    assert warning in documents["revision-summary"]["content"]
    assert warning in documents["physical-properties"]["content"]
    assert warning in documents["bom-node:body-1"]["content"]
    assert "Volume: approximately" not in documents["part-metadata-v1"]["content"]
    assert "Mass:" not in documents["part-metadata-v1"]["content"]
    assert json.dumps(metadata, sort_keys=True) == original


def test_semantic_text_excludes_suppressed_freecad_features() -> None:
    text = build_semantic_text(
        {
            "identity": {"name": "Bracket", "kind": "part"},
            "features": {
                "items": [
                    {
                        "name": "Active pocket",
                        "type": "POCKET",
                        "properties": {"depth_mm": 8, "suppressed": False},
                    },
                    {
                        "name": "Obsolete pocket",
                        "type": "POCKET",
                        "properties": {"depth_mm": 20, "suppressed": True},
                    },
                ]
            },
        }
    )

    assert "Active pocket" in text
    assert "depth mm=8" in text
    assert "Obsolete pocket" not in text
    assert "depth mm=20" not in text


def test_semantic_text_includes_extended_neutral_primitive_dimensions() -> None:
    text = build_semantic_text(
        {
            "identity": {"name": "Primitive fixture", "kind": "part"},
            "features": {
                "items": [
                    {
                        "name": "Ellipsoid",
                        "type": "BOSS",
                        "properties": {"radius3_mm": 4, "angle3_deg": 270},
                    },
                    {
                        "name": "Prism",
                        "type": "BOSS",
                        "properties": {
                            "side_count": 6,
                            "first_angle_deg": 15,
                            "second_angle_deg": 75,
                        },
                    },
                    {
                        "name": "Wedge",
                        "type": "BOSS",
                        "properties": {"x_max_mm": 12, "z2_max_mm": 5},
                    },
                ]
            },
        }
    )

    assert "radius3 mm=4" in text
    assert "first angle deg=15" in text
    assert "second angle deg=75" in text
    assert "x max mm=12" in text
    assert "z2 max mm=5" in text


def test_semantic_text_includes_neutral_draft_array_pattern_dimensions() -> None:
    text = build_semantic_text(
        {
            "identity": {"name": "Array fixture", "kind": "part"},
            "features": {
                "items": [
                    {
                        "name": "Orthogonal Array",
                        "type": "PATTERN",
                        "properties": {
                            "pattern_kind": "ortho",
                            "occurrence_count": 6,
                            "count_x": 2,
                            "count_y": 3,
                            "count_z": 1,
                            "interval_x_mm": [10, 0, 0],
                            "interval_y_mm": [0, 12, 0],
                        },
                    },
                    {
                        "name": "Path Array",
                        "type": "PATTERN",
                        "properties": {
                            "pattern_kind": "path",
                            "occurrence_count": 5,
                            "align": True,
                            "align_mode": "Original",
                            "start_offset_mm": 1,
                            "end_offset_mm": 2,
                            "spine_refs": ["ArrayPath"],
                        },
                    },
                    {
                        "name": "Point Array",
                        "type": "PATTERN",
                        "properties": {
                            "pattern_kind": "point",
                            "occurrence_count": 3,
                            "point_refs": ["ArrayPoints"],
                        },
                    },
                    {
                        "name": "Circular Array",
                        "type": "PATTERN",
                        "properties": {
                            "pattern_kind": "circular",
                            "occurrence_count": 5,
                            "circle_count": 2,
                            "polar_count": 5,
                            "symmetry": 1,
                            "radial_distance_mm": 15,
                            "tangential_distance_mm": 20,
                            "axis": [0, 1, 0],
                            "center_mm": [4, 5, 6],
                        },
                    },
                ]
            },
        }
    )

    assert "type=PATTERN" in text
    assert "pattern kind=ortho" in text
    assert "occurrence count=6" in text
    assert "count x=2" in text
    assert "interval x mm=[10, 0, 0]" in text
    assert "pattern kind=path" in text
    assert "align mode=Original" in text
    assert "start offset mm=1" in text
    assert "references=ArrayPath" in text
    assert "pattern kind=point" in text
    assert "references=ArrayPoints" in text
    assert "pattern kind=circular" in text
    assert "circle count=2" in text
    assert "radial distance mm=15" in text
    assert "tangential distance mm=20" in text


def test_semantic_text_includes_freecad_design_intent_and_references() -> None:
    text = build_semantic_text(
        {
            "identity": {"name": "Loft fixture", "kind": "part"},
            "features": {
                "items": [
                    {
                        "name": "Native Loft",
                        "type": "LOFT",
                        "properties": {
                            "section_refs": ["Lower Section", "Upper Section"],
                            "solid": True,
                            "ruled": True,
                            "closed": False,
                            "linearize": True,
                            "max_degree": 3,
                            "support_refs": ["XY Plane"],
                            "transformation_refs": ["Linear Pattern"],
                        },
                    }
                ]
            },
        }
    )

    assert "type=LOFT" in text
    assert "solid=True" in text
    assert "ruled=True" in text
    assert "closed=False" in text
    assert "linearize=True" in text
    assert "max degree=3" in text
    assert "references=Linear Pattern, Lower Section, Upper Section, XY Plane" in text


def test_gateway_sends_openai_compatible_embedding_request(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return json.dumps({"data": [{"index": 0, "embedding": [0.0] * 1536}]}).encode()

    def urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    gateway = AiGateway(
        Settings(
            ai_gateway_base_url="http://gateway.test/v1",
            ai_gateway_api_key="secret",
            ai_embedding_model="embedding-model",
        )
    )
    vectors = gateway.embed(["motor bracket"])
    assert len(vectors[0]) == 1536
    assert captured["url"] == "http://gateway.test/v1/embeddings"
    assert captured["body"]["dimensions"] == 1536


def test_lexical_terms_remove_natural_language_search_filler() -> None:
    assert _lexical_terms("Find item which contains a box") == ["box"]
    assert _lexical_terms("Parts with stainless steel material") == [
        "stainless",
        "steel",
        "material",
    ]


def test_search_metadata_promotes_cross_cad_properties_and_derived_facts() -> None:
    metadata = revision_with_bom()[1].derived_metadata_json["partMetadata"]
    metadata["properties"] = {"Stock Number": "STK-10", "Surface Finish": "anodized"}
    metadata["native"] = {"active_model_state": "Machined"}
    metadata["drawings"] = [{
        "entities": [
            {"type": "DIMENSION"},
            {"type": "GDT", "symbol_kind": "geometric_tolerance"},
            {"type": "DIMENSION", "tolerance": 0.05},
        ]
    }]
    metadata["assembly"] = {"instance_count": 4}
    normalized = normalize_search_properties(metadata)
    derived = build_derived_metadata(normalized)
    assert normalized["properties"]["stock_number"] == "STK-10"
    assert normalized["properties"]["finish"] == "anodized"
    assert normalized["properties"]["configuration"] == "Machined"
    assert derived["density_kg_m3"] == 2700
    assert derived["hole_diameters_mm"] == [6.0]
    assert derived["drawing_count"] == 1
    assert derived["tolerance_count"] == 2
    assert derived["instance_count"] == 4


def test_ai_chunks_cover_search_operations_across_cad_systems() -> None:
    for cad_system in ("FreeCAD", "Example CAD", "Other CAD"):
        item, revision = revision_with_bom()
        revision.cad_system = cad_system
        metadata = revision.derived_metadata_json["partMetadata"]
        metadata["drawings"] = [{"entities": [{"type": "TOLERANCE", "text": "±0.05"}]}]
        metadata["native"] = {
            "parameters": [{"name": "HoleDiameter", "value": 6, "unit": "mm"}],
            "occurrences": [{"id": "occ-1", "part_number": "CHILD-1"}],
            "bom_views": [{"name": "Structured", "rows": []}],
            "assembly_joints": [{"name": "Pivot", "type": "Revolute"}],
            "configurations": {
                "active_values": ["Machined"],
                "variants": ["Default", "Machined"],
            },
            "reference_geometry": [{"name": "MachiningLCS", "kind": "coordinate_system"}],
        }
        keys = {document["chunk_key"] for document in build_revision_documents(item, revision)}
        assert {
            "physical-properties", "features-parameters", "assembly-bom",
            "drawings", "extraction-quality",
        } <= keys


def test_index_keeps_lexical_documents_when_embedding_gateway_fails(monkeypatch) -> None:
    item, revision = revision_with_bom()
    db = Mock()
    db.get.return_value = item
    monkeypatch.setattr(
        AiGateway,
        "embed",
        lambda *_: (_ for _ in ()).throw(HTTPException(status_code=502, detail="offline")),
    )
    count = index_revision(
        db,
        revision,
        Settings(
            ai_gateway_base_url="http://gateway.invalid/v1",
            ai_embedding_model="embedding-model",
        ),
    )
    assert count == 6
    assert db.add.call_count == 6
    assert all(call.args[0].embedding is None for call in db.add.call_args_list)
    db.commit.assert_called_once()


def test_ai_search_sources_include_authorized_navigation_ids(monkeypatch) -> None:
    item_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    document = SimpleNamespace(
        id=uuid.uuid4(), item_id=item_id, revision_id=revision_id,
        title="Revision summary", chunk_key="revision-summary",
        content="Recorded bracket dimensions", metadata_json={},
    )
    source = SimpleNamespace(
        document=document, item_number="PART-001", revision_code="A", score=0.8
    )
    monkeypatch.setattr(ai_api, "retrieve", lambda *_args, **_kwargs: [source])

    result = ai_api.search_ai_documents("bracket", None, None, None)

    assert result[0].item_id == item_id
    assert result[0].revision_id == revision_id
    assert result[0].label == "S1"


def test_revision_summary_uses_engineering_lifecycle_instead_of_processing_state() -> None:
    item, revision = revision_with_bom()
    revision.lifecycle_state = "RELEASED"
    summary = build_revision_documents(item, revision)[0]["content"]
    assert "Lifecycle state: RELEASED" in summary
    assert "Lifecycle state: READY" not in summary
