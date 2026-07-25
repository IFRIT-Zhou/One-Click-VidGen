"""Post-production editor for replacing a completed job's generated images."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .db import list_media_assets
from .pipeline import JOBS_DIR, OUTPUT_DIR, PROJECT_ROOT, register_job_asset


# Reference images and local replacements share the same supported formats as
# the main image-reference workflow.  Image2 accepts PNG/WebP as well as JPG.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAPPING_FILENAME = "画面映射.json"
TIMELINE_FILENAME = "画面时间线.json"
MANIFEST_FILENAME = "画面修改清单.json"
HTML_FILENAME = "最终画面.html"
SUBTITLE_FILENAME = "最终字幕.srt"
TIMING_BACKUP_FILENAME = "画面映射.初始时序.json"


class VisualEditor:
    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._image_tasks: dict[str, dict[str, dict[str, Any]]] = {}
        self._render_processes: dict[str, subprocess.Popen[str]] = {}
        self._render_cancelled: set[str] = set()
        self._lock = threading.Lock()
        self._mapping_lock = threading.Lock()

    @staticmethod
    def output_dir(job_id: str, user_id: int) -> Path:
        root = OUTPUT_DIR.resolve()
        for asset in list_media_assets(user_id=user_id, generation_job_id=job_id):
            if str(asset.get("role") or "") != "project_output":
                continue
            stored = Path(str(asset.get("storage_path") or ""))
            candidate = (stored if stored.is_absolute() else PROJECT_ROOT / stored).resolve()
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            if relative.parts:
                directory = root / relative.parts[0]
                if directory.is_dir():
                    return directory
        raise FileNotFoundError("project output folder is not available")

    def projects(self, user_id: int) -> list[dict[str, Any]]:
        root = OUTPUT_DIR.resolve()
        job_by_folder: dict[str, str] = {}
        for asset in list_media_assets(user_id=user_id):
            if str(asset.get("role") or "") != "project_output":
                continue
            stored = Path(str(asset.get("storage_path") or ""))
            candidate = (stored if stored.is_absolute() else PROJECT_ROOT / stored).resolve()
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            if relative.parts and asset.get("generation_job_id"):
                job_by_folder.setdefault(relative.parts[0], str(asset["generation_job_id"]))
        projects: list[dict[str, Any]] = []
        if not root.is_dir():
            return projects
        for directory in sorted((path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")), key=lambda path: path.stat().st_mtime, reverse=True):
            job_id = job_by_folder.get(directory.name)
            if job_id:
                self._ensure_editor_support_files(job_id, directory)
                projects.append({"id": job_id, "name": directory.name, "editable": True})
        return projects

    @staticmethod
    def _ensure_editor_support_files(job_id: str, output_dir: Path) -> None:
        """Migrate old job artifacts once; all normal editing then uses output only."""
        other_dir = output_dir / "other"
        other_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir = JOBS_DIR / job_id / "artifacts"
        for source_name, target_name in (
            ("poster_mapping.json", MAPPING_FILENAME),
            ("fine_grained_timeline.json", TIMELINE_FILENAME),
        ):
            source = artifact_dir / source_name
            target = other_dir / target_name
            if source.is_file() and not target.is_file():
                shutil.copy2(source, target)
        (other_dir / MANIFEST_FILENAME).write_text(
            json.dumps({"job_id": job_id, "project_name": output_dir.name}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _mapping_path(project_dir: Path) -> Path:
        return project_dir / "other" / MAPPING_FILENAME

    @staticmethod
    def _timeline_path(project_dir: Path) -> Path:
        return project_dir / "other" / TIMELINE_FILENAME

    @staticmethod
    def _timing_backup_path(project_dir: Path) -> Path:
        """The original sentence-to-picture allocation, kept outside the workspace."""
        return project_dir / "other" / TIMING_BACKUP_FILENAME

    @staticmethod
    def _load_mapping(project_dir: Path) -> list[dict[str, Any]]:
        path = VisualEditor._mapping_path(project_dir)
        if not path.is_file():
            return VisualEditor._rebuild_mapping_from_output(project_dir)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("poster mapping is invalid")
        return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def _legacy_text_by_image(project_dir: Path) -> dict[str, str]:
        """Recover per-image text from the self-contained HTML + SRT of old outputs."""
        html_path = project_dir / "other" / HTML_FILENAME
        srt_path = project_dir / "other" / SUBTITLE_FILENAME
        if not html_path.is_file() or not srt_path.is_file():
            return {}
        try:
            html = html_path.read_text(encoding="utf-8")
            match = re.search(r"const posterTimeline\s*=\s*(\[.*?\]);\s*let subtitleData", html, re.S)
            posters = json.loads(match.group(1)) if match else []
            srt = srt_path.read_text(encoding="utf-8")
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

        def as_seconds(value: str) -> float:
            hours, minutes, tail = value.strip().split(":")
            seconds, milliseconds = tail.replace(".", ",").split(",")
            return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000

        subtitles: list[tuple[float, float, str]] = []
        for block in re.split(r"\n\s*\n", srt.strip()):
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            timing_index = next((index for index, line in enumerate(lines) if "-->" in line), -1)
            if timing_index < 0:
                continue
            try:
                start_raw, end_raw = lines[timing_index].split("-->", 1)
                subtitles.append((as_seconds(start_raw), as_seconds(end_raw), " ".join(lines[timing_index + 1:])))
            except (ValueError, IndexError):
                continue
        result: dict[str, str] = {}
        for poster in posters if isinstance(posters, list) else []:
            if not isinstance(poster, dict):
                continue
            filename = Path(str(poster.get("url") or "")).name
            if not filename:
                continue
            try:
                start, end = float(poster.get("start") or 0), float(poster.get("end") or 0)
            except (TypeError, ValueError):
                continue
            text = " ".join(item[2] for item in subtitles if item[1] > start and item[0] < end).strip()
            if text:
                result[filename] = text
        return result

    @staticmethod
    def _save_mapping(project_dir: Path, mapping: list[dict[str, Any]]) -> None:
        path = VisualEditor._mapping_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _load_timeline(project_dir: Path) -> list[dict[str, Any]]:
        path = VisualEditor._timeline_path(project_dir)
        if not path.is_file():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict) and str(item.get("slide_id") or "")]

    @staticmethod
    def _validate_timing_partition(mapping: list[dict[str, Any]], timeline: list[dict[str, Any]]) -> list[str]:
        """Require a complete, ordered subtitle partition before moving a boundary.

        A timing edit moves one subtitle sentence across the boundary of two adjacent
        pictures.  Rejecting imperfect legacy mappings keeps the result gap-free and
        prevents an accidental black frame in module 5.
        """
        expected = [str(item.get("slide_id") or "") for item in timeline]
        expected = [item for item in expected if item]
        actual: list[str] = []
        for item in mapping:
            slides = [str(value) for value in item.get("includes_slides", []) if str(value)]
            if not slides:
                raise ValueError("该项目的画面映射缺少字幕分组，暂不能安全调整时序。请使用新版任务重新生成一次视频。")
            actual.extend(slides)
        if not expected or actual != expected:
            raise ValueError("该项目的画面与字幕时间线不完整或顺序不一致，暂不能安全调整时序。")
        return expected

    @staticmethod
    def _timing_details(mapping: list[dict[str, Any]], timeline: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        by_slide = {str(item.get("slide_id") or ""): item for item in timeline}
        result: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(mapping):
            macro_id = str(item.get("macro_scene_id") or "")
            slides = [str(value) for value in item.get("includes_slides", []) if str(value) in by_slide]
            if not macro_id or not slides:
                continue
            first = by_slide[slides[0]]
            last = by_slide[slides[-1]]
            try:
                start, end = float(first.get("start", 0)), float(last.get("end", 0))
            except (TypeError, ValueError):
                start, end = 0.0, 0.0
            previous = mapping[index - 1] if index else None
            following = mapping[index + 1] if index + 1 < len(mapping) else None
            result[macro_id] = {
                "start": start,
                "end": end,
                "duration": max(0.0, end - start),
                "sentences": [
                    {
                        "slide_id": slide_id,
                        "start": float(by_slide[slide_id].get("start", 0) or 0),
                        "end": float(by_slide[slide_id].get("end", 0) or 0),
                        "text": str(by_slide[slide_id].get("text_content") or ""),
                    }
                    for slide_id in slides
                ],
                "can_extend_prev": bool(previous and len(previous.get("includes_slides", [])) > 1),
                "can_extend_next": bool(following and len(following.get("includes_slides", [])) > 1),
                "can_shrink_prev": bool(previous and len(slides) > 1),
                "can_shrink_next": bool(following and len(slides) > 1),
            }
        return result

    @staticmethod
    def _ensure_timing_backup(project_dir: Path, mapping: list[dict[str, Any]]) -> None:
        path = VisualEditor._timing_backup_path(project_dir)
        if not path.exists():
            path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _recover_timing_from_html(project_dir: Path, mapping: list[dict[str, Any]], timeline: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """Recover legacy slide groups from the self-contained rendered HTML.

        Earlier output packages sometimes archived prompts but accidentally omitted
        ``includes_slides``.  The exported HTML still contains the authoritative
        poster start/end ranges, so it can be converted back to sentence groups
        without regenerating images or involving either Agent.
        """
        html_path = project_dir / "other" / HTML_FILENAME
        if not html_path.is_file() or not mapping or not timeline:
            return None
        try:
            html = html_path.read_text(encoding="utf-8")
            match = re.search(r"const posterTimeline\s*=\s*(\[.*?\]);\s*let subtitleData", html, re.S)
            posters = json.loads(match.group(1)) if match else []
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(posters, list) or len(posters) != len(mapping):
            return None
        recovered = [dict(item) for item in mapping]
        expected = [str(item.get("slide_id") or "") for item in timeline]
        for entry, poster in zip(recovered, posters, strict=True):
            if not isinstance(poster, dict):
                return None
            macro_id = str(entry.get("macro_scene_id") or "")
            filename = Path(str(poster.get("url") or "")).name
            # Mapping and generated image filenames use the same poster_NNN prefix.
            if not macro_id or not filename.startswith(macro_id):
                return None
            try:
                start, end = float(poster["start"]), float(poster["end"])
            except (KeyError, TypeError, ValueError):
                return None
            slides = [
                str(scene.get("slide_id") or "")
                for scene in timeline
                if float(scene.get("end", 0) or 0) > start and float(scene.get("start", 0) or 0) < end
            ]
            if not slides:
                return None
            entry["includes_slides"] = slides
        actual = [str(slide) for entry in recovered for slide in entry.get("includes_slides", [])]
        return recovered if actual == expected else None

    @classmethod
    def _mapping_with_recovered_timing(cls, project_dir: Path, mapping: list[dict[str, Any]], timeline: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        try:
            cls._validate_timing_partition(mapping, timeline)
            return mapping, False
        except ValueError:
            recovered = cls._recover_timing_from_html(project_dir, mapping, timeline)
            if recovered is None:
                raise
            cls._validate_timing_partition(recovered, timeline)
            return recovered, True

    @staticmethod
    def _write_timing_html(project_dir: Path, mapping: list[dict[str, Any]], timeline: list[dict[str, Any]]) -> None:
        """Regenerate only module 4's HTML timeline; pictures are never regenerated here."""
        VisualEditor._validate_timing_partition(mapping, timeline)
        scenes_by_id = {str(scene.get("slide_id") or ""): scene for scene in timeline}
        poster_timeline: list[dict[str, Any]] = []
        image_dir = project_dir / "image"
        for item in mapping:
            macro_id = str(item.get("macro_scene_id") or "")
            image = VisualEditor._find_image(image_dir, macro_id)
            included = [scenes_by_id[str(slide_id)] for slide_id in item.get("includes_slides", [])]
            poster_timeline.append({
                "start": min(float(scene["start"]) for scene in included),
                "end": max(float(scene["end"]) for scene in included),
                "url": f"../image/{image.name}",
            })
        import module4_video_render as visual
        visual.write_html(
            timeline,
            poster_timeline,
            html_path=project_dir / "other" / HTML_FILENAME,
            audio_url="../input/配音.wav",
        )

    @staticmethod
    def _rebuild_mapping_from_output(project_dir: Path) -> list[dict[str, Any]]:
        """Best-effort compatibility for old outputs whose job workspace was removed."""
        mapping: list[dict[str, Any]] = []
        for image in sorted((project_dir / "image").glob("*")):
            if image.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            macro_id = re.sub(r"_[0-9a-f]{8,}$", "", image.stem, flags=re.IGNORECASE)
            prompt_path = image.with_suffix(".txt")
            mapping.append({
                "macro_scene_id": macro_id,
                "image_prompt": prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else "",
                "includes_slides": [],
            })
        VisualEditor._save_mapping(project_dir, mapping)
        return mapping

    @staticmethod
    def _find_image(image_dir: Path, macro_id: str) -> Path:
        matches = [path for path in image_dir.glob(f"{macro_id}*") if path.suffix.lower() in IMAGE_EXTENSIONS]
        if not matches:
            raise FileNotFoundError(f"image for {macro_id} is missing")
        return sorted(matches)[0]

    @staticmethod
    def _backup_dir(project_dir: Path) -> Path:
        # Keep versions alongside each exported project so users can find and copy
        # them directly from output, rather than hiding them in a dot-folder.
        path = project_dir / "重绘备份"
        legacy_path = project_dir / ".visual_editor_backups"
        if legacy_path.is_dir() and not path.exists():
            shutil.move(str(legacy_path), str(path))
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def _backup_current(cls, project_dir: Path, image: Path, macro_id: str) -> None:
        backup_dir = cls._backup_dir(project_dir)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        shutil.copy2(image, backup_dir / f"{macro_id}.{stamp}{image.suffix.lower()}")
        prompt_path = image.with_suffix(".txt")
        if prompt_path.is_file():
            shutil.copy2(prompt_path, backup_dir / f"{macro_id}.{stamp}.txt")
        original_image = backup_dir / f"{macro_id}.original{image.suffix.lower()}"
        original_prompt = backup_dir / f"{macro_id}.original.txt"
        if not original_image.exists():
            shutil.copy2(image, original_image)
        if prompt_path.is_file() and not original_prompt.exists():
            shutil.copy2(prompt_path, original_prompt)

    def inspect(self, job_id: str, user_id: int) -> dict[str, Any]:
        project_dir = self.output_dir(job_id, user_id)
        # Transparently migrate old hidden version folders when a project is opened.
        self._backup_dir(project_dir)
        image_dir = project_dir / "image"
        legacy_text_by_image = self._legacy_text_by_image(project_dir)
        timeline_items = self._load_timeline(project_dir)
        timeline = {str(item.get("slide_id") or ""): item for item in timeline_items}
        mapping = self._load_mapping(project_dir)
        try:
            mapping, recovered_timing = self._mapping_with_recovered_timing(project_dir, mapping, timeline_items)
            timing_by_macro = self._timing_details(mapping, timeline_items)
            timing_available = bool(timing_by_macro)
            timing_message = (
                "已从该项目成片恢复原始画面时序；首次调整时会保存到项目映射中。"
                if recovered_timing else "按字幕句子调整画面边界；不会产生空白或重叠。"
            )
        except ValueError as exc:
            timing_by_macro = {}
            timing_available = False
            timing_message = str(exc)
        items: list[dict[str, Any]] = []
        for entry in mapping:
            macro_id = str(entry.get("macro_scene_id") or "")
            if not macro_id:
                continue
            try:
                image = self._find_image(image_dir, macro_id)
            except FileNotFoundError:
                continue
            slides = [str(value) for value in entry.get("includes_slides", [])]
            text = " ".join(str(timeline.get(value, {}).get("text_content") or "") for value in slides).strip()
            if not text:
                text = legacy_text_by_image.get(image.name, "")
            items.append({
                "id": macro_id,
                "prompt": str(entry.get("image_prompt") or ""),
                "slides": slides,
                "text": text,
                "image_url": f"/api/jobs/{job_id}/visual-images/{image.name}?v={image.stat().st_mtime_ns}",
                "timing": timing_by_macro.get(macro_id),
            })
        with self._lock:
            task = dict(self._tasks.get(job_id) or {"status": "idle", "message": ""})
            image_tasks = {
                macro_id: dict(value)
                for macro_id, value in self._image_tasks.get(job_id, {}).items()
            }
        for item in items:
            item["task"] = image_tasks.get(item["id"], {"status": "idle", "message": ""})
        return {
            "items": items,
            "task": task,
            "image_tasks": image_tasks,
            "has_active_image_tasks": any(value.get("status") == "running" for value in image_tasks.values()),
            "timing_available": timing_available,
            "timing_message": timing_message,
            "project_dir": str(project_dir),
            "version": int(time.time() * 1000),
        }

    def status(self, job_id: str) -> dict[str, Any]:
        """Lightweight task state for the UI; does not reload the image grid."""
        with self._lock:
            task = dict(self._tasks.get(job_id) or {"status": "idle", "message": ""})
            image_tasks = {
                macro_id: dict(value)
                for macro_id, value in self._image_tasks.get(job_id, {}).items()
            }
        return {
            "task": task,
            "image_tasks": image_tasks,
            "has_active_image_tasks": any(value.get("status") == "running" for value in image_tasks.values()),
        }

    @staticmethod
    def image_path(job_id: str, user_id: int, filename: str) -> Path:
        if Path(filename).name != filename:
            raise FileNotFoundError("invalid image")
        path = (VisualEditor.output_dir(job_id, user_id) / "image" / filename).resolve()
        image_root = (VisualEditor.output_dir(job_id, user_id) / "image").resolve()
        if image_root not in path.parents or not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise FileNotFoundError("image not found")
        return path

    def _set_task(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            task = dict(self._tasks.get(job_id) or {})
            task.update(changes)
            task["updated_at"] = time.time()
            self._tasks[job_id] = task

    def _set_image_task(self, job_id: str, macro_id: str, **changes: Any) -> None:
        with self._lock:
            task = dict(self._image_tasks.setdefault(job_id, {}).get(macro_id) or {})
            task.update(changes)
            task["updated_at"] = time.time()
            self._image_tasks[job_id][macro_id] = task

    def _start_image_task(self, job: Any, macro_id: str, action: str, message: str) -> None:
        with self._lock:
            current = self._image_tasks.setdefault(job.id, {}).get(macro_id) or {}
            if current.get("status") == "running":
                raise RuntimeError(f"{macro_id} is already being processed")
        self._set_image_task(job.id, macro_id, status="running", action=action, message=message)

    @staticmethod
    def _log(job: Any, message: str) -> None:
        from .pipeline import store
        store.log(job, f"[画面修改] {message}")

    def redraw(
        self,
        *,
        job: Any,
        prompt: str,
        macro_id: str,
        reference_macro_ids: list[str] | None = None,
        reference_upload_paths: list[str] | None = None,
    ) -> None:
        reference_macro_ids = list(dict.fromkeys(str(value) for value in (reference_macro_ids or []) if str(value)))[:1]
        reference_upload_paths = list(dict.fromkeys(str(value) for value in (reference_upload_paths or []) if str(value)))[:3]
        self._start_image_task(job, macro_id, "redraw", "正在调用 Image2 重绘图片")
        reference_count = len(reference_macro_ids) + len(reference_upload_paths)
        reference_note = f"，使用 {reference_count} 张参考图" if reference_count else ""
        self._log(job, f"开始重绘 {macro_id}{reference_note}，请等待 Image2 返回图片。")

        def work() -> None:
            try:
                project_dir = self.output_dir(job.id, int(job.user_id))
                image = self._find_image(project_dir / "image", macro_id)
                reference_paths = [
                    str(self._find_image(project_dir / "image", reference_id))
                    for reference_id in reference_macro_ids
                ]
                if reference_upload_paths:
                    reference_dir = project_dir / "other" / "reference_images"
                    reference_dir.mkdir(parents=True, exist_ok=True)
                    for index, source_value in enumerate(reference_upload_paths, start=1):
                        source = Path(source_value)
                        if not source.is_file():
                            raise ValueError("uploaded redraw reference image was not found")
                        archived = reference_dir / f"uploaded_{index:02d}_{source.name}"
                        if not archived.exists():
                            shutil.copy2(source, archived)
                        reference_paths.append(str(archived))
                reference_paths = list(dict.fromkeys(reference_paths))[:4]
                with self._mapping_lock:
                    mapping = self._load_mapping(project_dir)
                    item = next((entry for entry in mapping if str(entry.get("macro_scene_id")) == macro_id), None)
                    if item is None:
                        raise ValueError("image mapping was not found")
                    self._backup_current(project_dir, image, macro_id)
                    item["image_prompt"] = prompt
                    self._save_mapping(project_dir, mapping)
                    image.with_suffix(".txt").write_text(prompt, encoding="utf-8")
                import module4_video_render as visual
                render_item = dict(item)
                if reference_paths:
                    render_item["reference_image_paths"] = reference_paths
                    render_item["image_prompt"] = (
                        f"【参考图编号】本次附带的第 1 至第 {len(reference_paths)} 张图片依次对应图1至图{len(reference_paths)}；"
                        "提示词中提及图N时，必须严格以第N张参考图作为该角色或物体的形象依据。\n"
                        f"{prompt}"
                    )
                rendered = visual.render_posters_concurrently([render_item], visual._provider_configs())[0]
                shutil.copy2(rendered, image)
                self._set_image_task(job.id, macro_id, status="completed", action="redraw", message="图片已重绘，请检查效果")
                self._log(job, f"{macro_id} 重绘完成。")
            except Exception as exc:
                self._set_image_task(job.id, macro_id, status="failed", action="redraw", message=str(exc))
                self._log(job, f"{macro_id} 重绘失败：{exc}")

        threading.Thread(target=work, daemon=True).start()

    def upload(self, *, job: Any, macro_id: str, source: Path) -> None:
        self._start_image_task(job, macro_id, "upload", "正在替换本地图片")
        self._log(job, f"开始替换 {macro_id} 的本地图片。")

        def work() -> None:
            try:
                project_dir = self.output_dir(job.id, int(job.user_id))
                image = self._find_image(project_dir / "image", macro_id)
                self._backup_current(project_dir, image, macro_id)
                shutil.copy2(source, image)
                self._set_image_task(job.id, macro_id, status="completed", action="upload", message="本地图片已替换")
                self._log(job, f"{macro_id} 本地图片替换完成。")
            except Exception as exc:
                self._set_image_task(job.id, macro_id, status="failed", action="upload", message=str(exc))
                self._log(job, f"{macro_id} 本地图片替换失败：{exc}")

        threading.Thread(target=work, daemon=True).start()

    def undo(self, *, job_id: str, user_id: int, macro_id: str) -> None:
        project_dir = self.output_dir(job_id, user_id)
        image = self._find_image(project_dir / "image", macro_id)
        backups = sorted(self._backup_dir(project_dir).glob(f"{macro_id}.*{image.suffix.lower()}"))
        backups = [path for path in backups if ".original" not in path.name]
        if not backups:
            raise FileNotFoundError("no previous image backup")
        shutil.copy2(backups[-1], image)
        self._set_task(job_id, status="completed", action="undo", macro_id=macro_id, message="已撤回到上一个图片版本")

    def reset_prompt(self, *, job_id: str, user_id: int, macro_id: str) -> str:
        project_dir = self.output_dir(job_id, user_id)
        image = self._find_image(project_dir / "image", macro_id)
        original = self._backup_dir(project_dir) / f"{macro_id}.original.txt"
        if not original.is_file():
            raise FileNotFoundError("original prompt backup is unavailable")
        prompt = original.read_text(encoding="utf-8")
        mapping = self._load_mapping(project_dir)
        item = next((entry for entry in mapping if str(entry.get("macro_scene_id")) == macro_id), None)
        if item is None:
            raise ValueError("image mapping was not found")
        item["image_prompt"] = prompt
        self._save_mapping(project_dir, mapping)
        image.with_suffix(".txt").write_text(prompt, encoding="utf-8")
        self._set_task(job_id, status="completed", action="reset_prompt", macro_id=macro_id, message="提示词已重置为初始版本")
        return prompt

    def adjust_timing(
        self, *, job_id: str, user_id: int, macro_id: str, action: str
    ) -> dict[str, Any]:
        actions = {"extend_prev", "extend_next", "shrink_prev", "shrink_next"}
        if action not in actions:
            raise ValueError("无效的时序调整操作")
        project_dir = self.output_dir(job_id, user_id)
        with self._mapping_lock:
            mapping = self._load_mapping(project_dir)
            timeline = self._load_timeline(project_dir)
            mapping, recovered_timing = self._mapping_with_recovered_timing(project_dir, mapping, timeline)
            index = next((i for i, item in enumerate(mapping) if str(item.get("macro_scene_id")) == macro_id), -1)
            if index < 0:
                raise ValueError("未找到要调整的画面")
            current = mapping[index]
            current_slides = current.get("includes_slides", [])
            if not isinstance(current_slides, list):
                raise ValueError("当前画面的字幕分组无效")
            self._ensure_timing_backup(project_dir, mapping)

            if action == "extend_prev":
                if index == 0 or len(mapping[index - 1].get("includes_slides", [])) <= 1:
                    raise ValueError("前一张图只剩一句字幕，不能再缩短")
                current_slides.insert(0, mapping[index - 1]["includes_slides"].pop())
                message = "已让当前画面向前多覆盖一句字幕"
            elif action == "extend_next":
                if index >= len(mapping) - 1 or len(mapping[index + 1].get("includes_slides", [])) <= 1:
                    raise ValueError("后一张图只剩一句字幕，不能再缩短")
                current_slides.append(mapping[index + 1]["includes_slides"].pop(0))
                message = "已让当前画面向后多覆盖一句字幕"
            elif action == "shrink_prev":
                if index == 0 or len(current_slides) <= 1:
                    raise ValueError("当前画面至少需要保留一句字幕")
                mapping[index - 1]["includes_slides"].append(current_slides.pop(0))
                message = "已让当前画面从前面少覆盖一句字幕"
            else:  # shrink_next
                if index >= len(mapping) - 1 or len(current_slides) <= 1:
                    raise ValueError("当前画面至少需要保留一句字幕")
                mapping[index + 1]["includes_slides"].insert(0, current_slides.pop())
                message = "已让当前画面从后面少覆盖一句字幕"
            self._validate_timing_partition(mapping, timeline)
            self._save_mapping(project_dir, mapping)
        recovery_note = "（已兼容恢复该历史项目的字幕分组）" if recovered_timing else ""
        self._set_task(job_id, status="completed", action="timing", macro_id=macro_id, message=f"{message}{recovery_note}")
        return self.inspect(job_id, user_id)

    def reset_timing(self, *, job_id: str, user_id: int) -> dict[str, Any]:
        project_dir = self.output_dir(job_id, user_id)
        backup_path = self._timing_backup_path(project_dir)
        if not backup_path.is_file():
            raise FileNotFoundError("尚未调整过画面时序，无需重置")
        with self._mapping_lock:
            current = self._load_mapping(project_dir)
            original = json.loads(backup_path.read_text(encoding="utf-8"))
            timeline = self._load_timeline(project_dir)
            if not isinstance(original, list):
                raise ValueError("初始时序备份无效")
            current_by_id = {str(item.get("macro_scene_id")): item for item in current if isinstance(item, dict)}
            restored: list[dict[str, Any]] = []
            for saved in original:
                if not isinstance(saved, dict):
                    continue
                entry = dict(saved)
                # Keep prompt changes for pictures that still participate.  A removed
                # picture intentionally comes back with the prompt it had at backup.
                edited = current_by_id.get(str(saved.get("macro_scene_id")))
                if edited is not None:
                    entry.update({key: value for key, value in edited.items() if key != "includes_slides"})
                entry["includes_slides"] = list(saved.get("includes_slides") or [])
                restored.append(entry)
            self._validate_timing_partition(restored, timeline)
            self._save_mapping(project_dir, restored)
        self._set_task(job_id, status="completed", action="timing", message="已恢复到首次调整前的画面时序")
        return self.inspect(job_id, user_id)

    def remove_timing_picture(self, *, job_id: str, user_id: int, macro_id: str) -> dict[str, Any]:
        """Remove one frame from the timeline and redistribute its subtitles safely."""
        project_dir = self.output_dir(job_id, user_id)
        with self._mapping_lock:
            mapping = self._load_mapping(project_dir)
            timeline = self._load_timeline(project_dir)
            mapping, recovered_timing = self._mapping_with_recovered_timing(project_dir, mapping, timeline)
            if len(mapping) <= 1:
                raise ValueError("至少需要保留一张画面")
            index = next((i for i, item in enumerate(mapping) if str(item.get("macro_scene_id")) == macro_id), -1)
            if index < 0:
                raise ValueError("未找到要移除的画面")
            self._ensure_timing_backup(project_dir, mapping)
            removed = mapping.pop(index)
            slides = list(removed.get("includes_slides") or [])
            if index == 0:
                mapping[0]["includes_slides"] = slides + list(mapping[0].get("includes_slides") or [])
            elif index == len(mapping):
                mapping[-1]["includes_slides"] = list(mapping[-1].get("includes_slides") or []) + slides
            else:
                previous = mapping[index - 1]
                following = mapping[index]
                previous_count = len(previous.get("includes_slides") or [])
                following_count = len(following.get("includes_slides") or [])
                # Preserve chronological order while choosing the split that makes
                # neighbouring subtitle counts as even as possible.
                split_at = min(
                    range(len(slides) + 1),
                    key=lambda value: (abs((previous_count + value) - (following_count + len(slides) - value)), abs(value - len(slides) / 2)),
                )
                previous["includes_slides"] = list(previous.get("includes_slides") or []) + slides[:split_at]
                following["includes_slides"] = slides[split_at:] + list(following.get("includes_slides") or [])
            self._validate_timing_partition(mapping, timeline)
            self._save_mapping(project_dir, mapping)
        recovery_note = "（已兼容恢复该历史项目的字幕分组）" if recovered_timing else ""
        self._set_task(job_id, status="completed", action="timing_remove", macro_id=macro_id, message=f"已移除 {macro_id}，其字幕已分配给相邻画面{recovery_note}")
        return self.inspect(job_id, user_id)

    def render_video(self, *, job: Any, mode: str = "both") -> None:
        with self._lock:
            has_active_image_tasks = any(
                value.get("status") == "running"
                for value in self._image_tasks.get(job.id, {}).values()
            )
        if has_active_image_tasks:
            raise RuntimeError("请等待正在重绘或替换的图片完成后，再重新渲染视频")
        mode = mode if mode in {"subtitles", "raw", "both"} else "both"
        from .pipeline import store
        setattr(job, "_visual_editor_render_mode", mode)
        store.update(job, step="render", progress=0, message="画面修改：准备重新渲染")
        self._set_task(job.id, status="running", action="render", message="正在仅运行模块 5 重新合成视频")

        def work() -> None:
            try:
                with job_store_lock(job):
                    project_dir = self.output_dir(job.id, int(job.user_id))
                    import module4_video_render as visual
                    import module5_video_render as renderer

                    mapping_path = self._mapping_path(project_dir)
                    timeline_path = self._timeline_path(project_dir)
                    if mapping_path.is_file():
                        shutil.copy2(mapping_path, visual.POSTER_MAPPING_PATH)
                    if timeline_path.is_file():
                        shutil.copy2(timeline_path, visual.TIMELINE_PATH)
                    html_path = project_dir / "other" / HTML_FILENAME
                    subtitle_path = project_dir / "other" / SUBTITLE_FILENAME
                    audio_path = project_dir / "input" / "配音.wav"
                    if not html_path.is_file() or not subtitle_path.is_file() or not audio_path.is_file():
                        raise FileNotFoundError("输出目录缺少重新渲染所需的画面、字幕或配音资料")
                    # Timing edits live in the mapping.  Rebuild the self-contained
                    # HTML from that mapping immediately before module 5, while
                    # preserving the existing images and subtitle/audio files.
                    mapping = self._load_mapping(project_dir)
                    timeline = self._load_timeline(project_dir)
                    try:
                        self._write_timing_html(project_dir, mapping, timeline)
                    except ValueError:
                        # Legacy projects may predate persisted slide groups. Their
                        # original HTML remains renderable, only timing edits stay off.
                        self._log(job, "该历史项目缺少可编辑的字幕分组，沿用原始画面时序重新渲染。")
                    html = html_path.read_text(encoding="utf-8")
                    html = html.replace("../image/", "assets/").replace("../input/配音.wav", "2_audio_srt/final_output.wav")
                    (visual.VISUAL_DIR / "index.html").write_text(html, encoding="utf-8")
                    visual.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
                    for image in (project_dir / "image").glob("*"):
                        if image.suffix.lower() in IMAGE_EXTENSIONS:
                            shutil.copy2(image, visual.ASSETS_DIR / image.name)
                    shutil.copy2(audio_path, renderer.AUDIO_DIR / "final_output.wav")
                    shutil.copy2(subtitle_path, renderer.AUDIO_DIR / "final_short.srt")
                    render_env = os.environ.copy()
                    render_env["VIDEO_RENDER_VARIANT"] = mode
                    render_env["PYTHONUTF8"] = "1"
                    command = [sys.executable, str(PROJECT_ROOT / "module5_video_render.py")]
                    self._log(job, f"开始重新渲染（{mode}），模块 5 的实时输出将持续写入主后台日志。")
                    process = subprocess.Popen(
                        command,
                        cwd=str(PROJECT_ROOT),
                        env=render_env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    with self._lock:
                        self._render_cancelled.discard(job.id)
                        self._render_processes[job.id] = process
                    try:
                        assert process.stdout is not None
                        for line in process.stdout:
                            line = line.strip()
                            if line:
                                store.log(job, line)
                        return_code = process.wait()
                    finally:
                        with self._lock:
                            cancelled = job.id in self._render_cancelled
                            self._render_processes.pop(job.id, None)
                            self._render_cancelled.discard(job.id)
                    if cancelled:
                        self._set_task(job.id, status="cancelled", action="render", message="重新渲染已停止")
                        self._log(job, "重新渲染已由用户停止。")
                        return
                    if return_code != 0:
                        raise RuntimeError(f"模块 5 重新渲染失败，退出码 {return_code}")
                    video_dir = project_dir / "video"
                    video_dir.mkdir(parents=True, exist_ok=True)
                    copies = {
                        renderer.FINAL_DIR / "final_with_subtitles.mp4": video_dir / "最终视频_字幕版.mp4",
                        renderer.FINAL_DIR / "final_raw_presentation.mp4": video_dir / "最终视频_纯净版.mp4",
                    }
                    selected = {"subtitles", "raw"} if mode == "both" else {mode}
                    for variant, (source, target) in {
                        "subtitles": (renderer.FINAL_DIR / "final_with_subtitles.mp4", video_dir / "最终视频_字幕版.mp4"),
                        "raw": (renderer.FINAL_DIR / "final_raw_presentation.mp4", video_dir / "最终视频_纯净版.mp4"),
                    }.items():
                        if variant not in selected:
                            continue
                        shutil.copy2(source, target)
                        artifact = JOBS_DIR / job.id / "artifacts" / source.name
                        shutil.copy2(source, artifact)
                        register_job_asset(job, target, "project_output", {"project_name": project_dir.name})
                    if mode in {"subtitles", "both"}:
                        job.artifacts["video_with_subtitles"] = f"/api/jobs/{job.id}/artifacts/final_with_subtitles.mp4"
                    if mode in {"raw", "both"}:
                        job.artifacts["video_raw"] = f"/api/jobs/{job.id}/artifacts/final_raw_presentation.mp4"
                    store.update(job, artifacts=dict(job.artifacts), progress=100, message="画面修改已重新渲染")
                    self._set_task(job.id, status="completed", action="render", message="视频已重新渲染")
                    self._log(job, "重新渲染完成，最终视频已更新。")
            except Exception as exc:
                self._set_task(job.id, status="failed", action="render", message=str(exc))
                self._log(job, f"重新渲染失败：{exc}")
            finally:
                if hasattr(job, "_visual_editor_render_mode"):
                    delattr(job, "_visual_editor_render_mode")

        threading.Thread(target=work, daemon=True).start()


    def cancel_render(self, job: Any) -> dict[str, Any]:
        """Stop only a module-5 render launched from the visual editor."""
        with self._lock:
            process = self._render_processes.get(job.id)
            task = dict(self._tasks.get(job.id) or {})
            if process is None or task.get("action") != "render" or task.get("status") != "running":
                return {"ok": False, "message": "当前没有可停止的重新渲染任务。"}
            self._render_cancelled.add(job.id)
        try:
            from .pipeline import _terminate_process_tree
            _terminate_process_tree(process)
        except Exception:
            try:
                process.terminate()
            except OSError:
                pass
        self._set_task(job.id, status="cancelled", action="render", message="正在停止重新渲染")
        self._log(job, "已收到停止重新渲染请求，正在终止模块 5。")
        return {"ok": True, "message": "已请求停止重新渲染，日志将显示最终结果。"}


class job_store_lock:
    """Share the generation lock: editing must not overwrite a running pipeline workspace."""

    def __init__(self, _job: Any) -> None:
        from .pipeline import store
        self._lock = store._pipeline_lock

    def __enter__(self) -> None:
        self._lock.acquire()

    def __exit__(self, *_args: Any) -> None:
        self._lock.release()


visual_editor = VisualEditor()
