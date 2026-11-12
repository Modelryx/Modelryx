import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from modelryx.core.auth import Principal
from modelryx.core.config import Settings
from modelryx.models import StagingFile, StagingSession, User


def workflow_data(staging: StagingSession) -> dict:
    value = (staging.result_json or {}).get("workflow")
    return value if isinstance(value, dict) else {}


def set_workflow(
    staging: StagingSession,
    *,
    state: str,
    stage: str,
    progress_percent: int,
    commit_id: uuid.UUID,
    error_detail: str | None = None,
) -> None:
    staging.result_json = {
        "workflow": {
            "state": state,
            "stage": stage,
            "progress_percent": progress_percent,
            "commit_id": str(commit_id),
            "error_detail": error_detail,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    }


def enqueue_staging_workflow(db: Session, staging: StagingSession) -> dict:
    if not staging.proposal_json:
        raise HTTPException(status_code=422, detail="Complete the Item proposal before submitting")
    files = list(db.scalars(select(StagingFile).where(StagingFile.staging_id == staging.id)))
    decisions = {
        item.get("staged_file_id"): item
        for item in staging.proposal_json.get("identity_decisions", [])
    }
    missing = [
        item.logical_path
        for item in files
        if item.state != "VERIFIED"
        and decisions.get(str(item.id), {}).get("decision") != "USE_EXISTING"
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"message": "Upload all required files before submitting", "files": missing},
        )
    current = workflow_data(staging)
    if current.get("state") in {"QUEUED", "RUNNING"}:
        return current
    commit_id = uuid.UUID(current["commit_id"]) if current.get("commit_id") else uuid.uuid4()
    staging.status = "PREPARING"
    set_workflow(
        staging,
        state="QUEUED",
        stage="VALIDATION",
        progress_percent=65,
        commit_id=commit_id,
    )
    return workflow_data(staging)


def claim_staging_workflow(db: Session) -> StagingSession | None:
    staging = db.scalar(
        select(StagingSession)
        .where(StagingSession.result_json["workflow"]["state"].astext == "QUEUED")
        .order_by(StagingSession.updated_at, StagingSession.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if staging is None:
        return None
    current = workflow_data(staging)
    set_workflow(
        staging,
        state="RUNNING",
        stage="VALIDATION",
        progress_percent=70,
        commit_id=uuid.UUID(current["commit_id"]),
    )
    db.commit()
    return staging


def recover_staging_workflows(db: Session, stale_seconds: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    rows = list(db.scalars(
        select(StagingSession).where(
            StagingSession.result_json["workflow"]["state"].astext == "RUNNING",
            StagingSession.updated_at < cutoff,
        )
    ))
    for staging in rows:
        current = workflow_data(staging)
        set_workflow(
            staging,
            state="QUEUED",
            stage=current.get("stage", "VALIDATION"),
            progress_percent=int(current.get("progress_percent", 65)),
            commit_id=uuid.UUID(current["commit_id"]),
            error_detail="Recovered after an interrupted worker run",
        )
    return len(rows)


def _request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/staging-workflow",
            "headers": [],
        }
    )
    request.state.request_id = f"staging-worker-{uuid.uuid4()}"
    return request


def _principal(user: User) -> Principal:
    role = user.role or "designer"
    return Principal(
        subject=user.external_subject,
        display_name=user.display_name,
        role=role,
        auth_version=user.auth_version or 0,
        roles=("designer",) if role == "engineer" else (role,),
    )


def process_staging_workflow(db: Session, staging_id: uuid.UUID, settings: Settings) -> None:
    # Import lazily so API modules can enqueue workflows without circular imports.
    from modelryx.api.artifact_commits import StagingCommitRequest, commit_staging_upload
    from modelryx.api.staging import StagingReadyRequest, mark_staging_ready
    from modelryx.api.validation import validate

    staging = db.get(StagingSession, staging_id)
    if staging is None:
        return
    current = workflow_data(staging)
    commit_id = uuid.UUID(current["commit_id"])
    user = db.get(User, staging.created_by)
    if user is None or not user.is_active:
        raise RuntimeError("The staging owner is unavailable")
    principal = _principal(user)
    request = _request()

    report = validate(staging.id, request, db, principal, settings)
    if report.status not in {"READY", "READY_WITH_WARNINGS"}:
        staging = db.get(StagingSession, staging_id)
        assert staging is not None
        set_workflow(
            staging,
            state="REVIEW_REQUIRED",
            stage="VALIDATION",
            progress_percent=75,
            commit_id=commit_id,
            error_detail=f"Validation status is {report.status}",
        )
        db.commit()
        return

    staging = db.get(StagingSession, staging_id)
    assert staging is not None
    set_workflow(
        staging,
        state="RUNNING",
        stage="PREPARING_COMMIT",
        progress_percent=85,
        commit_id=commit_id,
    )
    db.commit()
    ready = mark_staging_ready(
        staging.id,
        StagingReadyRequest(expected_snapshot_version=staging.snapshot_version),
        request,
        db,
        principal,
    )
    commit_staging_upload(
        staging.id,
        StagingCommitRequest(snapshot_version=ready.snapshot_version, commit_id=commit_id),
        request,
        db,
        principal,
        settings,
    )


def fail_staging_workflow(db: Session, staging_id: uuid.UUID, error: Exception) -> None:
    staging = db.get(StagingSession, staging_id)
    if staging is None or staging.status in {"CANCELLED", "COMPLETED", "PROMOTED"}:
        return
    current = workflow_data(staging)
    commit_id = uuid.UUID(current["commit_id"]) if current.get("commit_id") else uuid.uuid4()
    detail = error.detail if isinstance(error, HTTPException) else str(error)
    if not isinstance(detail, str):
        detail = str(detail)
    set_workflow(
        staging,
        state="FAILED",
        stage=current.get("stage", "VALIDATION"),
        progress_percent=int(current.get("progress_percent", 65)),
        commit_id=commit_id,
        error_detail=detail[:4000],
    )
    db.commit()
