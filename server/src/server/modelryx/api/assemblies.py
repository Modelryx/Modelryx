import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.api.items import item_response, item_revision_response, revision_checkout_status
from modelryx.core.audit import user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.db import get_db
from modelryx.models import BomLine, BomViewRevision, Part, Revision, RevisionContentSnapshot
from modelryx.models.schemas import (
    BomOccurrenceResponse,
    BomResponse,
    ItemBomOccurrenceResponse,
    ItemBomResponse,
    ItemRevisionResponse,
    ItemWhereUsedEntryResponse,
    ItemWhereUsedResponse,
    NativeBomResponse,
    NativeBomViewResponse,
    PartResponse,
    RevisionResponse,
    WhereUsedItemResponse,
    WhereUsedPathSegmentResponse,
    WhereUsedResponse,
)

router = APIRouter(prefix="/api/v1", tags=["assemblies"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
IDENTITY_TRANSFORM = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]
MAX_WHERE_USED_PATHS = 1000


def can_read_revision(db: Session, revision: Revision, principal: Principal) -> bool:
    if revision.state in {"READY", "READY_WITH_WARNINGS"}:
        return True
    if principal.role == "administrator":
        return True
    part = db.get(Part, revision.part_id)
    return part is not None and part.created_by == user_for_principal(db, principal).id


def latest_bom(db: Session, revision_id: uuid.UUID) -> BomViewRevision | None:
    return db.scalar(
        select(BomViewRevision)
        .where(BomViewRevision.revision_id == revision_id)
        .order_by(BomViewRevision.sequence.desc())
    )


def build_occurrences(
    db: Session,
    revision_id: uuid.UUID,
    principal: Principal,
    remaining_depth: int,
    ancestors: set[uuid.UUID],
    snapshot_id: uuid.UUID | None = None,
) -> list[BomOccurrenceResponse]:
    if revision_id in ancestors:
        raise HTTPException(status_code=409, detail="Stored assembly BOM contains a cycle")
    bom = (
        db.scalar(select(BomViewRevision).where(
            BomViewRevision.revision_id == revision_id,
            BomViewRevision.snapshot_id == snapshot_id,
        )) if snapshot_id is not None else latest_bom(db, revision_id)
    )
    if bom is None:
        return []
    lines = list(
        db.scalars(
            select(BomLine)
            .where(BomLine.bom_view_revision_id == bom.id)
            .order_by(BomLine.sequence, BomLine.occurrence_key, BomLine.id)
        )
    )
    result: list[BomOccurrenceResponse] = []
    for line in lines:
        child_revision = db.get(Revision, line.child_revision_id)
        child_part = db.get(Part, child_revision.part_id) if child_revision else None
        if child_revision is None or child_part is None:
            raise HTTPException(status_code=409, detail="BOM references a missing Item Revision")
        if not can_read_revision(db, child_revision, principal):
            raise HTTPException(status_code=403, detail="A referenced component is not authorized")
        children = []
        if remaining_depth > 1 and child_part.part_type == "ASSEMBLY":
            children = build_occurrences(
                db,
                child_revision.id,
                principal,
                remaining_depth - 1,
                ancestors | {revision_id},
            )
        result.append(
            BomOccurrenceResponse(
                id=line.id,
                occurrence_key=line.occurrence_key,
                source_reference=line.source_reference or line.occurrence_key,
                reference_designator=line.reference_designator or line.occurrence_key,
                quantity=line.quantity,
                transform=line.transform or IDENTITY_TRANSFORM,
                is_suppressed=line.is_suppressed,
                is_visible=line.is_visible,
                properties=line.properties_json or {},
                child_part=PartResponse.model_validate(child_part),
                child_revision=RevisionResponse.model_validate(child_revision),
                children=children,
            )
        )
    return result


def _manufacturing_occurrences(
    occurrences: list[BomOccurrenceResponse], *, parts_only: bool = False, multiplier: int = 1
) -> list[BomOccurrenceResponse]:
    result: list[BomOccurrenceResponse] = []
    for occurrence in occurrences:
        properties = occurrence.properties or {}
        structure = str(properties.get("bom_structure") or "NORMAL").upper()
        if (
            occurrence.is_suppressed
            or properties.get("excluded") is True
            or properties.get("reference") is True
            or structure == "REFERENCE"
        ):
            continue
        quantity = max(1, occurrence.quantity) * multiplier
        children = _manufacturing_occurrences(occurrence.children, parts_only=parts_only)
        if structure == "PHANTOM":
            result.extend(
                _manufacturing_occurrences(
                    occurrence.children, parts_only=parts_only, multiplier=quantity
                )
            )
            continue
        terminal = structure in {"PURCHASED", "INSEPARABLE"}
        if parts_only and occurrence.child_part.part_type == "ASSEMBLY" and not terminal:
            result.extend(
                _manufacturing_occurrences(
                    occurrence.children, parts_only=True, multiplier=quantity
                )
            )
            continue
        result.append(
            occurrence.model_copy(
                update={"quantity": quantity, "children": [] if terminal else children}
            )
        )
    if not parts_only:
        return result
    merged: dict[uuid.UUID, BomOccurrenceResponse] = {}
    for occurrence in result:
        key = occurrence.child_revision.id
        existing = merged.get(key)
        if existing is None:
            merged[key] = occurrence
        else:
            merged[key] = existing.model_copy(
                update={"quantity": existing.quantity + occurrence.quantity}
            )
    return list(merged.values())


@router.get("/revisions/{revision_id}/bom", response_model=BomResponse)
def read_bom(
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
    depth: Annotated[int, Query(ge=1, le=20)] = 20,
    view: Annotated[
        Literal["engineering", "structured", "parts_only"], Query()
    ] = "engineering",
    snapshot_id: uuid.UUID | None = None,
) -> BomResponse:
    revision = db.get(Revision, revision_id)
    if revision is None or not can_read_revision(db, revision, principal):
        raise HTTPException(status_code=404, detail="Revision not found")
    part = db.get(Part, revision.part_id)
    if part is None or part.part_type != "ASSEMBLY":
        raise HTTPException(status_code=409, detail="Revision does not belong to an assembly")
    if snapshot_id is not None:
        snapshot = db.get(RevisionContentSnapshot, snapshot_id)
        if snapshot is None or snapshot.revision_id != revision.id:
            raise HTTPException(status_code=404, detail="Revision version not found")
        current_bom = latest_bom(db, revision.id)
        if current_bom is None or current_bom.snapshot_id != snapshot_id:
            # Older root BOMs are exact, but child revisions do not pin content snapshots.
            depth = 1
    occurrences = build_occurrences(db, revision.id, principal, depth, set(), snapshot_id)
    if view != "engineering":
        occurrences = _manufacturing_occurrences(
            occurrences, parts_only=view == "parts_only"
        )
    return BomResponse(
        assembly_part=PartResponse.model_validate(part),
        assembly_revision=RevisionResponse.model_validate(revision),
        occurrences=occurrences,
    )


@router.get("/revisions/{revision_id}/bom/native", response_model=NativeBomResponse)
def read_native_bom(
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
    view_type: Annotated[Literal["MODEL_DATA", "STRUCTURED", "PARTS_ONLY"] | None, Query()] = None,
    model_state: Annotated[str | None, Query(max_length=255)] = None,
    iassembly_member: Annotated[str | None, Query(max_length=255)] = None,
    authoritative: Annotated[bool, Query()] = True,
) -> NativeBomResponse:
    revision = db.get(Revision, revision_id)
    if revision is None or not can_read_revision(db, revision, principal):
        raise HTTPException(status_code=404, detail="Revision not found")
    metadata = (revision.derived_metadata_json or {}).get("partMetadata") or {}
    native = metadata.get("native", {})
    native_views = native.get("bom_views", [])
    active_model_state = str(native.get("active_model_state") or "") or None
    active_iassembly_member = str(native.get("active_iassembly_member") or "") or None
    requested_view_type = view_type or ("STRUCTURED" if authoritative else None)
    views: list[NativeBomViewResponse] = []
    for value in native_views if isinstance(native_views, list) else []:
        if not isinstance(value, dict):
            continue
        if requested_view_type is not None and value.get("view_type") != requested_view_type:
            continue
        if (
            model_state is not None
            and str(value.get("model_state_member_name") or "").casefold()
            != model_state.casefold()
        ):
            continue
        if (
            iassembly_member is not None
            and str(value.get("iassembly_member_name") or "").casefold()
            != iassembly_member.casefold()
        ):
            continue
        views.append(NativeBomViewResponse.model_validate(value))
    selection_reason = None
    if authoritative and views:
        target_model_state = model_state or active_model_state
        target_iassembly_member = iassembly_member or active_iassembly_member

        def score(candidate: NativeBomViewResponse) -> tuple[int, str]:
            value = 0
            if target_model_state:
                if (
                    (candidate.model_state_member_name or "").casefold()
                    == target_model_state.casefold()
                ):
                    value += 4
            elif not candidate.model_state_member_name:
                value += 1
            if target_iassembly_member:
                if (
                    (candidate.iassembly_member_name or "").casefold()
                    == target_iassembly_member.casefold()
                ):
                    value += 4
            elif not candidate.iassembly_member_name:
                value += 1
            return value, candidate.name.casefold()

        views = [max(views, key=score)]
        selected = views[0]
        contexts = [
            f"model state {selected.model_state_member_name}"
            if selected.model_state_member_name else None,
            f"iAssembly member {selected.iassembly_member_name}"
            if selected.iassembly_member_name else None,
        ]
        selection_reason = "Selected active " + requested_view_type.replace("_", " ").lower()
        context = " and ".join(value for value in contexts if value)
        if context:
            selection_reason += f" for {context}"
    return NativeBomResponse(
        revision_id=revision.id,
        active_model_state=active_model_state,
        active_iassembly_member=active_iassembly_member,
        selection_reason=selection_reason,
        views=views,
    )


def _direct_parent_lines(
    db: Session, child_revision_id: uuid.UUID, principal: Principal
) -> list[tuple[BomLine, BomViewRevision, Revision, Part]]:
    rows = db.execute(
        select(BomLine, BomViewRevision)
        .join(BomViewRevision, BomViewRevision.id == BomLine.bom_view_revision_id)
        .where(BomLine.child_revision_id == child_revision_id)
        .order_by(BomLine.occurrence_key, BomLine.id)
    ).all()
    result: list[tuple[BomLine, BomViewRevision, Revision, Part]] = []
    for line, bom in rows:
        latest = latest_bom(db, bom.revision_id)
        if latest is None or latest.id != bom.id:
            continue
        parent_revision = db.get(Revision, line.parent_revision_id)
        parent_part = db.get(Part, parent_revision.part_id) if parent_revision else None
        if (
            parent_revision is None
            or parent_part is None
            or parent_part.current_revision_id != parent_revision.id
            or not can_read_revision(db, parent_revision, principal)
        ):
            continue
        result.append((line, bom, parent_revision, parent_part))
    return result


@router.get("/revisions/{revision_id}/where-used", response_model=WhereUsedResponse)
def where_used(
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
    depth: Annotated[int, Query(ge=1, le=20)] = 20,
) -> WhereUsedResponse:
    revision = db.get(Revision, revision_id)
    if revision is None or not can_read_revision(db, revision, principal):
        raise HTTPException(status_code=404, detail="Revision not found")
    items: list[WhereUsedItemResponse] = []
    warnings: set[str] = set()

    def visit(
        child_revision: Revision,
        path: list[WhereUsedPathSegmentResponse],
        total_quantity: int,
        remaining_depth: int,
        ancestors: frozenset[uuid.UUID],
    ) -> None:
        if len(items) >= MAX_WHERE_USED_PATHS:
            warnings.add(
                f"Where-used traversal was limited to {MAX_WHERE_USED_PATHS} assembly paths"
            )
            return
        if remaining_depth == 0:
            if _direct_parent_lines(db, child_revision.id, principal):
                warnings.add(f"Where-used traversal stopped at the requested depth of {depth}")
            return
        for line, _, parent_revision, parent_part in _direct_parent_lines(
            db, child_revision.id, principal
        ):
            if len(items) >= MAX_WHERE_USED_PATHS:
                warnings.add(
                    f"Where-used traversal was limited to {MAX_WHERE_USED_PATHS} assembly paths"
                )
                return
            if parent_revision.id in ancestors:
                warnings.add("Stored assembly BOM contains a cycle; cyclic paths were omitted")
                continue
            segment = WhereUsedPathSegmentResponse(
                occurrence_id=line.id,
                occurrence_key=line.occurrence_key,
                quantity=line.quantity,
                parent_item_id=parent_part.id,
                parent_revision_id=parent_revision.id,
                child_item_id=child_revision.part_id,
                child_revision_id=child_revision.id,
            )
            next_path = [segment, *path]
            next_quantity = total_quantity * line.quantity
            items.append(
                WhereUsedItemResponse(
                    occurrence_id=line.id,
                    occurrence_key="/".join(row.occurrence_key for row in next_path),
                    parent_part=PartResponse.model_validate(parent_part),
                    parent_revision=RevisionResponse.model_validate(parent_revision),
                    direct=len(next_path) == 1,
                    depth=len(next_path),
                    total_quantity=next_quantity,
                    path=next_path,
                    paths=[next_path],
                )
            )
            visit(
                parent_revision,
                next_path,
                next_quantity,
                remaining_depth - 1,
                ancestors | {parent_revision.id},
            )

    visit(revision, [], 1, depth, frozenset({revision.id}))
    grouped_items: dict[uuid.UUID, WhereUsedItemResponse] = {}
    for item in items:
        key = item.parent_revision.id
        existing = grouped_items.get(key)
        if existing is None:
            grouped_items[key] = item
            continue
        existing.total_quantity += item.total_quantity
        existing.occurrence_count += item.occurrence_count
        existing.paths.extend(item.paths or [item.path])
        if item.depth < existing.depth:
            existing.direct = item.direct
            existing.depth = item.depth
            existing.occurrence_id = item.occurrence_id
            existing.occurrence_key = item.occurrence_key
            existing.path = item.path
    items = list(grouped_items.values())
    items.sort(
        key=lambda item: (
            item.parent_part.part_number,
            item.parent_revision.revision_code,
            item.depth,
            item.occurrence_key,
        )
    )
    return WhereUsedResponse(revision_id=revision_id, items=items, warnings=sorted(warnings))


def canonical_occurrence(
    db: Session, principal: Principal, value: BomOccurrenceResponse
) -> ItemBomOccurrenceResponse:
    return ItemBomOccurrenceResponse(
        id=value.id,
        occurrence_key=value.occurrence_key,
        source_reference=value.source_reference,
        reference_designator=value.reference_designator,
        quantity=value.quantity,
        transform=value.transform,
        is_suppressed=value.is_suppressed,
        is_visible=value.is_visible,
        properties=value.properties,
        child_item=item_response(value.child_part),
        child_revision=ItemRevisionResponse(
            **value.child_revision.model_dump(exclude={"part_id"}),
            item_id=value.child_revision.part_id,
            checkout_status=revision_checkout_status(db, value.child_revision.id, principal),
        ),
        children=[canonical_occurrence(db, principal, child) for child in value.children],
    )


@router.get("/items/{item_id}/revisions/{revision_id}/bom", response_model=ItemBomResponse)
def read_item_bom(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
    depth: Annotated[int, Query(ge=1, le=20)] = 20,
    snapshot_id: uuid.UUID | None = None,
) -> ItemBomResponse:
    revision = db.get(Revision, revision_id)
    if revision is None or revision.part_id != item_id:
        raise HTTPException(status_code=404, detail="Item revision not found")
    legacy = read_bom(revision_id, db, principal, depth, "engineering", snapshot_id)
    return ItemBomResponse(
        assembly_item=item_response(legacy.assembly_part),
        assembly_revision=item_revision_response(
            revision, revision_checkout_status(db, revision.id, principal)
        ),
        occurrences=[canonical_occurrence(db, principal, row) for row in legacy.occurrences],
    )


@router.get(
    "/items/{item_id}/revisions/{revision_id}/where-used",
    response_model=ItemWhereUsedResponse,
)
def item_where_used(
    item_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: Db,
    principal: Actor,
    depth: Annotated[int, Query(ge=1, le=20)] = 20,
) -> ItemWhereUsedResponse:
    revision = db.get(Revision, revision_id)
    if revision is None or revision.part_id != item_id:
        raise HTTPException(status_code=404, detail="Item revision not found")
    legacy = where_used(revision_id, db, principal, depth)
    return ItemWhereUsedResponse(
        revision_id=revision_id,
        items=[
            ItemWhereUsedEntryResponse(
                occurrence_id=row.occurrence_id,
                occurrence_key=row.occurrence_key,
                parent_item=item_response(row.parent_part),
                parent_revision=ItemRevisionResponse(
                    **row.parent_revision.model_dump(exclude={"part_id"}),
                    item_id=row.parent_revision.part_id,
                ),
                direct=row.direct,
                depth=row.depth,
                total_quantity=row.total_quantity,
                path=row.path,
                occurrence_count=row.occurrence_count,
                paths=row.paths,
            )
            for row in legacy.items
        ],
        warnings=legacy.warnings,
    )
