"""Authenticated HTTP wrapper around the canonical FreeCAD dependency analyzer."""

from __future__ import annotations

import base64
import binascii
import hmac
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

app = FastAPI(title="Modelryx FreeCAD Analyzer", version="1.0")

MAX_FILES = 500
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_UNCOMPRESSED_BYTES + 64 * 1024 * 1024
MEMORY_SPOOL_BYTES = 8 * 1024 * 1024
MAX_PATH_LENGTH = 240
PREPROCESSOR = Path(os.environ.get(
    "MODELRYX_FREECAD_ANALYZER_SCRIPT",
    "/opt/modelryx-preprocessor/modelryx_freecad_preprocessor.py",
))
ANALYSIS_TIMEOUT_SECONDS = 300


def _safe_member(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or len(normalized) > MAX_PATH_LENGTH
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or (path.parts and ":" in path.parts[0])
    ):
        raise HTTPException(status_code=422, detail=f"Unsafe CAD package path: {name}")
    return path


def _root_path(encoded: str | None) -> PurePosixPath:
    if not encoded:
        raise HTTPException(status_code=422, detail="CAD root path is required")
    try:
        value = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid encoded CAD root path") from exc
    path = _safe_member(value)
    if path.suffix.casefold() != ".fcstd":
        raise HTTPException(status_code=422, detail="FreeCAD analyzer root must be an .FCStd file")
    return path


def _require_key(value: str | None) -> None:
    expected = os.environ.get("MODELRYX_INTERNAL_WORKER_KEY", "")
    supplied = value or ""
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid worker credential")


def _checked_archive_size(received: int, chunk_size: int) -> int:
    total = received + chunk_size
    if total > MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="CAD analysis package is too large")
    return total


def _declared_archive_size(value: str) -> int:
    try:
        size = int(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Content-Length header") from None
    if size < 0:
        raise HTTPException(status_code=400, detail="Invalid Content-Length header")
    if size > MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="CAD analysis package is too large")
    return size


def _validated_files(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    """Validate the complete archive namespace before writing any member."""
    files: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    logical_paths: dict[str, PurePosixPath] = {}
    for entry in archive.infolist():
        if entry.is_dir():
            _safe_member(entry.filename.rstrip("/\\"))
            continue
        relative = _safe_member(entry.filename)
        collision_key = relative.as_posix().casefold()
        if collision_key in logical_paths:
            raise HTTPException(
                status_code=422,
                detail=f"CAD package contains a duplicate path: {relative.as_posix()}",
            )
        logical_paths[collision_key] = relative
        files.append((entry, relative))

    if len(files) > MAX_FILES:
        raise HTTPException(status_code=413, detail="CAD package contains too many files")
    if sum(entry.file_size for entry, _relative in files) > MAX_UNCOMPRESSED_BYTES:
        raise HTTPException(status_code=413, detail="CAD package is too large")

    file_keys = set(logical_paths)
    for _entry, relative in files:
        for parent in relative.parents:
            if parent == PurePosixPath("."):
                break
            if parent.as_posix().casefold() in file_keys:
                raise HTTPException(
                    status_code=422,
                    detail=f"CAD package path conflicts with a file: {parent.as_posix()}",
                )
    return files


def _resolved_root_path(
    files: list[tuple[zipfile.ZipInfo, PurePosixPath]], requested: PurePosixPath
) -> PurePosixPath:
    """Resolve Windows-origin root casing after archive collisions are excluded."""
    requested_key = requested.as_posix().casefold()
    for _entry, relative in files:
        if relative.as_posix().casefold() == requested_key:
            return relative
    raise HTTPException(status_code=422, detail="CAD root file is missing from the package")


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate the analyzer and descendants created in its isolated process group."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
            if result.returncode and process.poll() is None:
                process.kill()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        if process.poll() is None:
            process.kill()
    finally:
        try:
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()


def _run_preprocessor(root: Path, output_root: Path) -> subprocess.CompletedProcess[str]:
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(
        [sys.executable, str(PREPROCESSOR), str(root), str(output_root)],
        **kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=ANALYSIS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        raise
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def _failure_detail(completed: subprocess.CompletedProcess[str], workspace: Path) -> dict[str, str]:
    """Return stable failure data without exposing temporary workspace paths."""
    raw = (completed.stderr or completed.stdout or "FreeCAD analysis failed").strip()
    message = raw.splitlines()[-1] if raw else "FreeCAD analysis failed"
    message = message.replace(str(workspace), "<workspace>")
    message = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", message)[:1000]
    return {"code": "FREECAD_ANALYSIS_FAILED", "message": message}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(
    request: Request,
    x_modelryx_root_path_b64: str | None = Header(default=None),
    x_modelryx_worker_key: str | None = Header(default=None),
) -> dict:
    _require_key(x_modelryx_worker_key)
    root_relative = _root_path(x_modelryx_root_path_b64)
    declared_length = request.headers.get("content-length")
    if declared_length:
        _declared_archive_size(declared_length)

    with tempfile.SpooledTemporaryFile(max_size=MEMORY_SPOOL_BYTES, mode="w+b") as content:
        received = 0
        async for chunk in request.stream():
            received = _checked_archive_size(received, len(chunk))
            await run_in_threadpool(content.write, chunk)
        if not received:
            raise HTTPException(status_code=422, detail="CAD analysis package is empty")
        content.seek(0)
        return await run_in_threadpool(_analyze_archive, content, root_relative)


def _analyze_content(content: bytes, root_relative: PurePosixPath) -> dict:
    """Analyze an in-memory archive; retained as a focused unit-test entry point."""
    return _analyze_archive(io.BytesIO(content), root_relative)


def _analyze_archive(archive_source: BinaryIO, root_relative: PurePosixPath) -> dict:
    """Run archive, filesystem, and FreeCAD work outside the ASGI event loop."""
    with tempfile.TemporaryDirectory(prefix="modelryx-analyzer-") as temporary:
        workspace = Path(temporary)
        source_root = workspace / "source"
        output_root = workspace / "output"
        try:
            with zipfile.ZipFile(archive_source) as archive:
                files = _validated_files(archive)
                resolved_root = _resolved_root_path(files, root_relative)
                for entry, relative in files:
                    destination = source_root.joinpath(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, destination.open("wb") as target:
                        while chunk := source.read(1024 * 1024):
                            target.write(chunk)
        except (NotImplementedError, RuntimeError, zipfile.BadZipFile) as exc:
            raise HTTPException(
                status_code=422, detail="CAD analysis package is not a valid ZIP"
            ) from exc

        root = source_root.joinpath(*resolved_root.parts)
        try:
            completed = _run_preprocessor(root, output_root)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=504,
                detail={
                    "code": "FREECAD_ANALYSIS_TIMEOUT",
                    "message": "FreeCAD analysis timed out",
                },
            ) from exc
        if completed.returncode != 0:
            raise HTTPException(status_code=422, detail=_failure_detail(completed, workspace))
        try:
            report = json.loads(completed.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=500, detail="FreeCAD analyzer returned invalid output"
            ) from exc
        report.pop("package", None)
        return report
