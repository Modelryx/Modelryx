import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.api.parts import get_part_or_404, require_engineer
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import (
    Checkout,
    Part,
    Revision,
    RevisionContentSnapshot,
)
from modelryx.models.schemas import CheckoutResponse, ItemCheckoutResponse

router = APIRouter(prefix="/api/v1", tags=["checkouts"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


def latest_snapshot_id(db: Session, revision_id: uuid.UUID) -> uuid.UUID | None:
    return db.scalar(
        select(RevisionContentSnapshot.id)
        .where(RevisionContentSnapshot.revision_id == revision_id)
        .order_by(RevisionContentSnapshot.sequence.desc())
    )


def active_checkout(
    db: Session, part_id: uuid.UUID, revision_id: uuid.UUID | None
) -> Checkout | None:
    checkout = db.scalar(
        select(Checkout).where(
            Checkout.part_id == part_id,
            Checkout.revision_id == revision_id,
        )
    )
    if checkout is not None and checkout.expires_at <= datetime.now(UTC):
        db.delete(checkout)
        db.flush()
        return None
    return checkout


@router.post(
    "/parts/{part_id}/checkout",
    response_model=CheckoutResponse,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/items/{part_id}/checkout",
    response_model=ItemCheckoutResponse,
    status_code=status.HTTP_201_CREATED,
)
def acquire_checkout(
    part_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> Checkout:
    require_engineer(principal)
    part: Part = get_part_or_404(db, part_id)
    user = user_for_principal(db, principal)
    revision = db.get(Revision, part.current_revision_id) if part.current_revision_id else None
    if revision is not None and revision.lifecycle_state != "WORKING":
        raise HTTPException(
            status_code=409,
            detail="Released revisions are immutable; create a new Item Revision to edit content",
        )
    revision_id = revision.id if revision else None
    base_snapshot_id = latest_snapshot_id(db, revision.id) if revision else None
    existing = active_checkout(db, part_id, revision_id)
    if existing is not None:
        if existing.owner_id == user.id:
            db.commit()
            return existing
        raise HTTPException(status_code=409, detail="Part is checked out by another user")
    checkout = Checkout(
        part_id=part.id,
        revision_id=revision_id,
        base_snapshot_id=base_snapshot_id,
        owner_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.checkout_expiry_seconds),
    )
    db.add(checkout)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Part was checked out concurrently") from exc
    record_audit(
        db,
        actor_id=user.id,
        action="part.checked_out",
        entity_type="part",
        entity_id=part.id,
        request_id=request.state.request_id,
        details={"base_snapshot_id": str(checkout.base_snapshot_id)},
    )
    db.commit()
    db.refresh(checkout)
    return checkout


@router.post(
    "/items/{part_id}/revisions/{revision_id}/checkout",
    response_model=ItemCheckoutResponse,
    status_code=status.HTTP_201_CREATED,
)
def acquire_revision_checkout(
    part_id: uuid.UUID,
    revision_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> Checkout:
    """Reserve the selected revision without changing its lifecycle state."""
    require_engineer(principal)
    part = get_part_or_404(db, part_id)
    revision = db.get(Revision, revision_id)
    if revision is None or revision.part_id != part_id:
        raise HTTPException(status_code=404, detail="Item revision not found")
    if revision.lifecycle_state != "WORKING":
        raise HTTPException(
            status_code=409,
            detail="Released revisions are immutable; create a new Item Revision to edit content",
        )
    if (
        revision.state not in {"COMMITTED", "PROCESSING", "READY", "READY_WITH_WARNINGS"}
    ):
        raise HTTPException(
            status_code=409,
            detail="Only a committed revision with content can be checked out",
        )
    user = user_for_principal(db, principal)
    base_snapshot_id = latest_snapshot_id(db, revision.id)
    if base_snapshot_id is None:
        raise HTTPException(
            status_code=409, detail="The revision has no committed content snapshot"
        )
    existing = active_checkout(db, part_id, revision.id)
    if existing is not None:
        if (
            existing.owner_id == user.id
            and existing.base_snapshot_id == base_snapshot_id
        ):
            db.commit()
            return existing
        raise HTTPException(status_code=409, detail="Revision is checked out by another user")
    checkout = Checkout(
        part_id=part.id,
        revision_id=revision.id,
        base_snapshot_id=base_snapshot_id,
        owner_id=user.id,
        checkout_group_id=uuid.uuid4(),
        root_revision_id=revision.id,
        scope="SINGLE",
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.checkout_expiry_seconds),
    )
    db.add(checkout)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Revision was checked out concurrently"
        ) from exc
    record_audit(
        db,
        actor_id=user.id,
        action="revision.checked_out",
        entity_type="revision",
        entity_id=revision.id,
        request_id=request.state.request_id,
        details={
            "item_id": str(part.id),
            "revision_code": revision.revision_code,
            "base_snapshot_id": str(base_snapshot_id),
        },
    )
    db.commit()
    db.refresh(checkout)
    return checkout


@router.get("/parts/{part_id}/checkout", response_model=CheckoutResponse)
@router.get("/items/{part_id}/checkout", response_model=ItemCheckoutResponse)
def read_checkout(part_id: uuid.UUID, db: Db, _: Actor) -> Checkout:
    part = get_part_or_404(db, part_id)
    checkout = active_checkout(db, part_id, part.current_revision_id)
    if checkout is None:
        db.commit()
        raise HTTPException(status_code=404, detail="Part is not checked out")
    db.commit()
    return checkout


@router.delete("/parts/{part_id}/checkout", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/items/{part_id}/checkout", status_code=status.HTTP_204_NO_CONTENT)
def release_checkout(part_id: uuid.UUID, request: Request, db: Db, principal: Actor) -> Response:
    require_engineer(principal)
    part = get_part_or_404(db, part_id)
    checkout = active_checkout(db, part_id, part.current_revision_id)
    if checkout is None:
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    user = user_for_principal(db, principal)
    if checkout.owner_id != user.id and principal.role != "administrator":
        raise HTTPException(status_code=403, detail="Only the owner can release this checkout")
    db.delete(checkout)
    record_audit(
        db,
        actor_id=user.id,
        action="part.checkout_released",
        entity_type="part",
        entity_id=part_id,
        request_id=request.state.request_id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/items/{part_id}/revisions/{revision_id}/checkout",
    response_model=ItemCheckoutResponse,
)
def read_revision_checkout(
    part_id: uuid.UUID, revision_id: uuid.UUID, db: Db, _: Actor
) -> Checkout:
    revision = db.get(Revision, revision_id)
    if revision is None or revision.part_id != part_id:
        raise HTTPException(status_code=404, detail="Item revision not found")
    checkout = active_checkout(db, part_id, revision_id)
    if checkout is None:
        db.commit()
        raise HTTPException(status_code=404, detail="Revision is not checked out")
    db.commit()
    return checkout


@router.delete(
    "/items/{part_id}/revisions/{revision_id}/checkout",
    status_code=status.HTTP_204_NO_CONTENT,
)
def release_revision_checkout(
    part_id: uuid.UUID,
    revision_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
) -> Response:
    require_engineer(principal)
    revision = db.get(Revision, revision_id)
    if revision is None or revision.part_id != part_id:
        raise HTTPException(status_code=404, detail="Item revision not found")
    checkout = active_checkout(db, part_id, revision_id)
    if checkout is None:
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    user = user_for_principal(db, principal)
    if checkout.owner_id != user.id and principal.role != "administrator":
        raise HTTPException(status_code=403, detail="Only the owner can release this checkout")
    db.delete(checkout)
    record_audit(
        db,
        actor_id=user.id,
        action="revision.checkout_released",
        entity_type="revision",
        entity_id=revision.id,
        request_id=request.state.request_id,
        details={"item_id": str(part_id), "revision_code": revision.revision_code},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
