"""An explicitly requested, non-destructive local animation of one saved shot.

Preview artifacts live outside the formal project record; this router never
advances workflow state, spends credits, generates narration, or replaces a shot.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import threading
import uuid
import wave

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from . import video_studio as studio
from .image_layer_packs import MAX_BYTES, owned_project, pack_asset_path
from .pipeline import ffmpeg_binary
from .vibe_files import publish
from .vibe_render import MAX_SECONDS, RENDERER_VERSION, VibePlan, render_preview

router = APIRouter(prefix='/api/video-studio')
_slot = threading.BoundedSemaphore(1)
_hex = re.compile(r'[0-9a-f]{32}')
_audio_limit = 512 * 1024**2
_files = {'preview':('preview.mp4','video/mp4'), 'silent':('silent.mp4','video/mp4'),
          'proof':('proof.jpg','image/jpeg')}


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input_stamp: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$',
                                    description='自动设计返回的本镜内容签名；原样传回以防错用旧时间轴。')
    pack_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    plan: VibePlan


def _hash(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _safe(root, path, *, file=False):
    resolved = path.resolve()
    if (resolved != path.absolute() or not resolved.is_relative_to(root) or resolved == root
            or (file and not resolved.is_file())):
        raise HTTPException(404, '预览素材不存在')
    return resolved


def _read_json(path, byte_limit):
    try:
        with path.open('rb') as f:
            raw = f.read(byte_limit+1)
        if len(raw)>byte_limit:
            raise HTTPException(413, '预览元数据文件过大')
        data = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(409, '预览元数据无效') from exc


def _stream_hash(source, limit, copy=None):
    h = hashlib.sha256()
    size = 0
    with source.open('rb') as f:
        for chunk in iter(lambda: f.read(1<<20), b''):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413,'媒体文件超过单镜预览上限')
            h.update(chunk)
            if copy is not None:
                copy.write(chunk)
    return h.hexdigest()


def _audio(path, start, end, copy=None):
    """Hash only this shot's PCM samples and format. Snapshot the exact hashed
    data when rendering, so a concurrent replacement never creates mixed audio.
    Non-PCM RIFF WAVs use a size-bounded whole-file snapshot."""
    try:
        audio = wave.open(str(path), 'rb')
    except (wave.Error, EOFError):
        with path.open('rb') as f: header = f.read(12)
        if header[:4] != b'RIFF' or header[8:12] != b'WAVE':
            raise HTTPException(422,'预览需要项目已保存的 WAV 配音')
        if copy:
            with copy.open('wb') as out:
                return _stream_hash(path, _audio_limit, out), start
        return _stream_hash(path, _audio_limit), start
    with audio:
        rate, chans, width = audio.getframerate(), audio.getnchannels(), audio.getsampwidth()
        first, last = round(start*rate), round(end*rate)
        if not (1 <= chans <= 8 and 1 <= width <= 4 and 1 <= rate <= 192000
                and 0 <= first < last <= audio.getnframes()+max(1,round(.001*rate))):
            raise HTTPException(422,'本镜时间段无有效完整配音，请先核对最终配音与字幕')
        last=min(last,audio.getnframes())
        if first>=last:
            raise HTTPException(422,'本镜无可用配音样本')
        audio.setpos(first)
        h=hashlib.sha256(f'{rate}:{chans}:{width}:{last-first}|'.encode())
        out=wave.open(str(copy),'wb') if copy else None
        try:
            if out: out.setparams((chans,width,rate,0,'NONE','not compressed'))
            todo=last-first
            while todo:
                count=min(todo,32768); chunk=audio.readframes(count)
                if len(chunk)!=count*width*chans:
                    raise HTTPException(422,'原配音数据不完整')
                h.update(chunk)
                if out: out.writeframesraw(chunk)
                todo-=count
        finally:
            if out: out.close()
        return h.hexdigest(),0.


def _number(value):
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value):
        raise HTTPException(409,'本镜的字幕或起止时间无效')
    return float(value)


def _snapshot(project, shot_id):
    with studio.LOCK:
        record=_read_json(_safe(project, project/'record.json',file=True),32*1024**2)
    shot=next((s for s in record.get('shots',[]) if s.get('id')==shot_id),None)
    if shot is None: raise HTTPException(404,'分镜不存在')
    start,end=_number(shot.get('start')),_number(shot.get('end'))
    if start<0 or not .1<=end-start<=MAX_SECONDS:
        raise HTTPException(422,f'单镜预览支持 0.1 至 {MAX_SECONDS} 秒')
    rows = shot.get('source_subtitles') or []
    ids = shot.get('slide_ids') or [r.get('slide_id') for r in rows]
    if not ids or len(ids)>512 or len(set(ids))!=len(ids):
        raise HTTPException(409,'本镜缺少可用的唯一字幕交界，请核对最终字幕')
    source = record.get('scenes') or rows
    wanted=set(ids)
    subtitles=[]; cues={}; previous=-1.
    for r in source:
        key=r.get('slide_id')
        if key not in wanted: continue
        a,b=_number(r.get('start')),_number(r.get('end'))
        if a < start-.002 or b>end+.002 or b<=a or a<previous-.002:
            raise HTTPException(409,'本镜与已保存字幕的时间范围不一致')
        subtitles.append([key,r.get('text',''),a,b]); cues[key]=max(0.,a-start); previous=b
    if [r[0] for r in subtitles]!=ids:
        raise HTTPException(409,'本镜字幕顺序与已保存时间轴不一致')
    image=_safe(project,studio._storyboard_image_path(project,shot),file=True)
    image_hash=_stream_hash(image,MAX_BYTES)
    audio=_safe(project, project / str(record.get('audio') or ''),file=True)
    if audio.suffix.lower()!='.wav':
        raise HTTPException(422,'预览需要项目已保存的 WAV 配音')
    audio_hash,audio_seek=_audio(audio,start,end)
    # Edits to a previous shot can move the entire sequence in time without
    # changing a single frame or sample in this one. Use shot-relative times.
    relative_cues=[[key,text,round(a-start,6),round(b-start,6)] for key,text,a,b in subtitles]
    # For compressed/float RIFF fallback we can only hash the entire file;
    # include its seek position so different audio slices never alias.
    stamp=_hash([RENDERER_VERSION,shot_id,round(end-start,6),relative_cues,image_hash,audio_hash,audio_seek])
    return stamp,image_hash,audio_hash,audio,start,end,cues


def _store(project):
    return _safe(project,project/'assets'/'vibe_previews')


def _receipt(project, preview_id):
    store=_store(project)
    p=_safe(project,store/preview_id/'receipt.json',file=True)
    data=_read_json(p,512*1024)
    if data.get('preview_id')!=preview_id or data.get('identity')!=project.name:
        raise HTTPException(409,'本地预览索引不一致')
    for asset,(name,_) in _files.items():
        # Historical pre-proof previews stay downloadable at their immutable URL.
        if asset=='proof' and 'proof_url' not in data:
            continue
        _safe(project,p.parent/name,file=True)
    return data


def _pointer(project,shot_id):
    return _safe(project,_store(project)/('latest-'+_hash(shot_id)[:32]+'.json'))


def _publish_pointer(project,shot_id,preview_id):
    p=_pointer(project,shot_id)
    tmp=p.with_name('.latest-'+uuid.uuid4().hex+'.tmp')
    try:
        tmp.write_text(json.dumps({'preview_id':preview_id}),encoding='utf-8')
        publish(tmp,p,replace=True)
        return p.stat().st_mtime
    finally:
        tmp.unlink(missing_ok=True)


@router.post('/{identity}/shots/{shot_id}/vibe-previews')
def create(identity:str, shot_id:str, data:PreviewRequest, request:Request):
    project=owned_project(request,identity)
    if not _slot.acquire(blocking=False):
        raise HTTPException(429,'本机正在渲染其他分镜预览，请等待后重试')
    try:
        stamp,im_hash,a_hash,audio,start,end,cues=_snapshot(project,shot_id)
        if data.input_stamp is not None and data.input_stamp != stamp:
            raise HTTPException(409,'设计后本镜画面、字幕或配音已变，请按最新时间轴重新设计。')
        if im_hash!=data.source_sha256:
            raise HTTPException(409,'核心分镜图已改变，请重新提取图层')
        plate=pack_asset_path(project,data.pack_id,'plate')
        manifest=_read_json(_safe(project,plate.with_name('manifest.json'),file=True),512*1024)
        if (manifest.get('project_id'),manifest.get('shot_id'),manifest.get('pack_id'),manifest.get('source_sha256'))!=(
                identity,shot_id,data.pack_id,im_hash):
            raise HTTPException(409,'图层包与本项目、本镜或当前图片版本不一致')
        assets={'plate':plate}
        entries=manifest.get('elements')
        if not isinstance(entries,list) or not 1<=len(entries)<=16: raise HTTPException(409,'图层包无效')
        for el in entries:
            layer=el.get('asset_id','')
            assets[layer]=pack_asset_path(project,data.pack_id,layer)
        plan=data.plan.model_dump(mode='json')
        preview_id=_hash([stamp,manifest,plan])[:32]
        store=_store(project); store.mkdir(parents=True,exist_ok=True)
        dest=_safe(project,store/preview_id)
        if dest.exists():
            receipt=_receipt(project,preview_id)
            selected_at=_publish_pointer(project,shot_id,preview_id)
            return {**receipt,'cached':True,'selected_at':selected_at}
        if sum(1 for f in store.iterdir() if _hex.fullmatch(f.name))>=192:
            raise HTTPException(409,'本项目预览缓存已达上限，请先由项目所有者清理不再使用的预览文件')
        with tempfile.TemporaryDirectory(prefix='.render-',dir=store) as tmp:
            temp=Path(tmp); out=temp/'out'; out.mkdir()
            digest,seek=_audio(audio,start,end,temp/'narration.wav')
            if digest!=a_hash: raise HTTPException(409,'配音版本已改变，请重新预览')
            try:
                metrics=render_preview(plan=data.plan, manifest=manifest, assets=assets, cues=cues,
                        audio=temp/'narration.wav', start=seek, duration=end-start, output_dir=out,
                        ffmpeg=ffmpeg_binary(),timeout=150)
            except (ValueError, KeyError) as exc:
                raise HTTPException(422,'动画单或素材无效：'+str(exc)) from exc
            except TimeoutError as exc:
                raise HTTPException(504,'本地渲染超时，未提交结果') from exc
            except FileNotFoundError as exc:
                raise HTTPException(409,'找不到 FFmpeg 或图层文件，请检查本地运行环境和素材') from exc
            except (RuntimeError, OSError) as exc:
                raise HTTPException(500,'本地预览编码失败，请检查磁盘、FFmpeg 和原素材') from exc
            if stamp!=_snapshot(project,shot_id)[0]:
                raise HTTPException(409,'预览期间图片、配音或字幕已变，未发布过期结果')
            for name,_ in _files.values():
                artifact=_safe(project,out/name,file=True)
                if artifact.stat().st_size==0:
                    raise HTTPException(500,'编码器未生成完整预览和核对图，未发布结果')
            base=f'/api/video-studio/{identity}/vibe-previews/{preview_id}/'
            receipt=dict(identity=identity,shot_id=shot_id,preview_id=preview_id,pack_id=data.pack_id,
                         source_sha256=im_hash,input_stamp=stamp,plan=plan,metrics=metrics,
                         preview_url=base+'preview',silent_url=base+'silent',proof_url=base+'proof',
                         cached=False,may_charge=False)
            (out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False),encoding='utf-8')
            try:
                publish(out,dest)
            except OSError:
                if not dest.is_dir(): raise HTTPException(503,'本地预览目录被占用或无写权限，请稍后重试；未采用或覆盖任何正式镜头。')
                receipt={**_receipt(project,preview_id),'cached':True}
        selected_at=_publish_pointer(project,shot_id,preview_id)
        return {**receipt,'selected_at':selected_at}
    finally:
        _slot.release()


@router.get('/{identity}/shots/{shot_id}/vibe-preview/latest')
def latest(identity:str,shot_id:str,request:Request):
    project=owned_project(request,identity)
    p=_pointer(project,shot_id)
    if not p.is_file():
        return {'available':False}
    preview_id=_read_json(p,4096).get('preview_id','')
    selected_at=p.stat().st_mtime
    if not _hex.fullmatch(preview_id): raise HTTPException(409,'预览索引损坏')
    receipt=_receipt(project,preview_id)
    try:
        stamp = _snapshot(project,shot_id)[0]
    except HTTPException as exc:
        if exc.status_code not in (404,409,422):
            raise
        return {'available':False,'stale':True,'reason':exc.detail}
    if receipt.get('shot_id')!=shot_id or receipt.get('input_stamp')!=stamp:
        return {'available':False,'stale':True}
    if receipt.get('engine')=='cloud':
        from .vibe_cloud import validate_cloud_preview
        if not validate_cloud_preview(project,shot_id,receipt):
            return {'available':False,'stale':True}
    return {**receipt,'available':True,'cached':True,'selected_at':selected_at}


@router.get('/{identity}/vibe-previews/{preview_id}/{asset_id}')
def download(identity:str,preview_id:str,asset_id:str,request:Request):
    project=owned_project(request,identity)
    if not _hex.fullmatch(preview_id) or asset_id not in _files:
        raise HTTPException(404,'预览文件不存在')
    receipt=_receipt(project,preview_id)
    if asset_id+'_url' not in receipt: raise HTTPException(404,'此版本预览没有该文件')
    name,media_type=_files[asset_id]
    path=_safe(project,_store(project)/preview_id/name,file=True)
    return FileResponse(path,media_type=media_type)
