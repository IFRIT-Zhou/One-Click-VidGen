import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import HTTPException, Request

from .db import get_user_by_id


COOKIE_NAME = "voice_video_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14
SESSION_SECRET = os.getenv("SESSION_SECRET", "voice-over-video-local-session-secret")


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def sign_session(user_id: int) -> str:
    payload = {
        "uid": int(user_id),
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }
    body = _b64_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(SESSION_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64_encode(signature)}"


def read_session(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, signature = token.split(".", 1)
    expected = hmac.new(SESSION_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    try:
        actual = _b64_decode(signature)
        if not hmac.compare_digest(actual, expected):
            return None
        payload = json.loads(_b64_decode(body).decode("utf-8"))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def current_user_from_request(request: Request) -> dict[str, Any] | None:
    payload = read_session(request.cookies.get(COOKIE_NAME))
    if not payload:
        return None
    return get_user_by_id(int(payload["uid"]))


def require_user(request: Request) -> dict[str, Any]:
    user = current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user
