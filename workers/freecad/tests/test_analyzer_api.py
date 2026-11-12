import base64
import io
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path, PurePosixPath

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1] / "converter"))
import analyzer_api


def test_analyzer_validates_credentials_and_root_path(monkeypatch) -> None:
    monkeypatch.setenv("MODELRYX_INTERNAL_WORKER_KEY", "test-key")
    analyzer_api._require_key("test-key")
    assert (
        str(analyzer_api._root_path(base64.b64encode(b"Assembly.FCStd").decode()))
        == "Assembly.FCStd"
    )
    assert analyzer_api.health() == {"status": "ok"}


def test_analyzer_rejects_traversal_and_bad_credentials(monkeypatch) -> None:
    monkeypatch.setenv("MODELRYX_INTERNAL_WORKER_KEY", "test-key")
    with pytest.raises(HTTPException) as bad_key:
        analyzer_api._require_key("wrong")
    assert bad_key.value.status_code == 401
    with pytest.raises(HTTPException) as traversal:
        analyzer_api._root_path(base64.b64encode(b"../Assembly.FCStd").decode())
    assert traversal.value.status_code == 422


def test_analyzer_rejects_missing_credential(monkeypatch) -> None:
    monkeypatch.setenv("MODELRYX_INTERNAL_WORKER_KEY", "test-key")
    with pytest.raises(HTTPException) as failure:
        analyzer_api._require_key(None)
    assert failure.value.status_code == 401


def test_analyzer_reports_preprocessor_timeout(monkeypatch) -> None:
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("Assembly.FCStd", b"fixture")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("freecad", 300)

    monkeypatch.setattr(analyzer_api, "_run_preprocessor", timeout)
    with pytest.raises(HTTPException) as failure:
        analyzer_api._analyze_content(
            archive_bytes.getvalue(),
            analyzer_api._root_path(base64.b64encode(b"Assembly.FCStd").decode()),
        )
    assert failure.value.status_code == 504
    assert failure.value.detail["code"] == "FREECAD_ANALYSIS_TIMEOUT"


@pytest.mark.parametrize(
    ("members", "detail"),
    [
        (
            [("Assembly.FCStd", b"first"), ("ASSEMBLY.fcstd", b"second")],
            "duplicate path",
        ),
        (
            [("Assembly.FCStd", b"root"), ("parts", b"file"), ("parts/child.FCStd", b"child")],
            "path conflicts with a file",
        ),
        (
            [
                ("Assembly.FCStd", b"root"),
                ("parts\\child.FCStd", b"first"),
                ("parts/child.FCStd", b"second"),
            ],
            "duplicate path",
        ),
    ],
)
def test_analyzer_rejects_ambiguous_archive_paths(members, detail) -> None:
    archive_bytes = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(archive_bytes, "w") as archive:
        warnings.simplefilter("ignore", UserWarning)
        for name, content in members:
            archive.writestr(name, content)

    with pytest.raises(HTTPException) as failure:
        analyzer_api._analyze_content(
            archive_bytes.getvalue(),
            analyzer_api._root_path(base64.b64encode(b"Assembly.FCStd").decode()),
        )

    assert failure.value.status_code == 422
    assert detail in failure.value.detail


def test_analyzer_streams_request_with_bounded_size(monkeypatch) -> None:
    monkeypatch.setattr(analyzer_api, "MAX_ARCHIVE_BYTES", 8)
    received = analyzer_api._checked_archive_size(0, 5)
    assert received == 5
    with pytest.raises(HTTPException) as failure:
        analyzer_api._checked_archive_size(received, 4)

    assert failure.value.status_code == 413
    assert failure.value.detail == "CAD analysis package is too large"


@pytest.mark.parametrize("value", ["invalid", "-1"])
def test_analyzer_rejects_invalid_declared_archive_size(value) -> None:
    with pytest.raises(HTTPException) as failure:
        analyzer_api._declared_archive_size(value)
    assert failure.value.status_code == 400
    assert failure.value.detail == "Invalid Content-Length header"


def test_analyzer_resolves_windows_root_path_casing(monkeypatch) -> None:
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("Assemblies/Main.FCStd", b"fixture")

    observed_root = None

    def completed(root, output_root):
        nonlocal observed_root
        observed_root = Path(root)
        return subprocess.CompletedProcess(
            [root, output_root], 0, stdout='{"kind":"assembly"}\n', stderr=""
        )

    monkeypatch.setattr(analyzer_api, "_run_preprocessor", completed)
    report = analyzer_api._analyze_content(
        archive_bytes.getvalue(), PurePosixPath("assemblies/main.fcstd")
    )

    assert report == {"kind": "assembly"}
    assert observed_root is not None
    assert observed_root.parts[-2:] == ("Assemblies", "Main.FCStd")


def test_analyzer_returns_sanitized_structured_failure(monkeypatch) -> None:
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("Assembly.FCStd", b"fixture")

    def failed(root, output_root):
        workspace = Path(output_root).parent
        return subprocess.CompletedProcess(
            [root, output_root],
            1,
            stdout="",
            stderr=f"traceback\nCould not open {workspace / 'source' / 'secret.FCStd'}\x00",
        )

    monkeypatch.setattr(analyzer_api, "_run_preprocessor", failed)
    with pytest.raises(HTTPException) as failure:
        analyzer_api._analyze_content(archive_bytes.getvalue(), PurePosixPath("Assembly.FCStd"))

    assert failure.value.status_code == 422
    assert failure.value.detail["code"] == "FREECAD_ANALYSIS_FAILED"
    assert "<workspace>" in failure.value.detail["message"]
    assert "\x00" not in failure.value.detail["message"]


def test_terminate_process_tree_uses_windows_tree_kill(monkeypatch) -> None:
    calls = []

    class Process:
        pid = 321

        @staticmethod
        def poll():
            return None

        @staticmethod
        def wait(timeout):
            assert timeout == 10

        @staticmethod
        def kill():
            raise AssertionError("fallback kill should not be needed")

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(analyzer_api.os, "name", "nt")
    monkeypatch.setattr(analyzer_api.subprocess, "run", run)
    analyzer_api._terminate_process_tree(Process())

    assert calls[0][0] == ["taskkill", "/PID", "321", "/T", "/F"]
    assert calls[0][1]["check"] is False
    assert calls[0][1]["timeout"] == 10


def test_windows_tree_kill_failure_falls_back_to_kill(monkeypatch) -> None:
    calls = []

    class Process:
        pid = 321

        def poll(self):
            return None

        def kill(self):
            calls.append("kill")

        def wait(self, timeout):
            calls.append("wait")

    monkeypatch.setattr(analyzer_api.os, "name", "nt")
    monkeypatch.setattr(analyzer_api.subprocess, "run", lambda args, **_: subprocess.CompletedProcess(args, 1))
    analyzer_api._terminate_process_tree(Process())
    assert calls == ["kill", "wait"]
