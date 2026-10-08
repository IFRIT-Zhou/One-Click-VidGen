"""Upload-only, bounded still/video scene timeline assembly."""
from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile

from .deps import CurrentUser
from .editor_cache import SceneCache, scene_key
from .editor_render import AUDIO_FORMATS, VIDEO_FORMATS, RenderError, probe, validate_subtitle_style

router = APIRouter(prefix="/api/v1/editor", tags=["editor"])
IMAGE_FORMATS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}


def validate_manifest(raw: str, assets: list[UploadFile]) -> dict:
    if len(raw.encode("utf-8")) > 65536 or not 1 <= len(assets) <= 48:
        raise HTTPException(422, "Manifest is limited to 64 KiB and 1 to 48 uploaded assets")
    try:
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or set(manifest) - {"scenes", "orientation", "music"}:
            raise ValueError
        scenes = manifest["scenes"]
        if not isinstance(scenes, list) or not 1 <= len(scenes) <= 16:
            raise ValueError
        orientation = manifest.get("orientation", "landscape")
        if orientation not in {"landscape", "portrait"}:
            raise ValueError
        kinds, total = {}, 0

        def number(value, minimum, maximum):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError
            return value

        def index(value, kind):
            if type(value) is not int or not 0 <= value < len(assets) or (value in kinds and kinds[value] != kind):
                raise ValueError
            kinds[value] = kind

        for scene in scenes:
            if not isinstance(scene, dict) or set(scene) - {"image_index", "video_index", "audio_indices", "audio_pauses", "duration", "pause"}:
                raise ValueError
            if ("image_index" in scene) == ("video_index" in scene):
                raise ValueError
            index(scene.get("image_index", scene.get("video_index")), "image" if "image_index" in scene else "video")
            duration = number(scene["duration"], 0.1, 600)
            pause = number(scene.get("pause", 0), 0, 10)
            audio = scene.get("audio_indices", [])
            pauses = scene.get("audio_pauses", [0] * len(audio))
            if not isinstance(audio, list) or len(audio) > 32 or not isinstance(pauses, list) or len(pauses) != len(audio):
                raise ValueError
            if "video_index" in scene and audio:
                raise ValueError
            for asset_index, audio_pause in zip(audio, pauses):
                index(asset_index, "audio")
                number(audio_pause, 0, 10)
            scene.update(duration=duration, pause=pause, audio_indices=audio, audio_pauses=pauses)
            total += duration + pause
        music = manifest.get("music", [])
        if not isinstance(music, list) or len(music) > 3:
            raise ValueError
        for track in music:
            if not isinstance(track, dict) or set(track) - {"audio_index", "volume", "delay", "loop", "fade"}:
                raise ValueError
            index(track["audio_index"], "audio")
            track["volume"] = number(track.get("volume", 0.35), 0, 2)
            track["delay"] = number(track.get("delay", 0), 0, 600)
            track["fade"] = number(track.get("fade", 1), 0, 30)
            track["loop"] = track.get("loop", True)
            if type(track["loop"]) is not bool:
                raise ValueError
        if total > 600 or set(kinds) != set(range(len(assets))):
            raise ValueError
        formats = {}
        for asset_index, kind in kinds.items():
            extension = Path(assets[asset_index].filename or "").suffix.lower()
            formats[asset_index] = {"image": IMAGE_FORMATS, "video": VIDEO_FORMATS, "audio": AUDIO_FORMATS}[kind].get(extension)
            if formats[asset_index] is None:
                raise ValueError
        return {"scenes": scenes, "music": music, "orientation": orientation, "kinds": kinds, "formats": formats, "duration": total}
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(422, "Invalid timeline: use 1–16 scenes, typed asset indices, duration/pause and at most 600 total seconds") from exc


def _image_info(path: Path, kind: str, *, lease_fd: int | None = None, timeout: float = 15) -> dict:
    with path.open("rb") as stream:
        signature = stream.read(12)
    if not ((kind == "png" and signature.startswith(b"\x89PNG\r\n\x1a\n")) or
            (kind == "jpeg" and signature.startswith(b"\xff\xd8\xff")) or
            (kind == "webp" and signature[:4] == b"RIFF" and signature[8:12] == b"WEBP")):
        raise RenderError("Image content does not match PNG, JPEG or WebP")
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-max_alloc", "67108864", "-protocol_whitelist", "file,pipe",
                                 "-f", "image2", "-pattern_type", "none", "-show_entries", "stream=width,height,codec_name",
                                 "-of", "json", str(path)], capture_output=True, check=True, timeout=timeout,
                                pass_fds=() if lease_fd is None else (lease_fd,))
        info = json.loads(result.stdout)["streams"][0]
        width, height = info["width"], info["height"]
        if min(width, height) <= 0 or max(width, height) > 8192 or width * height > 16777216:
            raise RenderError("Image exceeds 8192 pixels per side or 16777216 pixels total")
        return info
    except (subprocess.SubprocessError, OSError, KeyError, IndexError, ValueError) as exc:
        if isinstance(exc, RenderError):
            raise
        raise RenderError("Could not decode the uploaded image") from exc


def build_timeline(directory: Path, manifest: dict, *, lease_fd: int | None = None, cache_owner: str | None = None) -> int:
    deadline = time.monotonic() + 900
    width, height = (720, 1280) if manifest["orientation"] == "portrait" else (1280, 720)
    dimensions = f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    outputs, total_size = [], 0
    asset_info = {}
    asset_hashes = {}
    cache = SceneCache(directory.parent / "editor-scene-cache")
    cached_scenes = 0

    def remaining_time():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RenderError("Timeline assembly exceeded 15 minutes")
        return remaining

    def asset_probe(index):
        if index not in asset_info:
            asset_info[index] = probe(directory / f"asset_{index}", manifest["formats"][index],
                                      lease_fd=lease_fd, timeout=min(20, remaining_time()))
        return asset_info[index]

    def run(command):
        remaining = remaining_time()
        try:
            subprocess.run(command, cwd=directory, capture_output=True, check=True, timeout=remaining,
                           pass_fds=() if lease_fd is None else (lease_fd,))
        except subprocess.TimeoutExpired as exc:
            raise RenderError("Timeline assembly exceeded 15 minutes") from exc
        except (subprocess.SubprocessError, OSError) as exc:
            raise RenderError("FFmpeg could not assemble the timeline") from exc

    for scene_number, scene in enumerate(manifest["scenes"]):
        remaining_time()
        duration, pause = scene["duration"], scene["pause"]
        length = duration + pause
        output = f"scene_{scene_number}.mp4"

        def validate_scene(path):
            actual = probe(path, "mov", lease_fd=lease_fd, timeout=min(20, remaining_time()))["duration"]
            if abs(actual - length) > 0.25:
                raise RenderError("Timeline scene exceeded the intermediate size limit")

        cache_key = None
        if cache_owner:
            try:
                cache_key = scene_key(directory, scene, manifest["orientation"], manifest["formats"], asset_hashes)
            except (OSError, ValueError):
                pass
        if cache_key and cache.restore(cache_owner, cache_key, directory / output, validate_scene):
            cached_scenes += 1
            total_size += (directory / output).stat().st_size
            if total_size > 400 * 1024 ** 2:
                raise RenderError("Timeline intermediates exceed 400 MiB")
            outputs.append(output)
            continue
        command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-max_alloc", "67108864", "-threads", "2"]
        filters = []
        if "image_index" in scene:
            image_index = scene["image_index"]
            if image_index not in asset_info:
                asset_info[image_index] = _image_info(directory / f"asset_{image_index}", manifest["formats"][image_index],
                                                       lease_fd=lease_fd, timeout=min(15, remaining_time()))
            command += ["-protocol_whitelist", "file,pipe", "-f", "image2", "-pattern_type", "none", "-loop", "1", "-framerate", "30", "-i", f"asset_{image_index}"]
            filters.append(f"[0:v]trim=duration={length},setpts=PTS-STARTPTS,{dimensions}[v]")
            audio_labels, audio_total = [], 0
            for input_number, (asset_index, gap) in enumerate(zip(scene["audio_indices"], scene["audio_pauses"]), 1):
                info = asset_probe(asset_index)
                if not any(stream["codec_type"] == "audio" for stream in info["streams"]):
                    raise RenderError("Narration must contain an audio stream")
                audio_total += info["duration"] + gap
                command += ["-protocol_whitelist", "file,pipe", "-f", manifest["formats"][asset_index], "-i", f"asset_{asset_index}"]
                filters.append(f"[{input_number}:a:0]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,apad=pad_dur={gap}[a{input_number}]")
                audio_labels.append(f"[a{input_number}]")
            if audio_total > duration + 0.1:
                raise RenderError("Scene duration is shorter than its narration and sentence pauses")
            if audio_labels:
                filters.append("".join(audio_labels) + f"concat=n={len(audio_labels)}:v=0:a=1,apad,atrim=duration={length}[a]")
            else:
                filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={length}[a]")
        else:
            asset_index = scene["video_index"]
            info = asset_probe(asset_index)
            visual = next((stream for stream in info["streams"] if stream["codec_type"] == "video"), None)
            if not visual or min(visual["width"], visual["height"]) <= 0 or max(visual["width"], visual["height"]) > 3840 or visual["width"] * visual["height"] > 8294400:
                raise RenderError("Timeline video exceeds the supported picture size")
            command += ["-protocol_whitelist", "file,pipe", "-f", manifest["formats"][asset_index], "-i", f"asset_{asset_index}"]
            filters.append(f"[0:v:0]trim=duration={duration},setpts=PTS-STARTPTS,{dimensions},tpad=stop_mode=clone:stop_duration={length},trim=duration={length}[v]")
            if any(stream["codec_type"] == "audio" for stream in info["streams"]):
                filters.append(f"[0:a:0]atrim=duration={min(duration, info['duration'])},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={length}[a]")
            else:
                filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={length}[a]")
        command += ["-filter_complex_threads", "1", "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
                    "-t", str(length), "-r", "30", "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-fs", str(512 * 1024 ** 2), output]
        run(command)
        validate_scene(directory / output)
        total_size += (directory / output).stat().st_size
        if total_size > 400 * 1024 ** 2:
            raise RenderError("Timeline intermediates exceed 400 MiB")
        if cache_key:
            cache.store(cache_owner, cache_key, directory / output)
        outputs.append(output)
    (directory / "timeline.concat").write_text("".join(f"file '{output}'\n" for output in outputs), encoding="utf-8")
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-f", "concat", "-safe", "1", "-i", "timeline.concat", "-c", "copy", "-f", "mp4", "video"])
    for output in outputs:
        (directory / output).unlink()
    if manifest["music"]:
        command = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
        filters, labels = [], []
        total_duration = manifest["duration"]
        for input_number, track in enumerate(manifest["music"]):
            index = track["audio_index"]
            info = asset_probe(index)
            if not any(stream["codec_type"] == "audio" for stream in info["streams"]):
                raise RenderError("Background music must contain audio")
            if track["loop"]:
                command += ["-stream_loop", "-1"]
            command += ["-protocol_whitelist", "file,pipe", "-f", manifest["formats"][index], "-i", f"asset_{index}"]
            delay = min(track["delay"], total_duration)
            audible = total_duration - delay if track["loop"] else min(total_duration-delay, info["duration"])
            fade = min(track["fade"], max(0, audible/2))
            chain = f"[{input_number}:a:0]asetpts=PTS-STARTPTS,volume={track['volume']}"
            if fade:
                chain += f",afade=t=in:st=0:d={fade},afade=t=out:st={max(0,audible-fade)}:d={fade}"
            chain += f",adelay={round(delay*1000)}:all=1,apad,atrim=duration={total_duration}[m{input_number}]"
            filters.append(chain)
            labels.append(f"[m{input_number}]")
        filters.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,alimiter=limit=0.95[mixed]")
        command += ["-filter_complex_threads", "1", "-filter_complex", ";".join(filters), "-map", "[mixed]", "-t", str(total_duration),
                    "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", "-f", "wav", "bgm"]
        run(command)
    return cached_scenes


@router.post("/timeline", status_code=202)
def create_timeline(
    user: CurrentUser, manifest: str = Form(...), assets: list[UploadFile] = File(...),
    bgm: UploadFile | None = File(None), subtitles: UploadFile | None = File(None),
    source_volume: float = Form(1, ge=0, le=2), bgm_volume: float = Form(0.35, ge=0, le=2),
    bgm_delay: float = Form(0, ge=0, le=600), bgm_loop: bool = Form(True), bgm_fade: float = Form(1, ge=0, le=30),
    burn_subtitles: bool = Form(True),
    subtitle_style: str = Form("{}"),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", min_length=1, max_length=128),
):
    from . import editor_api as jobs
    timeline = validate_manifest(manifest, assets)
    try:
        style = validate_subtitle_style(subtitle_style)
    except RenderError as exc:
        raise HTTPException(422, str(exc)) from exc
    if timeline["music"] and bgm:
        raise HTTPException(422, "Use manifest music tracks or a single bgm upload, not both")
    if not all(math.isfinite(value) for value in (source_volume, bgm_volume, bgm_delay, bgm_fade)):
        raise HTTPException(422, "Invalid audio settings")
    bgm_format = AUDIO_FORMATS.get(Path(bgm.filename or "").suffix.lower()) if bgm else None
    subtitle_extension = Path(subtitles.filename or "").suffix.lower() if subtitles else None
    if (bgm and not bgm_format) or (subtitles and subtitle_extension not in {".srt", ".ass", ".vtt"}):
        raise HTTPException(415, "Unsupported music or subtitle file type")
    directory, state, lease = jobs._reserve(user, idempotency_key)
    if directory is None:
        return jobs._public(state)
    options = dict(video_format="mov", bgm_format=bgm_format, subtitle_extension=subtitle_extension,
                   trim_start=0, trim_end=timeline["duration"], source_volume=source_volume, bgm_volume=bgm_volume,
                   bgm_delay=bgm_delay, bgm_loop=bgm_loop, bgm_fade=bgm_fade, burn_subtitles=burn_subtitles,
                   subtitle_style=style, timeline=timeline)
    if timeline["music"]:
        options.update(bgm_format="wav", bgm_volume=1, bgm_delay=0, bgm_loop=False, bgm_fade=0)
    try:
        for index, asset in enumerate(assets):
            maximum = {"image": 20, "audio": 30, "video": 200}[timeline["kinds"][index]] * 1024 ** 2
            jobs._save_upload(asset, directory / f"asset_{index}", maximum)
        if bgm:
            jobs._save_upload(bgm, directory / "bgm", 30 * 1024 ** 2)
        if subtitles:
            jobs._save_upload(subtitles, directory / "subtitles", 1024 ** 2)
        jobs.threading.Thread(target=jobs._worker, args=(directory, options, lease), daemon=True).start()
    except Exception:
        jobs._cleanup_inputs(directory)
        state.update(status="failed", error="Timeline upload did not complete")
        jobs._write(directory, state)
        lease.close()
        raise
    return jobs._public(state)
