from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


_ASPECT_DIMENSIONS = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (720, 720),
    "2:1": (1280, 640),
}
_SAFE_FONT_FAMILY = re.compile(r"^[A-Za-z0-9 _-]+$")


class VideoRenderError(Exception):
    """A rendering failure with a stable, machine-readable error code."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class RenderScene:
    image_path: str | Path
    audio_path: str | Path
    narration: str


@dataclass(frozen=True)
class RenderResult:
    output_path: Path
    duration_seconds: float
    scene_durations: tuple[float, ...]
    width: int
    height: int
    subtitles_burned: bool


def render_mp4(
    task_dir: str | Path,
    scenes: Sequence[RenderScene | Mapping[str, Any]],
    aspect_ratio: str,
    output_path: str | Path,
    *,
    burn_subtitles: bool = False,
    subtitle_font: str = "Noto Sans CJK SC",
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_seconds: float = 1800,
) -> RenderResult:
    """Render ordered still-image/audio scenes to an H.264/AAC MP4.

    Every input, temporary file, and the output must be contained by ``task_dir``.
    Paths in scene mappings may be absolute or relative to that directory.
    """

    root = _resolve_task_dir(task_dir)
    if aspect_ratio not in _ASPECT_DIMENSIONS:
        raise VideoRenderError(
            "INVALID_ASPECT_RATIO",
            "Unsupported aspect ratio",
            aspect_ratio=aspect_ratio,
            supported=sorted(_ASPECT_DIMENSIONS),
        )
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise VideoRenderError("INVALID_TIMEOUT", "timeout_seconds must be positive")
    if burn_subtitles and not _SAFE_FONT_FAMILY.fullmatch(subtitle_font):
        raise VideoRenderError(
            "INVALID_SUBTITLE_FONT",
            "Subtitle font contains unsupported characters",
            subtitle_font=subtitle_font,
        )

    normalized = _normalize_scenes(root, scenes)
    destination = _resolve_output(root, output_path)
    if destination in {scene.image_path for scene in normalized} | {
        scene.audio_path for scene in normalized
    }:
        raise VideoRenderError("INVALID_OUTPUT_PATH", "Output would overwrite a scene input")

    _require_executable(ffmpeg_bin, "FFMPEG_NOT_FOUND")
    _require_executable(ffprobe_bin, "FFPROBE_NOT_FOUND")
    durations = tuple(
        _probe_audio_duration(scene.audio_path, ffprobe_bin, timeout_seconds)
        for scene in normalized
    )
    width, height = _ASPECT_DIMENSIONS[aspect_ratio]
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix=".video-render-", dir=root))
    except OSError as exc:
        raise VideoRenderError(
            "OUTPUT_PREPARATION_FAILED", "Unable to prepare the render output directory"
        ) from exc
    encoded = work_dir / "output.mp4"
    subtitle_path = work_dir / "subtitles.srt"
    subtitles_burned = burn_subtitles and any(scene.narration.strip() for scene in normalized)
    try:
        if subtitles_burned:
            subtitle_path.write_text(_build_srt(normalized, durations), encoding="utf-8")
        argv = _ffmpeg_argv(
            root=root,
            scenes=normalized,
            durations=durations,
            output_path=encoded,
            width=width,
            height=height,
            subtitle_path=subtitle_path if subtitles_burned else None,
            subtitle_font=subtitle_font,
            ffmpeg_bin=ffmpeg_bin,
        )
        _run(argv, cwd=root, timeout_seconds=timeout_seconds, stage="encode")
        actual_duration = _validate_output(
            encoded,
            expected_duration=sum(durations),
            ffprobe_bin=ffprobe_bin,
            timeout_seconds=timeout_seconds,
        )
        try:
            os.replace(encoded, destination)
        except OSError as exc:
            raise VideoRenderError(
                "OUTPUT_FINALIZATION_FAILED",
                "Unable to move the rendered video to its output path",
                path=str(destination),
            ) from exc
    except OSError as exc:
        raise VideoRenderError(
            "RENDER_IO_FAILED", "A file operation failed during rendering"
        ) from exc
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return RenderResult(
        output_path=destination,
        duration_seconds=actual_duration,
        scene_durations=durations,
        width=width,
        height=height,
        subtitles_burned=subtitles_burned,
    )


@dataclass(frozen=True)
class _ResolvedScene:
    image_path: Path
    audio_path: Path
    narration: str


def _resolve_task_dir(task_dir: str | Path) -> Path:
    try:
        root = Path(task_dir).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise VideoRenderError("INVALID_TASK_DIR", "Task directory does not exist") from exc
    if not root.is_dir():
        raise VideoRenderError("INVALID_TASK_DIR", "Task path is not a directory", path=str(root))
    return root


def _owned_path(root: Path, value: str | Path, *, must_exist: bool, field: str) -> Path:
    candidate = Path(value).expanduser()
    candidate = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = candidate.resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise VideoRenderError(
            "INVALID_SCENE_PATH", f"{field} does not exist", field=field, path=str(value)
        ) from exc
    if not resolved.is_relative_to(root):
        raise VideoRenderError(
            "PATH_OUTSIDE_TASK_DIR",
            f"{field} must be inside task_dir",
            field=field,
            path=str(value),
        )
    return resolved


def _normalize_scenes(
    root: Path, scenes: Sequence[RenderScene | Mapping[str, Any]]
) -> tuple[_ResolvedScene, ...]:
    if isinstance(scenes, (str, bytes)) or not isinstance(scenes, Sequence) or not scenes:
        raise VideoRenderError("INVALID_SCENES", "At least one scene is required")
    result: list[_ResolvedScene] = []
    for index, raw in enumerate(scenes):
        if isinstance(raw, RenderScene):
            image_path, audio_path, narration = raw.image_path, raw.audio_path, raw.narration
        elif isinstance(raw, Mapping):
            missing = [key for key in ("image_path", "audio_path", "narration") if key not in raw]
            if missing:
                raise VideoRenderError(
                    "INCOMPLETE_SCENE", "Scene is missing required fields", index=index, missing=missing
                )
            image_path, audio_path, narration = (
                raw["image_path"], raw["audio_path"], raw["narration"]
            )
        else:
            raise VideoRenderError("INVALID_SCENE", "Scene must be a RenderScene or mapping", index=index)
        if not isinstance(image_path, (str, Path)) or not isinstance(audio_path, (str, Path)):
            raise VideoRenderError("INVALID_SCENE", "Scene paths must be strings or Paths", index=index)
        if not isinstance(narration, str):
            raise VideoRenderError("INVALID_SCENE", "Scene narration must be a string", index=index)
        image = _owned_path(root, image_path, must_exist=True, field=f"scenes[{index}].image_path")
        audio = _owned_path(root, audio_path, must_exist=True, field=f"scenes[{index}].audio_path")
        if not image.is_file() or not audio.is_file():
            raise VideoRenderError("INVALID_SCENE_PATH", "Scene inputs must be regular files", index=index)
        result.append(_ResolvedScene(image, audio, narration))
    return tuple(result)


def _resolve_output(root: Path, output_path: str | Path) -> Path:
    if not isinstance(output_path, (str, Path)):
        raise VideoRenderError("INVALID_OUTPUT_PATH", "Output path must be a string or Path")
    output = _owned_path(root, output_path, must_exist=False, field="output_path")
    if output.suffix.lower() != ".mp4":
        raise VideoRenderError("INVALID_OUTPUT_PATH", "Output path must end in .mp4", path=str(output))
    return output


def _require_executable(binary: str, code: str) -> None:
    if not binary or (os.sep not in binary and shutil.which(binary) is None):
        raise VideoRenderError(code, f"Required executable is unavailable: {binary}", executable=binary)
    if os.sep in binary and not (Path(binary).is_file() and os.access(binary, os.X_OK)):
        raise VideoRenderError(code, f"Required executable is unavailable: {binary}", executable=binary)


def _run(argv: list[str], *, cwd: Path, timeout_seconds: float, stage: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoRenderError(
            "RENDER_TIMEOUT", "Media command timed out", stage=stage, timeout_seconds=timeout_seconds
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise VideoRenderError(
            "MEDIA_COMMAND_FAILED",
            "Media command failed",
            stage=stage,
            returncode=exc.returncode,
            stderr=(exc.stderr or "")[-4000:],
        ) from exc
    except OSError as exc:
        raise VideoRenderError("MEDIA_COMMAND_FAILED", "Unable to start media command", stage=stage) from exc


def _probe(path: Path, ffprobe_bin: str, timeout_seconds: float) -> dict[str, Any]:
    completed = _run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_name,codec_type,duration",
            "-of",
            "json",
            str(path),
        ],
        cwd=path.parent,
        timeout_seconds=timeout_seconds,
        stage="probe",
    )
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise VideoRenderError("INVALID_PROBE_RESULT", "ffprobe returned invalid JSON", path=str(path)) from exc
    if not isinstance(payload, dict):
        raise VideoRenderError("INVALID_PROBE_RESULT", "ffprobe returned an invalid result", path=str(path))
    return payload


def _duration(payload: Mapping[str, Any], *, stream_type: str | None = None) -> float | None:
    candidates = [
        stream.get("duration")
        for stream in payload.get("streams", [])
        if stream_type is None or stream.get("codec_type") == stream_type
    ]
    candidates.append(payload.get("format", {}).get("duration"))
    for raw in candidates:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            return value
    return None


def _probe_audio_duration(path: Path, ffprobe_bin: str, timeout_seconds: float) -> float:
    payload = _probe(path, ffprobe_bin, timeout_seconds)
    if not any(stream.get("codec_type") == "audio" for stream in payload.get("streams", [])):
        raise VideoRenderError("INVALID_AUDIO", "Scene audio has no audio stream", path=str(path))
    duration = _duration(payload, stream_type="audio")
    if duration is None:
        raise VideoRenderError("INVALID_AUDIO_DURATION", "Unable to determine audio duration", path=str(path))
    return duration


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _safe_narration(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    text = re.sub(r"\n[ \t]*\n+", "\n", text)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("{", "\uff5b")
        .replace("}", "\uff5d")
        .replace("--&gt;", "-\u200b-&gt;")
        .strip()
    )


def _build_srt(scenes: Sequence[_ResolvedScene], durations: Sequence[float]) -> str:
    cues: list[str] = []
    cursor = 0.0
    cue_number = 1
    for scene, duration in zip(scenes, durations, strict=True):
        narration = _safe_narration(scene.narration)
        if narration:
            cues.append(
                f"{cue_number}\n{_srt_timestamp(cursor)} --> {_srt_timestamp(cursor + duration)}\n"
                f"{narration}\n"
            )
            cue_number += 1
        cursor += duration
    return "\n".join(cues)


def _ffmpeg_argv(
    *,
    root: Path,
    scenes: Sequence[_ResolvedScene],
    durations: Sequence[float],
    output_path: Path,
    width: int,
    height: int,
    subtitle_path: Path | None,
    subtitle_font: str,
    ffmpeg_bin: str,
) -> list[str]:
    argv = [ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y"]
    for scene, duration in zip(scenes, durations, strict=True):
        argv.extend(["-loop", "1", "-framerate", "25", "-t", f"{duration:.6f}", "-i", str(scene.image_path)])
        argv.extend(["-i", str(scene.audio_path)])

    filters: list[str] = []
    concat_inputs: list[str] = []
    for index, duration in enumerate(durations):
        video_input, audio_input = index * 2, index * 2 + 1
        filters.append(
            f"[{video_input}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25,format=yuv420p,"
            f"trim=duration={duration:.6f},setpts=PTS-STARTPTS[v{index}]"
        )
        filters.append(
            f"[{audio_input}:a]aresample=48000:async=1:first_pts=0,"
            f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[a{index}]"
        )
        concat_inputs.extend([f"[v{index}]", f"[a{index}]"])
    filters.append(f"{''.join(concat_inputs)}concat=n={len(scenes)}:v=1:a=1[vcat][aout]")
    video_label = "vcat"
    if subtitle_path is not None:
        relative_subtitle = subtitle_path.relative_to(root).as_posix()
        filters.append(
            f"[vcat]subtitles=filename='{relative_subtitle}':"
            f"force_style='FontName={subtitle_font},FontSize=24,Outline=2,Shadow=0'[vout]"
        )
        video_label = "vout"

    argv.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[{video_label}]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(output_path),
        ]
    )
    return argv


def _validate_output(
    path: Path, *, expected_duration: float, ffprobe_bin: str, timeout_seconds: float
) -> float:
    if not path.is_file() or path.stat().st_size == 0:
        raise VideoRenderError("INVALID_OUTPUT", "FFmpeg did not create a non-empty output")
    payload = _probe(path, ffprobe_bin, timeout_seconds)
    streams = payload.get("streams", [])
    video = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not video or not audio:
        raise VideoRenderError(
            "INVALID_OUTPUT_STREAMS", "Rendered output must contain video and audio streams"
        )
    if video[0].get("codec_name") != "h264" or audio[0].get("codec_name") != "aac":
        raise VideoRenderError(
            "INVALID_OUTPUT_CODECS",
            "Rendered output codecs are not H.264/AAC",
            video_codec=video[0].get("codec_name"),
            audio_codec=audio[0].get("codec_name"),
        )
    actual_duration = _duration(payload)
    if actual_duration is None:
        raise VideoRenderError("INVALID_OUTPUT_DURATION", "Rendered output has no valid duration")
    tolerance = max(0.5, expected_duration * 0.03)
    if abs(actual_duration - expected_duration) > tolerance:
        raise VideoRenderError(
            "INVALID_OUTPUT_DURATION",
            "Rendered duration differs from the scene audio duration",
            expected=expected_duration,
            actual=actual_duration,
            tolerance=tolerance,
        )
    return actual_duration
