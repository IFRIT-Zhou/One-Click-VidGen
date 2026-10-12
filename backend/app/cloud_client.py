"""Authenticated client for the optional cluster GPU service.

Cloud credentials live only in this backend process.  The browser talks to the
local FastAPI proxy and never receives access or refresh tokens.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
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


def tts_queue_accepting(payload: dict[str, Any]) -> bool:
    """Only an explicit healthy on-demand admission contract permits cold GPUs."""
    admission=payload.get('tts_admission')
    if payload.get('control_api') is not True or not isinstance(admission,dict):
        return False
    if not all(admission.get(key) is True for key in ('accepting','on_demand','scheduler_ready','queue_ready')):
        return False
    ray=payload.get('ray') if isinstance(payload.get('ray'),dict) else {}
    dispatcher=ray.get('dispatcher') if isinstance(ray.get('dispatcher'),dict) else {}
    return not any(dispatcher.get(key) is False for key in ('redis_ready','consumer_alive'))


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
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = str(code or "CLOUD_API_ERROR")
        self.details = details
        self.request_id = request_id
        self.retry_after = retry_after


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
    """Persist refresh tokens outside project data and archives."""

    def __init__(self) -> None:
        self._sessions: dict[int, CloudAuthSession] = {}
        self._lock = threading.RLock()
        configured_path = os.getenv("CLOUD_SESSION_STORE_PATH", "").strip()
        self._path = Path(configured_path or (Path.cwd() / ".cloud_sessions.json")).resolve()
        self._disk_depth = 0
        self._load()

    def _load(self) -> None:
        import json
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._sessions = {}
            for user_id, value in raw.items():
                if not isinstance(value, dict) or not value.get("refresh_token"):
                    continue
                self._sessions[int(user_id)] = CloudAuthSession(
                    access_token=str(value.get("access_token") or ""),
                    refresh_token=str(value["refresh_token"]),
                    expires_at=float(value.get("expires_at") or 0),
                    user=dict(value.get("user") or {}),
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._sessions = {}
            return

    def _save(self) -> None:
        import json
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps({
            str(user_id): {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expires_at": session.expires_at,
                "user": session.user,
            } for user_id, session in self._sessions.items()
        }, ensure_ascii=False), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._path)

    @contextmanager
    def lock_for(self, user_id: int):
        """Serialize token rotation across backend threads AND image subprocesses."""
        with self._lock:
            if self._disk_depth:
                self._disk_depth += 1
                try:
                    yield
                finally:
                    self._disk_depth -= 1
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(self._path) + ".lock", "a+b") as handle:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0, 2)
                    if handle.tell() == 0:
                        handle.write(b"0")
                        handle.flush()
                    while True:
                        handle.seek(0)
                        try:
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            time.sleep(0.05)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                self._disk_depth = 1
                try:
                    yield
                finally:
                    self._disk_depth = 0
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def get(self, user_id: int) -> CloudAuthSession | None:
        with self.lock_for(user_id):
            self._load()
            return self._sessions.get(int(user_id))

    def set(self, user_id: int, session: CloudAuthSession) -> None:
        with self.lock_for(user_id):
            self._load()
            self._sessions[int(user_id)] = session
            self._save()

    def clear(self, user_id: int) -> None:
        with self.lock_for(user_id):
            self._load()
            self._sessions.pop(int(user_id), None)
            self._save()


cloud_sessions = CloudSessionStore()
_runtime_stores: dict[str, CloudSessionStore] = {}
_runtime_stores_lock = threading.Lock()


def cloud_client_for_image(config: dict) -> "CloudClient":
    path = str(config["cloud_session_store_path"])
    with _runtime_stores_lock:
        store = _runtime_stores.get(path)
        if store is None:
            store = CloudSessionStore()
            store._path = Path(path).resolve()
            store._sessions = {}
            store._load()
            _runtime_stores[path] = store
    return CloudClient(int(config["cloud_user_id"]), session_store=store,
                       config=CloudConfig(base_url=config["cloud_base_url"]))


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
            retry_after=response.headers.get("Retry-After") if response.status_code == 429 else None,
        )

    def _send(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        retry_auth: bool = True,
        raise_errors: bool = True,
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
            if response.status_code in {408, 429, 502, 503, 504} and retryable and attempt < self.config.retry_count:
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
            self._refresh_access_token(force=True, failed_token=session.access_token)
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
                raise_errors=raise_errors,
                stream=stream,
                headers=headers,
                **kwargs,
            )
        if response.status_code == 401 and authenticated:
            response.close()
            with self.sessions.lock_for(self.user_id):
                current = self.sessions.get(self.user_id)
                if current and current.access_token == session.access_token:
                    self.sessions.clear(self.user_id)
            raise CloudApiError("登录已过期，请重新登录；已完成图片和剩余任务已保留。",
                                status_code=401, code="CLOUD_LOGIN_REQUIRED")
        if raise_errors and not 200 <= response.status_code < 300:
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
            expires_in = max(30, int(payload.get("expires_in") or 3600))
        except (TypeError, ValueError):
            expires_in = 3600
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

    def _refresh_access_token(self, *, force: bool, failed_token: str | None = None) -> CloudAuthSession:
        with self.sessions.lock_for(self.user_id):
            session = self.sessions.get(self.user_id)
            if session is None:
                raise CloudApiError("请先登录集群云端账户", status_code=401, code="CLOUD_LOGIN_REQUIRED")
            if force and failed_token is not None and session.access_token != failed_token:
                return session
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
            except CloudApiError as exc:
                if exc.status_code == 401:
                    self.sessions.clear(self.user_id)
                    raise CloudApiError("登录已过期，请重新登录；已完成图片和剩余任务已保留。",
                                        status_code=401, code="CLOUD_LOGIN_REQUIRED") from exc
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

    def cluster_health(self) -> dict[str, Any]:
        """Check Ray readiness, retrying transient downstream probe failures.

        The cloud gateway returns HTTP 200 with ``ok=false`` when its internal
        Ray probe briefly exceeds its timeout. Treat only that transient health
        result as retryable; persistent or non-timeout failures are returned to
        the caller so a genuinely unavailable cluster still blocks submission.
        """
        attempts = max(1, min(3, self.config.retry_count + 1))
        payload: dict[str, Any] = {}
        for attempt in range(attempts):
            payload = self._json_request("GET", "/health", authenticated=False)
            if payload.get("ok") is not False or tts_queue_accepting(payload):
                return payload
            detail = str(payload.get("ray_error") or "").strip().lower()
            transient = any(
                marker in detail
                for marker in ("timed out", "timeout", "temporarily unavailable", "connection reset")
            )
            if not transient or attempt + 1 >= attempts:
                return payload
            time.sleep(self.config.retry_delay * (attempt + 1))
        return payload

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
            "cloud_user_id": str(self.user_id),
            "cloud_session_store_path": str(self.sessions._path),
        }

    def image_pool_status(self, method: str = "running") -> dict[str, Any]:
        """Verify that the deployed cloud-api exposes the image-pool proxy."""
        from .cloud_image_channel import image_provider
        provider = image_provider(method)
        path = "/image-pool/account-status" + ("?provider=ican" if provider == "ican" else "")
        return self._json_request("POST", path, json={})

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
        # Modern workers update the shared store at refresh time; do not extend
        # token expiry simply because an unchanged snapshot was returned.
        if current.access_token == access_token and current.refresh_token == refresh_token:
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

    def request_password_reset(self, email: str) -> dict[str, Any]:
        return self._json_request("POST", "/auth/password-reset/request", authenticated=False, json={"email": email})

    def confirm_password_reset(self, email: str, code: str, password: str) -> dict[str, Any]:
        payload = self._json_request("POST", "/auth/password-reset/confirm", authenticated=False,
                                     json={"email": email, "code": code, "password": password})
        if payload.get("ok") is not True:
            raise CloudApiError("云端未确认密码重置成功", code="CLOUD_INVALID_RESPONSE")
        session = self.sessions.get(self.user_id)
        if session and str(session.user.get("email", "")).lower() == email.lower():
            self.sessions.clear(self.user_id)
        return payload

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

    def video_capabilities(self):
        return self._json_request("GET", "/cloud/video/capabilities")

    def video_asset(self, path: Path, kind="image"):
        import mimetypes
        with path.open("rb") as source:
            return self._json_request("POST", "/cloud/video/assets", data={"kind": kind},
                files={"file": (path.name, source, mimetypes.guess_type(path.name)[0] or "application/octet-stream")})

    def video_quote(self, spec):
        return self._json_request("POST", "/cloud/video/quotes", json={"spec": spec})

    def create_video_job(self, quote_id, client_job_id):
        return self._json_request("POST", "/cloud/video/jobs", headers={"Idempotency-Key": client_job_id},
                                  json={"quote_id": quote_id, "client_job_id": client_job_id})

    def video_job(self, identity, *, by_client=False):
        from urllib.parse import quote
        segment = "by-client-id/" if by_client else ""
        return self._json_request("GET", "/cloud/video/jobs/" + segment + quote(str(identity), safe=""))

    def cancel_video_job(self, identity):
        from urllib.parse import quote
        return self._json_request("POST", "/cloud/video/jobs/" + quote(str(identity), safe="") + "/cancel")

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
