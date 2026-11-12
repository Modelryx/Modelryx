import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.api.approval_groups import require_admin
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.db import get_db
from modelryx.models import ItemNumberTemplate
from modelryx.services.item_numbers import render_item_number, validate_item_number_pattern

router = APIRouter(prefix="/api/v1", tags=["item-numbering"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]


class TemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    part_pattern: str = Field(min_length=1, max_length=100)
    assembly_pattern: str = Field(min_length=1, max_length=100)
    part_next_value: int = Field(default=1, ge=1)
    assembly_next_value: int = Field(default=1, ge=1)
    is_selected: bool = False


class TemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    part_pattern: str | None = Field(default=None, min_length=1, max_length=100)
    assembly_pattern: str | None = Field(default=None, min_length=1, max_length=100)
    part_next_value: int | None = Field(default=None, ge=1)
    assembly_next_value: int | None = Field(default=None, ge=1)
    is_enabled: bool | None = None
    is_selected: bool | None = None


class TemplateResponse(BaseModel):
    id: uuid.UUID
    name: str
    part_pattern: str
    assembly_pattern: str
    part_next_value: int
    assembly_next_value: int
    is_enabled: bool
    is_selected: bool
    part_example: str
    assembly_example: str
    created_at: datetime
    updated_at: datetime


def response(template: ItemNumberTemplate) -> TemplateResponse:
    return TemplateResponse(
        id=template.id, name=template.name,
        part_pattern=template.part_pattern, assembly_pattern=template.assembly_pattern,
        part_next_value=template.part_next_value,
        assembly_next_value=template.assembly_next_value,
        is_enabled=template.is_enabled, is_selected=template.is_selected,
        part_example=render_item_number(template.part_pattern, template.part_next_value),
        assembly_example=render_item_number(
            template.assembly_pattern, template.assembly_next_value
        ),
        created_at=template.created_at, updated_at=template.updated_at,
    )


def pattern_or_422(value: str) -> str:
    try:
        return validate_item_number_pattern(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def select_template(db: Session, template: ItemNumberTemplate) -> None:
    if not template.is_enabled:
        raise HTTPException(status_code=409, detail="Enable the template before selecting it")
    db.execute(update(ItemNumberTemplate).values(is_selected=False))
    template.is_selected = True


@router.get("/item-number-templates", response_model=list[TemplateResponse])
def list_enabled_templates(db: Db, principal: Actor) -> list[TemplateResponse]:
    del principal
    rows = db.scalars(
        select(ItemNumberTemplate)
        .where(ItemNumberTemplate.is_enabled.is_(True))
        .order_by(ItemNumberTemplate.name)
    )
    return [response(row) for row in rows]


@router.get("/admin/item-number-templates", response_model=list[TemplateResponse])
def list_templates(db: Db, principal: Actor) -> list[TemplateResponse]:
    require_admin(principal)
    rows = db.scalars(select(ItemNumberTemplate).order_by(ItemNumberTemplate.name))
    return [response(row) for row in rows]


@router.post(
    "/admin/item-number-templates",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_template(
    payload: TemplateCreate, request: Request, db: Db, principal: Actor
) -> TemplateResponse:
    require_admin(principal)
    actor = user_for_principal(db, principal)
    template = ItemNumberTemplate(
        name=payload.name.strip(),
        part_pattern=pattern_or_422(payload.part_pattern),
        assembly_pattern=pattern_or_422(payload.assembly_pattern),
        part_next_value=payload.part_next_value,
        assembly_next_value=payload.assembly_next_value,
        is_enabled=True, is_selected=False, created_by=actor.id,
    )
    if not template.name:
        raise HTTPException(status_code=422, detail="Template name cannot be blank")
    db.add(template)
    try:
        db.flush()
        if payload.is_selected:
            select_template(db, template)
        record_audit(
            db, actor_id=actor.id, action="item_number_template.created",
            entity_type="item_number_template", entity_id=template.id,
            request_id=request.state.request_id,
            details={"name": template.name},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Template name already exists") from exc
    db.refresh(template)
    return response(template)


@router.patch("/admin/item-number-templates/{template_id}", response_model=TemplateResponse)
def update_template(template_id: uuid.UUID, payload: TemplateUpdate, request: Request,
                    db: Db, principal: Actor) -> TemplateResponse:
    require_admin(principal)
    template = db.get(ItemNumberTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Item-number template not found")
    actor = user_for_principal(db, principal)
    fields = payload.model_fields_set
    if payload.name is not None:
        template.name = payload.name.strip()
        if not template.name:
            raise HTTPException(status_code=422, detail="Template name cannot be blank")
    if payload.part_pattern is not None:
        template.part_pattern = pattern_or_422(payload.part_pattern)
    if payload.assembly_pattern is not None:
        template.assembly_pattern = pattern_or_422(payload.assembly_pattern)
    if payload.part_next_value is not None:
        template.part_next_value = payload.part_next_value
    if payload.assembly_next_value is not None:
        template.assembly_next_value = payload.assembly_next_value
    if payload.is_enabled is not None:
        if not payload.is_enabled and template.is_selected:
            raise HTTPException(
                status_code=409, detail="Select another template before disabling this one"
            )
        template.is_enabled = payload.is_enabled
    if payload.is_selected is True:
        select_template(db, template)
    elif payload.is_selected is False and template.is_selected:
        raise HTTPException(status_code=409, detail="Select another template instead")
    record_audit(
        db, actor_id=actor.id, action="item_number_template.updated",
        entity_type="item_number_template", entity_id=template.id,
        request_id=request.state.request_id, details={"fields": sorted(fields)},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Template name already exists") from exc
    db.refresh(template)
    return response(template)
