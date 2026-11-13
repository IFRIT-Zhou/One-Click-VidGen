# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun
# AGPL-3.0 Section 7 terms: ../../ADDITIONAL_TERMS.md

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Any

from fastapi import HTTPException, Request

from .db import get_user_by_id


COOKIE_NAME = "voice_video_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
_SECRET_LOCK = threading.Lock()


def session_secret() -> str:
    global SESSION_SECRET
    with _SECRET_LOCK:
        if not SESSION_SECRET or SESSION_SECRET == "voice-over-video-local-session-secret":
            from .config import ENV_PATH, _parse_env_lines, save_project_env_values
            saved = _parse_env_lines(ENV_PATH).get("SESSION_SECRET", "")
            if not saved or saved == "voice-over-video-local-session-secret":
                saved = secrets.token_urlsafe(48)
                save_project_env_values({"SESSION_SECRET": saved})
            SESSION_SECRET = saved
        return SESSION_SECRET
AUTH_MODE = os.getenv("AUTH_MODE", "account").strip().lower()
DISABLE_AUTH = os.getenv("DISABLE_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}
LOCAL_USER_ID = int(os.getenv("LOCAL_USER_ID", "1"))
LOCAL_USER_EMAIL = os.getenv("LOCAL_USER_EMAIL", "local@voice-over-video.local")
LOCAL_USER_NAME = os.getenv("LOCAL_USER_NAME", "本地工作台")


def local_auth_enabled() -> bool:
    return DISABLE_AUTH or AUTH_MODE in {"local", "none", "disabled"}


def local_user() -> dict[str, Any]:
    return {
        "id": LOCAL_USER_ID,
        "email": LOCAL_USER_EMAIL,
        "name": LOCAL_USER_NAME,
        "created_at": None,
        "local": True,
    }


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
    signature = hmac.new(session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64_encode(signature)}"


def read_session(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, signature = token.split(".", 1)
    expected = hmac.new(session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
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
    if local_auth_enabled():
        return local_user()
    payload = read_session(request.cookies.get(COOKIE_NAME))
    if not payload:
        return None
    return get_user_by_id(int(payload["uid"]))


def require_user(request: Request) -> dict[str, Any]:
    user = current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user
