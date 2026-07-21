import json
import os
import subprocess
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
from .pipeline import JOBS_DIR, OUTPUT_DIR, PROJECT_ROOT, normalize_project_name, store
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
from story_agents import GENERAL_AGENT_SYSTEM_PROMPT, SCIENCE_AGENT_SYSTEM_PROMPT, STORY_AGENT_SYSTEM_PROMPT


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
    visual_prompt_mode: Literal["simple", "full"] = "simple"
    visual_pacing_preset: Literal["auto", "slow", "standard", "fast", "custom"] = "auto"
    visual_min_duration: float | None = Field(default=None, ge=4, le=20)
    visual_target_duration: float | None = Field(default=None, ge=5, le=30)
    visual_max_duration: float | None = Field(default=None, ge=6, le=40)
    visual_max_slides: int | None = Field(default=None, ge=1, le=12)
    visual_style_prompt: str | None = Field(default=None, max_length=1000)
    global_character_prompt: str | None = Field(default=None, max_length=2000)
    story_environment_prompt: str | None = Field(default=None, max_length=2000)
    visual_prompt_system: str | None = Field(default=None, max_length=4000)
    agent1_prompt_system: str | None = Field(default=None, max_length=12000)


class ParameterPresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parameters: dict[str, Any]


class AgentPromptPresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    visual_prompt_system: str = Field(min_length=1, max_length=4000)
    agent1_prompt_system: str | None = Field(default=None, max_length=12000)
    agent2_director_theme: str | None = Field(default=None, max_length=40)
    content_mode: Literal["urban_suspense", "science_explainer", "general"] | None = None


class VisualRedrawRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)


class VisualRenderRequest(BaseModel):
    mode: Literal["subtitles", "raw", "both"] = "both"


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
    common_api_key: str | None = Field(default=None, max_length=2048)
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
                    "default_agent1_system": STORY_AGENT_SYSTEM_PROMPT,
                },
                CONTENT_MODE_SCIENCE: {
                    "label": "口播科普",
                    "description": "红围巾短发少女的清晰科教漫画",
                    "default_style": SCIENCE_VISUAL_STYLE,
                    "default_character": SCIENCE_GLOBAL_CHARACTER_PROMPT,
                    "default_system": SCIENCE_VISUAL_PROMPT_SYSTEM,
                    "default_agent1_system": SCIENCE_AGENT_SYSTEM_PROMPT,
                },
                CONTENT_MODE_GENERAL: {
                    "label": "通用自定义",
                    "description": "自行决定画风与人物形象的通用视频模式",
                    "default_style": GENERAL_VISUAL_STYLE,
                    "default_character": "",
                    "default_system": GENERAL_VISUAL_PROMPT_SYSTEM,
                    "default_agent1_system": GENERAL_AGENT_SYSTEM_PROMPT,
                },
            },
        },
    }


def _api_key_status() -> dict[str, Any]:
    values = _parse_env_lines(PROJECT_ROOT / ".env")
    return {
        "language": {"configured": bool(values.get("GEMINI_API_KEY", "").strip())},
        "image": {"configured": bool(values.get("RUNNINGHUB_API_KEY", "").strip())},
        "common": {"configured": bool(values.get("APP_COMMON_API_KEY", "").strip())},
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
    common = str(payload.common_api_key or "").strip()
    qwen_tts = str(payload.qwen_tts_api_key or "").strip()
    if not any((language, image, common, qwen_tts)):
        raise HTTPException(status_code=400, detail="请至少填写一个 API Key")
    if any("\n" in value or "\r" in value for value in (language, image, common, qwen_tts)):
        raise HTTPException(status_code=400, detail="API Key 不能包含换行")

    # A common RunningHub-style key fills either dedicated field left blank.
    updates: dict[str, str] = {}
    if common:
        updates["APP_COMMON_API_KEY"] = common
    if language or common:
        updates["GEMINI_API_KEY"] = language or common
    if image or common:
        updates["RUNNINGHUB_API_KEY"] = image or common
    if qwen_tts:
        updates["DASHSCOPE_API_KEY"] = qwen_tts
    try:
        save_project_env_values(updates)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"保存 API Key 失败: {exc}") from exc
    return {
        "keys": _api_key_status(),
        "message": "API Key 已保存到本机 .env；通用 Key 已自动补全未单独填写的模型。",
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
        "agent1_prompt_system": STORY_AGENT_SYSTEM_PROMPT,
        "agent2_director_theme": "惊悚漫画",
    },
    CONTENT_MODE_SCIENCE: {
        "name": "口播科普",
        "visual_prompt_system": SCIENCE_VISUAL_PROMPT_SYSTEM,
        "agent1_prompt_system": SCIENCE_AGENT_SYSTEM_PROMPT,
        "agent2_director_theme": "科普科技口播视频",
    },
    CONTENT_MODE_GENERAL: {
        "name": "通用自定义",
        "visual_prompt_system": GENERAL_VISUAL_PROMPT_SYSTEM,
        "agent1_prompt_system": GENERAL_AGENT_SYSTEM_PROMPT,
        "agent2_director_theme": "通用视频",
    },
}


def _default_agent_prompt_preset_path(content_mode: str) -> Path:
    return _default_agent_prompt_preset_dir() / f"{content_mode}.json"


def _ensure_default_agent_prompt_presets() -> None:
    for content_mode, defaults in DEFAULT_AGENT_PROMPT_PRESETS.items():
        path = _default_agent_prompt_preset_path(content_mode)
        if path.exists():
            continue
        document = {
            "version": 1,
            "kind": "default",
            "content_mode": content_mode,
            "name": defaults["name"],
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "visual_prompt_system": defaults["visual_prompt_system"],
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
    return {"name": str(payload.get("name") or path.stem), "parameters": parameters}


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
        "version": 1,
        "name": name,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
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
    data["project_name"] = normalize_project_name(data.get("project_name"))
    if data.get("visual_prompt_mode") != "full":
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
    if project_dir is None:
        raise HTTPException(status_code=404, detail="project output folder is not available yet")
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="open-folder is currently supported on Windows only")
    try:
        subprocess.Popen(["explorer.exe", str(project_dir)], close_fds=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not open output folder: {exc}") from exc
    return {"ok": True, "path": str(project_dir)}


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
    visual_editor.redraw(job=job, prompt=payload.prompt.strip(), macro_id=macro_id)
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
