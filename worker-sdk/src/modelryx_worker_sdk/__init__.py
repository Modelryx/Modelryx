"""Shared helpers for the Modelryx worker protocol."""

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

CONTRACT_VERSION = "1.0"

_ROLE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_SHA256 = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)


def output_path(job_id: str, role: str) -> str:
    """Build a job-scoped HTTP path for an output with a protocol role name."""
    if not _ROLE.fullmatch(role):
        raise ValueError(f"Invalid worker output role: {role!r}")
    return f"/api/v1/jobs/{UUID(job_id)}/outputs/{role}"


def describe_output(
    job_id: str,
    role: str,
    file: Path,
    media_type: str,
    *,
    generator: str,
    source_role: str,
) -> dict:
    """Describe a finalized output file before making the job publishable."""
    download_url = output_path(job_id, role)
    if not all(value.strip() for value in (media_type, generator, source_role)):
        raise ValueError("Worker output media type and provenance are required")
    digest = hashlib.sha256()
    size = 0
    with file.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    if size == 0:
        raise ValueError(f"Worker output {role} is empty")
    return {
        "role": role,
        "mediaType": media_type,
        "sha256": digest.hexdigest(),
        "size": size,
        "downloadUrl": download_url,
        "provenance": {"generator": generator, "source_role": source_role},
    }


def validate_result(
    request: dict,
    result: object,
    *,
    allowed_outputs: Mapping[str, tuple[str, int]],
) -> None:
    """Check a result before downloads using the consumer's media types and byte limits."""
    if not isinstance(result, dict):
        raise TypeError("Worker result must be an object")
    for field in ("contractVersion", "jobId", "attempt", "cadSystem", "configuration"):
        if result.get(field) != request.get(field):
            raise ValueError(f"Worker result does not match the request's {field}")

    source_digest = result.get("sourceSha256")
    if (
        not isinstance(source_digest, str)
        or source_digest.lower() != request["source"]["sha256"].lower()
    ):
        raise ValueError("Worker result does not match the submitted source")

    adapter_version = result.get("adapterVersion")
    if not isinstance(adapter_version, str) or not adapter_version.strip():
        raise ValueError("Worker result requires an adapter version")
    warnings = result.get("warnings")
    if not isinstance(warnings, list) or any(
        not isinstance(warning, str) or not warning.strip() for warning in warnings
    ):
        raise ValueError("Worker result warnings must be a list of nonempty strings")
    if result.get("status") != ("partial" if warnings else "complete"):
        raise ValueError("Worker result status does not match its warnings")

    outputs = result.get("outputs")
    if not isinstance(outputs, list):
        raise TypeError("Worker result requires an output list")

    requested = set(request["requiredOutputs"]) | set(
        request.get("preferredOutputs") or []
    )
    seen: set[str] = set()
    for output in outputs:
        if not isinstance(output, dict):
            raise TypeError("Worker output must be an object")
        role = output.get("role")
        if (
            not isinstance(role, str)
            or role not in requested
            or role not in allowed_outputs
            or role in seen
        ):
            raise ValueError(f"Unexpected or duplicate worker output role: {role!r}")
        media_type, max_bytes = allowed_outputs[role]
        size = output.get("size")
        digest = output.get("sha256")
        if (
            output.get("downloadUrl") != output_path(request["jobId"], role)
            or output.get("mediaType") != media_type
            or type(size) is not int
            or not 0 < size <= max_bytes
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
        ):
            raise ValueError(f"Invalid manifest for worker output {role}")
        provenance = output.get("provenance")
        if not isinstance(provenance, dict) or any(
            not isinstance(provenance.get(field), str) or not provenance[field].strip()
            for field in ("generator", "source_role")
        ):
            raise ValueError(f"Missing provenance for worker output {role}")
        seen.add(role)

    if not set(request["requiredOutputs"]).issubset(seen):
        raise ValueError("Worker result omitted required outputs")
