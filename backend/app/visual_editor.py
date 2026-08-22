"""Post-production editor for replacing a completed job's generated images."""

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun
# AGPL-3.0 Section 7 terms: ../../ADDITIONAL_TERMS.md

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
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
MAPPING_FILENAME = "画面映射.json"
TIMELINE_FILENAME = "画面时间线.json"
MANIFEST_FILENAME = "画面修改清单.json"
HTML_FILENAME = "最终画面.html"
SUBTITLE_FILENAME = "最终字幕.srt"
TIMING_BACKUP_FILENAME = "画面映射.初始时序.json"
REFERENCE_MANIFEST_FILENAME = "参考图清单.json"
SUBTITLE_HISTORY_DIRNAME = "字幕历史"


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
        VisualEditor._migrate_segmented_slide_ids(output_dir)

    @staticmethod
    def _mapping_path(project_dir: Path) -> Path:
        return project_dir / "other" / MAPPING_FILENAME

    @staticmethod
    def _timeline_path(project_dir: Path) -> Path:
        return project_dir / "other" / TIMELINE_FILENAME

    @staticmethod
    def _prepare_render_workspace(render_root: Path) -> dict[str, Path]:
        """Create a job-local module 4/5 cache below the archived output project."""
        visual_dir = render_root / "3_visual_template"
        paths = {
            "root": render_root,
            "visual": visual_dir,
            "assets": visual_dir / "assets",
            "audio": render_root / "2_audio_srt",
            "final": render_root / "4_final_video",
        }
        for key, directory in paths.items():
            if key != "root":
                directory.mkdir(parents=True, exist_ok=True)
        return paths

    @staticmethod
    def _migrate_segmented_slide_ids(project_dir: Path) -> bool:
        """Namespace repeated local slide IDs in legacy long-text exports.

        Each old render part restarted at scene_001.  Once the parts were joined,
        later entries overwrote earlier ones in the editor's slide lookup, making
        pictures show another part's narration.  Prefix both sides of the mapping
        with part_NNN while preserving prompts, images and edited timing.
        """
        mapping_path = VisualEditor._mapping_path(project_dir)
        timeline_path = VisualEditor._timeline_path(project_dir)
        if not mapping_path.is_file() or not timeline_path.is_file():
            return False
        try:
            mapping_payload = json.loads(mapping_path.read_text(encoding="utf-8"))
            timeline_payload = json.loads(timeline_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(mapping_payload, list) or not isinstance(timeline_payload, list):
            return False
        timeline = [dict(item) for item in timeline_payload if isinstance(item, dict)]
        mapping = [dict(item) for item in mapping_payload if isinstance(item, dict)]
        slide_ids = [str(item.get("slide_id") or "") for item in timeline]
        if not slide_ids or len(set(slide_ids)) == len(slide_ids):
            return False

        part_prefixes: list[str] = []
        for item in mapping:
            match = re.match(r"^(part_\d+)_poster_", str(item.get("macro_scene_id") or ""))
            if match and match.group(1) not in part_prefixes:
                part_prefixes.append(match.group(1))
        if not part_prefixes:
            return False

        section_index = 0
        previous_number = -1
        for item in timeline:
            slide_id = str(item.get("slide_id") or "")
            match = re.search(r"(\d+)$", slide_id)
            number = int(match.group(1)) if match else previous_number + 1
            if previous_number >= 0 and number <= previous_number:
                section_index += 1
            if section_index >= len(part_prefixes):
                return False
            prefix = part_prefixes[section_index]
            item["slide_id"] = f"{prefix}_{slide_id}"
            if item.get("id"):
                item["id"] = f"{prefix}_{item['id']}"
            previous_number = number

        valid_prefixes = set(part_prefixes)
        for item in mapping:
            match = re.match(r"^(part_\d+)_poster_", str(item.get("macro_scene_id") or ""))
            if not match or match.group(1) not in valid_prefixes:
                return False
            prefix = match.group(1)
            item["includes_slides"] = [
                value if str(value).startswith(f"{prefix}_") else f"{prefix}_{value}"
                for value in item.get("includes_slides", [])
            ]

        expected = [str(item.get("slide_id") or "") for item in timeline]
        actual = [str(value) for item in mapping for value in item.get("includes_slides", [])]
        if actual != expected:
            return False
        other_dir = project_dir / "other"
        mapping_backup = other_dir / "画面映射.分段编号修复前.json"
        timeline_backup = other_dir / "画面时间线.分段编号修复前.json"
        if not mapping_backup.exists():
            shutil.copy2(mapping_path, mapping_backup)
        if not timeline_backup.exists():
            shutil.copy2(timeline_path, timeline_backup)
        mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
        timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    @staticmethod
    def _archived_main_reference_paths(project_dir: Path) -> list[str]:
        """Return the task's original reference images in stable 图1/图2/图3 order."""
        manifest_path = project_dir / "other" / REFERENCE_MANIFEST_FILENAME
        if not manifest_path.is_file():
            return []
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        entries = payload if isinstance(payload, list) else []
        ordered: list[tuple[int, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            match = re.fullmatch(r"图([1-3])", str(entry.get("reference_id") or "").strip())
            filename = Path(str(entry.get("filename") or "")).name
            path = project_dir / "other" / "reference_images" / filename
            if match and filename and path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                ordered.append((int(match.group(1)), str(path)))
        return [path for _, path in sorted(ordered)]

    @staticmethod
    def _timing_backup_path(project_dir: Path) -> Path:
        """The original sentence-to-picture allocation, kept outside the workspace."""
        return project_dir / "other" / TIMING_BACKUP_FILENAME

    @staticmethod
    def _timing_history_entries(project_dir: Path) -> list[dict[str, str]]:
        history_root = project_dir / "other" / "时序历史基准"
        entries: list[dict[str, str]] = []
        if not history_root.is_dir():
            return entries
        for path in history_root.glob("*/画面映射.初始时序*.json"):
            if not path.is_file():
                continue
            key = f"{path.parent.name}:{path.name}"
            stamp = path.parent.name.split("_", 2)
            label = path.parent.name
            if len(stamp) >= 2 and len(stamp[0]) == 8 and len(stamp[1]) >= 6:
                date_value, time_value = stamp[0], stamp[1][:6]
                label = f"{date_value[:4]}-{date_value[4:6]}-{date_value[6:8]} {time_value[:2]}:{time_value[2:4]}:{time_value[4:6]}"
            entries.append({"id": key, "label": label})
        return sorted(entries, key=lambda item: item["id"], reverse=True)

    @staticmethod
    def _timing_history_path(project_dir: Path, history_id: str) -> Path:
        history_root = project_dir / "other" / "时序历史基准"
        for path in history_root.glob("*/画面映射.初始时序*.json"):
            if path.is_file() and f"{path.parent.name}:{path.name}" == str(history_id):
                return path
        raise FileNotFoundError("所选历史时序不存在")

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
    def _srt_timestamp(value: float) -> str:
        milliseconds = max(0, int(round(float(value) * 1000)))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    @staticmethod
    def _validated_subtitle_timeline(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not timeline:
            raise ValueError("该项目缺少可编辑的字幕时间线")
        validated: list[dict[str, Any]] = []
        seen: set[str] = set()
        previous_start = -1.0
        for raw in timeline:
            item = dict(raw)
            slide_id = str(item.get("slide_id") or "").strip()
            text = str(item.get("text_content") or "").strip()
            if not slide_id or slide_id in seen:
                raise ValueError("字幕时间线包含空编号或重复编号")
            if not text:
                raise ValueError(f"{slide_id} 的字幕正文不能为空")
            try:
                start = float(item.get("start"))
                end = float(item.get("end"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{slide_id} 的字幕时间戳无效") from exc
            if start < previous_start - 1e-6 or end <= start:
                raise ValueError(f"{slide_id} 的字幕时间戳顺序无效")
            item["text_content"] = text
            item["start"] = start
            item["end"] = end
            validated.append(item)
            seen.add(slide_id)
            previous_start = start
        return validated

    @staticmethod
    def _write_subtitle_files(project_dir: Path, timeline: list[dict[str, Any]]) -> None:
        validated = VisualEditor._validated_subtitle_timeline(timeline)
        timeline_path = VisualEditor._timeline_path(project_dir)
        subtitle_path = project_dir / "other" / SUBTITLE_FILENAME
        timeline_path.parent.mkdir(parents=True, exist_ok=True)
        timeline_temp = timeline_path.with_name(timeline_path.name + ".tmp")
        subtitle_temp = subtitle_path.with_name(subtitle_path.name + ".tmp")
        timeline_temp.write_text(
            json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        blocks = [
            "\n".join((
                str(index),
                f"{VisualEditor._srt_timestamp(item['start'])} --> {VisualEditor._srt_timestamp(item['end'])}",
                str(item["text_content"]),
            ))
            for index, item in enumerate(validated, 1)
        ]
        subtitle_temp.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        os.replace(timeline_temp, timeline_path)
        os.replace(subtitle_temp, subtitle_path)

    @staticmethod
    def _archive_subtitle_state(project_dir: Path, label: str) -> Path:
        history_root = project_dir / "other" / SUBTITLE_HISTORY_DIRNAME
        stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000:06d}"
        destination = history_root / stamp
        destination.mkdir(parents=True, exist_ok=False)
        for name in (TIMELINE_FILENAME, SUBTITLE_FILENAME):
            source = project_dir / "other" / name
            if source.is_file():
                shutil.copy2(source, destination / name)
        (destination / "metadata.json").write_text(
            json.dumps({"label": label, "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return destination

    @staticmethod
    def _subtitle_history_entries(project_dir: Path) -> list[dict[str, str]]:
        history_root = project_dir / "other" / SUBTITLE_HISTORY_DIRNAME
        entries: list[dict[str, str]] = []
        if not history_root.is_dir():
            return entries
        for directory in history_root.iterdir():
            if not directory.is_dir() or not (directory / TIMELINE_FILENAME).is_file():
                continue
            label = directory.name
            metadata_path = directory / "metadata.json"
            if metadata_path.is_file():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    label = f"{metadata.get('created_at') or directory.name} · {metadata.get('label') or '字幕备份'}"
                except (OSError, json.JSONDecodeError):
                    pass
            entries.append({"id": directory.name, "label": label})
        return sorted(entries, key=lambda item: item["id"], reverse=True)

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

    @classmethod
    def _commit_baseline_locked(
        cls,
        project_dir: Path,
        mapping: list[dict[str, Any]],
        macro_id: str,
        *,
        prompt: str | None = None,
        history_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Promote the current picture and prompt to the editor's new baseline.

        Existing originals and undo versions are moved into a visible history
        directory first.  Nothing is deleted, while normal undo/reset can no
        longer cross the newly confirmed baseline.
        """
        image = cls._find_image(project_dir / "image", macro_id)
        item = next((entry for entry in mapping if str(entry.get("macro_scene_id")) == macro_id), None)
        if item is None:
            raise ValueError("image mapping was not found")

        if prompt is not None:
            clean_prompt = str(prompt).strip()
            if not clean_prompt:
                raise ValueError("prompt is empty")
            item["image_prompt"] = clean_prompt
        current_prompt = str(item.get("image_prompt") or "")
        prompt_path = image.with_suffix(".txt")
        prompt_path.write_text(current_prompt, encoding="utf-8")

        backup_dir = cls._backup_dir(project_dir)
        if history_dir is None:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            history_dir = backup_dir / "历史基准" / stamp
        history_dir.mkdir(parents=True, exist_ok=True)

        # Archive both the previous immutable baseline and the undo chain.  The
        # root backup folder is then deliberately clean for this picture, making
        # the current version the earliest version reachable by Undo/Reset.
        for previous in sorted(backup_dir.glob(f"{macro_id}.*")):
            if not previous.is_file():
                continue
            destination = history_dir / previous.name
            if destination.exists():
                destination = history_dir / f"{previous.stem}_{time.time_ns()}{previous.suffix}"
            shutil.move(str(previous), str(destination))

        baseline_image = backup_dir / f"{macro_id}.original{image.suffix.lower()}"
        baseline_prompt = backup_dir / f"{macro_id}.original.txt"
        shutil.copy2(image, baseline_image)
        shutil.copy2(prompt_path, baseline_prompt)
        return item

    def commit_baseline(
        self,
        *,
        job: Any,
        user_id: int,
        macro_id: str,
        prompt: str | None = None,
    ) -> None:
        project_dir = self.output_dir(job.id, user_id)
        with self._lock:
            current = self._image_tasks.get(job.id, {}).get(macro_id) or {}
            if current.get("status") == "running":
                raise RuntimeError("请等待该图片重绘或替换完成后再确认")
        with self._mapping_lock:
            mapping = self._load_mapping(project_dir)
            self._commit_baseline_locked(project_dir, mapping, macro_id, prompt=prompt)
            self._save_mapping(project_dir, mapping)
        self._set_image_task(
            job.id,
            macro_id,
            status="completed",
            action="commit_baseline",
            message="已确认为新的原图基准",
        )
        self._set_task(job.id, status="completed", action="commit_baseline", macro_id=macro_id, message=f"{macro_id} 已确认为新的原图")
        self._log(job, f"{macro_id} 已确认为新的原图；旧原图与撤回记录已归档到重绘备份。")

    def commit_all_baselines(self, *, job: Any, user_id: int) -> int:
        project_dir = self.output_dir(job.id, user_id)
        with self._lock:
            if any(
                value.get("status") == "running"
                for value in self._image_tasks.get(job.id, {}).values()
            ):
                raise RuntimeError("请等待所有正在重绘或替换的图片完成后再确认全部")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        history_dir = self._backup_dir(project_dir) / "历史基准" / stamp
        with self._mapping_lock:
            mapping = self._load_mapping(project_dir)
            count = 0
            for item in mapping:
                macro_id = str(item.get("macro_scene_id") or "")
                if not macro_id:
                    continue
                try:
                    self._commit_baseline_locked(
                        project_dir,
                        mapping,
                        macro_id,
                        history_dir=history_dir,
                    )
                except FileNotFoundError:
                    continue
                count += 1
            if not count:
                raise ValueError("没有找到可确认的当前图片")
            self._save_mapping(project_dir, mapping)
        self._set_task(job.id, status="completed", action="commit_all_baselines", message=f"已将当前 {count} 张图片确认为新的原图")
        self._log(job, f"已将当前 {count} 张图片全部确认为新的原图；旧原图与撤回记录已归档到重绘备份。")
        return count

    def inspect(self, job_id: str, user_id: int) -> dict[str, Any]:
        project_dir = self.output_dir(job_id, user_id)
        self._migrate_segmented_slide_ids(project_dir)
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
        bgm_settings = self._inspect_bgm_settings(job_id, project_dir)
        return {
            "items": items,
            "task": task,
            "image_tasks": image_tasks,
            "has_active_image_tasks": any(value.get("status") == "running" for value in image_tasks.values()),
            "timing_available": timing_available,
            "timing_message": timing_message,
            "timing_history": self._timing_history_entries(project_dir),
            "subtitle_history": self._subtitle_history_entries(project_dir),
            "bgm": bgm_settings,
            "project_dir": str(project_dir),
            "version": int(time.time() * 1000),
        }

    def save_subtitle_texts(
        self, *, job_id: str, user_id: int, updates: dict[str, str]
    ) -> dict[str, Any]:
        project_dir = self.output_dir(job_id, user_id)
        normalized: dict[str, str] = {}
        for raw_id, raw_text in dict(updates or {}).items():
            slide_id = str(raw_id or "").strip()
            text = str(raw_text or "").strip()
            if not slide_id:
                raise ValueError("字幕编号不能为空")
            if not text:
                raise ValueError(f"{slide_id} 的字幕正文不能为空")
            if len(text) > 1200:
                raise ValueError(f"{slide_id} 的字幕正文过长")
            normalized[slide_id] = text
        if not normalized:
            raise ValueError("没有需要保存的字幕修改")

        with self._mapping_lock:
            timeline = self._load_timeline(project_dir)
            by_id = {str(item.get("slide_id") or ""): item for item in timeline}
            missing = [slide_id for slide_id in normalized if slide_id not in by_id]
            if missing:
                raise ValueError(f"找不到字幕：{', '.join(missing[:3])}")
            changed = {
                slide_id: text
                for slide_id, text in normalized.items()
                if str(by_id[slide_id].get("text_content") or "").strip() != text
            }
            if not changed:
                return self.inspect(job_id, user_id)
            self._archive_subtitle_state(project_dir, "保存字幕修改前")
            for slide_id, text in changed.items():
                by_id[slide_id]["text_content"] = text
            self._write_subtitle_files(project_dir, timeline)
            try:
                self._write_timing_html(project_dir, self._load_mapping(project_dir), timeline)
            except (OSError, ValueError, FileNotFoundError):
                # The SRT/timeline pair is authoritative. Legacy HTML is rebuilt
                # during the next render whenever its picture mapping is usable.
                pass
        self._set_task(
            job_id,
            status="completed",
            action="subtitle",
            message=f"已保存 {len(changed)} 条字幕修改；重新渲染后进入成片。",
        )
        return self.inspect(job_id, user_id)

    def restore_subtitle_history(
        self, *, job_id: str, user_id: int, history_id: str
    ) -> dict[str, Any]:
        project_dir = self.output_dir(job_id, user_id)
        safe_id = Path(str(history_id or "")).name
        if not safe_id or safe_id != str(history_id):
            raise ValueError("字幕历史编号无效")
        history_root = (project_dir / "other" / SUBTITLE_HISTORY_DIRNAME).resolve()
        source_dir = (history_root / safe_id).resolve()
        if history_root not in source_dir.parents:
            raise ValueError("字幕历史路径无效")
        source_timeline = source_dir / TIMELINE_FILENAME
        if not source_timeline.is_file():
            raise FileNotFoundError("所选字幕历史不存在")
        payload = json.loads(source_timeline.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("字幕历史内容无效")
        archived = self._validated_subtitle_timeline(
            [dict(item) for item in payload if isinstance(item, dict)]
        )
        with self._mapping_lock:
            current = self._validated_subtitle_timeline(self._load_timeline(project_dir))
            archived_text = {
                str(item["slide_id"]): str(item["text_content"])
                for item in archived
            }
            missing = [
                str(item["slide_id"])
                for item in current
                if str(item["slide_id"]) not in archived_text
            ]
            if missing:
                raise ValueError("所选字幕历史与当前项目句子编号不一致，无法安全恢复")
            # Subtitle history restores display text only. Current timestamps may
            # have changed after a later TTS refinement and must remain authoritative.
            for item in current:
                item["text_content"] = archived_text[str(item["slide_id"])]
            self._archive_subtitle_state(project_dir, "恢复字幕历史前")
            self._write_subtitle_files(project_dir, current)
            try:
                self._write_timing_html(project_dir, self._load_mapping(project_dir), current)
            except (OSError, ValueError, FileNotFoundError):
                pass
        self._set_task(
            job_id,
            status="completed",
            action="subtitle",
            message="已恢复所选字幕历史；重新渲染后进入成片。",
        )
        return self.inspect(job_id, user_id)

    @staticmethod
    def _subtitle_audio_path(project_dir: Path) -> Path:
        path = project_dir / "input" / "配音.wav"
        if not path.is_file():
            raise FileNotFoundError("项目配音文件不存在，无法校准字幕边界")
        return path

    def subtitle_audio_path(self, job_id: str, user_id: int) -> Path:
        return self._subtitle_audio_path(self.output_dir(job_id, user_id))

    def preview_subtitle_boundary(
        self, *, job_id: str, user_id: int, left_slide_id: str
    ) -> dict[str, Any]:
        """Return the current adjacent boundary for manual listening and adjustment."""
        project_dir = self.output_dir(job_id, user_id)
        timeline = self._validated_subtitle_timeline(self._load_timeline(project_dir))
        index = next(
            (position for position, item in enumerate(timeline) if str(item["slide_id"]) == left_slide_id),
            -1,
        )
        if index < 0 or index >= len(timeline) - 1:
            raise ValueError("请选择一条后面仍有字幕的句子")
        left = timeline[index]
        right = timeline[index + 1]
        pair_start = float(left["start"])
        pair_end = float(right["end"])
        if pair_end - pair_start < 0.4:
            raise ValueError("相邻两句时长过短，无法安全校准")
        current_boundary = (float(left["end"]) + float(right["start"])) / 2
        audio_path = self._subtitle_audio_path(project_dir)
        minimum = pair_start + 0.15
        maximum = pair_end - 0.15
        return {
            "left_slide_id": str(left["slide_id"]),
            "right_slide_id": str(right["slide_id"]),
            "left_text": str(left["text_content"]),
            "right_text": str(right["text_content"]),
            "pair_start": round(pair_start, 4),
            "pair_end": round(pair_end, 4),
            "clip_start": round(pair_start, 4),
            "clip_end": round(pair_end, 4),
            "current_boundary": round(current_boundary, 4),
            "suggested_boundary": round(current_boundary, 4),
            "minimum_boundary": round(minimum, 4),
            "maximum_boundary": round(maximum, 4),
            "audio_url": f"/api/jobs/{job_id}/visual-editor/audio?v={audio_path.stat().st_mtime_ns}",
        }

    def apply_subtitle_boundary(
        self, *, job_id: str, user_id: int, left_slide_id: str, boundary: float
    ) -> dict[str, Any]:
        project_dir = self.output_dir(job_id, user_id)
        with self._mapping_lock:
            timeline = self._validated_subtitle_timeline(self._load_timeline(project_dir))
            index = next(
                (position for position, item in enumerate(timeline) if str(item["slide_id"]) == left_slide_id),
                -1,
            )
            if index < 0 or index >= len(timeline) - 1:
                raise ValueError("字幕边界对应的相邻句子不存在")
            left = timeline[index]
            right = timeline[index + 1]
            minimum = float(left["start"]) + 0.15
            maximum = float(right["end"]) - 0.15
            value = float(boundary)
            if not minimum <= value <= maximum:
                raise ValueError("建议边界超出相邻两句的安全范围")
            self._archive_subtitle_state(project_dir, "校准字幕边界前")
            left["end"] = round(value, 6)
            right["start"] = round(value, 6)
            self._write_subtitle_files(project_dir, timeline)
            try:
                self._write_timing_html(project_dir, self._load_mapping(project_dir), timeline)
            except (OSError, ValueError, FileNotFoundError):
                pass
        self._set_task(
            job_id,
            status="completed",
            action="subtitle_boundary",
            message=f"已校准 {left_slide_id} 与 {right['slide_id']} 的字幕边界；重新渲染后进入成片。",
        )
        return self.inspect(job_id, user_id)

    @staticmethod
    def _inspect_bgm_settings(job_id: str, project_dir: Path) -> dict[str, Any]:
        manifest_path = project_dir / "other" / "BGM设置.json"
        empty = {"enabled": False, "tracks": [], "fade_enabled": False, "fade_duration": 1}
        if not manifest_path.is_file():
            return empty
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return empty
        root = (project_dir / "input" / "BGM").resolve()
        tracks: list[dict[str, Any]] = []
        for item in manifest.get("tracks") or []:
            filename = Path(str(item.get("filename") or "")).name
            path = (root / filename).resolve()
            if not filename or root not in path.parents or not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            tracks.append({
                "archived_filename": filename,
                "name": filename,
                "volume_db": float(item.get("volume_db", -10)),
                "duration_seconds": item.get("duration_seconds"),
                "url": f"/api/jobs/{job_id}/visual-bgm/{filename}?v={path.stat().st_mtime_ns}",
            })
        return {
            "enabled": bool(tracks),
            "tracks": tracks,
            "fade_enabled": bool(manifest.get("fade_enabled")),
            "fade_duration": float(manifest.get("fade_duration") or 1),
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

    @staticmethod
    def bgm_path(job_id: str, user_id: int, filename: str) -> Path:
        if Path(filename).name != filename:
            raise FileNotFoundError("invalid BGM filename")
        root = (VisualEditor.output_dir(job_id, user_id) / "input" / "BGM").resolve()
        path = (root / filename).resolve()
        if root not in path.parents or not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            raise FileNotFoundError("BGM file not found")
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
            redraw_output: Path | None = None
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
                if not reference_paths and (
                    item.get("reference_image_ids")
                    or re.search(r"角色形象参考图[1-3]", prompt)
                ):
                    # Module 4 sends the complete catalog whenever a prompt uses
                    # any task reference, preserving the original 图N numbering.
                    reference_paths = self._archived_main_reference_paths(project_dir)
                if reference_paths:
                    render_item["reference_image_paths"] = reference_paths
                    render_item["image_prompt"] = (
                        f"【参考图编号】本次附带的第 1 至第 {len(reference_paths)} 张图片依次对应图1至图{len(reference_paths)}；"
                        "提示词中提及图N时，必须严格以第N张参考图作为该角色或物体的形象依据。\n"
                        f"{prompt}"
                    )
                cloud_pool_client = None
                if bool((job.request or {}).get("use_cloud_image_pool")):
                    if job.user_id is None:
                        raise RuntimeError("使用云端号池重绘需要先登录账户")
                    from .cloud_client import cloud_client_for
                    cloud_pool_client = cloud_client_for(int(job.user_id))
                    runtime = cloud_pool_client.image_pool_runtime()
                    base_url = runtime["base_url"].rstrip("/")
                    provider_configs = [{
                        "endpoint": f"{base_url}/image-pool/generate",
                        "query_url": f"{base_url}/image-pool/query",
                        "upload_url": f"{base_url}/image-pool/media/upload",
                        "account_url": f"{base_url}/image-pool/account-status",
                        "resolution": os.getenv("RUNNINGHUB_RESOLUTION", "1k").strip(),
                        "ratio": os.getenv("RUNNINGHUB_TARGET_RATIO", "2:1").strip(),
                        "api_key": runtime["access_token"],
                        "refresh_token": runtime["refresh_token"],
                        "cloud_base_url": base_url,
                        "account_label": "云端号池",
                        "cloud_pool": "1",
                    }]
                    self._log(job, f"{macro_id} 使用云端图像号池重绘，费用由账户积分结算。")
                else:
                    provider_configs = visual._provider_configs()
                account_pool = visual.shared_runninghub_account_pool(
                    provider_configs,
                    namespace="visual_editor_redraw",
                )
                render_item["progress_label"] = f"{macro_id}（重绘）"
                redraw_dir = JOBS_DIR / job.id / "visual_editor" / "redraw_results"
                redraw_dir.mkdir(parents=True, exist_ok=True)
                redraw_output = redraw_dir / f"{macro_id}_{time.time_ns()}.jpg"
                render_item["_output_path"] = str(redraw_output)
                rendered = visual._render_poster_with_retry(render_item, account_pool)
                if cloud_pool_client is not None and provider_configs:
                    active_config = provider_configs[0]
                    cloud_pool_client.adopt_image_pool_runtime({
                        "access_token": active_config.get("api_key"),
                        "refresh_token": active_config.get("refresh_token"),
                        "expires_in": 900,
                    })
                if not rendered.is_file() or rendered.stat().st_size <= 0:
                    raise FileNotFoundError(f"Image2 返回完成，但没有找到下载后的重绘图片: {rendered}")
                shutil.copy2(rendered, image)
                self._set_image_task(job.id, macro_id, status="completed", action="redraw", message="图片已重绘，请检查效果")
                self._log(job, f"{macro_id} 重绘完成。")
            except Exception as exc:
                self._set_image_task(job.id, macro_id, status="failed", action="redraw", message=str(exc))
                self._log(job, f"{macro_id} 重绘失败：{exc}")
            finally:
                if redraw_output is not None:
                    redraw_output.unlink(missing_ok=True)

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

    def commit_timing_baseline(self, *, job_id: str, user_id: int) -> dict[str, Any]:
        """Promote the current sentence-to-picture allocation to the reset baseline."""
        project_dir = self.output_dir(job_id, user_id)
        backup_path = self._timing_backup_path(project_dir)
        with self._mapping_lock:
            mapping = self._load_mapping(project_dir)
            timeline = self._load_timeline(project_dir)
            mapping, _recovered_timing = self._mapping_with_recovered_timing(project_dir, mapping, timeline)
            self._validate_timing_partition(mapping, timeline)

            if backup_path.is_file():
                stamp = time.strftime("%Y%m%d_%H%M%S")
                history_dir = project_dir / "other" / "时序历史基准" / stamp
                history_dir.mkdir(parents=True, exist_ok=True)
                history_path = history_dir / TIMING_BACKUP_FILENAME
                if history_path.exists():
                    history_path = history_dir / f"画面映射.初始时序_{time.time_ns()}.json"
                shutil.copy2(backup_path, history_path)

            self._save_mapping(project_dir, mapping)
            backup_path.write_text(
                json.dumps(mapping, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        self._set_task(
            job_id,
            status="completed",
            action="commit_timing_baseline",
            message="当前画面时序已保存为新的初始时序",
        )
        return self.inspect(job_id, user_id)

    def restore_timing_history(
        self,
        *,
        job_id: str,
        user_id: int,
        history_id: str,
    ) -> dict[str, Any]:
        """Load a selected archived timing version without changing the reset baseline."""
        project_dir = self.output_dir(job_id, user_id)
        history_path = self._timing_history_path(project_dir, history_id)
        with self._mapping_lock:
            current = self._load_mapping(project_dir)
            archived = json.loads(history_path.read_text(encoding="utf-8"))
            timeline = self._load_timeline(project_dir)
            if not isinstance(archived, list):
                raise ValueError("历史时序文件无效")
            current_by_id = {
                str(item.get("macro_scene_id")): item
                for item in current
                if isinstance(item, dict)
            }
            restored: list[dict[str, Any]] = []
            for saved in archived:
                if not isinstance(saved, dict):
                    continue
                entry = dict(saved)
                edited = current_by_id.get(str(saved.get("macro_scene_id")))
                if edited is not None:
                    entry.update({key: value for key, value in edited.items() if key != "includes_slides"})
                entry["includes_slides"] = list(saved.get("includes_slides") or [])
                restored.append(entry)
            self._validate_timing_partition(restored, timeline)
            self._save_mapping(project_dir, restored)
        self._set_task(
            job_id,
            status="completed",
            action="restore_timing_history",
            message="已切换到所选历史时序",
        )
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

    @staticmethod
    def _archive_bgm_override(project_dir: Path, settings: dict[str, Any]) -> int:
        """Replace a completed project's portable BGM archive for future renders."""
        input_dir = project_dir / "input"
        bgm_dir = input_dir / "BGM"
        other_dir = project_dir / "other"
        bgm_dir.mkdir(parents=True, exist_ok=True)
        other_dir.mkdir(parents=True, exist_ok=True)
        # The directory is project-scoped and contains only archived BGM copies.
        # Delete files individually so an unrelated project folder can never be
        # removed by a malformed/computed path.
        staged_sources: list[tuple[Path, float, float | None]] = []
        for index, item in enumerate(settings.get("tracks") or [], 1):
            source = Path(str(item.get("path") or "")).resolve()
            if not source.is_file() or source.suffix.lower() not in AUDIO_EXTENSIONS:
                raise ValueError(f"BGM 文件不可用：{source.name or '未知文件'}")
            # An existing archived track lives inside the directory that is about
            # to be replaced. Stage it beside the manifest before cleaning.
            if bgm_dir.resolve() in source.parents:
                staged = other_dir / f".bgm_stage_{time.time_ns()}_{index}{source.suffix.lower()}"
                shutil.copy2(source, staged)
                source = staged
            raw_duration = item.get("duration_seconds")
            duration = float(raw_duration) if raw_duration is not None else None
            staged_sources.append((source, float(item.get("volume_db", -10)), duration))
        for existing in bgm_dir.iterdir():
            if existing.is_file():
                existing.unlink()
        manifest_path = other_dir / "BGM设置.json"
        if manifest_path.is_file():
            manifest_path.unlink()

        archived_tracks: list[dict[str, Any]] = []
        for index, (source, volume_db, duration) in enumerate(staged_sources, 1):
            target = bgm_dir / f"{index:03d}{source.suffix.lower()}"
            shutil.copy2(source, target)
            archived_tracks.append({
                "filename": target.name,
                "volume_db": max(-60.0, min(6.0, volume_db)),
                "duration_seconds": duration,
            })
            if source.name.startswith(".bgm_stage_") and source.parent == other_dir:
                source.unlink(missing_ok=True)
        if not archived_tracks:
            return 0
        manifest = {
            "enabled": True,
            "tracks": archived_tracks,
            "fade_enabled": bool(settings.get("fade_enabled")),
            "fade_duration": max(0.1, min(30.0, float(settings.get("fade_duration") or 1))),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(archived_tracks)

    @staticmethod
    def _retire_unselected_video_variants(
        *, project_dir: Path, job: Any, selected: set[str]
    ) -> list[Path]:
        """Keep the current output area honest without deleting an older render."""
        variants = {
            "subtitles": (
                project_dir / "video" / "最终视频_字幕版.mp4",
                JOBS_DIR / job.id / "artifacts" / "final_with_subtitles.mp4",
                "video_with_subtitles",
            ),
            "raw": (
                project_dir / "video" / "最终视频_纯净版.mp4",
                JOBS_DIR / job.id / "artifacts" / "final_raw_presentation.mp4",
                "video_raw",
            ),
        }
        retired: list[Path] = []
        history_dir: Path | None = None
        for variant, (project_video, artifact_video, artifact_key) in variants.items():
            if variant in selected:
                continue
            if project_video.is_file():
                if history_dir is None:
                    history_dir = (
                        project_dir
                        / "other"
                        / "历史成片"
                        / f"{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}"
                    )
                    history_dir.mkdir(parents=True, exist_ok=True)
                archived = history_dir / project_video.name
                shutil.move(str(project_video), str(archived))
                retired.append(archived)
            artifact_video.unlink(missing_ok=True)
            if isinstance(getattr(job, "artifacts", None), dict):
                job.artifacts.pop(artifact_key, None)
        return retired

    def render_video(
        self,
        *,
        job: Any,
        mode: str = "both",
        bgm_override: dict[str, Any] | None = None,
    ) -> None:
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
            render_root: Path | None = None
            try:
                with job_store_lock(job):
                    project_dir = self.output_dir(job.id, int(job.user_id))
                    if bgm_override is not None:
                        bgm_count = self._archive_bgm_override(project_dir, bgm_override)
                        if bgm_count:
                            self._log(job, f"已将当前选择的 {bgm_count} 首 BGM 保存到项目，并用于本次重新渲染。")
                        else:
                            self._log(job, "已清除该项目归档的 BGM，本次重新渲染不添加背景音乐。")
                    self._migrate_segmented_slide_ids(project_dir)
                    render_root = project_dir / "other" / f".render_runtime_{job.id}_{time.time_ns()}"
                    render_paths = self._prepare_render_workspace(render_root)
                    mapping_path = self._mapping_path(project_dir)
                    timeline_path = self._timeline_path(project_dir)
                    if mapping_path.is_file():
                        shutil.copy2(mapping_path, render_paths["visual"] / "poster_mapping.json")
                    if timeline_path.is_file():
                        shutil.copy2(timeline_path, render_paths["visual"] / "fine_grained_timeline.json")
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
                    (render_paths["visual"] / "index.html").write_text(html, encoding="utf-8")
                    for image in (project_dir / "image").glob("*"):
                        if image.suffix.lower() in IMAGE_EXTENSIONS:
                            shutil.copy2(image, render_paths["assets"] / image.name)
                    shutil.copy2(audio_path, render_paths["audio"] / "final_output.wav")
                    shutil.copy2(subtitle_path, render_paths["audio"] / "final_short.srt")
                    render_env = os.environ.copy()
                    render_env["VIDEO_RENDER_VARIANT"] = mode
                    render_env["PYTHONUTF8"] = "1"
                    render_env["OCV_RENDER_WORKSPACE_DIR"] = str(render_root)
                    bgm_manifest_path = project_dir / "other" / "BGM设置.json"
                    if bgm_manifest_path.is_file():
                        bgm_manifest = json.loads(bgm_manifest_path.read_text(encoding="utf-8"))
                        archived_bgm_tracks: list[dict[str, Any]] = []
                        bgm_root = (project_dir / "input" / "BGM").resolve()
                        for item in bgm_manifest.get("tracks") or []:
                            bgm_path = (bgm_root / str(item.get("filename") or "")).resolve()
                            if bgm_root in bgm_path.parents and bgm_path.is_file():
                                archived_bgm_tracks.append({
                                    "path": str(bgm_path),
                                    "volume_db": float(item.get("volume_db", -15)),
                                })
                        if archived_bgm_tracks:
                            render_env["BGM_TRACKS_JSON"] = json.dumps(archived_bgm_tracks, ensure_ascii=False)
                            render_env["BGM_FADE_ENABLED"] = "1" if bool(bgm_manifest.get("fade_enabled")) else "0"
                            render_env["BGM_FADE_DURATION"] = str(float(bgm_manifest.get("fade_duration") or 1))
                            self._log(job, f"重新渲染将沿用项目归档的 {len(archived_bgm_tracks)} 首 BGM。")
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
                    selected = {"subtitles", "raw"} if mode == "both" else {mode}
                    outputs = {
                        "subtitles": (render_paths["final"] / "final_with_subtitles.mp4", video_dir / "最终视频_字幕版.mp4"),
                        "raw": (render_paths["final"] / "final_raw_presentation.mp4", video_dir / "最终视频_纯净版.mp4"),
                    }
                    for variant in selected:
                        source, _target = outputs[variant]
                        if not source.is_file() or source.stat().st_size <= 0:
                            raise FileNotFoundError(f"模块 5 未生成所选的 {variant} 成片")
                    for variant, (source, target) in outputs.items():
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
                    retired = self._retire_unselected_video_variants(
                        project_dir=project_dir,
                        job=job,
                        selected=selected,
                    )
                    if retired:
                        self._log(job, f"已将未选中的旧版本移入历史成片目录，共 {len(retired)} 个；本次没有重新渲染这些版本。")
                    store.update(job, artifacts=dict(job.artifacts), progress=100, message="画面修改已重新渲染")
                    self._set_task(job.id, status="completed", action="render", message="视频已重新渲染")
                    self._log(job, "重新渲染完成，最终视频已更新。")
            except Exception as exc:
                self._set_task(job.id, status="failed", action="render", message=str(exc))
                self._log(job, f"重新渲染失败：{exc}")
            finally:
                if render_root is not None:
                    shutil.rmtree(render_root, ignore_errors=True)
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
