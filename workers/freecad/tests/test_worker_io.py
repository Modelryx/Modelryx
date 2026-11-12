from __future__ import annotations

import json
import hashlib
import os
import sys
from pathlib import Path

import pytest

CONVERTER = Path(__file__).resolve().parents[1] / "converter"
sys.path.insert(0, str(CONVERTER))

import worker_io
from worker_io import atomic_write_json, peak_memory_bytes


def test_atomic_write_json_replaces_complete_document(tmp_path: Path, monkeypatch) -> None:
    replaced = []

    def replace(source: str, destination: Path) -> None:
        replaced.append((source, destination))
        destination.write_bytes(Path(source).read_bytes())
        os.unlink(source)

    monkeypatch.setattr(worker_io.os, "replace", replace)
    output = tmp_path / "nested" / "progress.json"
    atomic_write_json(output, {"stage": "OPENING", "progress": 40})
    atomic_write_json(output, {"stage": "COMPLETE", "progress": 100})

    assert json.loads(output.read_text(encoding="utf-8")) == {
        "stage": "COMPLETE",
        "progress": 100,
    }
    assert len(replaced) == 2
    assert list(output.parent.glob(f".{output.name}.*.tmp")) == []


def test_peak_memory_is_non_negative_when_available() -> None:
    value = peak_memory_bytes()
    assert value is None or value >= 0


def test_native_source_limit_is_checked_before_open(tmp_path, monkeypatch):
    source = tmp_path / "model.FCStd"
    source.write_bytes(b"12345")
    monkeypatch.setattr(worker_io, "MAX_NATIVE_DOCUMENT_BYTES", 4)
    with pytest.raises(ValueError, match="document limit"):
        worker_io.check_native_document_size(source)


def test_file_checksum_streams_without_read_bytes(tmp_path, monkeypatch):
    source = tmp_path / "source"
    content = b"native" * 300_000
    source.write_bytes(content)
    def forbidden(*args, **kwargs):
        raise AssertionError("whole-file read is forbidden")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    assert worker_io.sha256_file(source) == hashlib.sha256(content).hexdigest()
