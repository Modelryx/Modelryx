import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.api.assemblies import can_read_revision
from modelryx.api.items import readable_item_revision
from modelryx.api.parts import require_engineer
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.db import get_db
from modelryx.models import (
    BomLine,
    BomViewRevision,
    Item,
    Revision,
    RevisionRelationship,
)
from modelryx.models.schemas import (
    RevisionRelationshipCreate,
    RevisionRelationshipResponse,
    RevisionRelationshipView,
)

router = APIRouter(prefix="/api/v1", tags=["relationships"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]


def response(row: RevisionRelationship) -> RevisionRelationshipResponse:
    return RevisionRelationshipResponse.model_validate(row)


@router.get(
    "/items/{item_id}/revisions/{revision_id}/relationships",
    response_model=list[RevisionRelationshipView],
)
def list_relationships(item_id: uuid.UUID, revision_id: uuid.UUID, db: Db, principal: Actor):
    readable_item_revision(db, item_id, revision_id, principal)
    results: list[RevisionRelationshipView] = []

    def visible(item: Item | None, related: Revision | None) -> bool:
        if item is None or item.status != "ACTIVE":
            return False
        if related is not None:
            return can_read_revision(db, related, principal)
        return (
            principal.role == "administrator"
            or item.created_by == user_for_principal(db, principal).id
        )

    rows = list(
        db.scalars(
            select(RevisionRelationship)
            .where(
                or_(
                    RevisionRelationship.source_revision_id == revision_id,
                    RevisionRelationship.target_revision_id == revision_id,
                    and_(
                        RevisionRelationship.target_revision_id.is_(None),
                        RevisionRelationship.target_item_id == item_id,
                    ),
                )
            )
            .order_by(RevisionRelationship.created_at.desc())
        )
    )
    for row in rows:
        outgoing = row.source_revision_id == revision_id
        related_id = row.target_revision_id if outgoing else row.source_revision_id
        related = db.get(Revision, related_id) if related_id else None
        related_item_id = related.part_id if related else row.target_item_id
        item = db.get(Item, related_item_id) if related_item_id else None
        readable_revision = related or (
            db.get(Revision, item.current_revision_id)
            if item and item.current_revision_id
            else None
        )
        if not visible(item, readable_revision):
            continue
        results.append(
            RevisionRelationshipView(
                **response(row).model_dump(),
                direction="reference" if outgoing else "referenced_by",
                item_id=item.id,
                item_number=item.part_number,
                item_name=item.name,
                revision_code=related.revision_code if related else None,
            )
        )

    # Assembly relationships come from the latest immutable BOM snapshot.
    bom_rows = db.execute(
        select(BomLine, BomViewRevision)
        .join(BomViewRevision, BomViewRevision.id == BomLine.bom_view_revision_id)
        .where(
            or_(
                BomLine.parent_revision_id == revision_id,
                BomLine.child_revision_id == revision_id,
            )
        )
    ).all()
    derived_results: dict[
        tuple[uuid.UUID, uuid.UUID, str], RevisionRelationshipView
    ] = {}
    for line, bom_view in bom_rows:
        latest_sequence = db.scalar(
            select(BomViewRevision.sequence)
            .where(BomViewRevision.revision_id == bom_view.revision_id)
            .order_by(BomViewRevision.sequence.desc())
            .limit(1)
        )
        if bom_view.sequence != latest_sequence:
            continue
        parent_revision = db.get(Revision, line.parent_revision_id)
        child_revision = db.get(Revision, line.child_revision_id)
        if parent_revision is None or child_revision is None:
            continue
        parent_item = db.get(Item, parent_revision.part_id)
        child_item = db.get(Item, child_revision.part_id)
        if parent_item is None or parent_item.current_revision_id != parent_revision.id:
            continue
        outgoing = parent_revision.id == revision_id
        related = child_revision if outgoing else parent_revision
        item = child_item if outgoing else parent_item
        if not visible(item, related):
            continue
        direction = "reference" if outgoing else "referenced_by"
        relationship_key = (parent_revision.id, child_revision.id, direction)
        existing_result = derived_results.get(relationship_key)
        if existing_result is not None:
            existing_result.occurrence_count += line.quantity
            continue
        derived_results[relationship_key] = RevisionRelationshipView(
                id=line.id,
                source_revision_id=parent_revision.id,
                target_revision_id=child_revision.id,
                target_item_id=child_item.id,
                relationship_type="USES",
                note=None,
                created_at=bom_view.created_at,
                direction=direction,
                item_id=item.id,
                item_number=item.part_number,
                item_name=item.name,
                revision_code=related.revision_code,
                is_derived=True,
                occurrence_count=line.quantity,
            )
    results.extend(derived_results.values())
    return sorted(results, key=lambda row: (row.direction, row.item_number, str(row.id)))


@router.post(
    "/items/{item_id}/revisions/{revision_id}/relationships",
    response_model=RevisionRelationshipResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_relationship(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    payload: RevisionRelationshipCreate,
    request: Request,
    db: Db,
    principal: Actor,
) -> RevisionRelationshipResponse:
    require_engineer(principal)
    readable_item_revision(db, item_id, revision_id, principal)
    if (
        payload.target_revision_id is not None
        and db.get(Revision, payload.target_revision_id) is None
    ):
        raise HTTPException(status_code=422, detail="Target revision not found")
    if payload.target_item_id is not None and db.get(Item, payload.target_item_id) is None:
        raise HTTPException(status_code=422, detail="Target item not found")
    if payload.target_revision_id == revision_id:
        raise HTTPException(status_code=422, detail="A revision cannot relate to itself")
    user = user_for_principal(db, principal)
    row = RevisionRelationship(
        source_revision_id=revision_id,
        target_revision_id=payload.target_revision_id,
        target_item_id=payload.target_item_id,
        relationship_type=payload.relationship_type,
        note=payload.note,
        created_by=user.id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Relationship already exists") from exc
    record_audit(
        db,
        actor_id=user.id,
        action="revision.relationship_created",
        entity_type="revision_relationship",
        entity_id=row.id,
        request_id=request.state.request_id,
    )
    db.commit()
    db.refresh(row)
    return response(row)


@router.delete(
    "/items/{item_id}/revisions/{revision_id}/relationships/{relationship_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_relationship(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    relationship_id: uuid.UUID,
    request: Request,
    db: Db,
    principal: Actor,
) -> None:
    require_engineer(principal)
    readable_item_revision(db, item_id, revision_id, principal)
    row = db.get(RevisionRelationship, relationship_id)
    if row is None or row.source_revision_id != revision_id:
        raise HTTPException(status_code=404, detail="Relationship not found")
    user = user_for_principal(db, principal)
    db.delete(row)
    record_audit(
        db,
        actor_id=user.id,
        action="revision.relationship_deleted",
        entity_type="revision_relationship",
        entity_id=relationship_id,
        request_id=request.state.request_id,
    )
    db.commit()
