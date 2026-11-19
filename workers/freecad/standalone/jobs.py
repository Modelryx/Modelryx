"""Durable single-process FreeCAD conversion queue."""

import hashlib
import json
import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, RLock, Thread
from zipfile import ZipFile

from modelryx_worker_sdk import describe_output, validate_result
from worker_io import atomic_write_json

from .conversion import OUTPUTS, ConversionRequest, expired, materialize_source


def export_preview_members(package: ZipFile, workspace: Path, requested: set[str]) -> None:
    """Expose the generic worker roles without changing the authoritative PFG conversion."""
    for role in ("model-preview", "glb"):
        if role not in requested:
            continue
        name, _, limit = OUTPUTS[role]
        entries = [entry for entry in package.infolist() if entry.filename == name]
        if len(entries) != 1 or not 0 < entries[0].file_size <= limit:
            raise ValueError(f"FreeCAD preview has a missing, ambiguous or oversized {role} output")
        with package.open(entries[0]) as source, (workspace / name).open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        file = workspace / name
        if role == "model-preview":
            value = json.loads(file.read_bytes())
            if not isinstance(value, dict):
                raise ValueError("FreeCAD model preview must be a JSON object")
        else:
            with file.open("rb") as stream:
                header = stream.read(12)
            if (len(header) != 12 or header[:4] != b"glTF"
                    or int.from_bytes(header[4:8], "little") != 2
                    or int.from_bytes(header[8:12], "little") != file.stat().st_size):
                raise ValueError("FreeCAD GLB output is invalid")


LOGGER = logging.getLogger(__name__)
ACTIVE = {"Queued", "Reconstructing", "Extracting", "Validating"}


class JobInterrupted(Exception):
    def __init__(self, state: str):
        self.state = state


class JobConflict(Exception):
    pass


class QueueFull(Exception):
    pass


class JobStore:
    def __init__(self, root: Path, adapter, *, capacity: int = 4, retention: int = 86400):
        self.root = root
        self.adapter = adapter
        self.capacity = capacity
        self.retention = retention
        self.lock = RLock()
        self.stop = Event()
        self.thread = Thread(target=self.run, name="freecad-queue")

    def start(self):
        self.root.mkdir(parents=True, exist_ok=True)
        for path in self.root.glob(".upload-*"):
            shutil.rmtree(path)
        for record in self.records():
            if record["state"] in ACTIVE - {"Queued"}:
                self.write(record, state="Queued", stage="recovering", progress=0)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join()

    def records(self):
        return [
            json.loads(path.read_text(encoding="utf-8")) for path in self.root.glob("*/job.json")
        ]

    def write(self, record: dict, **changes) -> None:
        record.update(changes, updatedAt=datetime.now(UTC).isoformat())
        atomic_write_json(self.root / record["id"] / "job.json", record)

    def get(self, job_id: str) -> dict | None:
        path = self.root / job_id / "job.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            record = self.get(job_id)
            if record is None:
                return False
            if record["state"] in ACTIVE:
                self.write(record, cancellationRequested=True)
            return True

    def submit(self, request: ConversionRequest, stream, media_type: str) -> dict:
        payload = request.model_dump(mode="json")
        with TemporaryDirectory(dir=self.root, prefix=".upload-") as staging:
            directory = Path(staging)
            digest = hashlib.sha256()
            size = 0
            with (directory / "source").open("wb") as target:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > request.source.size:
                        raise ValueError("Source size does not match the request")
                    digest.update(chunk)
                    target.write(chunk)
            if size != request.source.size or digest.hexdigest() != request.source.sha256.lower():
                raise ValueError("Source bytes do not match the declared size and SHA-256")
            with self.lock:
                current = self.get(request.jobId)
                if current is not None:
                    if current["request"] != payload or current["sourceMediaType"] != media_type:
                        raise JobConflict
                    return current
                if expired(request):
                    raise ValueError("The job request has expired")
                if self.adapter.compatibility()[0] != "COMPATIBLE":
                    raise QueueFull
                if sum(record["state"] in ACTIVE for record in self.records()) >= self.capacity:
                    raise QueueFull
                now = datetime.now(UTC).isoformat()
                record = {
                    "id": request.jobId,
                    "request": payload,
                    "sourceMediaType": media_type,
                    "state": "Queued",
                    "stage": "queued",
                    "progress": 0,
                    "createdAt": now,
                    "updatedAt": now,
                    "cancellationRequested": False,
                }
                atomic_write_json(directory / "job.json", record)
                directory.rename(self.root / request.jobId)
                return record

    def run(self):
        while not self.stop.is_set():
            with self.lock:
                pending = []
                for record in self.records():
                    if record["state"] == "Queued":
                        pending.append(record)
                    elif (
                        record["state"] not in ACTIVE
                        and (
                            datetime.now(UTC) - datetime.fromisoformat(record["updatedAt"])
                        ).total_seconds()
                        >= self.retention
                    ):
                        try:
                            shutil.rmtree(self.root / record["id"])
                        except OSError:
                            LOGGER.warning("Could not reclaim job %s", record["id"], exc_info=True)
            if pending:
                self.process(min(pending, key=lambda record: record["createdAt"]))
            else:
                self.stop.wait(1)

    def process(self, record: dict):
        request = ConversionRequest.model_validate(record["request"])
        directory = self.root / request.jobId

        def check_interrupted():
            with self.lock:
                current = self.get(request.jobId)
            if current["cancellationRequested"]:
                raise JobInterrupted("Cancelled")
            if expired(request):
                raise JobInterrupted("Expired")
            if self.stop.is_set():
                raise JobInterrupted("Queued")

        def report(stage: str, progress: int, _message: str):
            with self.lock:
                check_interrupted()
                record.update(self.get(request.jobId))
                self.write(
                    record, state="Extracting", stage=stage, progress=min(80, max(10, progress))
                )

        try:
            with self.lock:
                check_interrupted()
                self.write(record, state="Reconstructing", stage="reconstructing", progress=5)
            # A process killed during extraction can leave a workspace behind.
            workspace = directory / "workspace"
            if workspace.exists():
                shutil.rmtree(workspace)
            workspace.mkdir()
            source = materialize_source(
                directory / "source",
                workspace / "input",
                request.source.rootPath,
                record["sourceMediaType"],
                check_interrupted,
            )
            preview = workspace / "preview.pfg"
            report("extracting", 10, "")
            self.adapter.convert(
                source,
                preview,
                timeout_seconds=min(
                    1800, max(1, (request.expiresAt - datetime.now(UTC)).total_seconds())
                ),
                max_nodes=100_000,
                max_triangles=5_000_000,
                progress_callback=report,
                cancellation_callback=check_interrupted,
            )
            with self.lock:
                check_interrupted()
                self.write(record, state="Validating", stage="validating", progress=85)
            requested = set(request.requiredOutputs) | set(request.preferredOutputs)
            with ZipFile(preview) as package:
                export_preview_members(package, workspace, requested)
                diagnostics_entry = package.getinfo("diagnostics.json")
                if diagnostics_entry.file_size > 8 * 1024**2:
                    raise ValueError("FreeCAD diagnostics exceed their size limit")
                diagnostics = json.loads(package.read(diagnostics_entry))
            warnings = [
                warning["message"]
                for warning in diagnostics.get("warnings", [])
                if warning.get("message", "").strip()
            ]
            if "drawing-pdf" in requested:
                pdf = workspace / OUTPUTS["drawing-pdf"][0]
                try:
                    self.adapter.export_drawing_pdf(
                        source, pdf,
                        timeout_seconds=min(
                            1800, max(1, (request.expiresAt - datetime.now(UTC)).total_seconds())
                        ),
                        cancellation_callback=check_interrupted,
                    )
                    if pdf.exists():
                        if pdf.stat().st_size > OUTPUTS["drawing-pdf"][2]:
                            raise ValueError("FreeCAD drawing PDF exceeds its size limit")
                        with pdf.open("rb") as stream:
                            if stream.read(5) != b"%PDF-":
                                raise ValueError("FreeCAD produced an invalid drawing PDF")
                except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
                    if "drawing-pdf" in request.requiredOutputs:
                        raise
                    LOGGER.warning("FreeCAD could not export the drawing PDF", exc_info=True)
                    pdf.unlink(missing_ok=True)
                    warnings.append("FreeCAD could not export the drawing PDF.")
                if not pdf.exists():
                    if "drawing-pdf" in request.requiredOutputs:
                        raise ValueError("FreeCAD drawing PDF is unavailable")
                    if "FreeCAD could not export the drawing PDF." not in warnings:
                        warnings.append("FreeCAD document contains no exportable drawing pages.")
            outputs = []
            for role, (name, media_type, limit) in OUTPUTS.items():
                if role in requested:
                    check_interrupted()
                    file = workspace / name
                    if role == "drawing-pdf" and not file.exists():
                        continue
                    if file.stat().st_size > limit:
                        raise ValueError("FreeCAD output exceeds its size limit")
                    outputs.append(
                        describe_output(
                            request.jobId,
                            role,
                            file,
                            media_type,
                            generator=self.adapter.generator,
                            source_role="PRIMARY",
                        )
                    )
                    os.replace(file, directory / name)
            result = {
                **{
                    key: record["request"][key]
                    for key in (
                        "contractVersion",
                        "jobId",
                        "attempt",
                        "cadSystem",
                        "configuration",
                    )
                },
                "sourceSha256": request.source.sha256,
                "adapterVersion": self.adapter.worker_version,
                "cadVersion": self.adapter.application_version(),
                "status": "partial" if warnings else "complete",
                "warnings": warnings,
                "outputs": outputs,
            }
            validate_result(
                record["request"],
                result,
                allowed_outputs={
                    role: (media_type, limit) for role, (_, media_type, limit) in OUTPUTS.items()
                },
            )
            with self.lock:
                check_interrupted()
                self.write(
                    record,
                    result=result,
                    state="Partial" if warnings else "ReadyToPublish",
                    stage="complete",
                    progress=100,
                )
        except Exception as error:
            with self.lock:
                if not isinstance(error, JobInterrupted):
                    try:
                        check_interrupted()
                    except JobInterrupted as interruption:
                        error = interruption
                record.update(self.get(request.jobId))
                if isinstance(error, JobInterrupted):
                    self.write(record, state=error.state, stage=error.state.lower())
                else:
                    LOGGER.exception("FreeCAD conversion failed for %s", request.jobId)
                    timed_out = isinstance(error, subprocess.TimeoutExpired)
                    invalid_package = record["state"] == "Reconstructing"
                    message = "FreeCAD could not convert the document. See the worker log."
                    if invalid_package:
                        message = "The FreeCAD dependency package is invalid or exceeds its limits."
                    self.write(
                        record,
                        state="Failed",
                        error={
                            "code": "TIMEOUT" if timed_out else "CONVERSION_FAILED",
                            "message": message,
                            "retryable": timed_out,
                            "stage": record["stage"],
                        },
                    )
        finally:
            shutil.rmtree(directory / "workspace", ignore_errors=True)
            if record["state"] not in ACTIVE:
                (directory / "source").unlink(missing_ok=True)
            if record["state"] not in {"Partial", "ReadyToPublish"}:
                for name, _, _ in OUTPUTS.values():
                    (directory / name).unlink(missing_ok=True)
