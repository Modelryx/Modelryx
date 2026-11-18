#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import FreeCAD  # type: ignore

for shared_path in (Path(__file__).resolve().parent / "shared", Path(__file__).resolve().parent.parent / "shared"):
    if shared_path.is_dir():
        sys.path.insert(0, str(shared_path))
        break
from modelryx_process_control import isolated_process_options, terminate_process_tree

MAX_LOG_BYTES = 16 * 1024 * 1024
ERROR_TAIL_BYTES = 4000


def _log_tail(stream: Any) -> str:
    stream.flush()
    stream.seek(0, os.SEEK_END)
    stream.seek(max(0, stream.tell() - ERROR_TAIL_BYTES))
    return stream.read(ERROR_TAIL_BYTES).decode("utf-8", errors="replace")


def _run_process(
    command: list[str],
    output: Path,
    *,
    timeout_seconds: int,
    cancellation_callback: Callable[[], None] | None,
    progress_path: Path | None = None,
    progress_callback: Callable[[str, int, str], None] | None = None,
) -> None:
    """Supervise both conversion and edits without buffering native logs in memory."""
    if cancellation_callback:
        cancellation_callback()
    if progress_path is not None:
        progress_path.unlink(missing_ok=True)
    started = time.monotonic()
    stage_started = started
    current_stage = "STARTING"
    stage_timeout = _stage_timeout_seconds()
    last_progress = ""
    with output.with_suffix(".stdout.log").open("w+b") as stdout, output.with_suffix(
        ".stderr.log"
    ).open("w+b") as stderr:
        process = subprocess.Popen(
            command, stdout=stdout, stderr=stderr, **isolated_process_options()
        )
        try:
            while process.poll() is None:
                if cancellation_callback:
                    cancellation_callback()
                now = time.monotonic()
                if now - started >= timeout_seconds:
                    raise subprocess.TimeoutExpired(command, timeout_seconds)
                if any(os.fstat(stream.fileno()).st_size > MAX_LOG_BYTES for stream in (stdout, stderr)):
                    raise RuntimeError("FreeCAD native log exceeded the 16 MiB per-stream resource limit")
                progress = None
                if progress_path is not None and progress_path.is_file():
                    try:
                        value = json.loads(progress_path.read_text(encoding="utf-8"))
                        progress = (str(value["stage"]), int(value["progress"]), str(value["message"]))
                    except (KeyError, OSError, TypeError, ValueError):
                        pass
                if progress is not None:
                    stage, percent, message = progress
                    if stage != current_stage:
                        current_stage = stage
                        stage_started = now
                    signature = json.dumps(progress)
                    if progress_callback and signature != last_progress:
                        # Callback errors must propagate, including cancellation and DB failures.
                        progress_callback(stage, percent, message)
                        last_progress = signature
                if stage_timeout is not None and now - stage_started >= stage_timeout:
                    raise subprocess.TimeoutExpired(
                        command, stage_timeout,
                        output=f"FreeCAD stage {current_stage} exceeded its time limit.",
                    )
                time.sleep(0.2)
            if cancellation_callback:
                cancellation_callback()
        finally:
            if process.poll() is None:
                terminate_process_tree(process)
        if any(os.fstat(stream.fileno()).st_size > MAX_LOG_BYTES for stream in (stdout, stderr)):
            raise RuntimeError("FreeCAD native log exceeded the 16 MiB per-stream resource limit")
        if process.returncode:
            raise RuntimeError(_log_tail(stderr) or _log_tail(stdout) or "FreeCAD worker failed")


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?", value)
    return tuple(int(part or 0) for part in match.groups()) if match else None


def _stage_timeout_seconds() -> float | None:
    raw = os.environ.get("MODELRYX_FREECAD_STAGE_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ValueError("MODELRYX_FREECAD_STAGE_TIMEOUT_SECONDS must be a positive number") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("MODELRYX_FREECAD_STAGE_TIMEOUT_SECONDS must be a positive number")
    return value


class FreeCADWorkerAdapter:
    worker_id = "freecad-controlled"
    worker_type = "FREECAD"
    worker_version = os.environ.get("MODELRYX_CONVERTER_VERSION", "modelryx-fcstd-29")
    generator = "modelryx-freecad-worker"

    def application_version(self) -> str | None:
        try:
            return ".".join(str(value) for value in FreeCAD.Version()[:3])
        except (AttributeError, RuntimeError, TypeError):
            return None

    def compatibility(self) -> tuple[str, str | None]:
        value = self.application_version()
        minimum = os.environ.get("MODELRYX_FREECAD_MIN_SUPPORTED_VERSION", "1.1.3")
        maximum = os.environ.get("MODELRYX_FREECAD_MAX_SUPPORTED_VERSION_EXCLUSIVE", "1.1.4")
        version = _version_tuple(value or "")
        lower = _version_tuple(minimum)
        upper = _version_tuple(maximum)
        if version is None:
            return "INCOMPATIBLE", "The CAD application version is unavailable."
        if lower is None or upper is None or lower >= upper:
            return "UNKNOWN", "The configured application-version policy is invalid."
        if version < lower or version >= upper:
            return "INCOMPATIBLE", f"Application {value} is outside >={minimum}, <{maximum}."
        return "COMPATIBLE", None

    def capabilities(self) -> dict[str, Any]:
        compatible = self.compatibility()[0] == "COMPATIBLE"
        return {
            "worker_id": self.worker_id,
            "worker_version": self.worker_version,
            "protocol_version": "1.0",
            "cad_system": "FreeCAD",
            "cad_version": self.application_version() or "unknown",
            "read": [
                "document.inspect",
                "feature.tree",
                "feature.parameters",
                "sketch.constraints",
                "geometry.inspect",
                "material.get",
            ],
            "mutate": [
                "sketch.constraint.set_value",
                "feature.parameter.set_value",
                "material.set",
                "sketch.create",
                "feature.create_pad",
                "feature.create_pocket",
                "feature.create_hole",
                "feature.create_fillet",
                "document.recompute",
                "document.save_as",
                "export.glb",
            ] if compatible else [],
            "limits": {
                "max_document_bytes": 2 * 1024 * 1024 * 1024,
                "timeout_seconds": 1800,
                "max_objects": 100_000,
            },
            "vendor_extensions": {
                "formats": ["FCStd"],
                "operations": [
                    "analyze",
                    "extract_metadata",
                    "generate_preview",
                    "edit",
                    "validate",
                ],
                "native_extraction": compatible,
                "dependency_analysis": False,
                "glb_export": compatible,
            },
        }

    def convert(
        self,
        source: Path,
        output_package: Path,
        *,
        timeout_seconds: int,
        max_nodes: int,
        max_triangles: int,
        progress_callback: Callable[[str, int, str], None] | None,
        cancellation_callback: Callable[[], None] | None,
    ) -> None:
        progress_path = output_package.with_suffix(".progress.json")
        command = [
            os.environ.get("MODELRYX_CONVERTER_PYTHON", "python"),
            os.environ.get(
                "MODELRYX_CONVERTER_SCRIPT",
                str(Path(__file__).resolve().parent / "converter/fcstd_to_modelryx_glb.py"),
            ),
            str(source),
            "--output",
            str(output_package),
            "--max-nodes",
            str(max_nodes),
            "--max-triangles",
            str(max_triangles),
            "--progress-file",
            str(progress_path),
        ]
        _run_process(
            command, output_package, timeout_seconds=timeout_seconds,
            cancellation_callback=cancellation_callback, progress_path=progress_path,
            progress_callback=progress_callback,
        )
        vendor_metadata = output_package.with_suffix(".freecad-raw-metadata.json")
        standard_metadata = output_package.with_suffix(".cad-raw-metadata.json")
        if vendor_metadata.is_file():
            shutil.copyfile(vendor_metadata, standard_metadata)

    def execute_plan(
        self,
        plan: Path,
        source: Path,
        output: Path,
        report: Path,
        *,
        timeout_seconds: int,
        cancellation_callback: Callable[[], None] | None = None,
    ) -> None:
        command = [
            os.environ.get("MODELRYX_CONVERTER_PYTHON", "python"),
            os.environ.get(
                "MODELRYX_CAD_EXECUTOR_SCRIPT",
                str(Path(__file__).resolve().parent / "converter/execute_cad_plan.py"),
            ),
            "--plan",
            str(plan),
            "--source",
            str(source),
            "--output",
            str(output),
            "--report",
            str(report),
        ]
        _run_process(
            command, report, timeout_seconds=timeout_seconds,
            cancellation_callback=cancellation_callback,
        )


def create_adapter() -> FreeCADWorkerAdapter:
    return FreeCADWorkerAdapter()
