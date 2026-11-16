import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal, hash_password
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.core.roles import assign_roles, effective_role, roles_for_user, user_has_role
from modelryx.models import ApplicationSetting, AuditLog, Item, Revision, User
from modelryx.services.approval_escalations import create_overdue_approval_escalations
from modelryx.services.checkout_notifications import (
    create_checkout_expiry_notifications,
    record_checkout_notification_run,
)
from modelryx.services.external_workers import PREFIX, registered_workers, validate_capabilities
from modelryx.services.freecad_settings import FREECAD_ANALYZER_KEY, FREECAD_WORKER_KEY
from modelryx.services.item_purge import (
    cascade_plan,
    item_storage_keys,
    preview_item_purge,
    purge_item_records,
    remove_purged_files,
)
from modelryx.services.revision_purge import (
    purge_revision_records,
    revision_deletion_eligibility,
    revision_storage_keys,
)

router = APIRouter(prefix="/api/v1/admin", tags=["administration"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]
Role = Literal["reader", "engineer", "designer", "reviewer", "administrator"]
ProcessingReadiness = Literal["READY", "NOT_READY", "UNKNOWN", "NOT_APPLICABLE"]
CadWorkerConfigurationSource = Literal[
    "ADMIN_OVERRIDE", "ENVIRONMENT_DEFAULT", "ADMIN_DISABLED", "NOT_CONFIGURED"
]


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    role: Role
    roles: list[Role]
    is_active: bool
    failed_login_attempts: int
    locked_until: datetime | None
    last_login_at: datetime | None
    password_changed_at: datetime | None
    created_at: datetime


class UserActivityResponse(BaseModel):
    id: uuid.UUID
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    occurred_at: datetime
    details: dict


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    role: Role | None = None
    roles: list[Role] | None = None
    password: str = Field(min_length=8, max_length=1024)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: Role | None = None
    roles: list[Role] | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=1024)


class ItemPurgeConfirmation(BaseModel):
    item_number: str = Field(min_length=1, max_length=100)
    cascade_item_numbers: list[str] = Field(default_factory=list, max_length=500)


class ItemPurgePreviewResponse(BaseModel):
    item_id: uuid.UUID
    item_number: str
    name: str
    status: str
    revision_count: int
    artifact_count: int
    stored_bytes: int
    attachment_count: int
    blocking_reference_count: int
    blocking_item_numbers: list[str] = Field(default_factory=list)
    cascade_item_numbers: list[str] = Field(default_factory=list)
    protected_legacy_item_numbers: list[str] = Field(default_factory=list)
    external_reference_item_numbers: list[str] = Field(default_factory=list)


class ItemPurgeResponse(BaseModel):
    item_id: uuid.UUID
    item_number: str
    deleted: bool
    file_cleanup_failures: list[str]


class RevisionDeletionOption(BaseModel):
    id: uuid.UUID
    revision_code: str
    state: str
    lifecycle_state: str
    created_at: datetime
    is_current: bool
    eligible: bool
    reason: str | None = None


class RevisionDeletionConfirmation(BaseModel):
    revision_code: str = Field(min_length=1, max_length=50)


class CheckoutNotificationRunResponse(BaseModel):
    examined: int
    created: int
    warning_seconds: int


class ApprovalEscalationRunResponse(BaseModel):
    overdue_requests: int
    created: int
    expired_emergency_requests: int


class CadWorkerSettingsResponse(BaseModel):
    freecad_url: str | None = None
    freecad_analyzer_url: str | None = None


class CadWorkerSettingsUpdate(BaseModel):
    freecad_url: str | None = Field(default=None, max_length=2000)
    freecad_analyzer_url: str | None = Field(default=None, max_length=2000)


class CadWorkerConnectionStatus(BaseModel):
    cad_system: Literal["freecad", "freecad_analyzer"]
    url: str | None
    availability: Literal["ONLINE", "OFFLINE", "NOT_CONFIGURED"]
    configuration_source: CadWorkerConfigurationSource = "NOT_CONFIGURED"
    checked_at: datetime
    detail: str | None = None
    capabilities: dict = Field(default_factory=dict)
    processing_readiness: ProcessingReadiness = "NOT_APPLICABLE"
    readiness_code: str | None = None
    readiness_detail: str | None = None


class CadWorkerStatusResponse(BaseModel):
    freecad: CadWorkerConnectionStatus
    freecad_analyzer: CadWorkerConnectionStatus


CAD_WORKER_SETTING_KEYS = {
    "freecad_url": FREECAD_WORKER_KEY,
    "freecad_analyzer_url": FREECAD_ANALYZER_KEY,
}


def _worker_url(value: str | None, label: str) -> str | None:
    if value is None or not value.strip():
        return None
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail=f"{label} must be an HTTP or HTTPS URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label} contains an invalid port") from exc
    _ = port
    if parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail=f"{label} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise HTTPException(status_code=422, detail=f"{label} must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def _cad_worker_configurations(
    db: Session, settings: Settings | None = None
) -> dict[str, tuple[str | None, CadWorkerConfigurationSource]]:
    rows = {
        row.key: row.value
        for row in db.scalars(
            select(ApplicationSetting).where(
                ApplicationSetting.key.in_(CAD_WORKER_SETTING_KEYS.values())
            )
        )
    }
    defaults = {
        "freecad_url": getattr(settings, "freecad_worker_url", None),
        "freecad_analyzer_url": getattr(settings, "freecad_preprocessor_url", None)
        or getattr(settings, "cad_analyzer_url", None),
    }
    configurations = {}
    for field, key in CAD_WORKER_SETTING_KEYS.items():
        if key in rows:
            value = rows[key] or None
            configurations[field] = (
                value,
                "ADMIN_OVERRIDE" if value is not None else "ADMIN_DISABLED",
            )
        elif defaults[field] is not None:
            configurations[field] = (defaults[field], "ENVIRONMENT_DEFAULT")
        else:
            configurations[field] = (None, "NOT_CONFIGURED")
    return configurations


def _cad_worker_urls(db: Session, settings: Settings | None = None) -> dict[str, str | None]:
    return {
        field: configuration[0]
        for field, configuration in _cad_worker_configurations(db, settings).items()
    }


async def _probe_freecad_worker(
    cad_system: Literal["freecad", "freecad_analyzer"], url: str | None, worker_key: str | None
) -> CadWorkerConnectionStatus:
    checked_at = datetime.now(UTC)
    if not url:
        return CadWorkerConnectionStatus(
            cad_system=cad_system, url=None, availability="NOT_CONFIGURED", checked_at=checked_at
        )
    try:
        if not worker_key:
            raise ValueError("The internal worker key is not configured.")
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
            readiness = await client.get(f"{url.rstrip('/')}/ready")
            readiness.raise_for_status()
            response = await client.get(
                f"{url.rstrip('/')}/api/v1/capabilities",
                headers={"X-Modelryx-Worker-Key": worker_key},
            )
            response.raise_for_status()
            capabilities = response.json()
            if not isinstance(capabilities, dict) or (
                capabilities.get("contractVersion") != "1.0"
                or capabilities.get("cadSystem") != "freecad"
                or (
                    cad_system == "freecad_analyzer"
                    and capabilities.get("dependencyAnalysis") is not True
                )
                or (
                    cad_system == "freecad"
                    and not {"part", "assembly"}.issubset(
                        set(capabilities.get("documentKinds", []))
                    )
                )
                or (
                    cad_system == "freecad"
                    and not {"modelryx-preview", "part-metadata", "native-snapshot"}.issubset(
                        set(capabilities.get("outputRoles", []))
                    )
                )
            ):
                raise ValueError(
                    "The endpoint does not advertise the required standalone FreeCAD capabilities."
                )
        return CadWorkerConnectionStatus(
            cad_system=cad_system,
            url=url,
            availability="ONLINE",
            checked_at=checked_at,
            capabilities=capabilities,
        )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return CadWorkerConnectionStatus(
            cad_system=cad_system,
            url=url,
            availability="OFFLINE",
            checked_at=checked_at,
            detail=f"Standalone FreeCAD check failed: {exc}",
        )


def require_admin(principal: Principal) -> None:
    if not principal.has_role("administrator"):
        raise HTTPException(status_code=403, detail="Administrator role required")


@router.get("/cad-workers", response_model=CadWorkerSettingsResponse)
def get_cad_worker_settings(
    db: Db, principal: Actor, settings: Config
) -> CadWorkerSettingsResponse:
    require_admin(principal)
    return CadWorkerSettingsResponse(**_cad_worker_urls(db, settings))


@router.get("/cad-workers/status", response_model=CadWorkerStatusResponse)
async def get_cad_worker_status(
    db: Db, principal: Actor, settings: Config
) -> CadWorkerStatusResponse:
    require_admin(principal)
    configurations = _cad_worker_configurations(db, settings)
    freecad, freecad_analyzer = await asyncio.gather(
        _probe_freecad_worker(
            "freecad", configurations["freecad_url"][0], settings.internal_worker_key
        ),
        _probe_freecad_worker(
            "freecad_analyzer",
            configurations["freecad_analyzer_url"][0],
            settings.internal_worker_key,
        ),
    )
    return CadWorkerStatusResponse(
        freecad=freecad.model_copy(
            update={"configuration_source": configurations["freecad_url"][1]}
        ),
        freecad_analyzer=freecad_analyzer.model_copy(
            update={"configuration_source": configurations["freecad_analyzer_url"][1]}
        ),
    )


@router.put("/cad-workers", response_model=CadWorkerSettingsResponse)
def update_cad_worker_settings(
    payload: CadWorkerSettingsUpdate, request: Request, db: Db, principal: Actor, settings: Config
) -> CadWorkerSettingsResponse:
    require_admin(principal)
    actor = user_for_principal(db, principal)
    values = {}
    for field, label in (
        ("freecad_url", "FreeCAD worker URL"),
        ("freecad_analyzer_url", "FreeCAD analyzer URL"),
    ):
        if field in payload.model_fields_set:
            values[field] = _worker_url(getattr(payload, field), label)
    now = datetime.now(UTC)
    for field, key in CAD_WORKER_SETTING_KEYS.items():
        if field not in values:
            continue
        row = db.get(ApplicationSetting, key)
        value = values[field]
        if value is None:
            # Keep an explicit empty override so an administrator can disable a
            # worker even when a deployment-level environment URL is present.
            if row is None:
                db.add(ApplicationSetting(key=key, value="", updated_by=actor.id, updated_at=now))
            else:
                row.value = ""
                row.updated_by = actor.id
                row.updated_at = now
            continue
        if row is None:
            db.add(ApplicationSetting(key=key, value=value, updated_by=actor.id, updated_at=now))
        else:
            row.value = value
            row.updated_by = actor.id
            row.updated_at = now
    record_audit(
        db,
        actor_id=actor.id,
        action="cad_worker_settings.updated",
        entity_type="application_settings",
        entity_id=uuid.uuid5(uuid.NAMESPACE_URL, "modelryx:cad-worker-settings"),
        request_id=request.state.request_id,
        details={"configured": {field: value is not None for field, value in values.items()}},
    )
    db.commit()
    return CadWorkerSettingsResponse(**_cad_worker_urls(db, settings))


class ExternalWorkerUpdate(BaseModel):
    url: str = Field(min_length=1, max_length=1500)
    enabled: bool = True


@router.get("/external-workers")
def list_external_workers(db: Db, principal: Actor) -> list[dict]:
    require_admin(principal)
    return registered_workers(db)


@router.get("/external-workers/status")
async def external_worker_status(db: Db, principal: Actor, settings: Config) -> list[dict]:
    """Probe registered workers without exposing credentials or guessing their CAD system."""
    require_admin(principal)
    workers = registered_workers(db)

    async def probe(worker: dict) -> dict:
        result = {
            "id": worker["id"],
            "cadSystem": worker["capabilities"]["cadSystem"],
            "url": worker["url"],
            "availability": "DISABLED" if not worker["enabled"] else "OFFLINE",
            "checkedAt": datetime.now(UTC).isoformat(),
            "detail": None,
        }
        if not worker["enabled"]:
            return result
        if not settings.internal_worker_key:
            result["detail"] = "The internal worker key is not configured."
            return result
        try:
            async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
                readiness = await client.get(f"{worker['url']}/ready")
                readiness.raise_for_status()
                response = await client.get(
                    f"{worker['url']}/api/v1/capabilities",
                    headers={"X-Modelryx-Worker-Key": settings.internal_worker_key},
                )
                response.raise_for_status()
                if validate_capabilities(response.json()) != worker["capabilities"]:
                    result["availability"] = "CHANGED"
                    result["detail"] = "Worker capabilities changed. Re-register the worker."
                else:
                    result["availability"] = "ONLINE"
        except (httpx.HTTPError, ValueError, TypeError):
            result["detail"] = "Worker readiness or capability check failed."
        return result

    return list(await asyncio.gather(*(probe(worker) for worker in workers)))


@router.put("/external-workers/{worker_id}")
async def register_external_worker(
    worker_id: str,
    payload: ExternalWorkerUpdate,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> dict:
    require_admin(principal)
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", worker_id):
        raise HTTPException(422, "Worker ID must be a lowercase identifier")
    url = _worker_url(payload.url, "Worker URL")
    key = PREFIX + worker_id
    row = db.get(ApplicationSetting, key)
    if row is not None and not payload.enabled:
        try:
            previous = json.loads(row.value)
            if previous["url"] != url:
                raise ValueError("Cannot change the URL while disabling a worker")
            capabilities = validate_capabilities(previous["capabilities"])
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, f"Invalid stored worker: {exc}") from exc
    else:
        if not settings.internal_worker_key:
            raise HTTPException(503, "The internal worker key is not configured")
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.get(
                    f"{url}/api/v1/capabilities",
                    headers={"X-Modelryx-Worker-Key": settings.internal_worker_key},
                )
                response.raise_for_status()
                capabilities = validate_capabilities(response.json())
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise HTTPException(422, f"Worker capability check failed: {exc}") from exc
    value = {"url": url, "enabled": payload.enabled, "capabilities": capabilities}
    encoded = json.dumps(value, separators=(",", ":"))
    if len(encoded) > 2000:
        raise HTTPException(422, "Worker configuration is too large")
    actor = user_for_principal(db, principal)
    if row is None:
        db.add(ApplicationSetting(key=key, value=encoded, updated_by=actor.id))
    else:
        row.value = encoded
        row.updated_by = actor.id
        row.updated_at = datetime.now(UTC)
    record_audit(
        db,
        actor_id=actor.id,
        action="external_worker.updated",
        entity_type="application_settings",
        entity_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
        request_id=request.state.request_id,
        details={"worker_id": worker_id, "enabled": payload.enabled},
    )
    db.commit()
    return {"id": worker_id, **value}


@router.delete("/external-workers/{worker_id}", status_code=204)
def delete_external_worker(worker_id: str, request: Request, db: Db, principal: Actor):
    require_admin(principal)
    key = PREFIX + worker_id
    row = db.get(ApplicationSetting, key)
    if row is None:
        raise HTTPException(404, "Worker not found")
    actor = user_for_principal(db, principal)
    db.delete(row)
    record_audit(
        db,
        actor_id=actor.id,
        action="external_worker.deleted",
        entity_type="application_settings",
        entity_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
        request_id=request.state.request_id,
        details={"worker_id": worker_id},
    )
    db.commit()


@router.post(
    "/maintenance/checkout-notifications/run",
    response_model=CheckoutNotificationRunResponse,
)
def run_checkout_notification_generation(
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> CheckoutNotificationRunResponse:
    require_admin(principal)
    user = user_for_principal(db, principal)
    try:
        result = create_checkout_expiry_notifications(
            db,
            warning_seconds=settings.checkout_expiry_warning_seconds,
        )
        record_checkout_notification_run(
            db,
            result=result,
            actor_id=user.id,
            request_id=request.state.request_id,
            trigger="administrator",
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        record_checkout_notification_run(
            db,
            failure_detail=str(exc),
            actor_id=user.id,
            request_id=request.state.request_id,
            trigger="administrator",
        )
        db.commit()
        raise HTTPException(
            status_code=500,
            detail="Checkout notification generation failed",
        ) from exc
    return CheckoutNotificationRunResponse(
        examined=result.examined,
        created=result.created,
        warning_seconds=settings.checkout_expiry_warning_seconds,
    )


@router.post(
    "/maintenance/approval-escalations/run",
    response_model=ApprovalEscalationRunResponse,
)
def run_approval_escalations(
    request: Request, db: Db, principal: Actor
) -> ApprovalEscalationRunResponse:
    require_admin(principal)
    actor = user_for_principal(db, principal)
    result = create_overdue_approval_escalations(
        db, actor_id=actor.id, request_id=request.state.request_id
    )
    db.commit()
    return ApprovalEscalationRunResponse(
        overdue_requests=result.overdue_requests,
        created=result.created,
        expired_emergency_requests=result.expired_emergency_requests,
    )


def user_response(user: User) -> UserResponse:
    roles = sorted(roles_for_user(user))
    return UserResponse(
        id=user.id,
        username=user.external_subject,
        display_name=user.display_name,
        role=effective_role(roles),
        roles=roles,
        is_active=user.is_active,
        failed_login_attempts=user.failed_login_attempts or 0,
        locked_until=user.locked_until,
        last_login_at=user.last_login_at,
        password_changed_at=user.password_changed_at,
        created_at=user.created_at,
    )


@router.get("/users", response_model=list[UserResponse])
def list_users(db: Db, principal: Actor) -> list[UserResponse]:
    require_admin(principal)
    users = db.scalars(select(User).order_by(User.display_name, User.external_subject))
    return [user_response(user) for user in users]


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, request: Request, db: Db, principal: Actor) -> UserResponse:
    require_admin(principal)
    username = payload.username.strip()
    display_name = payload.display_name.strip()
    if not username or not display_name:
        raise HTTPException(status_code=422, detail="Username and display name cannot be blank")
    requested_roles = payload.roles or ([payload.role] if payload.role else None)
    if not requested_roles:
        raise HTTPException(status_code=422, detail="At least one role is required")
    user = User(
        external_subject=username,
        display_name=display_name,
        role="reader",
        password_hash=hash_password(payload.password),
        password_changed_at=datetime.now(UTC),
        is_active=True,
    )
    try:
        assigned_roles = assign_roles(user, requested_roles)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    actor = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=actor.id,
        action="user.created",
        entity_type="user",
        entity_id=user.id,
        request_id=request.state.request_id,
        details={"username": username, "roles": sorted(assigned_roles)},
    )
    db.commit()
    db.refresh(user)
    return user_response(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: uuid.UUID, payload: UserUpdate, request: Request, db: Db, principal: Actor
) -> UserResponse:
    require_admin(principal)
    # Serialize access changes, including requests authenticated before another
    # administrator's transaction committed. Released automatically on rollback/commit.
    db.execute(select(func.pg_advisory_xact_lock(724196203)))
    actor = db.scalar(
        select(User)
        .where(User.external_subject == principal.subject)
        .execution_options(populate_existing=True)
    )
    if (
        actor is None
        or not actor.is_active
        or not user_has_role(actor, "administrator")
        or (actor.auth_version or 0) != principal.auth_version
    ):
        raise HTTPException(status_code=403, detail="Active administrator access is required")
    user = db.get(User, user_id, populate_existing=True)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "display_name" in changes and not changes["display_name"].strip():
        raise HTTPException(status_code=422, detail="Display name cannot be blank")
    requested_roles = changes.pop("roles", None)
    legacy_role = changes.pop("role", None)
    if requested_roles is None and legacy_role is not None:
        requested_roles = [legacy_role]
    resulting_roles = (
        set(requested_roles) if requested_roles is not None else set(roles_for_user(user))
    )
    removing_access = changes.get("is_active") is False or "administrator" not in resulting_roles
    if user.is_active and user_has_role(user, "administrator") and removing_access:
        active_admins = sum(
            1
            for candidate in db.scalars(select(User).where(User.is_active.is_(True)))
            if user_has_role(candidate, "administrator")
        )
        if active_admins <= 1:
            raise HTTPException(
                status_code=409,
                detail="At least one active administrator must remain",
            )
    if user.external_subject == principal.subject and (
        changes.get("is_active") is False or "administrator" not in resulting_roles
    ):
        raise HTTPException(
            status_code=409,
            detail="You cannot remove your own administrator access",
        )
    for field, value in changes.items():
        setattr(user, field, value.strip() if field == "display_name" else value)
    if requested_roles is not None:
        try:
            assign_roles(user, requested_roles)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    actor = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=actor.id,
        action="user.updated",
        entity_type="user",
        entity_id=user.id,
        request_id=request.state.request_id,
        details={
            "fields": sorted([*changes, *(["roles"] if requested_roles is not None else [])]),
            "roles": sorted(roles_for_user(user)),
        },
    )
    db.commit()
    db.refresh(user)
    return user_response(user)


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: uuid.UUID, payload: PasswordReset, request: Request, db: Db, principal: Actor
) -> None:
    require_admin(principal)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(payload.password)
    user.auth_version = (user.auth_version or 0) + 1
    user.failed_login_attempts = 0
    user.locked_until = None
    user.password_changed_at = datetime.now(UTC)
    actor = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=actor.id,
        action="user.password_reset",
        entity_type="user",
        entity_id=user.id,
        request_id=request.state.request_id,
    )
    db.commit()


@router.post("/users/{user_id}/unlock", status_code=status.HTTP_204_NO_CONTENT)
def unlock_user_account(user_id: uuid.UUID, request: Request, db: Db, principal: Actor) -> None:
    require_admin(principal)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.failed_login_attempts = 0
    user.locked_until = None
    actor = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=actor.id,
        action="user.unlocked",
        entity_type="user",
        entity_id=user.id,
        request_id=request.state.request_id,
        details={"username": user.external_subject},
    )
    db.commit()


@router.post("/users/{user_id}/sessions/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_user_sessions(user_id: uuid.UUID, request: Request, db: Db, principal: Actor) -> None:
    require_admin(principal)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.external_subject == principal.subject:
        raise HTTPException(status_code=409, detail="You cannot revoke your current session")
    user.auth_version = (user.auth_version or 0) + 1
    actor = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=actor.id,
        action="user.sessions_revoked",
        entity_type="user",
        entity_id=user.id,
        request_id=request.state.request_id,
        details={"username": user.external_subject},
    )
    db.commit()


@router.get("/users/{user_id}/activity", response_model=list[UserActivityResponse])
def read_user_activity(user_id: uuid.UUID, db: Db, principal: Actor) -> list[UserActivityResponse]:
    require_admin(principal)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    records = db.scalars(
        select(AuditLog)
        .where((AuditLog.actor_id == user_id) | (AuditLog.entity_id == user_id))
        .order_by(AuditLog.occurred_at.desc())
        .limit(25)
    )
    return [
        UserActivityResponse(
            id=record.id,
            action=record.action,
            entity_type=record.entity_type,
            entity_id=record.entity_id,
            occurred_at=record.occurred_at,
            details=record.details_json,
        )
        for record in records
    ]


@router.get("/items/{item_id}/purge", response_model=ItemPurgePreviewResponse)
def preview_item_deletion(item_id: uuid.UUID, db: Db, principal: Actor) -> ItemPurgePreviewResponse:
    require_admin(principal)
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    preview = preview_item_purge(db, item_id)
    cascade = cascade_plan(db, item_id)
    candidate_numbers = {
        item.part_number for item in [*cascade.deletable_items, *cascade.protected_items]
    }
    external_blockers = sorted(set(preview.blocking_item_numbers) - candidate_numbers)
    return ItemPurgePreviewResponse(
        item_id=item.id,
        item_number=item.part_number,
        name=item.name,
        status=item.status,
        revision_count=preview.revision_count,
        artifact_count=preview.artifact_count,
        stored_bytes=preview.stored_bytes,
        attachment_count=preview.attachment_count,
        blocking_reference_count=(preview.blocking_reference_count if external_blockers else 0),
        blocking_item_numbers=external_blockers,
        cascade_item_numbers=[item.part_number for item in cascade.deletable_items],
        protected_legacy_item_numbers=[item.part_number for item in cascade.protected_items],
        external_reference_item_numbers=cascade.external_item_numbers,
    )


@router.get(
    "/items/{item_id}/revisions/deletion-options",
    response_model=list[RevisionDeletionOption],
)
def revision_deletion_options(
    item_id: uuid.UUID, db: Db, principal: Actor
) -> list[RevisionDeletionOption]:
    require_admin(principal)
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    revisions = list(
        db.scalars(
            select(Revision)
            .where(Revision.part_id == item_id)
            .order_by(Revision.created_at.desc(), Revision.revision_code.desc())
        )
    )
    options: list[RevisionDeletionOption] = []
    for revision in revisions:
        eligibility = revision_deletion_eligibility(db, item, revision)
        options.append(
            RevisionDeletionOption(
                id=revision.id,
                revision_code=revision.revision_code,
                state=revision.state,
                lifecycle_state=revision.lifecycle_state,
                created_at=revision.created_at,
                is_current=item.current_revision_id == revision.id,
                eligible=eligibility.eligible,
                reason=eligibility.reason,
            )
        )
    return options


@router.post(
    "/items/{item_id}/revisions/{revision_id}/delete",
    status_code=status.HTTP_204_NO_CONTENT,
)
def permanently_delete_revision(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    payload: RevisionDeletionConfirmation,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> None:
    require_admin(principal)
    item = db.get(Item, item_id)
    revision = db.get(Revision, revision_id)
    if item is None or revision is None or revision.part_id != item_id:
        raise HTTPException(status_code=404, detail="Revision not found")
    if payload.revision_code != revision.revision_code:
        raise HTTPException(status_code=422, detail="Revision confirmation does not match")
    eligibility = revision_deletion_eligibility(db, item, revision)
    if not eligibility.eligible:
        raise HTTPException(status_code=409, detail=eligibility.reason)
    artifact_keys, workspace_keys = revision_storage_keys(db, revision.id)
    actor = user_for_principal(db, principal)
    record_audit(
        db,
        actor_id=actor.id,
        action="revision.purged",
        entity_type="revision",
        entity_id=revision.id,
        request_id=request.state.request_id,
        details={
            "item_id": str(item.id),
            "item_number": item.part_number,
            "revision_code": revision.revision_code,
            "deletion": "permanent",
        },
    )
    purge_revision_records(db, item, revision)
    db.commit()
    remove_purged_files(settings.storage_root, artifact_keys, workspace_keys)


@router.post("/items/{item_id}/purge", response_model=ItemPurgeResponse)
def permanently_delete_item(
    item_id: uuid.UUID,
    payload: ItemPurgeConfirmation,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> ItemPurgeResponse:
    require_admin(principal)
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if payload.item_number != item.part_number:
        raise HTTPException(status_code=422, detail="Item number confirmation does not match")
    preview = preview_item_purge(db, item_id)
    cascade = cascade_plan(db, item_id)
    candidate_numbers = {
        item.part_number for item in [*cascade.deletable_items, *cascade.protected_items]
    }
    external_blockers = sorted(set(preview.blocking_item_numbers) - candidate_numbers)
    if external_blockers:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Item cannot be permanently deleted. External references: "
                f"{', '.join(sorted(set(external_blockers + cascade.external_item_numbers)))}. "
                "Delete the referencing assemblies/items first, or remove their references "
                "before permanent deletion. Archived Items still retain their references."
            ),
        )
    eligible_cascade = {item.part_number: item for item in cascade.deletable_items}
    requested_cascade = sorted(set(payload.cascade_item_numbers))
    invalid_cascade = sorted(set(requested_cascade) - set(eligible_cascade))
    if invalid_cascade:
        raise HTTPException(
            status_code=409,
            detail=(
                "These referenced Items are no longer eligible for deletion: "
                f"{', '.join(invalid_cascade)}. Refresh the deletion preview."
            ),
        )
    selected_cascade = [eligible_cascade[number] for number in requested_cascade]
    purge_items = [*selected_cascade, item]
    artifact_keys: list[str] = []
    workspace_keys: list[str] = []
    for purge_item in purge_items:
        owned_artifacts, owned_workspaces = item_storage_keys(db, purge_item.id)
        artifact_keys.extend(owned_artifacts)
        workspace_keys.extend(owned_workspaces)
    actor = user_for_principal(db, principal)
    item_number = item.part_number
    record_audit(
        db,
        actor_id=actor.id,
        action="item.purged",
        entity_type="item",
        entity_id=item.id,
        request_id=request.state.request_id,
        details={
            "item_number": item_number,
            "revision_count": preview.revision_count,
            "artifact_count": preview.artifact_count,
            "stored_bytes": preview.stored_bytes,
            "cascade_item_numbers": requested_cascade,
            "deletion": "permanent",
        },
    )
    for dependent in selected_cascade:
        purge_item_records(db, dependent)
    purge_item_records(db, item)
    db.commit()
    failures = remove_purged_files(settings.storage_root, artifact_keys, workspace_keys)
    return ItemPurgeResponse(
        item_id=item_id,
        item_number=item_number,
        deleted=True,
        file_cleanup_failures=failures,
    )
