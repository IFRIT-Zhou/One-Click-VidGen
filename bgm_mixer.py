from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
FFMPEG = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"


def _binary(path: Path, fallback: str) -> str:
    if path.is_file():
        return str(path)
    found = shutil.which(fallback)
    if found:
        return found
    raise FileNotFoundError(f"未找到 {fallback}，请确认整合包 tools/ffmpeg/bin 完整")


def _duration(path: Path) -> float:
    process = subprocess.run(
        [
            _binary(FFPROBE, "ffprobe"), "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.returncode != 0:
        raise RuntimeError(f"无法读取媒体时长：{path.name}｜{process.stderr[-400:]}")
    try:
        value = float(process.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"媒体时长无效：{path.name}") from exc
    if value <= 0:
        raise RuntimeError(f"媒体时长为 0：{path.name}")
    return value


def normalize_tracks(raw_tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for item in raw_tracks:
        path = Path(str(item.get("path") or "")).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"找不到 BGM：{path}")
        volume_db = max(-60.0, min(6.0, float(item.get("volume_db", -10))))
        tracks.append({"path": path, "volume_db": volume_db})
    return tracks


def mix_bgm_into_videos(
    videos: list[Path],
    raw_tracks: list[dict[str, Any]],
    *,
    fade_enabled: bool = False,
    fade_duration: float = 1.0,
) -> None:
    tracks = normalize_tracks(raw_tracks)
    videos = [Path(video).resolve() for video in videos if Path(video).is_file()]
    if not tracks or not videos:
        return
    fade_duration = max(0.1, min(30.0, float(fade_duration)))
    ffmpeg = _binary(FFMPEG, "ffmpeg")
    with tempfile.TemporaryDirectory(prefix="ocv_bgm_") as temp_name:
        temp_dir = Path(temp_name)
        prepared: list[tuple[Path, float]] = []
        for index, track in enumerate(tracks, 1):
            source = track["path"]
            duration = _duration(source)
            output = temp_dir / f"track_{index:04d}.wav"
            filters = [f"volume={track['volume_db']:.2f}dB"]
            if fade_enabled:
                actual_fade = min(fade_duration, duration)
                filters.append(f"afade=t=out:st={max(0.0, duration - actual_fade):.6f}:d={actual_fade:.6f}")
            command = [
                ffmpeg, "-y", "-i", str(source), "-vn", "-af", ",".join(filters),
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output),
            ]
            process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if process.returncode != 0 or not output.is_file():
                raise RuntimeError(f"BGM 预处理失败：{source.name}｜{process.stderr[-800:]}")
            prepared.append((output, duration))

        for video_index, video in enumerate(videos, 1):
            video_duration = _duration(video)
            playlist = temp_dir / f"playlist_{video_index:02d}.txt"
            accumulated = 0.0
            lines: list[str] = []
            while accumulated < video_duration:
                for track_path, track_duration in prepared:
                    lines.append(f"file '{track_path.as_posix()}'")
                    accumulated += track_duration
                    if accumulated >= video_duration:
                        break
            playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
            mixed = temp_dir / f"mixed_{video_index:02d}.mp4"
            bgm_filters = [f"atrim=0:{video_duration:.6f}", "asetpts=PTS-STARTPTS"]
            if fade_enabled:
                final_fade = min(fade_duration, video_duration)
                bgm_filters.append(
                    f"afade=t=out:st={max(0.0, video_duration - final_fade):.6f}:d={final_fade:.6f}"
                )
            filter_complex = (
                f"[1:a]{','.join(bgm_filters)}[bgm];"
                "[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
            )
            command = [
                ffmpeg, "-y", "-i", str(video), "-f", "concat", "-safe", "0", "-i", str(playlist),
                "-filter_complex", filter_complex, "-map", "0:v:0", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{video_duration:.6f}",
                "-movflags", "+faststart", str(mixed),
            ]
            print(f"[BGM] 正在混合 {video.name}（{video_index}/{len(videos)}）", flush=True)
            process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if process.returncode != 0 or not mixed.is_file() or mixed.stat().st_size < 1024:
                raise RuntimeError(f"BGM 混合失败：{video.name}｜{process.stderr[-1200:]}")
            adjacent_temp = video.with_name(f".{video.stem}.bgm-mixing{video.suffix}")
            adjacent_temp.unlink(missing_ok=True)
            shutil.copy2(mixed, adjacent_temp)
            os.replace(adjacent_temp, video)
            print(f"[BGM] 已完成 {video.name}", flush=True)


def tracks_from_env() -> list[dict[str, Any]]:
    raw = str(os.getenv("BGM_TRACKS_JSON") or "").strip()
    if not raw:
        return []
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("BGM_TRACKS_JSON 必须是数组")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="One-Click VidGen BGM mixer")
    parser.add_argument("--video", action="append", required=True)
    parser.add_argument("--tracks-json", required=True)
    parser.add_argument("--fade", action="store_true")
    parser.add_argument("--fade-duration", type=float, default=1.0)
    args = parser.parse_args()
    tracks = json.loads(args.tracks_json)
    mix_bgm_into_videos(
        [Path(value) for value in args.video],
        tracks,
        fade_enabled=args.fade,
        fade_duration=args.fade_duration,
    )


if __name__ == "__main__":
    main()
