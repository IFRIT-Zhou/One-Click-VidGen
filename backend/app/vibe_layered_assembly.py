"""Strict, reusable assembly of actual prepared assets before animation.

No generated code, file paths, invented data, or implicit material generation.
Groups survive in safe element IDs rather than unrecognised Remotion fields.
"""
from __future__ import annotations

from collections import Counter
import copy
import json
import math
from pathlib import Path
import re
import unicodedata
from typing import Annotated, Literal

from PIL import Image
from pydantic import Field, model_validator

from .vibe_layered import Strict, Label, Plate, Box, Color, Unit, bounds, VERSION
from .vibe_layered_limits import MAX_SUBJECTS, MAX_LABELS, MAX_DECORATIONS, MAX_LAYERS, MAX_MOTION_GROUPS

Group = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,23}$')]
Motion = Literal['fade', 'slide_left', 'slide_right', 'slide_up', 'scale', 'grow', 'draw']


def _response_payload(value):
    # Only the exact top-level response_format echo is transport metadata.
    # Never loosen unknown nested fields or mutate the diagnostic response.
    if isinstance(value, dict) and value.get('type') == 'json_object':
        return {key: item for key, item in value.items() if key != 'type'}
    return value


class RecoveredText(Strict):
    text: str = Field(min_length=1, max_length=180,
        description='从 core 原图逐字补录、初次清单遗漏且当前实际展示的原文；不是总结、扩写或改写。')
    source_box: Box = Field(description='该原文在 core 原图中的归一化 [左,上,右,下]，必须有正面积，供后续视觉核查。')

    @model_validator(mode='after')
    def traceable_original_text(self):
        if not _normalize(self.text):
            raise ValueError('补录原文不能为空、纯空白或仅有标点')
        bounds(self.source_box, 'recovered_text.source_box')
        return self


class AssemblySubject(Strict):
    asset_id: str = Field(pattern=r'^subject_[0-7]$')
    prepared_variant: Literal['cutout', 'original'] = Field(default='cutout',
        description='默认使用已抠图素材cutout；仅实际抠图破损且原始裁片可保真使用时选original。身份仍为asset_id，不能自行指定路径；两种素材均已从源图裁取，像素边界相同。')
    prepared_crop: Box = Field(default=(0, 0, 1, 1),
        description='仅针对当前实际 subject_N 已准备图片自身的归一化 [左,上,右,下]。素材已从 core/参考图裁过，默认整张 [0,0,1,1]；仅确实看见边缘污染时局部修边。绝不能复制源crop/box来二次裁切；source_anchor会同步换算修边后的目标位置，manual使用box。')
    placement: Literal['manual', 'source_anchor'] = Field(default='manual',
        description='core 原图取材且 source_geometry.anchor_available=true 时首选source_anchor：程序按源像素位置与prepared_crop确定目标box，无须重新估算。独立参考图或确需重新摆放用manual，并提供box。')
    box: Box | None = Field(default=None,
        description='manual时必填最终画布归一化[左,上,右,下]；source_anchor时可省略，程序根据可信源坐标与实际修边像素计算，模型提供的box不会覆盖源映射。')
    group: Group
    at: float = Field(default=0, ge=0, le=60)
    enter: float = Field(default=.6, ge=.2, le=2)
    motion: Literal['fade', 'slide_left', 'slide_right', 'slide_up', 'scale'] = 'fade'
    exit: float = Field(default=.3, ge=0, le=1)
    edge_fade_right: float = Field(default=0, ge=0, le=.25)
    embedded_text: list[Annotated[str, Field(min_length=1, max_length=180)]] = Field(default_factory=list, max_length=30,
        description='最终裁片中仍保留的可见文字，逐项填写；已裁掉或不存在则为空。不得与代码文字重复。')

    @model_validator(mode='before')
    @classmethod
    def legacy_crop_input(cls, value):
        # Read old saved responses without advertising the ambiguous field to
        # the model. New serialization always has exactly one coordinate key.
        if isinstance(value, dict) and 'crop' in value:
            if 'prepared_crop' in value:
                raise ValueError('不能同时提供 crop 和 prepared_crop；请仅使用实际主体素材坐标 prepared_crop')
            return {('prepared_crop' if key == 'crop' else key): item for key, item in value.items()}
        return value

    @model_validator(mode='after')
    def placement_contract(self):
        bounds(self.prepared_crop, 'prepared_crop')
        if self.placement == 'manual' and self.box is None:
            raise ValueError('manual 放置必须提供最终 box；core 原位还原可选择 source_anchor')
        if self.box is not None:
            bounds(self.box, 'subjects.box')
        return self

    @property
    def crop(self):
        """Read-only compatibility for internal callers, never model schema."""
        return self.prepared_crop


class AssemblyLabel(Label):
    group: Group
    font_size: float | None = Field(default=None, ge=12, le=200,
        description='输出 canvas 像素字号，文字最高200，与Remotion一致；醒目数字与单位可拆同组使用不同字号。独立空文字底板可省略。')
    at: float = Field(default=0, ge=0, le=60)
    exit: float = Field(default=.3, ge=0, le=1)


class Decoration(Strict):
    type: Literal['line', 'polyline', 'rect', 'ellipse']
    group: Group
    box: Box
    # Line points are normalized within this decoration's box, not the canvas.
    points: list[tuple[Unit, Unit]] = Field(default_factory=list, max_length=32,
        description='line/polyline 必填：line 恰好 2 点，polyline 至少 2 点；每点是 box 内部的归一化 [x,y]，不是全画布坐标。rect/ellipse 不填。')
    fill: Color = '#FFFFFF'
    stroke: Color = '#FFFFFF'
    stroke_width: float = Field(default=2, ge=0, le=12)
    radius: float = Field(default=0, ge=0, le=160)
    opacity: Unit = 1
    at: float = Field(default=0, ge=0, le=60)
    enter: float = Field(default=.6, ge=.2, le=2)
    exit: float = Field(default=.3, ge=0, le=1)
    motion: Motion = 'fade'

    @model_validator(mode='after')
    def line_contract(self):
        detail = f'decoration type={self.type} group={self.group} box={list(self.box)} points={self.points}'
        if self.type in ('line', 'polyline'):
            if len(self.points) < 2 or self.type == 'line' and len(self.points) != 2:
                raise ValueError(detail+'：线条 points 缺失或数量无效；line 恰好两个点，polyline 至少两个点。填写该 box 内部归一化路径，不能更改无关主体或文字')
            if len(set(self.points)) < 2:
                raise ValueError(detail+'：标注线不能是零长度路径；只修正 points，不猜测或自动改变方向')
        elif self.points:
            raise ValueError('rect/ellipse 不能携带线条 points')
        if self.motion == 'draw' and self.type not in ('line', 'polyline'):
            raise ValueError('draw 仅用于线条')
        return self


class Assembly(Strict):
    background_color: Color
    subjects: list[AssemblySubject] = Field(default_factory=list, max_length=MAX_SUBJECTS)
    labels: list[AssemblyLabel] = Field(default_factory=list, max_length=MAX_LABELS)
    decorations: list[Decoration] = Field(default_factory=list, max_length=MAX_DECORATIONS)
    recovered_text: list[RecoveredText] = Field(default_factory=list, max_length=16,
        description='仅用于补回初次 inventory 漏识别、但 core 原图确实存在的原文。必须逐字附原图 source_box，且在 labels 或 embedded_text 实际展示；后续视觉复核逐项核实。通常为空。')

    @model_validator(mode='before')
    @classmethod
    def remove_transport_marker(cls, value):
        return _response_payload(value)

    @model_validator(mode='after')
    def unique_subjects(self):
        ids = [subject.asset_id for subject in self.subjects]
        if len(ids) != len(set(ids)):
            raise ValueError('同一个主体素材不能重复摆放；请保留唯一主体')
        if not self.subjects and not self.labels and not self.decorations:
            raise ValueError('复原海报没有内容')
        _validate_recovered_occurrences(self.recovered_text)
        return self


class GroupMotion(Strict):
    id: Group
    at: float = Field(ge=0, le=60)
    enter: float = Field(default=.65, ge=.3, le=1.5)
    motion: Motion = 'fade'
    exit: float = Field(default=.3, ge=0, le=.7)


class MotionSchedule(Strict):
    groups: list[GroupMotion] = Field(min_length=1, max_length=MAX_MOTION_GROUPS)

    @model_validator(mode='before')
    @classmethod
    def remove_transport_marker(cls, value):
        return _response_payload(value)

    @model_validator(mode='after')
    def unique_groups(self):
        ids = [group.id for group in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError('每个信息组只能编排一次')
        return self


def _dimensions(context):
    width, height = context['canvas']
    duration = float(context['duration'])
    if not all(type(v) is int and 0 < v <= 1920 for v in (width, height)) or not math.isfinite(duration) or not .1 <= duration <= 60:
        raise ValueError('无效画布或时长')
    return width, height, duration


def _box(box, canvas, label):
    left, top, right, bottom = bounds(box, label)
    width, height = canvas
    result = [round(left*width), round(top*height), round(right*width)-round(left*width), round(bottom*height)-round(top*height)]
    if min(result[2:]) < 1:
        raise ValueError(label+' 至少需要一个像素')
    return result


def materialize_subjects(assembly: Assembly, assets: dict, output_dir: Path) -> dict:
    """Crop actual RGBA outputs, keeping original pixels and transparent bounds.

    Caller owns an isolated task directory. Never overwrite existing files or
    modify the original asset. No resizing occurs before final composition.
    """
    result = dict(assets)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, subject in enumerate(assembly.subjects):
        source_path = resolve_subject_asset(subject, assets)
        crop = bounds(subject.prepared_crop, f'subjects[{index}].prepared_crop ({subject.asset_id})')
        target = output_dir/f'placed_subject_{index}.png'
        if target.exists():
            raise FileExistsError('不会覆盖已有复原素材：'+target.name)
        with Image.open(source_path) as source:
            if source.width*source.height > 40_000_000:
                raise ValueError('主体素材尺寸过大')
            rgba = source.convert('RGBA')
            x, y, width, height = _box(crop, rgba.size, f'subjects[{index}].prepared_crop ({subject.asset_id})')
            piece = rgba.crop((x, y, x+width, y+height))
            if piece.getchannel('A').getbbox() is None:
                raise ValueError('主体裁片完全透明，请修正 crop')
            # No alpha-based auto-trim: the designer saw these exact pixel bounds.
            with target.open('xb') as output:
                piece.save(output, format='PNG')
        result[f'placed_subject_{index}'] = target
    return result


def resolve_subject_asset(subject: AssemblySubject, assets: dict):
    """Resolve a bounded variant, never a model-provided path or fallback."""
    key = subject.asset_id + ('_original' if subject.prepared_variant == 'original' else '')
    if key not in assets or assets[key] is None:
        raise ValueError(f'主体 {subject.asset_id} 未绑定实际素材 {key}（prepared_variant={subject.prepared_variant}）；'
                         '仅可选择已提供的素材，不会自动替换或访问任意路径')
    if subject.prepared_variant == 'original' and assets.get(subject.asset_id) is not None:
        with Image.open(assets[subject.asset_id]) as cutout, Image.open(assets[key]) as original:
            if cutout.size != original.size:
                raise ValueError(f'主体 {subject.asset_id} 的 original/cutout 像素边界不一致 '
                                 f'({original.width}x{original.height}/{cutout.width}x{cutout.height})；'
                                 '不能套用相同 prepared_crop 猜测映射，请重新准备同一坐标边界的素材')
    return assets[key]


def compile_assembly(assembly: Assembly, context: dict, assets: dict) -> dict:
    width, height, duration = _dimensions(context)
    if 'background' not in assets:
        raise ValueError('复原海报缺少实际背景素材')
    canvas = [width, height]
    elements = [dict(id='background', type='image', asset_id='background', role='background',
        box=[0, 0, width, height], at=0, end=duration, enter=0, exit=0, motion='none', fit='cover')]

    def common(layer, location):
        if layer.at + layer.enter + layer.exit >= duration:
            raise ValueError(f'{location} group={layer.group} 没有留出完整呈现时间；只调整本层时序')
        return dict(box=_box(layer.box, canvas, f'{location}.box group={layer.group}'), at=layer.at, end=duration,
                    enter=layer.enter, exit=layer.exit, motion=layer.motion)

    for index, subject in enumerate(assembly.subjects):
        if subject.box is None:
            raise ValueError(f'{subject.asset_id} 的 source_anchor 尚未通过源几何解析，不能猜测最终 box')
        asset = f'placed_subject_{index}'
        if asset not in assets:
            raise ValueError('请先依据实际 crop 准备主体素材：'+asset)
        elements.append(dict(id=f'g:{subject.group}:subject_{index}', type='image', asset_id=asset,
            fit='contain', edge_fade_right=subject.edge_fade_right, **common(subject, f'subjects[{index}]')))
    for index, decoration in enumerate(assembly.decorations):
        node = dict(id=f'g:{decoration.group}:decoration_{index}', type=decoration.type,
                    stroke=decoration.stroke, stroke_width=decoration.stroke_width,
                    opacity=decoration.opacity, **common(decoration, f'decorations[{index}]'))
        if decoration.type in ('line', 'polyline'):
            _, _, w, h = node['box']
            node['points'] = [[round(x*w, 3), round(y*h, 3)] for x, y in decoration.points]
            if len({tuple(point) for point in node['points']}) < 2:
                raise ValueError(f'decorations[{index}] id={node["id"]} points 在 {w}x{h} 像素框中缩成零长度；'
                                 '仅修正本装饰的 box/points，不能改动其他素材')
        else:
            node['fill'] = decoration.fill
            if decoration.type == 'rect': node['radius'] = decoration.radius
        elements.append(node)
    for index, label in enumerate(assembly.labels):
        label_id = f'g:{label.group}:text_{index}'
        base = common(label, f'labels[{index}] id={label_id}')
        if label.plate:
            style = label.plate if isinstance(label.plate, Plate) else Plate(color=label.plate, stroke_width=0)
            plate = dict(id=f'g:{label.group}:plate_{index}', type='rect', fill=style.color,
                stroke_width=style.stroke_width, radius=style.radius, opacity=style.opacity, **base)
            if style.box: plate['box'] = _box(style.box, canvas, f'labels[{index}].plate.box group={label.group}')
            if style.stroke: plate['stroke'] = style.stroke
            if style.glow: plate['glow'] = style.glow
            if style.gradient_to: plate['gradient'] = dict(color=style.gradient_to, angle=style.gradient_angle)
            elements.append(plate)
        if label.text.strip():
            minimum_font = min(label.font_size, max(16, round(label.font_size*.65)))
            # This is a deterministic impossibility check, not a speculative
            # glyph-width estimate. Browser measurement still owns line wraps.
            if base['box'][3] + 2 < minimum_font * label.line_height:
                raise ValueError(f'labels[{index}] id={label_id} group={label.group} text={json.dumps(label.text, ensure_ascii=False)}：'
                    f'文字框 {base["box"][2]}x{base["box"][3]}px 高度不足最低一行 '
                    f'{minimum_font * label.line_height:.1f}px（font_size={label.font_size:g}，最低字号={minimum_font:g}，line_height={label.line_height:g}）；'
                    '只调整此标签的 box/字号/换行，保留文字和其他素材/坐标；若是纯装饰箭头请改为 decoration polyline')
            node = dict(id=label_id, type='text', text=label.text,
                fill=label.color, font_size=label.font_size, min_font_size=minimum_font,
                bold=label.bold, align=label.align, valign=label.valign, line_height=label.line_height, **base)
            if label.gradient_to: node['gradient'] = dict(color=label.gradient_to, angle=180)
            elements.append(node)
    if len(elements) > MAX_LAYERS: raise ValueError(f'图层超过本地渲染上限 {MAX_LAYERS}；未截断或省略内容')
    return dict(version=VERSION, canvas=canvas, background=assembly.background_color, elements=elements)


def _normalize(text):
    value = unicodedata.normalize('NFKC', text)
    # Ignore layout separators, but preserve decimal points, ratios, signs,
    # inequalities and percentage symbols because they carry factual meaning.
    return re.sub(r'[\s，,。；;！？!?（）()【】\[\]“”"「」『』、|｜]+', '', value).replace('∶', ':')


def _source_text_box(value):
    """A source text ROI, never a rendered box or a subject's broad crop."""
    if (not isinstance(value, (tuple, list)) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in value)
            or value[0] >= value[2] or value[1] >= value[3]):
        return None
    return list(value)


def _source_boxes_overlap(left, right):
    # Shared borders are valid for adjacent occurrences. Tiny floating-point
    # noise is not evidence that two source regions contain the same glyph.
    return (min(left[2], right[2])-max(left[0], right[0]) > 1e-9
            and min(left[3], right[3])-max(left[1], right[1]) > 1e-9)


class RecoveredTextConflict(ValueError):
    """An auditable source-claim conflict; not a global ban on repeated text.

    Pydantic retains this exception in a ValidationError's ctx.error. Callers
    can use ``conflict`` to request targeted source localization without
    parsing a localized message or guessing which two declarations failed.
    """
    def __init__(self, message, *, conflict):
        super().__init__(message)
        self.conflict = conflict


def _recovery_conflict(kind, index, item, box, entry, original_box):
    conflict = dict(kind=kind,
        submitted=dict(path=f'recovered_text[{index}]', text=item.text, source_box=box),
        existing=dict(path=entry['path'], text=entry.get('text', entry['normalized']),
            source_box=original_box),
        text_relation='same_text' if _normalize(item.text) == entry['normalized'] else 'substring',
        coordinate_space='core_normalized_ltrb')
    if kind == 'source_occurrence_overlap':
        overlap = [max(box[0], original_box[0]), max(box[1], original_box[1]),
            min(box[2], original_box[2]), min(box[3], original_box[3])]
        area = (overlap[2]-overlap[0])*(overlap[3]-overlap[1])
        conflict['overlap'] = dict(source_box=overlap,
            submitted_fraction=area/((box[2]-box[0])*(box[3]-box[1])),
            existing_fraction=area/((original_box[2]-original_box[0])*(original_box[3]-original_box[1])))
    return conflict


def _validate_recovered_occurrences(recovered, existing_entries=()):
    """Validate occurrence declarations, not global substring membership.

    An axis '0' is not the same occurrence as the zero inside '78.0%'. Repeated
    text at separate source positions remains possible, but declarations are
    still counted exactly once and must be visually verified against core.
    """
    entries = list(existing_entries)
    for index, item in enumerate(recovered):
        value = _normalize(item.text)
        box = _source_text_box(item.source_box)
        for entry in entries:
            previous = entry['normalized']
            if not previous or not (value in previous or previous in value):
                continue
            original_box = _source_text_box(entry.get('source_box'))
            if original_box is not None and _source_boxes_overlap(box, original_box):
                conflict = _recovery_conflict('source_occurrence_overlap', index, item, box, entry, original_box)
                relation = '完整原文相同' if conflict['text_relation'] == 'same_text' else '一项是另一项的文字片段'
                raise RecoveredTextConflict(f'recovered_text[{index}] text={json.dumps(item.text, ensure_ascii=False)} '
                    f'与 {entry["path"]} text={json.dumps(conflict["existing"]["text"], ensure_ascii=False)} '
                    f'的同一原文位置重复或片段重叠（{relation}）：'
                    f'补录 source_box={box}，已登记 source_box={original_box}。'
                    '此冲突仅针对上述两项申报及其重叠原图区域，不表示不同坐标轴或不同位置不能出现相同文字。'
                    '该处已登记则直接展示原清单内容，不重复补录；若是另一坐标轴/另一位置的同字，'
                    '请定位并填写各自独立的原图文字框，OCR漏检时检查局部裁区，不要复制邻近文字框。'
                    '不要删除原图真实刻度、改写数字或用SVG绕过文字核查。', conflict=conflict)
            if value == previous and original_box is None:
                conflict = _recovery_conflict('unlocalized_same_text', index, item, box, entry, original_box)
                raise RecoveredTextConflict(f'recovered_text[{index}] text={json.dumps(item.text, ensure_ascii=False)} '
                    f'与 {entry["path"]} 已登记的完整原文相同，但旧项没有可核实的原图文字框，暂不能判定为另一处。'
                    '请用原图定位工具核实并修订清单；素材 embedded_text 的主体大裁区不等于文字位置。'
                    '此疑点仅针对上述两项申报，不表示不同位置禁止同字。'
                    '同一处沿用原登记；确有不同位置的同字则逐处登记，不要删除真实刻度或改写原文。', conflict=conflict)
        entries.append(dict(path=f'recovered_text[{index}]', text=item.text, normalized=value, source_box=box))


def _text_entries(value, prefix=''):
    labels = value.labels if hasattr(value, 'labels') else value.get('labels', [])
    subjects = value.subjects if hasattr(value, 'subjects') else value.get('subjects', [])
    def field(row, name, default=None):
        return getattr(row, name, default) if not isinstance(row, dict) else row.get(name, default)
    result = []
    for index, row in enumerate(labels):
        text, group = field(row, 'text', ''), field(row, 'group')
        if not text.strip(): continue
        source_box = _source_text_box(field(row, 'source_box', field(row, 'box'))) if prefix == 'inventory.' else None
        result.append(dict(path=f'{prefix}labels[{index}]', index=index,
            id=field(row, 'id') or (f'g:{group}:text_{index}' if group else None),
            group=group, text=text, normalized=_normalize(text), source_box=source_box))
    for index, row in enumerate(subjects):
        group = field(row, 'group')
        for text_index, text in enumerate(field(row, 'embedded_text', [])):
            if not text.strip(): continue
            result.append(dict(path=f'{prefix}subjects[{index}].embedded_text[{text_index}]', index=index,
                id=f'g:{group}:subject_{index}' if group else None, group=group,
                text=text, normalized=_normalize(text)))
    return result


def _texts(value):
    return [row['normalized'] for row in _text_entries(value)]


def _public_entry(row):
    return {key: value for key, value in row.items() if key != 'normalized' and value is not None}


def _related_entries(rows, characters):
    relevant = [row for row in rows if any(char in row['normalized'] for char in characters)]
    # Pure wrong-symbol labels are the highest signal; do not bury them under
    # unrelated long paragraphs sharing a common character.
    relevant.sort(key=lambda row: sum(row['normalized'].count(c) for c in characters) / max(1, len(row['normalized'])), reverse=True)
    return [_public_entry(row) for row in relevant[:8]]


def _missing_label_candidates(expected_entries, actual_entries, missing):
    candidates = {}
    for source in expected_entries:
        if not any(char in source['normalized'] for char in missing): continue
        best, best_score = None, .49
        expected = Counter(source['normalized'])
        for row in actual_entries:
            if not row['path'].startswith('labels[') or row['normalized'] == source['normalized']: continue
            overlap = sum((expected & Counter(row['normalized'])).values())
            score = 2*overlap/max(1, len(source['normalized'])+len(row['normalized']))
            if score > best_score:
                best, best_score = row, score
        if best:
            candidates[best['path']] = {**_public_entry(best), 'expected_source':source['path'], 'expected_text':source['text']}
    return list(candidates.values())[:6]


def _character_error(expected_entries, actual_entries, *, recovered=False):
    expected = Counter(''.join(row['normalized'] for row in expected_entries))
    actual = Counter(''.join(row['normalized'] for row in actual_entries))
    missing, surplus = expected - actual, actual - expected
    added = {char: count for char, count in surplus.items() if not expected[char]}
    repeated = {char: count for char, count in surplus.items() if expected[char]}
    def counts(delta):
        return [dict(char=char, expected=expected[char], actual=actual[char], delta=count)
                for char, count in list(delta.items())[:32]]
    detail = {'新增字符': counts(added), '缺少字符': counts(missing), '重复字符': counts(repeated),
              '相关现有图层': _related_entries(actual_entries, surplus),
              '缺失原文来源': _related_entries(expected_entries, missing),
              '可能缺字标签': _missing_label_candidates(expected_entries, actual_entries, missing)}
    guidance = ('仅修复上述关联文字字段；保留已正确主体的 asset_id/prepared_variant/prepared_crop/box、背景和其他标签坐标，'
                '不改写事实、不删主体、不把其他文字挪进 embedded_text 来凑数。')
    if surplus:
        guidance += ('若新增符号只是装饰箭头/双箭头/分隔线，应从 labels 移除并用 decorations 的 polyline/line 路径复原；'
                     '装饰符号不作为文字无条件豁免。真实原文仅在 core 确有且清单漏识别时登记 recovered_text 和 source_box。')
    if recovered:
        guidance += '补录声明必须恰好对应实际新增展示的原文，不能虚报或漏报。'
    return '复原文字守恒校验失败：'+json.dumps(detail, ensure_ascii=False, separators=(',', ':'))+'；'+guidance


_NUMERIC = r'[<>≤≥]?[+-]?\d+(?:\.\d+)*(?::\d+)?(?:%|‰)?'


def _number_error(expected_entries, actual_entries):
    expected = Counter(token for row in expected_entries for token in re.findall(_NUMERIC, row['normalized']))
    actual = Counter(token for row in actual_entries for token in re.findall(_NUMERIC, row['normalized']))
    missing, changed = expected - actual, actual - expected
    related = [_public_entry(row) for row in actual_entries
               if any(token in changed for token in re.findall(_NUMERIC, row['normalized']))][:8]
    sources = [_public_entry(row) for row in expected_entries
               if any(token in missing for token in re.findall(_NUMERIC, row['normalized']))][:8]
    detail = {'缺少原数值': dict(missing), '新增或改变数值': dict(changed),
              '相关现有图层': related, '原数值来源': sources}
    return ('数值、比例或单位发生改变：'+json.dumps(detail, ensure_ascii=False, separators=(',', ':'))+
            '；仅修复关联标签的数值文本，保留已正确主体素材/裁切/位置；数字与单位可使用同组相邻标签和不同字号，'
            '不得改变数字顺序、符号或单位，也不能重排主体来规避文字校验。')


def _validate_prepared_crop_space(assembly, inventory):
    originals = inventory.subjects if hasattr(inventory, 'subjects') else inventory.get('subjects', [])
    def same(left, right):
        return len(left) == len(right) == 4 and all(abs(float(a)-float(b)) <= 1e-6 for a, b in zip(left, right))
    whole = (0, 0, 1, 1)
    for index, subject in enumerate(assembly.subjects):
        source_index = int(subject.asset_id.removeprefix('subject_'))
        if source_index >= len(originals): continue
        original = originals[source_index]
        source_crop = original.crop if hasattr(original, 'crop') else original.get('crop')
        if source_crop is None or same(source_crop, whole) or same(subject.prepared_crop, whole): continue
        if same(subject.prepared_crop, source_crop):
            raise ValueError(f'坐标空间混用：subjects[{index}] asset_id={subject.asset_id} '
                f'prepared_crop={list(subject.prepared_crop)} 复制了 inventory.subjects[{source_index}].crop={list(source_crop)}。'
                '后者是 core/参考原图上的裁取位置；subject_N 已经完成该裁取，不能再次使用原图坐标裁它。'
                '默认保留当前实际素材整张 prepared_crop=[0,0,1,1]；只有亲眼确认当前素材边缘污染才用该素材自身坐标小幅修边。'
                '仅修正该 prepared_crop，保留已正确 asset_id/prepared_variant/box 及文字/装饰；不自动换算或猜测坐标。')


def validate_content(assembly: Assembly, inventory):
    _validate_prepared_crop_space(assembly, inventory)
    expected_entries = _text_entries(inventory, 'inventory.')
    actual_entries = _text_entries(assembly)
    expected, actual = [row['normalized'] for row in expected_entries], [row['normalized'] for row in actual_entries]
    recovered = [_normalize(item.text) for item in assembly.recovered_text]
    _validate_recovered_occurrences(assembly.recovered_text, expected_entries)
    # A recovery is an explicit, image-local claim for the visual reviewer to
    # verify, not a license to drop/rewrite inventory text or hide declarations.
    # Only actually additional visible characters can be claimed as recovered.
    expected_entries += [dict(path=f'recovered_text[{index}]', index=index, text=row.text,
                             source_box=list(row.source_box), normalized=recovered[index])
                         for index, row in enumerate(assembly.recovered_text)]
    expected += recovered
    if Counter(''.join(expected)) != Counter(''.join(actual)):
        raise ValueError(_character_error(expected_entries, actual_entries, recovered=bool(recovered)))
    if Counter(re.findall(_NUMERIC, ''.join(expected))) != Counter(re.findall(_NUMERIC, ''.join(actual))):
        # Joining separate labels can concatenate unrelated numbers. Compare the
        # individual numerical tokens too, accepting an explicit split/rejoin.
        expected_numbers = Counter(token for text in expected for token in re.findall(_NUMERIC, text))
        actual_numbers = Counter(token for text in actual for token in re.findall(_NUMERIC, text))
        if expected_numbers != actual_numbers:
            raise ValueError(_number_error(expected_entries, actual_entries))
    expected_subjects = len(inventory.subjects if hasattr(inventory, 'subjects') else inventory.get('subjects', []))
    if {subject.asset_id for subject in assembly.subjects} != {f'subject_{i}' for i in range(expected_subjects)}:
        raise ValueError('必须保留清单中的全部主体且不能凭空增加主体')
    return True


def static_plan(plan):
    result = copy.deepcopy(plan)
    for element in result['elements']:
        element.update(at=0, enter=0, exit=0, motion='none')
    return result


def plan_groups(plan):
    result = {}
    for element in plan['elements']:
        if element.get('role') == 'background': continue
        match = re.fullmatch(r'g:([a-z][a-z0-9_]{0,23}):[a-z0-9_]+', element['id'])
        if not match: raise ValueError('内容图层缺少有效信息组')
        result.setdefault(match[1], []).append(element['id'])
    return result


def apply_motion(plan, schedule: MotionSchedule, context):
    _, _, duration = _dimensions(context)
    expected = plan_groups(plan)
    motions = {group.id: group for group in schedule.groups}
    if set(motions) != set(expected):
        raise ValueError('动效必须恰好覆盖全部信息组，不能遗漏或新增组')
    for group in schedule.groups:
        # Every group must be fully visible long enough to read before exit.
        if group.at + group.enter + group.exit + min(1.2, duration*.2) > duration:
            raise ValueError(f'信息组 {group.id} 入场过晚，未留足阅读时间')
    if duration >= 4 and len(motions) >= 2:
        times = sorted(group.at for group in schedule.groups)
        if times[-1] - times[0] < min(1.2, duration*.15):
            raise ValueError('多信息组不能在开头同时出现；请按字幕至少分两阶段呈现')
    result = copy.deepcopy(plan)
    for element in result['elements']:
        if element.get('role') == 'background': continue
        group = motions[element['id'].split(':')[1]]
        motion = group.motion
        if motion == 'draw' and element['type'] not in ('line', 'polyline'):
            # A group can include a line and its caption. Caption fades in while
            # the line draws; never send an unsupported text draw to Remotion.
            motion = 'fade'
        element.update(at=group.at, enter=group.enter, exit=group.exit, end=duration, motion=motion)
    return result


def validate_motion(assembly: Assembly, context):
    groups = {}
    for layer in [*assembly.subjects, *assembly.decorations, *assembly.labels]:
        current = (layer.at, layer.enter, layer.motion, layer.exit)
        if layer.group in groups and groups[layer.group] != current:
            raise ValueError('同一信息组需使用一致的入场时序；底板和文字不能脱节')
        groups[layer.group] = current
    schedule = MotionSchedule(groups=[dict(id=key, at=value[0], enter=value[1], motion=value[2], exit=value[3]) for key, value in groups.items()])
    # Use a minimal group-only plan: this checks timing, not raster assets.
    apply_motion({'elements':[dict(id=f'g:{key}:check', type='rect') for key in groups]}, schedule, context)
    return True


ASSEMBLY_DIRECTOR = """你是海报复原排版师。只返回符合 schema 的 JSON，不能生成代码或请求新模型。
这是静态复原阶段，尚不编排动画。你会看到 core 原图、已完成的干净背景和真实透明/保真主体素材。
依据实际素材和 source_geometry 中程序核实的几何信息复原，不从头猜测已有的源坐标。尽量恢复原图的比例、位置、层叠、配色、字号、渐变、文本框和留白。
source_geometry 明确区分完整源图取材坐标、已准备裁片局部坐标和原海报目标锚点：三者不可互换。
若主体 source_id=core 且 anchor_available=true，首选placement="source_anchor"并省略box；程序将源像素位置与prepared_crop修边后的像素位置映射回画布，保持主体原尺寸和相对位置。不要把裁片缩小塞进另一张卡片，不要把透明边界误当主体宽度。
独立参考图的source_crop不代表它在core里的位置；其anchor_available=false时用placement="manual"，对照core决定box，并根据实际透明边缘保持可见主体的大小；不能复制参考图坐标充当海报位置。
inventory.labels.source_box和inventory.decorations.source_box是原图定位参考，不是强制字框：保留原位置/层次，实际字框可按字号与换行微调。分色标题与数字单位应形成紧邻、同基线的视觉整体，不留无故的大空隙，也不互相盖字。
subjects.asset_id 只能使用已给定的 subject_N。它已从core/参考原图裁出并准备好，不是原海报；prepared_crop默认整张[0,0,1,1]，不裁是首选。
只有在当前展示的subject_N素材自身边缘确实看到污染/旧文字时，才用prepared_crop作小幅局部修边；坐标归一化到这一张实际素材自身。严禁复制初次inventory.source crop/box或原海报位置坐标来再次裁切；不能因为人物位于原图左侧就把当前人物素材再裁左侧。manual时最终位置用box，source_anchor时由程序解析源映射；绝不拿box替prepared_crop。
prepared_variant默认cutout，仅当看到抠图结果破损、而提供的subject_N_original能保真还原时选original；asset_id仍是subject_N，不把_original写成新身份，不请求文件路径。两种素材保持相同像素边界，prepared_crop坐标含义相同；不能为省事把所有主体都切成original。原始裁片的拼接边缘和夹带旧字仍须复核。
使用original时仔细检查裁片外围是否包含多余背景装饰、水面、其他文字；可在prepared_crop中只修掉无关边缘，不能切掉主体和必要底座。core原裁片优先source_anchor原位复原，裁切后的尺寸位置由程序同步换算，不手工再次缩放。照片/证据图保留原像素，不能为了去边框损坏内容。
embedded_text 要如实列出最终裁片里仍保留的可见文字；代码 labels 不得重复这些文字。证据图和照片不得重画或为了抠图毁损。
不要遗漏或新增清单中的文字、主体、数字和单位。数字与单位既可组合一层，也允许且鼓励按core原图的字号层次拆成同组相邻标签并使用不同字号：醒目数字保持大字号（最高200），单位/说明保持较小字号；按原图基线和间距排版，不重叠、不改变任何数字或单位。标题中不同颜色的词组也可拆成同组邻接标签，总字必须守恒；不要为避免校验把所有字合成同字号的一行。
初次inventory可能漏识别原图标题等文字：仅当core原图确实存在时，逐字补回labels或embedded_text，并在recovered_text登记同一原文及其在core上的归一化source_box。不得凭常识扩写、总结或杜撰数字，不能补录清单已有文字，不能用补录删除/替换已有原文。通常recovered_text=[]；所有补录必须接受后续core视觉逐项复核。
box 是最终画布归一化[左,上,右,下]，严格左<右且上<下。字号单位为输出canvas像素而非原图像素。
labels 保留独立 plate 的渐变/边框/圆角，也可让一个plate和多行文字共用 group。空文本必须有plate。
简单标注线/箭头/分隔线用decorations重建；line/polyline必须提供points，line恰好2点，polyline至少2个不同点；points归一化到该装饰box内部，不是整个画布。不能只写box省略points；原图没有的装饰不要凭空添加。
纯装饰箭头/双箭头不使用文字标签中的«、»、←、→代替，而是用polyline折线重建；这些符号若确属原文则须与清单或经核实的recovered_text一致，不能因为看起来像装饰就豁免文字守恒。
group 用简短英文语义ID（如title、comparison、evidence、conclusion），同一图文组合/底框和文字同组。不得把全海报塞进一组。
所有at暂填0，不需要假装动画。层次顺序为背景、subjects、decorations、labels；labels中底板在对应文字之前。"""

REPAIR_DIRECTOR = ASSEMBLY_DIRECTOR + """
这是唯一一轮视觉返工。对照core与复原截图以及具体问题清单，仅修复存在问题的裁切、比例、位置、文字样式和装饰；保留已正确的内容。
若校验给出具体labels索引/id/group/text及新增/缺少/重复字符，只修改关联文字或装饰字段，保留未被指出有问题的主体asset_id/prepared_variant/prepared_crop/box和其他坐标；不要因局部符号或数字错误重新裁切/移动整幅海报主体。
必须返回完整 Assembly JSON，不返回补丁或解释。不能删除难处理的主体或文字来通过校验，不能新增图片调用。"""

MOTION_DIRECTOR = """你是分层海报动效编排师。静态复原已审核，布局与素材已经冻结。只返回符合 MotionSchedule schema 的 JSON。
为已有信息组安排入场/强调顺序及收尾，不得新增组、漏组或改任何文字、位置、尺寸、颜色或素材。
以最终配音的字幕时间戳为依据。标题可先出现，随后主体/比较组/关键数值按讲述顺序出现，完整信息留足阅读时间。
不要把所有图层都安排在0秒后半秒内同时出现。>=4秒且有多个信息组时至少分两阶段，每组内部的文字、主体与底框同步。
enter一般0.5至0.9秒，避免急促闪现；motion可fade/slide_left/slide_right/slide_up/scale/grow，draw仅用于标注线组（同组文字会淡入）。
exit建议0.3秒，保留背景，不整体淡成黑屏；最后一个信息组入场后至少留出约1.2秒阅读，再开始退出。
groups必须覆盖给出的全部group id且恰好一次。at是本镜绝对秒，不能使用全文时间；参考字幕内容而非平均分配。"""
