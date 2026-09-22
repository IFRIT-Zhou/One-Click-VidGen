"""Convert OCV's model-neutral motion prompt into MiniMax H3 Ref2VA format.

The contract is intentionally isolated from the storyboard Agents.  It runs only
when a project explicitly enables it for a local ComfyUI video workflow.
"""
from __future__ import annotations

import hashlib
import base64
import io
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .gemini_client import generate_gemini_text, parse_json_response
from .video_text_policy import approved_visible_texts, visual_first_prompt_issues


H3_SKILL_SOURCE = "MiniMax-AI/MiniMax-H3 h3-prompt-writing + OCV community Ref2V refinements"
_SECTIONS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
_TIME_RANGE = re.compile(
    r"\[\s*(\d{1,2}):(\d{1,2}(?:\.\d+)?)\s*[-–—~至]\s*"
    r"(\d{1,2}):(\d{1,2}(?:\.\d+)?)\s*\]"
)

_SYSTEM = r"""You are OCV H3 Prompt Agent. Your only job is to convert one already-approved,
model-neutral video prompt into the official MiniMax H3 full-reference (Ref2VA) prompt format.
Do not redesign the storyboard, alter narration boundaries, add new facts, or change the core image.

Follow the official h3-prompt-writing skill contract:
1. Write exactly these six sections, in exactly this order:
   subject_definitions, summary, retention_analysis, detailed_description,
   overall_soundscape, non_diegetic_music.
2. Write the section bodies in English. Keep dialogue, lyrics, and visible scene text in their
   original language. Never translate or invent visible text.
3. The actual core-storyboard image is attached to this request. Inspect its pixels before writing.
   Use <Picture 1> consistently. It is the supplied core-storyboard image and a composition/action
   anchor. It may be a near-opening image, but it is not a command to keep every depicted element
   visible at once or to return to the same composition at the end.
4. Use concrete chronological stages with [Shot N] and [HH:MM-HH:MM]. The final timestamp must
   exactly equal requested_duration_seconds, which is always 4-15 seconds.
5. Describe composition, subjects, positions, environment, actions/state changes, camera motion,
   physical sound, and exactly when referenced content appears. Prefer specific observable actions.
6. Do not introduce unresolved reference labels. Define any <Subject N> before using it.
7. Preserve speaker attribution and the approved order of events. A quoted belief, thought,
   stereotype, hypothetical, or example must remain visibly framed as such.
8. Audio policy: do not invent narration, spoken dialogue, or singing. If the source explicitly asks
   for silence and reference_audio_supplied is false, write N/A for both audio sections. When
   reference_audio_supplied is true, define <Audio 1> as the supplied, already-final TTS segment and
   use its exact timing, cadence, pauses, and emotional emphasis to drive visible performance; never
   transcribe it into visible text, invent extra speech, replace its wording, or add background music.
   Otherwise retain only requested physical/ambient sound effects. H3 tasks in OCV must never generate
   background music, even when the source prompt mentions or requests music. Always write
   "N/A; no background music; retain only diegetic ambient and action sound effects" in
   non_diegetic_music.
9. OCV uses H3 for silent-performance animation with generated diegetic effects only. Never generate
   human speech, dialogue, narration, whispers, chanting, humming, singing, crowd voices, or vocal
   reactions. Generate only environmental ambience and physical/action sound effects.
10. Preserve the lighting direction, shadow placement, exposure, contrast, and color temperature of
   <Picture 1> as closely as possible. Do not invent dramatic relighting, flicker, lens flares, or a
   day/night change unless the approved source explicitly requests it. A centered presenter, podium,
   or stage never implies theatrical lighting. Unless the approved source explicitly requests a
   lighting change or effect, do not add a spotlight, circular pool of light, radial glow, halo,
   vignette, darkened corners, or dimmed surroundings to isolate the subject. By default, keep the
   whole frame illuminated like <Picture 1>.
11. Keep the camera locked by default. Perform only camera movement explicitly written in the approved
   model-neutral video prompt; do not add push-ins, pull-outs, pans, tilts, zooms, or handheld motion.
12. The detailed description should be rich enough for H3, but every described event must fit the
   short requested duration. Do not pad it with generic cinematic adjectives.
13. approved_visible_texts is authoritative. Only those exact strings may appear as newly generated
   visible text. Narration, subtitles, intent, quoted examples, and model-neutral prompt sentences are
   semantic context, not dialogue bubbles or titles. Remove any unapproved visible dialogue instruction
   instead of translating it. Empty approved_visible_texts means generate no new visible words. Pictorial
   thought bubbles may contain concrete places, objects, or events without written labels.
   Never generate subtitles, automatic captions, lower-third captions, narration transcription, title
   cards, explanatory overlays, or watermarks. Even when approved_visible_texts is non-empty, those exact
   short strings may appear only inside their approved in-scene container and must never become subtitles.
14. Describe change rather than redundantly reconstructing the attached image. Do not restate every
   visible hairstyle, clothing color, prop, or background object inside each shot. Instead explicitly
   preserve identity, drawing style, composition, spatial relations, lighting and palette from
   <Picture 1>, then describe only what moves, appears, disappears, changes, or receives emphasis.
15. Each [Shot N] must follow this internal order: composition and framing; referenced subjects;
   environment/light continuity; visible action arc; camera movement; precisely triggered diegetic
   sound effects; and the screen position/time at which <Picture 1> content is used. Keep one coherent
   camera style per shot. A cut must reveal genuinely new information; otherwise prefer a locked shot
   or the single camera movement already approved by the source prompt.
16. Use concrete physical verbs, materials, directions, speeds, amplitudes and screen positions.
   Avoid empty adjectives such as cinematic, atmospheric, stunning, or dramatic. Aim for roughly
   250-500 English words when the approved action has enough events, but never pad a short 4-15 second
   shot or schedule more actions than can visibly finish.

Return only a JSON object: {"h3_prompt":"..."}. The h3_prompt value contains the six sections.
"""


def _clean_prompt(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:text)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _image_data(image_path: Path | None) -> tuple[dict[str, str] | None, str]:
    if image_path is None:
        return None, ''
    image_path = Path(image_path)
    if not image_path.is_file() or image_path.stat().st_size <= 0:
        raise ValueError('核心分镜图不存在，无法交给 H3 提示词 Agent 识图')
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
        image.thumbnail((1600, 1600))
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=90, optimize=True)
    return {'mime_type': 'image/jpeg', 'data': base64.b64encode(buffer.getvalue()).decode('ascii')}, digest


def _repair_section_structure(prompt: str) -> str:
    """Normalize harmless heading variants and restore omitted H3 sections."""
    prompt = _clean_prompt(prompt)
    # Compatible providers sometimes wrap the required names in Markdown or
    # use spaces/hyphens instead of underscores despite the exact contract.
    for name in _SECTIONS:
        words = re.escape(name).replace(r"_", r"[\s_-]+")
        pattern = rf"(?im)^\s*(?:[#>*-]+\s*)?(?:\*\*)?{words}(?:\*\*)?\s*:?[ \t]*$"
        prompt = re.sub(pattern, f"{name}:", prompt)
    defaults = {
        "subject_definitions": "<Picture 1> is the supplied core storyboard image and visual anchor.",
        "summary": "Create the approved target-video action using <Picture 1> as the visual anchor.",
        "retention_analysis": "<Picture 1> is preserved for composition, subjects, style, lighting, and spatial relationships.",
        "detailed_description": "The approved action unfolds continuously while preserving <Picture 1>.",
        "overall_soundscape": "Generate only scene-appropriate environmental ambience and physical action sound effects.",
        "non_diegetic_music": "N/A; no background music.",
    }
    for index, name in enumerate(_SECTIONS):
        if re.search(rf"(?im)^\s*{re.escape(name)}\s*:\s*", prompt):
            continue
        insertion = f"{name}:\n{defaults[name]}\n\n"
        next_match = None
        for later in _SECTIONS[index + 1:]:
            next_match = re.search(rf"(?im)^\s*{re.escape(later)}\s*:\s*", prompt)
            if next_match:
                break
        if next_match:
            prompt = prompt[:next_match.start()] + insertion + prompt[next_match.start():]
        else:
            prompt = prompt.rstrip() + "\n\n" + insertion.rstrip()
    return prompt.strip()


def _section_bounds(prompt: str) -> dict[str, tuple[int, int]]:
    """Locate official sections so deterministic repairs never rewrite prose blindly."""
    matches = []
    for name in _SECTIONS:
        match = re.search(rf"(?im)^\s*{re.escape(name)}\s*:\s*", prompt)
        if match:
            matches.append((name, match.start(), match.end()))
    result = {}
    for index, (name, start, body_start) in enumerate(matches):
        result[name] = (body_start, matches[index + 1][1] if index + 1 < len(matches) else len(prompt))
    return result


def _insert_in_section(prompt: str, section: str, sentence: str) -> str:
    bounds = _section_bounds(prompt)
    if section not in bounds:
        return prompt
    body_start, _ = bounds[section]
    return prompt[:body_start] + "\n" + sentence.strip() + "\n" + prompt[body_start:]


def _section_body(prompt: str, section: str) -> str:
    """Return one H3 section body for section-specific policy checks."""
    bounds = _section_bounds(prompt)
    if section not in bounds:
        return ''
    body_start, body_end = bounds[section]
    return prompt[body_start:body_end]


def _repair_picture_label(prompt: str) -> str:
    """Canonicalize or supply the one image label required by this pipeline.

    Compatible language providers occasionally paraphrase the reference as
    ``Picture 1``, ``Image 1`` or ``图1``.  That is semantically harmless but
    violates H3's exact label grammar.  Repair it locally instead of asking the
    user to spend another Agent call or blocking the GPU workflow.
    """
    prompt = _clean_prompt(prompt)
    aliases = (
        r"<\s*(?:picture|image)\s*1\s*>",
        r"\[\s*(?:picture|image)\s*1\s*\]",
        r"(?<![\w<])(?:picture|image)\s*1(?![\w>])",
        r"(?<![\w<])(?:图|图片|参考图|核心图)\s*1(?!\w)",
    )
    for pattern in aliases:
        prompt = re.sub(pattern, "<Picture 1>", prompt, flags=re.I)
    if "<Picture 1>" not in prompt:
        prompt = _insert_in_section(
            prompt, "subject_definitions",
            "<Picture 1> is the supplied core storyboard image and the composition and action anchor for the target video.",
        )
    # The official skill expects labels to stay meaningful across the analysis
    # and playback description. Add only the missing relationship statements.
    bounds = _section_bounds(prompt)
    for section, sentence in (
        ("summary", "[reference generation] Use <Picture 1> as the visual anchor for the approved target-video action."),
        ("retention_analysis", "<Picture 1> is partially preserved for composition, subject identity, style, and spatial relationships."),
        ("detailed_description", "<Picture 1> anchors the opening composition; its depicted elements may unfold over time rather than remain simultaneous."),
    ):
        start, end = bounds.get(section, (0, 0))
        if start == end or "<Picture 1>" in prompt[start:end]:
            continue
        prompt = _insert_in_section(prompt, section, sentence)
        bounds = _section_bounds(prompt)
    return prompt


def _clock(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _repair_timeline(prompt: str, duration: int) -> str:
    """Normalize provider timing variations and end exactly at requested duration.

    H3 asks for exact timestamps, but compatible language models commonly end a
    12-second request at 11, 11.5 or 13 seconds. The stage order is already
    approved, so retrying the Agent adds cost without adding editorial value.
    Keep all stage text and deterministically normalize only its time ranges.
    """
    matches = list(_TIME_RANGE.finditer(prompt))
    if not matches:
        return _insert_in_section(
            prompt,
            "detailed_description",
            f"[Shot 1] [00:00-{_clock(duration)}] The approved action unfolds continuously across the requested duration.",
        )
    values = []
    for match in matches:
        start = int(match.group(1)) * 60 + float(match.group(2))
        end = int(match.group(3)) * 60 + float(match.group(4))
        values.append((start, end))
    largest = max(end for _, end in values)
    scale = duration / largest if largest > duration and largest > 0 else 1.0
    normalized = []
    for index, (start, end) in enumerate(values):
        new_start = max(0, min(duration, round(start * scale)))
        new_end = max(new_start, min(duration, round(end * scale)))
        if index == len(values) - 1:
            new_end = duration
            if new_start >= duration:
                new_start = max(0, duration - 1)
        normalized.append(f"[{_clock(new_start)}-{_clock(new_end)}]")
    for match, replacement in reversed(list(zip(matches, normalized))):
        prompt = prompt[:match.start()] + replacement + prompt[match.end():]
    return prompt


def _enforce_no_background_music(prompt: str) -> str:
    """Make the global H3 no-BGM policy deterministic, including cached prompts."""
    bounds = _section_bounds(prompt)
    if "non_diegetic_music" not in bounds:
        return prompt
    body_start, body_end = bounds["non_diegetic_music"]
    policy = "N/A; no background music; retain only diegetic ambient and action sound effects."
    return prompt[:body_start] + policy + "\n" + prompt[body_end:]


def _enforce_visual_and_sound_policy(prompt: str) -> str:
    """Apply only the mandatory H3 output-audio policy to the submitted prompt.

    Lighting and camera guidance belongs to the conversion Agent's system
    contract and must not be pasted as boilerplate into every final prompt.
    """
    bounds = _section_bounds(prompt)
    if "overall_soundscape" in bounds:
        body_start, body_end = bounds["overall_soundscape"]
        sound = (
            "Generate only scene-appropriate diegetic environmental ambience and physical/action "
            "sound effects. Do not generate any human speech, dialogue, narration, whispering, "
            "chanting, humming, singing, crowd voices, or vocal reactions."
        )
        prompt = prompt[:body_start] + sound + "\n" + prompt[body_end:]
    subtitle_policy = (
        "Do not generate subtitles, automatic captions, lower thirds, narration transcription, title "
        "cards, explanatory overlays, or watermarks. Only exact approved short in-scene text may appear "
        "inside its assigned object or bubble; never turn it into subtitles."
    )
    if subtitle_policy not in prompt:
        prompt = _insert_in_section(prompt, "detailed_description", subtitle_policy)
    return prompt


def _enforce_reference_audio(prompt: str, *, reference_audio: bool, lipsync: bool) -> str:
    """Keep the Agent from weakening the selected per-shot audio behavior."""
    if not reference_audio:
        return prompt
    definition = (
        "<Audio 1> is the supplied TTS performance reference. It controls the exact timing, "
        "cadence, pauses, emotion, facial performance, and mouth movement of the visible speaker."
    )
    if definition not in prompt:
        prompt = _insert_in_section(prompt, "subject_definitions", definition)
    if lipsync:
        direction = (
            "The visible speaking subject must continuously lip-sync to <Audio 1> with accurate "
            "mouth opening, closing, syllable rhythm, pauses, facial expression, and matching body "
            "performance. Do not merely use <Audio 1> as loose pacing. The supplied audio is a "
            "performance reference rather than the final soundtrack; do not visualize or transcribe it."
        )
    else:
        direction = (
            "Use <Audio 1> only for action timing, pauses, and emotional cadence. Mouth lip-sync is "
            "not required. The supplied audio is a performance reference rather than the final soundtrack."
        )
    if direction not in prompt:
        prompt = _insert_in_section(prompt, "detailed_description", direction)
    return prompt


def _validate(prompt: str, duration: int, *, reference_audio: bool = False,
              lipsync: bool = False) -> str:
    prompt = _repair_section_structure(prompt)
    prompt = _repair_picture_label(prompt)
    positions = []
    for name in _SECTIONS:
        match = re.search(rf"(?im)^\s*{re.escape(name)}\s*:\s*", prompt)
        if not match:
            raise ValueError(f"H3 转换结果缺少 {name} 段落")
        positions.append(match.start())
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise ValueError("H3 转换结果的六段结构顺序不正确")
    if "<Picture 1>" not in prompt:
        raise ValueError("H3 转换结果没有保留核心分镜图标签 <Picture 1>")
    prompt = _enforce_reference_audio(prompt, reference_audio=reference_audio, lipsync=lipsync)
    prompt = _enforce_visual_and_sound_policy(prompt)
    prompt = _enforce_no_background_music(prompt)
    prompt = _repair_timeline(prompt, duration)
    ranges = re.findall(r"\[(\d{2}):(\d{2})-(\d{2}):(\d{2})\]", prompt)
    if not ranges:
        raise ValueError("H3 转换结果缺少 [HH:MM-HH:MM] 镜头时间轴")
    ends = [int(minutes) * 60 + int(seconds) for _, _, minutes, seconds in ranges]
    if max(ends) != duration or any(end > duration for end in ends):
        raise ValueError(f"H3 转换结果的时间轴无法自动校正到 {duration} 秒")
    # Prevent a malformed response from silently becoming an expensive GPU run.
    if len(prompt) < 500 or len(prompt) > 16000:
        raise ValueError("H3 转换结果长度异常")
    if not 4 <= duration <= 15:
        raise ValueError("H3 仅接受 4～15 秒的镜头提示词")
    return prompt


def source_fingerprint(shot: dict[str, Any], duration: int, *, reference_audio: bool = False,
                       lipsync: bool = False, image_digest: str = '') -> str:
    source = {
        "intent": str(shot.get("intent") or ""),
        "action": str(shot.get("action") or ""),
        "image_prompt": str(shot.get("image_prompt") or ""),
        "video_prompt": str(shot.get("video_prompt") or ""),
        "duration": duration,
        "reference_audio": reference_audio,
        "reference_audio_lipsync": lipsync,
        "core_image_digest": image_digest,
        "skill": H3_SKILL_SOURCE,
        "contract": 11,
    }
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def convert_for_h3(shot: dict[str, Any], duration: int, *, reference_audio: bool = False,
                   lipsync: bool = False, image_path: Path | None = None) -> tuple[str, str]:
    """Return ``(H3 prompt, source fingerprint)`` for one frozen storyboard shot."""
    image_data, image_digest = _image_data(image_path)
    fingerprint = source_fingerprint(shot, duration, reference_audio=reference_audio, lipsync=lipsync,
                                     image_digest=image_digest)
    cached = str(shot.get("h3_prompt") or "").strip()
    if cached and shot.get("h3_prompt_source") == fingerprint:
        return _validate(cached, duration, reference_audio=reference_audio, lipsync=lipsync), fingerprint
    generic = str(shot.get("video_prompt") or "").strip()
    if not generic:
        raise ValueError("通用视频提示词为空，无法转换为 H3 提示词")
    payload = {
        "requested_duration_seconds": duration,
        "storyboard_intent": str(shot.get("intent") or ""),
        "approved_dynamic_expression": str(shot.get("action") or ""),
        "core_image_prompt_for_context": str(shot.get("image_prompt") or ""),
        "model_neutral_video_prompt": generic,
        "approved_visible_texts": approved_visible_texts(shot.get("motion_plan")),
        "reference_audio_supplied": reference_audio,
        "reference_audio_requires_lipsync": bool(reference_audio and lipsync),
        "core_storyboard_image_attached": bool(image_data),
        "core_storyboard_image_sha256": image_digest,
        "reference_assets": [
            {"label": "<Picture 1>", "role": "core storyboard image / composition and action anchor"}
        ] + ([{"label": "<Audio 1>", "role": "final TTS segment / timing and performance reference"}]
             if reference_audio else []),
    }
    raw = generate_gemini_text(
        system_prompt=_SYSTEM,
        user_prompt=json.dumps(payload, ensure_ascii=False),
        temperature=0.1,
        max_output_tokens=7000,
        response_mime_type="application/json",
        json_root="object",
        image_data=image_data,
    )
    result = parse_json_response(raw)
    for _ in range(2):
        if not isinstance(result, str):
            break
        result = parse_json_response(result)
    if not isinstance(result, dict) or not isinstance(result.get("h3_prompt"), str):
        raise ValueError("H3 提示词 Agent 未返回有效 JSON 对象")
    prompt = _validate(result["h3_prompt"], duration, reference_audio=reference_audio, lipsync=lipsync)
    # Only the playback description instructs H3 to render new on-screen
    # content.  Scanning subject_definitions with the generic quote matcher can
    # pair the closing ASCII quote of one definition with the opening quote of
    # the next and fabricate a false visible-text span across two subjects.
    issues = visual_first_prompt_issues(
        _section_body(prompt, "detailed_description"), shot.get("motion_plan")
    )
    if issues:
        raise ValueError("H3 转换结果重新加入了未选用的画中文字：" + "；".join(issues))
    return prompt, fingerprint
