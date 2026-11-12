import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelryx.core.audit import record_audit
from modelryx.core.auth import Principal, create_access_token, hash_password, verify_password
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.core.roles import assign_roles, effective_role, roles_for_user
from modelryx.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
Db = Annotated[Session, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]
DUMMY_PASSWORD_HASH = hash_password("modelryx-invalid-password-sentinel")
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_DURATION = timedelta(minutes=15)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    display_name: str
    role: str
    roles: list[str]


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Db, settings: Config) -> LoginResponse:
    username = payload.username.strip()
    user = db.scalar(select(User).where(User.external_subject == username).with_for_update())
    is_bootstrap = secrets_match_bootstrap(payload, username, settings)
    if is_bootstrap and (user is None or user.password_hash is None):
        if user is None:
            user = User(
                external_subject=username,
                display_name=settings.bootstrap_admin_display_name,
                role="administrator",
                password_hash=hash_password(payload.password),
                password_changed_at=datetime.now(UTC),
            )
            db.add(user)
            assign_roles(user, ["administrator"])
        else:
            user.password_hash = hash_password(payload.password)
            user.password_changed_at = datetime.now(UTC)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            user = db.scalar(select(User).where(User.external_subject == username))
    password_valid = verify_password(
        payload.password,
        user.password_hash if user is not None else DUMMY_PASSWORD_HASH,
    )
    now = datetime.now(UTC)
    locked = user is not None and user.locked_until is not None and user.locked_until > now
    if user is not None and user.is_active and not locked and not password_valid:
        if user.locked_until is not None:
            user.locked_until = None
            user.failed_login_attempts = 0
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
            user.locked_until = now + LOGIN_LOCKOUT_DURATION
            record_audit(
                db,
                actor_id=user.id,
                action="user.login_locked",
                entity_type="user",
                entity_id=user.id,
                request_id=request.state.request_id,
                details={"locked_until": user.locked_until.isoformat()},
            )
        db.commit()
    if user is None or user.is_active is False or locked or not password_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    roles = tuple(sorted(roles_for_user(user)))
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    db.commit()
    principal = Principal(
        subject=user.external_subject,
        display_name=user.display_name,
        role=effective_role(roles),
        auth_version=user.auth_version or 0,
        roles=roles,
    )
    return LoginResponse(
        access_token=create_access_token(principal, settings),
        expires_in=settings.jwt_expiry_minutes * 60,
        display_name=user.display_name,
        role=effective_role(roles),
        roles=list(roles),
    )


def secrets_match_bootstrap(payload: LoginRequest, username: str, settings: Settings) -> bool:
    return secrets.compare_digest(
        username, settings.bootstrap_admin_username
    ) and secrets.compare_digest(payload.password, settings.bootstrap_admin_password)
