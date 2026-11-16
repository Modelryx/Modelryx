import json
import zipfile
from pathlib import Path

import pytest
from modelryx.core.config import get_settings
from modelryx.services.processing import (
    merge_related_drawing_metadata,
    read_freecad_drawing_previews,
    read_worker_drawing_representations,
)

PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def test_extracts_declared_freecad_drawing_preview(tmp_path: Path) -> None:
    package_path = tmp_path / "preview.pfg"
    with zipfile.ZipFile(package_path, "w") as package:
        package.writestr(
            "manifest.json",
            json.dumps({"drawings": [{"file": "drawings/drawing-0001-Page.png"}]}),
        )
        package.writestr("drawings/drawing-0001-Page.png", PNG_HEADER + b"preview")

    results = read_freecad_drawing_previews(package_path, tmp_path, get_settings())

    assert [path.name for path in results] == ["drawing-0001-Page.png"]
    assert results[0].read_bytes() == PNG_HEADER + b"preview"


def test_rejects_unsafe_freecad_drawing_preview_path(tmp_path: Path) -> None:
    package_path = tmp_path / "unsafe.pfg"
    with zipfile.ZipFile(package_path, "w") as package:
        package.writestr(
            "manifest.json",
            json.dumps({"drawings": [{"file": "drawings/../outside.png"}]}),
        )
        package.writestr("drawings/../outside.png", PNG_HEADER + b"preview")

    with pytest.raises(RuntimeError, match="unsafe drawing preview path"):
        read_freecad_drawing_previews(package_path, tmp_path, get_settings())


def test_extracts_and_merges_example_cad_related_drawing_metadata(tmp_path: Path) -> None:
    package_path = tmp_path / "drawing-representations.zip"
    with zipfile.ZipFile(package_path, "w") as package:
        package.writestr("drawing-0001.pdf", b"%PDF-1.7\nfixture")
        package.writestr("manifest.json", json.dumps({
            "drawings": [{
                "file": "drawing-0001.pdf",
                "source": "drawings/model.idw",
                "displayName": "Model drawing",
                "sheets": [{
                    "id": "sheet-1", "name": "Sheet:1", "page": 1,
                    "width_mm": 420, "height_mm": 297, "view_count": 3,
                    "title_block_name": "ISO",
                }],
            }],
            "failures": [],
        }))

    manifest, paths = read_worker_drawing_representations(
        package_path, tmp_path, get_settings()
    )
    metadata = {"native": {}}
    merge_related_drawing_metadata(metadata, manifest)

    assert [path.name for path in paths] == ["drawing-0001-model.pdf"]
    assert metadata["drawings"][0]["page_count"] == 1
    assert metadata["drawings"][0]["filename"] == "drawing-0001-model.pdf"
    assert metadata["native"]["drawing_sheets"][0]["drawing_name"] == "Model drawing"
    assert metadata["native"]["drawing_sheets"][0]["source_path"] == "drawings/model.idw"
