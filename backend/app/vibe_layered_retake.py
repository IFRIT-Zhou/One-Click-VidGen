"""One local source retake during visual repair; no new image/model requests."""
import math
from pathlib import Path

from PIL import Image, ImageOps
from pydantic import Field, model_validator

from . import vibe_cloud as api
from . import vibe_rmbg as rmbg
from .vibe_layered import Box, Strict, bounds


class SourceRetake(Strict):
    asset_id: str = Field(pattern=r'^subject_[0-7]$')
    source_crop: Box = Field(description='冻结 source_id 对应完整原图上的归一化 [左,上,右,下]；不是当前裁片坐标，不是宽高。必须包含同一主体的完整边缘。')
    reason: str = Field(min_length=1, max_length=600)

    @model_validator(mode='before')
    @classmethod
    def reject_boolean_coordinates(cls, value):
        if isinstance(value, dict) and isinstance(value.get('source_crop'), (list, tuple)):
            if any(isinstance(item, bool) for item in value['source_crop']):
                raise ValueError('source_crop 必须为数值坐标，不能使用布尔值')
        return value

    @model_validator(mode='after')
    def valid_region(self):
        bounds(self.source_crop, self.asset_id+'.source_crop')
        if not self.reason.strip():
            raise ValueError('重新取材必须说明原始裁片截断的位置')
        return self


def _check(cancelled):
    if cancelled and cancelled():
        raise InterruptedError('已停止本镜的本地重新取材')


def _source_image(source_id, sources):
    if source_id not in sources or not Path(sources[source_id]).is_file():
        raise ValueError(f'重新取材的原始素材 {source_id} 缺失；只能使用本镜已绑定的源图')
    with Image.open(sources[source_id]) as image:
        if image.width*image.height > 40_000_000 or getattr(image, 'n_frames', 1) != 1:
            raise ValueError(f'重新取材的原始素材 {source_id} 不是有效的单帧图片或尺寸过大')
        return ImageOps.exif_transpose(image).convert('RGBA')


def _pixel_box(crop, size):
    width, height = size
    return (math.floor(crop[0]*width), math.floor(crop[1]*height),
            math.ceil(crop[2]*width), math.ceil(crop[3]*height))


def _requests(requests, inventory):
    rows = [row if isinstance(row, SourceRetake) else SourceRetake.model_validate(row) for row in requests]
    if len(rows) > len(inventory.subjects) or len(rows) > 8:
        raise ValueError('重新取材数量不能超过已有主体数量')
    ids = [row.asset_id for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('同一主体只能请求一次重新取材，不能重复 asset_id')
    for row in rows:
        if int(row.asset_id.split('_')[1]) >= len(inventory.subjects):
            raise ValueError(f'{row.asset_id} 不是已有主体；重新取材不能新增或替换素材身份')
    return rows


def source_manifest(inventory, sources):
    """Read-only identity map; coordinates explicitly belong to full sources."""
    result = []
    sizes = {}
    for index, subject in enumerate(inventory.subjects):
        if subject.source not in sizes:
            sizes[subject.source] = list(_source_image(subject.source, sources).size)
        result.append(dict(asset_id=f'subject_{index}', source_id=subject.source,
            source_image_id='core' if subject.source == 'core' else 'retake_source_'+subject.source,
            current_source_crop=list(subject.crop), treatment=subject.treatment,
            source_size=sizes[subject.source]))
    return result


def validate_retakes(requests, inventory, sources):
    """Validate before the bounded review-format repair, without any writes."""
    for request in _requests(requests, inventory):
        subject = inventory.subjects[int(request.asset_id.split('_')[1])]
        if all(abs(a-b) <= 1e-9 for a, b in zip(request.source_crop, subject.crop)):
            raise ValueError(f'{request.asset_id}.source_crop 未改变，不能重复裁取同一区域')
        image = _source_image(subject.source, sources)
        pixel = _pixel_box(request.source_crop, image.size)
        if pixel == _pixel_box(subject.crop, image.size):
            raise ValueError(f'{request.asset_id}.source_crop 对应像素范围未改变')
        if subject.source == 'core' and subject.treatment == 'preserve':
            x, y, right, bottom = request.source_crop
            if (right-x)*(bottom-y) > .94:
                raise ValueError(f'{request.asset_id} 不能以整张 core 海报作保真前景')
        if image.crop(pixel).getchannel('A').getbbox() is None:
            raise ValueError(f'{request.asset_id}.source_crop 裁取范围完全透明')


def apply_retakes(requests, inventory, sources, assets, folder, cancelled=None):
    """Re-crop immutable bound sources, preserving text and all other settings."""
    _check(cancelled)
    validate_retakes(requests, inventory, sources)
    rows = _requests(requests, inventory)
    if not rows:
        return inventory, dict(assets), []
    folder = Path(folder)
    # Refuse a reused destination before preparing any member of the batch.
    for request in rows:
        for name in (request.asset_id+'-original.png', request.asset_id+'-rgba.png'):
            if (folder/name).exists():
                raise FileExistsError('不会覆盖已有重新取材素材：'+name)
    _check(cancelled)
    folder.mkdir(parents=True, exist_ok=True)
    updated_assets = dict(assets)
    subjects = list(inventory.subjects)
    pending = {}
    audit = []
    for request in rows:
        _check(cancelled)
        index = int(request.asset_id.split('_')[1])
        subject = inventory.subjects[index]
        cropped = api.crop_asset(sources, subject.source, request.source_crop, folder, request.asset_id+'-original')
        with Image.open(cropped) as image:
            rgba = image.convert('RGBA')
            low, high = rgba.getchannel('A').getextrema()
            size = list(rgba.size)
        if high == 0:
            raise ValueError(f'{request.asset_id} 重新裁取后完全透明，未采用')
        updated_assets[request.asset_id+'_original'] = cropped
        updated_assets[request.asset_id] = cropped
        if subject.treatment == 'cutout' and low == 255:
            pending[request.asset_id] = cropped
        subjects[index] = subject.model_copy(update={'crop': request.source_crop})
        audit.append(dict(asset_id=request.asset_id, source_id=subject.source,
            previous_source_crop=list(subject.crop), source_crop=list(request.source_crop),
            treatment=subject.treatment, reason=request.reason, source_path=str(sources[subject.source]),
            previous_asset=str(assets.get(request.asset_id, '')),
            original_asset=str(cropped), prepared_size=size))
    _check(cancelled)
    if pending:
        cutouts = rmbg.run_cutouts(pending, folder, should_stop=cancelled, trim_padding=None)
        _check(cancelled)
        if set(cutouts) != set(pending):
            raise ValueError('重新取材的 RMBG 返回主体不完整，未采用')
        for name, raw in pending.items():
            with Image.open(raw) as original, Image.open(cutouts[name]) as cutout:
                if original.size != cutout.size:
                    raise ValueError(f'{name} 重新抠图与原始裁片尺寸不一致，未采用')
                if cutout.convert('RGBA').getchannel('A').getbbox() is None:
                    raise ValueError(f'{name} 重新抠图完全透明，未采用')
        updated_assets.update(cutouts)
    for item in audit:
        item['prepared_asset'] = str(updated_assets[item['asset_id']])
        item['rmbg_used'] = item['asset_id'] in pending
    _check(cancelled)
    return inventory.model_copy(update={'subjects': subjects}), updated_assets, audit
