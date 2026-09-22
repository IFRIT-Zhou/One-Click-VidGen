"""Provider-neutral dynamic-video execution with resumable paid submissions.

This module does not decide shot content. It accepts a reviewed prompt and an
ordered image list where image 1 is the core storyboard reference.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests


DEFAULT_BASE_URL = "https://www.runninghub.ai"
SUBMIT_PATH = "/openapi/v2/rhart-video/sparkvideo-2.0-fast/multimodal-video"
QUERY_PATH = "/openapi/v2/query"
UPLOAD_PATH = "/openapi/v2/media/upload/binary"
RUNNING = {"RUNNING", "QUEUED", "PENDING", "PROCESSING", "SUBMITTED", ""}
SUCCESS = {"SUCCESS", "SUCCEEDED", "COMPLETED", "COMPLETE", "FINISHED"}
FAILURE = {"FAILED", "FAILURE", "ERROR", "CANCELLED", "CANCELED", "REJECTED",
           "BLOCKED", "ABORTED", "TERMINATED", "TIMEOUT", "TIMED_OUT", "EXPIRED"}


class DynamicVideoError(RuntimeError):
    pass


class DynamicVideoTaskFailed(DynamicVideoError):
    """The provider confirmed a terminal failure; a user may choose to retry."""

    def __init__(self, message: str, *, status: str = "FAILED") -> None:
        super().__init__(message)
        self.status = status if status in FAILURE else "FAILED"


class DynamicVideoTaskUnknown(DynamicVideoError):
    """Submission outcome is unknown; never create another paid task automatically."""


class DynamicVideoStopped(DynamicVideoError):
    """Only local execution stopped; an already submitted cloud task may continue."""


def _stop_if_requested(should_stop: Callable[[], bool] | None) -> None:
    if should_stop and should_stop():
        raise DynamicVideoStopped("已停止本地视频处理；已提交的云端任务保留身份，可能仍在执行")


def validate_video_file(path: Path) -> dict[str, Any]:
    """Check complete ISO-BMFF boxes and a video track, without requiring FFmpeg.

    This is a container/integrity check, not a guarantee of codec decodability.
    """
    size = path.stat().st_size if path.is_file() else 0
    if size < 32:
        raise DynamicVideoTaskUnknown("视频下载结果为空或不完整，保留云端任务身份")
    boxes: set[bytes] = set()
    video_track = False
    with path.open("rb") as handle:
        def walk(start: int, end: int, depth: int = 0) -> None:
            nonlocal video_track
            cursor = start
            while cursor < end:
                if end - cursor < 8:
                    raise DynamicVideoTaskUnknown("视频 MP4 容器被截断，保留任务身份等待重新下载")
                handle.seek(cursor)
                length, kind = struct.unpack(">I4s", handle.read(8))
                header = 8
                if length == 1:
                    if end - cursor < 16:
                        raise DynamicVideoTaskUnknown("视频 MP4 扩展头不完整")
                    length = struct.unpack(">Q", handle.read(8))[0]
                    header = 16
                elif length == 0:
                    length = end - cursor
                if length < header or cursor + length > end:
                    raise DynamicVideoTaskUnknown("视频 MP4 长度校验失败，禁止将不完整下载当作成功")
                if depth == 0:
                    boxes.add(kind)
                    if kind == b"mdat" and length <= header:
                        raise DynamicVideoTaskUnknown("视频 MP4 媒体数据为空")
                if kind in {b"moov", b"trak", b"mdia"} and depth < 4:
                    walk(cursor + header, cursor + length, depth + 1)
                elif kind == b"hdlr" and depth >= 3 and length >= header + 12:
                    handle.seek(cursor + header + 8)
                    video_track = video_track or handle.read(4) == b"vide"
                cursor += length
        walk(0, size)
    if not {b"ftyp", b"moov", b"mdat"}.issubset(boxes) or not video_track:
        raise DynamicVideoTaskUnknown("下载内容不是完整的 MP4 视频，保留任务身份等待重新下载")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": size, "sha256": digest.hexdigest()}


@dataclass(frozen=True)
class VideoGenerationRequest:
    prompt: str
    image_paths: tuple[Path, ...]
    output_path: Path
    duration: int
    ratio: str
    resolution: str = "720p"
    seed: int = -1
    generate_audio: bool = False
    real_person_mode: bool = False


def validate_request(request: VideoGenerationRequest) -> None:
    if not request.prompt.strip() or len(request.prompt) > 20480:
        raise ValueError("视频提示词不能为空且不得超过 20480 字符")
    if request.duration not in range(4, 16):
        raise ValueError("视频生成时长只支持 4～15 秒")
    if request.ratio not in {"16:9", "9:16", "4:3", "3:4", "1:1", "21:9"}:
        raise ValueError("视频比例不受支持")
    if request.resolution not in {"480p", "720p"}:
        raise ValueError("首版只允许模型原生的 480p 或 720p")
    if not 1 <= len(request.image_paths) <= 9:
        raise ValueError("视频必须包含核心分镜图，图片总数为 1～9 张")
    for path in request.image_paths:
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"视频参考图片不存在或为空：{path}")
        if path.stat().st_size > 30 * 1024 * 1024:
            raise ValueError("单张视频参考图片不得超过 30 MB")


def request_fingerprint(request: VideoGenerationRequest) -> str:
    validate_request(request)
    digest = hashlib.sha256()
    contract = {"prompt": request.prompt.strip(), "duration": request.duration,
                "ratio": request.ratio, "resolution": request.resolution,
                "seed": request.seed, "generate_audio": request.generate_audio,
                "real_person_mode": request.real_person_mode}
    digest.update(json.dumps(contract, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    for path in request.image_paths:
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _find_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, dict):
        for key in ("fileUrl", "fileURL", "file_url", "videoUrl", "videoURL", "video_url",
                    "downloadUrl", "downloadURL", "download_url", "url"):
            found = _find_url(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _find_url(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_url(item)
            if found:
                return found
    return None


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class RunningHubVideoProvider:
    def __init__(self, api_key: str, *, base_url: str | None = None,
                 submit_path: str | None = None,
                 query_path: str | None = None, upload_path: str | None = None,
                 session: requests.Session | None = None) -> None:
        if not api_key.strip():
            raise ValueError("视频 API Key 不能为空")
        self.api_key = api_key.strip()
        self.base_url = (base_url or os.getenv("RUNNINGHUB_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.submit_path = submit_path or SUBMIT_PATH
        self.query_path = query_path or QUERY_PATH
        self.upload_path = upload_path or UPLOAD_PATH
        for label, value in (("提交", self.submit_path), ("查询", self.query_path), ("上传", self.upload_path)):
            if not value.startswith("/") or value.startswith("//") or "://" in value:
                raise ValueError(f"视频{label}路径必须是当前 API 站点的相对路径")
        self.session = session or requests.Session()

    @property
    def identity(self) -> dict[str, str]:
        return {"base_url": self.base_url, "submit_path": self.submit_path,
                "query_path": self.query_path, "upload_path": self.upload_path,
                "account_fingerprint": hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()}

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def upload_image(self, path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
        }.get(path.suffix.lower(), "application/octet-stream")
        content = path.read_bytes()
        try:
            response = self.session.post(self.url(self.upload_path),
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (path.name, content, mime)}, timeout=120)
            try:
                payload = response.json()
                url = _find_url(payload) if response.ok else None
            finally:
                response.close()
            if url:
                return url
        except (requests.RequestException, ValueError):
            pass
        # The standard multimodal endpoint accepts a data URI. This also avoids
        # silently dropping a reference when upload succeeds without a URL.
        return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"

    def submit(self, request: VideoGenerationRequest, *,
               should_stop: Callable[[], bool] | None = None,
               before_submit: Callable[[], None] | None = None) -> dict[str, Any]:
        validate_request(request)
        image_urls = []
        for path in request.image_paths:
            _stop_if_requested(should_stop)
            image_urls.append(self.upload_image(path))
        payload = {"prompt": request.prompt.strip(), "resolution": request.resolution,
                   "duration": str(request.duration), "imageUrls": image_urls,
                   "videoUrls": [], "audioUrls": [], "generateAudio": request.generate_audio,
                   "ratio": request.ratio, "realPersonMode": request.real_person_mode,
                   "returnLastFrame": False, "seed": request.seed}
        _stop_if_requested(should_stop)
        if before_submit:
            before_submit()
        response = self.session.post(self.url(self.submit_path), headers=self.headers, json=payload, timeout=120)
        try:
            body = response.json()
        except ValueError as exc:
            raise DynamicVideoTaskUnknown(f"视频提交响应不是 JSON（HTTP {response.status_code}）") from exc
        finally:
            response.close()
        task_id = str(body.get("taskId") or "") if isinstance(body, dict) else ""
        # A task ID is more important than an unusual HTTP code: persist it and
        # query that identity instead of risking another paid POST.
        if not task_id:
            body = body if isinstance(body, dict) else {}
            status = str(body.get("status") or "").upper()
            code = body.get("errorCode", body.get("code"))
            message = str(body.get("errorMessage") or body.get("message") or "未返回任务身份")
            message = message.replace(self.api_key, "[redacted]")[:1000]
            rejected = (response.status_code < 500 and response.status_code not in {408, 425, 429} and (
                status in FAILURE or
                (code not in (None, "", 0, "0", 200, "200") and message != "未返回任务身份") or
                (400 <= response.status_code < 500 and response.status_code not in {408, 425, 429})))
            if rejected:
                raise DynamicVideoTaskFailed(f"视频提交被明确拒绝（HTTP {response.status_code}）：{message}",
                                             status=status or "REJECTED")
            raise DynamicVideoTaskUnknown(f"视频提交结果无法确认（HTTP {response.status_code}）：{message}")
        return {"task_id": task_id, "status": str(body.get("status") or "SUBMITTED").upper(),
                "submitted_at": time.time()}

    def query(self, task_id: str) -> dict[str, Any]:
        response = self.session.post(self.url(self.query_path), headers=self.headers,
                                     json={"taskId": task_id}, timeout=60)
        try:
            body = response.json()
        except ValueError as exc:
            raise DynamicVideoTaskUnknown(f"任务 {task_id} 查询响应不是 JSON") from exc
        finally:
            response.close()
        if not response.ok or not isinstance(body, dict):
            raise DynamicVideoTaskUnknown(f"任务 {task_id} 查询失败（HTTP {response.status_code}）")
        status = str(body.get("status") or "").upper()
        return {"status": status, "result_url": _find_url(body.get("results")), "raw": body}

    def download(self, url: str, output: Path, *,
                 should_stop: Callable[[], bool] | None = None) -> Path:
        _stop_if_requested(should_stop)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".part")
        with self.session.get(url, stream=True, timeout=300) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(1024 * 1024):
                    _stop_if_requested(should_stop)
                    if chunk:
                        handle.write(chunk)
        validate_video_file(temporary)
        _stop_if_requested(should_stop)
        temporary.replace(output)
        return output

    def run(self, request: VideoGenerationRequest, state_path: Path, *,
            progress: Callable[[str], None] | None = None, poll_seconds: float = 5,
            timeout_seconds: float = 1800,
            should_stop: Callable[[], bool] | None = None) -> Path:
        fingerprint = request_fingerprint(request)
        notify = progress or (lambda _message: None)
        try:
            _stop_if_requested(should_stop)
        except DynamicVideoStopped:
            # The caller records that execution began before entering run().
            # Give an immediate, pre-network cancellation an explicit safe
            # identity, so it is distinguishable from a lost paid-task file.
            # Exclusive creation must never replace even a corrupt old state.
            state_path.parent.mkdir(parents=True, exist_ok=True)
            paused = {"status": "PAUSED_BEFORE_SUBMIT", "fingerprint": fingerprint,
                      "provider": self.identity, "paid_submission_started": False,
                      "output": str(request.output_path.resolve()), "local_stopped_at": time.time()}
            try:
                with state_path.open("x", encoding="utf-8") as handle:
                    json.dump(paused, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                pass
            raise
        state = None
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    raise ValueError("not object")
            except (ValueError, OSError) as exc:
                raise DynamicVideoTaskUnknown("原视频任务状态损坏，禁止自动重新提交；请保留状态文件联系支持") from exc
            if state.get("fingerprint") != fingerprint:
                raise ValueError("已有视频任务属于另一组参数；为避免重复扣费，不能覆盖任务身份")
            if state.get("provider") and state["provider"] != self.identity:
                raise DynamicVideoTaskUnknown("原视频任务的 API 站点、模型接口或账号已变化；请恢复原配置后继续查询，禁止重新提交")
            if state.get("terminal") and state.get("status") in FAILURE:
                raise DynamicVideoTaskFailed(str(state.get("error") or "原视频任务已确认失败；需要用户明确重试"),
                                             status=state["status"])
            task_id = str(state.get("task_id") or "")
            safe_paused = state.get("status") == "PAUSED_BEFORE_SUBMIT" and state.get("paid_submission_started") is False
            if not task_id and not safe_paused:
                raise DynamicVideoTaskUnknown("任务状态缺少 task_id，禁止自动重新提交")
            if state.get("status") == "DOWNLOADED" and request.output_path.is_file():
                try:
                    info = validate_video_file(request.output_path)
                    if ((not state.get("sha256") or state["sha256"] == info["sha256"]) and
                            (not state.get("bytes") or state["bytes"] == info["bytes"])):
                        state.update(info)
                        _atomic_json(state_path, state)
                        return request.output_path
                except (DynamicVideoTaskUnknown, OSError):
                    pass
                notify("本地视频完整性校验未通过，将复用原任务重新下载")
            if not state.get("provider"):
                raise DynamicVideoTaskUnknown("旧视频任务缺少 API 账号来源记录，仅可复用已验证的本地视频；禁止猜测账号查询或重新提交")
            if task_id:
                notify(f"继续查询原视频任务 {task_id}")
        if state is None or not state.get("task_id"):
            # Persist intent before the paid POST. If the provider accepts the
            # task but the response is lost, a restart must stop here instead
            # of guessing that submission failed and charging a second time.
            state = {"status": "SUBMITTING", "fingerprint": fingerprint,
                     "provider": self.identity, "paid_submission_started": False,
                     "output": str(request.output_path.resolve()), "submit_started_at": time.time()}
            _atomic_json(state_path, state)
            def before_submit() -> None:
                _stop_if_requested(should_stop)
                state.update(paid_submission_started=True)
                _atomic_json(state_path, state)
            try:
                submitted = self.submit(request, should_stop=should_stop, before_submit=before_submit)
            except DynamicVideoStopped:
                state.update(status="PAUSED_BEFORE_SUBMIT", local_stopped_at=time.time())
                _atomic_json(state_path, state)
                raise
            except DynamicVideoTaskFailed as exc:
                state.update(status=exc.status, terminal=True, error=str(exc), submit_response_lost=False)
                _atomic_json(state_path, state)
                raise
            except Exception as exc:
                state.update(status="UNKNOWN", last_error=str(exc).replace(self.api_key, "[redacted]"),
                             submit_response_lost=True)
                _atomic_json(state_path, state)
                raise DynamicVideoTaskUnknown(
                    "付费提交结果未能确认，已冻结此镜头任务；禁止自动再次提交") from exc
            state.update(submitted)
            _atomic_json(state_path, state)
            task_id = state["task_id"]
            notify(f"视频任务已提交并保存身份：{task_id}")
        deadline = time.monotonic() + timeout_seconds
        next_notice = 0.0
        while time.monotonic() < deadline:
            _stop_if_requested(should_stop)
            try:
                result = self.query(task_id)
            except (requests.RequestException, DynamicVideoTaskUnknown) as exc:
                state.update(status="UNKNOWN", last_error=str(exc), last_query_at=time.time())
                _atomic_json(state_path, state)
                raise DynamicVideoTaskUnknown(
                    f"任务 {task_id} 状态暂时无法确认；已保存身份，禁止自动重新提交") from exc
            status, result_url = result["status"], result["result_url"]
            state.update(status=status or "UNKNOWN", last_query_at=time.time())
            _atomic_json(state_path, state)
            if status in FAILURE:
                message = result["raw"].get("errorMessage") or result["raw"].get("message") or status
                message = str(message).replace(self.api_key, "[redacted]")[:1000]
                state.update(status=status, terminal=True, error=str(message))
                _atomic_json(state_path, state)
                raise DynamicVideoTaskFailed(f"任务 {task_id} 生成失败：{message}", status=status)
            if status in SUCCESS:
                if not result_url:
                    raise DynamicVideoTaskUnknown(f"任务 {task_id} 已完成但未返回下载地址")
                self.download(result_url, request.output_path, should_stop=should_stop)
                state.update(status="DOWNLOADED", completed_at=time.time(), **validate_video_file(request.output_path))
                _atomic_json(state_path, state)
                return request.output_path
            if status not in RUNNING:
                state.update(status="UNKNOWN", provider_status=status)
                _atomic_json(state_path, state)
            if time.monotonic() >= next_notice:
                notify(f"视频任务 {task_id} 状态：{status or 'UNKNOWN'}")
                next_notice = time.monotonic() + 30
            wait_until = time.monotonic() + max(0, poll_seconds)
            while time.monotonic() < wait_until:
                _stop_if_requested(should_stop)
                time.sleep(min(0.2, max(0, wait_until - time.monotonic())))
        state.update(status="UNKNOWN", last_error="poll timeout", last_query_at=time.time())
        _atomic_json(state_path, state)
        raise DynamicVideoTaskUnknown(f"任务 {task_id} 查询超时；已保存身份，禁止自动重新提交")
