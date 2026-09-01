"""Parse user-authored structural blank markers without exposing them to models."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable


MIN_BLANK_SECONDS = 0.2
MAX_BLANK_SECONDS = 30.0
DEFAULT_BLANK_SECONDS = 3.0
CANONICAL_MARKER = "【OCV留白：{seconds:.1f}秒】"

_MARKER = re.compile(
    r"【\s*OCV\s*留白\s*[：:]\s*(\d+(?:\.\d+)?)\s*秒\s*】",
    re.IGNORECASE,
)
_MARKER_START = re.compile(r"【\s*OCV\s*留白", re.IGNORECASE)


@dataclass(frozen=True)
class StructuralBlankPlan:
    text_blocks: list[str]
    pauses_after_blocks: list[float]
    leading_pause: float = 0.0
    trailing_pause: float = 0.0

    @property
    def clean_text(self) -> str:
        return "\n".join(block for block in self.text_blocks if block).strip()

    @property
    def marker_count(self) -> int:
        return sum(1 for value in self.pauses_after_blocks if value > 0) + int(self.leading_pause > 0) + int(self.trailing_pause > 0)


@dataclass(frozen=True)
class StructuralChunkPlan:
    chunks: list[str]
    pauses_after: list[float]
    leading_pause: float = 0.0
    trailing_pause: float = 0.0


def parse_structural_blanks(text: str) -> StructuralBlankPlan:
    source = str(text or "")
    matches = list(_MARKER.finditer(source))
    if _MARKER_START.search(_MARKER.sub("", source)):
        raise ValueError("留白标记格式不正确，请使用【OCV留白：3.0秒】")
    if not matches:
        clean = source.strip()
        return StructuralBlankPlan([clean] if clean else [], [0.0] if clean else [])

    blocks: list[str] = []
    pauses: list[float] = []
    pending_pause = 0.0
    leading_pause = 0.0
    cursor = 0
    for match in matches:
        seconds = float(match.group(1))
        if not MIN_BLANK_SECONDS <= seconds <= MAX_BLANK_SECONDS:
            raise ValueError(f"留白时长必须在 {MIN_BLANK_SECONDS:.1f}–{MAX_BLANK_SECONDS:.1f} 秒之间")
        block = source[cursor:match.start()].strip()
        if block:
            if pending_pause:
                if blocks:
                    pauses[-1] = round(pauses[-1] + pending_pause, 3)
                else:
                    leading_pause = round(leading_pause + pending_pause, 3)
                pending_pause = 0.0
            blocks.append(block)
            pauses.append(round(seconds, 3))
        else:
            pending_pause += seconds
        cursor = match.end()

    tail = source[cursor:].strip()
    trailing_pause = 0.0
    if tail:
        if pending_pause:
            if blocks:
                pauses[-1] = round(pauses[-1] + pending_pause, 3)
            else:
                leading_pause = round(leading_pause + pending_pause, 3)
        blocks.append(tail)
        pauses.append(0.0)
    else:
        trailing_pause = round((pauses[-1] if pauses else 0.0) + pending_pause, 3)
        if pauses:
            pauses[-1] = 0.0
        elif pending_pause:
            leading_pause = round(pending_pause, 3)

    return StructuralBlankPlan(blocks, pauses, leading_pause, trailing_pause)


def segment_structural_script(
    text: str,
    segmenter: Callable[[str], list[str]],
) -> StructuralChunkPlan:
    plan = parse_structural_blanks(text)
    chunks: list[str] = []
    pauses_after: list[float] = []
    for block, pause in zip(plan.text_blocks, plan.pauses_after_blocks):
        block_chunks = [str(value).strip() for value in segmenter(block) if str(value).strip()]
        if not block_chunks:
            continue
        chunks.extend(block_chunks)
        pauses_after.extend([0.0] * len(block_chunks))
        pauses_after[-1] = round(float(pause), 3)
    if not chunks and (plan.leading_pause or plan.trailing_pause):
        raise ValueError("留白标记前后至少需要一段可配音文案")
    return StructuralChunkPlan(chunks, pauses_after, plan.leading_pause, plan.trailing_pause)


def canonical_blank_marker(seconds: float = DEFAULT_BLANK_SECONDS) -> str:
    value = max(MIN_BLANK_SECONDS, min(MAX_BLANK_SECONDS, float(seconds)))
    return CANONICAL_MARKER.format(seconds=value)
