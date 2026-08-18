"""Local cloud-api simulator used before the production cluster is available.

This service intentionally implements the same HTTP contract consumed by
``backend.app.cloud_client.CloudClient``.  It stores everything in memory and
must never be used as a production account or billing service.
"""

from __future__ import annotations

import io
import math
import secrets
import struct
import subprocess
import tempfile
import threading
import time
import wave
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response


app = FastAPI(title="One-Click VidGen Mock Cloud API", version="1.0.0-mock")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
FFMPEG = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
DEFAULT_VOICE_SAMPLES = {
    "mock_voice_sample_a": PROJECT_ROOT / "tools" / "IndexTTS25" / "examples" / "voice_01.wav",
    "mock_voice_sample_b": PROJECT_ROOT / "tools" / "IndexTTS25" / "examples" / "voice_02.wav",
}


class MockCloudError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


@app.exception_handler(MockCloudError)
async def mock_error_handler(request: Request, exc: MockCloudError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "request_id": f"mock_{secrets.token_hex(5)}",
            "details": exc.details or {},
        },
    )


@dataclass
class MockState:
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    access_tokens: dict[str, str] = field(default_factory=dict)
    refresh_tokens: dict[str, str] = field(default_factory=dict)
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    image_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    model_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    image_assets: dict[str, bytes] = field(default_factory=dict)
    idempotency: dict[tuple[str, str], str] = field(default_factory=dict)
    voices: dict[str, dict[str, Any]] = field(default_factory=dict)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)


state = MockState()


def reset_mock_state() -> None:
    with state.lock:
        state.users.clear()
        state.access_tokens.clear()
        state.refresh_tokens.clear()
        state.jobs.clear()
        state.image_jobs.clear()
        state.model_jobs.clear()
        state.image_assets.clear()
        state.idempotency.clear()
        state.voices.clear()
        state.ledger.clear()
        for email, name, credits, scenario in (
            ("demo@example.com", "演示用户", 5000, "normal"),
            ("low@example.com", "余额不足测试", 0, "normal"),
            ("fail@example.com", "失败退款测试", 5000, "failure"),
            ("slow@example.com", "慢速取消测试", 5000, "slow"),
        ):
            state.users[email] = {
                "id": f"usr_{len(state.users) + 1:03d}",
                "email": email,
                "name": name,
                "password": "demo12345",
                "credits": credits,
                "scenario": scenario,
                "max_concurrent_jobs": 2,
            }
        for voice_id, display_name, frequency in (
            ("mock_voice_sample_a", "流程测试真人样本 A", 330),
            ("mock_voice_sample_b", "流程测试真人样本 B", 220),
        ):
            state.voices[voice_id] = {
                "id": voice_id,
                "type": "preset",
                "status": "active",
                "display_name": display_name,
                "audio_duration": 1.2,
                "frequency": frequency,
                "owner": None,
                "audio": None,
                "content_type": "audio/wav",
                "source_path": str(DEFAULT_VOICE_SAMPLES[voice_id]),
            }


reset_mock_state()


def _wav_bytes(*, duration: float = 1.1, frequency: int = 300) -> bytes:
    sample_rate = 24000
    frames = max(1, int(sample_rate * duration))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        for index in range(frames):
            fade = min(1.0, index / 600, (frames - index) / 600)
            value = int(3500 * max(0.0, fade) * math.sin(2 * math.pi * frequency * index / sample_rate))
            output.writeframesraw(value.to_bytes(2, "little", signed=True))
    return buffer.getvalue()


def _mock_image_bytes(width: int = 2048, height: int = 1024) -> bytes:
    """Create a deterministic 2:1 RGB PNG without adding an image dependency."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    row = bytes([0]) + bytes((18, 54, 82)) * width
    raw = row * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def _normalize_audio_bytes(data: bytes) -> bytes:
    """Decode supported input and return 24 kHz mono signed-16 PCM WAV."""
    if not data:
        raise MockCloudError(422, "VOICE_AUDIO_INVALID", "上传的模拟音色没有音频内容")
    if FFMPEG.is_file():
        with tempfile.TemporaryDirectory(prefix="ocv_mock_voice_") as directory:
            source = Path(directory) / "source_audio"
            destination = Path(directory) / "normalized.wav"
            source.write_bytes(data)
            completed = subprocess.run(
                [
                    str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                    "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(destination),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
            if completed.returncode == 0 and destination.is_file():
                normalized = destination.read_bytes()
                if normalized.startswith(b"RIFF"):
                    return normalized
            message = completed.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise MockCloudError(422, "VOICE_AUDIO_INVALID", f"无法解码上传的模拟音色：{message or 'FFmpeg 转换失败'}")
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            if source.getnchannels() == 1 and source.getsampwidth() == 2 and source.getframerate() == 24000:
                return data
    except (wave.Error, EOFError):
        pass
    raise MockCloudError(503, "MOCK_FFMPEG_MISSING", "模拟云端缺少便携 FFmpeg，无法处理参考音色")


def _voice_audio_bytes(voice: dict[str, Any]) -> bytes:
    cached = voice.get("audio")
    if isinstance(cached, bytes) and cached:
        return cached
    source_path = Path(str(voice.get("source_path") or ""))
    if source_path.is_file():
        try:
            voice["audio"] = _normalize_audio_bytes(source_path.read_bytes())
            voice["content_type"] = "audio/wav"
            with wave.open(io.BytesIO(voice["audio"]), "rb") as audio:
                voice["audio_duration"] = round(audio.getnframes() / audio.getframerate(), 3)
            return voice["audio"]
        except (OSError, MockCloudError, wave.Error):
            pass
    # Source-only Git deployments may not contain the portable IndexTTS-2.5
    # examples. Keep a deterministic fallback so contract tests still work.
    voice["audio"] = _wav_bytes(frequency=int(voice.get("frequency") or 300))
    return voice["audio"]


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {key: user[key] for key in ("id", "email", "name")}


def _issue_tokens(email: str) -> dict[str, Any]:
    access = f"mock_access_{secrets.token_urlsafe(18)}"
    refresh = f"mock_refresh_{secrets.token_urlsafe(18)}"
    state.access_tokens[access] = email
    state.refresh_tokens[refresh] = email
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": 900,
        "user": _public_user(state.users[email]),
    }


def _authenticated_email(authorization: str | None = Header(default=None)) -> str:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    email = state.access_tokens.get(token)
    if not email:
        raise MockCloudError(401, "CLOUD_LOGIN_REQUIRED", "模拟云端登录已失效，请重新登录")
    return email


def _estimate_credits(chunks: list[dict[str, Any]]) -> float:
    characters = sum(len(str(item.get("text") or "")) for item in chunks)
    return round(math.ceil(characters / 200) * 0.2, 1)


def _active_jobs(email: str) -> list[dict[str, Any]]:
    return [job for job in state.jobs.values() if job["email"] == email and job["status"] in {"queued", "running"}]


def _settle_job(job: dict[str, Any], status: str) -> None:
    if job.get("settled"):
        return
    user = state.users[job["email"]]
    reserved = float(job["reserved_credits"])
    if status == "completed":
        job["consumed_credits"] = reserved
        job["released_credits"] = 0
        entry_type = "consume"
        amount = -reserved
    else:
        user["credits"] = round(float(user["credits"]) + reserved, 1)
        job["consumed_credits"] = 0
        job["released_credits"] = reserved
        entry_type = "release"
        amount = reserved
    job["settled"] = True
    state.ledger.insert(0, {
        "id": f"led_{secrets.token_hex(5)}",
        "type": entry_type,
        "amount": amount,
        "job_id": job["job_id"],
        "created_at": time.time(),
    })


def _update_job(job: dict[str, Any]) -> None:
    if job["status"] in {"completed", "failed", "cancelled"}:
        return
    elapsed = time.monotonic() - job["created_monotonic"]
    scenario = state.users[job["email"]]["scenario"]
    duration = 12.0 if scenario == "slow" else 2.5
    if elapsed < 0.7:
        job.update(status="queued", progress=5, message="模拟任务正在排队")
        return
    if scenario == "failure" and elapsed >= 1.7:
        job.update(
            status="failed",
            progress=68,
            message="模拟 GPU Worker 故障；已释放预扣积分",
            error={"code": "MOCK_GPU_FAILURE", "message": "模拟执行失败"},
        )
        _settle_job(job, "failed")
        return
    if elapsed < duration:
        progress = 15 + int(min(1.0, (elapsed - 0.7) / max(0.1, duration - 0.7)) * 78)
        job.update(status="running", progress=progress, message=f"模拟集群配音中 {progress}%")
        return
    job.update(status="completed", progress=100, message="模拟云端配音完成")
    _settle_job(job, "completed")


def _job_payload(job: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: value for key, value in job.items()
        if key not in {"email", "created_monotonic", "chunks", "settled"}
    }
    if job["status"] == "completed":
        payload["result"] = {
            "chunks": [
                {
                    "index": int(item["index"]),
                    "audio_url": f"/api/v1/cloud/jobs/{job['job_id']}/chunks/{int(item['index'])}/audio",
                    "duration": round(max(0.8, min(3.0, len(str(item.get('text') or '')) * 0.085)), 3),
                }
                for item in job["chunks"]
            ]
        }
    return payload


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return """<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>OCV 模拟云端</title>
    <style>body{font-family:system-ui;background:#07111f;color:#e5eefc;max-width:920px;margin:48px auto;padding:0 24px}h1{color:#67e8f9}table{width:100%;border-collapse:collapse;background:#0f1d31}td,th{padding:12px;border:1px solid #28405f;text-align:left}code{color:#99f6e4}button{padding:10px 16px;border:0;border-radius:9px;background:#60a5fa;color:#07111f;font-weight:700;cursor:pointer}.ok{padding:12px;border:1px solid #2dd4bf;background:#0f766e33;border-radius:12px}</style></head>
    <body><h1>One-Click VidGen 模拟云端</h1><p class='ok'>服务运行正常：<code>http://127.0.0.1:8030/api/v1</code></p>
    <p>所有账号密码均为 <code>demo12345</code>。数据仅保存在内存中，关闭窗口即消失。默认音色优先使用整合包自带的 IndexTTS-2.5 真人示例；模拟任务只回传测试样本，不会朗读新文案。</p>
    <table><tr><th>账号</th><th>用途</th></tr><tr><td>demo@example.com</td><td>正常登录、积分、报价和配音完成</td></tr><tr><td>low@example.com</td><td>模拟积分不足</td></tr><tr><td>fail@example.com</td><td>模拟任务失败与积分释放</td></tr><tr><td>slow@example.com</td><td>模拟慢任务，适合测试停止与取消</td></tr></table>
    <p><button onclick="fetch('/api/v1/mock/reset',{method:'POST'}).then(()=>alert('模拟数据已重置'))">重置模拟数据</button></p>
    <p>请回到 One-Click VidGen 右上角点击“登录”进行测试。</p></body></html>"""


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "ocv-mock-cloud", "mock": True, "version": app.version}


@app.post("/api/v1/mock/reset")
def reset_endpoint() -> dict[str, bool]:
    reset_mock_state()
    return {"ok": True}


@app.post("/api/v1/auth/register", status_code=201)
def register(body: dict[str, Any]) -> dict[str, Any]:
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if "@" not in email or len(password) < 8:
        raise MockCloudError(422, "INVALID_REGISTRATION", "请输入有效邮箱，密码至少 8 位")
    with state.lock:
        if email in state.users:
            raise MockCloudError(409, "EMAIL_EXISTS", "该模拟账户已经存在")
        user = {
            "id": f"usr_{len(state.users) + 1:03d}", "email": email, "name": email.split("@")[0],
            "password": password, "credits": 1000, "scenario": "normal", "max_concurrent_jobs": 2,
        }
        state.users[email] = user
    return {"user": _public_user(user), "verification_required": False}


@app.post("/api/v1/auth/login")
def login(body: dict[str, Any]) -> dict[str, Any]:
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    user = state.users.get(email)
    if not user or not secrets.compare_digest(str(user["password"]), password):
        raise MockCloudError(401, "INVALID_CREDENTIALS", "模拟账户或密码错误")
    with state.lock:
        return _issue_tokens(email)


@app.post("/api/v1/auth/refresh")
def refresh_token(body: dict[str, Any]) -> dict[str, Any]:
    refresh = str(body.get("refresh_token") or "")
    email = state.refresh_tokens.pop(refresh, None)
    if not email:
        raise MockCloudError(401, "INVALID_REFRESH_TOKEN", "模拟刷新令牌已失效")
    return _issue_tokens(email)


@app.post("/api/v1/auth/logout", status_code=204)
def logout(email: str = Depends(_authenticated_email)) -> Response:
    for token, owner in list(state.access_tokens.items()):
        if owner == email:
            state.access_tokens.pop(token, None)
    return Response(status_code=204)


@app.get("/api/v1/users/me")
def current_user(email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    return {**_public_user(state.users[email]), "status": "active"}


@app.get("/api/v1/account/summary")
def account_summary(email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    with state.lock:
        for job in state.jobs.values():
            if job["email"] == email:
                _update_job(job)
        active = _active_jobs(email)
        reserved = round(sum(float(job["reserved_credits"]) for job in active), 1)
        user = state.users[email]
        return {
            "user": _public_user(user),
            "credits": {"available": user["credits"], "reserved": reserved, "currency": "mock_credit"},
            "quota": {"running_jobs": len(active), "queued_jobs": sum(job["status"] == "queued" for job in active), "max_concurrent_jobs": user["max_concurrent_jobs"]},
        }


@app.get("/api/v1/wallet/ledger")
def wallet_ledger(page: int = 1, page_size: int = 20, email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    owned_ids = {job["job_id"] for job in state.jobs.values() if job["email"] == email}
    owned_ids.update(job["job_id"] for job in state.image_jobs.values() if job["email"] == email)
    owned_ids.update(job["job_id"] for job in state.model_jobs.values() if job["email"] == email)
    items = [entry for entry in state.ledger if entry.get("job_id") in owned_ids]
    start = max(0, (page - 1) * page_size)
    return {"items": items[start:start + page_size], "page": page, "page_size": page_size, "total": len(items)}


@app.post("/api/v1/model-pool/status")
def model_pool_status(email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    return {"code": 0, "data": {"available": True, "provider": "mock", "model": "auto"}}


@app.post("/api/v1/model-pool/v1/chat/completions")
def model_pool_chat_completions(body: dict[str, Any], email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise MockCloudError(422, "MODEL_MESSAGES_REQUIRED", "文本模型 messages 不能为空")
    with state.lock:
        user = state.users[email]
        if int(user["credits"]) < 1:
            raise MockCloudError(402, "INSUFFICIENT_CREDITS", "模拟云端积分不足")
        user["credits"] -= 1
        job_id = f"mock_llm_{secrets.token_hex(6)}"
        state.model_jobs[job_id] = {"job_id": job_id, "email": email, "created_at": time.time()}
        state.ledger.insert(0, {
            "id": f"led_{secrets.token_hex(5)}",
            "type": "consume",
            "amount": -1,
            "job_id": job_id,
            "created_at": time.time(),
        })
    content = '[{"includes_slides":[1],"image_prompt":"模拟云端文本号池响应"}]'
    return {
        "id": job_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(body.get("model") or "auto"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


@app.post("/api/v1/image-pool/generate")
def image_pool_generate(body: dict[str, Any], email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise MockCloudError(422, "IMAGE_PROMPT_REQUIRED", "出图提示词不能为空")
    with state.lock:
        user = state.users[email]
        if int(user["credits"]) < 1:
            raise MockCloudError(402, "INSUFFICIENT_CREDITS", "模拟云端积分不足")
        user["credits"] -= 1
        job_id = f"mock_img_{secrets.token_hex(6)}"
        state.image_jobs[job_id] = {
            "job_id": job_id,
            "email": email,
            "status": "completed",
            "prompt": prompt,
            "created_at": time.time(),
        }
        state.ledger.insert(0, {
            "id": f"led_{secrets.token_hex(5)}",
            "type": "consume",
            "amount": -1,
            "job_id": job_id,
            "created_at": time.time(),
        })
    return {"code": 0, "data": {"taskId": job_id}, "reserved_credits": 1}


@app.post("/api/v1/image-pool/query")
def image_pool_query(body: dict[str, Any], request: Request, email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    job_id = str(body.get("taskId") or "")
    job = state.image_jobs.get(job_id)
    if not job or job["email"] != email:
        raise MockCloudError(404, "IMAGE_JOB_NOT_FOUND", "找不到该模拟出图任务")
    return {
        "code": 0,
        "data": {
            "taskId": job_id,
            "status": "SUCCESS",
            "imageUrl": str(request.url_for("image_pool_result", job_id=job_id)),
        },
    }


@app.get("/api/v1/image-pool/results/{job_id}")
def image_pool_result(job_id: str, email: str = Depends(_authenticated_email)) -> Response:
    job = state.image_jobs.get(job_id)
    if not job or job["email"] != email:
        raise MockCloudError(404, "IMAGE_JOB_NOT_FOUND", "找不到该模拟出图任务")
    return Response(_mock_image_bytes(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/v1/image-pool/account-status")
def image_pool_account_status(email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    return {"code": 0, "data": {"currentTaskCounts": 0}}


@app.post("/api/v1/image-pool/media/upload")
async def image_pool_media_upload(request: Request, file: UploadFile = File(...), email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    data = await file.read()
    if not data:
        raise MockCloudError(422, "IMAGE_UPLOAD_INVALID", "参考图为空")
    asset_id = f"mock_asset_{secrets.token_hex(6)}"
    state.image_assets[asset_id] = data
    return {
        "code": 0,
        "data": {"download_url": str(request.url_for("image_pool_media", asset_id=asset_id))},
    }


@app.get("/api/v1/image-pool/media/{asset_id}")
def image_pool_media(asset_id: str, email: str = Depends(_authenticated_email)) -> Response:
    data = state.image_assets.get(asset_id)
    if not data:
        raise MockCloudError(404, "IMAGE_ASSET_NOT_FOUND", "找不到该模拟参考图")
    return Response(data, media_type="application/octet-stream")


@app.get("/api/v1/cloud/voices")
def list_voices(type: str = "all", email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    items = []
    for voice in state.voices.values():
        if voice["owner"] not in {None, email}:
            continue
        if type != "all" and voice["type"] != type:
            continue
        items.append({key: voice[key] for key in ("id", "type", "status", "display_name", "audio_duration")})
    return {"items": items, "limits": {"max_uploaded_voices": 20}, "capabilities": {"preset": True, "upload": True}}


def _owned_voice(voice_id: str, email: str) -> dict[str, Any]:
    voice = state.voices.get(voice_id)
    if not voice or voice["owner"] not in {None, email}:
        raise MockCloudError(404, "VOICE_NOT_FOUND", "找不到该模拟音色")
    return voice


@app.get("/api/v1/cloud/voices/{voice_id}")
def get_voice(voice_id: str, email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    voice = _owned_voice(voice_id, email)
    return {key: voice[key] for key in ("id", "type", "status", "display_name", "audio_duration")}


@app.get("/api/v1/cloud/voices/{voice_id}/audio")
def voice_audio(voice_id: str, email: str = Depends(_authenticated_email)) -> Response:
    voice = _owned_voice(voice_id, email)
    return Response(_voice_audio_bytes(voice), media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.post("/api/v1/cloud/voices", status_code=201)
async def upload_voice(
    file: UploadFile = File(...),
    display_name: str = Form(...),
    email: str = Depends(_authenticated_email),
) -> dict[str, Any]:
    data = await file.read()
    if not data or len(data) > 20 * 1024 * 1024:
        raise MockCloudError(413, "VOICE_FILE_INVALID", "模拟音色文件为空或超过 20MB")
    voice_id = f"mock_uploaded_{secrets.token_hex(5)}"
    normalized = _normalize_audio_bytes(data)
    with wave.open(io.BytesIO(normalized), "rb") as audio:
        duration = round(audio.getnframes() / audio.getframerate(), 3)
    voice = {
        "id": voice_id, "type": "uploaded", "status": "active", "display_name": display_name[:80],
        "audio_duration": duration, "frequency": 280, "owner": email, "audio": normalized,
        "content_type": "audio/wav", "source_path": "",
    }
    state.voices[voice_id] = voice
    public_voice = {key: voice[key] for key in ("id", "type", "status", "display_name", "audio_duration")}
    return {"voice": public_voice, "deduplicated": False}


@app.delete("/api/v1/cloud/voices/{voice_id}", status_code=204)
def delete_voice(voice_id: str, email: str = Depends(_authenticated_email)) -> Response:
    voice = _owned_voice(voice_id, email)
    if voice["type"] == "preset":
        raise MockCloudError(403, "PRESET_VOICE_READ_ONLY", "默认音色不能删除")
    state.voices.pop(voice_id, None)
    return Response(status_code=204)


@app.post("/api/v1/cloud/quotes")
def quote(body: dict[str, Any], email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    chunks = body.get("chunks") if isinstance(body.get("chunks"), list) else []
    estimated = _estimate_credits(chunks)
    return {"estimated_credits": estimated, "characters": sum(len(str(item.get("text") or "")) for item in chunks), "currency": "mock_credit"}


@app.post("/api/v1/cloud/jobs", status_code=201)
def create_job(
    body: dict[str, Any],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    email: str = Depends(_authenticated_email),
) -> dict[str, Any]:
    chunks = body.get("chunks") if isinstance(body.get("chunks"), list) else []
    if not chunks:
        raise MockCloudError(422, "EMPTY_CHUNKS", "模拟任务缺少文本分块")
    key = str(idempotency_key or body.get("client_job_id") or secrets.token_hex(8))
    with state.lock:
        previous = state.idempotency.get((email, key))
        if previous:
            job = state.jobs[previous]
            _update_job(job)
            return _job_payload(job)
        user = state.users[email]
        if len(_active_jobs(email)) >= int(user["max_concurrent_jobs"]):
            raise MockCloudError(429, "CONCURRENCY_LIMIT", "模拟账户并发已满，请稍后重试")
        estimated = _estimate_credits(chunks)
        if float(user["credits"]) < estimated:
            raise MockCloudError(403, "INSUFFICIENT_CREDITS", f"模拟积分不足：需要 {estimated}，当前 {user['credits']}")
        voice = body.get("voice") or {}
        _owned_voice(str(voice.get("id") or ""), email)
        user["credits"] -= estimated
        job_id = f"mock_job_{secrets.token_hex(6)}"
        job = {
            "job_id": job_id, "client_job_id": str(body.get("client_job_id") or ""), "email": email,
            "status": "queued", "progress": 0, "message": "模拟任务已创建", "chunks": chunks,
            "voice_id": str(voice.get("id") or ""),
            "reserved_credits": estimated, "consumed_credits": 0, "released_credits": 0,
            "created_at": time.time(), "created_monotonic": time.monotonic(), "settled": False,
        }
        state.jobs[job_id] = job
        state.idempotency[(email, key)] = job_id
        state.ledger.insert(0, {"id": f"led_{secrets.token_hex(5)}", "type": "reserve", "amount": -estimated, "job_id": job_id, "created_at": time.time()})
        return _job_payload(job)


def _owned_job(job_id: str, email: str) -> dict[str, Any]:
    job = state.jobs.get(job_id)
    if not job or job["email"] != email:
        raise MockCloudError(404, "JOB_NOT_FOUND", "找不到该模拟任务")
    _update_job(job)
    return job


@app.get("/api/v1/cloud/jobs/{job_id}")
def get_job(job_id: str, email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    with state.lock:
        return _job_payload(_owned_job(job_id, email))


@app.get("/api/v1/cloud/jobs")
def list_jobs(page: int = 1, page_size: int = 20, status: str | None = None, email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    with state.lock:
        items = []
        for job in state.jobs.values():
            if job["email"] != email:
                continue
            _update_job(job)
            if status and job["status"] != status:
                continue
            items.append(_job_payload(job))
        items.sort(key=lambda item: item["created_at"], reverse=True)
        start = max(0, (page - 1) * page_size)
        return {"items": items[start:start + page_size], "page": page, "page_size": page_size, "total": len(items)}


@app.post("/api/v1/cloud/jobs/{job_id}/cancel")
def cancel_job(job_id: str, email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    with state.lock:
        job = _owned_job(job_id, email)
        if job["status"] not in {"completed", "failed", "cancelled"}:
            job.update(status="cancelled", progress=job.get("progress", 0), message="模拟任务已取消，预扣积分已释放")
            _settle_job(job, "cancelled")
        return _job_payload(job)


@app.get("/api/v1/cloud/jobs/{job_id}/chunks/{chunk_index}/audio")
def chunk_audio(job_id: str, chunk_index: int, email: str = Depends(_authenticated_email)) -> Response:
    with state.lock:
        job = _owned_job(job_id, email)
        if job["status"] != "completed":
            raise MockCloudError(409, "JOB_NOT_COMPLETED", "模拟任务尚未完成")
        try:
            chunk = next(item for item in job["chunks"] if int(item["index"]) == chunk_index)
        except StopIteration as exc:
            raise MockCloudError(404, "CHUNK_NOT_FOUND", "找不到该模拟音频分块") from exc
        voice = _owned_voice(str(job.get("voice_id") or ""), email)
        return Response(_voice_audio_bytes(voice), media_type="audio/wav")


@app.post("/api/v1/recharge/orders", status_code=201)
def create_recharge_order(body: dict[str, Any], email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    amount = max(1, int(body.get("credits") or body.get("amount") or 100))
    state.users[email]["credits"] += amount
    order_id = f"mock_order_{secrets.token_hex(5)}"
    return {"order_id": order_id, "status": "paid", "credits": amount, "mock": True}


@app.get("/api/v1/recharge/orders/{order_id}")
def get_recharge_order(order_id: str, email: str = Depends(_authenticated_email)) -> dict[str, Any]:
    return {"order_id": order_id, "status": "paid", "mock": True}
