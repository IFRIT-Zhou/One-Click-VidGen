"""Source-first visual direction and original-pixel provenance for cloud Vibe.

This is a semantic contract, not an image-content classifier. The director must
identify the important source subjects, and the visual review checks omissions.
Local validation ensures declared preservation is real, bound, and displayed.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .vibe_motion_plan import Identifier


class VisualElement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=120)
    source: Identifier
    kind: Literal['subject', 'evidence', 'chart', 'text', 'decoration']
    treatment: Literal['preserve', 'native', 'omit']
    output_asset_id: Identifier | None = None
    reason: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def semantic_treatment(self):
        if not self.label.strip():
            raise ValueError('原图元素名称不能为空')
        if self.kind in ('subject', 'evidence') and self.treatment == 'native':
            raise ValueError('原图主体和证据不可用代码或生成式插画重画；请保真复用，确实无关才 omit 并说明')
        if self.treatment == 'omit' and not self.reason.strip():
            raise ValueError('省略原图元素必须说明与当前叙事无关的具体原因')
        if self.treatment != 'preserve' and self.output_asset_id is not None:
            raise ValueError('只有 preserve 元素可指定 output_asset_id；native/omit 不应伪装为素材保留')
        return self


class VisualBasis(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['native', 'hybrid', 'image_led']
    style_notes: str = Field(min_length=1, max_length=1200)
    elements: list[VisualElement] = Field(min_length=1, max_length=24)

    @model_validator(mode='after')
    def meaningful_style(self):
        if not self.style_notes.strip():
            raise ValueError('须说明原图色彩、人物或画风如何延续到动效中')
        if self.mode == 'native' and any(
                element.treatment != 'omit' and not (
                    element.kind in ('text', 'chart') or
                    (element.kind == 'decoration' and element.treatment == 'native'))
                for element in self.elements):
            raise ValueError('native 可用于文字、图表和代码装饰；需要保留原图主体或证据时请使用 hybrid 或 image_led，不能仅为分隔线等装饰切换模式')
        return self


def _data(value):
    return value.model_dump(mode='json', exclude_none=True) if hasattr(value, 'model_dump') else value


def validate_visual_basis(basis: VisualBasis | None, scene, source_asset_ids: set[str],
                          material_specs: Mapping[str, dict], *, required=False, original_only=False):
    """Validate all provenance before any paid material dispatch.

    material_specs includes reused specs and this proposal's ordered material
    plan. source_asset_ids are the original shot inputs, not generated outputs.
    """
    if basis is None:
        if required:
            raise ValueError('新云端动效方案必须明确 visual_basis：先辨认原图主体、证据和风格，再决定保留/原生排版/省略；不可默默变成纯文字')
        return
    basis = VisualBasis.model_validate(basis)
    source_asset_ids = set(source_asset_ids)
    if original_only and material_specs:
        raise ValueError('当前代码动效只允许原始输入素材，不使用生成、裁切或抠图素材')
    specs = {key: _data(value) for key, value in material_specs.items()}
    if source_asset_ids.intersection(specs):
        raise ValueError('生成素材 ID 不可覆盖输入原图身份')
    known = source_asset_ids | set(specs)
    for identity, spec in specs.items():
        if not isinstance(spec, dict) or spec.get('mode') not in ('crop', 'matte', 'illustration'):
            raise ValueError(f'素材 {identity} 的来源记录无效')
        links = list(spec.get('references') or [])
        if spec.get('source') is not None:
            links.append(spec['source'])
        if spec['mode'] in ('crop', 'matte') and not spec.get('source'):
            raise ValueError(f'素材 {identity} 缺少原像素来源')
        if any(link not in known for link in links):
            raise ValueError(f'素材 {identity} 的来源链包含未知素材')

    raw_scene = _data(scene)
    used = set()
    for stage in raw_scene.get('stages', []):
        visual = stage.get('visual') or {}
        if visual.get('asset_id'):
            used.add(visual['asset_id'])
        used.update(item['asset_id'] for item in stage.get('items', []) if item.get('asset_id'))

    def roots(identity, trail=frozenset()):
        if identity in trail:
            raise ValueError(f'素材 {identity} 的来源链循环，不能验证原图保真')
        if identity in source_asset_ids:
            return {identity}
        spec = specs.get(identity)
        if spec is None:
            raise ValueError(f'素材 {identity} 不存在于原图或本次素材链')
        links = ([spec['source']] if spec.get('source') else []) + list(spec.get('references') or [])
        result = set()
        for link in links:
            result.update(roots(link, trail | {identity}))
        return result

    def original_pixels(identity, source, trail=frozenset()):
        if identity == source:
            return
        if identity in trail:
            raise ValueError(f'素材 {identity} 的保真来源链循环')
        spec = specs.get(identity)
        if spec is None or spec.get('mode') not in ('crop', 'matte'):
            raise ValueError(f'保留素材 {identity} 必须直接复用 {source} 或使用其 crop/matte 原像素链，不能以 illustration 冒充保真')
        original_pixels(spec['source'], source, trail | {identity})

    protected_sources = set()
    preserved_subjects = 0
    for element in basis.elements:
        if element.source not in source_asset_ids:
            raise ValueError(f'原图元素“{element.label}”的 source {element.source} 不是本次输入原图/参考图；生成素材不能冒充原图')
        if element.kind in ('subject', 'evidence'):
            protected_sources.add(element.source)
        if element.treatment != 'preserve':
            continue
        output = element.output_asset_id or element.source
        if original_only and output != element.source:
            raise ValueError(f'当前代码动效只能直接复用原输入 {element.source}，不能用派生素材 {output}')
        if output not in known:
            raise ValueError(f'原图元素“{element.label}”的输出素材 {output} 不存在')
        original_pixels(output, element.source)
        if output not in used:
            raise ValueError(f'原图元素“{element.label}”声明保留 {output}，但未在 stage.visual.asset_id 或 item.asset_id 中实际显示')
        if element.kind in ('subject', 'evidence'):
            preserved_subjects += 1
    for identity, spec in specs.items():
        ancestry = roots(identity)  # Validate every chain, including unused plans.
        if spec['mode'] == 'illustration' and ancestry & protected_sources:
            raise ValueError(f'素材 {identity} 以受保护主体/证据为生成式参考，不能重画其身份或证据；请改用 crop/matte 保留原像素')
    if basis.mode in ('hybrid', 'image_led') and not preserved_subjects:
        raise ValueError(f'{basis.mode} 模式至少须保留一个原图主体或证据，并在实际阶段画面中显示；不能仅声明融合却输出纯文字')
