import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import ApplicationSetting, WorkerHeartbeat

router = APIRouter(prefix="/api/v1/workers", tags=["worker-health"])
Db = Annotated[Session, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]
Actor = Annotated[Principal, Depends(current_principal)]


@router.get("/external-formats")
def external_formats(db: Db, _: Actor) -> list[dict]:
    """Expose supported formats without exposing worker endpoints or credentials."""
    from modelryx.services.external_workers import registered_workers

    # STEP retains its built-in path. FreeCAD registrations opt into the external path;
    # without one, the existing embedded/remote FreeCAD processing remains available.
    built_in_extensions = {".step", ".stp"}
    return [
        {
            "id": worker["id"],
            "cadSystem": worker["capabilities"]["cadSystem"],
            "workerName": worker["capabilities"]["workerName"],
            "workerVersion": worker["capabilities"]["workerVersion"],
            "sourceExtensions": [
                extension
                for extension in worker["capabilities"]["sourceExtensions"]
                if extension not in built_in_extensions
            ],
            "documentKinds": worker["capabilities"]["documentKinds"],
            "dependencyAnalysis": worker["capabilities"]["dependencyAnalysis"],
        }
        for worker in registered_workers(db)
        if worker["enabled"]
        and any(
            extension not in built_in_extensions
            for extension in worker["capabilities"]["sourceExtensions"]
        )
    ]


class HeartbeatRequest(BaseModel):
    worker_type: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z][A-Z0-9_-]*$")
    machine_name: str = Field(min_length=1, max_length=255)
    worker_version: str = Field(min_length=1, max_length=100)
    application_version: str | None = Field(default=None, max_length=100)
    adapter_version: str | None = Field(default=None, max_length=100)
    compatibility_state: Literal["COMPATIBLE", "INCOMPATIBLE", "UNKNOWN"] = "UNKNOWN"
    capabilities: dict[str, Any] = Field(default_factory=dict)
    status: Literal["IDLE", "BUSY", "DEGRADED"] = "IDLE"
    current_job_id: uuid.UUID | None = None
    detail: str | None = Field(default=None, max_length=500)


@router.put("/heartbeat", status_code=204)
def record_heartbeat(
    payload: HeartbeatRequest,
    db: Db,
    settings: Config,
    x_modelryx_worker_key: Annotated[str | None, Header()] = None,
) -> Response:
    expected = settings.internal_worker_key
    if (
        not expected
        or not x_modelryx_worker_key
        or not hmac.compare_digest(expected, x_modelryx_worker_key)
    ):
        raise HTTPException(status_code=401, detail="Invalid worker credential")
    values = {
        **payload.model_dump(exclude={"capabilities"}),
        "capabilities_json": payload.capabilities,
        "last_heartbeat_at": datetime.now(UTC),
    }
    statement = insert(WorkerHeartbeat).values(**values)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[WorkerHeartbeat.worker_type, WorkerHeartbeat.machine_name],
            set_={
                key: value
                for key, value in values.items()
                if key not in {"worker_type", "machine_name"}
            },
        )
    )
    db.commit()
    return Response(status_code=204)


class WorkerPreflightResponse(BaseModel):
    worker_type: str
    available: bool
    compatible: bool
    status: str | None = None
    message: str
    application_version: str | None = None
    adapter_version: str | None = None
    capabilities: dict = Field(default_factory=dict)


def _best_heartbeat(
    db: Session, worker_type: str, settings: Settings
) -> tuple[WorkerHeartbeat | None, bool]:
    rows = list(
        db.scalars(
            select(WorkerHeartbeat)
            .where(WorkerHeartbeat.worker_type == worker_type)
            .order_by(WorkerHeartbeat.last_heartbeat_at.desc())
        )
    )
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.worker_heartbeat_timeout_seconds)
    online = [row for row in rows if row.last_heartbeat_at >= cutoff]
    preferred = next(
        (row for row in online if row.compatibility_state != "INCOMPATIBLE"),
        online[0] if online else (rows[0] if rows else None),
    )
    return preferred, bool(online)


@router.get("/preflight", response_model=WorkerPreflightResponse)
async def worker_preflight(
    worker_type: Annotated[str, Query(min_length=2, max_length=32, pattern=r"^[A-Z][A-Z0-9_-]*$")],
    db: Db,
    _: Actor,
    settings: Config,
) -> WorkerPreflightResponse:
    if worker_type == "FREECAD":
        from modelryx.api.admin import _probe_freecad_worker
        from modelryx.services.external_workers import (
            AmbiguousWorkerError,
            matching_worker,
            validate_capabilities,
        )
        from modelryx.services.freecad_settings import FREECAD_WORKER_KEY, configured_url

        # Follow processing precedence. A selected remote service must not be
        # masked by a healthy embedded heartbeat (or require one to be online).
        try:
            external = matching_worker(db, "source.FCStd", "freecad")
        except AmbiguousWorkerError:
            return WorkerPreflightResponse(
                worker_type=worker_type, available=False, compatible=False,
                message=(
                    "Multiple enabled FreeCAD workers match this format. "
                    "Resolve the conflicting registrations."
                ),
            )
        if external is not None:
            capabilities = {}
            available = False
            try:
                if not settings.internal_worker_key:
                    raise ValueError("The internal worker key is not configured.")
                async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
                    response = await client.get(f"{external['url'].rstrip('/')}/ready")
                    response.raise_for_status()
                    response = await client.get(
                        f"{external['url'].rstrip('/')}/api/v1/capabilities",
                        headers={"X-Modelryx-Worker-Key": settings.internal_worker_key},
                    )
                    response.raise_for_status()
                    capabilities = response.json()
                    if validate_capabilities(capabilities) != external["capabilities"]:
                        raise ValueError("Worker capabilities changed; register the worker again.")
                available = True
                message = "External FreeCAD worker is online and compatible."
            except (httpx.HTTPError, ValueError, TypeError):
                message = (
                    "The selected external FreeCAD worker is offline or incompatible. "
                    "Check its readiness, shared key and registration in External CAD Workers."
                )
            return WorkerPreflightResponse(
                worker_type=worker_type, available=available, compatible=available,
                status="IDLE" if available else None, message=message,
                adapter_version=capabilities.get("workerVersion") if available else None,
                capabilities=capabilities if available else {},
            )
        remote_url = configured_url(db, FREECAD_WORKER_KEY, settings.freecad_worker_url)
        if remote_url:
            connection = await _probe_freecad_worker(
                "freecad", remote_url, settings.internal_worker_key
            )
            available = connection.availability == "ONLINE"
            return WorkerPreflightResponse(
                worker_type=worker_type, available=available, compatible=available,
                status="IDLE" if available else None,
                message=(
                    "Standalone FreeCAD worker is online and compatible."
                    if available else (
                        "The configured standalone FreeCAD worker is offline or incompatible. "
                        "Check its status in Operations."
                    )
                ),
                adapter_version=connection.capabilities.get("adapterVersion"),
                capabilities=connection.capabilities,
            )
    # Remote Windows CAD adapters pre-date heartbeat registration. Keep their
    # compatibility probe while allowing every other adapter to use the common
    # heartbeat protocol without a server-side product whitelist.
    if worker_type in {"AUTOCAD", "INVENTOR"}:
        from modelryx.api.admin import _probe_cad_worker

        cad_system = worker_type.casefold()
        url = db.scalar(
            select(ApplicationSetting.value).where(
                ApplicationSetting.key == f"cad.{cad_system}.server_url"
            )
        )
        connection = await _probe_cad_worker(cad_system, url, settings.internal_worker_key)
        available = connection.availability == "ONLINE"
        capabilities = connection.capabilities
        return WorkerPreflightResponse(
            worker_type=worker_type,
            available=available,
            compatible=available,
            status="IDLE" if available else None,
            message=(
                f"{worker_type} server is online and compatible."
                if available
                else connection.detail
                or f"{worker_type} processing server is not configured or offline."
            ),
            adapter_version=capabilities.get("adapterVersion"),
            capabilities=capabilities,
        )
    heartbeat, available = _best_heartbeat(db, worker_type, settings)
    compatible = bool(available and heartbeat and heartbeat.compatibility_state != "INCOMPATIBLE")
    if not available:
        message = (
            f"{worker_type} processing is currently unavailable. You may stage files, "
            "but processing will wait for an online worker."
        )
    elif not compatible:
        message = heartbeat.detail or f"{worker_type} worker is incompatible with this server."
    elif heartbeat.status == "DEGRADED":
        message = heartbeat.detail or f"{worker_type} worker is online with limited capabilities."
    else:
        message = f"{worker_type} worker is online and compatible."
    return WorkerPreflightResponse(
        worker_type=worker_type,
        available=available,
        compatible=compatible,
        status=heartbeat.status if heartbeat else None,
        message=message,
        application_version=heartbeat.application_version if heartbeat else None,
        adapter_version=heartbeat.adapter_version if heartbeat else None,
        capabilities=heartbeat.capabilities_json if heartbeat else {},
    )
