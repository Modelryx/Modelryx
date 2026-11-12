import json
import uuid
from types import SimpleNamespace

from modelryx.api import previews


def test_preview_map_keeps_duplicate_source_ids_ambiguous(monkeypatch, tmp_path) -> None:
    revision_id, item_id = uuid.uuid4(), uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    nodes = [
        SimpleNamespace(id=first, occurrence_key="a", source_reference="same-source", children=[]),
        SimpleNamespace(id=second, occurrence_key="b", source_reference="same-source", children=[]),
    ]
    metadata_path = tmp_path / "model-preview.json"
    metadata_path.write_text(json.dumps({"items": [
        {"occurrenceId": "preview-1", "sourceObjectId": "same-source"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(
        previews, "read_item_bom", lambda *_args: SimpleNamespace(occurrences=nodes)
    )
    monkeypatch.setattr(
        previews, "preview_artifact", lambda *_args: SimpleNamespace(storage_key="x")
    )
    monkeypatch.setattr(previews, "LocalStorageProvider", lambda _root: SimpleNamespace(
        path_for=lambda _key: metadata_path
    ))

    result = previews.preview_occurrence_map(
        item_id, revision_id, None, None, SimpleNamespace(storage_root=tmp_path)
    )
    assert result["matches"] == {}
    assert set(result["ambiguous"]) == {str(first), str(second)}
    assert result["unmapped"] == []
