"""Dispatch CAD conversion to the standalone worker."""

import hashlib
import json
import re
import time
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
from modelryx_worker_sdk import validate_result as validate_worker_result

OUTPUTS = {
    "modelryx-preview": ("preview.pfg", "application/zip"),
    "part-metadata": ("preview.part-metadata.json", "application/json"),
    "native-snapshot": ("preview.cad-raw-metadata.json", "application/json"),
}


def convert_remote(
    source, workspace, output, settings, job, document_kind, check_cancelled, report
):
    if not settings.internal_worker_key:
        raise RuntimeError("The internal worker key is not configured")
    root_path = source.relative_to(workspace).as_posix()
    dependencies = [path for path in workspace.rglob("*") if path.is_file()]
    if len(dependencies) == 1:
        upload = source
        media_type = "application/octet-stream"
    else:
        upload = workspace.parent / f"{workspace.name}-source.zip"
        with zipfile.ZipFile(upload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(dependencies):
                archive.write(path, path.relative_to(workspace).as_posix())
        media_type = "application/zip"

    digest = hashlib.sha256()
    with upload.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            check_cancelled()
            digest.update(chunk)
    remote_id = str(uuid.uuid5(job.id, f"attempt:{max(job.attempt_count, 1)}"))
    request = {
        "contractVersion": "1.0",
        "jobId": remote_id,
        "attempt": max(job.attempt_count, 1),
        "documentKind": document_kind,
        "source": {
            "snapshotId": str(job.snapshot_id),
            "sha256": digest.hexdigest(),
            "size": upload.stat().st_size,
            "rootPath": root_path,
        },
        "configuration": None,
        "requiredOutputs": list(OUTPUTS),
        "preferredOutputs": [],
        "expiresAt": (
            datetime.now(UTC) + timedelta(seconds=settings.converter_timeout_seconds)
        ).isoformat(),
    }
    from modelryx.services.cad_worker_settings import worker_default

    endpoint = worker_default(settings)
    if not endpoint:
        raise RuntimeError("The external CAD endpoint is not configured")
    base = endpoint.rstrip("/")
    headers = {"X-Modelryx-Worker-Key": settings.internal_worker_key}
    deadline = time.monotonic() + settings.converter_timeout_seconds
    try:
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            capabilities = client.get(f"{base}/api/v1/capabilities", headers=headers)
            capabilities.raise_for_status()
            advertised = capabilities.json()
            if (
                advertised.get("contractVersion") != "1.0"
                or not isinstance(advertised.get("cadSystem"), str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}", advertised["cadSystem"])
                or document_kind not in advertised.get("documentKinds", [])
                or not set(OUTPUTS).issubset(advertised.get("outputRoles", []))
            ):
                raise RuntimeError("Standalone CAD worker lacks required capabilities")
            request["cadSystem"] = advertised["cadSystem"]
            with upload.open("rb") as stream:
                response = client.post(
                    f"{base}/api/v1/jobs",
                    headers=headers,
                    files={
                        "request": ("request.json", json.dumps(request), "application/json"),
                        "source": (upload.name, stream, media_type),
                    },
                )
            response.raise_for_status()
            while True:
                try:
                    check_cancelled()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Standalone CAD worker timed out")
                except Exception:
                    try:
                        client.post(f"{base}/api/v1/jobs/{remote_id}/cancel", headers=headers)
                    except httpx.HTTPError:
                        pass
                    raise
                response = client.get(f"{base}/api/v1/jobs/{remote_id}", headers=headers)
                response.raise_for_status()
                record = response.json()
                if record.get("id") != remote_id:
                    raise RuntimeError("Standalone CAD worker returned a different job")
                state = record.get("state")
                if state in {"ReadyToPublish", "Partial"}:
                    break
                if state in {"Failed", "Cancelled", "Expired"}:
                    raise RuntimeError(f"Standalone CAD job ended in {state}")
                if state not in {"Queued", "Reconstructing", "Extracting", "Validating"}:
                    raise RuntimeError("Standalone CAD worker returned an invalid state")
                report(
                    str(record.get("stage") or "CONVERTING")[:32],
                    min(69, max(20, int(record.get("progress") or 20))),
                    "Processing with the standalone CAD worker.",
                )
                time.sleep(1)
            result = record.get("result")
            validate_result(request, result, state, settings, advertised.get("adapterVersion"))
            for manifest in result["outputs"]:
                role = manifest["role"]
                path = output.parent / OUTPUTS[role][0]
                digest = hashlib.sha256()
                size = 0
                with client.stream(
                    "GET", base + manifest["downloadUrl"], headers=headers
                ) as response:
                    response.raise_for_status()
                    with path.open("wb") as stream:
                        for chunk in response.iter_bytes():
                            check_cancelled()
                            size += len(chunk)
                            if size > manifest["size"]:
                                raise RuntimeError(f"CAD output {role} exceeds its declared size")
                            digest.update(chunk)
                            stream.write(chunk)
                if size != manifest["size"] or digest.hexdigest() != manifest["sha256"].lower():
                    raise RuntimeError(f"CAD output {role} failed integrity validation")
    finally:
        if upload != source:
            upload.unlink(missing_ok=True)


def validate_result(request, result, state, settings, expected_adapter_version=None):
    limits = {
        role: (
            media_type,
            settings.max_preview_package_bytes
            if role == "modelryx-preview"
            else settings.max_preview_bom_bytes,
        )
        for role, (_, media_type) in OUTPUTS.items()
    }
    try:
        validate_worker_result(request, result, allowed_outputs=limits)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"Standalone CAD result is invalid: {error}") from error
    if (
        expected_adapter_version is not None
        and result["adapterVersion"] != expected_adapter_version
    ) or state != ("Partial" if result["warnings"] else "ReadyToPublish"):
        raise RuntimeError("Standalone CAD result has invalid status or converter version")
