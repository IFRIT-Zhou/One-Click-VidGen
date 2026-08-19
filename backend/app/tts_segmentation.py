"""Semantic, token-safe sentence grouping for the IndexTTS-2.5 engine.

This module is deliberately independent from the IndexTTS2 2.0 character
splitter.  The language model may only group already numbered, contiguous
text units; Python remains the authority for coverage, order and the hard
token ceiling.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from .gemini_client import (
    GeminiError,
    generate_gemini_text,
    language_provider_configured,
    parse_json_response,
)
from .indextts25_local import IndexTTS25Config, load_indextts25_config


INDEXTTS25_SEGMENT_MAX_TOKENS = 110
SEGMENTER_VERSION = "indextts25-voice-agent-v1"
_STRONG_ENDINGS = frozenset("。！？!?")
_CLOSERS = frozenset("”’」』】）)]》〉")
_SECONDARY_ENDINGS = frozenset("；;：:")
_WEAK_ENDINGS = frozenset("，,、")


@lru_cache(maxsize=2)
def _official_encoding(vocab_path: str):
    """Load the exact official 2.5 merge table without importing its GPU stack."""
    packages_dir = load_indextts25_config().packages_dir
    packages = str(packages_dir)
    if packages not in sys.path:
        sys.path.insert(0, packages)
    import tiktoken  # type: ignore

    ranks: dict[bytes, int] = {}
    with Path(vocab_path).open("rb") as source:
        for raw_line in source:
            line = raw_line.strip()
            if not line:
                continue
            token, rank = line.split()
            ranks[base64.b64decode(token)] = int(rank)
    return tiktoken.Encoding(
        name=Path(vocab_path).stem,
        pat_str=r"'s|'t|'re|'ve|'m|'ll|'d| ?\p{L}+| ?\p{N}+| ?[^\s\p{L}\p{N}]+|\s+(?!\S)|\s+",
        mergeable_ranks=ranks,
        special_tokens={},
    )


def build_indextts25_token_counter(config: IndexTTS25Config | None = None) -> Callable[[str], int]:
    """Return a counter backed by IndexTTS-2.5's bundled tokenizer vocabulary."""
    active = config or load_indextts25_config()
    vocab_path = active.model_dir / "multilingual_zh_ja_yue_char_del.tiktoken"
    if not vocab_path.is_file():
        raise FileNotFoundError(f"找不到 IndexTTS-2.5 tokenizer 词表: {vocab_path}")
    encoding = _official_encoding(str(vocab_path.resolve()))

    def count(text: str) -> int:
        # User text is counted as ordinary text. Pronunciation annotations are
        # transformed later by the official engine; the 10-token safety margin
        # below its native 120-token ceiling leaves room for that processing.
        return len(encoding.encode(text, disallowed_special=()))

    return count


def _ends_with_ellipsis(text: str, index: int) -> int:
    if text[index] == "…":
        end = index + 1
        while end < len(text) and text[end] == "…":
            end += 1
        return end
    if text.startswith("...", index):
        end = index + 3
        while end < len(text) and text[end] == ".":
            end += 1
        return end
    return 0


def split_strong_sentence_units(text: str) -> list[str]:
    """Split only at complete sentence endings, ellipses and paragraph ends."""
    units: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        end = 0
        if text[index] in _STRONG_ENDINGS:
            end = index + 1
        else:
            end = _ends_with_ellipsis(text, index)
        if text[index] in "\r\n":
            end = index + 1
            while end < len(text) and text[end] in "\r\n":
                end += 1
        if not end:
            index += 1
            continue
        while end < len(text) and text[end] in _CLOSERS:
            end += 1
        piece = text[start:end]
        if piece:
            units.append(piece)
        start = end
        index = end
    if start < len(text):
        units.append(text[start:])
    return [unit for unit in units if unit]


def _largest_safe_prefix(text: str, max_tokens: int, token_count: Callable[[str], int]) -> int:
    low, high = 1, len(text)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        if token_count(text[:middle]) <= max_tokens:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return max(1, best)


def _preferred_cut(text: str, safe_end: int) -> int:
    """Choose the strongest available boundary; commas are the last resort."""
    candidates: dict[str, int] = {"strong": 0, "secondary": 0, "weak": 0, "space": 0}
    index = 0
    while index < safe_end:
        char = text[index]
        ellipsis_end = _ends_with_ellipsis(text, index)
        if char in _STRONG_ENDINGS:
            candidates["strong"] = index + 1
        elif ellipsis_end and ellipsis_end <= safe_end:
            candidates["strong"] = ellipsis_end
            index = ellipsis_end - 1
        elif char in "\r\n":
            candidates["strong"] = index + 1
        elif char in _SECONDARY_ENDINGS:
            candidates["secondary"] = index + 1
        elif char in _WEAK_ENDINGS:
            candidates["weak"] = index + 1
        elif char.isspace():
            candidates["space"] = index + 1
        index += 1
    for level in ("strong", "secondary", "weak", "space"):
        if candidates[level] > 0:
            return candidates[level]
    return safe_end


def split_overlong_unit(
    text: str,
    *,
    max_tokens: int,
    token_count: Callable[[str], int],
) -> list[str]:
    """Hard-bound one long sentence, using commas only when no better cut exists."""
    result: list[str] = []
    remaining = text
    while remaining and token_count(remaining) > max_tokens:
        safe_end = _largest_safe_prefix(remaining, max_tokens, token_count)
        cut = _preferred_cut(remaining, safe_end)
        piece = remaining[:cut]
        if not piece:
            piece, cut = remaining[:safe_end], safe_end
        result.append(piece)
        remaining = remaining[cut:]
    if remaining:
        result.append(remaining)
    return result


def build_safe_sentence_units(
    text: str,
    *,
    max_tokens: int,
    token_count: Callable[[str], int],
) -> list[str]:
    units: list[str] = []
    for sentence in split_strong_sentence_units(text):
        if token_count(sentence) <= max_tokens:
            units.append(sentence)
        else:
            units.extend(
                split_overlong_unit(sentence, max_tokens=max_tokens, token_count=token_count)
            )
    return units


def fallback_group_units(
    units: list[str],
    *,
    max_tokens: int,
    token_count: Callable[[str], int],
) -> list[str]:
    """Greedily fill chunks while preserving the complete-sentence units."""
    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = current + unit
        if not current or token_count(candidate) <= max_tokens:
            current = candidate
            continue
        chunks.append(current)
        current = unit
    if current:
        chunks.append(current)
    return chunks


def _validate_agent_groups(
    raw: Any,
    units: list[str],
    *,
    max_tokens: int,
    token_count: Callable[[str], int],
) -> list[str]:
    if isinstance(raw, dict):
        raw = raw.get("groups")
    if not isinstance(raw, list) or not raw:
        raise ValueError("返回结果不是非空分组数组")
    expected = 1
    chunks: list[str] = []
    for item in raw:
        ids = item.get("includes_sentences") if isinstance(item, dict) else None
        if not isinstance(ids, list) or not ids:
            raise ValueError("分组缺少 includes_sentences")
        try:
            normalized_ids = [int(value) for value in ids]
        except (TypeError, ValueError) as exc:
            raise ValueError("句子编号不是整数") from exc
        wanted = list(range(expected, expected + len(normalized_ids)))
        if normalized_ids != wanted:
            raise ValueError("句子编号必须连续、有序且不能遗漏或重复")
        if normalized_ids[-1] > len(units):
            raise ValueError("句子编号超出范围")
        selected_units = [units[index - 1] for index in normalized_ids]
        chunk = "".join(selected_units)
        if token_count(chunk) <= max_tokens:
            chunks.append(chunk)
        else:
            # The model occasionally understands the semantic relationship but
            # miscalculates the supplied token totals. Keep its outer boundary
            # and clamp only this oversized group at existing complete-sentence
            # units. Every input unit has already passed the hard ceiling.
            repaired = fallback_group_units(
                selected_units,
                max_tokens=max_tokens,
                token_count=token_count,
            )
            if not repaired or any(token_count(value) > max_tokens for value in repaired):
                raise ValueError("Agent 超限分组无法安全细分")
            print(
                f"[TTS25_SEGMENT] Agent 有 1 组超过 {max_tokens} token，"
                f"已在完整句边界内安全细分为 {len(repaired)} 组",
                flush=True,
            )
            chunks.extend(repaired)
        expected = normalized_ids[-1] + 1
    if expected != len(units) + 1:
        raise ValueError("Agent 分组未完整覆盖全文")
    return chunks


def group_with_voice_segmentation_agent(
    units: list[str],
    *,
    max_tokens: int,
    token_count: Callable[[str], int],
    agent_call: Callable[..., str] = generate_gemini_text,
) -> list[str]:
    payload = [
        {"sentence_id": index, "tokens": token_count(unit), "text": unit}
        for index, unit in enumerate(units, 1)
    ]
    system_prompt = (
        "你是配音断句 Agent，只负责把相邻句子组合成适合自然朗读的配音段落。"
        "不得改写、删减、补充、调序或拆开任何输入句子。"
        "优先让一个语义完整的话题、动作或因果关系处在同一段；避免把承接词、转折句、问答关系拆散。"
        f"每组 token 总数不得超过 {max_tokens}，建议尽量落在 65-105 token。"
        "只输出严格 JSON 数组，每项格式为 {\"includes_sentences\":[1,2]}。"
        "所有 sentence_id 必须从 1 开始连续覆盖一次，不能遗漏、重复或跨组乱序。"
    )
    response = agent_call(
        system_prompt=system_prompt,
        user_prompt=json.dumps({"sentences": payload}, ensure_ascii=False),
        temperature=0.05,
        response_mime_type="application/json",
        max_output_tokens=min(4096, max(512, len(units) * 12)),
        json_root="array",
    )
    return _validate_agent_groups(
        parse_json_response(response), units, max_tokens=max_tokens, token_count=token_count
    )


def segment_indextts25_text(
    text: str,
    *,
    max_tokens: int = INDEXTTS25_SEGMENT_MAX_TOKENS,
    token_count: Callable[[str], int] | None = None,
    agent_enabled: bool | None = None,
    agent_call: Callable[..., str] = generate_gemini_text,
) -> tuple[list[str], str, int]:
    """Return chunks, source label and the original official-token count."""
    if not text:
        raise ValueError("IndexTTS-2.5 配音文案为空")
    count = token_count or build_indextts25_token_counter()
    total_tokens = count(text)
    if total_tokens <= max_tokens:
        return [text], "short_text", total_tokens

    units = build_safe_sentence_units(text, max_tokens=max_tokens, token_count=count)
    fallback = fallback_group_units(units, max_tokens=max_tokens, token_count=count)
    use_agent = language_provider_configured() if agent_enabled is None else agent_enabled
    source = "python_fallback"
    chunks = fallback
    if use_agent:
        try:
            chunks = group_with_voice_segmentation_agent(
                units,
                max_tokens=max_tokens,
                token_count=count,
                agent_call=agent_call,
            )
            source = "voice_segmentation_agent"
        except (GeminiError, json.JSONDecodeError, TypeError, ValueError, RuntimeError) as exc:
            print(f"[TTS25_SEGMENT] 配音断句 Agent 失败，已使用本地安全断句：{exc}", flush=True)
    if "".join(chunks) != text:
        raise RuntimeError("IndexTTS-2.5 断句完整性检查失败：结果未完整覆盖文案")
    if any(count(chunk) > max_tokens for chunk in chunks):
        raise RuntimeError("IndexTTS-2.5 断句失败：仍存在超过 110 token 的片段")
    return chunks, source, total_tokens
