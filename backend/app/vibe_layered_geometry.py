"""Server-owned source anchors for prepared layered-poster materials.

Three coordinate spaces are kept separate: the EXIF-oriented source image,
the actual (already cropped) prepared pixels, and the normalized core canvas.
No model inference, image mutation, arbitrary paths, or automatic recropping.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

from PIL import Image, ImageOps


VERSION = 'source-anchors-v1'


def _value(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _region(raw, label):
    if not isinstance(raw, (list, tuple)) or len(raw) != 4 or any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or not 0 <= value <= 1 for value in raw
    ):
        raise ValueError(label+' 必须为 0 到 1 的四个有限归一化坐标')
    left, top, right, bottom = raw
    if left >= right or top >= bottom:
        raise ValueError(label+' 为空或坐标倒置；须为 [左,上,右,下]')
    return list(raw)


def _size(path, label, *, exif=False):
    if path is None or not Path(path).is_file():
        raise ValueError(label+' 文件缺失，不能确定坐标映射')
    with Image.open(path) as image:
        if image.width*image.height > 40_000_000 or getattr(image, 'n_frames', 1) != 1:
            raise ValueError(label+' 不是有效的单帧图片或尺寸过大')
        # Sources follow crop_asset; prepared PNGs follow materialize_subjects.
        oriented = ImageOps.exif_transpose(image) if exif else image
        return list(oriented.size)


def _canvas_dimensions(canvas):
    if canvas is None:
        return None
    if not isinstance(canvas, (list, tuple)) or len(canvas) != 2 or any(
        type(value) is not int or value <= 0 for value in canvas
    ):
        raise ValueError('canvas 必须为两个正整数像素尺寸')
    return list(canvas)


def _aspect_error(core_size, canvas):
    if canvas is None:
        return None
    source_width, source_height = core_size
    width, height = canvas
    # Match the source long edge, then allow at most one output pixel of
    # rounding on its short edge. Never invent a cover/pan/letterbox transform.
    delta = abs(height-source_height*width/source_width) if source_width >= source_height else abs(width-source_width*height/source_height)
    if delta > 1+1e-9:
        return (f'core {source_width}x{source_height} 与输出 canvas {width}x{height} 宽高比不一致 '
                f'(等比缩放后的短边偏差 {delta:.3f} 像素，超过 1 像素舍入容差)；'
                '不能推断独立背景的 cover/裁切变换，请使用 manual 对照画布定位，禁止按 fill 拉伸。')
    return None


def geometry_manifest(inventory, sources, assets, canvas=None):
    """Expose measured anchors without confusing a reference crop with core.

The inventory may be a legacy Design or the dedicated V2 Inventory. A source
retake must rebuild this manifest from the updated crop and actual material.
Size-mismatched old material is explicitly unanchorable rather than guessed;
manual placement remains available to callers with that legacy material.
"""
    core_size = _size(sources.get('core'), 'core 原海报', exif=True)
    canvas = _canvas_dimensions(canvas)
    aspect_error = _aspect_error(core_size, canvas)
    sizes = {'core': core_size}
    rows = []
    for index, subject in enumerate(_value(inventory, 'subjects', [])):
        identity = f'subject_{index}'
        source_id = _value(subject, 'source')
        if source_id not in sources:
            raise ValueError(f'{identity} 的 source={source_id} 未绑定本镜原图')
        if source_id not in sizes:
            sizes[source_id] = _size(sources[source_id], source_id+' 原图', exif=True)
        source_size = sizes[source_id]
        crop = _region(_value(subject, 'source_crop', _value(subject, 'crop')),
                       identity+'.source_crop')
        width, height = source_size
        pixel = [math.floor(crop[0]*width), math.floor(crop[1]*height),
                 math.ceil(crop[2]*width), math.ceil(crop[3]*height)]
        expected = [pixel[2]-pixel[0], pixel[3]-pixel[1]]
        prepared = _size(assets.get(identity), identity+' 已准备素材')
        original_id = identity+'_original'
        variants = {'cutout': list(prepared)}
        if original_id in assets:
            original_size = _size(assets[original_id], original_id+' 原始裁片')
            if original_size != prepared:
                raise ValueError(f'{identity} original/cutout 像素边界不一致 '
                                 f'({original_size}/{prepared})；不能共享 prepared_crop')
            variants['original'] = original_size
        reason = None
        if source_id != 'core':
            reason = '素材来自独立参考图；source_crop 仅定位该参考图，不能当作 core 海报位置。请使用 manual 并依据 core 填写 box。'
        elif prepared != expected:
            reason = (f'源裁切像素窗口 {pixel} 应为 {expected[0]}x{expected[1]}，'
                      f'实际 prepared 为 {prepared[0]}x{prepared[1]}；无法证明未缩放/自动裁边，不能猜测原位映射。')
        elif aspect_error:
            reason = aspect_error
        rows.append(dict(asset_id=identity, source_id=source_id, source_size=list(source_size),
            source_crop=crop, source_pixel_window=pixel, expected_prepared_size=expected,
            prepared_size=prepared, prepared_variant_sizes=variants,
            anchor_available=reason is None, anchor_unavailable_reason=reason,
            source_anchor_box=[pixel[0]/width, pixel[1]/height, pixel[2]/width, pixel[3]/height]
                if reason is None else None,
            coordinate_space={
                'source_crop':'normalized bounds in EXIF-oriented source_id image, not prepared pixels',
                'source_pixel_window':'[left,top,right,bottom], floor/ceil in EXIF-oriented source image',
                'prepared_crop':'normalized bounds in the whole supplied prepared material, rounded to actual pixels',
                'source_anchor_box':'normalized core canvas bounds of the full prepared material; not a crop request',
                'canvas':'output pixels; source anchors require the core aspect ratio within one pixel rounding',
            }))
    return {'version': VERSION, 'core_size': core_size, 'canvas': canvas, 'subjects': rows}


def resolve_source_anchors(assembly, geometry):
    """Return a deep copy with source_anchor boxes calculated by the server.

Source-pixel anchors stay fixed when prepared_crop trims an edge. The trimmed
piece is NOT stretched back into the full source box. Manual and legacy
subjects are left untouched. The supplied manifest is trusted server data,
never a model-defined replacement for the local sources or their dimensions.
"""
    result = copy.deepcopy(assembly)
    subjects = _value(result, 'subjects', [])
    anchored = [subject for subject in subjects if _value(subject, 'placement', 'manual') == 'source_anchor']
    if not anchored:
        return result
    if not isinstance(geometry, dict) or geometry.get('version') != VERSION:
        raise ValueError('source_anchor 缺少当前版本的实际素材几何清单；请重新准备定位信息')
    aspect_error = _aspect_error(geometry['core_size'], _canvas_dimensions(geometry.get('canvas')))
    if aspect_error:
        raise ValueError('source_anchor 无可信原图锚点：'+aspect_error)
    rows = geometry.get('subjects', [])
    mapping = {row['asset_id']: row for row in rows}
    if len(mapping) != len(rows):
        raise ValueError('素材几何清单存在重复 asset_id，不能确定 source_anchor')
    for subject in anchored:
        identity = _value(subject, 'asset_id')
        row = mapping.get(identity)
        if not row or row.get('anchor_available') is not True or row.get('source_id') != 'core':
            reason = row.get('anchor_unavailable_reason') if row else '几何清单中没有该主体'
            raise ValueError(f'{identity}.placement=source_anchor 无可信原图锚点：{reason or "定位信息不完整"}')
        prepared_width, prepared_height = row['prepared_size']
        variant = _value(subject, 'prepared_variant', 'cutout')
        if row.get('prepared_variant_sizes', {}).get(variant) != row['prepared_size']:
            raise ValueError(f'{identity} 的 {variant} 备选未验证为同一像素边界，不能使用 source_anchor')
        left, top, right, bottom = row['source_pixel_window']
        source_width, source_height = row['source_size']
        if [right-left, bottom-top] != [prepared_width, prepared_height] or row['source_size'] != geometry['core_size']:
            raise ValueError(f'{identity} 的源像素、prepared 尺寸与 core 不一致，不能使用 source_anchor')
        crop = _region(_value(subject, 'prepared_crop', [0,0,1,1]), identity+'.prepared_crop')
        # This is deliberately round(), exactly as materialize_subjects/_box.
        x1, y1, x2, y2 = [round(value*size) for value, size in zip(
            crop, [prepared_width, prepared_height, prepared_width, prepared_height])]
        if x1 >= x2 or y1 >= y2:
            raise ValueError(f'{identity}.prepared_crop 取整后为空，至少需要一个实际像素')
        target = ((left+x1)/source_width, (top+y1)/source_height,
                  (left+x2)/source_width, (top+y2)/source_height)
        if isinstance(subject, dict):
            subject['box'] = target
        else:
            subject.box = target
    return result
