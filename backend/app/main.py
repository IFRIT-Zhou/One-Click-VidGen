import os
import subprocess
from pathlib import Path
from typing import Any, Literal

import pymysql
from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from .auth import COOKIE_NAME, current_user_from_request, local_auth_enabled, local_user, require_user, sign_session
from .db import (
    authenticate_user,
    create_user,
    db_status,
    ensure_local_user,
    init_database,
    list_media_assets,
    sole_user_id,
)
from .gemini_client import DEFAULT_GEMINI_MODEL, gemini_configured
from .indextts2_local import EMOTIONS, load_indextts2_config
from .editor import (
    edit_store,
    list_uploads,
    render_artifact_path,
    save_upload,
    upload_path,
)
from .pipeline import JOBS_DIR, PROJECT_ROOT, normalize_project_name, store
from module4_video_render import (
    CONTENT_MODE_SCIENCE,
    CONTENT_MODE_STORY,
    DEFAULT_VISUAL_PROMPT_SYSTEM,
    DEFAULT_VISUAL_STYLE,
    SCIENCE_VISUAL_PROMPT_SYSTEM,
    SCIENCE_VISUAL_STYLE,
    build_visual_prompt_system,
)


class GenerateRequest(BaseModel):
    project_name: str = Field(default="", max_length=80)
    script: str = ""
    module1_only: bool = False
    content_mode: Literal["urban_suspense", "science_explainer"] = "urban_suspense"
    skip_tts: bool = False
    source_audio_id: str | None = None
    skip_text_correction: bool = False
    auto_split_long_text: bool = True
    split_text_threshold: int = Field(default=3000, ge=800, le=12000)
    tts_voice_id: str = Field(default="voice_05.wav", max_length=180)
    tts_speed: float = Field(default=1, ge=0.5, le=2)
    tts_volume: float = Field(default=1, ge=0.1, le=10)
    tts_pitch: int = Field(default=0, ge=-12, le=12)
    tts_parallelism: int = Field(default=2, ge=1, le=3)
    tts_emotion: str | None = Field(default=None, max_length=30)
    tts_english_normalization: bool = False
    tts_pronunciation: str | None = Field(default=None, max_length=200)
    api_key: str | None = None
    base_url: str | None = "https://api.openai.com/v1"
    model: str | None = "gpt-4o-mini"
    visual_style: str = "video-edit-agent"
    visual_backend: str | None = "poster"
    visual_prompt_mode: Literal["simple", "full"] = "simple"
    visual_style_prompt: str | None = Field(default=None, max_length=1000)
    visual_prompt_system: str | None = Field(default=None, max_length=4000)


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
        if local_auth_enabled():
            user = local_user()
            ensure_local_user(int(user["id"]), str(user["email"]), str(user["name"]))
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
    indextts2 = load_indextts2_config()
    return {
        "ok": True,
        "tts_online": indextts2.ready,
        "tts_provider": "official IndexTTS2 2.0.0 (local GPU)",
        "tts_voice_id": indextts2.default_voice,
        "tts_autostart": False,
        "tts_api_base_url": None,
        "tts_device": indextts2.device,
        "tts_missing": indextts2.missing_resources(),
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
    indextts2 = load_indextts2_config()
    if indextts2.ready:
        return {
            "online": True,
            "launching": False,
            "started": False,
            "message": f"官方 IndexTTS2 已就绪（{indextts2.device}）",
        }
    return {
        "online": False,
        "launching": False,
        "started": False,
        "message": "官方 IndexTTS2 未就绪：" + "、".join(indextts2.missing_resources()),
    }


@app.get("/api/session")
def session(request: Request) -> dict[str, Any]:
    user = current_user_from_request(request)
    return {
        "user": user,
        "auth_mode": "local" if local_auth_enabled() else "account",
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
    indextts2 = load_indextts2_config()
    last_visual_prompt = ""
    for job in store.list_recent():
        candidate = str(job.get("request", {}).get("visual_prompt_system") or "").strip()
        if candidate:
            last_visual_prompt = candidate
            break
    return {
        "scripts": list_files(["*.txt"]),
        "tts": {
            "model": "official IndexTTS2 2.0.0 · local FP16",
            "voices": list(indextts2.available_voices()),
            "emotions": list(EMOTIONS),
            "defaults": {
                "voice_id": indextts2.default_voice,
                "speed": 1,
                "volume": 1,
                "pitch": 0,
                "parallelism": 2,
                "emotion": None,
                "english_normalization": False,
                "pronunciation": "",
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
        "visual_prompt": {
            "default_system": DEFAULT_VISUAL_PROMPT_SYSTEM,
            "default_style": DEFAULT_VISUAL_STYLE,
            "last_used_system": last_visual_prompt,
            "modes": {
                CONTENT_MODE_STORY: {
                    "label": "都市惊悚",
                    "description": "人物、线索与悬念连续的阴森漫画故事",
                    "default_style": DEFAULT_VISUAL_STYLE,
                    "default_system": DEFAULT_VISUAL_PROMPT_SYSTEM,
                },
                CONTENT_MODE_SCIENCE: {
                    "label": "口播科普",
                    "description": "红围巾短发少女的清晰科教漫画",
                    "default_style": SCIENCE_VISUAL_STYLE,
                    "default_system": SCIENCE_VISUAL_PROMPT_SYSTEM,
                },
            },
        },
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
    data = payload.model_dump()
    data["project_name"] = normalize_project_name(data.get("project_name"))
    if data.get("visual_prompt_mode") == "simple":
        data["visual_prompt_system"] = build_visual_prompt_system(
            str(data.get("visual_style_prompt") or ""),
            str(data.get("content_mode") or CONTENT_MODE_STORY),
        )
    script = str(data.get("script") or "").strip()
    if data.get("module1_only"):
        data["skip_tts"] = False
        data["skip_text_correction"] = False
        data["source_audio_id"] = None
    if data.get("skip_tts"):
        if not data.get("source_audio_id"):
            raise HTTPException(status_code=400, detail="请先上传已有配音")
        if not script:
            data["skip_text_correction"] = True
    elif len(script) < 5:
        raise HTTPException(status_code=400, detail="请输入至少 5 个字的口播文案")
    elif data.get("skip_text_correction"):
        raise HTTPException(status_code=400, detail="只有使用已有配音时才能跳过字幕校对")
    job = store.create(data, user_id=int(user["id"]))
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


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="job not found")
    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=400, detail="只有失败或已停止的任务可以断点续跑")
    return store.resume(job)


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


@app.post("/api/jobs/{job_id}/artifacts/{filename}/open-folder")
def open_artifact_folder(job_id: str, filename: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="artifact not found")
    path = (JOBS_DIR / job_id / "artifacts" / filename).resolve()
    expected_root = (JOBS_DIR / job_id / "artifacts").resolve()
    if expected_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="打开文件夹功能目前只支持 Windows 便携版")
    try:
        subprocess.Popen(["explorer.exe", f"/select,{path}"], close_fds=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法打开资源管理器: {exc}") from exc
    return {"ok": True, "path": str(path.parent)}


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
