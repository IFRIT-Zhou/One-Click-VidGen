"""Sentence-level TTS regeneration for completed, output-scoped projects."""

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
import uuid
import wave
from pathlib import Path
from typing import Any

from .db import list_media_assets
from .pipeline import (
    JOBS_DIR, PROJECT_ROOT, TTS_OUTPUT_DIR, is_step_workflow_v2,
    persist_step_workflow_state, store,
)
from .visual_editor import VisualEditor


SEGMENT_DIRNAME = "tts_segments"
SEGMENT_MANIFEST = "manifest.json"
SUBTITLE_FILENAME = "最终字幕.srt"
TIMELINE_FILENAME = "画面时间线.json"
TTS_HISTORY_LIMIT = 20


def _srt_time(value: float) -> str:
    milliseconds = max(0, int(round(value * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _parse_srt_time(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})", value.strip())
    if not match:
        raise ValueError(f"无效 SRT 时间: {value}")
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def _rewrite_srt_times(path: Path, warp) -> None:
    if not path.is_file():
        return
    pattern = re.compile(
        r"(?P<start>\d+:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
        r"(?P<end>\d+:\d{2}:\d{2}[,.]\d{3})"
    )
    text = path.read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        start = warp(_parse_srt_time(match.group("start")))
        end = max(start + 0.001, warp(_parse_srt_time(match.group("end"))))
        return f"{_srt_time(start)} --> {_srt_time(end)}"

    path.write_text(pattern.sub(replace, text), encoding="utf-8")


def _srt_entries(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), -1)
        if timing_index < 0:
            continue
        match = re.fullmatch(
            r"(?P<start>\d+:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
            r"(?P<end>\d+:\d{2}:\d{2}[,.]\d{3})",
            lines[timing_index],
        )
        if not match:
            continue
        text = " ".join(lines[timing_index + 1:]).strip()
        if text:
            entries.append({
                "text": text,
                "start": _parse_srt_time(match.group("start")),
                "end": _parse_srt_time(match.group("end")),
            })
    return entries


def _write_srt_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for index, item in enumerate(entries, 1):
        start = max(0.0, float(item.get("start") or 0))
        end = max(start + 0.001, float(item.get("end") or 0))
        blocks.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{str(item.get('text') or '').strip()}")
    path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def _concat_wavs(sources: list[Path], destination: Path) -> None:
    if not sources:
        raise ValueError("没有可合并的逐句音频")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(sources[0]), "rb") as first:
        params = first.getparams()
        expected = (params.nchannels, params.sampwidth, params.framerate)
    with wave.open(str(destination), "wb") as output:
        output.setparams(params)
        for source in sources:
            with wave.open(str(source), "rb") as audio:
                actual = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate())
                if actual != expected:
                    raise ValueError(f"逐句音频格式不一致: {source.name}")
                output.writeframes(audio.readframes(audio.getnframes()))


def _concat_segment_wavs(segments: list[dict[str, Any]], segment_dir: Path, destination: Path) -> None:
    """Concatenate speech WAVs and materialize boundary silence from metadata."""
    if not segments:
        raise ValueError("没有可合并的逐句音频")
    sources = [segment_dir / str(item.get("filename") or "") for item in segments]
    if any(not source.is_file() for source in sources):
        raise FileNotFoundError("逐句音频文件不完整")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(sources[0]), "rb") as first:
        params = first.getparams()
        expected = (params.nchannels, params.sampwidth, params.framerate)
    with wave.open(str(destination), "wb") as output:
        output.setparams(params)
        silence_frame = b"\0" * (params.nchannels * params.sampwidth)
        for item, source in zip(segments, sources):
            with wave.open(str(source), "rb") as audio:
                actual = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate())
                if actual != expected:
                    raise ValueError(f"逐句音频格式不一致: {source.name}")
                output.writeframes(audio.readframes(audio.getnframes()))
            pause = max(0.0, min(30.0, float(item.get("pause_after") or 0)))
            if pause:
                output.writeframes(silence_frame * int(round(pause * params.framerate)))


def _build_regeneration_plan(
    reading_texts: dict[int, str],
    engine: str,
) -> tuple[list[str], dict[int, list[int]], dict[int, int]]:
    """Flatten selected sentences into engine-safe synthesis chunks.

    Oversized IndexTTS-2.5 text is generated in safe pieces, but the mapping
    keeps the editor's original sentence boundary authoritative. The pieces
    are merged back before timestamps, subtitles or the visual timeline move.
    """

    flattened: list[str] = []
    chunk_positions: dict[int, list[int]] = {}
    token_totals: dict[int, int] = {}
    token_count = None
    if engine == "indextts25":
        from .tts_segmentation import build_indextts25_token_counter

        token_count = build_indextts25_token_counter()

    for index, text in reading_texts.items():
        chunks = [text]
        if token_count is not None:
            from .tts_segmentation import segment_indextts25_text

            chunks, _source, total = segment_indextts25_text(
                text,
                token_count=token_count,
                agent_enabled=False,
            )
            token_totals[index] = total
        start = len(flattened)
        flattened.extend(chunks)
        chunk_positions[index] = list(range(start, len(flattened)))
    return flattened, chunk_positions, token_totals


class TtsEditor:
    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _set_task(self, job_id: str, **values: Any) -> None:
        with self._lock:
            current = dict(self._tasks.get(job_id) or {})
            current.update(values)
            current["updated_at"] = time.time()
            self._tasks[job_id] = current

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._tasks.get(job_id) or {"status": "idle", "message": ""})

    @staticmethod
    def _project_dir(job_id: str, user_id: int) -> Path:
        try:
            return VisualEditor.output_dir(job_id, user_id)
        except FileNotFoundError:
            root = TTS_OUTPUT_DIR.resolve()
            for asset in list_media_assets(user_id=user_id, generation_job_id=job_id):
                if str(asset.get("role") or "") != "tts_output":
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
            raise FileNotFoundError("配音项目输出文件夹不可用")

    @staticmethod
    def _ensure_module1_layout(job_id: str, project_dir: Path) -> None:
        """Give stand-alone TTS outputs the same editable layout as video projects."""
        flat_audio = project_dir / "配音.wav"
        flat_subtitle = project_dir / "配音字幕.srt"
        input_dir = project_dir / "input"
        other_dir = project_dir / "other"
        input_dir.mkdir(parents=True, exist_ok=True)
        other_dir.mkdir(parents=True, exist_ok=True)
        if flat_audio.is_file() and not (input_dir / "配音.wav").is_file():
            shutil.copy2(flat_audio, input_dir / "配音.wav")
        if flat_subtitle.is_file() and not (other_dir / SUBTITLE_FILENAME).is_file():
            shutil.copy2(flat_subtitle, other_dir / SUBTITLE_FILENAME)
        archived_segments = JOBS_DIR / job_id / "artifacts" / SEGMENT_DIRNAME
        target_segments = other_dir / SEGMENT_DIRNAME
        if archived_segments.is_dir() and not (target_segments / SEGMENT_MANIFEST).is_file():
            shutil.copytree(archived_segments, target_segments, dirs_exist_ok=True)

    @staticmethod
    def _sync_module1_flat_outputs(project_dir: Path, job_id: str | None = None) -> None:
        """Keep the original user-facing stand-alone filenames current."""
        audio = project_dir / "input" / "配音.wav"
        subtitle = project_dir / "other" / SUBTITLE_FILENAME
        if (project_dir / "配音.wav").exists() and audio.is_file():
            shutil.copy2(audio, project_dir / "配音.wav")
        if (project_dir / "配音字幕.srt").exists() and subtitle.is_file():
            shutil.copy2(subtitle, project_dir / "配音字幕.srt")
        if job_id:
            artifacts = JOBS_DIR / job_id / "artifacts"
            if (artifacts / "final_output.wav").exists() and audio.is_file():
                shutil.copy2(audio, artifacts / "final_output.wav")
            if (artifacts / "final_output.srt").exists() and subtitle.is_file():
                shutil.copy2(subtitle, artifacts / "final_output.srt")

    @staticmethod
    def _segment_dir(project_dir: Path) -> Path:
        return project_dir / "other" / SEGMENT_DIRNAME

    @staticmethod
    def _load_manifest(project_dir: Path) -> dict[str, Any]:
        path = TtsEditor._segment_dir(project_dir) / SEGMENT_MANIFEST
        if not path.is_file():
            raise FileNotFoundError("该任务生成时尚未保存逐句音频，请使用新版任务重新生成后再进行单句重配")
        payload = json.loads(path.read_text(encoding="utf-8"))
        segments = payload.get("segments") if isinstance(payload, dict) else None
        if not isinstance(segments, list) or not segments:
            raise ValueError("逐句配音清单损坏或为空")
        return payload

    @staticmethod
    def _history_root(project_dir: Path) -> Path:
        return TtsEditor._segment_dir(project_dir) / "history"

    @staticmethod
    def _history_entries(project_dir: Path) -> list[Path]:
        root = TtsEditor._history_root(project_dir)
        return sorted(
            [path for path in root.iterdir() if path.is_dir()] if root.is_dir() else [],
            key=lambda path: path.name,
        )

    @staticmethod
    def _snapshot_history(
        project_dir: Path,
        manifest: dict[str, Any],
        affected_indices: list[int],
        action: str,
    ) -> tuple[int, bool]:
        segment_dir = TtsEditor._segment_dir(project_dir)
        history_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000_000:09d}"
        history = TtsEditor._history_root(project_dir) / history_id
        history.mkdir(parents=True, exist_ok=False)
        for source in (
            segment_dir / SEGMENT_MANIFEST,
            project_dir / "other" / SUBTITLE_FILENAME,
            project_dir / "other" / TIMELINE_FILENAME,
        ):
            if source.is_file():
                shutil.copy2(source, history / source.name)
        by_index = {
            int(item.get("index") or 0): item
            for item in manifest.get("segments") or []
            if isinstance(item, dict)
        }
        backed_up: list[str] = []
        for index in sorted(set(affected_indices)):
            item = by_index.get(index)
            filename = Path(str((item or {}).get("filename") or "")).name
            source = segment_dir / filename
            if filename and source.is_file():
                shutil.copy2(source, history / filename)
                backed_up.append(filename)
        (history / "history.json").write_text(
            json.dumps(
                {"action": action, "affected_indices": affected_indices, "audio_files": backed_up},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        entries = TtsEditor._history_entries(project_dir)
        pruned = len(entries) > TTS_HISTORY_LIMIT
        for old in entries[:-TTS_HISTORY_LIMIT]:
            shutil.rmtree(old, ignore_errors=True)
        return min(len(entries), TTS_HISTORY_LIMIT), pruned

    @staticmethod
    def _refresh_segment_timing(segments: list[dict[str, Any]], segment_dir: Path) -> float:
        current = 0.0
        for index, item in enumerate(segments, 1):
            audio = segment_dir / str(item.get("filename") or "")
            with wave.open(str(audio), "rb") as reader:
                duration = reader.getnframes() / reader.getframerate()
            item["index"] = index
            item["start"] = round(current, 6)
            item["end"] = round(current + duration, 6)
            item["duration"] = round(duration, 6)
            item["pause_after"] = round(max(0.0, min(30.0, float(item.get("pause_after") or 0))), 3)
            current += duration + item["pause_after"]
        return round(current, 6)

    def _synthesize_parts(
        self,
        *,
        job: Any,
        user_id: int,
        project_dir: Path,
        manifest: dict[str, Any],
        texts: list[str],
        work_dir: Path,
    ) -> list[Path]:
        """Generate logical replacement parts without touching live project files."""
        engine = str(manifest.get("engine") or job.request.get("tts_engine") or "indextts25")
        if engine == "indextts2":
            engine = "indextts25"
        reading = {index: text for index, text in enumerate(texts, 1)}
        chunks, positions, _totals = _build_regeneration_plan(reading, engine)
        output_dir = work_dir / "output"
        generated_dir = work_dir / "segments"
        chunks_path = work_dir / "chunks.json"
        script_path = work_dir / "selected.txt"
        work_dir.mkdir(parents=True, exist_ok=True)
        chunks_path.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
        script_path.write_text("\n".join(chunks), encoding="utf-8")
        if engine == "cluster":
            from .cloud_client import cloud_client_for
            from .cloud_tts import synthesize_cloud_tts

            cloud_request = dict(job.request)
            cloud_request.pop("_cloud_job_id", None)
            cloud_request.pop("_cloud_job_status", None)
            for key in (
                "cluster_voice_type", "cluster_voice_id", "tts_speed", "tts_volume",
                "tts_pitch", "tts_emotion", "tts_emotion_weight",
            ):
                if key in manifest:
                    cloud_request[key] = manifest[key]
            synthesize_cloud_tts(
                client=cloud_client_for(user_id),
                local_job_id=f"{job.id}-boundary-{uuid.uuid4().hex[:10]}",
                request=cloud_request,
                output_dir=output_dir,
                segment_archive_dir=generated_dir,
                temp_dir=work_dir / "temp",
                is_cancelled=lambda: False,
                on_progress=lambda percent, message: self._set_task(
                    job.id, status="running", progress=max(1, min(95, int(percent))), message=message
                ),
                on_log=lambda line: store.log(job, f"[断句重配] {line}"),
                on_remote_job=lambda _job_id, _payload: None,
                chunks_override=chunks,
            )
        else:
            command = [
                sys.executable, str(PROJECT_ROOT / "module1_agent_director.py"),
                "--text", str(script_path), "--job-id", f"{job.id}_boundary_edit",
                "--tts-engine", engine, "--chunks-json", str(chunks_path),
                "--output-dir", str(output_dir), "--segment-archive-dir", str(generated_dir),
                "--tts-speed", str(manifest.get("tts_speed") or 1),
                "--tts-volume", str(manifest.get("tts_volume") or 1),
                "--tts-pitch", str(manifest.get("tts_pitch") or 0),
                "--tts-parallelism", str(manifest.get("tts_parallelism") or 1),
            ]
            if user_id:
                command.extend(["--user-id", str(user_id)])
            if engine == "qwen":
                command.extend(["--qwen-voice", str(manifest.get("qwen_voice") or "Elias")])
                instructions = str(manifest.get("qwen_instructions") or "").strip()
                if instructions:
                    command.extend(["--qwen-instructions", instructions])
            else:
                archived_voice = next((project_dir / "input").glob("TTS参考音色.*"), None)
                if archived_voice and archived_voice.is_file():
                    command.extend(["--tts-voice-path", str(archived_voice)])
                else:
                    command.extend(["--tts-voice-id", str(manifest.get("tts_voice_id") or "voice_05.wav")])
                emotion = str(manifest.get("tts_emotion") or "").strip()
                if emotion:
                    command.extend(["--tts-emotion", emotion, "--tts-emotion-weight", str(manifest.get("tts_emotion_weight", 0.65))])
            process = subprocess.run(
                command, cwd=str(PROJECT_ROOT), env=os.environ.copy(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            for line in process.stdout.splitlines():
                if line.strip():
                    store.log(job, f"[断句重配] {line.strip()}")
            if process.returncode != 0:
                raise RuntimeError(f"TTS 子任务退出码 {process.returncode}")
        generated = json.loads((generated_dir / SEGMENT_MANIFEST).read_text(encoding="utf-8")).get("segments") or []
        if len(generated) != len(chunks):
            raise RuntimeError("断句重配结果与安全分段计划不一致")
        results: list[Path] = []
        for logical_index in range(1, len(texts) + 1):
            sources = [generated_dir / str(generated[pos]["filename"]) for pos in positions[logical_index]]
            result = work_dir / f"part_{logical_index}.wav"
            if len(sources) == 1:
                shutil.copy2(sources[0], result)
            else:
                _concat_wavs(sources, result)
            with wave.open(str(result), "rb") as audio:
                if audio.getnframes() <= 0:
                    raise RuntimeError(f"第 {logical_index} 段重配音频为空")
            results.append(result)
        return results

    @staticmethod
    def _migrate_legacy_archive(job_id: str, project_dir: Path) -> bool:
        """Recover exact original TTS chunks from Module 1 SRT when old artifacts still exist."""
        segment_dir = TtsEditor._segment_dir(project_dir)
        if (segment_dir / SEGMENT_MANIFEST).is_file():
            return True
        module1_srt = JOBS_DIR / job_id / "artifacts" / "final_output.srt"
        audio_path = project_dir / "input" / "配音.wav"
        entries = _srt_entries(module1_srt)
        if not entries or not audio_path.is_file():
            return False
        params_path = project_dir / "other" / "任务参数.json"
        try:
            request = json.loads(params_path.read_text(encoding="utf-8")) if params_path.is_file() else {}
        except json.JSONDecodeError:
            request = {}
        segment_dir.mkdir(parents=True, exist_ok=True)
        items: list[dict[str, Any]] = []
        with wave.open(str(audio_path), "rb") as source:
            frame_rate = source.getframerate()
            total_frames = source.getnframes()
            params = source.getparams()
            for index, entry in enumerate(entries, 1):
                start_frame = min(total_frames, max(0, int(round(float(entry["start"]) * frame_rate))))
                end_frame = min(total_frames, max(start_frame + 1, int(round(float(entry["end"]) * frame_rate))))
                source.setpos(start_frame)
                frames = source.readframes(end_frame - start_frame)
                filename = f"segment_{index:04d}.wav"
                with wave.open(str(segment_dir / filename), "wb") as target:
                    target.setparams(params)
                    target.writeframes(frames)
                duration = (end_frame - start_frame) / frame_rate
                items.append({
                    "index": index, "text": entry["text"], "filename": filename,
                    "start": round(float(entry["start"]), 6),
                    "end": round(float(entry["start"]) + duration, 6),
                    "duration": round(duration, 6),
                })
        manifest = {
            "schema_version": 1,
            "migrated_from_module1_srt": True,
            "engine": "indextts25" if request.get("tts_engine") == "indextts2" else (request.get("tts_engine") or "indextts25"),
            "tts_voice_id": request.get("tts_voice_id") or "voice_05.wav",
            "tts_speed": request.get("tts_speed") or 1,
            "tts_volume": request.get("tts_volume") or 1,
            "tts_pitch": request.get("tts_pitch") or 0,
            "tts_emotion": request.get("tts_emotion") or "",
            "tts_emotion_weight": request.get("tts_emotion_weight", 0.65),
            "qwen_voice": request.get("qwen_tts_voice") or "Elias",
            "qwen_instructions": request.get("qwen_tts_instructions") or "",
            "total_duration": round(sum(float(item["duration"]) for item in items), 6),
            "segments": items,
        }
        (segment_dir / SEGMENT_MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True

    def inspect(self, job_id: str, user_id: int) -> dict[str, Any]:
        project_dir = self._project_dir(job_id, user_id)
        stored_job = store.get(job_id)
        uploaded_finished_audio = bool(stored_job and stored_job.request.get("skip_tts"))
        self._ensure_module1_layout(job_id, project_dir)
        self._migrate_legacy_archive(job_id, project_dir)
        try:
            manifest = self._load_manifest(project_dir)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return {
                "available": False,
                "message": str(exc),
                "segments": [],
                "task": self.status(job_id),
            }
        segment_dir = self._segment_dir(project_dir)
        items: list[dict[str, Any]] = []
        for raw in manifest["segments"]:
            if not isinstance(raw, dict):
                continue
            index = int(raw.get("index") or 0)
            filename = Path(str(raw.get("filename") or "")).name
            audio = segment_dir / filename
            if index <= 0 or not filename or not audio.is_file():
                continue
            subtitle_text = str(raw.get("text") or "").strip()
            tts_text = str(raw.get("tts_text") or subtitle_text).strip()
            items.append({
                **raw,
                "index": index,
                "text": subtitle_text,
                "tts_text": tts_text,
                "pronunciation_modified": bool(tts_text and tts_text != subtitle_text),
                "audio_url": f"/api/jobs/{job_id}/tts-editor/audio/{index}?v={audio.stat().st_mtime_ns}",
            })
        return {
            "project_id": job_id,
            "available": bool(items),
            "message": "可选择一条或多条重新配音；完成后请重新渲染视频。" if items else "逐句音频文件不完整",
            "revision": int(manifest.get("revision") or 0),
            "engine": "indextts25" if manifest.get("engine") == "indextts2" else (manifest.get("engine") or "indextts25"),
            "settings": {
                "tts_voice_id": manifest.get("tts_voice_id") or "voice_05.wav",
                "tts_speed": manifest.get("tts_speed", 1),
                "tts_volume": manifest.get("tts_volume", 1),
                "tts_pitch": manifest.get("tts_pitch", 0),
                "tts_parallelism": manifest.get("tts_parallelism", 1),
                "tts_emotion": manifest.get("tts_emotion") or "",
                "tts_emotion_weight": manifest.get("tts_emotion_weight", 0.65),
                "cluster_voice_type": manifest.get("cluster_voice_type") or "preset",
                "cluster_voice_id": manifest.get("cluster_voice_id") or "",
                "qwen_voice": manifest.get("qwen_voice") or "Elias",
                "qwen_instructions": manifest.get("qwen_instructions") or "",
            },
            "segments": items,
            "history_count": len(self._history_entries(project_dir)),
            "history_limit": TTS_HISTORY_LIMIT,
            "structural_edit_available": not uploaded_finished_audio,
            "structural_edit_message": (
                "上传的成品配音只能调整已有句间停顿；拆句或合并会改变原音频，需要配音引擎才能重配。"
                if uploaded_finished_audio else ""
            ),
            "task": self.status(job_id),
        }

    def audio_path(self, job_id: str, user_id: int, index: int) -> Path:
        project_dir = self._project_dir(job_id, user_id)
        self._migrate_legacy_archive(job_id, project_dir)
        manifest = self._load_manifest(project_dir)
        raw = next((item for item in manifest["segments"] if int(item.get("index") or 0) == index), None)
        if not isinstance(raw, dict):
            raise FileNotFoundError("找不到该句配音")
        segment_dir = self._segment_dir(project_dir).resolve()
        path = (segment_dir / Path(str(raw.get("filename") or "")).name).resolve()
        if segment_dir not in path.parents or not path.is_file():
            raise FileNotFoundError("找不到该句配音文件")
        return path

    @staticmethod
    def _apply_boundary_delta(project_dir: Path, boundary: float, delta: float) -> None:
        if abs(delta) < 0.0005:
            return
        subtitle_path = project_dir / "other" / SUBTITLE_FILENAME
        entries = _srt_entries(subtitle_path)
        for item in entries:
            start = float(item["start"])
            end = float(item["end"])
            if start >= boundary - 0.0005:
                item["start"] = max(0.0, start + delta)
                item["end"] = max(item["start"] + 0.001, end + delta)
            elif end > boundary:
                item["end"] = max(start + 0.001, end + delta)
        if entries:
            _write_srt_entries(subtitle_path, entries)
        timeline_path = project_dir / "other" / TIMELINE_FILENAME
        if not timeline_path.is_file():
            return
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        if not isinstance(timeline, list):
            return
        for item in timeline:
            if not isinstance(item, dict):
                continue
            start = float(item.get("start") or 0)
            end = float(item.get("end") or 0)
            if start >= boundary - 0.0005:
                item["start"] = round(max(0.0, start + delta), 6)
                item["end"] = round(max(float(item["start"]) + 0.001, end + delta), 6)
            elif end >= boundary - 0.0005:
                # Keep the preceding picture visible while subtitles are blank.
                item["end"] = round(max(start + 0.001, end + delta), 6)
        timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_pause(self, *, job: Any, user_id: int, left_index: int, seconds: float) -> dict[str, Any]:
        if self.status(job.id).get("status") == "running":
            raise RuntimeError("该项目已有音频编辑任务正在运行")
        project_dir = self._project_dir(job.id, user_id)
        self._ensure_module1_layout(job.id, project_dir)
        self._migrate_legacy_archive(job.id, project_dir)
        manifest = self._load_manifest(project_dir)
        segments = [dict(item) for item in manifest["segments"] if isinstance(item, dict)]
        if left_index < 1 or left_index >= len(segments):
            raise ValueError("只能在两句配音的交界处设置停顿")
        seconds = round(max(0.0, min(30.0, float(seconds))), 3)
        left = segments[left_index - 1]
        old_pause = max(0.0, float(left.get("pause_after") or 0))
        delta = seconds - old_pause
        if abs(delta) < 0.0005:
            return {"ok": True, "history_count": len(self._history_entries(project_dir)), "message": "停顿时长未变化"}
        boundary = float(left.get("end") or 0) + old_pause
        history_count, pruned = self._snapshot_history(project_dir, manifest, [], "修改额外停顿")
        try:
            left["pause_after"] = seconds
            manifest["segments"] = segments
            manifest["total_duration"] = self._refresh_segment_timing(segments, self._segment_dir(project_dir))
            manifest["revision"] = int(manifest.get("revision") or 0) + 1
            manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._apply_boundary_delta(project_dir, boundary, delta)
            temp_audio = project_dir / "input" / ".配音.pause-edit.wav"
            _concat_segment_wavs(segments, self._segment_dir(project_dir), temp_audio)
            temp_audio.replace(project_dir / "input" / "配音.wav")
            (self._segment_dir(project_dir) / SEGMENT_MANIFEST).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._sync_module1_flat_outputs(project_dir, job.id)
        except Exception:
            self._restore_history(project_dir, self._history_entries(project_dir)[-1], consume=True)
            raise
        store.log(job, f"已将第 {left_index}、{left_index + 1} 句之间的额外停顿设为 {seconds:.1f} 秒")
        return {
            "ok": True, "history_count": history_count, "history_pruned": pruned,
            "message": f"已保存 {seconds:.1f} 秒额外停顿",
        }

    @staticmethod
    def _reshape_subtitles_for_span(
        project_dir: Path,
        old_start: float,
        old_end: float,
        new_parts: list[dict[str, Any]],
        requested_texts: list[str],
    ) -> None:
        path = project_dir / "other" / SUBTITLE_FILENAME
        entries = _srt_entries(path)
        if not entries:
            return
        affected = [i for i, item in enumerate(entries) if float(item["end"]) > old_start + 0.0005 and float(item["start"]) < old_end - 0.0005]
        if not affected:
            raise ValueError("无法在最终字幕中定位需要调整的句子")
        first, last = affected[0], affected[-1]
        preserved_text = "".join(str(entries[i]["text"]) for i in affected)
        weights = [max(1, len(re.sub(r"\s+", "", text))) for text in requested_texts]
        split_texts: list[str] = []
        cursor = 0
        total_weight = sum(weights)
        for position, weight in enumerate(weights):
            if position == len(weights) - 1:
                end = len(preserved_text)
            else:
                end = round(len(preserved_text) * sum(weights[: position + 1]) / total_weight)
            split_texts.append(preserved_text[cursor:end])
            cursor = end
        replacements = [
            {"text": text or requested_texts[index], "start": part["start"], "end": part["end"]}
            for index, (text, part) in enumerate(zip(split_texts, new_parts))
        ]
        delta = float(new_parts[-1]["end"]) - old_end
        following = [dict(item) for item in entries[last + 1:]]
        for item in following:
            item["start"] = float(item["start"]) + delta
            item["end"] = float(item["end"]) + delta
        _write_srt_entries(path, [*entries[:first], *replacements, *following])

    @staticmethod
    def _warp_timeline_span(project_dir: Path, old_start: float, old_end: float, new_end: float) -> None:
        path = project_dir / "other" / TIMELINE_FILENAME
        if not path.is_file():
            return
        timeline = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(timeline, list):
            return
        old_duration = max(0.001, old_end - old_start)
        new_duration = max(0.001, new_end - old_start)
        delta = new_end - old_end

        def warp(value: float) -> float:
            if value <= old_start:
                return value
            if value < old_end:
                return old_start + (value - old_start) * new_duration / old_duration
            return value + delta

        for item in timeline:
            if isinstance(item, dict):
                start = warp(float(item.get("start") or 0))
                end = max(start + 0.001, warp(float(item.get("end") or 0)))
                item["start"], item["end"] = round(start, 6), round(end, 6)
        path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")

    def resegment(
        self,
        *,
        job: Any,
        user_id: int,
        start_index: int,
        replace_count: int,
        parts: list[dict[str, Any]],
        settings_override: dict[str, Any] | None = None,
    ) -> None:
        if bool(job.request.get("skip_tts")):
            raise ValueError("上传成品配音只能修改现有交界处停顿，不能断句或合并重配")
        if replace_count not in {1, 2} or len(parts) not in {1, 2}:
            raise ValueError("断句调整范围无效")
        if self.status(job.id).get("status") == "running":
            raise RuntimeError("该项目已有音频编辑任务正在运行")
        self._set_task(job.id, status="running", progress=0, message="准备调整断句")

        def work() -> None:
            project_dir: Path | None = None
            history: Path | None = None
            work_dir: Path | None = None
            try:
                project_dir = self._project_dir(job.id, user_id)
                self._ensure_module1_layout(job.id, project_dir)
                manifest = self._load_manifest(project_dir)
                for key, value in dict(settings_override or {}).items():
                    if key in {
                        "tts_voice_id", "tts_speed", "tts_volume", "tts_pitch", "tts_parallelism",
                        "tts_emotion", "tts_emotion_weight", "cluster_voice_type", "cluster_voice_id",
                        "qwen_voice", "qwen_instructions",
                    }:
                        manifest[key] = value
                segments = [dict(item) for item in manifest["segments"] if isinstance(item, dict)]
                offset = start_index - 1
                if offset < 0 or offset + replace_count > len(segments):
                    raise ValueError("需要调整的原句不存在")
                replaced = segments[offset: offset + replace_count]
                old_display = "".join(str(item.get("text") or "") for item in replaced)
                display_texts = [str(item.get("text") or "").strip() for item in parts]
                reading_texts = [str(item.get("tts_text") or item.get("text") or "").strip() for item in parts]
                if any(not text for text in display_texts + reading_texts):
                    raise ValueError("断句前后文字不能为空")
                compact = lambda value: re.sub(r"\s+", "", value)
                if compact(old_display) != compact("".join(display_texts)):
                    raise ValueError("调整断点不能增加、删除或改写字幕文字")
                engine = str(manifest.get("engine") or "indextts25")
                if engine in {"indextts2", "indextts25", "cluster"}:
                    from .tts_segmentation import INDEXTTS25_SEGMENT_MAX_TOKENS, build_indextts25_token_counter
                    count = build_indextts25_token_counter()
                    totals = [count(text) for text in reading_texts]
                    if any(total > INDEXTTS25_SEGMENT_MAX_TOKENS for total in totals):
                        raise ValueError(f"断句后仍有片段超过 110 token：{totals}")
                work_dir = self._segment_dir(project_dir) / f".boundary-{uuid.uuid4().hex}"
                generated = self._synthesize_parts(
                    job=job, user_id=user_id, project_dir=project_dir,
                    manifest=manifest, texts=reading_texts, work_dir=work_dir,
                )
                history_count, pruned = self._snapshot_history(
                    project_dir, manifest,
                    [int(item.get("index") or 0) for item in replaced],
                    "新增断点" if replace_count == 1 and len(parts) == 2 else "调整断点",
                )
                history = self._history_entries(project_dir)[-1]
                old_start = float(replaced[0].get("start") or 0)
                old_end = float(replaced[-1].get("end") or 0)
                trailing_pause = float(replaced[-1].get("pause_after") or 0)
                new_items: list[dict[str, Any]] = []
                for position, (part, audio) in enumerate(zip(parts, generated), 1):
                    filename = f"segment_{uuid.uuid4().hex[:12]}.wav"
                    shutil.copy2(audio, self._segment_dir(project_dir) / filename)
                    new_items.append({
                        "text": display_texts[position - 1],
                        "tts_text": reading_texts[position - 1],
                        "filename": filename,
                        "pause_after": (
                            max(0.0, min(30.0, float(part.get("pause_after") or 0)))
                            if position < len(parts) else trailing_pause
                        ),
                    })
                updated = [*segments[:offset], *new_items, *segments[offset + replace_count:]]
                manifest["segments"] = updated
                manifest["total_duration"] = self._refresh_segment_timing(updated, self._segment_dir(project_dir))
                manifest["revision"] = int(manifest.get("revision") or 0) + 1
                manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._reshape_subtitles_for_span(project_dir, old_start, old_end, new_items, display_texts)
                self._warp_timeline_span(project_dir, old_start, old_end, float(new_items[-1]["end"]))
                temp_audio = project_dir / "input" / ".配音.boundary-edit.wav"
                _concat_segment_wavs(updated, self._segment_dir(project_dir), temp_audio)
                temp_audio.replace(project_dir / "input" / "配音.wav")
                (self._segment_dir(project_dir) / SEGMENT_MANIFEST).write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                used = {str(item.get("filename") or "") for item in updated}
                for item in replaced:
                    old_file = self._segment_dir(project_dir) / str(item.get("filename") or "")
                    if old_file.name not in used:
                        old_file.unlink(missing_ok=True)
                self._sync_module1_flat_outputs(project_dir, job.id)
                self._set_task(
                    job.id, status="completed", progress=100,
                    message=f"断句已调整；历史 {history_count}/20" + ("，最早一版已自动清理" if pruned else ""),
                )
                store.log(job, f"断句调整完成：从第 {start_index} 句开始，以 {len(parts)} 句替换原 {replace_count} 句")
            except Exception as exc:
                if project_dir is not None and history is not None and history.is_dir():
                    try:
                        self._restore_history(project_dir, history, consume=True)
                    except Exception as rollback_exc:
                        store.log(job, f"断句调整回滚失败：{rollback_exc}")
                self._set_task(job.id, status="failed", progress=0, message=f"断句调整失败：{exc}")
                store.log(job, f"断句调整失败：{type(exc).__name__}: {exc}")
            finally:
                if work_dir is not None:
                    shutil.rmtree(work_dir, ignore_errors=True)

        threading.Thread(target=work, daemon=True, name=f"tts-boundary-{job.id}").start()

    @staticmethod
    def _restore_history(project_dir: Path, history: Path, *, consume: bool) -> None:
        segment_dir = TtsEditor._segment_dir(project_dir)
        manifest_backup = history / SEGMENT_MANIFEST
        if not manifest_backup.is_file():
            raise FileNotFoundError("音频编辑历史不完整")
        restored = json.loads(manifest_backup.read_text(encoding="utf-8"))
        filenames = {
            Path(str(item.get("filename") or "")).name
            for item in restored.get("segments") or [] if isinstance(item, dict)
        }
        for backup in history.glob("*.wav"):
            shutil.copy2(backup, segment_dir / backup.name)
        for current in segment_dir.glob("*.wav"):
            if current.name not in filenames:
                current.unlink(missing_ok=True)
        shutil.copy2(manifest_backup, segment_dir / SEGMENT_MANIFEST)
        for name in (SUBTITLE_FILENAME, TIMELINE_FILENAME):
            backup = history / name
            target = project_dir / "other" / name
            if backup.is_file():
                shutil.copy2(backup, target)
            else:
                target.unlink(missing_ok=True)
        temp_audio = project_dir / "input" / ".配音.undo.wav"
        _concat_segment_wavs(restored["segments"], segment_dir, temp_audio)
        temp_audio.replace(project_dir / "input" / "配音.wav")
        TtsEditor._sync_module1_flat_outputs(project_dir)
        if consume:
            shutil.rmtree(history, ignore_errors=True)

    def undo(self, *, job: Any, user_id: int) -> dict[str, Any]:
        if self.status(job.id).get("status") == "running":
            raise RuntimeError("请等待当前音频编辑完成后再撤销")
        project_dir = self._project_dir(job.id, user_id)
        entries = self._history_entries(project_dir)
        if not entries:
            raise ValueError("没有可撤销的音频编辑")
        history = entries[-1]
        meta_path = history / "history.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        self._restore_history(project_dir, history, consume=True)
        self._sync_module1_flat_outputs(project_dir, job.id)
        store.log(job, f"已撤销音频编辑：{meta.get('action') or '上一步'}")
        return {"ok": True, "history_count": len(self._history_entries(project_dir)), "message": "已撤销上一步音频编辑"}

    def regenerate(
        self,
        *,
        job: Any,
        user_id: int,
        indices: list[int],
        settings_override: dict[str, Any] | None = None,
        text_overrides: dict[int, str] | None = None,
    ) -> None:
        selected = sorted(set(int(value) for value in indices if int(value) > 0))
        if not selected:
            raise ValueError("请至少选择一句需要重配的内容")
        if bool(job.request.get("skip_tts")):
            raise ValueError("该项目使用的是用户上传配音，无法调用 TTS 进行单句重配")
        with self._lock:
            if self._tasks.get(job.id, {}).get("status") == "running":
                raise RuntimeError("该项目已有单句重配任务正在运行")
        project_dir = self._project_dir(job.id, user_id)
        self._migrate_legacy_archive(job.id, project_dir)
        manifest = self._load_manifest(project_dir)
        valid = {int(item.get("index") or 0) for item in manifest["segments"] if isinstance(item, dict)}
        if any(value not in valid for value in selected):
            raise ValueError("选择中包含不存在的配音句号")
        normalized_text_overrides: dict[int, str] = {}
        for raw_index, raw_text in dict(text_overrides or {}).items():
            index = int(raw_index)
            if index not in selected:
                raise ValueError("朗读文本只能修改本次选中的句子")
            text = str(raw_text or "").strip()
            if not text:
                raise ValueError(f"第 {index} 句朗读文本不能为空")
            if len(text) > 1200:
                raise ValueError(f"第 {index} 句朗读文本过长，请缩短后重试")
            normalized_text_overrides[index] = text
        self._set_task(job.id, status="running", progress=0, message=f"准备重配 {len(selected)} 句")

        def work() -> None:
            try:
                self._regenerate_sync(
                    job,
                    user_id,
                    selected,
                    dict(settings_override or {}),
                    normalized_text_overrides,
                )
                self._set_task(
                    job.id, status="completed", progress=100,
                    message=f"已重配 {len(selected)} 句，并重建配音与时间轴；请点击重新渲染。",
                )
                store.log(job, f"单句重配完成：已更新 {len(selected)} 句配音、整条音频、字幕和画面时间线")
            except Exception as exc:
                self._set_task(job.id, status="failed", progress=0, message=f"单句重配失败：{exc}")
                store.log(job, f"单句重配失败：{type(exc).__name__}: {exc}")

        threading.Thread(target=work, daemon=True, name=f"tts-edit-{job.id}").start()

    def _regenerate_sync(
        self,
        job: Any,
        user_id: int,
        indices: list[int],
        settings_override: dict[str, Any],
        text_overrides: dict[int, str],
    ) -> None:
        project_dir = self._project_dir(job.id, user_id)
        segment_dir = self._segment_dir(project_dir)
        manifest_path = segment_dir / SEGMENT_MANIFEST
        manifest = self._load_manifest(project_dir)
        allowed_settings = {
            "tts_voice_id", "tts_speed", "tts_volume", "tts_pitch", "tts_parallelism",
            "tts_emotion", "tts_emotion_weight", "cluster_voice_type", "cluster_voice_id",
            "qwen_voice", "qwen_instructions",
        }
        for key, value in settings_override.items():
            if key in allowed_settings:
                manifest[key] = value
        segments = [dict(item) for item in manifest["segments"] if isinstance(item, dict)]
        by_index = {int(item["index"]): item for item in segments}
        reading_texts = {
            index: str(
                text_overrides.get(index)
                or by_index[index].get("tts_text")
                or by_index[index].get("text")
                or ""
            ).strip()
            for index in indices
        }
        if any(not text for text in reading_texts.values()):
            raise ValueError("所选句子中存在空的朗读文本")
        engine = str(manifest.get("engine") or job.request.get("tts_engine") or "indextts25")
        if engine == "indextts2":
            engine = "indextts25"
            manifest["engine"] = engine
        synthesis_chunks, chunk_positions, token_totals = _build_regeneration_plan(
            reading_texts,
            engine,
        )
        for index, positions in chunk_positions.items():
            if len(positions) > 1:
                store.log(
                    job,
                    f"第 {index} 句修正后为 {token_totals.get(index, 0)} token，"
                    f"已安全拆成 {len(positions)} 段生成，完成后自动合并回原句",
                )
        old_ranges = [
            (float(item.get("start") or 0), float(item.get("end") or 0))
            for item in segments
        ]

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        work_dir = segment_dir / ".regenerate"
        shutil.rmtree(work_dir, ignore_errors=True)
        output_dir = work_dir / "output"
        generated_dir = work_dir / "segments"
        work_dir.mkdir(parents=True, exist_ok=True)
        chunks_path = work_dir / "chunks.json"
        chunks_path.write_text(
            json.dumps(synthesis_chunks, ensure_ascii=False),
            encoding="utf-8",
        )
        script_path = work_dir / "selected.txt"
        script_path.write_text("\n".join(synthesis_chunks), encoding="utf-8")

        store.log(job, f"开始单句重配：第 {', '.join(map(str, indices))} 句（{engine}）")
        if engine == "cluster":
            from .cloud_client import cloud_client_for
            from .cloud_tts import synthesize_cloud_tts

            cloud_request = dict(job.request)
            cloud_request.pop("_cloud_job_id", None)
            cloud_request.pop("_cloud_job_status", None)
            cloud_request["cluster_voice_type"] = manifest.get("cluster_voice_type") or cloud_request.get("cluster_voice_type") or "preset"
            cloud_request["cluster_voice_id"] = manifest.get("cluster_voice_id") or cloud_request.get("cluster_voice_id") or ""
            cloud_request["tts_speed"] = manifest.get("tts_speed") or 1
            cloud_request["tts_volume"] = manifest.get("tts_volume") or 1
            cloud_request["tts_pitch"] = manifest.get("tts_pitch") or 0
            cloud_request["tts_emotion"] = manifest.get("tts_emotion") or ""
            cloud_request["tts_emotion_weight"] = manifest.get("tts_emotion_weight", 0.65)
            synthesize_cloud_tts(
                client=cloud_client_for(user_id),
                local_job_id=f"{job.id}-edit-{timestamp}",
                request=cloud_request,
                output_dir=output_dir,
                segment_archive_dir=generated_dir,
                temp_dir=work_dir / "temp",
                is_cancelled=lambda: False,
                on_progress=lambda percent, message: self._set_task(
                    job.id,
                    status="running",
                    progress=max(1, min(95, int(percent))),
                    message=f"单句重配：{message}",
                ),
                on_log=lambda line: store.log(job, f"[单句重配] {line}"),
                on_remote_job=lambda _job_id, _payload: None,
                chunks_override=synthesis_chunks,
            )
        else:
            command = [
                sys.executable, str(PROJECT_ROOT / "module1_agent_director.py"),
                "--text", str(script_path), "--job-id", f"{job.id}_tts_edit",
                "--tts-engine", engine, "--chunks-json", str(chunks_path),
                "--output-dir", str(output_dir), "--segment-archive-dir", str(generated_dir),
                "--tts-speed", str(manifest.get("tts_speed") or 1),
                "--tts-volume", str(manifest.get("tts_volume") or 1),
                "--tts-pitch", str(manifest.get("tts_pitch") or 0),
                "--tts-parallelism", str(manifest.get("tts_parallelism") or 1),
            ]
            if user_id:
                command.extend(["--user-id", str(user_id)])
            if engine == "qwen":
                command.extend(["--qwen-voice", str(manifest.get("qwen_voice") or "Elias")])
                instructions = str(manifest.get("qwen_instructions") or "").strip()
                if instructions:
                    command.extend(["--qwen-instructions", instructions])
            else:
                voice_override = str(settings_override.get("tts_voice_id") or "").strip()
                archived_voice = next((project_dir / "input").glob("TTS参考音色.*"), None)
                if voice_override:
                    command.extend(["--tts-voice-id", voice_override])
                elif archived_voice and archived_voice.is_file():
                    command.extend(["--tts-voice-path", str(archived_voice)])
                else:
                    command.extend(["--tts-voice-id", str(manifest.get("tts_voice_id") or "voice_05.wav")])
                emotion = str(manifest.get("tts_emotion") or "").strip()
                if emotion:
                    command.extend([
                        "--tts-emotion",
                        emotion,
                        "--tts-emotion-weight",
                        str(manifest.get("tts_emotion_weight", 0.65)),
                    ])

            process = subprocess.Popen(
                command, cwd=str(PROJECT_ROOT), env=os.environ.copy(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                cleaned = line.strip()
                if cleaned:
                    store.log(job, f"[单句重配] {cleaned}")
            return_code = process.wait()
            if return_code != 0:
                raise RuntimeError(f"TTS 子任务退出码 {return_code}")

        generated_manifest = json.loads((generated_dir / SEGMENT_MANIFEST).read_text(encoding="utf-8"))
        generated = generated_manifest.get("segments") or []
        if len(generated) != len(synthesis_chunks):
            raise RuntimeError("重配结果分段数与安全断句计划不一致")
        # Generation is complete and validated, but live audio has not been
        # replaced yet.  Capture the last good state at this exact boundary.
        self._snapshot_history(project_dir, manifest, indices, "重配选中句")
        for original_index in indices:
            generated_sources = [
                generated_dir / str(generated[position]["filename"])
                for position in chunk_positions[original_index]
            ]
            destination = segment_dir / str(by_index[original_index]["filename"])
            if len(generated_sources) == 1:
                shutil.copy2(generated_sources[0], destination)
            else:
                _concat_wavs(generated_sources, destination)
            # Keep the display subtitle in ``text``.  Only the synthesis layer
            # remembers pinyin or other pronunciation hints entered by users.
            by_index[original_index]["tts_text"] = reading_texts[original_index]

        voice_override = str(settings_override.get("tts_voice_id") or "").strip()
        if engine == "indextts25" and voice_override:
            from .indextts25_local import load_indextts25_config, resolve_voice_reference

            voice_source = resolve_voice_reference(load_indextts25_config(), voice_override, user_id=user_id)
            input_dir = project_dir / "input"
            input_dir.mkdir(parents=True, exist_ok=True)
            for old_voice in input_dir.glob("TTS参考音色.*"):
                old_voice.unlink(missing_ok=True)
            shutil.copy2(voice_source, input_dir / f"TTS参考音色{voice_source.suffix.lower()}")

        current = 0.0
        new_ranges: list[tuple[float, float]] = []
        for item in segments:
            audio = segment_dir / str(item["filename"])
            with wave.open(str(audio), "rb") as reader:
                duration = reader.getnframes() / reader.getframerate()
            item["start"] = round(current, 6)
            item["end"] = round(current + duration, 6)
            item["duration"] = round(duration, 6)
            new_ranges.append((current, current + duration))
            current += duration + max(0.0, min(30.0, float(item.get("pause_after") or 0)))

        def warp(value: float) -> float:
            value = max(0.0, float(value))
            for (old_start, old_end), (new_start, new_end) in zip(old_ranges, new_ranges):
                if value <= old_end + 1e-6:
                    old_duration = max(1e-6, old_end - old_start)
                    ratio = min(1.0, max(0.0, (value - old_start) / old_duration))
                    return new_start + ratio * (new_end - new_start)
            return current

        _concat_segment_wavs(segments, segment_dir, project_dir / "input" / "配音.wav")
        _rewrite_srt_times(project_dir / "other" / SUBTITLE_FILENAME, warp)
        timeline_path = project_dir / "other" / TIMELINE_FILENAME
        if timeline_path.is_file():
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
            if isinstance(timeline, list):
                for item in timeline:
                    if not isinstance(item, dict):
                        continue
                    start = warp(float(item.get("start") or 0))
                    end = max(start + 0.001, warp(float(item.get("end") or 0)))
                    item["start"] = round(start, 6)
                    item["end"] = round(end, 6)
                timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")

        manifest["segments"] = segments
        manifest["total_duration"] = round(current, 6)
        manifest["revision"] = int(manifest.get("revision") or 0) + 1
        manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._sync_module1_flat_outputs(project_dir, job.id)
        request_updates = {
            "tts_voice_id": manifest.get("tts_voice_id"),
            "tts_speed": manifest.get("tts_speed", 1),
            "tts_volume": manifest.get("tts_volume", 1),
            "tts_pitch": manifest.get("tts_pitch", 0),
            "tts_parallelism": manifest.get("tts_parallelism", 1),
            "tts_emotion": manifest.get("tts_emotion") or "",
            "tts_emotion_weight": manifest.get("tts_emotion_weight", 0.65),
            "cluster_voice_type": manifest.get("cluster_voice_type") or "preset",
            "cluster_voice_id": manifest.get("cluster_voice_id") or "",
            "qwen_tts_voice": manifest.get("qwen_voice") or "Elias",
            "qwen_tts_instructions": manifest.get("qwen_instructions") or "",
        }
        job.request.update(request_updates)
        store.update(job, request=job.request)
        if is_step_workflow_v2(job.request):
            persist_step_workflow_state(
                job,
                str(job.request.get("_step_mode_stage") or "audio_review"),
                message="配音精修已保存",
            )
            store.update(job, request=job.request)
        shutil.rmtree(work_dir, ignore_errors=True)


tts_editor = TtsEditor()
