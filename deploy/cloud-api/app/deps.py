from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import User, UserRole, UserStatus
from .security import decode_access_token


bearer = HTTPBearer(auto_error=False)
Db = Annotated[Session, Depends(get_db)]


def current_user(
    db: Db,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer)
    ],
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc
    user = db.get(User, str(payload["sub"]))
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is not active")
    if payload.get("auth_version", 0) != user.auth_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Password changed; sign in again")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def current_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user


CurrentAdmin = Annotated[User, Depends(current_admin)]
