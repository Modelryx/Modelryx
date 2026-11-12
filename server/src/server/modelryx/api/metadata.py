"""Developer-facing inspection of normalized and raw revision metadata."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import ArtifactFile, MetadataExtraction, Revision
from modelryx.models.schemas import MetadataExtractionResponse, RevisionMetadataResponse
from modelryx.services.artifact_content import (
    current_artifact_files,
    latest_content_snapshot_id,
)
from modelryx.services.storage import LocalStorageProvider

router = APIRouter(prefix="/api/v1", tags=["metadata"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


def extraction_response(extraction: MetadataExtraction) -> MetadataExtractionResponse:
    raw_url = (
        f"/api/v1/revisions/{extraction.revision_id}/metadata/extractions/{extraction.id}/raw"
        if extraction.raw_artifact_id
        else None
    )
    return MetadataExtractionResponse(
        id=extraction.id,
        revision_id=extraction.revision_id,
        snapshot_id=extraction.snapshot_id,
        schema_version=extraction.schema_version,
        worker_type=extraction.worker_type,
        worker_version=extraction.worker_version,
        source_system=extraction.source_system,
        source_system_version=extraction.source_system_version,
        status=extraction.status,
        extracted_at=extraction.extracted_at,
        metadata=extraction.metadata_json,
        derived=extraction.derived_json,
        warnings=extraction.warnings_json,
        errors=extraction.errors_json,
        raw_metadata_url=raw_url,
        created_at=extraction.created_at,
    )


def revision_or_404(db: Session, revision_id: uuid.UUID) -> Revision:
    revision = db.get(Revision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    return revision


@router.get(
    "/revisions/{revision_id}/metadata",
    response_model=RevisionMetadataResponse,
)
def current_revision_metadata(revision_id: uuid.UUID, db: Db, _: Actor) -> RevisionMetadataResponse:
    revision = revision_or_404(db, revision_id)
    extraction = (
        db.get(MetadataExtraction, revision.current_metadata_extraction_id)
        if revision.current_metadata_extraction_id
        else None
    )
    if extraction is None:
        raise HTTPException(status_code=409, detail="Revision metadata is unavailable")
    snapshot_id = latest_content_snapshot_id(db, revision.id)
    if snapshot_id is not None and extraction.raw_artifact_id not in {
        artifact_file.id for artifact_file in current_artifact_files(db, revision.id)
    }:
        raise HTTPException(
            status_code=409,
            detail="Current metadata does not belong to the latest Artifact snapshot",
        )
    return RevisionMetadataResponse(
        revision_id=revision.id,
        metadata_status=revision.metadata_status,
        current=extraction_response(extraction),
    )


@router.get(
    "/revisions/{revision_id}/metadata/extractions",
    response_model=list[MetadataExtractionResponse],
)
def revision_metadata_history(
    revision_id: uuid.UUID, db: Db, _: Actor
) -> list[MetadataExtractionResponse]:
    revision_or_404(db, revision_id)
    extractions = db.scalars(
        select(MetadataExtraction)
        .where(MetadataExtraction.revision_id == revision_id)
        .order_by(MetadataExtraction.created_at.desc(), MetadataExtraction.id.desc())
    )
    return [extraction_response(extraction) for extraction in extractions]


@router.get(
    "/revisions/{revision_id}/metadata/extractions/{extraction_id}/raw",
    response_class=FileResponse,
)
def raw_extraction_metadata(
    revision_id: uuid.UUID,
    extraction_id: uuid.UUID,
    db: Db,
    _: Actor,
    settings: Config,
) -> FileResponse:
    revision_or_404(db, revision_id)
    extraction = db.get(MetadataExtraction, extraction_id)
    if extraction is None or extraction.revision_id != revision_id:
        raise HTTPException(status_code=404, detail="Metadata extraction not found")
    artifact = (
        db.get(ArtifactFile, extraction.raw_artifact_id) if extraction.raw_artifact_id else None
    )
    if artifact is None:
        raise HTTPException(status_code=409, detail="Raw metadata is unavailable")
    path = LocalStorageProvider(settings.storage_root).path_for(artifact.storage_key)
    if not path.is_file():
        raise HTTPException(status_code=409, detail="Raw metadata artifact is unavailable")
    return FileResponse(path, media_type="application/json", filename="cad-raw-metadata.json")
