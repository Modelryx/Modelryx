from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from modelryx.services.processing import materialize_freecad_workspace


class TestStorage:
    __test__ = False

    def __init__(self, root: Path):
        self.root = root

    def path_for(self, key: str) -> Path:
        return self.root / key


def _artifact(key: str, logical_path: str, content: bytes):
    return SimpleNamespace(
        id=uuid.uuid4(),
        storage_key=key,
        reference_name=logical_path,
        original_filename=Path(logical_path).name,
        sha256=content.hex(),
    )


def test_workspace_preserves_directories_for_same_named_components(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    root = _artifact("root", "Assembly.FCStd", b"root")
    left = _artifact("left", "left/Plate.FCStd", b"left")
    right = _artifact("right", "right/Plate.FCStd", b"right")
    for artifact, content in ((root, b"root"), (left, b"left"), (right, b"right")):
        (storage_root / artifact.storage_key).write_bytes(content)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    source = materialize_freecad_workspace(
        TestStorage(storage_root), root, [left, right], workspace
    )

    assert source == workspace / "Assembly.FCStd"
    assert (workspace / "left" / "Plate.FCStd").read_bytes() == b"left"
    assert (workspace / "right" / "Plate.FCStd").read_bytes() == b"right"


def test_workspace_rejects_case_insensitive_path_collision(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    root = _artifact("root", "Assembly.FCStd", b"root")
    first = _artifact("first", "Parts/Plate.FCStd", b"first")
    second = _artifact("second", "parts/plate.fcstd", b"second")
    for artifact in (root, first, second):
        (storage_root / artifact.storage_key).write_bytes(artifact.storage_key.encode())

    with pytest.raises(RuntimeError, match="different component files"):
        materialize_freecad_workspace(
            TestStorage(storage_root), root, [first, second], tmp_path / "workspace"
        )


def test_workspace_uses_original_filename_for_semantic_model_reference(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    source = _artifact("source", "MODEL", b"freecad")
    source.original_filename = "PART-0000206.FCStd"
    (storage_root / source.storage_key).write_bytes(b"freecad")

    converter_source = materialize_freecad_workspace(
        TestStorage(storage_root), source, [], tmp_path / "workspace"
    )

    assert converter_source == tmp_path / "workspace" / "PART-0000206.FCStd"
    assert converter_source.read_bytes() == b"freecad"
