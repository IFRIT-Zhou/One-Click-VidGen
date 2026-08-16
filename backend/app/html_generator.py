import html
import json
import os
import re
import wave
from pathlib import Path
from typing import Any

import requests

from .gemini_client import DEFAULT_GEMINI_MODEL, DEFAULT_OPENAI_COMPATIBLE_BASE_URL


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
VISUAL_DIR = WORKSPACE_DIR / "3_visual_template"
AUDIO_SRT_DIR = WORKSPACE_DIR / "2_audio_srt"


class HtmlGenerationError(RuntimeError):
    pass


def _first_present(item: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return default


def normalize_scene(item: dict[str, Any], index: int) -> dict[str, Any]:
    scene_id = str(_first_present(item, ("slide_id", "scene_id", "id"), f"scene_{index:03d}"))
    if scene_id.startswith("segment_"):
        scene_id = f"scene_{index:03d}"
    start = float(_first_present(item, ("start", "start_time"), 0))
    end = float(_first_present(item, ("end", "end_time"), start + 1))
    text = str(_first_present(item, ("text_content", "text", "content"), "")).strip()
    visual_summary = str(item.get("visual_summary") or text).strip()
    return {
        "scene_id": scene_id,
        "start_time": round(start, 3),
        "end_time": round(max(end, start + 0.2), 3),
        "text_content": text,
        "visual_summary": visual_summary,
    }


def load_scenes() -> list[dict[str, Any]]:
    timeline_path = VISUAL_DIR / "fine_grained_timeline.json"
    if not timeline_path.exists():
        timeline_path = VISUAL_DIR / "scene_timeline.json"
    if not timeline_path.exists():
        raise FileNotFoundError(f"找不到分镜时间轴: {timeline_path}")
    raw = json.loads(timeline_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise HtmlGenerationError(f"分镜时间轴格式错误: {timeline_path}")
    return [normalize_scene(item, index) for index, item in enumerate(raw, 1)]


def audio_duration_seconds() -> float:
    wav_path = AUDIO_SRT_DIR / "final_output.wav"
    if not wav_path.exists():
        return 1.0
    with wave.open(str(wav_path), "rb") as wav:
        return round(wav.getnframes() / wav.getframerate(), 3)


def clean_model_html(raw: str) -> str:
    text = raw.strip()
    fence_match = re.search(r"```(?:html)?\s*(.*?)```", text, re.S | re.I)
    if fence_match:
        text = fence_match.group(1).strip()
    start = text.lower().find("<!doctype html")
    if start == -1:
        start = text.lower().find("<html")
    if start > 0:
        text = text[start:]
    return text


def validate_hyperframes_html(content: str) -> None:
    required = [
        "<html",
        'id="stage"',
        'data-composition-id="main"',
        "window.base64Subtitle",
        "window.__timelines",
        "timelineData",
        'id="subtitle-overlay"',
        'id="bg-layer"',
        'id="bg-glow"',
    ]
    missing = [item for item in required if item not in content]
    if missing:
        raise HtmlGenerationError(f"语言模型返回 HTML 缺少必要结构: {', '.join(missing)}")


def call_openai_compatible_html(
    *,
    scenes: list[dict[str, Any]],
    duration: float,
    api_key: str,
    base_url: str,
    model: str,
    style: str,
) -> str:
    base = (base_url or DEFAULT_OPENAI_COMPATIBLE_BASE_URL).rstrip("/")
    url = f"{base}/chat/completions"
    compact_scenes = [
        {
            "scene_id": scene["scene_id"],
            "start_time": round(float(scene["start_time"]), 3),
            "end_time": round(float(scene["end_time"]), 3),
            "text_content": scene["text_content"],
            "visual_summary": scene.get("visual_summary") or scene["text_content"],
        }
        for scene in scenes
    ]
    system_prompt = (
        "你是资深视频视觉模板工程师。输出必须是一个完整 HTML 文档，"
        "用于 Hyperframes 渲染 1920x1080 口播/PPT 翻页视频。"
        "只输出 HTML，不要解释，不要 Markdown。"
    )
    user_prompt = f"""
根据下面 scene_timeline 生成完整 HTML。

硬性要求：
- 必须包含 <div id="stage" data-composition-id="main" data-width="1920" data-height="1080" data-duration="{duration}" data-start="0">。
- 必须包含 <audio id="main-audio" src="./2_audio_srt/final_output.wav" data-start="0" autoplay></audio>。
- 必须保留 window.base64Subtitle = ""; 作为 Python 后续注入字幕的位置。
- 必须定义 const timelineData = [...]，每项含 slide/start/end。
- 必须定义 window.__timelines['main'] = {{ duration, seek(t), play(), pause() }}。
- seek(t) 必须控制当前 slide 显示、字幕显示、进度条。
- 每个分镜用一个 .slide，data-slide 等于 scene_id。
- 页面风格是“{style}”，像高质感中文知识口播/PPT翻页，不要使用外部网络资源。
- #stage 内最前面必须依次包含 <div id="bg-layer"></div> 和 <div id="bg-glow"></div>，#bg-layer 必须使用 rgba 半透明渐变背景。
- 字幕容器 id 必须是 subtitle-overlay，进度条 id 必须是 progress-bar-fill。
- CSS/JS 必须自包含，不能引用远程脚本。

scene_timeline:
{json.dumps(compact_scenes, ensure_ascii=False)}
"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if "openrouter.ai" in base:
        headers.setdefault("HTTP-Referer", "http://localhost:8010")
        headers.setdefault("X-Title", "voice-over-video")

    response = requests.post(
        url,
        headers=headers,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.35,
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise HtmlGenerationError(f"无法解析语言模型响应: {payload}") from exc
    cleaned = clean_model_html(content)
    validate_hyperframes_html(cleaned)
    return cleaned


def text_size_class(text: str) -> str:
    length = len(text)
    if length <= 32:
        return "short"
    if length <= 72:
        return "medium"
    return "long"


def break_text(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"([。！？；;])", r"\1<br>", escaped)
    escaped = re.sub(r"(，|、)", r"\1<wbr>", escaped)
    return escaped


def fallback_html(scenes: list[dict[str, Any]], duration: float, style: str) -> str:
    total = len(scenes)
    slide_blocks = []
    timeline_items = []
    for index, scene in enumerate(scenes, 1):
        scene_id = scene["scene_id"]
        text = scene["text_content"]
        cls = text_size_class(text)
        slide_blocks.append(
            f"""
  <div class="slide" data-slide="{html.escape(scene_id)}">
    <div class="slide-scene-num">{index:02d} / {total:02d}</div>
    <div class="slide-content">
      <div class="slide-kicker">VOICE OVER VIDEO</div>
      <p class="slide-text {cls}">{break_text(text)}</p>
    </div>
  </div>"""
        )
        timeline_items.append(
            {
                "slide": scene_id,
                "start": round(float(scene["start_time"]), 3),
                "end": round(float(scene["end_time"]), 3),
            }
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Voice Over Video</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: 100vw; height: 100vh; overflow: hidden;
    display: grid; place-items: center;
    background: #050812;
    font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", Arial, sans-serif;
  }}
  #stage {{
    position: relative;
    width: 1920px; height: 1080px; overflow: hidden;
    background: transparent;
    color: #ecf2ff;
  }}
  #bg-layer {{
    position: absolute; inset: 0; z-index: 1;
    background:
      radial-gradient(circle at 10% 14%, rgba(124,219,255,.20), rgba(6,16,31,.70) 32%, rgba(6,16,31,.70) 33%),
      radial-gradient(circle at 90% 16%, rgba(139,92,246,.18), rgba(11,21,40,.70) 34%, rgba(11,21,40,.70) 35%),
      linear-gradient(135deg, rgba(6,16,31,.72) 0%, rgba(11,21,40,.70) 48%, rgba(9,26,29,.72) 100%);
  }}
  #bg-glow {{
    position: absolute; inset: 0; z-index: 2; pointer-events: none;
    background: radial-gradient(circle at 50% 42%, rgba(124,219,255,.10), rgba(124,219,255,0) 44%);
  }}
  .mesh {{
    position: absolute; inset: 0; opacity: .5; z-index: 3;
    background-image:
      linear-gradient(rgba(255,255,255,.045) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,.045) 1px, transparent 1px);
    background-size: 64px 64px;
    mask-image: linear-gradient(180deg, rgba(0,0,0,.86), transparent 92%);
  }}
  .frame-line {{
    position: absolute; left: 72px; right: 72px; top: 72px; bottom: 72px;
    border: 1px solid rgba(148,163,184,.20);
    pointer-events: none; z-index: 8;
  }}
  .brand-pill {{
    position: absolute; left: 96px; top: 92px;
    border: 1px solid rgba(124,219,255,.32);
    color: #9fe8ff; background: rgba(124,219,255,.08);
    padding: 12px 18px; border-radius: 8px;
    font-size: 18px; letter-spacing: .18em; z-index: 9;
  }}
  .slide {{
    position: absolute; inset: 0; display: none; z-index: 7;
    align-items: center; justify-content: center;
    padding: 138px 170px 170px;
    opacity: 0; transform: translateY(28px) scale(.985);
  }}
  .slide-content {{ width: min(1360px, 100%); text-align: left; }}
  .slide-kicker {{
    margin-bottom: 32px; color: #7cdbff;
    font-size: 20px; letter-spacing: .18em; font-weight: 700;
  }}
  .slide-scene-num {{
    position: absolute; right: 96px; top: 92px;
    color: rgba(206,216,234,.70); font-size: 22px; letter-spacing: .08em; z-index: 9;
  }}
  .slide-text {{
    color: #f7fbff; text-wrap: balance;
    text-shadow: 0 24px 80px rgba(0,0,0,.34);
  }}
  .slide-text.short {{ font-size: 76px; line-height: 1.28; font-weight: 760; }}
  .slide-text.medium {{ font-size: 62px; line-height: 1.42; font-weight: 730; }}
  .slide-text.long {{ font-size: 48px; line-height: 1.58; font-weight: 680; }}
  #subtitle-overlay {{
    display: none; position: absolute; left: 0; bottom: 40px;
    width: 100%; z-index: 9999;
    max-width: 1400px; padding: 18px 34px;
    margin: 0 auto; right: 0;
    border-radius: 8px; background: rgba(0,0,0,.68);
    border: 1px solid rgba(255,255,255,.12);
    color: #fff; font-size: 40px; line-height: 1.42; font-weight: 700;
    text-align: center; text-shadow: 0 3px 14px rgba(0,0,0,.75);
  }}
  .progress-bar-wrap {{
    position: absolute; left: 0; right: 0; bottom: 0; height: 5px;
    background: rgba(255,255,255,.07); z-index: 10000;
  }}
  .progress-bar-fill {{
    width: 0%; height: 100%;
    background: linear-gradient(90deg, #7cdbff, #8b5cf6, #2dd4bf);
  }}
</style>
</head>
<body>
<div id="stage" data-composition-id="main" data-width="1920" data-height="1080" data-duration="{duration}" data-start="0">
  <div id="bg-layer"></div>
  <div id="bg-glow"></div>
  <audio id="main-audio" src="./2_audio_srt/final_output.wav" data-start="0" autoplay></audio>
  <div class="mesh"></div>
  <div class="frame-line"></div>
  <div class="brand-pill">{html.escape(style.upper())}</div>
{''.join(slide_blocks)}
  <div id="subtitle-overlay"></div>
  <div class="progress-bar-wrap"><div class="progress-bar-fill" id="progress-bar-fill"></div></div>
</div>
<script>
window.base64Subtitle = "";

let subtitleData = [];
function parseTime(str) {{
  const parts = str.split(':');
  const secParts = parts[2].split(',');
  return parseInt(parts[0]) * 3600 + parseInt(parts[1]) * 60 + parseInt(secParts[0]) + parseInt(secParts[1]) / 1000;
}}
if (window.base64Subtitle) {{
  try {{
    const rawSubtitleText = decodeURIComponent(escape(atob(window.base64Subtitle)));
    const blocks = rawSubtitleText.trim().split(/\\n\\s*\\n/);
    for (const block of blocks) {{
      const lines = block.split('\\n');
      if (lines.length >= 3) {{
        const match = lines[1].match(/([\\d:,]+)\\s*-->\\s*([\\d:,]+)/);
        if (match) subtitleData.push({{ start: parseTime(match[1]), end: parseTime(match[2]), text: lines.slice(2).join(' ').trim() }});
      }}
    }}
  }} catch (err) {{ console.error('SRT Base64 解码失败', err); }}
}}
let showSubtitles = true;
if (typeof window.__hyperframes !== 'undefined' && window.__hyperframes.getVariables) {{
  const hfVars = window.__hyperframes.getVariables();
  if (hfVars && hfVars.showSubtitles === false) showSubtitles = false;
}}
const timelineData = {json.dumps(timeline_items, ensure_ascii=False, indent=2)};
const totalDuration = {duration};
window.__timelines = window.__timelines || {{}};
window.__timelines['main'] = {{
  duration: totalDuration,
  seek: function(t) {{
    for (const item of timelineData) {{
      const slideEl = document.querySelector('.slide[data-slide="' + item.slide + '"]');
      if (!slideEl) continue;
      if (t >= item.start && t < item.end) {{
        slideEl.style.display = 'flex';
        const progress = Math.min((t - item.start) / 0.42, 1);
        slideEl.style.opacity = progress;
        slideEl.style.transform = 'translateY(' + ((1 - progress) * 28) + 'px) scale(' + (.985 + .015 * progress) + ')';
      }} else {{
        slideEl.style.display = 'none';
        slideEl.style.opacity = 0;
      }}
    }}
    let currentText = '';
    for (const sub of subtitleData) {{
      if (t >= sub.start && t < sub.end) {{ currentText = sub.text; break; }}
    }}
    const subOverlay = document.getElementById('subtitle-overlay');
    if (showSubtitles && currentText) {{
      subOverlay.innerText = currentText;
      subOverlay.style.display = 'block';
    }} else {{
      subOverlay.style.display = 'none';
    }}
    const bar = document.getElementById('progress-bar-fill');
    if (bar) bar.style.width = Math.min((t / totalDuration) * 100, 100) + '%';
  }},
  play: function() {{}},
  pause: function() {{}}
}};
</script>
</body>
</html>
"""


def generate_visual_html(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    style: str = "video-edit-agent",
) -> tuple[Path, str]:
    VISUAL_DIR.mkdir(parents=True, exist_ok=True)
    scenes = load_scenes()
    duration = max(audio_duration_seconds(), max(float(scene["end_time"]) for scene in scenes) + 0.5)

    provider = "fallback"
    content: str
    key = api_key or os.getenv("GEMINI_API_KEY", "")
    if key:
      try:
          content = call_openai_compatible_html(
              scenes=scenes,
              duration=duration,
              api_key=key,
              base_url=base_url or os.getenv("GEMINI_API_BASE", DEFAULT_OPENAI_COMPATIBLE_BASE_URL),
              model=model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
              style=style,
          )
          provider = "runninghub_gemini"
      except Exception as exc:
          content = fallback_html(scenes, duration, f"{style} / fallback: {exc}")
          provider = f"fallback_after_language_model_error: {exc}"
    else:
        content = fallback_html(scenes, duration, style)

    out_path = VISUAL_DIR / "index.html"
    out_path.write_text(content, encoding="utf-8")
    return out_path, provider
