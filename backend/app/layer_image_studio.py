"""Optional *billed* OCV Image Studio operations on an explicitly selected native layer pack.

Never runs during deterministic layer extraction. The upstream proposes a matte or missing
background, not the pixels of a medical specimen. Outputs stay in the user's studio history.
"""
import shutil
import time
import uuid
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from PIL import Image, ImageChops, ImageOps

from .auth import require_user


class PackInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    pack_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class MatteInput(PackInput):
    asset_id: str = Field(pattern=r'^layer_(0[1-9]|1[0-6])$')
    subject: str = Field(min_length=1, max_length=180, pattern=r'\S')
    confirm_reference_upload: Literal[True]


class PlateInput(PackInput):
    confirm_plate_upload: Literal[True]
    preserve_background: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=16,
        description='原图确实可见、必须按原位保留的背景装饰。不是要求生成或猜测被挡住的内容。')


SIDE = 1024
MATTE_PROMPT = ('输出与输入画布严格对齐的黑白蒙版，不输出插图。目标为：{}。'
                '目标内部纯白，其余区域及画布边缘填充部分纯黑。不要文字、棋盘格、描边、'
                '阴影、再构图、缩放或移动，不补充不可见结构。')
def build_plate_prompt(preserve_background=(), *, seamless=False):
    """Used by plate repair and isolated full-frame Vibe backdrop preparation."""
    keep = '；'.join(preserve_background)
    return (
        '参考图为移除独立前景后的底板。' +
        ('仅消除已挖除前景区域的颜色接缝，输出完整自然的无缝底板。' if seamless else
         '仅修复选区内底纹不连续处，使其与邻近未选区域自然延续。') +
        '清理的是前景，不是清空背景；保留原画幅、布局、色调、光感与原有低对比装饰，不移位、不缩放。' +
        (f'必须保护原图可见的这些背景内容及位置：{keep}。' if keep else '') +
        '保留的是参考图已存在的像素信息，不在留白处另造装饰；科学结构不得改变连接、增减细节，'
        '被遮挡且无依据的部位仅接续底色，不能猜绘。'
        '不要恢复已移出的前景；不要生成文字或其可编辑底框，它们由后续文字层创建。'
        '不新增人物、器官、标本、箭头、数值或医疗信息。仅输出背景。')


def _square_reference(src, target):
    with Image.open(src) as file:
        rgba = file.convert('RGBA')
    # Flatten transparency BEFORE uploading: invisible RGB in a rectangular crop
    # may belong to a different object. Never transmit those hidden source pixels.
    flat = Image.new('RGB', rgba.size, (48, 48, 48))
    flat.paste(rgba, mask=rgba.getchannel('A'))
    scaled = ImageOps.contain(flat, (SIDE, SIDE), Image.Resampling.LANCZOS)
    x, y = (SIDE-scaled.width)//2, (SIDE-scaled.height)//2
    frame = Image.new('RGB', (SIDE, SIDE), (48, 48, 48))
    frame.paste(scaled, (x, y))
    frame.save(target)
    return [x, y, x+scaled.width, y+scaled.height]


def process_proposal(path, record, image):
    """Return native-size final image. No model-generated RGB in matte-mode output."""
    meta = record.get('layer_matte') or record['layer_plate']
    if image.width != image.height or not 256 <= image.width <= 4096:
        raise ValueError('模型输出画幅与提交画布不符，未生成图层；请核对本次工作室任务，不要自动重试')
    box = tuple(round(v * image.width/SIDE) for v in meta['content_box'])
    with Image.open(path/'source.png') as f:
        had_alpha = 'A' in f.getbands() or 'transparency' in f.info
        src = f.convert('RGBA')
    region = image.convert('RGB').crop(box)
    if 'layer_matte' in record:
        thumb = region.copy()
        thumb.thumbnail((128,128))
        pixels = list(thumb.getdata())
        bw = sum(max(p)-min(p)<28 and (max(p)<56 or min(p)>199) for p in pixels)
        if bw < len(pixels)*.86:
            raise ValueError('模型未返回可用的黑白蒙版；原图层未变，未自动重试')
        mask = region.convert('L').point(lambda v: 0 if v<32 else (255 if v>223 else v))
        if mask.getextrema()[0] >= 128 or mask.getextrema()[1] < 128:
            raise ValueError('模型蒙版未区分主体与背景')
        alpha = ImageChops.multiply(mask.resize(src.size, Image.Resampling.BILINEAR), src.getchannel('A'))
        hist = alpha.histogram()
        if not sum(hist[128:]) or not sum(hist[:128]):
            raise ValueError('蒙版未区分主体与背景；原图层未变')
        src.putalpha(alpha)
        alpha.save(path/'mask.png')
        return src
    with Image.open(path/'selection.png') as f:
        mask = f.convert('L')
    if mask.size != src.size or not mask.getbbox():
        raise ValueError('底板修补范围无效，未采用模型结果')
    # Even when the model redraws the entire frame, outside the local mask we
    # publish exactly the existing RGB, never model changes to labels/specimens.
    result = Image.composite(region.resize(src.size, Image.Resampling.LANCZOS), src.convert('RGB'), mask)
    if had_alpha:
        result.putalpha(src.getchannel('A'))
    return result


def submit(data, request):
    from . import image_studio as studio
    from .image_layer_packs import owned_project, pack_asset_path
    user = int(require_user(request)['id'])
    project = owned_project(request, data.project_id)
    matte = isinstance(data, MatteInput)
    asset = data.asset_id if matte else 'plate'
    source = pack_asset_path(project, data.pack_id, asset)
    mask = source.with_name('plate-mask.png')
    if not matte and (mask.is_symlink() or not mask.is_file() or mask.resolve().parent != source.resolve().parent):
        raise HTTPException(409, '该图层包没有可安全使用的底板修补范围')
    prompt = MATTE_PROMPT.format(data.subject.strip()) if matte else build_plate_prompt(data.preserve_background)
    # This operation promises OCV Image 2.5; never silently switch to another
    # provider or use the calling assistant's own image-generation account.
    req = studio.ImageRequest(prompt=prompt, provider='pool', method='ican',
                              size='1024x1024', ratio='1:1', resolution='1k')
    path = None
    record = None
    try:
        config, client = studio.config_for(user, req)
        identity = uuid.uuid4().hex
        job_folder = studio.folder(user, identity)
        job_folder.mkdir(parents=True)
        path = job_folder
        shutil.copyfile(source, path/'source.png')
        if not matte:
            shutil.copyfile(mask, path/'selection.png')
        bounds = _square_reference(path/'source.png', path/'ref_1.png')
        meta = dict(project_id=data.project_id, pack_id=data.pack_id, asset_id=asset,
                    content_box=bounds, requires_review=True)
        record = dict(id=identity, created_at=time.time(), status='running', message='准备图像工作室任务',
                      prompt=prompt, provider=req.provider, method=req.method, size=req.size,
                      ratio=req.ratio, profile_id=None, profile_name='', resolution=req.size,
                      references=['ref_1.png'], **{'layer_matte' if matte else 'layer_plate':meta})
        studio.save(path, record)
        studio.enqueue(user, path, record, config, client)
        return record
    except Exception as exc:
        if path is not None and record is None:
            for name in ('source.png', 'selection.png', 'ref_1.png'):
                (path/name).unlink(missing_ok=True)
            path.rmdir()
        elif path is not None:
            record.update(status='failed', message=str(exc))
            studio.save(path, record)
        if getattr(exc, 'code', '') == 'CLOUD_LOGIN_REQUIRED':
            raise HTTPException(401, '[CLOUD_LOGIN_REQUIRED] 请登录 OCV 后再使用 ICAN Image 2.5') from exc
        raise HTTPException(400, str(exc)) from exc
