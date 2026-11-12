import copy
import uuid

import pytest
from modelryx.services.licensed_cad_contracts import (
    nx_document_kind,
    validate_nx_worker_metadata,
    validate_nx_worker_result,
)


def contract():
    identity = str(uuid.uuid4())
    roles = ["native-snapshot", "part-metadata", "model-preview"]
    request = {
        "contractVersion": "1.0",
        "jobId": identity,
        "attempt": 1,
        "cadSystem": "siemens_nx",
        "configuration": None,
        "source": {"sha256": "a" * 64},
        "requiredOutputs": roles,
    }
    result = {
        **{
            key: request[key]
            for key in ("contractVersion", "jobId", "attempt", "cadSystem", "configuration")
        },
        "sourceSha256": "a" * 64,
        "adapterVersion": "test",
        "status": "partial",
        "warnings": ["Optional preview unavailable"],
        "outputs": [
            {
                "role": role,
                "mediaType": "application/json",
                "size": 12,
                "sha256": "b" * 64,
                "downloadUrl": f"/api/v1/jobs/{identity}/outputs/{role}",
                "provenance": {"source_role": "native-snapshot", "generator": "modelryx-nx-core"},
            }
            for role in roles
        ],
    }
    return request, result


def test_accepts_metadata_only_partial_result_without_requiring_preview():
    request, result = contract()
    before = copy.deepcopy(result)
    validate_nx_worker_result(request, result)
    assert result == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("jobId", str(uuid.uuid4())),
        ("attempt", 2),
        ("contractVersion", "2.0"),
        ("cadSystem", "inventor"),
        ("configuration", {"name": "Other"}),
        ("sourceSha256", "c" * 64),
        ("adapterVersion", ""),
    ],
)
def test_rejects_results_from_other_jobs_or_sources(field, value):
    request, result = contract()
    result[field] = value
    with pytest.raises(RuntimeError):
        validate_nx_worker_result(request, result)


@pytest.mark.parametrize(
    "url",
    [
        "https://another-host.example/output",
        "//another-host.example/output",
        "/api/v1/jobs/other/outputs/native-snapshot",
        "/output?redirect=elsewhere",
    ],
)
def test_rejects_foreign_download_paths_before_credentials_are_sent(url):
    request, result = contract()
    result["outputs"][0]["downloadUrl"] = url
    with pytest.raises(RuntimeError, match="download path"):
        validate_nx_worker_result(request, result)


@pytest.mark.parametrize(
    "change",
    ["duplicate", "missing", "private-bundle", "provenance", "hash", "size", "media", "quality"],
)
def test_rejects_invalid_output_contracts(change):
    request, result = contract()
    if change == "duplicate":
        result["outputs"].append(copy.deepcopy(result["outputs"][0]))
    elif change == "missing":
        result["outputs"].pop()
    elif change == "private-bundle":
        result["outputs"][0]["role"] = "component-bundle"
    elif change == "provenance":
        result["outputs"][0]["provenance"] = {}
    elif change == "hash":
        result["outputs"][0]["sha256"] = "invalid"
    elif change == "size":
        result["outputs"][0]["size"] = True
    elif change == "media":
        result["outputs"][0]["mediaType"] = "model/gltf-binary"
    else:
        result["status"] = "complete"
    with pytest.raises(RuntimeError):
        validate_nx_worker_result(request, result)


@pytest.mark.parametrize("change", [None, "filename", "kind", "system", "native-kind"])
def test_binds_native_and_normalized_metadata_to_the_submitted_document(change):
    request = {"documentKind": "assembly", "source": {"rootPath": "package/root.prt"}}
    native = {"source_filename": "root.prt", "document_type": "assembly"}
    metadata = {"source": {"system": "siemens_nx"}, "native": {"document_type": "assembly"}}
    if change == "filename":
        native["source_filename"] = "other.prt"
    elif change == "kind":
        native["document_type"] = "part"
    elif change == "system":
        metadata["source"]["system"] = "inventor"
    elif change == "native-kind":
        metadata["native"]["document_type"] = "drawing"
    if change is None:
        validate_nx_worker_metadata(request, native, metadata)
    else:
        with pytest.raises(RuntimeError):
            validate_nx_worker_metadata(request, native, metadata)


def test_accepts_native_drawing_metadata_and_pdf_without_a_glb():
    request, result = contract()
    request.update(
        {
            "documentKind": "drawing",
            "source": {
                "rootPath": "drawings/qualification.prt",
                "sha256": request["source"]["sha256"],
            },
        }
    )
    native = {"source_filename": "qualification.prt", "document_type": "drawing"}
    metadata = {"source": {"system": "siemens_nx"}, "native": {"document_type": "drawing"}}
    validate_nx_worker_metadata(request, native, metadata)
    result["outputs"].append(
        {
            "role": "drawing-pdf",
            "mediaType": "application/pdf",
            "size": 1234,
            "sha256": "c" * 64,
            "downloadUrl": f"/api/v1/jobs/{request['jobId']}/outputs/drawing-pdf",
            "provenance": {
                "source_role": "authoritative-native",
                "generator": "siemens-nxopen-pdf",
            },
        }
    )
    validate_nx_worker_result(request, result)
    result["outputs"][-1]["mediaType"] = "model/gltf-binary"
    with pytest.raises(RuntimeError, match="drawing-pdf"):
        validate_nx_worker_result(request, result)


def test_drawing_metadata_is_not_reclassified_from_its_prt_extension():
    request = {"documentKind": "drawing", "source": {"rootPath": "qualification.prt"}}
    native = {"source_filename": "qualification.prt", "document_type": "part"}
    metadata = {"source": {"system": "siemens_nx"}, "native": {"document_type": "part"}}
    with pytest.raises(RuntimeError, match="document kind"):
        validate_nx_worker_metadata(request, native, metadata)


@pytest.mark.parametrize("item_type", ["PART", "ASSEMBLY"])
def test_managed_drawing_artifact_wins_over_owning_item_type(item_type):
    from typing import get_args

    from modelryx.api.staging import ProposedArtifact

    accepted_types = get_args(ProposedArtifact.model_fields["artifact_type"].annotation)
    assert "DRAWING" in accepted_types
    assert nx_document_kind("DRAWING", item_type) == "drawing"
    assert nx_document_kind("CAD_ASSEMBLY", "ASSEMBLY") == "assembly"
    assert nx_document_kind("CAD_MODEL", "PART") == "part"


def test_pdf_output_retains_authoritative_drawing_source_lineage(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from modelryx.models import Artifact, ArtifactFile
    from modelryx.services import processing
    from modelryx.services.storage import LocalStorageProvider

    source = Artifact(id=uuid.uuid4(), artifact_type="DRAWING", classification="AUTHORITATIVE")
    native = ArtifactFile(id=uuid.uuid4(), artifact_id=source.id, role="PRIMARY")
    added = []

    class Database:
        def get(self, model, identifier):
            assert model is Artifact and identifier == source.id
            return source

        def add(self, value):
            if value.id is None:
                value.id = uuid.uuid4()
            added.append(value)

        def flush(self):
            pass

    def source_files(_db, _revision_id, _snapshot_id, *, artifact_types):
        return [native] if source.artifact_type in artifact_types else []

    monkeypatch.setattr(processing, "artifact_files_for_snapshot", source_files)
    content = tmp_path / "native-export.pdf"
    content.write_bytes(b"%PDF-1.7\nqualification-storage-fixture\n%%EOF\n")
    revision = SimpleNamespace(id=uuid.uuid4(), part_id=uuid.uuid4(), created_by=uuid.uuid4())
    output = processing.store_output(
        Database(),
        LocalStorageProvider(tmp_path / "objects"),
        revision,
        SimpleNamespace(id=uuid.uuid4()),
        kind="PDF",
        filename="drawing.pdf",
        mime_type="application/pdf",
        source=content,
        generator="siemens-nxopen-pdf",
    )
    derived = next(value for value in added if isinstance(value, Artifact))
    assert derived.classification == "DERIVED" and derived.artifact_type == "PDF"
    assert derived.derived_from_artifact_id == source.id
    assert output.sha256 == hashlib.sha256(content.read_bytes()).hexdigest()
