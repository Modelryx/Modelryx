"""Authenticated HTTP entry point for standalone FreeCAD conversion."""

import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from uuid import UUID

from analyzer_api import analyze
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from modelryx_worker_sdk import CONTRACT_VERSION
from pydantic import ValidationError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from .conversion import DOCUMENT_KINDS, MAX_SOURCE_BYTES, OUTPUTS, PACKAGE_TYPES, ConversionRequest
from .jobs import JobConflict, JobStore, QueueFull

MAX_REQUEST_BYTES = 64 * 1024
MAX_BODY_BYTES = MAX_SOURCE_BYTES + MAX_REQUEST_BYTES + 64 * 1024


def create_app(*, adapter=None, root: Path | None = None, worker_key: str | None = None):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        key = worker_key or os.environ.get("MODELRYX_INTERNAL_WORKER_KEY")
        if not key:
            raise RuntimeError("MODELRYX_INTERNAL_WORKER_KEY is required")
        native = adapter
        if native is None:
            from modelryx_freecad_adapter import FreeCADWorkerAdapter

            native = FreeCADWorkerAdapter()
        store = JobStore(
            root
            if root is not None
            else Path(os.environ.get("MODELRYX_FREECAD_JOB_ROOT", "/data/jobs")),
            native,
        )
        app.state.worker_key = key.encode()
        app.state.store = store
        app.state.upload_lock = Lock()
        store.start()
        try:
            yield
        finally:
            await run_in_threadpool(store.close)

    app = FastAPI(title="Modelryx FreeCAD Worker", lifespan=lifespan)
    app.add_api_route("/analyze", analyze, methods=["POST"])

    def authenticate(request: Request):
        supplied = request.headers.get("X-Modelryx-Worker-Key", "").encode()
        if not hmac.compare_digest(supplied, request.app.state.worker_key):
            raise HTTPException(401, "Invalid worker key")

    def store(request: Request) -> JobStore:
        return request.app.state.store

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def readiness(request: Request):
        jobs = store(request)
        if (
            not jobs.thread.is_alive()
            or jobs.adapter.compatibility()[0] != "COMPATIBLE"
            or not os.access(jobs.root, os.W_OK)
        ):
            raise HTTPException(503, "FreeCAD conversion is unavailable")
        return {"status": "ready"}

    @app.get("/api/v1/capabilities", dependencies=[Depends(authenticate)])
    def capabilities():
        return {
            "contractVersion": CONTRACT_VERSION,
            "cadSystem": "freecad",
            "documentKinds": list(DOCUMENT_KINDS),
            "sourceExtensions": [".fcstd"],
            "outputRoles": list(OUTPUTS),
            "dependencyAnalysis": True,
        }

    @app.post("/api/v1/jobs", status_code=202, dependencies=[Depends(authenticate)])
    async def submit(request: Request):
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
            raise HTTPException(400, "Expected a multipart request")
        lock = request.app.state.upload_lock
        if not lock.acquire(blocking=False):
            raise HTTPException(
                429, "FreeCAD is accepting another upload", headers={"Retry-After": "1"}
            )
        try:
            try:
                parser = MultiPartParser(
                    request.headers,
                    bounded_body(request),
                    max_files=2,
                    max_fields=0,
                )
                form = await parser.parse()
            except MultiPartException as error:
                raise HTTPException(400, error.message) from error
            try:
                envelope, source = form.get("request"), form.get("source")
                if not isinstance(envelope, UploadFile) or not isinstance(source, UploadFile):
                    raise HTTPException(400, "Expected request and source file parts")
                media_type = source.content_type
                if media_type not in PACKAGE_TYPES:
                    raise HTTPException(400, "Unsupported source media type")
                payload = await envelope.read(MAX_REQUEST_BYTES + 1)
                if len(payload) > MAX_REQUEST_BYTES:
                    raise HTTPException(400, "Request JSON exceeds its size limit")
                try:
                    job = ConversionRequest.model_validate_json(payload)
                    job.check_profile()
                    record = await run_in_threadpool(
                        store(request).submit, job, source.file, media_type
                    )
                except (ValidationError, ValueError) as error:
                    raise HTTPException(
                        400, "Invalid FreeCAD job request or source bytes"
                    ) from error
                except JobConflict as error:
                    raise HTTPException(
                        409, "Job ID already refers to a different request"
                    ) from error
                except QueueFull as error:
                    raise HTTPException(
                        429, "FreeCAD cannot accept more jobs", headers={"Retry-After": "5"}
                    ) from error
                return JSONResponse(
                    record, status_code=202, headers={"Location": f"/api/v1/jobs/{job.jobId}"}
                )
            finally:
                await form.close()
        finally:
            lock.release()

    @app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(authenticate)])
    def status(job_id: UUID, request: Request):
        jobs = store(request)
        with jobs.lock:
            record = jobs.get(str(job_id))
        if record is None:
            raise HTTPException(404, "Job not found")
        return record

    @app.post("/api/v1/jobs/{job_id}/cancel", status_code=202, dependencies=[Depends(authenticate)])
    def cancel(job_id: UUID, request: Request):
        if not store(request).cancel(str(job_id)):
            raise HTTPException(404, "Job not found")
        return {"id": str(job_id)}

    @app.get("/api/v1/jobs/{job_id}/outputs/{role}", dependencies=[Depends(authenticate)])
    def output(job_id: UUID, role: str, request: Request):
        jobs = store(request)
        with jobs.lock:
            record = jobs.get(str(job_id))
            manifest = next(
                (
                    item
                    for item in (record or {}).get("result", {}).get("outputs", [])
                    if item["role"] == role
                ),
                None,
            )
            if manifest is None:
                raise HTTPException(404, "Output not found")
            file = (jobs.root / str(job_id) / OUTPUTS[role][0]).open("rb")

        def chunks():
            with file:
                while chunk := file.read(1024 * 1024):
                    yield chunk

        return StreamingResponse(
            chunks(),
            media_type=manifest["mediaType"],
            headers={"Content-Length": str(manifest["size"])},
            background=BackgroundTask(file.close),
        )

    return app


async def bounded_body(request: Request):
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise MultiPartException("Source upload exceeds its size limit")
        yield chunk


app = create_app()
