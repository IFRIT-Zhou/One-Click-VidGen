"""Versioned, project-owned PNG layers from an existing storyboard. No model, no project edits."""
from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import threading
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image, ImageChops, ImageDraw, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from . import video_studio as studio
from .auth import require_user
from .vibe_files import publish

router = APIRouter(prefix='/api/video-studio')
Coord = Annotated[StrictInt, Field(ge=0, le=32768)]
Extent = Annotated[StrictInt, Field(gt=0, le=32768)]
PACK_ID = re.compile('[0-9a-f]{32}')
ASSET_ID = re.compile(r'(?:plate|layer_(?:0[1-9]|1[0-6]))')
MAX_PIXELS = 16_777_216
MAX_BYTES = 32 * 1024 * 1024
ALGORITHM = 'ocv-mask-plate-v1.1'
_slots = threading.BoundedSemaphore(2)


class Mask(BaseModel):
    model_config = ConfigDict(extra='forbid')
    shape: Literal['ellipse', 'rect']
    box: tuple[Coord, Coord, Coord, Coord] = Field(description='Canvas coordinates: left, top, right exclusive, bottom exclusive.')


class Element(Mask):
    name: str = Field(min_length=1, max_length=80, pattern=r'\S')


class LayerPackRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_sha256: str = Field(pattern='^[0-9a-f]{64}$', description='Version hash of current core image, not a path.')
    canvas: tuple[Extent, Extent] = Field(description='Coordinate canvas dimensions of the uncropped source preview.')
    elements: list[Element] = Field(min_length=1, max_length=16)
    cleanup: list[Mask] = Field(default_factory=list, max_length=16, description='Optional plate-only masks; no layers returned for these.')

    @model_validator(mode='after')
    def check_geometry(self):
        w, h = self.canvas
        for mask in [*self.elements, *self.cleanup]:
            x, y, r, b = mask.box
            if not (0 <= x < r <= w and 0 <= y < b <= h):
                raise ValueError('选区越界或为空；请使用同一完整预览图的画布坐标')
        return self


def _hash(b):
    return hashlib.sha256(b).hexdigest()


def _inside(root, file):
    if not file.resolve().is_relative_to(root.resolve()) or file.resolve() == root.resolve():
        raise HTTPException(404, '素材不存在')
    return file


def owned_project(request, identity):
    user_id = require_user(request)['id']
    path = studio.directory(user_id, identity)
    # Resolve the trusted storage root once: Windows may spell a legitimate
    # ancestor using its 8.3 alias (ADMINI~1). Only redirects *below* the
    # trusted root can take this request into another user's storage.
    expected = studio.ROOT.resolve() / str(user_id) / identity
    record = expected / 'record.json'
    if path.resolve() != expected or record.resolve() != record or not record.is_file():
        raise HTTPException(404, '项目不存在')
    return expected


def pack_asset_path(project, pack_id, asset_id):
    if not PACK_ID.fullmatch(pack_id) or not ASSET_ID.fullmatch(asset_id):
        raise HTTPException(404, '图层资产不存在')
    folder = project / 'assets' / 'layer_packs' / pack_id
    target = _inside(project, folder / (asset_id + '.png'))
    # Do not follow reparse points to a different pack/project/user.
    if target.resolve() != target.absolute() or not target.is_file():
        raise HTTPException(404, '图层资产不存在')
    return target


def _pixel_box(mask, canvas, size):
    rect = tuple(round(n * size[i % 2] / canvas[i % 2]) for i, n in enumerate(mask.box))
    if rect[0] == rect[2] or rect[1] == rect[3]:
        raise HTTPException(422, '选区换算到原图后不足一个像素')
    return rect


def _alpha(size, shape):
    if shape == 'rect':
        return Image.new('L', size, 255)
    # Antialias edges; no source resampling or generated replacement pixels in layers.
    w, h = size
    mask = Image.new('L', (w * 2, h * 2))
    ImageDraw.Draw(mask).ellipse((0, 0, w * 2 - 1, h * 2 - 1), fill=255)
    return mask.resize(size, Image.Resampling.LANCZOS)


def _clean_plate(original, union):
    """Normalized multiscale interpolation using *only* exterior pixels for hidden background.

    Removing an object cannot recover the real background behind it. In particular, never
    present this plate as a clinical source. Exterior pixels are pasted from source exactly.
    """
    import cv2
    import numpy as np
    pixels = np.asarray(original)
    hole = np.asarray(union) > 0
    if np.mean(hole) > .75:
        raise HTTPException(422, '去除区域过大，请保留至少四分之一画布作为背景依据')
    # Bound RAM by doing the coarse fill on a proxy, without allowing any hidden RGB
    # to leak into it even at anti-aliased mask boundaries.
    H, W = hole.shape
    scale = min(1, 640 / max(W, H))
    small = (max(1, round(W * scale)), max(1, round(H * scale)))
    valid = (~hole).astype('uint8')
    weight = cv2.resize(valid.astype('float32'), small, interpolation=cv2.INTER_AREA)
    numerator = np.stack([cv2.resize(np.where(hole, 0, pixels[:, :, i]).astype('float32'),
                          small, interpolation=cv2.INTER_AREA) for i in range(pixels.shape[2])], axis=2)
    pyramid = [(numerator, weight)]
    while max(weight.shape) > 1:
        numerator, weight = cv2.pyrDown(numerator), cv2.pyrDown(weight)
        pyramid.append((numerator, weight))
    estimate = numerator / weight[:, :, None].clip(1e-20)
    for color, weight in reversed(pyramid[:-1]):
        estimate = cv2.resize(estimate, weight.shape[::-1], interpolation=cv2.INTER_LINEAR)
        strength = np.minimum(1, weight * 4)[:, :, None]
        estimate = estimate * (1-strength) + (color / weight[:, :, None].clip(1e-20)) * strength
    # Convert before final upscale to limit peak memory on 4K images.
    fill = cv2.resize(np.uint8(estimate.clip(0, 255).round()), (W, H), interpolation=cv2.INTER_LINEAR)
    result = original.copy()
    result.paste(Image.fromarray(fill), (0, 0), union)
    return result


def create_pack(source: Path, store: Path, data: LayerPackRequest, *, project_id: str, shot_id: str, preserve_plate=False):
    # Hash the SAME bounded bytes that PIL decodes, even if another process replaces the shot.
    with source.open('rb') as f:
        if source.stat().st_size > MAX_BYTES:
            raise HTTPException(413, '分镜图文件过大')
        content = f.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, '分镜图文件过大')
    if _hash(content) != data.source_sha256:
        raise HTTPException(409, '分镜图版本已改变，请重新读取当前分镜与选区')
    fingerprint = json.dumps([ALGORITHM+('-whole-frame' if preserve_plate else ''), project_id, shot_id, data.model_dump(mode='json')],
                             ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    pack_id = _hash(fingerprint.encode())[:32]
    store.mkdir(parents=True, exist_ok=True)
    destination = store / pack_id
    if destination.resolve() != destination.absolute():
        raise HTTPException(404, '素材不存在')
    if destination.is_dir():
        receipt = destination / 'manifest.json'
        if receipt.resolve() != receipt.absolute():
            raise HTTPException(404, '素材不存在')
        return json.loads(receipt.read_text(encoding='utf-8'))
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in {'JPEG','PNG','WEBP'} or getattr(image, 'n_frames', 1) != 1:
                raise HTTPException(422, '仅支持单帧 JPEG、PNG、WebP 分镜图')
            if image.width * image.height > MAX_PIXELS:
                raise HTTPException(413, '原图像素超过本地提取上限')
            oriented = ImageOps.exif_transpose(image)
            original = oriented.convert('RGBA' if 'A' in oriented.getbands() or 'transparency' in oriented.info else 'RGB')
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, '无法读取分镜图') from exc
    w, h = original.size
    cw, ch = data.canvas
    if abs(cw * h - ch * w) > .004 * max(cw * h, ch * w):
        raise HTTPException(422, '选区画布比例与原始分镜不一致，请使用未裁剪的完整预览')
    boxes = [_pixel_box(mask, data.canvas, original.size) for mask in [*data.elements, *data.cleanup]]
    if sum((r-x)*(b-y) for x,y,r,b in boxes) > 2*w*h:
        raise HTTPException(422, '重复选区像素过多，请减少重叠')
    union = Image.new('L', original.size)
    base_url = f'/api/video-studio/{project_id}/layer-packs/{pack_id}/'
    manifest = dict(pack_id=pack_id, project_id=project_id, shot_id=shot_id, source_sha256=data.source_sha256,
                    source_size=[w, h], canvas=list(data.canvas), algorithm=ALGORITHM, may_charge=False,
                    clean_plate=dict(asset_id='plate', url=base_url+'plate', size=[w,h],
                        method='interpolated_background', needs_visual_review=True),
                    elements=[], cleanup=[m.model_dump(mode='json') for m in data.cleanup])
    # Publish only complete packs. Same version + same plan produces the same ID across retries.
    with tempfile.TemporaryDirectory(prefix='.layers-', dir=store) as stage:
        output = Path(stage) / 'pack'
        output.mkdir()
        for i, (mask, box) in enumerate(zip([*data.elements, *data.cleanup], boxes)):
            x, y, r, b = box
            alpha = _alpha((r-x,b-y), mask.shape)
            union.paste(ImageChops.lighter(union.crop(box), alpha.point(lambda a: 255 if a else 0)), box)
            if i < len(data.elements):
                tile = original.crop(box).convert('RGBA')
                tile.putalpha(ImageChops.multiply(alpha, tile.getchannel('A')))
                asset = f'layer_{i+1:02d}'
                tile.save(output / (asset+'.png'))
                manifest['elements'].append(dict(asset_id=asset, name=mask.name, shape=mask.shape,
                         box=list(mask.box), box_px=list(box), url=base_url+asset))
        if preserve_plate:
            # Internal whole-poster transition path: no inpainting or extraction.
            # The public layer-pack endpoint never enables this option.
            if len(boxes)!=1 or boxes[0]!=(0,0,w,h) or data.cleanup:
                raise HTTPException(422,'整图转场必须使用完整画布')
            original.save(output / 'plate.png')
            manifest['clean_plate'].update(method='original_whole_frame',needs_visual_review=False)
        else:
            _clean_plate(original, union).save(output / 'plate.png')
        union.save(output / 'plate-mask.png')  # Internal binary authority for bounded AI plate refinements.
        (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        try:
            publish(output,destination)
        except OSError:
            if not (destination / 'manifest.json').is_file(): raise
    return manifest


@router.post('/{identity}/shots/{shot_id}/layer-packs')
def extract(identity: str, shot_id: str, data: LayerPackRequest, request: Request):
    project = owned_project(request, identity)
    with studio.LOCK:
        # studio.read() can repair a project as a side effect; this operation must not.
        record = json.loads((project/'record.json').read_text(encoding='utf-8-sig'))
        shot = next((row for row in record.get('shots', []) if row.get('id') == shot_id), None)
        if not shot:
            raise HTTPException(404, '镜头不存在')
        source = studio._storyboard_image_path(project, shot)
    store = _inside(project, project/'assets'/'layer_packs')
    if store.resolve() != store.absolute():
        raise HTTPException(404, '素材目录不存在')
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, '图层提取繁忙，请稍后再试')
    try:
        return create_pack(source, store, data, project_id=identity, shot_id=shot_id)
    finally:
        _slots.release()


@router.get('/{identity}/layer-packs/{pack_id}/{asset_id}')
def download(identity: str, pack_id: str, asset_id: str, request: Request):
    return FileResponse(pack_asset_path(owned_project(request, identity), pack_id, asset_id), media_type='image/png')
