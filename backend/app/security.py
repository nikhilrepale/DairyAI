from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import Dairy, DairyMembership, User

password_hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class TenantContext:
    user: User
    dairy: Dairy
    membership: DairyMembership


def _jwt_secret() -> str:
    env_file = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_file, override=False)
    secret = os.getenv("JWT_SECRET_KEY", "")
    if len(secret) < 32 or secret.startswith('REPLACE_WITH_') or secret.lower() in {'change-me', 'your-secret'}:
        raise HTTPException(
            status_code=503,
            detail=(
                "Authentication is not configured. Set JWT_SECRET_KEY in backend/.env "
                "or the deployment environment to a random secret of at least 32 characters."
            ),
        )
    return secret


def ensure_auth_configured() -> None:
    _jwt_secret()
    try:
        token_hours = int(os.getenv("JWT_ACCESS_TOKEN_HOURS", "12"))
    except ValueError as exc:
        raise HTTPException(
            status_code=503, detail="JWT_ACCESS_TOKEN_HOURS must be an integer"
        ) from exc
    if token_hours < 1:
        raise HTTPException(
            status_code=503, detail="JWT_ACCESS_TOKEN_HOURS must be at least 1"
        )


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerificationError, ValueError):
        return False


def create_access_token(user_id: int, dairy_id: int) -> str:
    now = datetime.now(timezone.utc)
    ensure_auth_configured()
    token_hours = int(os.getenv("JWT_ACCESS_TOKEN_HOURS", "12"))
    payload = {
        "sub": str(user_id),
        "dairy_id": dairy_id,
        "iat": now,
        "exp": now + timedelta(hours=token_hours),
        "type": "access",
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def get_tenant_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> TenantContext:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    try:
        payload = jwt.decode(credentials.credentials, _jwt_secret(), algorithms=["HS256"])
        if payload.get("type") != "access":
            raise unauthorized
        user_id, dairy_id = int(payload["sub"]), int(payload["dairy_id"])
    except HTTPException:
        raise
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise unauthorized from exc

    user = db.get(User, user_id)
    if user is None or not user.active:
        raise unauthorized
    membership = (
        db.query(DairyMembership)
        .filter_by(user_id=user_id, dairy_id=dairy_id, active=True)
        .first()
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="No active membership for this dairy")
    dairy = db.query(Dairy).filter_by(id=dairy_id, active=True).first()
    if dairy is None:
        raise HTTPException(status_code=403, detail="Dairy is inactive")
    return TenantContext(user=user, dairy=dairy, membership=membership)


def require_roles(*allowed_roles: str):
    def dependency(
        context: Annotated[TenantContext, Depends(get_tenant_context)],
    ) -> TenantContext:
        if context.membership.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient dairy membership role")
        return context

    return dependency


TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
OwnerManagerDep = Annotated[TenantContext, Depends(require_roles("OWNER", "MANAGER"))]
OwnerDep = Annotated[TenantContext, Depends(require_roles("OWNER"))]
