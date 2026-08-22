"""Sentence-level TTS regeneration for completed, output-scoped projects."""

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

from .pipeline import JOBS_DIR, PROJECT_ROOT, store
from .visual_editor import VisualEditor


SEGMENT_DIRNAME = "tts_segments"
SEGMENT_MANIFEST = "manifest.json"
SUBTITLE_FILENAME = "最终字幕.srt"
TIMELINE_FILENAME = "画面时间线.json"


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
        return VisualEditor.output_dir(job_id, user_id)

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
            "available": bool(items),
            "message": "可选择一条或多条重新配音；完成后请重新渲染视频。" if items else "逐句音频文件不完整",
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
        if engine == "indextts25":
            from .tts_segmentation import (
                INDEXTTS25_SEGMENT_MAX_TOKENS,
                build_indextts25_token_counter,
            )

            token_count = build_indextts25_token_counter()
            for index, text in reading_texts.items():
                total = token_count(text)
                if total > INDEXTTS25_SEGMENT_MAX_TOKENS:
                    raise ValueError(
                        f"第 {index} 句发音修正后为 {total} token，超过 "
                        f"IndexTTS-2.5 单句 {INDEXTTS25_SEGMENT_MAX_TOKENS} token 安全上限"
                    )
        old_ranges = [
            (float(item.get("start") or 0), float(item.get("end") or 0))
            for item in segments
        ]

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        history = segment_dir / "history" / timestamp
        history.mkdir(parents=True, exist_ok=True)
        for source in (
            manifest_path,
            project_dir / "input" / "配音.wav",
            project_dir / "other" / SUBTITLE_FILENAME,
            project_dir / "other" / TIMELINE_FILENAME,
        ):
            if source.is_file():
                shutil.copy2(source, history / source.name)
        for source in (project_dir / "input").glob("TTS参考音色.*"):
            if source.is_file():
                shutil.copy2(source, history / source.name)
        for index in indices:
            source = segment_dir / str(by_index[index]["filename"])
            if source.is_file():
                shutil.copy2(source, history / source.name)

        work_dir = segment_dir / ".regenerate"
        shutil.rmtree(work_dir, ignore_errors=True)
        output_dir = work_dir / "output"
        generated_dir = work_dir / "segments"
        work_dir.mkdir(parents=True, exist_ok=True)
        chunks_path = work_dir / "chunks.json"
        chunks_path.write_text(
            json.dumps([reading_texts[index] for index in indices], ensure_ascii=False),
            encoding="utf-8",
        )
        script_path = work_dir / "selected.txt"
        script_path.write_text("\n".join(reading_texts[index] for index in indices), encoding="utf-8")

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
                chunks_override=[reading_texts[index] for index in indices],
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
        if len(generated) != len(indices):
            raise RuntimeError("重配结果句数与所选句数不一致")
        for original_index, new_item in zip(indices, generated):
            source = generated_dir / str(new_item["filename"])
            shutil.copy2(source, segment_dir / str(by_index[original_index]["filename"]))
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
            current += duration

        def warp(value: float) -> float:
            value = max(0.0, float(value))
            for (old_start, old_end), (new_start, new_end) in zip(old_ranges, new_ranges):
                if value <= old_end + 1e-6:
                    old_duration = max(1e-6, old_end - old_start)
                    ratio = min(1.0, max(0.0, (value - old_start) / old_duration))
                    return new_start + ratio * (new_end - new_start)
            return current

        _concat_wavs(
            [segment_dir / str(item["filename"]) for item in segments],
            project_dir / "input" / "配音.wav",
        )
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
        shutil.rmtree(work_dir, ignore_errors=True)


tts_editor = TtsEditor()
