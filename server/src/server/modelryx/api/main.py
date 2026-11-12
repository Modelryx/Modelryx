import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from modelryx.api.actions import router as actions_router
from modelryx.api.admin import router as admin_router
from modelryx.api.ai import router as ai_router
from modelryx.api.approval_delegations import router as approval_delegations_router
from modelryx.api.approval_groups import router as approval_groups_router
from modelryx.api.artifact_commits import router as artifact_commits_router
from modelryx.api.artifacts import router as artifacts_router
from modelryx.api.assemblies import router as assemblies_router
from modelryx.api.assistant import router as assistant_router
from modelryx.api.attachments import router as attachments_router
from modelryx.api.auth import router as auth_router
from modelryx.api.bom_analysis import router as bom_analysis_router
from modelryx.api.bundles import router as bundles_router
from modelryx.api.cad import router as cad_router
from modelryx.api.checkouts import router as checkouts_router
from modelryx.api.duplicate_checks import router as duplicate_checks_router
from modelryx.api.engineering_search import router as engineering_search_router
from modelryx.api.item_number_templates import router as item_number_templates_router
from modelryx.api.items import router as items_router
from modelryx.api.knowledge import router as knowledge_router
from modelryx.api.metadata import router as metadata_router
from modelryx.api.operations import router as operations_router
from modelryx.api.parts import router as parts_router
from modelryx.api.personal import router as personal_router
from modelryx.api.previews import router as previews_router
from modelryx.api.relationships import router as relationships_router
from modelryx.api.revision_approvals import router as revision_approvals_router
from modelryx.api.revision_comparisons import router as revision_comparisons_router
from modelryx.api.search import router as search_router
from modelryx.api.staging import router as staging_router
from modelryx.api.staging_workflows import router as staging_workflows_router
from modelryx.api.ui_views import router as ui_views_router
from modelryx.api.validation import router as validation_router
from modelryx.api.worker_health import router as worker_health_router
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import get_settings
from modelryx.core.db import get_db
from modelryx.engineering_search.catalog_loader import active_scenario_catalog

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("modelryx")


@asynccontextmanager
async def lifespan(_: FastAPI):
    active_scenario_catalog()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(actions_router)
app.include_router(worker_health_router)
app.include_router(bom_analysis_router)
app.include_router(admin_router)
app.include_router(approval_delegations_router)
app.include_router(approval_groups_router)
app.include_router(ai_router)
app.include_router(assistant_router)
app.include_router(artifacts_router)
app.include_router(artifact_commits_router)
app.include_router(items_router)
app.include_router(item_number_templates_router)
app.include_router(knowledge_router)
app.include_router(metadata_router)
app.include_router(attachments_router)
app.include_router(parts_router)
app.include_router(personal_router)
app.include_router(assemblies_router)
app.include_router(checkouts_router)
app.include_router(duplicate_checks_router)
app.include_router(engineering_search_router)
app.include_router(previews_router)
app.include_router(revision_approvals_router)
app.include_router(relationships_router)
app.include_router(revision_comparisons_router)
app.include_router(bundles_router)
app.include_router(cad_router)
app.include_router(search_router)
app.include_router(staging_router)
app.include_router(staging_workflows_router)
app.include_router(validation_router)
app.include_router(operations_router)
app.include_router(ui_views_router)

# Note: Web frontend runs separately in development (Vite dev server)
# For production with integrated frontend, mount static files here


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request_failed request_id=%s path=%s", request_id, request.url.path)
        raise
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_complete request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        (time.monotonic() - started) * 1000,
    )
    return response


class HealthResponse(BaseModel):
    status: str


class DetailedHealthResponse(BaseModel):
    status: str
    database: str
    timestamp: str


class SessionResponse(BaseModel):
    subject: str
    display_name: str
    role: str
    roles: list[str]


@app.get("/health/live", response_model=HealthResponse, tags=["health"])
def live() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/health/ready", response_model=HealthResponse, tags=["health"])
def ready(db: Annotated[Session, Depends(get_db)]) -> HealthResponse | JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return HealthResponse(status="ok")


@app.get("/health", response_model=DetailedHealthResponse, tags=["health"])
def health(db: Annotated[Session, Depends(get_db)]) -> DetailedHealthResponse | JSONResponse:
    """
    Aggregated health check endpoint.
    Returns detailed status of all system components.
    """
    import datetime
    
    database_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        logger.warning("health_check database_failed: %s", str(e))
        database_status = "error"
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "database": database_status,
                "timestamp": datetime.datetime.utcnow().isoformat(),
            },
        )
    
    return DetailedHealthResponse(
        status="ok",
        database=database_status,
        timestamp=datetime.datetime.utcnow().isoformat(),
    )


@app.get("/api/v1/session", response_model=SessionResponse, tags=["session"])
def session(principal: Annotated[Principal, Depends(current_principal)]) -> SessionResponse:
    return SessionResponse(
        subject=principal.subject,
        display_name=principal.display_name,
        role=principal.role,
        roles=list(principal.roles) if principal.roles else [
            "designer" if principal.role == "engineer" else principal.role
        ],
    )
