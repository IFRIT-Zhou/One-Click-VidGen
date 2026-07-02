import os
from pathlib import Path
from typing import Any, Literal

import pymysql
from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from .auth import COOKIE_NAME, current_user_from_request, require_user, sign_session
from .db import (
    authenticate_user,
    create_user,
    db_status,
    init_database,
    list_media_assets,
    sole_user_id,
)
from .gemini_client import DEFAULT_GEMINI_MODEL, gemini_configured
from .runninghub_tts import EMOTIONS, SYSTEM_VOICE_IDS, load_runninghub_tts_config
from .editor import (
    edit_store,
    list_uploads,
    render_artifact_path,
    save_upload,
    upload_path,
)
from .pipeline import PROJECT_ROOT, JOBS_DIR, store


class GenerateRequest(BaseModel):
    script: str = Field(..., min_length=5)
    tts_voice_id: Literal[
        "Wise_Woman",
        "Friendly_Person",
        "Inspirational_girl",
        "Deep_Voice_Man",
        "Calm_Woman",
        "Casual_Guy",
        "Lively_Girl",
        "Patient_Man",
        "Young_Knight",
        "Determined_Man",
        "Lovely_Girl",
        "Decent_Boy",
        "Imposing_Manner",
        "Elegant_Man",
        "Abbess",
        "Sweet_Girl_2",
        "Exuberant_Girl",
    ] = "Wise_Woman"
    tts_speed: float = Field(default=1, ge=0.5, le=2)
    tts_volume: float = Field(default=1, ge=0.1, le=10)
    tts_pitch: int = Field(default=0, ge=-12, le=12)
    tts_emotion: Literal[
        "happy",
        "sad",
        "angry",
        "fearful",
        "disgusted",
        "surprised",
        "neutral",
    ] | None = None
    tts_english_normalization: bool = False
    tts_pronunciation: str | None = Field(default=None, max_length=200)
    api_key: str | None = None
    base_url: str | None = "https://api.openai.com/v1"
    model: str | None = "gpt-4o-mini"
    visual_style: str = "video-edit-agent"
    visual_backend: str | None = "poster"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class EditRequest(BaseModel):
    video_id: str
    audio_id: str | None = None
    subtitle_id: str | None = None
    trim_start: float = Field(default=0, ge=0)
    trim_end: float = Field(default=0, ge=0)
    video_volume: float = Field(default=1, ge=0, le=3)
    audio_volume: float = Field(default=0.8, ge=0, le=3)
    audio_offset: float = Field(default=0, ge=0)
    burn_subtitles: bool = True


app = FastAPI(title="Voice Over Video API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORKSPACE_DIR = PROJECT_ROOT / "workspace"
if WORKSPACE_DIR.exists():
    app.mount("/workspace", StaticFiles(directory=str(WORKSPACE_DIR)), name="workspace")


@app.on_event("startup")
def startup() -> None:
    init_database()
    if db_status()["ready"]:
        store.load_persisted()
        edit_store.load_persisted()
        store.import_legacy_jobs(sole_user_id())


def list_files(patterns: list[str]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for pattern in patterns:
        for path in PROJECT_ROOT.glob(pattern):
            if path.is_file():
                if path.name in {"requirements.txt", "package-lock.json", "package.json"}:
                    continue
                items.append(
                    {
                        "name": path.name,
                        "path": str(path.relative_to(PROJECT_ROOT)),
                    }
                )
    return sorted(items, key=lambda item: item["name"])


@app.get("/api/health")
def health() -> dict[str, Any]:
    runninghub_tts = load_runninghub_tts_config()
    return {
        "ok": True,
        "tts_online": runninghub_tts is not None,
        "tts_provider": "runninghub/minimax/speech-2.8-hd",
        "tts_voice_id": runninghub_tts.voice_id if runninghub_tts else None,
        "tts_autostart": False,
        "tts_api_base_url": runninghub_tts.endpoint if runninghub_tts else None,
        "mysql": db_status(),
        "gemini": {
            "configured": gemini_configured(),
            "model": os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        },
        "backend_port": 8010,
    }


@app.post("/api/tts/start")
def start_tts(request: Request) -> dict[str, Any]:
    require_user(request)
    runninghub_tts = load_runninghub_tts_config()
    if runninghub_tts is not None:
        return {
            "online": True,
            "launching": False,
            "started": False,
            "message": "RunningHub MiniMax TTS 已配置",
        }
    return {
        "online": False,
        "launching": False,
        "started": False,
        "message": "RunningHub TTS 未启用或未配置 API Key",
    }


@app.get("/api/session")
def session(request: Request) -> dict[str, Any]:
    user = current_user_from_request(request)
    return {
        "user": user,
        "mysql": db_status(),
    }


@app.post("/api/auth/register")
def register(payload: RegisterRequest, response: Response) -> dict[str, Any]:
    try:
        user = create_user(payload.email, payload.password, payload.name)
    except pymysql.err.IntegrityError:
        raise HTTPException(status_code=409, detail="邮箱已注册")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MySQL 不可用: {exc}")
    response.set_cookie(
        COOKIE_NAME,
        sign_session(int(user["id"])),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 14,
    )
    return {"user": user}


@app.post("/api/auth/login")
def login(payload: LoginRequest, response: Response) -> dict[str, Any]:
    try:
        user = authenticate_user(payload.email, payload.password)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MySQL 不可用: {exc}")
    if not user:
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")
    response.set_cookie(
        COOKIE_NAME,
        sign_session(int(user["id"])),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 14,
    )
    return {"user": user}


@app.post("/api/auth/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@app.get("/api/settings")
def settings() -> dict[str, Any]:
    runninghub_tts = load_runninghub_tts_config()
    return {
        "scripts": list_files(["*.txt"]),
        "tts": {
            "model": "minimax/speech-2.8-hd",
            "voices": list(SYSTEM_VOICE_IDS),
            "emotions": list(EMOTIONS),
            "defaults": {
                "voice_id": runninghub_tts.voice_id if runninghub_tts else "Wise_Woman",
                "speed": runninghub_tts.speed if runninghub_tts else 1,
                "volume": runninghub_tts.volume if runninghub_tts else 1,
                "pitch": runninghub_tts.pitch if runninghub_tts else 0,
                "emotion": runninghub_tts.emotion if runninghub_tts else None,
                "english_normalization": (
                    runninghub_tts.english_normalization if runninghub_tts else False
                ),
                "pronunciation": (
                    runninghub_tts.pronunciation_dict[0]
                    if runninghub_tts and runninghub_tts.pronunciation_dict
                    else ""
                ),
            },
        },
        "model_options": [
            {"value": "gpt-4o-mini", "label": "OpenAI gpt-4o-mini"},
            {"value": "gpt-4.1-mini", "label": "OpenAI gpt-4.1-mini"},
            {"value": "openai/gpt-4o-mini", "label": "OpenRouter openai/gpt-4o-mini"},
            {"value": "google/gemini-2.5-flash-lite", "label": "OpenRouter Gemini Flash Lite"},
        ],
        "provider_notes": [
            "当前画面生成是 HTML/CSS 代码生成，不调用昂贵的图片生成接口。",
            "可把 base_url 设置为 OpenAI 兼容中转，例如 https://openrouter.ai/api/v1。",
            "不建议使用来路不明的低价转发站保存长期密钥；本地使用时可填临时 key。",
        ],
    }


@app.get("/api/scripts/{name}")
def read_script(name: str) -> dict[str, str]:
    path = (PROJECT_ROOT / name).resolve()
    if PROJECT_ROOT not in path.parents or not path.is_file() or path.suffix.lower() != ".txt":
        raise HTTPException(status_code=404, detail="script not found")
    return {"name": path.name, "content": path.read_text(encoding="utf-8")}


@app.post("/api/jobs")
def create_job(payload: GenerateRequest, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = store.create(payload.model_dump(), user_id=int(user["id"]))
    store.run_async(job)
    return job.snapshot()


@app.get("/api/jobs")
def list_jobs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=5, ge=1, le=100),
) -> dict[str, Any]:
    user = require_user(request)
    return store.list_page(
        user_id=int(user["id"]),
        page=page,
        page_size=page_size,
    )


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="job not found")
    return job.snapshot()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="job not found")
    return store.cancel(job)


@app.get("/api/jobs/{job_id}/logs")
def get_job_logs(job_id: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="job not found")
    return {"logs": job.logs}


@app.get("/api/jobs/{job_id}/assets")
def get_job_assets(job_id: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "assets": list_media_assets(
            user_id=int(user["id"]),
            generation_job_id=job_id,
        )
    }


@app.get("/api/jobs/{job_id}/artifacts/{filename}")
def get_artifact(job_id: str, filename: str, request: Request) -> FileResponse:
    user = require_user(request)
    job = store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="artifact not found")
    path = (JOBS_DIR / job_id / "artifacts" / filename).resolve()
    expected_root = (JOBS_DIR / job_id / "artifacts").resolve()
    if expected_root not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(str(path), filename=filename)


@app.post("/api/editor/uploads")
async def upload_editor_asset(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    user = require_user(request)
    try:
        asset = await save_upload(int(user["id"]), file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"asset": asset}


@app.get("/api/editor/uploads")
def get_editor_uploads(request: Request) -> dict[str, Any]:
    user = require_user(request)
    return {"assets": list_uploads(int(user["id"]))}


@app.get("/api/editor/uploads/{filename}")
def get_editor_upload(filename: str, request: Request) -> FileResponse:
    user = require_user(request)
    try:
        path = upload_path(int(user["id"]), filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(str(path), filename=path.name)


@app.post("/api/editor/jobs")
def create_editor_job(payload: EditRequest, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = edit_store.create(int(user["id"]), payload.model_dump())
    edit_store.run_async(job)
    return job.snapshot()


@app.get("/api/editor/jobs")
def list_editor_jobs(request: Request) -> dict[str, Any]:
    user = require_user(request)
    return {"jobs": edit_store.list_recent(int(user["id"]))}


@app.get("/api/editor/jobs/{job_id}")
def get_editor_job(job_id: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = edit_store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="editor job not found")
    return job.snapshot()


@app.get("/api/editor/jobs/{job_id}/assets")
def get_editor_job_assets(job_id: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = edit_store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="editor job not found")
    return {
        "assets": list_media_assets(
            user_id=int(user["id"]),
            editor_job_id=job_id,
        )
    }


@app.get("/api/editor/jobs/{job_id}/artifacts/{filename}")
def get_editor_artifact(job_id: str, filename: str, request: Request) -> FileResponse:
    user = require_user(request)
    job = edit_store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="artifact not found")
    try:
        path = render_artifact_path(int(user["id"]), job_id, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(str(path), filename=filename)
