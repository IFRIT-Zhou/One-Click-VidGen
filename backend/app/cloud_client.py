"""Authenticated client for the optional cluster GPU service.

Cloud credentials live only in this backend process.  The browser talks to the
local FastAPI proxy and never receives access or refresh tokens.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urljoin, urlparse

import requests


DEFAULT_CONNECT_TIMEOUT = 15.0
DEFAULT_READ_TIMEOUT = 180.0
DEFAULT_CLOUD_API_BASE_URL = "https://oneclickvidgen.com/api/v1"
ALIPAY_GATEWAY_HOSTS = {
    "openapi.alipay.com",
    "openapi-sandbox.dl.alipaydev.com",
    "openapi.alipaydev.com",
}


def _float_env(name: str, default: float, *, minimum: float = 0.1) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int, *, minimum: int = 0, maximum: int = 10) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class CloudConfig:
    base_url: str
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    poll_interval: float = 2.0
    max_wait_seconds: float = 3600.0
    retry_count: int = 2
    retry_delay: float = 1.0

    @property
    def configured(self) -> bool:
        parsed = urlparse(self.base_url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def load_cloud_config() -> CloudConfig:
    configured_base_url = os.getenv("CLOUD_API_BASE_URL", "").strip()
    return CloudConfig(
        base_url=(configured_base_url or DEFAULT_CLOUD_API_BASE_URL).rstrip("/"),
        connect_timeout=_float_env("CLOUD_API_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT),
        read_timeout=_float_env("CLOUD_API_READ_TIMEOUT", DEFAULT_READ_TIMEOUT),
        poll_interval=_float_env("CLOUD_JOB_POLL_INTERVAL", 2.0, minimum=0.2),
        max_wait_seconds=_float_env("CLOUD_JOB_MAX_WAIT_SECONDS", 3600.0, minimum=30.0),
        retry_count=_int_env("CLOUD_API_RETRY_COUNT", 2),
        retry_delay=_float_env("CLOUD_API_RETRY_DELAY_SECONDS", 1.0, minimum=0.1),
    )


class CloudApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        code: str = "CLOUD_API_ERROR",
        details: Any = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = str(code or "CLOUD_API_ERROR")
        self.details = details
        self.request_id = request_id


@dataclass
class CloudAuthSession:
    access_token: str
    refresh_token: str
    expires_at: float
    user: dict[str, Any] = field(default_factory=dict)

    def public_snapshot(self) -> dict[str, Any]:
        safe_user = {
            key: value
            for key, value in self.user.items()
            if key.lower() not in {"access_token", "refresh_token", "password", "password_hash"}
        }
        return {
            "authenticated": True,
            "user": safe_user,
            "access_expires_at": self.expires_at,
        }


class CloudSessionStore:
    """Process-local, per-workspace-user cloud sessions.

    Keeping the refresh token out of the project database avoids leaving cloud
    credentials in portable archives.  A backend restart intentionally requires
    the user to log in to the cluster again.
    """

    def __init__(self) -> None:
        self._sessions: dict[int, CloudAuthSession] = {}
        self._locks: dict[int, threading.RLock] = {}
        self._lock = threading.RLock()

    def lock_for(self, user_id: int) -> threading.RLock:
        with self._lock:
            return self._locks.setdefault(int(user_id), threading.RLock())

    def get(self, user_id: int) -> CloudAuthSession | None:
        with self._lock:
            return self._sessions.get(int(user_id))

    def set(self, user_id: int, session: CloudAuthSession) -> None:
        with self._lock:
            self._sessions[int(user_id)] = session

    def clear(self, user_id: int) -> None:
        with self._lock:
            self._sessions.pop(int(user_id), None)


cloud_sessions = CloudSessionStore()


class CloudClient:
    def __init__(
        self,
        user_id: int,
        *,
        config: CloudConfig | None = None,
        session_store: CloudSessionStore | None = None,
    ) -> None:
        self.user_id = int(user_id)
        self.config = config or load_cloud_config()
        self.sessions = session_store or cloud_sessions

    def _require_config(self) -> None:
        if not self.config.configured:
            raise CloudApiError(
                "集群云端地址尚未配置，请设置 CLOUD_API_BASE_URL",
                status_code=503,
                code="CLOUD_NOT_CONFIGURED",
            )

    def _url(self, path_or_url: str) -> str:
        self._require_config()
        value = str(path_or_url or "").strip()
        if not value:
            raise CloudApiError("云端接口路径为空", code="CLOUD_INVALID_URL")
        base = urlparse(self.config.base_url)
        parsed = urlparse(value)
        if parsed.scheme or parsed.netloc:
            if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
                raise CloudApiError("云端返回了不受信任的下载地址", code="CLOUD_INVALID_URL")
            return value
        # status_url/audio_url may already include /api/v1.  Resolve absolute
        # paths against the origin and short resource paths against Base URL.
        if value.startswith("/api/"):
            return f"{base.scheme}://{base.netloc}{value}"
        return urljoin(f"{self.config.base_url}/", value.lstrip("/"))

    @staticmethod
    def _error_from_response(response: requests.Response) -> CloudApiError:
        message = f"云端请求失败（HTTP {response.status_code}）"
        code = "CLOUD_API_ERROR"
        details: Any = None
        request_id: str | None = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            code = str(payload.get("code") or code)
            message = str(payload.get("message") or payload.get("detail") or message)
            details = payload.get("details")
            request_id = str(payload.get("request_id") or "") or None
        elif response.text.strip():
            message = response.text.strip()[:500]
        return CloudApiError(
            message,
            status_code=response.status_code,
            code=code,
            details=details,
            request_id=request_id,
        )

    def _send(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        retry_auth: bool = True,
        stream: bool = False,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        url = self._url(path)
        request_headers = dict(headers or {})
        if authenticated:
            session = self._ensure_access_token()
            request_headers["Authorization"] = f"Bearer {session.access_token}"
        method_name = method.upper()
        retryable = method_name in {"GET", "HEAD", "OPTIONS"} or "Idempotency-Key" in request_headers
        response: requests.Response | None = None
        for attempt in range(self.config.retry_count + 1):
            files = kwargs.get("files")
            if attempt and isinstance(files, dict):
                for value in files.values():
                    file_object = value[1] if isinstance(value, tuple) and len(value) > 1 else value
                    if hasattr(file_object, "seek"):
                        file_object.seek(0)
            try:
                response = requests.request(
                    method_name,
                    url,
                    headers=request_headers,
                    timeout=(self.config.connect_timeout, self.config.read_timeout),
                    stream=stream,
                    **kwargs,
                )
            except requests.RequestException as exc:
                if retryable and attempt < self.config.retry_count:
                    time.sleep(self.config.retry_delay * (attempt + 1))
                    continue
                raise CloudApiError(
                    f"无法连接集群云端服务：{exc}",
                    status_code=503,
                    code="CLOUD_CONNECTION_ERROR",
                ) from exc
            if response.status_code in {429, 502, 503, 504} and retryable and attempt < self.config.retry_count:
                retry_after = response.headers.get("Retry-After", "")
                response.close()
                try:
                    delay = max(self.config.retry_delay, min(10.0, float(retry_after)))
                except (TypeError, ValueError):
                    delay = self.config.retry_delay * (attempt + 1)
                time.sleep(delay)
                continue
            break
        assert response is not None
        if response.status_code == 401 and authenticated and retry_auth:
            response.close()
            self._refresh_access_token(force=True)
            files = kwargs.get("files")
            if isinstance(files, dict):
                for value in files.values():
                    file_object = value[1] if isinstance(value, tuple) and len(value) > 1 else value
                    if hasattr(file_object, "seek"):
                        file_object.seek(0)
            return self._send(
                method,
                path,
                authenticated=True,
                retry_auth=False,
                stream=stream,
                headers=headers,
                **kwargs,
            )
        if not 200 <= response.status_code < 300:
            error = self._error_from_response(response)
            response.close()
            raise error
        return response

    def _json_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._send(method, path, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise CloudApiError("云端返回了无效 JSON", code="CLOUD_INVALID_RESPONSE") from exc
        finally:
            response.close()
        if not isinstance(payload, dict):
            raise CloudApiError("云端响应格式无效", code="CLOUD_INVALID_RESPONSE")
        return payload

    def _session_from_payload(self, payload: dict[str, Any]) -> CloudAuthSession:
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        if not access_token or not refresh_token:
            raise CloudApiError("云端登录响应缺少 Token", code="CLOUD_INVALID_RESPONSE")
        try:
            expires_in = max(30, int(payload.get("expires_in") or 900))
        except (TypeError, ValueError):
            expires_in = 900
        return CloudAuthSession(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
            user=dict(payload.get("user") or {}),
        )

    def _ensure_access_token(self) -> CloudAuthSession:
        session = self.sessions.get(self.user_id)
        if session is None:
            raise CloudApiError("请先登录集群云端账户", status_code=401, code="CLOUD_LOGIN_REQUIRED")
        if session.expires_at <= time.time() + 30:
            return self._refresh_access_token(force=False)
        return session

    def _refresh_access_token(self, *, force: bool) -> CloudAuthSession:
        with self.sessions.lock_for(self.user_id):
            session = self.sessions.get(self.user_id)
            if session is None:
                raise CloudApiError("请先登录集群云端账户", status_code=401, code="CLOUD_LOGIN_REQUIRED")
            if not force and session.expires_at > time.time() + 30:
                return session
            try:
                payload = self._json_request(
                    "POST",
                    "/auth/refresh",
                    authenticated=False,
                    json={"refresh_token": session.refresh_token},
                )
                refreshed = self._session_from_payload({**payload, "user": payload.get("user") or session.user})
            except CloudApiError:
                self.sessions.clear(self.user_id)
                raise
            self.sessions.set(self.user_id, refreshed)
            return refreshed

    def session_snapshot(self) -> dict[str, Any]:
        session = self.sessions.get(self.user_id)
        return {
            "configured": self.config.configured,
            "base_url": self.config.base_url,
            **(session.public_snapshot() if session else {"authenticated": False, "user": None}),
        }

    def image_pool_runtime(self) -> dict[str, str]:
        """Provide short-lived credentials only to the local image worker.

        The browser never receives this value and the caller must not persist it
        in the job request or project archive.
        """
        session = self._ensure_access_token()
        return {
            "base_url": self.config.base_url,
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
        }

    def image_pool_status(self) -> dict[str, Any]:
        """Verify that the deployed cloud-api exposes the image-pool proxy."""
        return self._json_request("POST", "/image-pool/account-status", json={})

    def model_pool_status(self) -> dict[str, Any]:
        """Verify that the deployed cloud-api exposes the text-model proxy."""
        return self._json_request("POST", "/model-pool/status", json={})

    def adopt_image_pool_runtime(self, payload: dict[str, Any]) -> None:
        """Adopt tokens refreshed by the isolated module-4 worker process."""
        current = self.sessions.get(self.user_id)
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        if current is None or not access_token or not refresh_token:
            return
        try:
            expires_in = max(30, int(payload.get("expires_in") or 900))
        except (TypeError, ValueError):
            expires_in = 900
        self.sessions.set(self.user_id, CloudAuthSession(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
            user=current.user,
        ))

    def register(self, email: str, password: str, captcha_token: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"email": email, "password": password}
        if captcha_token:
            body["captcha_token"] = captcha_token
        return self._json_request("POST", "/auth/register", authenticated=False, json=body)

    def login(self, email: str, password: str) -> dict[str, Any]:
        payload = self._json_request(
            "POST", "/auth/login", authenticated=False, json={"email": email, "password": password}
        )
        session = self._session_from_payload(payload)
        self.sessions.set(self.user_id, session)
        return session.public_snapshot()

    def logout(self) -> None:
        try:
            if self.sessions.get(self.user_id):
                try:
                    response = self._send("POST", "/auth/logout")
                    response.close()
                except CloudApiError:
                    # Revocation is best effort because the public interface may
                    # not expose logout yet.  Clearing the local refresh token is
                    # still mandatory.
                    pass
        finally:
            self.sessions.clear(self.user_id)

    def account_summary(self) -> dict[str, Any]:
        return self._json_request("GET", "/account/summary")

    def wallet_ledger(self, *, page: int = 1, page_size: int = 20, entry_type: str = "all") -> dict[str, Any]:
        return self._json_request(
            "GET", "/wallet/ledger", params={"page": page, "page_size": page_size, "type": entry_type}
        )

    def quote(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json_request("POST", "/cloud/quotes", json=payload)

    def list_voices(self, *, voice_type: str = "all", page: int = 1, page_size: int = 50) -> dict[str, Any]:
        return self._json_request(
            "GET", "/cloud/voices", params={"type": voice_type, "page": page, "page_size": page_size}
        )

    def get_voice(self, voice_id: str) -> dict[str, Any]:
        return self._json_request("GET", f"/cloud/voices/{voice_id}")

    def stream_voice_audio(self, voice_id: str) -> requests.Response:
        return self._send("GET", f"/cloud/voices/{voice_id}/audio", stream=True)

    def upload_voice(
        self,
        *,
        file_object: BinaryIO,
        filename: str,
        content_type: str,
        display_name: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._json_request(
            "POST",
            "/cloud/voices",
            headers={"Idempotency-Key": idempotency_key},
            files={"file": (filename, file_object, content_type or "application/octet-stream")},
            data={"display_name": display_name},
        )

    def delete_voice(self, voice_id: str) -> None:
        response = self._send("DELETE", f"/cloud/voices/{voice_id}")
        response.close()

    def create_job(self, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        return self._json_request(
            "POST", "/cloud/jobs", headers={"Idempotency-Key": idempotency_key}, json=payload
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._json_request("GET", f"/cloud/jobs/{job_id}")

    def list_jobs(self, *, page: int = 1, page_size: int = 20, status: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if status:
            params["status"] = status
        return self._json_request("GET", "/cloud/jobs", params=params)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self._json_request("POST", f"/cloud/jobs/{job_id}/cancel")

    def create_recharge_order(self, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        order = self._json_request(
            "POST", "/recharge/orders", headers={"Idempotency-Key": idempotency_key}, json=payload
        )
        payment = order.get("payment") if isinstance(order, dict) else None
        if isinstance(payment, dict) and payment.get("provider") == "alipay":
            payment_url = str(payment.get("payment_url") or "").strip()
            parsed = urlparse(payment_url)
            if parsed.scheme != "https" or parsed.hostname not in ALIPAY_GATEWAY_HOSTS:
                raise CloudApiError(
                    "云端返回的支付宝收银台地址不受信任",
                    status_code=502,
                    code="CLOUD_PAYMENT_URL_REJECTED",
                )
        return order

    def list_recharge_products(self) -> dict[str, Any]:
        return self._json_request("GET", "/recharge/products")

    def get_recharge_order(self, order_id: str) -> dict[str, Any]:
        return self._json_request("GET", f"/recharge/orders/{order_id}")

    def download_to(self, path_or_url: str, destination: Path, *, max_bytes: int = 200 * 1024 * 1024) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        for attempt in range(self.config.retry_count + 1):
            response = self._send("GET", path_or_url, stream=True)
            written = 0
            try:
                with temporary.open("wb") as output:
                    for block in response.iter_content(chunk_size=1024 * 256):
                        if not block:
                            continue
                        written += len(block)
                        if written > max_bytes:
                            raise CloudApiError(
                                "云端音频超过客户端下载限制",
                                status_code=413,
                                code="CLOUD_AUDIO_TOO_LARGE",
                            )
                        output.write(block)
                if written <= 0:
                    raise CloudApiError("云端返回了空音频", code="CLOUD_EMPTY_AUDIO")
                os.replace(temporary, destination)
                return
            except requests.RequestException as exc:
                if attempt >= self.config.retry_count:
                    raise CloudApiError(
                        f"下载云端音频失败：{exc}",
                        status_code=503,
                        code="CLOUD_DOWNLOAD_ERROR",
                    ) from exc
                time.sleep(self.config.retry_delay * (attempt + 1))
            finally:
                response.close()
                temporary.unlink(missing_ok=True)


def cloud_client_for(user_id: int) -> CloudClient:
    return CloudClient(int(user_id))
