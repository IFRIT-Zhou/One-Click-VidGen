"""Typed data, not generated code, for the pinned Shotcraft adapters."""
from __future__ import annotations

import copy
import math
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .vibe_motion_plan import Identifier, MotionBackground
from .vibe_source_style import SourcePalette, SourceStyle
from .vibe_visual_basis import VisualBasis, validate_visual_basis

VERSION = 'ocv-shotcraft-v1'
RENDERER_VERSION = 'ocv-remotion-shotcraft-v1'
RECIPE_RULES = json.loads((Path(__file__).resolve().parents[2] / 'tools/vibe_remotion/lib/shotcraft-recipes.json').read_text(encoding='utf-8'))
RECIPES = tuple(RECIPE_RULES)
Recipe = Literal[tuple(RECIPES)]


def minimum_frames(recipe, count):
    rule = RECIPE_RULES[recipe]
    return max(rule['min_frames'], rule.get('per_item_frames', 0)*count)


class Item(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Identifier
    title: str = Field(default='', max_length=16, description='Audience-facing caption; may be empty for an image card. Never fill with production labels.')
    body: str = Field(default='', max_length=70)
    asset_id: Identifier | None = None
    motif: Literal['document', 'book', 'inspection', 'refinement', 'none'] = 'none'


class Stage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Identifier
    recipe: Recipe
    at: float = Field(ge=0, le=60, allow_inf_nan=False,
        description='Absolute shot-relative start in seconds; not a subtitle offset. First stage must be 0. Next stage start ends this stage.')
    eyebrow: str = Field(default='', max_length=18)
    headline: str = Field(min_length=1, max_length=28)
    body: str = Field(default='', max_length=70)
    footer: str = Field(default='', max_length=45)
    items: list[Item] = Field(min_length=1, max_length=4)

    @model_validator(mode='after')
    def editorial_limits(self):
        if not self.headline.strip() or any(not item.title.strip() and not item.asset_id for item in self.items):
            raise ValueError('标题不可为空白；已绑定图片的卡片可以不带标题')
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError('同阶段的条目编号不能重复')
        if self.recipe == 'spotlight-hero-card' and len(self.items) != 1:
            raise ValueError('spotlight 只允许一个主角条目')
        rule = RECIPE_RULES[self.recipe]
        if not rule['min_items'] <= len(self.items) <= rule['max_items']:
            raise ValueError(f'{self.recipe} 需要 {rule["min_items"]} 至 {rule["max_items"]} 个条目')
        if len(self.headline) > rule.get('headline_max', 28):
            raise ValueError(f'{self.recipe} 标题最多 {rule["headline_max"]} 字')
        if rule.get('image_required') and any(not item.asset_id for item in self.items):
            raise ValueError('扫描聚焦需要真实原图素材，不能扫描空白道具')
        if rule.get('text_only') and any(item.asset_id or item.motif != 'none' for item in self.items):
            raise ValueError('重点划线仅支持文字；需要展示照片时请选择图像模板')
        if rule.get('body_required') and any(not item.body.strip() for item in self.items):
            raise ValueError('翻牌背面需要真实解释文字 item.body')
        if any(len(item.body) > rule.get('item_body_max', 70) for item in self.items):
            raise ValueError(f'{self.recipe} 小卡正文最多 {rule["item_body_max"]} 字；请精简或移到阶段正文，不能删改证据')
        if self.recipe == 'outro-group-photo-launch':
            for item in self.items:
                if len(item.body) > 22:
                    raise ValueError(f'阶段{self.id}条目{item.id}.body实长{len(item.body)}字符，最大22（英文/数字/标点也计数）；原文={item.body!r}。DOI不要截断，移到footer或stage.body；小卡只放短关键词')
        return self


class ShotcraftPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal['ocv-shotcraft-v1'] = VERSION
    canvas: tuple[int, int]
    palette: SourcePalette
    background: MotionBackground
    typography: Literal['sans', 'serif'] = 'sans'
    stages: list[Stage] = Field(min_length=1, max_length=4)

    @model_validator(mode='after')
    def layout(self):
        w, h = self.canvas
        if w < 160 or h < 90 or w > 1920 or h > 1080 or w*h > 1920*1080 or w % 2 or h % 2:
            raise ValueError('画布须是至多1920×1080的偶数像素尺寸')
        if w/h < 1.6:
            raise ValueError('首批模板仅支持横屏16:10及以上；不会拉伸竖屏，请使用本地模式或其他视频渠道')
        if len({stage.id for stage in self.stages}) != len(self.stages):
            raise ValueError('阶段编号不能重复')
        return self


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recipes: list[Recipe] = Field(min_length=1, max_length=3)
    reason: str = Field(min_length=1, max_length=1200)
    source_style: SourceStyle

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.recipes)) != len(self.recipes):
            raise ValueError('模板选择不能重复')
        return self


class OriginalCrop(BaseModel):
    """A bounded rectangle in an existing input, never a path or generated image."""
    model_config = ConfigDict(extra='forbid')
    id: Identifier
    source: Identifier
    box: tuple[float, float, float, float] = Field(description='Normalized left, top, right, bottom in the original input; preserve the complete subject/evidence, not the whole poster.')

    @model_validator(mode='after')
    def rectangle(self):
        l, t, r, b = self.box
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in self.box) or l >= r or t >= b:
            raise ValueError('裁切框须是0到1的有效原图矩形坐标')
        if self.source == 'core' and (r-l)*(b-t) > .95:
            raise ValueError('不能用整张core海报冒充主体裁切；请选择具体照片区域')
        return self


class ShotcraftProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: str = Field(min_length=1, max_length=1600)
    source_notes: str = Field(default='', max_length=2500)
    questions: list[str] = Field(default_factory=list, max_length=5)
    source_style: SourceStyle
    visual_basis: VisualBasis
    crops: list[OriginalCrop] = Field(default_factory=list, max_length=4,
        description='Optional lossless original-pixel rectangles, performed locally without an image API or matting.')
    scene: ShotcraftPlan | None = None


def crop_specs(proposal, original_ids):
    originals = set(original_ids)
    specs = {}
    used = {item.asset_id for stage in proposal.scene.stages for item in stage.items} if proposal.scene else set()
    for crop in proposal.crops:
        if crop.id.startswith('render_'):
            raise ValueError('裁切素材编号不能占用审查帧的render_命名空间')
        if crop.id in originals or crop.id in specs:
            raise ValueError('裁切素材编号不能覆盖原图或重复：'+crop.id)
        if crop.source not in originals:
            raise ValueError('裁切只能来自本次实际输入原图：'+crop.source)
        if crop.id not in used or not any(e.source == crop.source and e.treatment == 'preserve'
                and e.output_asset_id == crop.id for e in proposal.visual_basis.elements):
            raise ValueError('裁切必须绑定画面并声明对应原图的preserve来源：'+crop.id)
        specs[crop.id] = {'mode': 'crop', 'source': crop.source, 'box': list(crop.box)}
    return specs


def bind_style(raw, profile: SourceStyle):
    """Frozen original-image style is authoritative; keep raw reply unmodified."""
    result = copy.deepcopy(raw)
    result['source_style'] = profile.model_dump(mode='json')
    if isinstance(result.get('scene'), dict):
        result['scene']['palette'] = profile.palette.model_dump(mode='json')
        result['scene']['background'] = profile.background.model_dump(mode='json')
    return result


def compile_shotcraft_plan(plan: ShotcraftPlan, cues, duration: float, asset_ids) -> dict:
    # Cues are sent to the director, but timestamps are explicitly absolute here.
    # This avoids the old relative-cue + absolute-at double-addition ambiguity.
    if not math.isfinite(duration) or not 2.5 <= duration <= 60:
        raise ValueError('首批模板支持2.5至60秒的单镜配音')
    known = set(asset_ids)
    result = plan.model_dump(mode='json', exclude_none=True)
    result.update(duration=duration, fps=30)
    if plan.stages[0].at != 0:
        raise ValueError('首阶段必须在本镜0秒开始，不能空等后面的字幕')
    for index, stage in enumerate(plan.stages):
        end = plan.stages[index+1].at if index+1 < len(plan.stages) else duration
        frames = math.floor(end*30+.5)-math.floor(stage.at*30+.5)
        minimum = minimum_frames(stage.recipe, len(stage.items))
        if end > duration or frames < minimum:
            raise ValueError(f'阶段 {stage.id} 时长不足：{stage.recipe} 至少需要 {minimum/30:g} 秒（含入场与阅读停留），不得缩短动作硬塞；请减少条目或阶段')
        if stage.recipe == 'outro-group-photo-launch' and end-stage.at > 6.001:
            raise ValueError(f'阶段{stage.id}把收束模板占用{end-stage.at:.2f}秒，聚合动作后会长时间静止。收束最多6秒；将前面仍在讲解的内容用spotlight/relay表达，保留末段收束，不得压缩原配音')
        for item in stage.items:
            if item.asset_id == 'core':
                raise ValueError('core 是整张分镜海报，只用于阅读，不得当成已抠出的主体显示')
            if item.asset_id and item.asset_id not in known:
                raise ValueError(f'阶段 {stage.id} 引用未知原素材 {item.asset_id}')
        result['stages'][index].pop('at')
        result['stages'][index].update(start=stage.at, end=end)
    return result


def validate_proposal(proposal, *, canvas, duration, cues, assets, selected):
    if proposal.scene is None:
        raise ValueError('缺少模板动画方案')
    if tuple(proposal.scene.canvas) != tuple(canvas):
        raise ValueError('不得更改输入画布尺寸')
    specs = crop_specs(proposal, assets)
    for stage in proposal.scene.stages:
        visible = [stage.eyebrow, stage.headline, stage.body, stage.footer]
        visible += [text for item in stage.items for text in (item.title, item.body)]
        for text in visible:
            if text.strip().strip('：:。') in {'机构署名', '主体占位', '图片占位', '阶段A', '价值落点', '内容收束', '镜头推进'}:
                raise ValueError(f'阶段 {stage.id} 出现制作标签“{text}”，不能上屏；图片卡可留空title，请保留真实观众文案')
    has_image = any(item.asset_id for stage in proposal.scene.stages for item in stage.items)
    if not has_image and any('医学示意' in (stage.eyebrow + stage.footer)
                             for stage in proposal.scene.stages):
        raise ValueError('本次无医学图像入画，不应把原海报的“医学示意”标签复制到页眉/页脚；请移除该标签，其他引文/DOI保持原值')
    if any(stage.recipe not in selected for stage in proposal.scene.stages):
        raise ValueError('方案使用了未选择/未读取说明的模板')
    proof_count = len(assets) + len(specs) + 4 + len(proposal.scene.stages) + sum(
        len(stage.items) for stage in proposal.scene.stages if RECIPE_RULES[stage.recipe].get('sequential') or stage.recipe == 'card-flip-reveal')
    if proof_count > 32:
        raise ValueError(f'方案需要{proof_count}张完整审查图片，超过32张上限；请减少接力条目/阶段，不得省略原图或审查帧')
    core_evidence = any(e.source == 'core' and e.kind == 'evidence' for e in proposal.visual_basis.elements)
    originals = any((e.source != 'core' or e.output_asset_id in specs) and e.kind == 'evidence' and e.treatment == 'preserve'
                    for e in proposal.visual_basis.elements)
    if core_evidence and not originals:
        labels = '、'.join(e.label for e in proposal.visual_basis.elements
                          if e.source == 'core' and e.kind == 'evidence')
        raise ValueError(f'原图元素“{labels}”被标为evidence，但没有独立保真素材。'
                         '请先重新核查分类：医院名称、作者、年份、期刊名、引文和DOI是text，可按原文native排版；'
                         '整张文字海报的布局是decoration，不要索取文字截图或独立文字图层。'
                         '若确为组织切片、实验照片、原始曲线等视觉证据，可用crops完整保真裁取并绑定；'
                         '只有无法完整提取才通过questions请求独立原素材；'
                         '不得把真实证据改标为text或擅自省略以绕过校验')
    validate_visual_basis(proposal.visual_basis, proposal.scene, set(assets), specs, required=True, original_only=not specs)
    return compile_shotcraft_plan(proposal.scene, cues, duration, set(assets) | set(specs))
