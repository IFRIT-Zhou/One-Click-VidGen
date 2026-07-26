import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Literal

import pymysql
from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from .auth import COOKIE_NAME, current_user_from_request, local_auth_enabled, local_user, require_user, sign_session
from .config import _parse_env_lines, save_project_env_values
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
from .qwen_tts import DEFAULT_VOICE as DEFAULT_QWEN_VOICE, voice_supports_instructions
from .editor import (
    edit_store,
    list_uploads,
    render_artifact_path,
    save_upload,
    upload_path,
)
from .pipeline import (
    JOBS_DIR, OUTPUT_DIR, PROJECT_ROOT, GenerationCancelled, SUBTITLE_VIDEO_STYLES,
    normalize_project_name, render_standalone_subtitle_video, store, system_subtitle_fonts,
    user_upload_path,
)
from .visual_editor import IMAGE_EXTENSIONS, visual_editor
from module4_video_render import (
    CONTENT_MODE_SCIENCE,
    CONTENT_MODE_GENERAL,
    CONTENT_MODE_STORY,
    DEFAULT_VISUAL_PROMPT_SYSTEM,
    GENERAL_VISUAL_PROMPT_SYSTEM,
    GENERAL_VISUAL_STYLE,
    DEFAULT_GLOBAL_CHARACTER_PROMPT,
    DEFAULT_VISUAL_STYLE,
    SCIENCE_GLOBAL_CHARACTER_PROMPT,
    SCIENCE_VISUAL_PROMPT_SYSTEM,
    SCIENCE_VISUAL_STYLE,
    build_visual_prompt_system,
)
from story_agents import AGENT0_SYSTEM_PROMPT, TIMELINE_AGENT_SYSTEM_PROMPT

MAX_SCRIPT_CHARACTERS = 12_000


class GenerateRequest(BaseModel):
    project_name: str = Field(default="", max_length=80)
    script: str = ""
    module1_only: bool = False
    subtitle_only: bool = False
    subtitle_use_correction: bool = True
    content_mode: Literal["urban_suspense", "science_explainer", "general"] = "urban_suspense"
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
    tts_engine: Literal["indextts2", "qwen"] = "indextts2"
    tts_emotion: str | None = Field(default=None, max_length=30)
    tts_english_normalization: bool = False
    tts_pronunciation: str | None = Field(default=None, max_length=200)
    qwen_tts_instructions: str | None = Field(default=None, max_length=1600)
    qwen_tts_voice: str = Field(default=DEFAULT_QWEN_VOICE, min_length=1, max_length=80)
    qwen_tts_optimize_instructions: bool = False
    api_key: str | None = None
    base_url: str | None = "https://api.openai.com/v1"
    model: str | None = "gpt-4o-mini"
    visual_style: str = "video-edit-agent"
    visual_backend: str | None = "poster"
    video_render_variant: Literal["subtitles", "raw", "both"] = "both"
    step_mode: bool = False
    visual_prompt_mode: Literal["simple", "full"] = "simple"
    visual_pacing_preset: Literal["auto", "slow", "standard", "fast", "custom"] = "auto"
    visual_min_duration: float | None = Field(default=None, ge=4, le=20)
    visual_target_duration: float | None = Field(default=None, ge=5, le=30)
    visual_max_duration: float | None = Field(default=None, ge=6, le=40)
    visual_max_slides: int | None = Field(default=None, ge=1, le=12)
    visual_style_prompt: str | None = Field(default=None, max_length=1000)
    global_character_prompt: str | None = Field(default=None, max_length=2000)
    protagonist_reference_image_id: str | None = Field(default=None, max_length=180)
    reference_image_ids: list[str] = Field(default_factory=list, max_length=3)
    story_environment_prompt: str | None = Field(default=None, max_length=2000)
    visual_prompt_system: str | None = Field(default=None, max_length=4000)
    agent0_prompt_system: str | None = Field(default=None, max_length=12000)
    agent1_prompt_system: str | None = Field(default=None, max_length=12000)


class ParameterPresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parameters: dict[str, Any]


class AgentPromptPresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    visual_prompt_system: str = Field(min_length=1, max_length=4000)
    agent0_prompt_system: str | None = Field(default=None, max_length=12000)
    agent1_prompt_system: str | None = Field(default=None, max_length=12000)
    agent2_director_theme: str | None = Field(default=None, max_length=40)
    content_mode: Literal["urban_suspense", "science_explainer", "general"] | None = None


class VisualRedrawRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    # One existing project frame can be locked as reference image 1.  Local
    # uploads follow it as images 2-4.
    reference_macro_ids: list[str] = Field(default_factory=list, max_length=1)
    reference_upload_ids: list[str] = Field(default_factory=list, max_length=3)


class VisualBaselineRequest(BaseModel):
    prompt: str | None = Field(default=None, max_length=12000)


class SubtitleRenderRequest(BaseModel):
    style: Literal["black_white_outline", "white_black_outline", "yellow_bg_black", "white_bg_black", "navy_bg_white"] = "navy_bg_white"
    font_name: str = Field(default="Microsoft YaHei", min_length=1, max_length=100)


class VisualRenderRequest(BaseModel):
    mode: Literal["subtitles", "raw", "both"] = "both"


class VisualTimingAdjustRequest(BaseModel):
    action: Literal["extend_prev", "extend_next", "shrink_prev", "shrink_next"]


class VisualTimingHistoryRequest(BaseModel):
    history_id: str = Field(min_length=1, max_length=260)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class ApiKeySettingsRequest(BaseModel):
    language_api_key: str | None = Field(default=None, max_length=2048)
    image_api_key: str | None = Field(default=None, max_length=2048)
    image_api_keys: list[str] = Field(default_factory=list, max_length=10)
    common_api_key: str | None = Field(default=None, max_length=2048)
    common_api_keys: list[str] = Field(default_factory=list, max_length=10)
    qwen_tts_api_key: str | None = Field(default=None, max_length=2048)


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
PARAMETER_PRESETS_DIR = PROJECT_ROOT / "saved_parameters"
AGENT_PROMPT_PRESETS_DIR = PROJECT_ROOT / "saved_agent_prompts"
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
            "default_character": DEFAULT_GLOBAL_CHARACTER_PROMPT,
            "last_used_system": last_visual_prompt,
            "modes": {
                CONTENT_MODE_STORY: {
                    "label": "都市惊悚",
                    "description": "人物、线索与悬念连续的阴森漫画故事",
                    "default_style": DEFAULT_VISUAL_STYLE,
                    "default_character": DEFAULT_GLOBAL_CHARACTER_PROMPT,
                    "default_system": DEFAULT_VISUAL_PROMPT_SYSTEM,
                    "default_agent0_system": AGENT0_SYSTEM_PROMPT,
                    "default_agent1_system": TIMELINE_AGENT_SYSTEM_PROMPT,
                },
                CONTENT_MODE_SCIENCE: {
                    "label": "口播科普",
                    "description": "红围巾短发少女的清晰科教漫画",
                    "default_style": SCIENCE_VISUAL_STYLE,
                    "default_character": SCIENCE_GLOBAL_CHARACTER_PROMPT,
                    "default_system": SCIENCE_VISUAL_PROMPT_SYSTEM,
                    "default_agent0_system": AGENT0_SYSTEM_PROMPT,
                    "default_agent1_system": TIMELINE_AGENT_SYSTEM_PROMPT,
                },
                CONTENT_MODE_GENERAL: {
                    "label": "通用自定义",
                    "description": "自行决定画风与人物形象的通用视频模式",
                    "default_style": GENERAL_VISUAL_STYLE,
                    "default_character": "",
                    "default_system": GENERAL_VISUAL_PROMPT_SYSTEM,
                    "default_agent0_system": AGENT0_SYSTEM_PROMPT,
                    "default_agent1_system": TIMELINE_AGENT_SYSTEM_PROMPT,
                },
            },
        },
    }


def _unique_api_keys(values: list[str]) -> list[str]:
    result: list[str] = []
    for raw_value in values:
        for value in re.split(r"[,;\s]+", str(raw_value or "")):
            key = value.strip()
            if key and key not in result:
                result.append(key)
    return result


def _runninghub_api_keys(values: dict[str, str]) -> list[str]:
    candidates = [values.get("RUNNINGHUB_API_KEY", ""), values.get("RUNNINGHUB_API_KEYS", "")]
    candidates.extend(
        value
        for name, value in sorted(values.items())
        if re.fullmatch(r"RUNNINGHUB_API_KEY_?\d+", name)
    )
    return _unique_api_keys(candidates)


def _common_api_keys(values: dict[str, str]) -> list[str]:
    return _unique_api_keys([
        values.get("APP_COMMON_API_KEY", ""),
        values.get("APP_COMMON_API_KEYS", ""),
    ])


def _api_key_status() -> dict[str, Any]:
    values = _parse_env_lines(PROJECT_ROOT / ".env")
    image_keys = _runninghub_api_keys(values)
    common_keys = _common_api_keys(values)
    return {
        "language": {"configured": bool(values.get("GEMINI_API_KEY", "").strip())},
        "image": {"configured": bool(image_keys), "count": len(image_keys)},
        "common": {"configured": bool(common_keys), "count": len(common_keys)},
        "qwen_tts": {"configured": bool(values.get("DASHSCOPE_API_KEY", "").strip())},
    }


@app.get("/api/api-keys")
def get_api_key_settings(request: Request) -> dict[str, Any]:
    require_user(request)
    return {"keys": _api_key_status()}


@app.put("/api/api-keys")
def save_api_key_settings(payload: ApiKeySettingsRequest, request: Request) -> dict[str, Any]:
    require_user(request)
    language = str(payload.language_api_key or "").strip()
    image = str(payload.image_api_key or "").strip()
    image_additions = _unique_api_keys(payload.image_api_keys)
    common = str(payload.common_api_key or "").strip()
    common_additions = _unique_api_keys(payload.common_api_keys)
    qwen_tts = str(payload.qwen_tts_api_key or "").strip()
    all_supplied = [language, image, common, qwen_tts, *image_additions, *common_additions]
    if not any(all_supplied):
        raise HTTPException(status_code=400, detail="请至少填写一个 API Key")
    if any("\n" in value or "\r" in value for value in all_supplied):
        raise HTTPException(status_code=400, detail="API Key 不能包含换行")

    existing = _parse_env_lines(PROJECT_ROOT / ".env")
    existing_common = _common_api_keys(existing)
    supplied_common = _unique_api_keys([common, *common_additions])
    common_pool = _unique_api_keys([*existing_common, *supplied_common])
    common_primary = common or existing.get("APP_COMMON_API_KEY", "").strip() or (common_pool[0] if common_pool else "")

    existing_image = _runninghub_api_keys(existing)
    supplied_image = _unique_api_keys([image, *image_additions])
    # Every common RunningHub-style account is also eligible for Image2 work.
    image_pool = _unique_api_keys([*existing_image, *supplied_image, *common_pool])
    image_primary = (
        image
        or common
        or existing.get("RUNNINGHUB_API_KEY", "").strip()
        or (image_pool[0] if image_pool else "")
    )

    # A common RunningHub-style key fills either dedicated field left blank.
    updates: dict[str, str] = {}
    if supplied_common:
        updates["APP_COMMON_API_KEY"] = common_primary
        updates["APP_COMMON_API_KEYS"] = ",".join(key for key in common_pool if key != common_primary)
    if language or common:
        updates["GEMINI_API_KEY"] = language or common
    if supplied_image or supplied_common:
        updates["RUNNINGHUB_API_KEY"] = image_primary
        updates["RUNNINGHUB_API_KEYS"] = ",".join(key for key in image_pool if key != image_primary)
    if qwen_tts:
        updates["DASHSCOPE_API_KEY"] = qwen_tts
    try:
        save_project_env_values(updates)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"保存 API Key 失败: {exc}") from exc
    return {
        "keys": _api_key_status(),
        "message": "API Key 已保存到本机 .env；新增账号已合并去重并加入图像并行号池。",
    }


@app.get("/api/scripts/{name}")
def read_script(name: str) -> dict[str, str]:
    path = (PROJECT_ROOT / name).resolve()
    if PROJECT_ROOT not in path.parents or not path.is_file() or path.suffix.lower() != ".txt":
        raise HTTPException(status_code=404, detail="script not found")
    return {"name": path.name, "content": path.read_text(encoding="utf-8")}


def _parameter_preset_dir(user_id: int) -> Path:
    directory = PARAMETER_PRESETS_DIR / str(int(user_id))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _parameter_preset_path(user_id: int, name: str) -> Path:
    safe_name = normalize_project_name(name)
    return _parameter_preset_dir(user_id) / f"{safe_name}.json"


def _agent_prompt_preset_dir(user_id: int) -> Path:
    directory = AGENT_PROMPT_PRESETS_DIR / str(int(user_id))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _agent_prompt_preset_path(user_id: int, name: str) -> Path:
    return _agent_prompt_preset_dir(user_id) / f"{normalize_project_name(name)}.json"


def _default_agent_prompt_preset_dir() -> Path:
    directory = AGENT_PROMPT_PRESETS_DIR / "defaults"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


DEFAULT_AGENT_PROMPT_PRESETS: dict[str, dict[str, str]] = {
    CONTENT_MODE_STORY: {
        "name": "都市惊悚",
        "visual_prompt_system": DEFAULT_VISUAL_PROMPT_SYSTEM,
        "agent0_prompt_system": AGENT0_SYSTEM_PROMPT,
        "agent1_prompt_system": TIMELINE_AGENT_SYSTEM_PROMPT,
        "agent2_director_theme": "惊悚漫画",
    },
    CONTENT_MODE_SCIENCE: {
        "name": "口播科普",
        "visual_prompt_system": SCIENCE_VISUAL_PROMPT_SYSTEM,
        "agent0_prompt_system": AGENT0_SYSTEM_PROMPT,
        "agent1_prompt_system": TIMELINE_AGENT_SYSTEM_PROMPT,
        "agent2_director_theme": "科普科技口播视频",
    },
    CONTENT_MODE_GENERAL: {
        "name": "通用自定义",
        "visual_prompt_system": GENERAL_VISUAL_PROMPT_SYSTEM,
        "agent0_prompt_system": AGENT0_SYSTEM_PROMPT,
        "agent1_prompt_system": TIMELINE_AGENT_SYSTEM_PROMPT,
        "agent2_director_theme": "通用视频",
    },
}
DEFAULT_AGENT_PROMPT_PRESET_VERSION = 4


def _default_agent_prompt_preset_path(content_mode: str) -> Path:
    return _default_agent_prompt_preset_dir() / f"{content_mode}.json"


def _ensure_default_agent_prompt_presets() -> None:
    for content_mode, defaults in DEFAULT_AGENT_PROMPT_PRESETS.items():
        path = _default_agent_prompt_preset_path(content_mode)
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if int(existing.get("version", 0)) >= DEFAULT_AGENT_PROMPT_PRESET_VERSION:
                    continue
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        document = {
            "version": DEFAULT_AGENT_PROMPT_PRESET_VERSION,
            "kind": "default",
            "content_mode": content_mode,
            "name": defaults["name"],
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "visual_prompt_system": defaults["visual_prompt_system"],
            "agent0_prompt_system": defaults["agent0_prompt_system"],
            "agent1_prompt_system": defaults["agent1_prompt_system"],
            "agent2_director_theme": defaults["agent2_director_theme"],
        }
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/parameter-presets")
def list_parameter_presets(request: Request) -> dict[str, Any]:
    user = require_user(request)
    presets: list[dict[str, Any]] = []
    for path in sorted(_parameter_preset_dir(int(user["id"])).glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        presets.append({
            "name": str(payload.get("name") or path.stem),
            "saved_at": str(payload.get("saved_at") or ""),
        })
    return {"presets": presets}


@app.get("/api/parameter-presets/{name}")
def load_parameter_preset(name: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    path = _parameter_preset_path(int(user["id"]), name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="未找到已保存的参数")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="参数文件无法读取") from exc
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        raise HTTPException(status_code=500, detail="参数文件格式无效")
    # Keep handwritten script text independent from form-schema migrations.
    # Older presets only have parameters.script, while newer ones also store a
    # dedicated manual_script snapshot for reliable restore.
    manual_script = payload.get("manual_script")
    if isinstance(manual_script, str):
        parameters = {**parameters, "script": manual_script}
    return {"name": str(payload.get("name") or path.stem), "parameters": parameters}


@app.delete("/api/parameter-presets/{name}")
def delete_parameter_preset(name: str, request: Request) -> dict[str, Any]:
    """Delete one of the current user's saved UI parameter presets."""
    user = require_user(request)
    normalized_name = normalize_project_name(name)
    path = _parameter_preset_path(int(user["id"]), normalized_name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="未找到已保存的参数")
    try:
        path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail="参数文件删除失败") from exc
    return {"ok": True, "name": normalized_name, "message": f"已删除参数：{normalized_name}"}


@app.put("/api/parameter-presets")
def save_parameter_preset(payload: ParameterPresetRequest, request: Request) -> dict[str, Any]:
    user = require_user(request)
    allowed_fields = set(GenerateRequest.model_fields) - {"api_key"}
    raw = {key: value for key, value in payload.parameters.items() if key in allowed_fields}
    try:
        parameters = GenerateRequest(**raw).model_dump(exclude={"api_key"})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"参数格式不正确：{exc}") from exc
    name = normalize_project_name(payload.name)
    parameters["project_name"] = name
    document = {
        "version": 2,
        "name": name,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "manual_script": str(payload.parameters.get("manual_script") or raw.get("script") or ""),
        "parameters": parameters,
    }
    path = _parameter_preset_path(int(user["id"]), name)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "name": name, "message": f"参数已保存：{name}"}


@app.get("/api/agent-prompt-presets")
def list_agent_prompt_presets(request: Request) -> dict[str, Any]:
    user = require_user(request)
    presets: list[dict[str, Any]] = []
    _ensure_default_agent_prompt_presets()
    for content_mode, defaults in DEFAULT_AGENT_PROMPT_PRESETS.items():
        path = _default_agent_prompt_preset_path(content_mode)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("visual_prompt_system") or "").strip():
            presets.append({
                "key": f"default:{content_mode}",
                "name": str(payload.get("name") or defaults["name"]),
                "kind": "default",
                "saved_at": str(payload.get("saved_at") or ""),
            })
    for path in sorted(_agent_prompt_preset_dir(int(user["id"])).glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("visual_prompt_system") or "").strip():
            presets.append({"key": f"user:{path.stem}", "name": str(payload.get("name") or path.stem), "kind": "user", "saved_at": str(payload.get("saved_at") or "")})
    return {"presets": presets}


@app.get("/api/agent-prompt-presets/{name}")
def load_agent_prompt_preset(name: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    _ensure_default_agent_prompt_presets()
    if name.startswith("default:"):
        content_mode = name.split(":", 1)[1]
        if content_mode not in DEFAULT_AGENT_PROMPT_PRESETS:
            raise HTTPException(status_code=404, detail="未找到默认 Agent 提示词")
        path = _default_agent_prompt_preset_path(content_mode)
    else:
        path = _agent_prompt_preset_path(int(user["id"]), name.removeprefix("user:"))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="未找到已保存的 Agent 提示词")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Agent 提示词文件无法读取") from exc
    prompt = str(payload.get("visual_prompt_system") or "").strip()
    if not prompt:
        raise HTTPException(status_code=500, detail="Agent 提示词文件格式无效")
    return {
        "name": str(payload.get("name") or path.stem),
        "visual_prompt_system": prompt,
        "agent0_prompt_system": str(payload.get("agent0_prompt_system") or "").strip(),
        "agent1_prompt_system": str(payload.get("agent1_prompt_system") or "").strip(),
        "agent2_director_theme": str(payload.get("agent2_director_theme") or "").strip(),
        "content_mode": str(payload.get("content_mode") or "").strip(),
    }


@app.put("/api/agent-prompt-presets")
def save_agent_prompt_preset(payload: AgentPromptPresetRequest, request: Request) -> dict[str, Any]:
    user = require_user(request)
    name = normalize_project_name(payload.name)
    document = {
        "version": 1,
        "name": name,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "visual_prompt_system": payload.visual_prompt_system.strip(),
        "agent0_prompt_system": str(payload.agent0_prompt_system or "").strip(),
        "agent1_prompt_system": str(payload.agent1_prompt_system or "").strip(),
        "agent2_director_theme": str(payload.agent2_director_theme or "").strip(),
        "content_mode": str(payload.content_mode or "").strip(),
    }
    _agent_prompt_preset_path(int(user["id"]), name).write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "key": f"user:{name}", "name": name, "message": f"Agent 提示词已保存：{name}"}


@app.post("/api/jobs")
def create_job(payload: GenerateRequest, request: Request) -> dict[str, Any]:
    user = require_user(request)
    data = payload.model_dump()
    script_length = len(str(data.get("script") or ""))
    if script_length > MAX_SCRIPT_CHARACTERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"单次文案最多 {MAX_SCRIPT_CHARACTERS:,} 个字符（当前 {script_length:,}）。"
                "请按完整章节拆分后分批生成，再自行拼接成片。"
            ),
        )
    data["project_name"] = normalize_project_name(data.get("project_name"))
    if data.get("visual_prompt_mode") != "full":
        data["agent0_prompt_system"] = None
        data["agent1_prompt_system"] = None
    if data.get("visual_prompt_mode") == "simple":
        if not str(data.get("global_character_prompt") or "").strip() and str(data.get("content_mode") or "") != CONTENT_MODE_GENERAL:
            data["global_character_prompt"] = (
                SCIENCE_GLOBAL_CHARACTER_PROMPT
                if str(data.get("content_mode") or "") == CONTENT_MODE_SCIENCE
                else DEFAULT_GLOBAL_CHARACTER_PROMPT
            )
        data["visual_prompt_system"] = build_visual_prompt_system(
            str(data.get("visual_style_prompt") or ""),
            str(data.get("content_mode") or CONTENT_MODE_STORY),
            str(data.get("global_character_prompt") or ""),
        )
    script = str(data.get("script") or "").strip()
    if data.get("module1_only"):
        data["skip_tts"] = False
        data["skip_text_correction"] = False
        data["source_audio_id"] = None
    if data.get("subtitle_only"):
        data["module1_only"] = False
        data["skip_tts"] = True
        if not data.get("source_audio_id"):
            raise HTTPException(status_code=400, detail="字幕识别需要先上传音频")
    if data.get("skip_tts"):
        if not data.get("source_audio_id"):
            raise HTTPException(status_code=400, detail="请先上传已有配音")
        if not script:
            data["skip_text_correction"] = True
    elif len(script) < 5:
        raise HTTPException(status_code=400, detail="请输入至少 5 个字的口播文案")
    elif data.get("tts_engine") == "qwen" and not os.getenv("DASHSCOPE_API_KEY", "").strip():
        raise HTTPException(status_code=400, detail="Qwen-TTS 尚未配置 API Key，请先在语音参数中保存 DASHSCOPE_API_KEY")
    elif data.get("tts_engine") == "qwen" and str(data.get("qwen_tts_instructions") or "").strip() and not voice_supports_instructions(str(data.get("qwen_tts_voice") or "")):
        raise HTTPException(status_code=400, detail="所选 Qwen 系统音色仅支持基础合成；请清空配音描述，或改选支持配音描述的音色")
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
    if job.status not in {"failed", "cancelled", "waiting_confirmation"}:
        raise HTTPException(status_code=400, detail="只有失败、已停止或等待确认的任务可以继续")
    return store.resume(job)


@app.post("/api/jobs/{job_id}/retry-tts")
def retry_job_tts(job_id: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="job not found")
    try:
        return store.retry_tts(job)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    media_type = "video/mp4" if path.suffix.lower() == ".mp4" else None
    return FileResponse(str(path), media_type=media_type)


@app.post("/api/jobs/{job_id}/output-folder")
def open_job_output_folder(job_id: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="job not found")
    output_root = OUTPUT_DIR.resolve()
    project_dir: Path | None = None
    for asset in list_media_assets(user_id=int(user["id"]), generation_job_id=job_id):
        if str(asset.get("role") or "") != "project_output":
            continue
        try:
            stored_path = Path(str(asset.get("storage_path") or ""))
            candidate = (stored_path if stored_path.is_absolute() else PROJECT_ROOT / stored_path).resolve()
            relative = candidate.relative_to(output_root)
        except (OSError, ValueError):
            continue
        if relative.parts:
            possible_dir = output_root / relative.parts[0]
            if possible_dir.is_dir():
                project_dir = possible_dir
                break
    # Subtitle-only jobs created before output archiving was added have a valid
    # artifact but no project_output database row. Migrate them lazily when the
    # user asks to open their output folder, so historical jobs remain usable.
    if project_dir is None and bool(job.request.get("subtitle_only")):
        legacy_srt = JOBS_DIR / job_id / "artifacts" / "final_short.srt"
        if legacy_srt.is_file():
            project_dir = OUTPUT_DIR / job_id
            project_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy_srt, project_dir / "最终字幕.srt")
    if project_dir is None:
        raise HTTPException(status_code=404, detail="project output folder is not available yet")
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="open-folder is currently supported on Windows only")
    try:
        subprocess.Popen(["explorer.exe", str(project_dir)], close_fds=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not open output folder: {exc}") from exc
    return {"ok": True, "path": str(project_dir)}


@app.post("/api/jobs/{job_id}/step-mode/visual-preview-folder")
def open_step_mode_visual_preview_folder(job_id: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="job not found")
    folder = JOBS_DIR / job_id / "step_mode_preview_images"
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="visual preview images are not available yet")
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="open-folder is currently supported on Windows only")
    try:
        subprocess.Popen(["explorer.exe", str(folder)], close_fds=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not open visual preview folder: {exc}") from exc
    return {"ok": True, "path": str(folder)}


@app.get("/api/subtitle-fonts")
def list_subtitle_fonts(request: Request) -> dict[str, Any]:
    require_user(request)
    return {"fonts": system_subtitle_fonts()}


@app.get("/api/jobs/{job_id}/subtitle-rendered-video")
def get_subtitle_rendered_video(job_id: str, request: Request) -> FileResponse:
    user = require_user(request)
    job = store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="subtitle video not found")
    path = OUTPUT_DIR / job_id / "带字幕视频.mp4"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="subtitle video not found")
    return FileResponse(str(path), media_type="video/mp4", filename="带字幕视频.mp4")


@app.post("/api/jobs/{job_id}/subtitle-render")
def render_subtitle_video(job_id: str, payload: SubtitleRenderRequest, request: Request) -> dict[str, Any]:
    user = require_user(request)
    job = store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="job not found")
    if not bool(job.request.get("subtitle_only")):
        raise HTTPException(status_code=400, detail="仅模块 2 字幕任务支持独立添加字幕")
    if job.status not in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail="请等待当前字幕任务结束")
    source_id = str(job.request.get("source_audio_id") or "").strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="未找到原始音频或视频素材")
    source = user_upload_path(int(user["id"]), source_id)
    srt_path = OUTPUT_DIR / job_id / "最终字幕.srt"
    if not srt_path.is_file():
        srt_path = JOBS_DIR / job_id / "artifacts" / "final_short.srt"
    if not source.is_file() or not srt_path.is_file():
        raise HTTPException(status_code=404, detail="原始媒体或 SRT 字幕文件不存在")
    output = OUTPUT_DIR / job_id / "带字幕视频.mp4"
    store.update(job, status="running", step="subtitle_render", progress=5, message="正在准备添加字幕")

    def worker() -> None:
        try:
            store.log(job, "收到添加字幕请求：不重新识别、不重新校对，直接开始渲染")
            render_standalone_subtitle_video(
                job, store, source, srt_path, output,
                style_key=payload.style,
                font_name=payload.font_name,
            )
            artifacts = dict(job.artifacts)
            artifacts["subtitle_video"] = f"/api/jobs/{job.id}/subtitle-rendered-video"
            store.update(job, status="completed", step="completed", progress=100, message="字幕视频已生成", artifacts=artifacts)
            store.log(job, f"字幕添加完成：{output}")
        except GenerationCancelled:
            store.log(job, "字幕添加已停止")
        except Exception as exc:
            store.log(job, f"字幕添加失败：{type(exc).__name__}: {exc}")
            store.update(job, status="failed", step="failed", error=str(exc), message="字幕添加失败")

    threading.Thread(target=worker, daemon=True).start()
    return job.snapshot()


def _owned_completed_job(job_id: str, request: Request) -> tuple[Any, int]:
    user = require_user(request)
    job = store.get(job_id)
    if not job or job.user_id != int(user["id"]):
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "completed":
        raise HTTPException(status_code=409, detail="visual editing is available after a job completes")
    return job, int(user["id"])


@app.get("/api/jobs/{job_id}/visual-editor")
def get_visual_editor(job_id: str, request: Request) -> dict[str, Any]:
    _job, user_id = _owned_completed_job(job_id, request)
    try:
        return visual_editor.inspect(job_id, user_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail=f"visual editor data is unavailable: {exc}") from exc


@app.get("/api/jobs/{job_id}/visual-editor/status")
def get_visual_editor_status(job_id: str, request: Request) -> dict[str, Any]:
    _owned_completed_job(job_id, request)
    return visual_editor.status(job_id)


@app.get("/api/visual-editor/projects")
def list_visual_editor_projects(request: Request) -> dict[str, Any]:
    user = require_user(request)
    projects = []
    for item in visual_editor.projects(int(user["id"])):
        job = store.get(str(item["id"]))
        if job and job.status == "completed":
            projects.append(item)
    return {"projects": projects}


@app.get("/api/jobs/{job_id}/visual-images/{filename}")
def get_visual_editor_image(job_id: str, filename: str, request: Request) -> FileResponse:
    _job, user_id = _owned_completed_job(job_id, request)
    try:
        path = visual_editor.image_path(job_id, user_id, filename)
    except OSError as exc:
        raise HTTPException(status_code=404, detail="image not found") from exc
    return FileResponse(str(path), media_type="image/jpeg")


@app.post("/api/jobs/{job_id}/visual-editor/{macro_id}/redraw")
def redraw_visual_editor_image(
    job_id: str,
    macro_id: str,
    payload: VisualRedrawRequest,
    request: Request,
) -> dict[str, Any]:
    job, _user_id = _owned_completed_job(job_id, request)
    reference_upload_paths: list[str] = []
    for upload_id in payload.reference_upload_ids:
        try:
            source = upload_path(int(job.user_id), str(upload_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="重绘参考图不存在，请重新上传") from exc
        if source.suffix.lower() not in IMAGE_EXTENSIONS:
            raise HTTPException(status_code=400, detail="重绘参考图仅支持 JPG、JPEG、PNG 或 WebP")
        reference_upload_paths.append(str(source))
    visual_editor.redraw(
        job=job,
        prompt=payload.prompt.strip(),
        macro_id=macro_id,
        reference_macro_ids=payload.reference_macro_ids,
        reference_upload_paths=reference_upload_paths,
    )
    return {"ok": True, "message": "image redraw started"}


@app.post("/api/jobs/{job_id}/visual-editor/{macro_id}/upload")
async def upload_visual_editor_image(
    job_id: str,
    macro_id: str,
    request: Request,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    job, _user_id = _owned_completed_job(job_id, request)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="only JPG/JPEG replacement images are supported")
    data = await file.read()
    if not data or len(data) > 30 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="replacement image must be between 1 byte and 30 MB")
    upload_dir = JOBS_DIR / job.id / "visual_editor_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    source = upload_dir / f"{macro_id}_{int(time.time() * 1000)}{suffix}"
    source.write_bytes(data)
    visual_editor.upload(job=job, macro_id=macro_id, source=source)
    return {"ok": True, "message": "local image replacement started"}


@app.post("/api/jobs/{job_id}/visual-editor/{macro_id}/undo")
def undo_visual_editor_image(job_id: str, macro_id: str, request: Request) -> dict[str, Any]:
    _job, user_id = _owned_completed_job(job_id, request)
    try:
        visual_editor.undo(job_id=job_id, user_id=user_id, macro_id=macro_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/jobs/{job_id}/visual-editor/{macro_id}/reset-prompt")
def reset_visual_editor_prompt(job_id: str, macro_id: str, request: Request) -> dict[str, Any]:
    _job, user_id = _owned_completed_job(job_id, request)
    try:
        prompt = visual_editor.reset_prompt(job_id=job_id, user_id=user_id, macro_id=macro_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "prompt": prompt}


@app.post("/api/jobs/{job_id}/visual-editor/{macro_id}/commit-baseline")
def commit_visual_editor_baseline(
    job_id: str,
    macro_id: str,
    payload: VisualBaselineRequest,
    request: Request,
) -> dict[str, Any]:
    job, user_id = _owned_completed_job(job_id, request)
    try:
        visual_editor.commit_baseline(
            job=job,
            user_id=user_id,
            macro_id=macro_id,
            prompt=payload.prompt,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "message": f"{macro_id} 已确认为新的原图"}


@app.post("/api/jobs/{job_id}/visual-editor/commit-all-baselines")
def commit_all_visual_editor_baselines(job_id: str, request: Request) -> dict[str, Any]:
    job, user_id = _owned_completed_job(job_id, request)
    try:
        count = visual_editor.commit_all_baselines(job=job, user_id=user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "count": count, "message": f"已将当前 {count} 张图片确认为新的原图"}


@app.post("/api/jobs/{job_id}/visual-editor/{macro_id}/timing")
def adjust_visual_editor_timing(
    job_id: str,
    macro_id: str,
    payload: VisualTimingAdjustRequest,
    request: Request,
) -> dict[str, Any]:
    _job, user_id = _owned_completed_job(job_id, request)
    try:
        return visual_editor.adjust_timing(
            job_id=job_id,
            user_id=user_id,
            macro_id=macro_id,
            action=payload.action,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/visual-editor/timing/reset")
def reset_visual_editor_timing(job_id: str, request: Request) -> dict[str, Any]:
    _job, user_id = _owned_completed_job(job_id, request)
    try:
        return visual_editor.reset_timing(job_id=job_id, user_id=user_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/visual-editor/timing/commit")
def commit_visual_editor_timing(job_id: str, request: Request) -> dict[str, Any]:
    _job, user_id = _owned_completed_job(job_id, request)
    try:
        return visual_editor.commit_timing_baseline(job_id=job_id, user_id=user_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/visual-editor/timing/history")
def restore_visual_editor_timing_history(
    job_id: str,
    payload: VisualTimingHistoryRequest,
    request: Request,
) -> dict[str, Any]:
    _job, user_id = _owned_completed_job(job_id, request)
    try:
        return visual_editor.restore_timing_history(
            job_id=job_id,
            user_id=user_id,
            history_id=payload.history_id,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/visual-editor/{macro_id}/timing/remove")
def remove_visual_editor_timing_picture(job_id: str, macro_id: str, request: Request) -> dict[str, Any]:
    _job, user_id = _owned_completed_job(job_id, request)
    try:
        return visual_editor.remove_timing_picture(job_id=job_id, user_id=user_id, macro_id=macro_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/visual-editor/render")
def render_visual_editor_video(job_id: str, payload: VisualRenderRequest, request: Request) -> dict[str, Any]:
    job, _user_id = _owned_completed_job(job_id, request)
    visual_editor.render_video(job=job, mode=payload.mode)
    return {"ok": True, "message": "module 5 render started"}


@app.post("/api/jobs/{job_id}/visual-editor/cancel")
def cancel_visual_editor_render(job_id: str, request: Request) -> dict[str, Any]:
    job, _user_id = _owned_completed_job(job_id, request)
    return visual_editor.cancel_render(job)


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
