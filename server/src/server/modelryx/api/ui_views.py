"""Saved engineering table layouts and filters."""

import json
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.db import get_db
from modelryx.models import UiSavedView

router = APIRouter(prefix="/api/v1", tags=["ui-views"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]


class ViewFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Literal["item_type", "cad", "status"]
    value: list[str] = Field(max_length=100)


class ViewSort(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Literal["item_number", "name", "item_type", "revision", "cad", "status"]
    desc: bool = False


class LibraryViewSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=100)
    itemName: str = Field(default="", max_length=100)
    filters: list[ViewFilter] = Field(default_factory=list, max_length=3)
    sorting: list[ViewSort] = Field(default_factory=list, max_length=1)
    visibility: dict[str, bool] = Field(default_factory=dict)
    sizing: dict[str, float] = Field(default_factory=dict)
    pageSize: int = Field(default=10, ge=1, le=100)
    density: Literal["comfortable", "compact"] = "comfortable"

    @model_validator(mode="after")
    def bounded_settings(self):
        if len(json.dumps(self.model_dump())) > 16_000:
            raise ValueError("Saved view settings are too large")
        return self


class SavedViewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=50)
    scope: Literal["PERSONAL", "SHARED"] = "PERSONAL"
    settings: LibraryViewSettings


class SavedViewResponse(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    scope: str
    settings: LibraryViewSettings
    can_edit: bool
    created_at: datetime
    updated_at: datetime


def response(row: UiSavedView, user_id: uuid.UUID, is_admin: bool) -> SavedViewResponse:
    return SavedViewResponse(
        id=row.id, owner_id=row.owner_id, name=row.name, scope=row.scope,
        settings=LibraryViewSettings.model_validate(row.settings_json),
        can_edit=(row.scope == "PERSONAL" and row.owner_id == user_id)
        or (row.scope == "SHARED" and is_admin),
        created_at=row.created_at, updated_at=row.updated_at,
    )


def editable(row: UiSavedView, user_id: uuid.UUID, is_admin: bool) -> bool:
    return (row.scope == "PERSONAL" and row.owner_id == user_id) or (
        row.scope == "SHARED" and is_admin
    )


@router.get("/ui-views", response_model=list[SavedViewResponse])
def list_views(db: Db, principal: Actor) -> list[SavedViewResponse]:
    user = user_for_principal(db, principal)
    rows = db.scalars(
        select(UiSavedView).where(
            UiSavedView.entity_type == "LIBRARY",
            or_(UiSavedView.scope == "SHARED", UiSavedView.owner_id == user.id),
        ).order_by(UiSavedView.scope, UiSavedView.name)
    )
    return [response(row, user.id, principal.role == "administrator") for row in rows]


@router.post("/ui-views", response_model=SavedViewResponse, status_code=status.HTTP_201_CREATED)
def create_view(payload: SavedViewWrite, request: Request, db: Db, principal: Actor):
    user = user_for_principal(db, principal)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="View name is required")
    if payload.scope == "SHARED" and principal.role != "administrator":
        raise HTTPException(status_code=403, detail="Only administrators can publish shared views")
    conditions = [
        UiSavedView.entity_type == "LIBRARY",
        UiSavedView.scope == payload.scope,
        UiSavedView.name == name,
    ]
    if payload.scope == "PERSONAL":
        conditions.append(UiSavedView.owner_id == user.id)
    duplicate = db.scalar(select(UiSavedView.id).where(*conditions))
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="A view with this name already exists")
    row = UiSavedView(owner_id=user.id, entity_type="LIBRARY", scope=payload.scope,
                      name=name, settings_json=payload.settings.model_dump())
    db.add(row)
    try:
        db.flush()
        record_audit(db, actor_id=user.id, action="ui_view.created", entity_type="ui_view",
                     entity_id=row.id, request_id=request.state.request_id,
                     details={"scope": row.scope, "name": row.name})
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="A view with this name already exists"
        ) from error
    db.refresh(row)
    return response(row, user.id, principal.role == "administrator")


@router.put("/ui-views/{view_id}", response_model=SavedViewResponse)
def update_view(view_id: uuid.UUID, payload: SavedViewWrite, request: Request,
                db: Db, principal: Actor):
    user = user_for_principal(db, principal)
    row = db.get(UiSavedView, view_id)
    if row is None or not editable(row, user.id, principal.role == "administrator"):
        raise HTTPException(status_code=404, detail="Saved view not found")
    if row.scope != payload.scope:
        raise HTTPException(status_code=409, detail="Create a new view to change its scope")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="View name is required")
    duplicate_conditions = [
        UiSavedView.entity_type == "LIBRARY", UiSavedView.scope == row.scope,
        UiSavedView.name == name, UiSavedView.id != row.id,
    ]
    if row.scope == "PERSONAL":
        duplicate_conditions.append(UiSavedView.owner_id == user.id)
    if db.scalar(select(UiSavedView.id).where(*duplicate_conditions)) is not None:
        raise HTTPException(status_code=409, detail="A view with this name already exists")
    row.name = name
    row.settings_json = payload.settings.model_dump()
    try:
        db.flush()
        record_audit(db, actor_id=user.id, action="ui_view.updated", entity_type="ui_view",
                     entity_id=row.id, request_id=request.state.request_id,
                     details={"scope": row.scope, "name": row.name})
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="A view with this name already exists"
        ) from error
    db.refresh(row)
    return response(row, user.id, principal.role == "administrator")


@router.delete("/ui-views/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_view(view_id: uuid.UUID, request: Request, db: Db, principal: Actor) -> None:
    user = user_for_principal(db, principal)
    row = db.get(UiSavedView, view_id)
    if row is None or not editable(row, user.id, principal.role == "administrator"):
        raise HTTPException(status_code=404, detail="Saved view not found")
    record_audit(db, actor_id=user.id, action="ui_view.deleted", entity_type="ui_view",
                 entity_id=row.id, request_id=request.state.request_id,
                 details={"scope": row.scope, "name": row.name})
    db.delete(row)
    db.commit()
