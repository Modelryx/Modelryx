from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from pydantic import ValidationError

from modelryx.cad.models import CADWorkerCapabilities, CADWorkerSummary
from modelryx.models import WorkerHeartbeat


def _capabilities(heartbeat: WorkerHeartbeat) -> CADWorkerCapabilities | None:
    payload = dict(heartbeat.capabilities_json or {})
    payload.setdefault("worker_version", heartbeat.worker_version)
    payload.setdefault("cad_version", heartbeat.application_version or "unknown")
    try:
        return CADWorkerCapabilities.model_validate(payload)
    except ValidationError:
        return None


def _timestamp(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _eligible_heartbeats(
    heartbeats: Iterable[WorkerHeartbeat], *, cutoff: datetime | None
) -> list[WorkerHeartbeat]:
    values = [
        heartbeat
        for heartbeat in heartbeats
        if heartbeat.compatibility_state == "COMPATIBLE"
        and heartbeat.status in {"IDLE", "BUSY"}
        and (cutoff is None or _timestamp(heartbeat.last_heartbeat_at) >= _timestamp(cutoff))
    ]
    return sorted(values, key=lambda value: _timestamp(value.last_heartbeat_at), reverse=True)


def worker_capabilities(
    worker_id: str,
    heartbeats: Iterable[WorkerHeartbeat],
    *,
    cutoff: datetime | None = None,
) -> CADWorkerCapabilities:
    for heartbeat in _eligible_heartbeats(heartbeats, cutoff=cutoff):
        capabilities = _capabilities(heartbeat)
        if capabilities is not None and capabilities.worker_id == worker_id:
            return capabilities
    raise ValueError(f"Unknown or unavailable CAD worker: {worker_id}")


def worker_summaries(
    heartbeats: Iterable[WorkerHeartbeat], *, cutoff: datetime | None = None
) -> list[CADWorkerSummary]:
    summaries: dict[str, CADWorkerSummary] = {}
    for heartbeat in _eligible_heartbeats(heartbeats, cutoff=cutoff):
        capabilities = _capabilities(heartbeat)
        if capabilities is None or capabilities.worker_id in summaries:
            continue
        summaries[capabilities.worker_id] = CADWorkerSummary(
            worker_id=capabilities.worker_id,
            cad_system=capabilities.cad_system,
            worker_version=capabilities.worker_version,
            protocol_version=capabilities.protocol_version,
            available=True,
            mutation_enabled=bool(capabilities.mutate),
        )
    return sorted(summaries.values(), key=lambda value: value.worker_id)
