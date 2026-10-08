"""Offline model installation inventory shared by the portable UI and pack tools."""
from pathlib import Path
import os
import hashlib
import threading

from fastapi import HTTPException

from . import managed_comfyui as engine
from .indextts25_local import load_indextts25_config, REQUIRED_MODEL_FILES

_VERIFY_LOCK = threading.Lock()
_VERIFY = {"running": False, "profile_id": "", "items": []}


def verification_status():
    with _VERIFY_LOCK:
        return {**_VERIFY, "items": [dict(item) for item in _VERIFY["items"]]}


def verify_models(profile_id=""):
    """Explicit, background checksum check; never hash large weights on startup."""
    info = installation("comfyui", profile_id)
    with _VERIFY_LOCK:
        if _VERIFY["running"]:
            raise HTTPException(409, "已有完整校验正在进行，请等待完成")
        _VERIFY.update(running=True, profile_id=profile_id, items=[])

    def run():
        try:
            for item in info["items"]:
                row = {"name": item["name"], "state": "checking"}
                with _VERIFY_LOCK:
                    _VERIFY["items"].append(row)
                try:
                    if not item["ready"]:
                        outcome = "missing"
                    elif not item.get("sha256"):
                        outcome = "no_checksum"
                    else:
                        path = Path(item["found_path"])
                        before = path.stat()
                        digest = hashlib.sha256()
                        with path.open("rb") as stream:
                            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                                digest.update(block)
                        after = path.stat()
                        outcome = "passed" if digest.hexdigest() == item["sha256"] else "mismatch"
                        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                            outcome = "changed"
                except OSError:
                    outcome = "unreadable"
                with _VERIFY_LOCK:
                    row["state"] = outcome
        finally:
            with _VERIFY_LOCK:
                _VERIFY["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return verification_status()


def installation(kind="comfyui", profile_id=""):
    root = engine.PROJECT / "models"
    if kind == "tts":
        config = load_indextts25_config()
        required = REQUIRED_MODEL_FILES
        items = []
        for relative in required:
            path = config.model_dir / relative
            size = path.stat().st_size if path.is_file() else 0
            items.append({"path": relative, "name": Path(relative).name, "ready": size > 0,
                          "bytes": 0, "actual_bytes": size, "install_path": str(path),
                          "found_path": str(path) if size else "", "download_url": "https://huggingface.co/IndexTeam/IndexTTS-2.5/tree/main" if not relative.startswith("hf_cache/") else ""})
        return {"kind": kind, "title": "IndexTTS-2.5 本地配音", "directory": str(config.model_dir),
                "default_directory": str(root / "tts" / "indextts25"), "items": items,
                "ready": all(x["ready"] for x in items), "runtime_missing": config.missing_runtime_resources(),
                "profiles": [], "message": "将 OCV 配套 TTS 模型包解压到此目录，保留 hf_cache 和 qwen0.6bemo4-merge 子目录。仅下载主模型仓库还需要补齐辅助模型；推荐使用完整的配套模型包。"}
    if kind != "comfyui":
        raise HTTPException(400, "未知模型类型")
    release, manifest = engine.runtime_info()
    profiles = engine.builtin_profiles()
    selected = next((p for p in profiles if p["id"] == profile_id), None)
    if profile_id and not selected:
        raise HTTPException(404, "内置工作流不存在，请刷新工作流列表")
    catalog = engine.read_json(Path(__file__).with_name("model_sources.json"), {})
    items = engine.model_inventory(manifest)
    if selected and selected.get("required_models") is not None:
        items = [x for x in items if x["path"] in selected["required_models"]]
    for item in items:
        item.update(catalog.get(item["name"], {}))
        item["used_by"] = [p["name"] for p in profiles if item["path"] in p.get("required_models", [])]
    return {"kind": kind, "title": selected["name"] if selected else "内置 ComfyUI 模型",
            "verification": verification_status(),
            "directory": str(root / "comfyui"), "default_directory": str(root / "comfyui"),
            "items": items, "ready": bool(items) and all(x["ready"] for x in items),
            "runtime_missing": [] if release else ["内置 ComfyUI 引擎组件"],
            "profiles": [{"id": p["id"], "name": p["name"]} for p in profiles],
            "message": "选择要用的工作流，只需补齐它的模型。下载后保持文件名与目录一致，也可将配套 models 文件夹合并到 OCV 根目录。"}


def open_folder(kind, profile_id="", relative=""):
    info = installation(kind, profile_id)
    if relative:
        item = next((x for x in info["items"] if x["path"] == relative), None)
        if item is None:
            raise HTTPException(400, "模型不在当前清单中")
        folder = Path(item["install_path"]).parent
    else:
        folder = Path(info["directory"])
    if os.name != "nt":
        raise HTTPException(400, "请按显示的路径手动打开文件夹")
    folder.mkdir(parents=True, exist_ok=True)
    os.startfile(str(folder))
    return {"path": str(folder)}
