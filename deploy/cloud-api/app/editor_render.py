"""Bounded, upload-only FFmpeg rendering. No client paths or remote inputs."""
from __future__ import annotations

import html
import json
import math
import re
import subprocess
from pathlib import Path

VIDEO_FORMATS = {".mp4": "mov", ".mov": "mov", ".m4v": "mov", ".webm": "matroska", ".mkv": "matroska"}
AUDIO_FORMATS = {".mp3": "mp3", ".wav": "wav", ".m4a": "mov", ".aac": "aac", ".ogg": "ogg", ".flac": "flac"}
MAX_DURATION = 600
RENDER_TIMEOUT = 900


class RenderError(ValueError):
    pass


def validate_subtitle_style(raw: str | dict | None) -> dict:
    defaults = {"fontSize": 28, "textColor": "#FFFFFF", "bottomMargin": 32, "outline": 2}
    try:
        if isinstance(raw, str):
            if len(raw) > 512:
                raise ValueError
            raw = json.loads(raw)
        values = {} if raw is None else raw
        if not isinstance(values, dict) or set(values) - set(defaults):
            raise ValueError
        result = {**defaults, **values}
        for key, minimum, maximum in (("fontSize", 16, 64), ("bottomMargin", 0, 120), ("outline", 0, 4)):
            if type(result[key]) is not int or not minimum <= result[key] <= maximum:
                raise ValueError
        if not isinstance(result["textColor"], str) or re.fullmatch(r"#[0-9a-fA-F]{6}", result["textColor"]) is None:
            raise ValueError
        result["textColor"] = result["textColor"].upper()
        return result
    except (TypeError, ValueError) as exc:
        raise RenderError("Invalid subtitle style: fontSize 16–64, textColor #RRGGBB, bottomMargin 0–120, outline 0–4; only these fields are allowed") from exc


def subtitle_force_style(raw: str | dict | None) -> str:
    style = validate_subtitle_style(raw)
    color = style["textColor"][1:]
    ass_color = f"&H00{color[4:6]}{color[2:4]}{color[0:2]}"
    return f"FontName=Noto Sans CJK SC,FontSize={style['fontSize']},PrimaryColour={ass_color},OutlineColour=&H00000000,BorderStyle=1,Outline={style['outline']},Shadow=0,Alignment=2,MarginV={style['bottomMargin']}"


def probe(path: Path, demuxer: str, *, lease_fd: int | None = None, timeout: float = 20) -> dict:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-f", demuxer, "-show_entries", "format=duration:stream=codec_type,width,height,duration",
            "-of", "json", str(path),
        ], capture_output=True, timeout=timeout, check=True,
            pass_fds=() if lease_fd is None else (lease_fd,))
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or not 0 < duration <= 3600:
            raise RenderError("Media duration must be between 0 and 3600 seconds")
        data["duration"] = duration
        return data
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        if isinstance(exc, RenderError):
            raise
        raise RenderError("The uploaded media could not be read") from exc


def _seconds(value: str) -> float:
    parts = value.strip().replace(",", ".").split(":")
    if len(parts) not in (2, 3):
        raise RenderError("Invalid subtitle timestamp")
    values = [float(part) for part in parts]
    result = sum(value * 60 ** index for index, value in enumerate(reversed(values)))
    if not math.isfinite(result) or result < 0:
        raise RenderError("Invalid subtitle timestamp")
    return result


def _stamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def normalize_subtitles(source: Path, extension: str, target: Path, start: float, duration: float) -> bool:
    try:
        content = source.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        cues = []
        if extension == ".ass":
            for line in content.splitlines():
                if line.lower().startswith("dialogue:"):
                    fields = line.split(":", 1)[1].split(",", 9)
                    if len(fields) != 10:
                        raise RenderError("Invalid ASS dialogue")
                    cues.append((_seconds(fields[1]), _seconds(fields[2]), fields[9].replace("\\N", "\n").replace("\\n", "\n")))
        else:
            for block in re.split(r"\n\s*\n", content):
                lines = block.strip().splitlines()
                for index, line in enumerate(lines):
                    if "-->" in line:
                        left, right = line.split("-->", 1)
                        cues.append((_seconds(left), _seconds(right.strip().split()[0]), "\n".join(lines[index + 1:])))
                        break
        if not cues or len(cues) > 10000:
            raise RenderError("Subtitles must contain 1 to 10000 timed cues")
        output = []
        for begin, end, text in cues:
            if end <= begin:
                raise RenderError("Subtitle end must be after start")
            begin, end = max(0, begin - start), min(duration, end - start)
            # Keep text only: uploaded ASS styles and drawing/filter instructions
            # never reach libass. All output filenames are server-generated.
            text = html.unescape(re.sub(r"<[^>]*>|\{[^}]*\}", "", text)).replace("\\", "")
            if end > begin and text.strip():
                output.append(f"{len(output) + 1}\n{_stamp(begin)} --> {_stamp(end)}\n{text.strip()}\n")
        if output:
            target.write_text("\n".join(output), encoding="utf-8")
        return bool(output)
    except (UnicodeError, ValueError, IndexError) as exc:
        if isinstance(exc, RenderError):
            raise
        raise RenderError("Subtitles must be valid UTF-8 SRT, ASS or VTT") from exc


def render(directory: Path, options: dict) -> float:
    force_style = subtitle_force_style(options.get("subtitle_style"))
    lease_fd = options.get("_lease_fd")
    video = probe(directory / "video", options["video_format"], lease_fd=lease_fd)
    streams = video.get("streams", [])
    visual = next((stream for stream in streams if stream["codec_type"] == "video"), None)
    width, height = (visual.get("width", 0), visual.get("height", 0)) if visual else (0, 0)
    if not visual or min(width, height) <= 0 or max(width, height) > 3840 or width * height > 3840 * 2160:
        raise RenderError("Video must have a longest side at most 3840 pixels and at most 8294400 pixels")
    start = options["trim_start"]
    end = options["trim_end"] if options["trim_end"] is not None else video["duration"]
    if end > video["duration"] + 0.1 or not 0 < end - start <= MAX_DURATION:
        raise RenderError("Choose a valid clip between 0 and 600 seconds within the source")
    duration = min(end, video["duration"]) - start
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "2",
               "-protocol_whitelist", "file,pipe", "-f", options["video_format"], "-ss", str(start), "-i", "video"]
    has_audio = any(stream["codec_type"] == "audio" for stream in streams)
    has_bgm = options.get("bgm_format") is not None
    if has_bgm:
        bgm = probe(directory / "bgm", options["bgm_format"], lease_fd=lease_fd)
        if not any(stream["codec_type"] == "audio" for stream in bgm.get("streams", [])):
            raise RenderError("Background music must contain audio")
        if options["bgm_loop"]:
            command += ["-stream_loop", "-1"]
        command += ["-protocol_whitelist", "file,pipe", "-f", options["bgm_format"], "-i", "bgm"]
    captions = False
    if options.get("subtitle_extension"):
        captions = normalize_subtitles(directory / "subtitles", options["subtitle_extension"], directory / "captions.srt", start, duration)
    filters = ["[0:v:0]setpts=PTS-STARTPTS,scale=w='min(1920,iw)':h='min(1920,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1" +
               (f",subtitles=filename=captions.srt:force_style='{force_style}'" if captions and options["burn_subtitles"] else "") + "[v]"]
    audio_labels = []
    if has_audio:
        filters.append(f"[0:a:0]asetpts=PTS-STARTPTS,volume={options['source_volume']},apad,atrim=duration={duration}[original]")
        audio_labels.append("[original]")
    if has_bgm:
        delay = min(options["bgm_delay"], duration)
        music_duration = duration - delay if options["bgm_loop"] else min(duration - delay, bgm["duration"])
        fade = min(options["bgm_fade"], max(0, music_duration / 2))
        chain = f"[1:a:0]asetpts=PTS-STARTPTS,volume={options['bgm_volume']}"
        if fade:
            chain += f",afade=t=in:st=0:d={fade},afade=t=out:st={max(0, music_duration-fade)}:d={fade}"
        chain += f",adelay={round(delay*1000)}:all=1,apad,atrim=duration={duration}[music]"
        filters.append(chain)
        audio_labels.append("[music]")
    if audio_labels:
        filters.append("".join(audio_labels) + f"amix=inputs={len(audio_labels)}:normalize=0:duration=longest,alimiter=limit=0.95[a]")
    if captions and not options["burn_subtitles"]:
        command += ["-protocol_whitelist", "file,pipe", "-f", "srt", "-i", "captions.srt"]
    command += ["-filter_complex_threads", "1", "-filter_complex", ";".join(filters), "-map", "[v]"]
    if audio_labels:
        command += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    if captions and not options["burn_subtitles"]:
        command += ["-map", f"{2 if has_bgm else 1}:0", "-c:s", "mov_text"]
    command += ["-t", str(duration), "-r", "30", "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-fs", str(512 * 1024 * 1024), "output.part.mp4"]
    try:
        subprocess.run(command, cwd=directory, capture_output=True, timeout=RENDER_TIMEOUT, check=True,
                       pass_fds=() if lease_fd is None else (lease_fd,))
    except subprocess.TimeoutExpired as exc:
        raise RenderError("Export exceeded the 15 minute processing limit") from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise RenderError("FFmpeg could not export this media") from exc
    actual = probe(directory / "output.part.mp4", "mov", lease_fd=lease_fd)["duration"]
    if abs(actual - duration) > 0.25:
        raise RenderError("Export could not complete within the output size limit")
    (directory / "output.part.mp4").replace(directory / "output.mp4")
    return duration
