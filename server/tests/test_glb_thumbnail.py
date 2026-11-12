import json
import struct
import uuid
from types import SimpleNamespace

from modelryx.api import previews
from modelryx.services.glb_thumbnail import render_glb_thumbnail
from PIL import Image


def test_renders_transformed_glb_geometry_as_png(tmp_path):
    vertices = [
        (-1, -1, -1),
        (1, -1, -1),
        (1, 1, -1),
        (-1, 1, -1),
        (-1, -1, 1),
        (1, -1, 1),
        (1, 1, 1),
        (-1, 1, 1),
    ]
    faces = [
        0,
        1,
        2,
        0,
        2,
        3,
        4,
        6,
        5,
        4,
        7,
        6,
        0,
        4,
        5,
        0,
        5,
        1,
        3,
        2,
        6,
        3,
        6,
        7,
        1,
        5,
        6,
        1,
        6,
        2,
        0,
        3,
        7,
        0,
        7,
        4,
    ]
    geometry = b"".join(struct.pack("<3f", *point) for point in vertices)
    indices = struct.pack("<" + "H" * len(faces), *faces)
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(geometry) + len(indices)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(geometry)},
            {"buffer": 0, "byteOffset": len(geometry), "byteLength": len(indices)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(vertices), "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": len(faces), "type": "SCALAR"},
        ],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.3, 0.6, 0.9, 1]}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "nodes": [{"translation": [3, 4, 5], "mesh": 0, "extras": {
            "modelryx": {"partId": "part-1", "occurrenceId": "body-1",
                          "display": {"color": "#202020"}}
        }}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    json_chunk = json.dumps(document).encode("utf-8")
    json_chunk += b" " * (-len(json_chunk) % 4)
    binary_chunk = geometry + indices
    binary_chunk += b"\x00" * (-len(binary_chunk) % 4)
    content = (
        struct.pack("<I4s", len(json_chunk), b"JSON")
        + json_chunk
        + struct.pack("<I4s", len(binary_chunk), b"BIN\x00")
        + binary_chunk
    )
    glb = tmp_path / "model.glb"
    glb.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(content)) + content)

    output = tmp_path / "revision-thumbnail.png"
    render_glb_thumbnail(glb, output)

    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.size == (640, 400)
        assert image.getpixel((320, 200)) != image.getpixel((0, 0))
        assert max(image.getpixel((320, 200))) < 50

    preview = tmp_path / "model-preview.json"
    preview.write_text(json.dumps({"items": [{
        "name": "Body", "nodeType": "body", "occurrenceId": "body-1",
        "display": {"color": "#C02020"},
    }]}), encoding="utf-8")
    render_glb_thumbnail(glb, output, preview_path=preview)
    with Image.open(output) as image:
        red, green, blue = image.getpixel((320, 200))
        assert red > green * 3 and red > blue * 3


def test_thumbnail_is_served_separately_from_2d_representations(monkeypatch, tmp_path):
    revision_id = uuid.uuid4()
    thumbnail_id = uuid.uuid4()
    files = [
        SimpleNamespace(
            id=thumbnail_id,
            artifact_id=None,
            kind="IMAGE",
            mime_type="image/png",
            original_filename="revision-thumbnail.png",
            storage_key="thumbnail.png",
        )
    ]
    (tmp_path / "thumbnail.png").write_bytes(b"PNG fixture")
    monkeypatch.setattr(previews, "current_artifact_files", lambda *_args, **_kwargs: files)
    db = SimpleNamespace(get=lambda *_args: SimpleNamespace(id=revision_id))
    settings = SimpleNamespace(storage_root=tmp_path)

    image = previews.revision_preview_thumbnail(revision_id, db, None, settings)
    assert image.path == tmp_path / "thumbnail.png"
    assert previews.revision_preview_representations(revision_id, db, None, settings) == []


def test_existing_thumbnail_uses_current_model_display_colors(monkeypatch, tmp_path):
    revision_id = uuid.uuid4()
    (tmp_path / "model.glb").write_bytes(b"managed model")
    (tmp_path / "model-preview.json").write_text("{}", encoding="utf-8")
    files = [
        SimpleNamespace(kind="GLB", original_filename="model.glb", storage_key="model.glb",
                        sha256="model-hash"),
        SimpleNamespace(kind="METADATA", original_filename="model-preview.json",
                        storage_key="model-preview.json", sha256="preview-hash"),
        SimpleNamespace(kind="IMAGE", original_filename="revision-thumbnail.png",
                        storage_key="thumbnail.png"),
    ]
    monkeypatch.setattr(previews, "current_artifact_files", lambda *_args, **_kwargs: files)
    monkeypatch.setattr(previews, "_display_thumbnail", lambda *_args: b"corrected png")
    db = SimpleNamespace(get=lambda *_args: SimpleNamespace(id=revision_id))

    response = previews.revision_preview_thumbnail(
        revision_id, db, None, SimpleNamespace(storage_root=tmp_path)
    )

    assert response.body == b"corrected png"
    assert response.media_type == "image/png"
