"""Reject cycles introduced when linking an existing assembly revision."""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.models import BomLine, BomViewRevision


def ensure_no_bom_cycle(db: Session, parent_id: uuid.UUID, child_ids: set[uuid.UUID]) -> None:
    """Walk current child BOMs; each parent edit replaces its own outgoing edges."""
    pending = list(child_ids)
    visited: set[uuid.UUID] = set()
    while pending:
        revision_id = pending.pop()
        if revision_id == parent_id:
            raise HTTPException(status_code=422, detail="BOM relationship would create a cycle")
        if revision_id in visited:
            continue
        visited.add(revision_id)
        if len(visited) > 10000:
            raise HTTPException(status_code=422, detail="BOM is too large to verify safely")
        view_id = db.scalar(
            select(BomViewRevision.id)
            .where(BomViewRevision.revision_id == revision_id)
            .order_by(BomViewRevision.sequence.desc())
            .limit(1)
        )
        if view_id is not None:
            pending.extend(
                db.scalars(
                    select(BomLine.child_revision_id).where(BomLine.bom_view_revision_id == view_id)
                )
            )


def unresolved_native_occurrences(db: Session, root_id: uuid.UUID, components: list) -> bool:
    """Compare native occurrence hierarchy with resolved current BOM relationships."""
    by_parent: dict[str | None, list[dict]] = {}
    for component in components:
        if isinstance(component, dict) and component.get("occurrence_id"):
            by_parent.setdefault(component.get("parent_occurrence_id"), []).append(component)
    pending: list[tuple[uuid.UUID, str | None]] = [(root_id, None)]
    seen: set[tuple[uuid.UUID, str | None]] = set()
    matched = 0
    while pending:
        revision_id, native_parent = pending.pop()
        if (revision_id, native_parent) in seen:
            return True
        seen.add((revision_id, native_parent))
        if len(seen) > 10000:
            return True
        view_id = db.scalar(
            select(BomViewRevision.id)
            .where(BomViewRevision.revision_id == revision_id)
            .order_by(BomViewRevision.sequence.desc())
            .limit(1)
        )
        actual = (
            {
                line.occurrence_key: line.child_revision_id
                for line in db.scalars(
                    select(BomLine).where(BomLine.bom_view_revision_id == view_id)
                )
            }
            if view_id is not None
            else {}
        )
        for occurrence in by_parent.get(native_parent, []):
            occurrence_id = str(occurrence["occurrence_id"])
            child = actual.get(occurrence_id)
            if child is None:
                return True
            matched += 1
            pending.append((child, occurrence_id))
    return matched != sum(len(group) for group in by_parent.values())
