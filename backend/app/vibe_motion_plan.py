"""Semantic cloud motion plans: content and narration cues, never drawing code.

The model chooses narrative stages and reusable layout/motion recipes. This
module resolves their clock against the final saved subtitle timeline. React
components, not model coordinates, own typography, spacing and transitions.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RENDERER_VERSION = 'ocv-remotion-v1'
Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9_:-]{1,80}$')]
Colour = Annotated[str, Field(pattern=r'^#[0-9a-fA-F]{6}$')]

THEME_PALETTES = {
    'paper': dict(background='#F6F3ED', ink='#152D34', muted='#64757A',
                  surface='#FFFEFA', line='#D9E0DB', accent='#217568'),
    'clinical': dict(background='#F2F7F8', ink='#102F43', muted='#5E7683',
                     surface='#FFFFFF', line='#D5E4E9', accent='#167B91'),
    'midnight': dict(background='#0B1821', ink='#F1F6F6', muted='#A1B8C1',
                     surface='#142A35', line='#304A55', accent='#89D9BF'),
}


def _contrast(first: str, second: str) -> float:
    def luminance(colour):
        channels = [int(colour[i:i+2], 16)/255 for i in (1, 3, 5)]
        linear = [c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4 for c in channels]
        return sum(c*w for c, w in zip(linear, (.2126, .7152, .0722)))
    a, b = luminance(first), luminance(second)
    return (max(a, b)+.05)/(min(a, b)+.05)


class MotionPalette(BaseModel):
    """Restricted source-inspired colours, not arbitrary model CSS."""
    model_config = ConfigDict(extra='forbid')
    background: Colour | None = None
    ink: Colour | None = None
    muted: Colour | None = None
    surface: Colour | None = None
    line: Colour | None = None
    accent: Colour | None = None


class MotionBackground(BaseModel):
    """Finite background recipes; no arbitrary model CSS or external textures."""
    model_config = ConfigDict(extra='forbid')
    kind: Literal['solid', 'soft_gradient', 'technical_grid', 'paper']


class StageVisual(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asset_id: Identifier
    placement: Literal['left', 'right'] = 'left'
    treatment: Literal['contain', 'cutout'] = 'contain'
    caption: str = Field(default='', max_length=120)


class MotionItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Identifier
    title: str = Field(min_length=1, max_length=72)
    body: str = Field(default='', max_length=220)
    value: str = Field(default='', max_length=48,
                       description='Exact verified display value, including inequality if needed; not invented or animated interpolation.')
    unit: str = Field(default='', max_length=32)
    note: str = Field(default='', max_length=100)
    asset_id: Identifier | None = None
    cue: str | None = Field(default=None, max_length=128,
                            description='Saved subtitle ID anchoring this item. Usually omit: the first meaningful item must enter within 0.5s of its stage, not after the full spoken sentence.')
    at: float | None = Field(default=None, ge=-10, le=60, allow_inf_nan=False,
                            description='Seconds after cue, or after stage start without cue; omit for gentle stagger. Leave time for complete entrance, readable hold and exit.')


class MotionStage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Identifier
    layout: Literal['findings', 'comparison', 'process', 'evidence'] = Field(
        description='findings: independent findings/cards; comparison: parallel contrast; process: true ordered steps; evidence: preserved original images with short annotations. Do not imply causality through process when the source only reports association.')
    cue: str | None = Field(default=None, max_length=128,
                            description='Saved subtitle anchor. Prefer cue alone with at omitted: stage start is cue start plus at, NOT cue start plus an absolute timestamp.')
    at: float = Field(default=0, ge=-10, le=60, allow_inf_nan=False,
                      description='OFFSET seconds after cue; usually OMIT when cue is given. Never repeat the cue start timestamp here. Without cue: absolute shot seconds. First stage: no cue and at=0.')
    headline: str = Field(min_length=1, max_length=90)
    eyebrow: str = Field(default='', max_length=48)
    footer: str = Field(default='', max_length=140)
    visual: StageVisual | None = Field(default=None,
        description='Optional large original subject/evidence alongside native text. Bind an input asset or prepared faithful crop/matte; never an arbitrary URL. Appears at stage start; reusing the same asset preserves continuity.')
    items: list[MotionItem] = Field(min_length=1, max_length=4)

    @model_validator(mode='after')
    def content(self):
        if not self.headline.strip() or any(not item.title.strip() for item in self.items):
            raise ValueError('阶段标题和条目标题不能为空')
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError('阶段内条目 ID 不能重复')
        if self.layout == 'evidence' and self.visual is None and not any(item.asset_id for item in self.items):
            raise ValueError('证据展示阶段必须绑定已有原图素材')
        if self.layout == 'comparison' and len(self.items) < 2:
            raise ValueError('对比阶段至少需要两个对比对象')
        return self


class MotionPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal['ocv-motion-v1'] = 'ocv-motion-v1'
    canvas: tuple[int, int] = (1920, 1080)
    theme: Literal['paper', 'midnight', 'clinical'] = 'paper'
    title: str = Field(min_length=1, max_length=90)
    subtitle: str = Field(default='', max_length=100)
    accent: Colour | None = None
    palette: MotionPalette | None = Field(default=None,
        description='Optional hex-colour overrides inspired by the input poster. Preserve readable contrast; palette.accent overrides the legacy accent field.')
    background: MotionBackground | None = Field(default=None,
        description='Optional source-inspired background recipe. New cloud designs bind this from source_style; omitted only for legacy plans.')
    stages: list[MotionStage] = Field(min_length=1, max_length=8,
        description='Information-driven chronological stages, usually 1–4; a short summary needs only 1–2. Do not pad stages with synonymous repetition. Each replaces the previous; first starts at shot time 0.')

    @model_validator(mode='after')
    def validate_plan(self):
        w, h = self.canvas
        if not 160 <= w <= 1920 or not 90 <= h <= 1920 or w*h > 1920*1080 or w % 2 or h % 2:
            raise ValueError('画布须为偶数尺寸，最多 1920×1080 总像素（可竖屏）')
        if not self.title.strip():
            raise ValueError('镜头标题不能为空')
        if len({stage.id for stage in self.stages}) != len(self.stages):
            raise ValueError('阶段 ID 不能重复')
        palette = dict(THEME_PALETTES[self.theme])
        if self.accent is not None:
            palette['accent'] = self.accent
        if self.palette is not None:
            palette.update(self.palette.model_dump(exclude_none=True))
        if _contrast(palette['ink'], palette['background']) < 4.5:
            raise ValueError('配色正文与背景对比度不足 4.5，请保留原图风格但提高文字可读性')
        if _contrast(palette['muted'], palette['background']) < 3:
            raise ValueError('配色辅助文字与背景对比度不足 3，请提高辅助文字可读性')
        return self


def compile_motion_plan(plan: MotionPlan, cues: dict[str, float], duration: float,
                        asset_ids: set[str] | dict) -> dict:
    """Resolve semantic anchors; reject missing cues/assets before any paid material."""
    if not math.isfinite(duration) or not .1 <= duration <= 60:
        raise ValueError('单镜时长必须位于 0.1–60 秒')
    starts = []
    for stage in plan.stages:
        if stage.cue is not None and stage.cue not in cues:
            raise ValueError(f'阶段 {stage.id} 引用不存在的字幕 {stage.cue}')
        cue_start = cues[stage.cue] if stage.cue is not None else 0
        start = cue_start + stage.at
        calculation = (f'cue {stage.cue} 起点 {cue_start:.3f} + at偏移 {stage.at:.3f} = {start:.3f} 秒'
                       if stage.cue is not None else f'本镜起点 0 + at {stage.at:.3f} = {start:.3f} 秒')
        if not math.isfinite(start) or start < 0 or start >= duration:
            raise ValueError(f'阶段 {stage.id} 时间超出本镜最终配音范围：{calculation}；合法起点为 0（含）–{duration:.3f}（不含）秒。'
                             '有cue时at只是偏移，通常省略at，不能重复填字幕起点；不得修改原字幕时间轴')
        starts.append(start)
    if abs(starts[0]) > .001:
        stage=plan.stages[0]
        raise ValueError(f'首阶段必须从本镜 0 秒开始，不能留下等待配音的空白开场：阶段 {stage.id} '
                         f'cue起点 {cues.get(stage.cue,0):.3f} + at偏移 {stage.at:.3f} = {starts[0]:.3f} 秒；请不填cue且at=0')
    for index,(a,b) in enumerate(zip(starts,starts[1:])):
        if b<=a:
            previous,current=plan.stages[index:index+2]
            raise ValueError(f'阶段须按最终字幕时间顺序排列且起点不能重复：{previous.id}={a:.3f} 秒，'
                             f'{current.id} 的cue起点 {cues.get(current.cue,0):.3f} + at偏移 {current.at:.3f} = {b:.3f} 秒。'
                             '有cue时at不是绝对时间，通常省略at；不得自动重排原字幕时间轴')
    result = plan.model_dump(mode='json', exclude_none=True)
    for index, (stage, start, dest) in enumerate(zip(plan.stages, starts, result['stages'])):
        if stage.visual is not None and stage.visual.asset_id not in asset_ids:
            raise ValueError(f'阶段 {stage.id} 主体引用了不存在的素材 {stage.visual.asset_id}')
        end = starts[index+1] if index+1 < len(starts) else duration
        length = end-start
        if length < min(1.2, duration):
            raise ValueError(f'阶段 {stage.id} 不足 1.2 秒，请合并内容，保留入场和阅读时间')
        entrance = min(.85, length*.35)
        reading = min(1., length*.40)
        exit_time = min(.3, length*.10)
        latest_offset = max(0., length-entrance-reading-exit_time)
        first_deadline = min(.5, length*.20)
        # Defaults adapt to short shots. Explicit model cue/at values are never
        # clamped, and saved narration/subtitle/stage boundaries stay untouched.
        stagger = min(.18, latest_offset/max(1, len(stage.items)-1))
        offsets = []
        dest.pop('cue', None); dest.pop('at', None)
        dest.update(start=round(start, 6), end=round(end, 6))
        for item_index, (item, value) in enumerate(zip(stage.items, dest['items'])):
            if item.asset_id is not None and item.asset_id not in asset_ids:
                raise ValueError(f'阶段 {stage.id} 引用了不存在的素材 {item.asset_id}')
            if item.cue is not None and item.cue not in cues:
                raise ValueError(f'条目 {item.id} 引用不存在的字幕 {item.cue}')
            if item.cue is not None:
                offset = cues[item.cue] + (item.at or 0) - start
            else:
                offset = item.at if item.at is not None else item_index*stagger
            if not math.isfinite(offset) or offset < 0 or offset > latest_offset+1e-6:
                latest = start+latest_offset
                actual = start+offset
                anchor = f'{item.cue}+{item.at or 0:.3f}' if item.cue is not None else f'阶段起点+{offset:.3f}'
                raise ValueError(
                    f'阶段 {stage.id} 的实际时段为 {start:.3f}–{end:.3f} 秒；'
                    f'条目 {item.id} 按 {anchor} 在本镜 {actual:.3f} 秒入场，'
                    f'距离阶段结束仅 {end-actual:.3f} 秒。'
                    f'该条目合法入场范围为本镜 {start:.3f}–{latest:.3f} 秒'
                    f'（不填cue时，本阶段at范围为 0–{latest_offset:.3f} 秒），'
                    f'须预留完整入场 {entrance:.3f} 秒、完整阅读 {reading:.3f} 秒'
                    f'和退出 {exit_time:.3f} 秒。请提前该条目、把它放到对应的下一阶段，'
                    '或调整下一阶段起点；不得修改原字幕时间轴。')
            offsets.append(offset)
            value.pop('cue', None)
            value['at'] = round(offset, 6)
        first_index = min(range(len(offsets)), key=offsets.__getitem__)
        if stage.visual is None and offsets[first_index] > first_deadline+1e-6:
            first_item = stage.items[first_index]
            raise ValueError(
                f'阶段 {stage.id} 的实际时段为 {start:.3f}–{end:.3f} 秒；'
                f'首个有意义主体（条目 {first_item.id}）到本镜 '
                f'{start+offsets[first_index]:.3f} 秒才入场，阶段开头空等 '
                f'{offsets[first_index]:.3f} 秒。首个主体须在阶段开始后 '
                f'0–{first_deadline:.3f} 秒（本镜 {start:.3f}–'
                f'{start+first_deadline:.3f} 秒）入场。请省略条目的cue/at采用默认错峰，'
                '或提前主体入场；不要等整句口播说完才显示。阶段仍按字幕语义边界安排，'
                '不得修改原字幕时间轴。')
    return result
