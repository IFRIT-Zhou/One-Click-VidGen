"""Extract style once from core, then keep it authoritative through repairs."""
from __future__ import annotations

import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .vibe_motion_plan import Colour, MotionBackground, _contrast


class SourcePalette(BaseModel):
    model_config = ConfigDict(extra='forbid')
    background: Colour
    ink: Colour
    muted: Colour
    surface: Colour
    line: Colour
    accent: Colour

    @model_validator(mode='after')
    def readable(self):
        if _contrast(self.ink, self.background) < 4.5:
            raise ValueError('原图风格色板的正文/背景对比度不足4.5；请保留背景与主色，提高文字可读性')
        if _contrast(self.muted, self.background) < 3:
            raise ValueError('原图风格色板的辅助文字/背景对比度不足3')
        if _contrast(self.ink, self.surface) < 4.5:
            raise ValueError('原图风格色板的正文/卡片底色对比度不足4.5；请兼顾背景与卡片上的文字可读性')
        if _contrast(self.muted, self.surface) < 3:
            raise ValueError('原图风格色板的辅助文字/卡片底色对比度不足3')
        return self


class SourceStyle(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: Literal['core']
    palette: SourcePalette
    background: MotionBackground
    style_notes: str = Field(min_length=1, max_length=1200,
        description='Describe observed core brightness, dominant colours and visual character; do not invent a replacement default theme.')

    @model_validator(mode='after')
    def meaningful_notes(self):
        if not self.style_notes.strip():
            raise ValueError('source_style 必须说明核心分镜图的明暗、主色与风格')
        return self


def bind_source_style(raw: dict, frozen: SourceStyle | None = None):
    """Return a new proposal bound to the first valid source profile.

    Raw model responses stay intact for diagnostics. The model cannot replace
    the frozen profile by changing scene.palette/theme during a repair.
    """
    if frozen is None:
        if not isinstance(raw, dict) or raw.get('source_style') is None:
            raise ValueError('新云端动效方案必须提供 source_style：从 core 提取完整六色和背景，不能默默改成默认白底')
        profile = SourceStyle.model_validate(raw['source_style'])
    else:
        profile = SourceStyle.model_validate(frozen)
    normalized = copy.deepcopy(raw)
    normalized['source_style'] = profile.model_dump(mode='json')
    if isinstance(normalized.get('scene'), dict):
        normalized['scene']['palette'] = profile.palette.model_dump(mode='json')
        normalized['scene']['background'] = profile.background.model_dump(mode='json')
        normalized['scene']['accent'] = profile.palette.accent
    return normalized, profile
