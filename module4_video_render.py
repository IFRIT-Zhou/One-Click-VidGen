"""Module 4: turn the semantic timeline into an image-backed HTML presentation."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from backend.app.config import load_project_env
from backend.app.gemini_client import GeminiError, gemini_configured, generate_gemini_text, parse_json_response


PROJECT_ROOT = Path(__file__).resolve().parent
VISUAL_DIR = PROJECT_ROOT / "workspace" / "3_visual_template"
ASSETS_DIR = VISUAL_DIR / "assets"
TIMELINE_PATH = VISUAL_DIR / "fine_grained_timeline.json"
RUNNINGHUB_HOST = "https://www.runninghub.cn"
_QUEUE_RETRY_LOCK = threading.Lock()


@dataclass(frozen=True)
class PosterTask:
    macro: dict[str, Any]
    output: Path
    task_id: str | None


class RunningHubQueueFull(RuntimeError):
    """The account has reached the cloud-side active task limit (error 421)."""


class RunningHubTransientError(RuntimeError):
    """A temporary RunningHub or network failure that can be retried safely."""


class RunningHubPowerInsufficient(RuntimeError):
    """The selected RunningHub WebApp has no remaining power-value quota."""


class RunningHubAccessDenied(RuntimeError):
    """The selected API key is not allowed to call the standard model endpoint."""


class RunningHubAllAccountsPowerInsufficient(RuntimeError):
    """Every configured RunningHub account returned power-value error 414."""


class RunningHubAllAccountsAccessDenied(RuntimeError):
    """Every configured RunningHub account returned access-denied error 1014."""


class RunningHubAllAccountsBusy(RuntimeError):
    """Every configured RunningHub account currently returned queue-full error 421."""


class RunningHubAccountPool:
    """Round-robin accounts, retire 414 accounts, and temporarily skip 421 accounts."""

    def __init__(self, configs: list[dict[str, str]]) -> None:
        self._configs = configs
        self._power_exhausted: set[str] = set()
        self._access_denied: set[str] = set()
        self._queue_full: set[str] = set()
        self._next_index = 0
        self._lock = threading.Lock()

    def acquire(self) -> dict[str, str]:
        with self._lock:
            available = [
                config
                for config in self._configs
                if config["api_key"] not in self._power_exhausted
                and config["api_key"] not in self._access_denied
                and config["api_key"] not in self._queue_full
            ]
            if not available:
                usable = [
                    config
                    for config in self._configs
                    if config["api_key"] not in self._power_exhausted
                    and config["api_key"] not in self._access_denied
                ]
                if usable:
                    raise RunningHubAllAccountsBusy(
                        "所有可用 RunningHub 账号当前均处于队列满状态（421）"
                    )
                if self._access_denied:
                    raise RunningHubAllAccountsAccessDenied(
                        "所有已配置的 RunningHub 账号均返回访问拒绝（1014）"
                    )
                raise RunningHubAllAccountsPowerInsufficient(
                    "所有已配置的 RunningHub 账号均返回 power value 不足（414）"
                )
            config = available[self._next_index % len(available)]
            self._next_index = (self._next_index + 1) % len(available)
            return config

    def mark_power_exhausted(self, config: dict[str, str]) -> None:
        with self._lock:
            self._power_exhausted.add(config["api_key"])
            self._queue_full.discard(config["api_key"])

    def mark_access_denied(self, config: dict[str, str]) -> None:
        with self._lock:
            self._access_denied.add(config["api_key"])
            self._queue_full.discard(config["api_key"])

    def mark_queue_full(self, config: dict[str, str]) -> None:
        with self._lock:
            if (
                config["api_key"] not in self._power_exhausted
                and config["api_key"] not in self._access_denied
            ):
                self._queue_full.add(config["api_key"])

    def mark_available(self, config: dict[str, str]) -> None:
        with self._lock:
            self._queue_full.discard(config["api_key"])

    def acquire_waiting_account(self) -> dict[str, str]:
        """Choose any non-414 account as the account whose queue will be observed."""
        with self._lock:
            available = [
                config
                for config in self._configs
                if config["api_key"] not in self._power_exhausted
                and config["api_key"] not in self._access_denied
            ]
            if not available:
                if self._access_denied:
                    raise RunningHubAllAccountsAccessDenied(
                        "所有已配置的 RunningHub 账号均返回访问拒绝（1014）"
                    )
                raise RunningHubAllAccountsPowerInsufficient(
                    "所有已配置的 RunningHub 账号均返回 power value 不足（414）"
                )
            config = available[self._next_index % len(available)]
            self._next_index = (self._next_index + 1) % len(available)
            return config


def _load_runninghub_env_from_file() -> None:
    """Use the current .env as the source of truth for RunningHub settings."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        load_project_env()
        return

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.startswith("RUNNINGHUB_"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value

    for key in list(os.environ):
        if key.startswith("RUNNINGHUB_") and key not in values:
            os.environ.pop(key, None)
    os.environ.update(values)


def _provider_configs() -> list[dict[str, str]]:
    _load_runninghub_env_from_file()
    base_config = {
        "endpoint": os.getenv("RUNNINGHUB_ENDPOINT", "/rhart-image-g-2/text-to-image").strip(),
        "resolution": os.getenv("RUNNINGHUB_RESOLUTION", "1k").strip(),
        "ratio": os.getenv("RUNNINGHUB_TARGET_RATIO", "16:9").strip(),
    }
    raw_keys = [os.getenv("RUNNINGHUB_API_KEY", "")]
    raw_keys.extend(re.split(r"[,;\s]+", os.getenv("RUNNINGHUB_API_KEYS", "")))
    raw_keys.extend(
        value
        for name, value in sorted(os.environ.items())
        if re.fullmatch(r"RUNNINGHUB_API_KEY_?\d+", name)
    )
    api_keys: list[str] = []
    for raw_key in raw_keys:
        key = raw_key.strip()
        if key and key not in api_keys:
            api_keys.append(key)

    missing = []
    if not api_keys:
        missing.append("RUNNINGHUB_API_KEY（可追加 _2、_3 或 RUNNINGHUB_API_KEYS）")
    if not base_config["endpoint"]:
        missing.append("RUNNINGHUB_ENDPOINT")
    if missing:
        raise RuntimeError(f"模块 4 缺少配置: {', '.join(missing)}。请在 .env 中设置后重试。")
    return [
        {**base_config, "api_key": api_key, "account_label": f"账号 {index}"}
        for index, api_key in enumerate(api_keys, 1)
    ]


def _fallback_mapping(scenes: list[dict[str, Any]], group_size: int = 5) -> list[dict[str, Any]]:
    groups = [scenes[index : index + group_size] for index in range(0, len(scenes), group_size)]
    return [
        {
            "macro_scene_id": f"poster_{index:03d}",
            "includes_slides": [str(scene["slide_id"]) for scene in group],
            "image_prompt": (
                "16:9 横版知识讲解海报，现代编辑设计，信息层级清晰，"
                f"围绕主题“{group[0]['visual_summary']}”，"
                "画面留出干净字幕空间，不要水印，不要边框。"
            ),
        }
        for index, group in enumerate(groups, 1)
    ]


def _normalize_mapping(raw: Any, scenes: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    remaining = {str(scene["slide_id"]) for scene in scenes}
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            continue
        included = [str(value) for value in item.get("includes_slides", []) if str(value) in remaining]
        prompt = str(item.get("image_prompt", "")).strip()
        if not included or not prompt:
            continue
        for slide_id in included:
            remaining.discard(slide_id)
        normalized.append(
            {
                "macro_scene_id": f"poster_{index:03d}",
                "includes_slides": included,
                "image_prompt": prompt,
            }
        )
    return normalized if normalized and not remaining else None


def build_macro_mapping(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fallback = _fallback_mapping(scenes)
    if not gemini_configured():
        print("Gemini 未配置，模块 4 使用本地分组提示词。", flush=True)
        return fallback

    system_prompt = (
        "你是知识口播视频的视觉导演。只输出严格 JSON 数组，不要 Markdown。"
        "将连续的 4 至 7 个 slide 合并为一张 16:9 横版海报。"
        "每项必须有 includes_slides（slide_id 数组）和 image_prompt（中文生图提示词）。"
        "覆盖每一个 slide_id 且不重复。图像中不要生成字幕、水印或无意义文字。"
    )
    try:
        response = generate_gemini_text(
            system_prompt=system_prompt,
            user_prompt=json.dumps(scenes, ensure_ascii=False),
            temperature=0.3,
            response_mime_type="application/json",
        )
        mapping = _normalize_mapping(parse_json_response(response), scenes)
        if mapping:
            print(f"Gemini 已规划 {len(mapping)} 张海报。", flush=True)
            return mapping
        print("Gemini 返回的海报映射不完整，使用本地分组提示词。", flush=True)
    except (GeminiError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"Gemini 模块 4 规划失败，使用本地分组提示词: {exc}", flush=True)
    return fallback


def _request_json(session: requests.Session, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    response = session.request(method, url, timeout=60, **kwargs)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("图像服务返回了无效 JSON")
    return payload


def _new_session() -> requests.Session:
    """Requests sessions are local to one worker; they are not shared between threads."""
    return requests.Session()


def _worker_count(name: str, default: int, task_count: int) -> int:
    return max(1, min(task_count, _positive_env_int(name, default)))


def _positive_env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        configured = int(raw_value)
    except ValueError:
        configured = default
    return max(1, configured)


def _retry_delay_seconds(attempt: int) -> float:
    raw_value = os.getenv("RUNNINGHUB_RETRY_DELAY_SECONDS", "8").strip()
    try:
        base_delay = max(1.0, float(raw_value))
    except ValueError:
        base_delay = 8.0
    return min(60.0, base_delay * min(attempt, 4))


def _queue_poll_seconds() -> float:
    raw_value = os.getenv("RUNNINGHUB_QUEUE_POLL_SECONDS", "5").strip()
    try:
        return max(1.0, float(raw_value))
    except ValueError:
        return 5.0


def _queue_probe_seconds() -> float:
    raw_value = os.getenv("RUNNINGHUB_QUEUE_PROBE_SECONDS", "30").strip()
    try:
        return max(_queue_poll_seconds(), float(raw_value))
    except ValueError:
        return 30.0


def _account_active_task_count(config: dict[str, str]) -> int:
    session = _new_session()
    try:
        payload = _request_json(
            session,
            "POST",
            f"{RUNNINGHUB_HOST}/uc/openapi/accountStatus",
            headers={"Authorization": f"Bearer {config['api_key']}"},
            json={"apikey": config["api_key"]},
        )
    except requests.RequestException as exc:
        raise RunningHubTransientError(
            f"RunningHub 队列状态查询网络异常: {type(exc).__name__}"
        ) from exc
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise RunningHubTransientError("RunningHub 队列状态查询失败")
    try:
        return max(0, int(payload["data"].get("currentTaskCounts", 0)))
    except (TypeError, ValueError) as exc:
        raise RunningHubTransientError("RunningHub 返回了无效的活跃任务数") from exc


def _wait_for_queue_slot(poster_id: str, config: dict[str, str]) -> None:
    """Wait for a real RunningHub queue change after error 421."""
    max_wait = _positive_env_int("RUNNINGHUB_QUEUE_MAX_WAIT_SECONDS", 1800)
    poll_seconds = _queue_poll_seconds()
    probe_seconds = _queue_probe_seconds()
    deadline = time.monotonic() + max_wait
    blocked_task_count = _account_active_task_count(config)
    next_probe_at = time.monotonic() + probe_seconds
    print(
        f"{poster_id} 收到 421，已进入本地队列（当前 RunningHub 活跃任务 {blocked_task_count}）。",
        flush=True,
    )
    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
        active_tasks = _account_active_task_count(config)
        if active_tasks < blocked_task_count:
            print(
                f"{poster_id} 检测到 RunningHub 活跃任务下降（{blocked_task_count} -> {active_tasks}），准备重新提交。",
                flush=True,
            )
            return
        if time.monotonic() >= next_probe_at:
            print(f"{poster_id} 队列状态未变化，执行一次受控重新探测。", flush=True)
            return
        print(
            f"{poster_id} 仍在队列等待（RunningHub 活跃任务 {active_tasks}）。",
            flush=True,
        )
    raise RuntimeError(f"{poster_id} 等待 RunningHub 队列空位超时（{max_wait}s）")


def _runninghub_error_code(payload: dict[str, Any], status_code: int | None = None) -> int | None:
    raw_code = payload.get("code", payload.get("errorCode", status_code))
    try:
        return int(raw_code)
    except (TypeError, ValueError):
        return status_code


def _runninghub_error_message(payload: dict[str, Any]) -> str:
    message = (
        payload.get("msg")
        or payload.get("message")
        or payload.get("errorMessage")
        or payload.get("error")
        or ""
    )
    return str(message).strip()


def _find_first_key(value: Any, key_names: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in key_names and item:
                return item
        for item in value.values():
            found = _find_first_key(item, key_names)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_key(item, key_names)
            if found:
                return found
    return None


def _find_image_url(value: Any) -> str | None:
    if isinstance(value, str):
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return None
    if isinstance(value, dict):
        for key in ("fileUrl", "fileURL", "imageUrl", "imageURL", "url", "downloadUrl", "downloadURL"):
            found = _find_image_url(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _find_image_url(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_image_url(item)
            if found:
                return found
    return None


def _runninghub_generate_url(config: dict[str, str]) -> str:
    endpoint = config["endpoint"]
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return f"{RUNNINGHUB_HOST}/openapi/v2/{endpoint.lstrip('/')}"


def _runninghub_headers(config: dict[str, str]) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['api_key']}",
    }


def _handle_runninghub_submit_error(payload: dict[str, Any], status_code: int | None = None) -> None:
    code = _runninghub_error_code(payload, status_code)
    message = _runninghub_error_message(payload)
    if code == 421:
        raise RunningHubQueueFull("RunningHub 队列已满（421）")
    if code == 414:
        raise RunningHubPowerInsufficient(
            "当前 RunningHub 工作流的 power value 不足（414）。"
            "请为该工作流充值/补充算力值后再试。"
        )
    if code == 1014:
        detail = f" 原因: {message}" if message else ""
        raise RunningHubAccessDenied(
            "RunningHub 标准模型 API 只允许企业级-共享 API Key 调用。"
            "当前配置的 API Key 被拒绝（1014）。" + detail
        )
    if code in {408, 409, 429, 500, 502, 503, 504, 1005, 1010, 1011, 1012}:
        detail = f"，原因: {message}" if message else ""
        raise RunningHubTransientError(f"RunningHub 临时不可用，错误码: {code}{detail}")
    detail = f"，原因: {message}" if message else ""
    raise RuntimeError(f"RunningHub 提交失败，错误码: {code}{detail}")


def _download_image(session: requests.Session, poster_id: str, file_url: str, output: Path) -> Path | None:
    try:
        response = session.get(str(file_url), timeout=120)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"{poster_id} 下载网络异常，稍后重试: {type(exc).__name__}", flush=True)
        return None
    output.write_bytes(response.content)
    if output.stat().st_size == 0:
        raise RuntimeError(f"{poster_id} 下载到空图像")
    print(f"海报已生成: {output.name}", flush=True)
    return output


def _submit_poster_request(
    macro: dict[str, Any], config: dict[str, str], session: requests.Session
) -> str:
    payload = {
        "prompt": macro["image_prompt"],
        "aspectRatio": config["ratio"],
        "resolution": config["resolution"],
    }
    response = session.post(
        _runninghub_generate_url(config),
        headers=_runninghub_headers(config),
        json=payload,
        timeout=60,
    )
    try:
        submitted = response.json()
    except ValueError as exc:
        response.raise_for_status()
        raise RuntimeError("RunningHub 生成图接口返回了无效 JSON") from exc
    if not isinstance(submitted, dict):
        raise RuntimeError("RunningHub 生成图接口返回了无效 JSON")
    if not response.ok:
        _handle_runninghub_submit_error(submitted, response.status_code)
    submitted_data = submitted.get("data") if isinstance(submitted.get("data"), dict) else {}
    task_id = str(
        submitted.get("taskId")
        or submitted_data.get("taskId", "")
        or _find_first_key(submitted, {"taskId", "taskID", "id"})
        or ""
    )
    if not task_id:
        _handle_runninghub_submit_error(submitted, response.status_code)
        raise RuntimeError("RunningHub 生成图接口未返回任务 ID")
    return task_id


def _poster_output_path(macro: dict[str, Any]) -> Path:
    poster_id = macro["macro_scene_id"]
    job_id = os.getenv("VOICE_OVER_VIDEO_JOB_ID", "").strip()
    suffix_source = f"{job_id}\0{macro.get('image_prompt', '')}"
    suffix = hashlib.sha1(suffix_source.encode("utf-8")).hexdigest()[:10]
    return ASSETS_DIR / f"{poster_id}_{suffix}.jpg"


def _submit_poster(macro: dict[str, Any], config: dict[str, str]) -> PosterTask:
    poster_id = macro["macro_scene_id"]
    output = _poster_output_path(macro)

    session = _new_session()
    try:
        task_id = _submit_poster_request(macro, config, session)
    except requests.RequestException as exc:
        raise RunningHubTransientError(
            f"{poster_id} 提交图像任务网络异常: {type(exc).__name__}"
        ) from exc

    print(f"海报任务已提交: {poster_id} ({task_id})", flush=True)
    return PosterTask(macro=macro, output=output, task_id=task_id)


def _wait_for_poster(task: PosterTask, config: dict[str, str]) -> Path:
    if task.task_id is None:
        return task.output

    poster_id = task.macro["macro_scene_id"]
    session = _new_session()
    print(f"等待海报结果: {poster_id}", flush=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        try:
            result = _request_json(
                session,
                "POST",
                f"{RUNNINGHUB_HOST}/openapi/v2/query",
                headers=_runninghub_headers(config),
                json={"taskId": task.task_id},
            )
        except requests.RequestException as exc:
            print(f"{poster_id} 查询网络异常，稍后重试: {type(exc).__name__}", flush=True)
            time.sleep(5)
            continue
        status = str(_find_first_key(result, {"status", "state", "taskStatus"}) or "").upper()
        file_url = _find_image_url(result)
        if status in {"SUCCESS", "SUCCEEDED", "COMPLETED", "COMPLETE", "FINISHED"} or file_url:
            if not file_url:
                raise RuntimeError(f"{poster_id} 未返回图像下载地址")
            downloaded = _download_image(session, poster_id, str(file_url), task.output)
            if downloaded:
                return downloaded
        elif status in {"RUNNING", "QUEUED", "PENDING", ""}:
            pass
        else:
            message = _runninghub_error_message(result)
            raise RuntimeError(f"{poster_id} 的云端图像工作流执行失败: {message or status}")
        time.sleep(5)
    raise RuntimeError(f"{poster_id} 图像生成超时")


def _render_poster_with_retry(
    macro: dict[str, Any], account_pool: RunningHubAccountPool
) -> Path:
    """Try another account after 421; queue only when every configured account is busy."""
    poster_id = macro["macro_scene_id"]
    max_attempts = _positive_env_int("RUNNINGHUB_SUBMIT_MAX_ATTEMPTS", 90)
    queued = False
    try:
        config = account_pool.acquire()
    except RunningHubAllAccountsBusy:
        config = account_pool.acquire_waiting_account()
        queued = True
    for attempt in range(1, max_attempts + 1):
        try:
            if queued:
                # Only one queued worker checks a newly-free slot and resubmits at a time.
                with _QUEUE_RETRY_LOCK:
                    _wait_for_queue_slot(poster_id, config)
                    task = _submit_poster(macro, config)
            else:
                task = _submit_poster(macro, config)
            result = _wait_for_poster(task, config)
            account_pool.mark_available(config)
            return result
        except RunningHubQueueFull:
            account_pool.mark_queue_full(config)
            try:
                next_config = account_pool.acquire()
            except RunningHubAllAccountsBusy:
                config = account_pool.acquire_waiting_account()
                queued = True
            else:
                print(
                    f"{poster_id} 的 {config['account_label']} 返回 421，"
                    f"切换到空闲的 {next_config['account_label']}。",
                    flush=True,
                )
                config = next_config
                queued = False
            continue
        except RunningHubPowerInsufficient:
            account_pool.mark_power_exhausted(config)
            print(
                f"{poster_id} 的 {config['account_label']} 返回 414，切换到下一个账号。",
                flush=True,
            )
            try:
                config = account_pool.acquire()
                queued = False
            except RunningHubAllAccountsBusy:
                config = account_pool.acquire_waiting_account()
                queued = True
            continue
        except RunningHubAccessDenied:
            account_pool.mark_access_denied(config)
            print(
                f"{poster_id} 的 {config['account_label']} 返回 1014，切换到下一个账号。",
                flush=True,
            )
            try:
                config = account_pool.acquire()
                queued = False
            except RunningHubAllAccountsBusy:
                config = account_pool.acquire_waiting_account()
                queued = True
            continue
        except RunningHubTransientError as exc:
            if attempt == max_attempts:
                raise RuntimeError(f"{poster_id} 重试 {max_attempts} 次后仍未提交: {exc}") from exc
            delay = _retry_delay_seconds(attempt)
            print(
                f"{poster_id} 遇到临时网络或服务异常，{delay:.0f}s 后重试 "
                f"({attempt}/{max_attempts}): {exc}",
                flush=True,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")


def render_posters_concurrently(
    mapping: list[dict[str, Any]], provider_configs: list[dict[str, str]]
) -> list[Path]:
    """Render with bounded local concurrency; RunningHub 421 responses enter the queue."""
    if not mapping:
        return []

    active_workers = _worker_count("RUNNINGHUB_ACTIVE_TASK_CONCURRENCY", 1, len(mapping))
    account_pool = RunningHubAccountPool(provider_configs)
    print(
        f"提交 {len(mapping)} 张海报任务（本地工作并发 {active_workers}，"
        f"{len(provider_configs)} 个账号可轮换，421 优先切账号后再入队）...",
        flush=True,
    )

    completed: dict[int, Path] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=active_workers, thread_name_prefix="runninghub-task") as executor:
        futures = {
            executor.submit(_render_poster_with_retry, macro, account_pool): index
            for index, macro in enumerate(mapping)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                completed[index] = future.result()
            except RunningHubAllAccountsPowerInsufficient as exc:
                for pending in futures:
                    pending.cancel()
                raise RuntimeError(
                    "所有已配置的 RunningHub 账号都返回 power value 不足（414），"
                    "已停止后续海报提交。请补充任一账号的工作流算力值后重新生成。"
                ) from exc
            except RunningHubAccessDenied as exc:
                for pending in futures:
                    pending.cancel()
                raise RuntimeError(f"RunningHub 标准模型接口拒绝访问（1014）：{exc}") from exc
            except RunningHubAllAccountsAccessDenied as exc:
                for pending in futures:
                    pending.cancel()
                raise RuntimeError(
                    "所有已配置的 RunningHub 账号都返回访问拒绝（1014）。"
                    "已停止后续海报提交，请确认这些 key 在 RunningHub 后台属于企业级-共享 API Key。"
                ) from exc
            except Exception as exc:
                failures.append(f"{mapping[index]['macro_scene_id']}: {exc}")
    if failures:
        raise RuntimeError("海报任务生成失败：" + "；".join(failures))
    return [completed[index] for index in range(len(mapping))]


def write_html(scenes: list[dict[str, Any]], poster_timeline: list[dict[str, Any]]) -> Path:
    if not poster_timeline:
        raise RuntimeError("没有可用海报，拒绝生成空白视频页面")
    total_duration = max(float(item["end"]) for item in scenes)
    poster_divs = "\n".join(
        f'<div class="poster-item" id="poster-{index}" style="background-image:url(\'{html.escape(item["url"], quote=True)}\')"></div>'
        for index, item in enumerate(poster_timeline)
    )
    poster_data = json.dumps(poster_timeline, ensure_ascii=False)
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=1920, initial-scale=1.0">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}} body{{width:1920px;height:1080px;overflow:hidden;background:#050a12;font-family:'PingFang SC','Microsoft YaHei',sans-serif}} #stage{{position:relative;width:100%;height:100%}} .poster-item{{position:absolute;inset:0;background-size:cover;background-position:center;opacity:0}} #subtitle-overlay{{position:absolute;z-index:10;bottom:40px;width:100%;text-align:center;pointer-events:none}} .subtitle-inner{{display:inline-block;max-width:1600px;padding:16px 48px;border-radius:12px;background:rgba(7,24,52,.84);color:#fff;font-size:40px;font-weight:600;line-height:1.4;letter-spacing:0}} #main-audio{{display:none}}
</style></head><body>
<audio id="main-audio" src="./2_audio_srt/final_output.wav" data-start="0" autoplay></audio>
<div id="stage" data-composition-id="main" data-width="1920" data-height="1080" data-duration="{total_duration}" data-start="0">{poster_divs}<div id="subtitle-overlay"><div class="subtitle-inner" id="subtitle-text"></div></div></div>
<script>
window.base64Subtitle = "";
const posterTimeline = {poster_data}; let subtitleData=[];
function parseTime(value){{const p=value.split(':');const s=p[2].split(',');return +p[0]*3600 + +p[1]*60 + +s[0] + +s[1]/1000;}}
try{{if(window.base64Subtitle){{const raw=decodeURIComponent(escape(atob(window.base64Subtitle)));for(const block of raw.trim().split(/\\n\\s*\\n/)){{const lines=block.split('\\n');const match=lines[1]?.match(/([\\d:,]+)\\s*-->\\s*([\\d:,]+)/);if(match)subtitleData.push({{start:parseTime(match[1]),end:parseTime(match[2]),text:lines.slice(2).join(' ').trim()}})}}}}}}catch(error){{console.error(error)}}
window.__timelines=window.__timelines||{{}}; window.__timelines.main={{duration:{total_duration},seek(t){{posterTimeline.forEach((poster,index)=>{{const el=document.getElementById('poster-'+index);const next=posterTimeline[index+1];if(t>=poster.start && t<poster.end)el.style.opacity=index===0?'1':String(Math.min((t-poster.start)/.8,1));else if(next && t>=next.start && t<next.start+.8)el.style.opacity='1';else el.style.opacity='0'}});const active=subtitleData.find(item=>t>=item.start&&t<=item.end);document.getElementById('subtitle-text').textContent=active?.text||''}},play(){{}},pause(){{}}}};
</script></body></html>"""
    html_path = VISUAL_DIR / "index.html"
    html_path.write_text(page, encoding="utf-8")
    return html_path


def run_online_poster_engine() -> None:
    print("[模块 4] 在线海报与页面生成启动", flush=True)
    if not TIMELINE_PATH.is_file():
        raise FileNotFoundError(f"找不到模块 3 剧本: {TIMELINE_PATH}")
    scenes = json.loads(TIMELINE_PATH.read_text(encoding="utf-8"))
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("模块 3 剧本必须是非空数组")
    required_fields = {"slide_id", "start", "end", "visual_summary"}
    if any(not isinstance(scene, dict) or not required_fields.issubset(scene) for scene in scenes):
        raise ValueError("模块 3 剧本缺少模块 4 所需字段")

    provider_configs = _provider_configs()
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    mapping = build_macro_mapping(scenes)
    scenes_by_id = {str(scene["slide_id"]): scene for scene in scenes}
    assets = render_posters_concurrently(mapping, provider_configs)
    poster_timeline: list[dict[str, Any]] = []
    for macro, asset in zip(mapping, assets, strict=True):
        included = [scenes_by_id[slide_id] for slide_id in macro["includes_slides"]]
        poster_timeline.append(
            {
                "start": min(float(scene["start"]) for scene in included),
                "end": max(float(scene["end"]) for scene in included),
                "url": f"./assets/{asset.name}",
            }
        )
    html_path = write_html(scenes, poster_timeline)
    print(f"模块 4 页面已写入: {html_path}", flush=True)


if __name__ == "__main__":
    try:
        run_online_poster_engine()
    except Exception as exc:
        print(f"模块 4 失败: {exc}", file=sys.stderr, flush=True)
        raise
