import shutil
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import (
    AiSearchFeedback,
    AiSearchSession,
    Artifact,
    ArtifactFile,
    AuditLog,
    Checkout,
    DownloadBundle,
    ProcessingJob,
    Revision,
    RevisionApprovalRequest,
    StagingSession,
    WorkerHeartbeat,
)
from modelryx.services.checkout_notifications import checkout_notification_health

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


class OperationalStatus(BaseModel):
    status: str
    processing_jobs: dict[str, int]
    revisions: dict[str, int]
    active_staging_sessions: int
    verified_artifact_bytes: int
    artifacts: dict[str, int]
    artifact_files: dict[str, int]
    storage_free_bytes: int
    bundle_jobs: dict[str, int]
    queue_depth: int
    checkout_notification_health: str
    checkout_notification_consecutive_failures: int
    checkout_notification_last_attempt_at: datetime | None
    checkout_notification_last_success_at: datetime | None
    checkout_notification_last_examined: int
    checkout_notification_last_created: int
    pending_approval_requests: int
    overdue_approval_requests: int
    oldest_pending_approval_age_seconds: int
    workers: list["WorkerStatus"]


class WorkerStatus(BaseModel):
    worker_type: str
    availability: str
    machine_name: str | None = None
    worker_version: str | None = None
    application_version: str | None = None
    adapter_version: str | None = None
    compatibility_state: str = "UNKNOWN"
    capabilities: dict = Field(default_factory=dict)
    status: str | None = None
    current_job: bool = False
    detail: str | None = None
    last_heartbeat_at: datetime | None = None
    configured_url: str | None = None


def worker_statuses(db: Session, settings: Settings, now: datetime) -> list[WorkerStatus]:
    cutoff = now - timedelta(seconds=settings.worker_heartbeat_timeout_seconds)
    latest: dict[str, WorkerHeartbeat] = {}
    for heartbeat in db.scalars(
        select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_heartbeat_at.desc())
    ):
        if heartbeat.worker_type.upper() != "FREECAD":
            latest.setdefault(heartbeat.worker_type, heartbeat)
    configured_urls = {
        "STEP": settings.step_worker_url,
    }
    worker_types = sorted(set(latest) | {key for key, value in configured_urls.items() if value})
    return [
        WorkerStatus(
            worker_type=worker_type,
            availability=(
                "ONLINE"
                if (heartbeat := latest.get(worker_type)) is not None
                and heartbeat.last_heartbeat_at >= cutoff
                else "OFFLINE"
            ),
            machine_name=heartbeat.machine_name if heartbeat else None,
            worker_version=heartbeat.worker_version if heartbeat else None,
            application_version=heartbeat.application_version if heartbeat else None,
            adapter_version=heartbeat.adapter_version if heartbeat else None,
            compatibility_state=heartbeat.compatibility_state if heartbeat else "UNKNOWN",
            capabilities=heartbeat.capabilities_json if heartbeat else {},
            status=heartbeat.status if heartbeat else None,
            current_job=heartbeat.current_job_id is not None if heartbeat else False,
            detail=heartbeat.detail if heartbeat else None,
            last_heartbeat_at=heartbeat.last_heartbeat_at if heartbeat else None,
            configured_url=configured_urls.get(worker_type) or None,
        )
        for worker_type in worker_types
    ]


def state_counts(
    db: Session,
    model: type[Artifact]
    | type[ArtifactFile]
    | type[ProcessingJob]
    | type[Revision]
    | type[DownloadBundle],
) -> dict[str, int]:
    return {
        state: count
        for state, count in db.execute(select(model.state, func.count()).group_by(model.state))
    }


@router.get("/status", response_model=OperationalStatus)
def operational_status(db: Db, principal: Actor, settings: Config) -> OperationalStatus:
    if principal.role != "administrator":
        raise HTTPException(status_code=403, detail="Administrator role required")
    jobs = state_counts(db, ProcessingJob)
    revisions = state_counts(db, Revision)
    active_staging_sessions = (
        db.scalar(
            select(func.count())
            .select_from(StagingSession)
            .where(StagingSession.status.in_(["PREPARING", "READY", "COMMITTING"]))
        )
        or 0
    )
    artifact_bytes = (
        db.scalar(
            select(func.coalesce(func.sum(ArtifactFile.byte_size), 0)).where(
                ArtifactFile.state == "VERIFIED"
            )
        )
        or 0
    )
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(settings.storage_root).free
    notification_health = checkout_notification_health(db)
    now = datetime.now(UTC)
    pending_approvals = db.scalar(
        select(func.count()).select_from(RevisionApprovalRequest).where(
            RevisionApprovalRequest.status == "PENDING"
        )
    ) or 0
    overdue_approvals = db.scalar(
        select(func.count()).select_from(RevisionApprovalRequest).where(
            RevisionApprovalRequest.status == "PENDING",
            RevisionApprovalRequest.due_at <= now,
        )
    ) or 0
    oldest_requested_at = db.scalar(
        select(func.min(RevisionApprovalRequest.requested_at)).where(
            RevisionApprovalRequest.status == "PENDING"
        )
    )
    oldest_age = (
        max(0, int((now - oldest_requested_at).total_seconds()))
        if oldest_requested_at
        else 0
    )
    unhealthy = (
        jobs.get("FAILED", 0) > 0
        or revisions.get("FAILED", 0) > 0
        or notification_health.health != "HEALTHY"
        or overdue_approvals > 0
    )
    return OperationalStatus(
        status="attention" if unhealthy else "ok",
        processing_jobs=jobs,
        revisions=revisions,
        active_staging_sessions=active_staging_sessions,
        verified_artifact_bytes=artifact_bytes,
        artifacts=state_counts(db, Artifact),
        artifact_files=state_counts(db, ArtifactFile),
        storage_free_bytes=free_bytes,
        bundle_jobs=state_counts(db, DownloadBundle),
        queue_depth=jobs.get("PENDING", 0),
        checkout_notification_health=notification_health.health,
        checkout_notification_consecutive_failures=notification_health.consecutive_failures,
        checkout_notification_last_attempt_at=notification_health.last_attempt_at,
        checkout_notification_last_success_at=notification_health.last_success_at,
        checkout_notification_last_examined=notification_health.last_examined,
        checkout_notification_last_created=notification_health.last_created,
        pending_approval_requests=pending_approvals,
        overdue_approval_requests=overdue_approvals,
        oldest_pending_approval_age_seconds=oldest_age,
        workers=worker_statuses(db, settings, now),
    )


@router.get("/metrics", response_class=PlainTextResponse)
def operational_metrics(db: Db, principal: Actor, settings: Config) -> str:
    if principal.role != "administrator":
        raise HTTPException(status_code=403, detail="Administrator role required")
    jobs = state_counts(db, ProcessingJob)
    bundles = state_counts(db, DownloadBundle)
    artifacts = state_counts(db, Artifact)
    artifact_files = state_counts(db, ArtifactFile)
    lines = [
        "# HELP modelryx_processing_jobs Processing jobs by state.",
        "# TYPE modelryx_processing_jobs gauge",
    ]
    lines.extend(
        f'modelryx_processing_jobs{{state="{state}"}} {count}'
        for state, count in sorted(jobs.items())
    )
    search_history = [
        entry
        for history in db.scalars(select(AiSearchSession.history_json))
        for entry in (history or [])
    ]
    parser_counts = Counter(str(entry.get("parser", "unknown")) for entry in search_history)
    warning_total = sum(int(entry.get("warning_count", 0)) for entry in search_history)
    zero_total = sum(int(entry.get("result_count", 0)) == 0 for entry in search_history)
    lines.extend(
        [
            "# HELP modelryx_ai_search_interpretations AI searches by bounded parser mode.",
            "# TYPE modelryx_ai_search_interpretations gauge",
            *(
                f'modelryx_ai_search_interpretations{{parser="{parser}"}} {count}'
                for parser, count in sorted(parser_counts.items())
                if parser in {"llm", "deterministic", "direct"}
            ),
            "# HELP modelryx_ai_search_warnings Total warnings returned by AI search.",
            "# TYPE modelryx_ai_search_warnings gauge",
            f"modelryx_ai_search_warnings {warning_total}",
            "# HELP modelryx_ai_search_zero_results AI searches returning zero results.",
            "# TYPE modelryx_ai_search_zero_results gauge",
            f"modelryx_ai_search_zero_results {zero_total}",
        ]
    )
    now = datetime.now(UTC)
    pending_approvals = db.scalar(
        select(func.count()).select_from(RevisionApprovalRequest).where(
            RevisionApprovalRequest.status == "PENDING"
        )
    ) or 0
    overdue_approvals = db.scalar(
        select(func.count()).select_from(RevisionApprovalRequest).where(
            RevisionApprovalRequest.status == "PENDING",
            RevisionApprovalRequest.due_at <= now,
        )
    ) or 0
    oldest_requested_at = db.scalar(
        select(func.min(RevisionApprovalRequest.requested_at)).where(
            RevisionApprovalRequest.status == "PENDING"
        )
    )
    oldest_age = (
        max(0, int((now - oldest_requested_at).total_seconds()))
        if oldest_requested_at
        else 0
    )
    lines.extend(
        [
            "# HELP modelryx_approval_requests_pending Pending approval requests.",
            "# TYPE modelryx_approval_requests_pending gauge",
            f"modelryx_approval_requests_pending {pending_approvals}",
            "# HELP modelryx_approval_requests_overdue Overdue pending approval requests.",
            "# TYPE modelryx_approval_requests_overdue gauge",
            f"modelryx_approval_requests_overdue {overdue_approvals}",
            "# HELP modelryx_approval_oldest_pending_age_seconds Age of oldest pending request.",
            "# TYPE modelryx_approval_oldest_pending_age_seconds gauge",
            f"modelryx_approval_oldest_pending_age_seconds {oldest_age}",
        ]
    )
    lines.extend(
        [
            "# HELP modelryx_download_bundles Download bundles by state.",
            "# TYPE modelryx_download_bundles gauge",
        ]
    )
    lines.extend(
        f'modelryx_download_bundles{{state="{state}"}} {count}'
        for state, count in sorted(bundles.items())
    )
    lines.extend(
        [
            "# HELP modelryx_artifacts Managed Artifacts by state.",
            "# TYPE modelryx_artifacts gauge",
        ]
    )
    lines.extend(
        f'modelryx_artifacts{{state="{state}"}} {count}'
        for state, count in sorted(artifacts.items())
    )
    lines.extend(
        [
            "# HELP modelryx_artifact_files Managed ArtifactFiles by state.",
            "# TYPE modelryx_artifact_files gauge",
        ]
    )
    lines.extend(
        f'modelryx_artifact_files{{state="{state}"}} {count}'
        for state, count in sorted(artifact_files.items())
    )
    verified_bytes = (
        db.scalar(
            select(func.coalesce(func.sum(ArtifactFile.byte_size), 0)).where(
                ArtifactFile.state == "VERIFIED"
            )
        )
        or 0
    )
    lines.extend(
        [
            "# HELP modelryx_verified_artifact_bytes Stored verified artifact bytes.",
            "# TYPE modelryx_verified_artifact_bytes gauge",
            f"modelryx_verified_artifact_bytes {verified_bytes}",
        ]
    )
    notification_health = checkout_notification_health(db)
    notification_health_value = {
        "HEALTHY": 0,
        "WARNING": 1,
        "DEGRADED": 2,
    }[notification_health.health]
    lines.extend(
        [
            (
                "# HELP modelryx_checkout_notification_health Checkout notification generation "
                "health (0 healthy, 1 warning, 2 degraded)."
            ),
            "# TYPE modelryx_checkout_notification_health gauge",
            f"modelryx_checkout_notification_health {notification_health_value}",
            (
                "# HELP modelryx_checkout_notification_consecutive_failures "
                "Consecutive checkout notification generation failures."
            ),
            "# TYPE modelryx_checkout_notification_consecutive_failures gauge",
            (
                "modelryx_checkout_notification_consecutive_failures "
                f"{notification_health.consecutive_failures}"
            ),
            (
                "# HELP modelryx_checkout_notification_last_examined Checkouts examined "
                "in the last successful notification run."
            ),
            "# TYPE modelryx_checkout_notification_last_examined gauge",
            f"modelryx_checkout_notification_last_examined {notification_health.last_examined}",
            (
                "# HELP modelryx_checkout_notification_last_created Notifications created "
                "in the last successful run."
            ),
            "# TYPE modelryx_checkout_notification_last_created gauge",
            f"modelryx_checkout_notification_last_created {notification_health.last_created}",
        ]
    )
    staging_states = {
        state: count
        for state, count in db.execute(
            select(StagingSession.status, func.count()).group_by(StagingSession.status)
        )
    }
    lines.extend(
        [
            "# HELP modelryx_staging_sessions Artifact staging sessions by status.",
            "# TYPE modelryx_staging_sessions gauge",
        ]
    )
    lines.extend(
        f'modelryx_staging_sessions{{status="{state}"}} {count}'
        for state, count in sorted(staging_states.items())
    )
    checkout_expired = Checkout.expires_at <= datetime.now(UTC)
    checkout_rows = db.execute(
        select(
            Checkout.scope,
            checkout_expired,
            func.count(),
        ).group_by(Checkout.scope, checkout_expired)
    )
    lines.extend(
        [
            "# HELP modelryx_checkout_locks Checkout locks by scope and expiry.",
            "# TYPE modelryx_checkout_locks gauge",
        ]
    )
    lines.extend(
        (f'modelryx_checkout_locks{{scope="{scope}",expired="{str(expired).lower()}"}} {count}')
        for scope, expired, count in checkout_rows
    )
    search_events = [
        event
        for history in db.scalars(select(AiSearchSession.history_json))
        for event in (history or [])
    ]
    parser_counts = Counter(event.get("parser", "unknown") for event in search_events)
    lines.extend(
        [
            "# HELP modelryx_engineering_search_queries Search executions by parser mode.",
            "# TYPE modelryx_engineering_search_queries gauge",
        ]
    )
    lines.extend(
        f'modelryx_engineering_search_queries{{parser="{parser}"}} {count}'
        for parser, count in sorted(parser_counts.items())
    )
    latency_buckets_ms = (50, 100, 250, 500, 1000, 5000)
    lines.extend(
        [
            (
                "# HELP modelryx_engineering_search_duration_milliseconds "
                "Retained search execution duration."
            ),
            "# TYPE modelryx_engineering_search_duration_milliseconds histogram",
        ]
    )
    for parser in sorted(parser_counts) or ["unknown"]:
        durations = [
            float(event["duration_ms"])
            for event in search_events
            if event.get("parser", "unknown") == parser
            and isinstance(event.get("duration_ms"), (int, float))
        ]
        for upper_bound in latency_buckets_ms:
            bucket_count = sum(duration <= upper_bound for duration in durations)
            lines.append(
                "modelryx_engineering_search_duration_milliseconds_bucket"
                f'{{parser="{parser}",le="{upper_bound}"}} {bucket_count}'
            )
        lines.extend(
            [
                (
                    "modelryx_engineering_search_duration_milliseconds_bucket"
                    f'{{parser="{parser}",le="+Inf"}} {len(durations)}'
                ),
                (
                    "modelryx_engineering_search_duration_milliseconds_sum"
                    f'{{parser="{parser}"}} {sum(durations)}'
                ),
                (
                    "modelryx_engineering_search_duration_milliseconds_count"
                    f'{{parser="{parser}"}} {len(durations)}'
                ),
            ]
        )
    zero_results = sum(event.get("result_count") == 0 for event in search_events)
    fallback_results = sum(event.get("parser") == "deterministic" for event in search_events)
    feedback_count = db.scalar(select(func.count()).select_from(AiSearchFeedback)) or 0
    lines.extend(
        [
            (
                "# HELP modelryx_engineering_search_zero_results "
                "Search executions returning zero results."
            ),
            "# TYPE modelryx_engineering_search_zero_results gauge",
            f"modelryx_engineering_search_zero_results {zero_results}",
            "# HELP modelryx_engineering_search_fallbacks Deterministic fallback executions.",
            "# TYPE modelryx_engineering_search_fallbacks gauge",
            f"modelryx_engineering_search_fallbacks {fallback_results}",
            "# HELP modelryx_engineering_search_feedback User-submitted search corrections.",
            "# TYPE modelryx_engineering_search_feedback gauge",
            f"modelryx_engineering_search_feedback {feedback_count}",
        ]
    )
    return "\n".join(lines) + "\n"


@router.get("/audit")
def read_audit_log(
    db: Db,
    principal: Actor,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict]:
    if principal.role != "administrator":
        raise HTTPException(status_code=403, detail="Administrator role required")
    records = db.scalars(select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(limit))
    return [
        {
            "id": str(item.id),
            "action": item.action,
            "entity_type": item.entity_type,
            "entity_id": str(item.entity_id) if item.entity_id else None,
            "request_id": item.request_id,
            "details": item.details_json,
            "occurred_at": item.occurred_at.isoformat(),
        }
        for item in records
    ]
