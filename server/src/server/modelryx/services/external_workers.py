"""Configuration and capability matching for standalone CAD workers."""

import json
import re
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.core.config import Settings, get_settings
from modelryx.models import ApplicationSetting
from modelryx.services.worker_output_policy import validate_processing_profile

PREFIX = "external_worker."
JOB_TYPE = "EXTERNAL_CAD_TO_MODELRYX"
REQUIRED_OUTPUTS = frozenset({"native-snapshot", "part-metadata", "model-preview"})
DOCUMENT_KINDS = frozenset({"part", "assembly", "drawing", "model", "presentation"})


class AmbiguousWorkerError(ValueError):
    """More than one enabled worker matches a source."""


def _capability_system(value: object) -> str:
    if not isinstance(value, dict) or value.get("contractVersion") != "1.0":
        raise ValueError("Worker must advertise contract version 1.0")
    system = value.get("cadSystem")
    if not isinstance(system, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}", system):
        raise ValueError("Worker CAD system contains invalid characters")
    return system


def validate_dependency_capabilities(value: object) -> dict:
    """Preprocessors advertise analysis support without conversion output roles."""
    system = _capability_system(value)
    if value.get("dependencyAnalysis") is not True:
        raise ValueError("The endpoint does not advertise dependency analysis")
    return {"contractVersion": "1.0", "cadSystem": system, "dependencyAnalysis": True}


def validate_capabilities(value: object) -> dict:
    """Accept only a bounded, supported subset of the versioned worker contract."""
    system = _capability_system(value)
    name = value.get("workerName", system)
    version = value.get("workerVersion", "unknown")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("Worker name must contain between 1 and 100 characters")
    if not isinstance(version, str) or not 1 <= len(version.strip()) <= 100:
        raise ValueError("Worker version must contain between 1 and 100 characters")

    def strings(name: str, pattern: str) -> list[str]:
        values = value.get(name)
        if not isinstance(values, list) or not 1 <= len(values) <= 32:
            raise ValueError(f"Worker {name} must be a nonempty list of at most 32 entries")
        if any(not isinstance(item, str) or not re.fullmatch(pattern, item) for item in values):
            raise ValueError(f"Worker {name} contains an invalid entry")
        return sorted(set(values))

    kinds = strings("documentKinds", r"[a-z][a-z0-9-]{0,31}")
    extensions = [item.casefold() for item in strings("sourceExtensions", r"\.[A-Za-z0-9]{1,16}")]
    extensions = sorted(set(extensions))
    outputs = strings("outputRoles", r"[a-z][a-z0-9-]{0,63}")
    if not set(kinds) <= DOCUMENT_KINDS:
        raise ValueError("Worker advertises an unsupported document kind")
    if not REQUIRED_OUTPUTS <= set(outputs):
        raise ValueError("Worker must provide native-snapshot, part-metadata, and model-preview")
    dependency_analysis = value.get("dependencyAnalysis", False)
    if type(dependency_analysis) is not bool:
        raise ValueError("Worker dependencyAnalysis must be a boolean")
    capabilities = {
        "contractVersion": "1.0",
        "cadSystem": system,
        "workerName": name.strip(),
        "workerVersion": version.strip(),
        "documentKinds": kinds,
        "sourceExtensions": extensions,
        "outputRoles": outputs,
        "dependencyAnalysis": dependency_analysis,
    }
    if "processingProfile" in value:
        capabilities["processingProfile"] = validate_processing_profile(
            value["processingProfile"], kinds, outputs
        )
    return capabilities


def registered_workers(db: Session) -> list[dict]:
    rows = db.scalars(
        select(ApplicationSetting)
        .where(ApplicationSetting.key.startswith(PREFIX, autoescape=True))
        .order_by(ApplicationSetting.key)
    )
    result = []
    for row in rows:
        try:
            value = json.loads(row.value)
            capabilities = validate_capabilities(value["capabilities"])
            if not isinstance(value.get("url"), str) or type(value.get("enabled")) is not bool:
                continue
            result.append({"id": row.key[len(PREFIX) :], **value, "capabilities": capabilities})
        except (ValueError, TypeError, KeyError):
            continue
    return result


def matching_worker(
    db: Session, filename: str, cad_system: str, settings: Settings | None = None
) -> dict | None:
    """Match a system and extension; never guess when multiple workers qualify."""
    extension = Path(filename).suffix.casefold()
    candidates = [
        w
        for w in discover_workers(db, settings or get_settings())
        if w["enabled"] and extension in w["capabilities"]["sourceExtensions"]
    ]
    normalized = re.sub(r"[^a-z0-9]", "", cad_system.casefold())
    if normalized and normalized != "unknown":
        candidates = [
            w
            for w in candidates
            if re.sub(r"[^a-z0-9]", "", w["capabilities"]["cadSystem"].casefold()) == normalized
        ]
    if len(candidates) > 1:
        raise AmbiguousWorkerError("More than one enabled worker matches this CAD source")
    return candidates[0] if candidates else None


def discover_workers(db: Session, settings: Settings) -> list[dict]:
    """Merge registrations with capabilities advertised by the configured external server.

    Registrations remain authoritative. URL-only deployments expose the same neutral format
    contract, without inventing extensions or requiring a second administrator registration.
    """
    workers = registered_workers(db)
    if not settings.internal_worker_key:
        return workers
    from modelryx.services.cad_worker_settings import CAD_WORKER_KEY, configured_url, worker_default

    url = configured_url(db, CAD_WORKER_KEY, worker_default(settings))
    if not url or any(w["enabled"] and w["url"].rstrip("/") == url.rstrip("/") for w in workers):
        return workers
    try:
        with httpx.Client(timeout=3.0, follow_redirects=False) as client:
            response = client.get(
                f"{url.rstrip('/')}/api/v1/capabilities",
                headers={"X-Modelryx-Worker-Key": settings.internal_worker_key},
            )
            response.raise_for_status()
            capabilities = validate_capabilities(response.json())
    except (httpx.HTTPError, ValueError, TypeError):
        return workers
    if any(
        w["enabled"]
        and w["capabilities"]["cadSystem"].casefold() == capabilities["cadSystem"].casefold()
        for w in workers
    ):
        return workers
    identifier = re.sub(r"[^a-z0-9_-]", "", capabilities["cadSystem"].casefold())
    if not identifier or not identifier[0].isalpha():
        identifier = "configured-" + identifier
    identifier = identifier[:64]
    if any(w["id"] == identifier for w in workers):
        identifier = "configured-" + identifier[:53]
    return [*workers, {"id": identifier, "url": url, "enabled": True, "capabilities": capabilities}]
