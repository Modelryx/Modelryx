from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.request import Request, urlopen

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

DATA_ROOT = Path(os.getenv("STEP_WORKER_DATA_ROOT", "/data/jobs")).resolve()
MAX_INPUT = int(os.getenv("STEP_WORKER_MAX_INPUT_BYTES", str(512 * 1024 * 1024)))
MAX_OUTPUT = int(os.getenv("STEP_WORKER_MAX_OUTPUT_BYTES", str(256 * 1024 * 1024)))
TIMEOUT = int(os.getenv("STEP_WORKER_TIMEOUT_SECONDS", "600"))
WORKERS = int(os.getenv("STEP_WORKER_CONCURRENCY", "2"))
QUEUE_CAPACITY = max(1, int(os.getenv("STEP_WORKER_QUEUE_CAPACITY", "32")))
SCRIPT = Path(os.getenv("STEP_WORKER_STEP_PROCESSOR", "/app/converter/step_to_glb.py"))
MESH_SCRIPT = Path(os.getenv("STEP_WORKER_MESH_PROCESSOR", "/app/converter/mesh_to_glb.py"))
COMPONENT_SCRIPT = Path(
    os.getenv("STEP_WORKER_COMPONENT_PROCESSOR", "/app/converter/component_bundle_to_glb.py")
)
WORKER_KEY = os.getenv("MODELRYX_INTERNAL_WORKER_KEY", "")
RETENTION_SECONDS = int(os.getenv("STEP_WORKER_RETENTION_SECONDS", "86400"))
MAX_ATTEMPTS = max(1, int(os.getenv("STEP_WORKER_MAX_ATTEMPTS", "3")))
RETRY_DELAY_SECONDS = max(0.0, float(os.getenv("STEP_WORKER_RETRY_DELAY_SECONDS", "2")))
MODELRYX_SERVER_URL = os.getenv("MODELRYX_SERVER_URL", "").rstrip("/")
HEARTBEAT_SECONDS = max(5, int(os.getenv("MODELRYX_HEARTBEAT_SECONDS", "10")))
executor = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="step-worker")
submission_slots = threading.BoundedSemaphore(WORKERS + QUEUE_CAPACITY)
app = FastAPI(title="Modelryx STEP Worker", version="2.0.0")
logger = logging.getLogger("step_worker")
active_jobs = 0
active_jobs_lock = threading.Lock()
active_processes: dict[str, subprocess.Popen[str]] = {}
active_processes_lock = threading.Lock()
cancel_requests: set[str] = set()
cancel_requests_lock = threading.Lock()
job_locks: dict[str, threading.RLock] = {}
job_locks_guard = threading.Lock()
heartbeat_task: asyncio.Task | None = None
idempotency_jobs: dict[str, str] = {}
idempotency_lock = threading.Lock()
metrics_lock = threading.Lock()
metrics_counters = {
    "submitted": 0,
    "duplicates": 0,
    "saturated": 0,
    "retries": 0,
    "recovered": 0,
    "quarantined": 0,
    "failed": 0,
}
admitted_jobs = 0


class JobCancelled(RuntimeError):
    """Raised when an authenticated cancellation interrupts conversion."""


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def job_dir(job_id: str) -> Path:
    try:
        return DATA_ROOT / uuid.UUID(job_id).hex
    except ValueError as exc:
        raise HTTPException(404, "job not found") from exc


def read_job(job_id: str) -> dict:
    path = job_dir(job_id) / "job.json"
    if not path.is_file():
        raise HTTPException(404, "job not found")
    return json.loads(path.read_text(encoding="utf-8"))


def write_job(directory: Path, record: dict) -> None:
    record["updated_at"] = now()
    temporary = directory / f"job.json.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
        temporary.replace(directory / "job.json")
    finally:
        temporary.unlink(missing_ok=True)


def quarantine_job_record(path: Path, error: Exception) -> Path:
    suffix = f"corrupt-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex}"
    quarantine = path.with_name(f"job.{suffix}.json")
    path.replace(quarantine)
    quarantine.with_suffix(quarantine.suffix + ".error.txt").write_text(
        str(error), encoding="utf-8"
    )
    increment_metric("quarantined")
    return quarantine


def increment_metric(name: str) -> None:
    with metrics_lock:
        metrics_counters[name] += 1


def job_lock(job_id: str) -> threading.RLock:
    with job_locks_guard:
        return job_locks.setdefault(job_id, threading.RLock())


def authorize(x_modelryx_worker_key: str | None) -> None:
    if not WORKER_KEY:
        raise HTTPException(503, "worker authentication is not configured")
    if x_modelryx_worker_key is None or not hmac.compare_digest(
        x_modelryx_worker_key, WORKER_KEY
    ):
        raise HTTPException(401, "invalid worker credential")


def cleanup_expired_jobs() -> int:
    cutoff = datetime.now(UTC).timestamp() - RETENTION_SECONDS
    removed = 0
    for state_path in DATA_ROOT.glob("*/job.json"):
        try:
            record = json.loads(state_path.read_text(encoding="utf-8"))
            updated = datetime.fromisoformat(
                record["updated_at"].replace("Z", "+00:00")
            ).timestamp()
            if record.get("state") in {"READY", "FAILED", "CANCELLED"} and updated < cutoff:
                shutil.rmtree(state_path.parent)
                with job_locks_guard:
                    job_locks.pop(str(record.get("id", "")), None)
                removed += 1
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
    return removed


def validate_glb(path: Path) -> None:
    size = path.stat().st_size
    if size < 12 or size > MAX_OUTPUT:
        raise ValueError(f"GLB size {size} is outside allowed range")
    header = path.read_bytes()[:12]
    if header[:4] != b"glTF" or int.from_bytes(header[4:8], "little") != 2:
        raise ValueError("converter produced an invalid GLB v2 header")
    if int.from_bytes(header[8:12], "little") != size:
        raise ValueError("GLB declared length does not match file size")


def prepare_step_outputs(directory: Path, original_name: str) -> None:
    """Normalize every STEP-owned output before the server can publish it."""
    metadata_path = directory / "part-metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("STEP processor produced invalid part metadata") from exc
    if not isinstance(metadata, dict) or metadata.get("schema") != "modelryx.part-metadata":
        raise ValueError("STEP processor produced an invalid PartMetadata document")
    source_name = Path(original_name).stem
    metadata.setdefault("identity", {})["name"] = source_name
    serialized = json.dumps(metadata, indent=2)
    metadata_path.write_text(serialized, encoding="utf-8")
    (directory / "step-raw-metadata.json").write_text(serialized, encoding="utf-8")
    preview = {
        "schemaVersion": 1,
        "rootOccurrenceId": "step-root",
        "items": [
            {
                "name": source_name,
                "nodeType": "part",
                "partId": "step-model",
                "occurrenceId": "step-model",
                "parentOccurrenceId": "step-root",
                "source": {"system": "STEP", "filename": original_name},
            }
        ],
    }
    (directory / "model-preview.json").write_text(
        json.dumps(preview, indent=2), encoding="utf-8"
    )


def request_cancel(job_id: str) -> None:
    with cancel_requests_lock:
        cancel_requests.add(job_id)


def is_cancel_requested(job_id: str) -> bool:
    with cancel_requests_lock:
        return job_id in cancel_requests


def clear_cancel_request(job_id: str) -> None:
    with cancel_requests_lock:
        cancel_requests.discard(job_id)


def terminate_active_process(job_id: str) -> bool:
    with active_processes_lock:
        process = active_processes.get(job_id)
    if process is None or process.poll() is not None:
        return False
    process.terminate()
    return True


def run_converter_process(job_id: str, directory: Path, input_path: Path) -> tuple[str, str]:
    converter = {
        ".stl": MESH_SCRIPT,
        ".zip": COMPONENT_SCRIPT,
    }.get(input_path.suffix.lower(), SCRIPT)
    command = [
        sys.executable,
        str(converter),
        str(input_path),
        "-o",
        str(directory / "model.glb"),
    ]
    env = dict(os.environ)
    env["MODELRYX_JOB_ID"] = job_id
    env["MODELRYX_STEP_WORKER_VERSION"] = app.version
    process = subprocess.Popen(
        command,
        cwd=directory,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    with active_processes_lock:
        active_processes[job_id] = process
    deadline = time.monotonic() + TIMEOUT
    try:
        while True:
            if is_cancel_requested(job_id):
                process.terminate()
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise JobCancelled("job cancelled by request")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.communicate()
                raise TimeoutError("conversion timed out")
            try:
                stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0:
            raise RuntimeError((stderr or stdout)[-4000:])
        return stdout, stderr
    finally:
        with active_processes_lock:
            active_processes.pop(job_id, None)


def convert(job_id: str) -> None:
    global active_jobs
    with active_jobs_lock:
        active_jobs += 1
    try:
        directory = job_dir(job_id)
        with job_lock(job_id):
            record = read_job(job_id)
            if record.get("state") == "CANCELLED":
                return
            record.update(
                state="PROCESSING",
                attempts=int(record.get("attempts", 0)) + 1,
                error=None,
            )
            write_job(directory, record)
        started = time.monotonic()
        failure: Exception | None = None
        stdout = ""
        stderr = ""
        try:
            input_name = record.get("input_name", "model.step")
            input_path = directory / input_name
            stdout, stderr = run_converter_process(job_id, directory, input_path)
            output = directory / "model.glb"
            validate_glb(output)
            if input_path.suffix.casefold() in {".step", ".stp"}:
                prepare_step_outputs(
                    directory, str(record.get("original_name") or input_name)
                )
            if stderr and stderr.strip():
                logger.warning("conversion_stderr job_id=%s output=%s", job_id, stderr[-2000:])
            if stdout and stdout.strip():
                logger.info("conversion_stdout job_id=%s output=%s", job_id, stdout[-2000:])
        except Exception as exc:  # noqa: BLE001 - conversion failures become job diagnostics
            failure = exc
        with job_lock(job_id):
            record = read_job(job_id)
            if isinstance(failure, JobCancelled) or is_cancel_requested(job_id):
                record.update(
                    state="CANCELLED",
                    error="job cancelled by request",
                    cancel_reason=record.get("cancel_reason") or "requested",
                )
                logger.info("conversion_cancelled job_id=%s", job_id)
            elif failure is not None:
                retryable = isinstance(failure, (OSError, TimeoutError))
                if retryable and int(record.get("attempts", 0)) < MAX_ATTEMPTS:
                    record.update(state="QUEUED", error=str(failure)[-4000:])
                    write_job(directory, record)
                    delay = RETRY_DELAY_SECONDS * (2 ** (int(record["attempts"]) - 1))
                    logger.warning(
                        "conversion_retry_scheduled job_id=%s attempt=%s delay_seconds=%s error=%s",
                        job_id,
                        record["attempts"],
                        delay,
                        failure,
                    )
                    increment_metric("retries")
                    if delay:
                        time.sleep(delay)
                    convert(job_id)
                    return
                record.update(state="FAILED", error=str(failure)[-4000:])
                increment_metric("failed")
                logger.error(
                    "conversion_failed job_id=%s attempt=%s error=%s",
                    job_id,
                    record.get("attempts"),
                    failure,
                    exc_info=(type(failure), failure, failure.__traceback__),
                )
            else:
                output = directory / "model.glb"
                record.update(
                    state="READY",
                    output_bytes=output.stat().st_size,
                    output_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                    error=None,
                )
                logger.info(
                    "conversion_succeeded job_id=%s input=%s output_bytes=%s duration_ms=%.2f",
                    job_id,
                    input_name,
                    record["output_bytes"],
                    (time.monotonic() - started) * 1000,
                )
            write_job(directory, record)
    finally:
        clear_cancel_request(job_id)
        with active_jobs_lock:
            active_jobs -= 1


def submit_job(job_id: str, *, block: bool) -> bool:
    """Admit one job without allowing the executor's private queue to grow unbounded."""
    global admitted_jobs
    if not submission_slots.acquire(blocking=block):
        increment_metric("saturated")
        return False
    with metrics_lock:
        admitted_jobs += 1

    def run() -> None:
        try:
            convert(job_id)
        finally:
            global admitted_jobs
            with metrics_lock:
                admitted_jobs -= 1
            submission_slots.release()

    try:
        executor.submit(run)
        return True
    except Exception:
        submission_slots.release()
        raise


def heartbeat_payload() -> dict:
    scripts_ready = all(path.is_file() for path in (SCRIPT, MESH_SCRIPT, COMPONENT_SCRIPT))
    storage_ready = DATA_ROOT.is_dir() and os.access(DATA_ROOT, os.W_OK)
    compatible = scripts_ready and storage_ready
    with active_jobs_lock:
        busy = active_jobs > 0
    return {
        "worker_type": "STEP",
        "machine_name": socket.gethostname(),
        "worker_version": app.version,
        "application_version": f"Python {platform.python_version()}",
        "adapter_version": "OpenCascade via conda worker",
        "compatibility_state": "COMPATIBLE" if compatible else "INCOMPATIBLE",
        "capabilities": {
            "step_processing": SCRIPT.is_file(),
            "mesh_to_glb": MESH_SCRIPT.is_file(),
            "component_bundle_to_glb": COMPONENT_SCRIPT.is_file(),
            "component_bundle_input_units": ["millimeter"] if COMPONENT_SCRIPT.is_file() else [],
            "max_parallel_jobs": WORKERS,
            "queue_capacity": QUEUE_CAPACITY,
            "idempotent_submission": True,
            "storage_writable": storage_ready,
        },
        "status": "BUSY" if busy else ("IDLE" if compatible else "DEGRADED"),
        "detail": None
        if compatible
        else "Converter scripts or writable job storage are unavailable.",
    }


def send_heartbeat() -> None:
    if not MODELRYX_SERVER_URL or not WORKER_KEY:
        return
    body = json.dumps(heartbeat_payload()).encode("utf-8")
    request = Request(
        f"{MODELRYX_SERVER_URL}/api/v1/workers/heartbeat",
        data=body,
        method="PUT",
        headers={
            "Content-Type": "application/json",
            "X-Modelryx-Worker-Key": WORKER_KEY,
        },
    )
    with urlopen(request, timeout=5) as response:
        if response.status != 204:
            raise RuntimeError(f"heartbeat returned HTTP {response.status}")


async def heartbeat_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(send_heartbeat)
        except Exception:
            logger.exception("step_heartbeat_failed")
        await asyncio.sleep(HEARTBEAT_SECONDS)


def response(record: dict) -> dict:
    job_id = record["id"]
    return {
        **record,
        "worker_version": app.version,
        "links": {
            "self": f"/api/v1/jobs/{job_id}",
            "glb": f"/api/v1/jobs/{job_id}/glb",
            "metadata": f"/api/v1/jobs/{job_id}/metadata",
            "preview": f"/api/v1/jobs/{job_id}/preview",
            "raw_metadata": f"/api/v1/jobs/{job_id}/raw-metadata",
            "cancel": f"/api/v1/jobs/{job_id}/cancel",
        },
    }


@app.on_event("startup")
async def startup() -> None:
    global heartbeat_task
    logging.basicConfig(
        level=os.getenv("STEP_WORKER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    for state_path in DATA_ROOT.glob("*/job.json"):
        try:
            record = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            try:
                quarantine = quarantine_job_record(state_path, exc)
                logger.error(
                    "invalid_job_record_quarantined path=%s quarantine=%s",
                    state_path,
                    quarantine,
                )
            except OSError:
                logger.exception("job_record_quarantine_failed path=%s", state_path)
            continue
        except OSError:
            logger.exception("invalid_job_record path=%s", state_path)
            continue
        idempotency_key = record.get("idempotency_key")
        if isinstance(idempotency_key, str) and idempotency_key:
            with idempotency_lock:
                idempotency_jobs[idempotency_key] = str(record["id"])
        if record.get("state") in {"QUEUED", "PROCESSING"}:
            increment_metric("recovered")
            record.update(
                state="QUEUED",
                error="job recovered after worker restart",
            )
            write_job(state_path.parent, record)
            submit_job(str(record["id"]), block=True)
    cleanup_expired_jobs()
    heartbeat_task = asyncio.create_task(heartbeat_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    if heartbeat_task is not None:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    if not SCRIPT.is_file() or not os.access(DATA_ROOT, os.W_OK):
        raise HTTPException(503, "STEP worker is not ready")
    return {"status": "ready", "processor": str(SCRIPT)}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics(x_modelryx_worker_key: str | None = Header(default=None)) -> str:
    authorize(x_modelryx_worker_key)
    with metrics_lock:
        counters = dict(metrics_counters)
        admitted = admitted_jobs
    with active_jobs_lock:
        active = active_jobs
    queued_records = []
    for path in DATA_ROOT.glob("*/job.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("state") == "QUEUED":
                queued_records.append(record)
        except (OSError, json.JSONDecodeError):
            continue
    oldest = 0.0
    if queued_records:
        created = []
        for item in queued_records:
            try:
                created.append(datetime.fromisoformat(
                    str(item["created_at"]).replace("Z", "+00:00")
                ))
            except (KeyError, ValueError):
                continue
        if created:
            oldest = max(0.0, (datetime.now(UTC) - min(created)).total_seconds())
    lines = [
        f"modelryx_step_queue_depth {len(queued_records)}",
        f"modelryx_step_queue_capacity {QUEUE_CAPACITY}",
        f"modelryx_step_jobs_admitted {admitted}",
        f"modelryx_step_active_jobs {active}",
        f"modelryx_step_oldest_queued_seconds {oldest:.3f}",
    ]
    lines.extend(f"modelryx_step_{name}_total {value}" for name, value in counters.items())
    return "\n".join(lines) + "\n"


@app.get("/capabilities")
def capabilities(x_modelryx_worker_key: str | None = Header(default=None)) -> dict:
    authorize(x_modelryx_worker_key)
    payload = heartbeat_payload()
    payload.update(
        {
            "contract_versions": ["1.0"],
            "document_kinds": ["part", "assembly"],
            "source_extensions": [".step", ".stp", ".stl", ".zip"],
            "limits": {
                "max_input_bytes": MAX_INPUT,
                "max_output_bytes": MAX_OUTPUT,
                "timeout_seconds": TIMEOUT,
            },
            "endpoints": {
                "create_job": "/api/v1/jobs",
                "job": "/api/v1/jobs/{jobId}",
                "cancel_job": "/api/v1/jobs/{jobId}/cancel",
                "glb": "/api/v1/jobs/{jobId}/glb",
                "metadata": "/api/v1/jobs/{jobId}/metadata",
                "preview": "/api/v1/jobs/{jobId}/preview",
                "raw_metadata": "/api/v1/jobs/{jobId}/raw-metadata",
            },
        }
    )
    return payload


@app.post("/api/v1/jobs", status_code=202)
async def create_job(
    source: Annotated[UploadFile, File()],
    x_modelryx_worker_key: str | None = Header(default=None),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    authorize(x_modelryx_worker_key)
    cleanup_expired_jobs()
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 128:
            raise HTTPException(400, "Idempotency-Key must contain 1 to 128 characters")
        with idempotency_lock:
            existing_id = idempotency_jobs.get(idempotency_key)
        if existing_id is not None:
            try:
                increment_metric("duplicates")
                return response(read_job(existing_id))
            except HTTPException:
                with idempotency_lock:
                    idempotency_jobs.pop(idempotency_key, None)
    suffix = Path(source.filename or "").suffix.lower()
    if suffix not in {".step", ".stp", ".stl", ".zip"}:
        raise HTTPException(400, "geometry must have a .step, .stp, .stl, or .zip extension")
    job_id = str(uuid.uuid4())
    directory = job_dir(job_id)
    directory.mkdir(parents=True)
    input_path = directory / f"model{suffix}"
    size = 0
    digest = hashlib.sha256()
    with input_path.open("wb") as output:
        while chunk := await source.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_INPUT:
                output.close()
                input_path.unlink(missing_ok=True)
                raise HTTPException(413, "geometry input exceeds configured limit")
            digest.update(chunk)
            output.write(chunk)
    record = {
        "id": job_id,
        "state": "QUEUED",
        "created_at": now(),
        "updated_at": now(),
        "input_name": input_path.name,
        "original_name": Path(source.filename or input_path.name).name,
        "input_bytes": size,
        "input_sha256": digest.hexdigest(),
        "error": None,
        "cancel_reason": None,
        "attempts": 0,
        "idempotency_key": idempotency_key,
    }
    write_job(directory, record)
    if idempotency_key is not None:
        with idempotency_lock:
            existing_id = idempotency_jobs.setdefault(idempotency_key, job_id)
        if existing_id != job_id:
            shutil.rmtree(directory, ignore_errors=True)
            increment_metric("duplicates")
            return response(read_job(existing_id))
    if not submit_job(job_id, block=False):
        if idempotency_key is not None:
            with idempotency_lock:
                if idempotency_jobs.get(idempotency_key) == job_id:
                    idempotency_jobs.pop(idempotency_key, None)
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(
            429,
            "STEP worker queue is full; retry the request later",
            headers={"Retry-After": "2"},
        )
    increment_metric("submitted")
    return response(record)


@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    x_modelryx_worker_key: str | None = Header(default=None),
    reason: str | None = None,
) -> dict:
    authorize(x_modelryx_worker_key)
    directory = job_dir(job_id)
    with job_lock(job_id):
        record = read_job(job_id)
        if record.get("state") in {"READY", "FAILED", "CANCELLED"}:
            return response(record)
        record["cancel_reason"] = reason or "requested"
        request_cancel(job_id)
        if record.get("state") == "QUEUED":
            record.update(state="CANCELLED", error="job cancelled by request")
            write_job(directory, record)
            clear_cancel_request(job_id)
            return response(record)
        terminated = terminate_active_process(job_id)
        if terminated:
            record["error"] = "cancellation requested; terminating active converter process"
        write_job(directory, record)
        return response(record)


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, x_modelryx_worker_key: str | None = Header(default=None)) -> dict:
    authorize(x_modelryx_worker_key)
    return response(read_job(job_id))


@app.get("/api/v1/jobs/{job_id}/glb")
def get_glb(job_id: str, x_modelryx_worker_key: str | None = Header(default=None)):
    authorize(x_modelryx_worker_key)
    record = read_job(job_id)
    path = job_dir(job_id) / "model.glb"
    if record["state"] != "READY" or not path.is_file():
        raise HTTPException(404, "GLB is not available")
    return FileResponse(path, media_type="model/gltf-binary", filename="model.glb")


@app.get("/api/v1/jobs/{job_id}/metadata")
def get_metadata(job_id: str, x_modelryx_worker_key: str | None = Header(default=None)):
    authorize(x_modelryx_worker_key)
    record = read_job(job_id)
    path = job_dir(job_id) / "part-metadata.json"
    if record["state"] != "READY" or not path.is_file():
        raise HTTPException(404, "STEP metadata is not available")
    return FileResponse(path, media_type="application/json", filename="part-metadata.json")


@app.get("/api/v1/jobs/{job_id}/preview")
def get_preview(job_id: str, x_modelryx_worker_key: str | None = Header(default=None)):
    authorize(x_modelryx_worker_key)
    record = read_job(job_id)
    path = job_dir(job_id) / "model-preview.json"
    if record["state"] != "READY" or not path.is_file():
        raise HTTPException(404, "STEP preview is not available")
    return FileResponse(path, media_type="application/json", filename="model-preview.json")


@app.get("/api/v1/jobs/{job_id}/raw-metadata")
def get_raw_metadata(job_id: str, x_modelryx_worker_key: str | None = Header(default=None)):
    authorize(x_modelryx_worker_key)
    record = read_job(job_id)
    path = job_dir(job_id) / "step-raw-metadata.json"
    if record["state"] != "READY" or not path.is_file():
        raise HTTPException(404, "STEP raw metadata is not available")
    return FileResponse(path, media_type="application/json", filename="step-raw-metadata.json")
