import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.core.auth import Principal
from modelryx.models import AuditLog, User


def user_for_principal(db: Session, principal: Principal) -> User:
    user = db.scalar(select(User).where(User.external_subject == principal.subject))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User account is unavailable")
    return user


def record_audit(
    db: Session,
    *,
    actor_id: uuid.UUID,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    request_id: str,
    details: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            request_id=request_id,
            details_json=details or {},
        )
    )
