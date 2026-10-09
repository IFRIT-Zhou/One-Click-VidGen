from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from .config import settings


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters")
    return _password_digest(password)


def hash_admin_reset_password() -> str:
    """The fixed admin reset password is an explicit exception to registration policy."""
    return _password_digest("123456789")


def _password_digest(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=64
    )
    return "scrypt$" + base64.b64encode(salt + digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, payload = encoded.split("$", 1)
        raw = base64.b64decode(payload)
    except (ValueError, TypeError):
        return False
    if algorithm != "scrypt" or len(raw) != 80:
        return False
    salt, expected = raw[:16], raw[16:]
    actual = hashlib.scrypt(
        password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=64
    )
    return hmac.compare_digest(expected, actual)


def create_access_token(user_id: str, role: str, auth_version: int = 0) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=settings.access_token_seconds)
    token = jwt.encode(
        {
            "sub": user_id,
            "role": role,
            "type": "access",
            "auth_version": auth_version,
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
            "jti": secrets.token_hex(12),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    return token, settings.access_token_seconds


def decode_access_token(token: str) -> dict[str, object]:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Invalid token type")
    return payload


def new_refresh_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    return token, hashlib.sha256(token.encode()).hexdigest()


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
