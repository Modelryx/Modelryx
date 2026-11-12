"""Validate the NX worker boundary before downloading or publishing remote output."""

import re
from pathlib import PurePosixPath
from urllib.parse import quote

NX_OUTPUT_MEDIA_TYPES = {
    "native-snapshot": "application/json",
    "part-metadata": "application/json",
    "model-preview": "application/json",
    "step": "model/step",
    "parasolid": "application/x-parasolid",
    "glb": "model/gltf-binary",
    "drawing-pdf": "application/pdf",
}


def nx_document_kind(artifact_type: str | None, item_type: str | None) -> str:
    """NX uses .prt for every kind; the managed source artifact identifies drawings."""
    if artifact_type == "DRAWING":
        return "drawing"
    return "assembly" if item_type == "ASSEMBLY" else "part"


def validate_nx_worker_result(request: dict, result: dict) -> None:
    """Bind output identity and authenticated download paths to the submitted job."""
    if not isinstance(result, dict) or any(
        result.get(key) != request.get(key)
        for key in ("contractVersion", "jobId", "attempt", "cadSystem", "configuration")
    ):
        raise RuntimeError("NX worker returned a different job identity or configuration")
    source_digest = result.get("sourceSha256")
    if (
        not isinstance(source_digest, str)
        or source_digest.casefold() != request["source"]["sha256"].casefold()
    ):
        raise RuntimeError("NX worker result refers to a different source snapshot")
    if not isinstance(result.get("adapterVersion"), str) or not result["adapterVersion"].strip():
        raise RuntimeError("NX worker result omits its adapter version")
    warnings = result.get("warnings")
    if (
        not isinstance(warnings, list)
        or any(not isinstance(value, str) or not value.strip() for value in warnings)
        or result.get("status") != ("partial" if warnings else "complete")
    ):
        raise RuntimeError("NX worker returned inconsistent result quality")
    manifests = result.get("outputs")
    if not isinstance(manifests, list):
        raise RuntimeError("NX worker result omits its output manifest")
    roles: set[str] = set()
    for manifest in manifests:
        if not isinstance(manifest, dict):
            raise RuntimeError("NX worker returned an invalid output manifest")
        role = manifest.get("role")
        if not isinstance(role, str) or role not in NX_OUTPUT_MEDIA_TYPES or role in roles:
            raise RuntimeError("NX worker returned an unsupported or duplicate output role")
        roles.add(role)
        # A worker-supplied absolute URL must never receive the internal worker credential.
        expected_path = f"/api/v1/jobs/{request['jobId']}/outputs/{quote(role, safe='')}"
        if manifest.get("downloadUrl") != expected_path:
            raise RuntimeError("NX worker output download path does not belong to this job")
        media_type = manifest.get("mediaType")
        size = manifest.get("size")
        digest = manifest.get("sha256")
        if (
            not isinstance(media_type, str)
            or media_type.casefold() != NX_OUTPUT_MEDIA_TYPES[role]
            or type(size) is not int
            or size <= 0
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None
        ):
            raise RuntimeError(f"NX worker returned invalid output metadata for {role}")
        provenance = manifest.get("provenance")
        if not isinstance(provenance, dict) or any(
            not isinstance(provenance.get(key), str) or not provenance[key].strip()
            for key in ("source_role", "generator")
        ):
            raise RuntimeError(f"NX worker output {role} omits its provenance")
    if not set(request["requiredOutputs"]).issubset(roles):
        raise RuntimeError("NX worker omitted required outputs")


def validate_nx_worker_metadata(request: dict, native: dict, metadata: dict) -> None:
    """Prevent native or normalized evidence for another document from being published."""
    expected_filename = PurePosixPath(request["source"]["rootPath"].replace("\\", "/")).name
    if not isinstance(native, dict) or native.get("source_filename") != expected_filename:
        raise RuntimeError("NX snapshot belongs to a different root document")
    if native.get("document_type") != request["documentKind"]:
        raise RuntimeError("NX snapshot document kind differs from the submitted document")
    source = metadata.get("source") if isinstance(metadata, dict) else None
    evidence = metadata.get("native") if isinstance(metadata, dict) else None
    if (
        not isinstance(source, dict)
        or source.get("system") != "siemens_nx"
        or not isinstance(evidence, dict)
        or evidence.get("document_type") != native["document_type"]
    ):
        raise RuntimeError("NX normalized metadata does not match its native source evidence")
