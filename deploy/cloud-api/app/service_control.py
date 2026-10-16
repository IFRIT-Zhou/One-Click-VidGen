"""Durable, scheduler-owned admin requests for device service controls.

The Cloud API deliberately does not open SSH, call Docker, or invoke a node
agent from an HTTP worker.  A request is a small, atomically-written record in
the scheduler state directory.  The single GPU scheduler claims the record,
performs the drain/phase checks, and writes the terminal result back.

Only the two service/node pairs with an installed, reviewed control path are
accepted here: H3 on ``10.250.0.3`` and TTS on ``10.250.0.20``.  Everything
else remains read-only and fails closed.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid
from typing import Any, Iterator


NODE3 = "10.250.0.3"
NODE20 = "10.250.0.20"
SUPPORTED = {(NODE3, "h3"), (NODE20, "tts")}
TERMINAL = {"succeeded", "failed", "cancelled"}
ACTIVE = {"pending", "running"}
SCHEMA = 1
_WINDOWS_LOCK = threading.RLock()


class ServiceControlError(RuntimeError):
    """A safe, public error code for a rejected control request."""

    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(code)
        self.code = code
        self.message = message
        self.status_code = status_code


def normalize_node_ip(value: Any) -> str | None:
    try:
        return str(ipaddress.ip_interface(str(value)).ip)
    except (ValueError, TypeError):
        return None


def state_root(root: str | os.PathLike[str] | None = None) -> Path:
    return Path(root or os.environ.get("GPU_SCHEDULER_STATE_DIR", "/var/lib/cloud-api/gpu-scheduler"))


def operation_root(root: str | os.PathLike[str] | None = None) -> Path:
    return state_root(root) / "service-controls" / "ops"


def _lock_path(root: Path) -> Path:
    return root / "service-controls" / "control.lock"


@contextmanager
def control_lock(root: str | os.PathLike[str] | None = None) -> Iterator[None]:
    """Serialize API and scheduler metadata updates without a long-lived lock.

    ``fcntl`` is used in production.  The process lock keeps isolated Windows
    tests deterministic while retaining the same atomic-write behavior.
    """
    root_path = state_root(root)
    path = _lock_path(root_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    with _WINDOWS_LOCK:
        stream = path.open("a+")
        try:
            try:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                # Windows development/test hosts have no fcntl.  The process
                # lock still protects concurrent requests in that process.
                pass
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            stream.close()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    old_mode = path.stat().st_mode & 0o777 if path.exists() else 0o640
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, old_mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) and value.get("schema") == SCHEMA else None


def _read_cache(path: Path) -> dict[str, Any]:
    """Read a scheduler/telemetry cache, whose schema predates this module."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _all(root: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    directory = operation_root(root)
    try:
        paths = list(directory.glob("*.json"))
    except OSError:
        return []
    records = [record for path in paths if (record := _read(path)) is not None]
    records.sort(key=lambda record: (float(record.get("created_at") or 0), record.get("operation_id", "")))
    return records


def _digest(*, device_id: str, node_ip: str, service: str, action: str,
            desired_running: bool, reason: str) -> str:
    payload = {"device_id": device_id, "node_ip": node_ip, "service": service,
               "action": action, "desired_running": desired_running, "reason": reason}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _public(record: dict[str, Any]) -> dict[str, Any]:
    """Return the stable response shape; never return administrator metadata."""
    return {"operation_id": record["operation_id"], "status": record["status"],
            "action": record["action"], "service": record["service"],
            "device_id": record["device_id"],
            "desired_running": bool(record["desired_running"]),
            "reason": record.get("reason", ""),
            **({"error_code": record["error_code"], "error_message": record["error_message"]}
               if record.get("status") in TERMINAL and record.get("error_code") else {})}


def _operation_path(root: Path, operation_id: str) -> Path:
    # Operation ids are generated locally and this check prevents path
    # traversal if a scheduler caller passes untrusted state.
    if not operation_id.startswith("svcctl_") or any(ch not in "svcctl_0123456789abcdef" for ch in operation_id):
        raise ValueError("invalid operation id")
    return operation_root(root) / f"{operation_id}.json"


def get_operation(operation_id: str, *, root: str | os.PathLike[str] | None = None) -> dict[str, Any] | None:
    try:
        return _read(_operation_path(state_root(root), operation_id))
    except ValueError:
        return None


def _same_request(record: dict[str, Any], digest: str) -> bool:
    return record.get("request_digest") == digest


def _validate_observation(node_ip: str, service: str, *, observed: dict[str, Any] | None,
                          root: Path, now: float) -> dict[str, Any]:
    """Validate the latest read-only cache before accepting a mutation."""
    if observed is None:
        raise ServiceControlError("NODE_STATE_UNKNOWN", "节点服务状态未知，已拒绝控制请求", 503)
    status = str(observed.get("status") or "unknown")
    running = observed.get("container_running")
    if not isinstance(running, bool) or status in {"unknown", "not_installed"}:
        raise ServiceControlError("NODE_STATE_UNKNOWN", "节点服务状态未知，已拒绝控制请求", 503)
    # The cache's reason is intentionally not trusted as an authorization
    # signal; inspect the scheduler-owned source fields below as well.
    filename = "node-health.json" if node_ip == NODE3 else "node20-health.json"
    node = _read_cache(state_root(root) / filename)
    stamp = node.get("time")
    try:
        fresh = math.isfinite(float(stamp)) and 0 <= now - float(stamp) <= (45 if node_ip == NODE3 else 90)
    except (TypeError, ValueError):
        fresh = False
    if not fresh or node.get("reachable") is not True:
        raise ServiceControlError("NODE_UNREACHABLE", "节点不可达或状态探测已过期", 503)
    if node.get("control_ready") is not True or observed.get("control_ready") is not True:
        raise ServiceControlError("CONTROL_AGENT_UNAVAILABLE", "节点尚未安装并验证安全控制代理", 503)
    if not _dedicated_ready(root, now):
        raise ServiceControlError("SCHEDULER_UNAVAILABLE", "专用设备调度器未就绪，已拒绝控制请求", 503)
    if service == "h3" and node_ip == NODE3:
        local = node.get("local_h3") or {}
        if (node.get("h3_busy") is True or local.get("busy") is True
                or (observed or {}).get("status") == "busy"):
            raise ServiceControlError("SERVICE_BUSY", "H3 当前有任务，已拒绝切换", 409)
        if local.get("blocked") is True:
            raise ServiceControlError("SERVICE_BUSY", "本地 H3 占用显卡，已拒绝切换", 409)
    placement = _read_cache(state_root(root) / "placement-health.json")
    ptime = placement.get("time")
    try:
        placement_fresh = math.isfinite(float(ptime)) and 0 <= now - float(ptime) <= 90
    except (TypeError, ValueError):
        placement_fresh = False
    if service == "tts":
        if not placement_fresh or placement.get("ok") is not True:
            raise ServiceControlError("PLACEMENT_UNKNOWN", "TTS 调度归属未知，已拒绝控制请求", 503)
        if placement.get("transaction_pending") is True:
            raise ServiceControlError("TRANSITION_PENDING", "调度器正在切换 GPU 阶段", 409)
    elif placement_fresh and placement.get("transaction_pending") is True:
        raise ServiceControlError("TRANSITION_PENDING", "调度器正在切换 GPU 阶段", 409)
    return {"running": running, "node": node, "placement": placement}


def _dedicated_ready(root: Path, now: float) -> bool:
    heartbeat = _read_cache(root / "heartbeat.json")
    try:
        age = now - float(heartbeat.get("time"))
    except (TypeError, ValueError):
        return False
    return 0 <= age <= 20 and heartbeat.get("policy") == "dedicated"


def enqueue(*, admin_id: str, device_id: str, node_ip: str | None, service: str,
            action: str, reason: str, idempotency_key: str,
            observed: dict[str, Any] | None = None,
            root: str | os.PathLike[str] | None = None,
            now: float | None = None) -> tuple[dict[str, Any], bool]:
    """Validate and persist one request.  Returns ``(public, created)``."""
    now = time.time() if now is None else now
    root_path = state_root(root)
    node_ip = normalize_node_ip(node_ip)
    if node_ip is None or (node_ip, service) not in SUPPORTED:
        raise ServiceControlError("UNSUPPORTED_SERVICE", "该节点服务没有受控启停通道", 409)
    if service not in {"tts", "h3"} or action not in {"start", "stop"}:
        raise ServiceControlError("INVALID_SERVICE_ACTION", "不支持的服务或操作", 422)
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 500:
        raise ServiceControlError("INVALID_REASON", "操作原因长度必须为 3–500 个字符", 422)
    if not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 160:
        raise ServiceControlError("INVALID_IDEMPOTENCY_KEY", "幂等键长度必须为 16–160 个字符", 422)
    desired_running = action == "start"
    digest = _digest(device_id=device_id, node_ip=node_ip, service=service, action=action,
                     desired_running=desired_running, reason=reason.strip())
    with control_lock(root_path):
        records = _all(root_path)
        for record in records:
            if record.get("admin_id") == admin_id and record.get("idempotency_key") == idempotency_key:
                if not _same_request(record, digest):
                    raise ServiceControlError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同的服务操作", 409)
                return _public(record), False
        for record in records:
            if (record.get("node_ip") == node_ip and record.get("service") == service
                    and record.get("status") in ACTIVE):
                raise ServiceControlError("CONTROL_BUSY", "该服务已有操作正在排队或执行", 409)
        observation = _validate_observation(node_ip, service, observed=observed,
                                            root=root_path, now=now)
        operation_id = "svcctl_" + uuid.uuid4().hex
        record = {"schema": SCHEMA, "operation_id": operation_id, "device_id": device_id,
                  "node_ip": node_ip, "service": service, "action": action,
                  "desired_running": desired_running, "reason": reason.strip(),
                  "admin_id": admin_id, "idempotency_key": idempotency_key,
                  "request_digest": digest, "status": "pending", "error_code": None,
                  "error_message": None, "result": None, "created_at": now,
                  "updated_at": now, "started_at": None, "finished_at": None,
                  "observed_running": observation["running"]}
        _atomic_json(_operation_path(root_path, operation_id), record)
        return _public(record), True


def list_pending(*, root: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    """Return pending operations in creation order for the sole scheduler."""
    return [record for record in _all(root) if record.get("status") in ACTIVE]


def claim(operation_id: str, *, root: str | os.PathLike[str] | None = None,
          now: float | None = None) -> dict[str, Any]:
    """Atomically move one pending operation to running."""
    now = time.time() if now is None else now
    root_path = state_root(root)
    with control_lock(root_path):
        record = get_operation(operation_id, root=root_path)
        if record is None:
            raise ServiceControlError("OPERATION_NOT_FOUND", "控制操作不存在", 404)
        if record.get("status") == "running":
            return record
        if record.get("status") != "pending":
            return record
        record.update(status="running", started_at=now, updated_at=now)
        _atomic_json(_operation_path(root_path, operation_id), record)
        return record


def finish(operation_id: str, *, status: str, result: dict[str, Any] | None = None,
           error_code: str | None = None, error_message: str | None = None,
           root: str | os.PathLike[str] | None = None,
           now: float | None = None) -> dict[str, Any]:
    """Write a scheduler result using a fixed, redacted field set."""
    if status not in TERMINAL:
        raise ValueError("status must be terminal")
    now = time.time() if now is None else now
    root_path = state_root(root)
    with control_lock(root_path):
        record = get_operation(operation_id, root=root_path)
        if record is None:
            raise ServiceControlError("OPERATION_NOT_FOUND", "控制操作不存在", 404)
        if record.get("status") in TERMINAL:
            return record
        safe_result = result if isinstance(result, dict) else None
        if safe_result is not None:
            safe_result = {key: safe_result[key] for key in ("container_running", "ready", "reason")
                           if key in safe_result}
        record.update(status=status, result=safe_result, error_code=error_code if isinstance(error_code, str) else None,
                      error_message=error_message if isinstance(error_message, str) else None,
                      finished_at=now, updated_at=now)
        _atomic_json(_operation_path(root_path, operation_id), record)
        return record


def latest(node_ip: str | None, service: str, *, root: str | os.PathLike[str] | None = None) -> dict[str, Any] | None:
    node_ip = normalize_node_ip(node_ip)
    if node_ip is None:
        return None
    records = [record for record in _all(root)
               if record.get("node_ip") == node_ip and record.get("service") == service]
    return records[-1] if records else None


def desired_state(node_ip: str | None, service: str, *, root: str | os.PathLike[str] | None = None) -> bool | None:
    """Return the last successfully applied admin state, if one exists."""
    normalized = normalize_node_ip(node_ip)
    if normalized is None or (normalized, service) not in SUPPORTED:
        return None
    records = [record for record in _all(root)
               if record.get("node_ip") == normalized and record.get("service") == service
               and record.get("status") == "succeeded"]
    return bool(records[-1]["desired_running"]) if records else None


def process_next(handler, *, root: str | os.PathLike[str] | None = None,
                 now: float | None = None) -> dict[str, Any] | None:
    """Run one durable request in the sole scheduler process.

    A request left in ``running`` by a daemon restart is retried: node actions
    are fixed, idempotent start/stop operations and verify the resulting state.
    """
    pending = list_pending(root=root)
    if not pending:
        return None
    record = claim(pending[0]["operation_id"], root=root, now=now)
    try:
        result = handler(record)
    except ServiceControlError as exc:
        return finish(record["operation_id"], status="failed", error_code=exc.code,
                      error_message=exc.message, root=root, now=now)
    except Exception:
        return finish(record["operation_id"], status="failed",
                      error_code="CONTROL_EXECUTION_FAILED",
                      error_message="设备操作未完成，请检查节点状态后重试", root=root, now=now)
    return finish(record["operation_id"], status="succeeded", result=result,
                  root=root, now=now)


def decorate_services(services: dict[str, dict[str, Any]], node_ip: str | None, *,
                      root: str | os.PathLike[str] | None = None,
                      now: float | None = None) -> dict[str, dict[str, Any]]:
    """Attach safe control capability/status metadata to read-only observations."""
    normalized = normalize_node_ip(node_ip)
    result = {}
    scheduler_ready = _dedicated_ready(state_root(root), time.time() if now is None else now)
    for service, observation in services.items():
        supported = ((normalized, service) in SUPPORTED and scheduler_ready
                     and observation.get("control_ready") is True)
        operation = latest(normalized, service, root=root) if supported else None
        active = bool(operation and operation.get("status") in ACTIVE)
        known = isinstance(observation.get("container_running"), bool)
        desired = None
        if supported and known:
            desired = bool(observation["container_running"])
        if operation is not None:
            desired = bool(operation.get("desired_running"))
        status = operation.get("status") if operation else ("idle" if supported and known else "unknown")
        reason = (operation.get("error_message") or operation.get("reason")
                  if operation else observation.get("reason"))
        control = {"supported": supported, "desired_running": desired,
                   "operation_id": operation.get("operation_id") if operation else None,
                   "status": status, "pending": active, "busy": active,
                   "reason": reason or ("专用设备调度器不可用" if (normalized, service) in SUPPORTED and not scheduler_ready
                                         else "节点尚未安装安全控制代理" if (normalized, service) in SUPPORTED and not supported
                                         else "该节点服务未开放手动控制" if not supported else "")}
        result[service] = {**observation, "control": control}
    return result
