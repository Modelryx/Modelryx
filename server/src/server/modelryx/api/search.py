from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from modelryx.api.items import item_response, item_revision_response
from modelryx.core.audit import user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.db import get_db
from modelryx.metadata.array_quality import array_geometry_unverified
from modelryx.models import Item, MetadataExtraction, Part, Revision
from modelryx.models.schemas import (
    ItemSearchItem,
    ItemSearchRequest,
    ItemSearchResponse,
    PartSearchItem,
    PartSearchRequest,
    PartSearchResponse,
)

router = APIRouter(prefix="/api/v1", tags=["search"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]


def search_rows(payload: PartSearchRequest | ItemSearchRequest, db: Session, principal: Principal):
    conditions = [Part.status == "ACTIVE"]
    if not payload.include_incomplete:
        conditions.append(Revision.state.in_(["READY", "READY_WITH_WARNINGS"]))
    elif principal.role != "administrator":
        user = user_for_principal(db, principal)
        conditions.append(
            or_(
                Revision.state.in_(["READY", "READY_WITH_WARNINGS"]),
                Part.created_by == user.id,
            )
        )
    if payload.query and (query := payload.query.strip()):
        part_document = func.to_tsvector(
            "simple",
            func.concat_ws(
                " ",
                Part.part_number,
                Part.name,
                func.coalesce(Part.description, ""),
                func.coalesce(Part.category, ""),
            ),
        )
        revision_document = func.to_tsvector("simple", func.coalesce(Revision.search_text, ""))
        text_query = func.websearch_to_tsquery("simple", query)
        conditions.append(
            or_(
                func.lower(Part.part_number) == query.lower(),
                part_document.op("@@")(text_query),
                revision_document.op("@@")(text_query),
            )
        )
    if payload.category:
        conditions.append(func.lower(Part.category) == payload.category.lower())
    if payload.cad_system:
        conditions.append(func.lower(Revision.cad_system) == payload.cad_system.lower())
    if payload.revision_state:
        conditions.append(Revision.state == payload.revision_state.upper())
    if payload.material:
        material = cast(Revision.metadata_json["properties"]["material"], String)
        derived_material = cast(Revision.derived_metadata_json["properties"]["material"], String)
        conditions.append(
            or_(
                func.lower(material) == f'"{payload.material.lower()}"',
                func.lower(derived_material) == f'"{payload.material.lower()}"',
                func.lower(MetadataExtraction.material) == payload.material.lower(),
            )
        )
    if any(
        value is not None for value in (
            payload.bbox_max_mm_gte, payload.bbox_max_mm_lte,
            payload.volume_mm3_gte, payload.volume_mm3_lte, payload.solid_count,
        )
    ):
        conditions.append(~array_geometry_unverified(MetadataExtraction.metadata_json))
    if payload.bbox_max_mm_gte is not None:
        conditions.append(MetadataExtraction.bbox_max_mm >= payload.bbox_max_mm_gte)
    if payload.bbox_max_mm_lte is not None:
        conditions.append(MetadataExtraction.bbox_max_mm <= payload.bbox_max_mm_lte)
    if payload.volume_mm3_gte is not None:
        conditions.append(MetadataExtraction.volume_mm3 >= payload.volume_mm3_gte)
    if payload.volume_mm3_lte is not None:
        conditions.append(MetadataExtraction.volume_mm3 <= payload.volume_mm3_lte)
    if payload.solid_count is not None:
        conditions.append(MetadataExtraction.solid_count == payload.solid_count)
    if isinstance(payload, ItemSearchRequest) and payload.item_type:
        conditions.append(Item.part_type == payload.item_type)

    base = Part.__table__.outerjoin(Revision, Revision.id == Part.current_revision_id).outerjoin(
        MetadataExtraction,
        MetadataExtraction.id == Revision.current_metadata_extraction_id,
    )
    predicate = and_(*conditions) if conditions else None
    count_statement = select(func.count()).select_from(base)
    statement = select(Part, Revision).select_from(base)
    if predicate is not None:
        count_statement = count_statement.where(predicate)
        statement = statement.where(predicate)
    total = db.scalar(count_statement) or 0
    rows = db.execute(
        statement.order_by(Part.part_number).limit(payload.limit).offset(payload.offset)
    ).all()
    return rows, total


@router.post("/parts/search", response_model=PartSearchResponse)
def search_parts(payload: PartSearchRequest, db: Db, principal: Actor) -> PartSearchResponse:
    rows, total = search_rows(payload, db, principal)
    return PartSearchResponse(
        items=[PartSearchItem(part=part, current_revision=revision) for part, revision in rows],
        total=total,
        limit=payload.limit,
        offset=payload.offset,
    )


@router.post("/items/search", response_model=ItemSearchResponse)
def search_items(payload: ItemSearchRequest, db: Db, principal: Actor) -> ItemSearchResponse:
    rows, total = search_rows(payload, db, principal)
    return ItemSearchResponse(
        items=[
            ItemSearchItem(
                item=item_response(item),
                current_revision=item_revision_response(revision) if revision else None,
            )
            for item, revision in rows
        ],
        total=total,
        limit=payload.limit,
        offset=payload.offset,
    )
