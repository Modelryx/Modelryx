import base64
import binascii
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.api.items import readable_item_revision
from modelryx.cad.commit import (
    StaleCADCommitError,
    cleanup_pending_commit,
    commit_state_token,
    commit_verified_result,
    next_revision_code,
    verified_execution,
)
from modelryx.cad.inspection import inspect_metadata_snapshot
from modelryx.cad.models import (
    CADCommitConfirm,
    CADCommitPlanResponse,
    CADCommitResponse,
    CADDocumentInspection,
    CADExecutionResponse,
    CADFeatureCreateRequest,
    CADMaterialChangeRequest,
    CADParameterChangeRequest,
    CADPlanConfirm,
    CADPlanResponse,
    CADResultResponse,
    CADSessionCreate,
    CADSessionResponse,
    CADWorkerCapabilities,
    CADWorkerSummary,
)
from modelryx.cad.plans import (
    refresh_plan_state,
    resolve_feature_creation,
    resolve_parameter_change,
    state_token,
)
from modelryx.cad.registry import worker_capabilities, worker_summaries
from modelryx.cad.sessions import (
    ACTIVE_SESSION_STATES,
    copy_verified_source,
    expire_if_needed,
    isolated_workspace_path,
    remove_workspace,
)
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import (
    ArtifactFile,
    CADCommitPlan,
    CADExecution,
    CADOperationPlan,
    CADSession,
    Checkout,
    Item,
    MetadataExtraction,
    ProcessingJob,
    Revision,
    StagingSession,
    WorkerHeartbeat,
)
from modelryx.services.artifact_content import current_artifact_files, latest_content_snapshot_id
from modelryx.services.freecad_settings import FREECAD_ANALYZER_KEY, configured_url
from modelryx.services.storage import LocalStorageProvider

router = APIRouter(prefix="/api/v1", tags=["cad-workers"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


def _worker_cutoff(settings: Settings) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=settings.worker_heartbeat_timeout_seconds)


def _cad_root_path(request: Request) -> tuple[str, str]:
    encoded = request.headers.get("X-Modelryx-Root-Path-B64", "")
    if not encoded:
        raise HTTPException(status_code=422, detail="CAD root path is required")
    try:
        root_path = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid encoded CAD root path") from exc
    return root_path, encoded


def _require_cad_editor(principal: Principal) -> None:
    if principal.role not in {"engineer", "administrator"}:
        raise HTTPException(status_code=403, detail="CAD sessions require engineer permission")


@router.post("/cad/analyze")
async def analyze_cad_upload(
    request: Request,
    principal: Actor,
    settings: Config,
    db: Db,
) -> dict:
    """Run the canonical dependency analyzer when no capable local service exists."""
    _require_cad_editor(principal)
    analyzer_url = configured_url(db, FREECAD_ANALYZER_KEY, settings.cad_analyzer_url)
    if not analyzer_url or not settings.internal_worker_key:
        raise HTTPException(status_code=503, detail="CAD worker analysis is unavailable")
    _root_path, encoded_root_path = _cad_root_path(request)
    content = await request.body()
    if not content or len(content) > settings.max_assembly_upload_bytes:
        raise HTTPException(status_code=413, detail="CAD analysis package is too large")
    try:
        async with httpx.AsyncClient(timeout=settings.converter_timeout_seconds) as client:
            response = await client.post(
                f"{analyzer_url.rstrip('/')}/analyze",
                content=content,
                headers={
                    "Content-Type": "application/zip",
                    "X-Modelryx-Root-Path-B64": encoded_root_path,
                    "X-Modelryx-Worker-Key": settings.internal_worker_key,
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503, detail="CAD analyzer could not be reached"
        ) from exc
    if response.is_error:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        raise HTTPException(response.status_code, detail or "CAD analysis failed")
    return response.json()


def _has_active_edit_staging(db: Session, revision_id: uuid.UUID) -> bool:
    return db.scalar(
        select(StagingSession.id).where(
            StagingSession.base_revision_id == revision_id,
            StagingSession.operation == "EDIT_ITEM_REVISION",
            StagingSession.status.in_({"PREPARING", "READY_TO_COMMIT"}),
        )
    ) is not None


def session_response(value: CADSession) -> CADSessionResponse:
    return CADSessionResponse(
        id=value.id,
        revision_id=value.revision_id,
        source_artifact_id=value.source_artifact_id,
        worker_id=value.worker_id,
        status=value.status,
        source_sha256=value.source_sha256,
        lock_version=value.lock_version,
        created_at=value.created_at,
        expires_at=value.expires_at,
        ready_at=value.ready_at,
        closed_at=value.closed_at,
        error=value.error_json,
    )


def plan_response(value: CADOperationPlan) -> CADPlanResponse:
    return CADPlanResponse(
        id=value.id,
        session_id=value.session_id,
        status=value.status,
        risk=value.risk,
        operation=value.operation,
        target_id=value.target_id,
        parameter=value.parameter,
        expected_value=value.expected_value_json["value"],
        new_value=value.new_value_json["value"],
        unit=value.unit,
        preview=value.preview_json,
        state_token=value.state_token,
        requested_by=value.requested_by,
        confirmed_by=value.confirmed_by,
        created_at=value.created_at,
        expires_at=value.expires_at,
        decided_at=value.decided_at,
    )


def execution_response(value: CADExecution) -> CADExecutionResponse:
    return CADExecutionResponse(
        id=value.id,
        plan_id=value.plan_id,
        processing_job_id=value.processing_job_id,
        status=value.status,
        idempotency_key=value.idempotency_key,
        result=value.result_json,
        error=value.error_json,
        created_at=value.created_at,
        started_at=value.started_at,
        completed_at=value.completed_at,
    )


def commit_plan_response(value: CADCommitPlan) -> CADCommitPlanResponse:
    return CADCommitPlanResponse(
        id=value.id,
        session_id=value.session_id,
        status=value.status,
        operation=value.preview_json.get("operation", "NEW_REVISION"),
        revision_code=value.revision_code,
        description=value.preview_json.get("description"),
        state_token=value.state_token,
        preview=value.preview_json,
        requested_by=value.requested_by,
        confirmed_by=value.confirmed_by,
        committed_revision_id=value.committed_revision_id,
        created_at=value.created_at,
        expires_at=value.expires_at,
        decided_at=value.decided_at,
    )


def _current_native_file(db: Session, revision: Revision) -> ArtifactFile | None:
    files = current_artifact_files(
        db,
        revision.id,
        artifact_types={"CAD_MODEL", "CAD_ASSEMBLY"},
    )
    return next((file for file in files if file.role == "PRIMARY"), files[0] if files else None)


def _revision_inspection(db: Session, revision: Revision) -> CADDocumentInspection:
    extraction = (
        db.get(MetadataExtraction, revision.current_metadata_extraction_id)
        if revision.current_metadata_extraction_id
        else None
    )
    if extraction is None:
        raise HTTPException(status_code=409, detail="CAD inspection is unavailable")
    native = _current_native_file(db, revision)
    return inspect_metadata_snapshot(
        extraction.metadata_json,
        revision_id=str(revision.id),
        source_sha256=native.sha256 if native else None,
    )


def owned_session(
    db: Session, session_id: uuid.UUID, principal: Principal, *, lock: bool = False
) -> CADSession:
    statement = select(CADSession).where(CADSession.id == session_id)
    value = db.scalar(statement.with_for_update() if lock else statement)
    user = user_for_principal(db, principal)
    if value is None or (principal.role != "administrator" and value.owner_id != user.id):
        raise HTTPException(status_code=404, detail="CAD session not found")
    if expire_if_needed(value):
        checkout = db.scalar(
            select(Checkout).where(
                Checkout.revision_id == value.revision_id,
                Checkout.owner_id == value.owner_id,
            )
        )
        if checkout is not None and not _has_active_edit_staging(db, value.revision_id):
            db.delete(checkout)
        db.commit()
    return value


@router.get("/cad/workers", response_model=list[CADWorkerSummary])
def list_cad_workers(db: Db, _: Actor, settings: Config) -> list[CADWorkerSummary]:
    return worker_summaries(
        db.scalars(select(WorkerHeartbeat)), cutoff=_worker_cutoff(settings)
    )


@router.get("/cad/workers/{worker_id}/capabilities", response_model=CADWorkerCapabilities)
def get_cad_worker_capabilities(
    worker_id: str, db: Db, _: Actor, settings: Config
) -> CADWorkerCapabilities:
    try:
        return worker_capabilities(
            worker_id,
            db.scalars(select(WorkerHeartbeat)),
            cutoff=_worker_cutoff(settings),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/item-revisions/{revision_id}/cad-inspection", response_model=CADDocumentInspection)
def get_cad_inspection(revision_id: uuid.UUID, db: Db, principal: Actor) -> CADDocumentInspection:
    candidate = db.get(Revision, revision_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    revision = readable_item_revision(db, candidate.part_id, revision_id, principal)
    if revision.lifecycle_state != "WORKING":
        raise HTTPException(status_code=409, detail="Released revisions cannot be edited")
    return _revision_inspection(db, revision)


@router.post(
    "/item-revisions/{revision_id}/cad-sessions",
    response_model=CADSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_cad_session(
    revision_id: uuid.UUID,
    payload: CADSessionCreate,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> CADSessionResponse:
    _require_cad_editor(principal)
    try:
        capabilities = worker_capabilities(
            payload.worker_id,
            db.scalars(select(WorkerHeartbeat)),
            cutoff=_worker_cutoff(settings),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not capabilities.mutate:
        raise HTTPException(status_code=422, detail="Worker does not support CAD mutation sessions")
    candidate = db.scalar(select(Revision).where(Revision.id == revision_id).with_for_update())
    if candidate is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    revision = readable_item_revision(db, candidate.part_id, revision_id, principal)
    if capabilities.cad_system.casefold() != revision.cad_system.casefold():
        raise HTTPException(status_code=422, detail="Worker does not support this CAD system")
    native = _current_native_file(db, revision)
    if native is None:
        raise HTTPException(status_code=409, detail="Verified native artifact is unavailable")
    active = db.scalar(
        select(CADSession).where(
            CADSession.revision_id == revision.id,
            CADSession.status.in_(ACTIVE_SESSION_STATES),
        )
    )
    if active is not None and not expire_if_needed(active):
        raise HTTPException(status_code=409, detail="Revision already has an active CAD session")
    user = user_for_principal(db, principal)
    if active is not None and active.status == "EXPIRED":
        expired_checkout = db.scalar(
            select(Checkout).where(
                Checkout.revision_id == revision.id,
                Checkout.owner_id == active.owner_id,
            )
        )
        if expired_checkout is not None:
            db.delete(expired_checkout)
            db.flush()
    base_snapshot_id = latest_content_snapshot_id(db, revision.id)
    if base_snapshot_id is None:
        raise HTTPException(status_code=409, detail="Revision has no committed content snapshot")
    checkout = db.scalar(select(Checkout).where(Checkout.revision_id == revision.id))
    now = datetime.now(UTC)
    if checkout is not None and checkout.expires_at <= now:
        db.delete(checkout)
        db.flush()
        checkout = None
    if checkout is not None:
        raise HTTPException(
            status_code=409,
            detail="Revision is already checked out by an edit or another CAD session",
        )
    checkout = Checkout(
        part_id=revision.part_id,
        revision_id=revision.id,
        base_snapshot_id=base_snapshot_id,
        owner_id=user.id,
        checkout_group_id=uuid.uuid4(),
        root_revision_id=revision.id,
        scope="SINGLE",
        expires_at=now + timedelta(seconds=settings.checkout_expiry_seconds),
    )
    db.add(checkout)
    db.flush()
    session_id = uuid.uuid4()
    target = isolated_workspace_path(settings.storage_root, session_id, native.original_filename)
    workspace_key = str(target.parent.relative_to(settings.storage_root.resolve())).replace(
        "\\", "/"
    )
    value = CADSession(
        id=session_id,
        revision_id=revision.id,
        source_artifact_id=native.id,
        owner_id=user.id,
        worker_id=payload.worker_id,
        status="OPENING",
        source_sha256=native.sha256,
        workspace_key=workspace_key,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.cad_session_expiry_seconds),
    )
    db.add(value)
    db.flush()
    try:
        source = LocalStorageProvider(settings.storage_root).path_for(native.storage_key)
        copy_verified_source(source, target, native.sha256, settings.cad_session_max_bytes)
        value.status = "READY"
        value.ready_at = datetime.now(UTC)
    except (FileNotFoundError, OSError, ValueError) as exc:
        value.status = "FAILED"
        value.error_json = {"code": "CAD_SESSION_COPY_FAILED", "message": str(exc)[:1000]}
        value.closed_at = datetime.now(UTC)
        db.delete(checkout)
    value.lock_version += 1
    record_audit(
        db,
        actor_id=user.id,
        action="cad_session.created" if value.status == "READY" else "cad_session.failed",
        entity_type="cad_session",
        entity_id=value.id,
        request_id=request.state.request_id,
        details={
            "revision_id": str(revision.id),
            "worker_id": value.worker_id,
            "status": value.status,
        },
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        remove_workspace(settings.storage_root, value.workspace_key)
        raise
    return session_response(value)


@router.get("/cad-sessions/{session_id}", response_model=CADSessionResponse)
def get_cad_session(session_id: uuid.UUID, db: Db, principal: Actor) -> CADSessionResponse:
    return session_response(owned_session(db, session_id, principal))


@router.get("/item-revisions/{revision_id}/cad-sessions", response_model=list[CADSessionResponse])
def list_cad_sessions(revision_id: uuid.UUID, db: Db, principal: Actor) -> list[CADSessionResponse]:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    readable_item_revision(db, revision.part_id, revision.id, principal)
    user = user_for_principal(db, principal)
    statement = select(CADSession).where(CADSession.revision_id == revision.id)
    if principal.role != "administrator":
        statement = statement.where(CADSession.owner_id == user.id)
    sessions = db.scalars(statement.order_by(CADSession.created_at.desc()))
    return [session_response(value) for value in sessions]


@router.post("/cad-sessions/{session_id}/discard", response_model=CADSessionResponse)
def discard_cad_session(
    session_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> CADSessionResponse:
    value = owned_session(db, session_id, principal, lock=True)
    if value.status not in {"READY", "READY_TO_REVIEW", "FAILED", "EXPIRED"}:
        raise HTTPException(status_code=409, detail=f"CAD session is {value.status.lower()}")
    user = user_for_principal(db, principal)
    value.status = "DISCARDED"
    value.closed_at = datetime.now(UTC)
    value.lock_version += 1
    checkout = db.scalar(
        select(Checkout).where(
            Checkout.revision_id == value.revision_id,
            Checkout.owner_id == value.owner_id,
        )
    )
    if checkout is not None and not _has_active_edit_staging(db, value.revision_id):
        db.delete(checkout)
    record_audit(
        db,
        actor_id=user.id,
        action="cad_session.discarded",
        entity_type="cad_session",
        entity_id=value.id,
        request_id=request.state.request_id,
        details={"revision_id": str(value.revision_id)},
    )
    db.commit()
    try:
        remove_workspace(settings.storage_root, value.workspace_key)
    except (OSError, ValueError) as exc:
        value.error_json = {
            "code": "CAD_WORKSPACE_CLEANUP_PENDING",
            "message": str(exc)[:1000],
            "workspace_key": value.workspace_key,
        }
        db.commit()
    return session_response(value)


def _owned_plan(
    db: Session, plan_id: uuid.UUID, principal: Principal, *, lock: bool = False
) -> tuple[CADOperationPlan, CADSession]:
    statement = select(CADOperationPlan).where(CADOperationPlan.id == plan_id)
    plan = db.scalar(statement.with_for_update() if lock else statement)
    if plan is None:
        raise HTTPException(status_code=404, detail="CAD plan not found")
    return plan, owned_session(db, plan.session_id, principal, lock=lock)


@router.post(
    "/cad-sessions/{session_id}/plans",
    response_model=CADPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_cad_plan(
    session_id: uuid.UUID,
    payload: CADParameterChangeRequest | CADMaterialChangeRequest | CADFeatureCreateRequest,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> CADPlanResponse:
    _require_cad_editor(principal)
    session = owned_session(db, session_id, principal, lock=True)
    if session.status != "READY":
        raise HTTPException(status_code=409, detail=f"CAD session is {session.status.lower()}")
    revision = db.get(Revision, session.revision_id)
    if revision is None:
        raise HTTPException(status_code=409, detail="CAD session revision is unavailable")
    inspection = _revision_inspection(db, revision)
    if isinstance(payload, CADMaterialChangeRequest):
        if (
            inspection.material is not None
            and payload.material.casefold() == inspection.material.casefold()
        ):
            raise HTTPException(status_code=422, detail="Requested material is already current")
        target_id = "document"
        parameter = "material"
        feature_type = "DOCUMENT"
        expected: float | str | None = inspection.material
        new_value: float | str = payload.material
        unit = None
        operation = "material.set"
    elif isinstance(payload, CADParameterChangeRequest):
        try:
            feature_type, expected, unit, operation = resolve_parameter_change(payload, inspection)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        target_id = payload.target_id
        parameter = payload.parameter
        new_value = payload.value
    else:
        feature_id = f"Modelryx_{uuid.uuid4().hex}"
        try:
            target_id, parameter, expected, new_value, feature_type = resolve_feature_creation(
                payload, inspection, feature_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        unit = "mm"
        operation = payload.operation
    try:
        capabilities = worker_capabilities(
            session.worker_id,
            db.scalars(select(WorkerHeartbeat)),
            cutoff=_worker_cutoff(settings),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if operation not in capabilities.mutate:
        raise HTTPException(status_code=409, detail="CAD worker cannot perform this operation")
    user = user_for_principal(db, principal)
    value = CADOperationPlan(
        session_id=session.id,
        operation=operation,
        target_id=target_id,
        parameter=parameter,
        expected_value_json={"value": expected},
        new_value_json={"value": new_value},
        unit=unit,
        preview_json={
            "operation": "NEW_REVISION",
            "summary": (
                f"Set {target_id}.{parameter} from {expected} to {new_value} {unit or ''}"
            ).strip(),
            "feature_type": feature_type,
            "recompute_required": True,
            "source_sha256": session.source_sha256,
        },
        state_token=state_token(session, inspection),
        requested_by=user.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    db.add(value)
    db.flush()
    record_audit(
        db,
        actor_id=user.id,
        action="cad_plan.proposed",
        entity_type="cad_operation_plan",
        entity_id=value.id,
        request_id=request.state.request_id,
        details={"session_id": str(session.id), "operation": operation},
    )
    db.commit()
    return plan_response(value)


@router.get("/cad-plans/{plan_id}", response_model=CADPlanResponse)
def get_cad_plan(plan_id: uuid.UUID, db: Db, principal: Actor) -> CADPlanResponse:
    plan, _ = _owned_plan(db, plan_id, principal)
    return plan_response(plan)


@router.post("/cad-plans/{plan_id}/cancel", response_model=CADPlanResponse)
def cancel_cad_plan(
    plan_id: uuid.UUID, request: Request, db: Db, principal: Actor
) -> CADPlanResponse:
    _require_cad_editor(principal)
    plan, _ = _owned_plan(db, plan_id, principal, lock=True)
    if plan.status != "PENDING":
        raise HTTPException(status_code=409, detail=f"CAD plan is {plan.status.lower()}")
    user = user_for_principal(db, principal)
    plan.status = "CANCELLED"
    plan.decided_at = datetime.now(UTC)
    record_audit(
        db,
        actor_id=user.id,
        action="cad_plan.cancelled",
        entity_type="cad_operation_plan",
        entity_id=plan.id,
        request_id=request.state.request_id,
        details={"session_id": str(plan.session_id)},
    )
    db.commit()
    return plan_response(plan)


@router.post("/cad-plans/{plan_id}/confirm", response_model=CADExecutionResponse)
def confirm_cad_plan(
    plan_id: uuid.UUID,
    payload: CADPlanConfirm,
    request: Request,
    db: Db,
    principal: Actor,
) -> CADExecutionResponse:
    _require_cad_editor(principal)
    existing = db.scalar(
        select(CADExecution).where(CADExecution.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        plan, _ = _owned_plan(db, existing.plan_id, principal)
        if plan.id != plan_id:
            raise HTTPException(status_code=409, detail="Idempotency key is already in use")
        return execution_response(existing)
    plan, session = _owned_plan(db, plan_id, principal, lock=True)
    if plan.status != "PENDING":
        winner = db.scalar(select(CADExecution).where(CADExecution.plan_id == plan.id))
        if winner is not None and winner.idempotency_key == payload.idempotency_key:
            return execution_response(winner)
        raise HTTPException(status_code=409, detail=f"CAD plan is {plan.status.lower()}")
    revision = db.get(Revision, session.revision_id)
    if revision is None:
        raise HTTPException(status_code=409, detail="CAD session revision is unavailable")
    inspection = _revision_inspection(db, revision)
    try:
        refresh_plan_state(plan, session, inspection)
    except ValueError as exc:
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    user = user_for_principal(db, principal)
    now = datetime.now(UTC)
    plan.status = "CONFIRMED"
    plan.confirmed_by = user.id
    plan.decided_at = now
    session.status = "EXECUTING"
    session.lock_version += 1
    execution = CADExecution(
        plan_id=plan.id,
        status="QUEUED",
        idempotency_key=payload.idempotency_key,
    )
    db.add(execution)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        winner = db.scalar(
            select(CADExecution).where(CADExecution.idempotency_key == payload.idempotency_key)
        )
        if winner is not None and winner.plan_id == plan_id:
            return execution_response(winner)
        raise HTTPException(status_code=409, detail="Idempotency key is already in use") from exc
    job = ProcessingJob(
        revision_id=session.revision_id,
        snapshot_id=latest_content_snapshot_id(db, revision.id),
        type="CAD_SESSION_EXECUTE",
        idempotency_key=f"cad:{execution.id}",
    )
    db.add(job)
    db.flush()
    execution.processing_job_id = job.id
    record_audit(
        db,
        actor_id=user.id,
        action="cad_plan.confirmed",
        entity_type="cad_operation_plan",
        entity_id=plan.id,
        request_id=request.state.request_id,
        details={"session_id": str(session.id), "execution_id": str(execution.id)},
    )
    db.commit()
    return execution_response(execution)


@router.get("/cad-executions/{execution_id}", response_model=CADExecutionResponse)
def get_cad_execution(execution_id: uuid.UUID, db: Db, principal: Actor) -> CADExecutionResponse:
    value = db.get(CADExecution, execution_id)
    if value is None:
        raise HTTPException(status_code=404, detail="CAD execution not found")
    _owned_plan(db, value.plan_id, principal)
    return execution_response(value)


@router.get("/cad-sessions/{session_id}/result", response_model=CADResultResponse)
def get_cad_result(session_id: uuid.UUID, db: Db, principal: Actor) -> CADResultResponse:
    session = owned_session(db, session_id, principal)
    try:
        plan, execution = verified_execution(db, session)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CADResultResponse(
        session=session_response(session),
        plan=plan_response(plan),
        execution=execution_response(execution),
    )


@router.post(
    "/cad-sessions/{session_id}/commit-plans",
    response_model=CADCommitPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_cad_commit_plan(
    session_id: uuid.UUID, request: Request, db: Db, principal: Actor
) -> CADCommitPlanResponse:
    _require_cad_editor(principal)
    session = owned_session(db, session_id, principal, lock=True)
    if session.status != "READY_TO_REVIEW":
        raise HTTPException(status_code=409, detail="CAD session is not ready to commit")
    active = db.scalar(
        select(CADCommitPlan).where(
            CADCommitPlan.session_id == session.id,
            CADCommitPlan.status == "PENDING",
            CADCommitPlan.expires_at > datetime.now(UTC),
        )
    )
    if active is not None:
        return commit_plan_response(active)
    try:
        _, execution = verified_execution(db, session)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    revision = db.get(Revision, session.revision_id)
    item = db.get(Item, revision.part_id) if revision else None
    if revision is None or item is None:
        raise HTTPException(status_code=409, detail="CAD source item is unavailable")
    existing_codes = set(
        db.scalars(select(Revision.revision_code).where(Revision.part_id == item.id))
    )
    current_revision = (
        db.get(Revision, item.current_revision_id) if item.current_revision_id else None
    )
    code = next_revision_code(
        current_revision.revision_code if current_revision is not None else revision.revision_code,
        existing_codes,
    )
    user = user_for_principal(db, principal)
    value = CADCommitPlan(
        session_id=session.id,
        revision_code=code,
        state_token=commit_state_token(db, session, execution, revision, item),
        preview_json={
            "source_revision_id": str(revision.id),
            "source_revision_code": revision.revision_code,
            "new_revision_code": code,
            "description": None,
            "output_sha256": execution.result_json.get("output_sha256"),
            "comparison": execution.result_json.get("comparison", {}),
            "validation": execution.result_json.get("validation", {}),
        },
        requested_by=user.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    db.add(value)
    db.flush()
    record_audit(
        db,
        actor_id=user.id,
        action="cad_commit.proposed",
        entity_type="cad_commit_plan",
        entity_id=value.id,
        request_id=request.state.request_id,
        details={"session_id": str(session.id), "revision_code": code},
    )
    db.commit()
    return commit_plan_response(value)


def _owned_commit_plan(
    db: Session, commit_plan_id: uuid.UUID, principal: Principal, *, lock: bool = False
) -> tuple[CADCommitPlan, CADSession]:
    statement = select(CADCommitPlan).where(CADCommitPlan.id == commit_plan_id)
    value = db.scalar(statement.with_for_update() if lock else statement)
    if value is None:
        raise HTTPException(status_code=404, detail="CAD commit plan not found")
    return value, owned_session(db, value.session_id, principal, lock=lock)


@router.get("/cad-commit-plans/{commit_plan_id}", response_model=CADCommitPlanResponse)
def get_cad_commit_plan(
    commit_plan_id: uuid.UUID, db: Db, principal: Actor
) -> CADCommitPlanResponse:
    value, _ = _owned_commit_plan(db, commit_plan_id, principal)
    return commit_plan_response(value)


@router.post("/cad-commit-plans/{commit_plan_id}/cancel", response_model=CADCommitPlanResponse)
def cancel_cad_commit_plan(
    commit_plan_id: uuid.UUID, request: Request, db: Db, principal: Actor
) -> CADCommitPlanResponse:
    _require_cad_editor(principal)
    value, _ = _owned_commit_plan(db, commit_plan_id, principal, lock=True)
    if value.status != "PENDING":
        raise HTTPException(status_code=409, detail=f"CAD commit plan is {value.status.lower()}")
    user = user_for_principal(db, principal)
    value.status = "CANCELLED"
    value.decided_at = datetime.now(UTC)
    record_audit(
        db,
        actor_id=user.id,
        action="cad_commit.cancelled",
        entity_type="cad_commit_plan",
        entity_id=value.id,
        request_id=request.state.request_id,
        details={"session_id": str(value.session_id)},
    )
    db.commit()
    return commit_plan_response(value)


@router.post("/cad-commit-plans/{commit_plan_id}/confirm", response_model=CADCommitResponse)
def confirm_cad_commit_plan(
    commit_plan_id: uuid.UUID,
    payload: CADCommitConfirm,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> CADCommitResponse:
    _require_cad_editor(principal)
    existing = db.scalar(
        select(CADCommitPlan).where(CADCommitPlan.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        _owned_commit_plan(db, existing.id, principal)
        if existing.id != commit_plan_id or existing.committed_revision_id is None:
            raise HTTPException(status_code=409, detail="Idempotency key is already in use")
        return CADCommitResponse(
            commit_plan=commit_plan_response(existing),
            revision_id=existing.committed_revision_id,
            revision_code=existing.revision_code,
        )
    value, session = _owned_commit_plan(db, commit_plan_id, principal, lock=True)
    if (
        value.status == "COMMITTED"
        and value.idempotency_key == payload.idempotency_key
        and value.committed_revision_id is not None
    ):
        return CADCommitResponse(
            commit_plan=commit_plan_response(value),
            revision_id=value.committed_revision_id,
            revision_code=value.revision_code,
        )
    now = datetime.now(UTC)
    expires_at = (
        value.expires_at.replace(tzinfo=UTC)
        if value.expires_at.tzinfo is None
        else value.expires_at
    )
    if value.status != "PENDING" or expires_at <= now:
        if value.status == "PENDING":
            value.status = "EXPIRED"
            value.decided_at = now
            db.commit()
        raise HTTPException(status_code=409, detail=f"CAD commit plan is {value.status.lower()}")
    user = user_for_principal(db, principal)
    requested_code = payload.revision_code.strip()
    requested_description = payload.description.strip() if payload.description else None
    source_revision = db.get(Revision, session.revision_id)
    if source_revision is None:
        raise HTTPException(status_code=409, detail="CAD session revision is unavailable")
    if payload.operation == "NEW_REVISION":
        duplicate_code = db.scalar(
            select(Revision.id).where(
                Revision.part_id == source_revision.part_id,
                Revision.revision_code == requested_code,
            )
        )
        if duplicate_code is not None:
            raise HTTPException(status_code=409, detail=f"Revision {requested_code} already exists")
        value.revision_code = requested_code
    else:
        value.revision_code = source_revision.revision_code
    value.preview_json = {
        **value.preview_json,
        "operation": payload.operation,
        "new_revision_code": requested_code if payload.operation == "NEW_REVISION" else None,
        "description": requested_description,
    }
    value.idempotency_key = payload.idempotency_key
    try:
        revision = commit_verified_result(db, settings, value, actor_id=user.id)
        record_audit(
            db,
            actor_id=user.id,
            action="cad_commit.completed",
            entity_type="revision",
            entity_id=revision.id,
            request_id=request.state.request_id,
            details={
                "session_id": str(value.session_id),
                "source_revision_id": str(value.preview_json["source_revision_id"]),
                "operation": payload.operation,
                "revision_code": revision.revision_code,
                "description": revision.description,
            },
        )
        db.commit()
        db.info.pop("cad_commit_created_keys", None)
    except IntegrityError as exc:
        cleanup_pending_commit(db, settings)
        db.rollback()
        winner = db.scalar(
            select(CADCommitPlan).where(CADCommitPlan.idempotency_key == payload.idempotency_key)
        )
        if (
            winner is not None
            and winner.id == commit_plan_id
            and winner.committed_revision_id is not None
        ):
            return CADCommitResponse(
                commit_plan=commit_plan_response(winner),
                revision_id=winner.committed_revision_id,
                revision_code=winner.revision_code,
            )
        raise HTTPException(status_code=409, detail="CAD commit conflicted concurrently") from exc
    except StaleCADCommitError as exc:
        cleanup_pending_commit(db, settings)
        db.rollback()
        stale, _ = _owned_commit_plan(db, commit_plan_id, principal, lock=True)
        if stale.status == "PENDING":
            stale.status = "STALE"
            stale.decided_at = datetime.now(UTC)
            db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        cleanup_pending_commit(db, settings)
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        cleanup_pending_commit(db, settings)
        db.rollback()
        raise
    return CADCommitResponse(
        commit_plan=commit_plan_response(value),
        revision_id=revision.id,
        revision_code=revision.revision_code,
    )
