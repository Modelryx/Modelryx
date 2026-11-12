import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.core.roles import effective_role, roles_for_user
from modelryx.models import User

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    subject: str
    display_name: str
    role: str
    auth_version: int = 0
    roles: tuple[str, ...] = ()

    def has_role(self, *roles: str) -> bool:
        assigned = set(self.roles) if self.roles else {
            "designer" if self.role == "engineer" else self.role
        }
        return bool(assigned.intersection(roles))


def current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> Principal:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_access_token(credentials.credentials, settings)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    user = db.scalar(select(User).where(User.external_subject == claims.subject))
    if user is None or not user.is_active or (user.auth_version or 0) != claims.auth_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled or the token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    roles = tuple(sorted(roles_for_user(user)))
    return Principal(
        subject=user.external_subject,
        display_name=user.display_name,
        role=effective_role(roles),
        auth_version=user.auth_version or 0,
        roles=roles,
    )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_access_token(principal: Principal, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    now = int(time.time())
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64encode(
        json.dumps(
            {
                **asdict(principal),
                "iss": settings.jwt_issuer,
                "iat": now,
                "exp": now + settings.jwt_expiry_minutes * 60,
            },
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}"
    signature = hmac.new(
        settings.jwt_secret.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64encode(signature)}"


def decode_access_token(token: str, settings: Settings) -> Principal:
    try:
        header, payload, signature = token.split(".")
        signing_input = f"{header}.{payload}"
        expected = hmac.new(
            settings.jwt_secret.encode(), signing_input.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_b64decode(signature), expected):
            raise ValueError("invalid signature")
        claims = json.loads(_b64decode(payload))
        token_header = json.loads(_b64decode(header))
        if token_header.get("alg") != "HS256" or claims.get("iss") != settings.jwt_issuer:
            raise ValueError("invalid token metadata")
        if not isinstance(claims.get("exp"), int) or claims["exp"] <= int(time.time()):
            raise ValueError("expired token")
        claim_roles = claims.get("roles")
        if not claim_roles:
            claim_roles = ["designer" if claims.get("role") == "engineer" else claims.get("role")]
        if (
            not isinstance(claim_roles, list)
            or not claim_roles
            or not set(claim_roles) <= {"reader", "designer", "reviewer", "administrator"}
        ):
            raise ValueError("invalid role")
        return Principal(
            subject=str(claims["subject"]),
            display_name=str(claims["display_name"]),
            role=str(claims["role"]),
            auth_version=int(claims.get("auth_version", 0)),
            roles=tuple(sorted(str(value) for value in claim_roles)),
        )
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid token") from exc


def hash_password(password: str, iterations: int = 600_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iteration_count = int(rounds)
        if not 100_000 <= iteration_count <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), _b64decode(salt), iteration_count)
        return hmac.compare_digest(actual, _b64decode(expected))
    except (binascii.Error, OverflowError, TypeError, ValueError):
        return False
