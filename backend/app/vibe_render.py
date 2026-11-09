"""Local, CPU-only compositor for a single OCV storyboard and its current narration.

The API resolves all paths/IDs and provides an empty private output directory. The
wire format contains no scripts, URLs, filenames, expressions or FFmpeg arguments.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import struct
import subprocess
import threading
import time
from typing import Annotated

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, model_validator

RENDERER_VERSION = 'ocv-vibe-layers-2'
FPS = 30
MAX_SECONDS = 60
MAX_PIXELS = 16_777_216
Cue = Annotated[str, Field(min_length=1, max_length=128)]
Coord = Annotated[int, Field(strict=True, ge=-32768, le=32768)]
Pair = tuple[Coord, Coord]
Seconds = Annotated[float, Field(strict=True, gt=0, le=5, allow_inf_nan=False)]
Delay = Annotated[float, Field(strict=True, ge=-10, le=60, allow_inf_nan=False)]


class _Fields(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Settle(_Fields):
    cue: Cue
    delay: Delay = 0
    seconds: Seconds = .6


class Marker(_Fields):
    cue: Cue
    until: Cue | None = None
    at: Pair = Field(description='Anchor in the original pack canvas; moves with the layer.')


class Motion(_Fields):
    asset_id: str = Field(pattern=r'^layer_(0[1-9]|1[0-6])$')
    cue: Cue
    delay: Delay = 0
    seconds: Seconds = .6
    scale_from: float = Field(default=1, strict=True, ge=.5, le=1.25, allow_inf_nan=False)
    offset: Pair = (0, 0)
    settle: Settle | None = None
    markers: list[Marker] = Field(default_factory=list, max_length=16)


class VibePlan(_Fields):
    sequence: list[Motion] = Field(min_length=1, max_length=16,
                                   description='Back-to-front paint order; exactly one entry per extracted layer.')
    fade_in: float = Field(default=.5, strict=True, ge=0, le=3, allow_inf_nan=False)
    fade_out: float = Field(default=.3, strict=True, ge=0, le=3, allow_inf_nan=False)

    @model_validator(mode='after')
    def distinct(self):
        if len({m.asset_id for m in self.sequence}) != len(self.sequence):
            raise ValueError('动画单有重复图层')
        return self


def _ease(t):
    t = min(1., max(0., t))
    return t*t*(3.-2.*t)


def _output_size(size):
    # Preserve full artwork and orientation. Bound both cost and portrait resolution.
    w, h = size
    k = min(1., (1920 if w >= h else 1080)/w, (1080 if w >= h else 1920)/h)
    return tuple(max(2, 2*round(d*k/2)) for d in size)


def _pair(value):
    if (not isinstance(value, (list,tuple)) or len(value)!=2
            or any(type(n) is not int or not 1 <= n <= 32768 for n in value)):
        raise ValueError('图层包尺寸无效')
    return tuple(value)


def _load_png(path, expected, target, *, plate=False):
    with Image.open(path) as img:
        if (img.format != 'PNG' or img.size != expected or getattr(img, 'n_frames', 1) != 1
                or expected[0]*expected[1] > MAX_PIXELS or not plate and img.mode != 'RGBA'):
            raise ValueError('素材尺寸、格式或透明图层与清单不符')
        if plate:
            rgba = img.convert('RGBA')
            image = Image.new('RGB', img.size)
            image.paste(rgba, mask=rgba.getchannel('A'))
        else:
            image = img.copy()
    return image if image.size == target else image.resize(target, Image.Resampling.LANCZOS)


@dataclass
class _Layer:
    tile: Image.Image
    anchor: tuple[int, int]
    offset: tuple[float,float]
    motion: Motion
    start: float
    settle_at: float
    travel_seconds: float
    markers: list


class _Scene:
    def __init__(self, plan, manifest, assets, cues, duration):
        size = _pair(manifest['source_size'])
        cw, ch = _pair(manifest['canvas'])
        w, h = self.size = _output_size(size)
        self.count = round(duration*FPS)
        self.fade_in, self.fade_out = plan.fade_in, plan.fade_out
        self.plate = _load_png(assets['plate'], size, self.size, plate=True)
        self.flat = Image.new('RGB', self.size, self.plate.getpixel((0,0)))
        self.line_height = max(3,round(6*h/ch))
        self.line_width = max(2,round(2*w/cw))
        self.layers = []
        items = {item['asset_id']:item for item in manifest['elements']}
        if set(items) != {m.asset_id for m in plan.sequence} or len(items)!=len(manifest['elements']):
            raise ValueError('动画单必须逐一引用当前图层包中的全部图层')

        def cue_time(cue, delay=0):
            if cue not in cues or not math.isfinite(cues[cue]):
                raise ValueError(f'本镜没有字幕 {cue}')
            instant = cues[cue]+delay
            if not -.001 <= instant < duration:
                raise ValueError(f'动画时间不在镜头范围内：{cue}')
            return max(0.,instant)

        total_pixels = 0
        for motion in plan.sequence:
            box = items[motion.asset_id]['box_px']
            if (not isinstance(box,(list,tuple)) or len(box)!=4 or any(type(i) is not int for i in box)
                    or not (0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1])):
                raise ValueError('素材原始坐标无效')
            x,y,r,b = [round(v*self.size[i%2]/size[i%2]) for i,v in enumerate(box)]
            target = (max(1,r-x),max(1,b-y))
            total_pixels += target[0]*target[1]
            if total_pixels > 2*w*h + 64*(w+h):
                raise ValueError('图层总面积超过单镜渲染预算')
            tile = _load_png(assets[motion.asset_id],(box[2]-box[0],box[3]-box[1]),target)
            at = cue_time(motion.cue,motion.delay)
            if abs(motion.offset[0]) > cw or abs(motion.offset[1]) > ch:
                raise ValueError('图层偏移超过一屏')
            settle_at = cue_time(motion.settle.cue,motion.settle.delay) if motion.settle else at
            if settle_at < at:
                raise ValueError('归位时间不得早于图层入场')
            travel_seconds = motion.settle.seconds if motion.settle else motion.seconds
            if max(at+motion.seconds,settle_at+travel_seconds)>duration+1/FPS:
                raise ValueError(f'图层 {motion.asset_id} 的入场或归位超过镜头时间，请缩短动作或提前触发')
            marks = []
            for m in motion.markers:
                since = cue_time(m.cue)
                until = cue_time(m.until) if m.until else duration
                if since < at or until <= since or not (0<=m.at[0]<cw and 0<=m.at[1]<ch):
                    raise ValueError('标记的显隐顺序或位置无效')
                marks.append((since,until,m.at[0]*w/cw-x,m.at[1]*h/ch-y))
            self.layers.append(_Layer(tile,(x,y),(motion.offset[0]*w/cw,motion.offset[1]*h/ch),
                                      motion,at,settle_at,travel_seconds, marks))

    def frame(self, i):
        t = i/FPS
        frame = self.plate.copy()
        for layer in self.layers:
            if t < layer.start: continue
            a = _ease((t-layer.start)/layer.motion.seconds)
            scale = 1+(layer.motion.scale_from-1)*(1-a)
            dims = tuple(max(1,round(d*scale)) for d in layer.tile.size)
            left = 1-_ease((t-layer.settle_at)/layer.travel_seconds)
            pos = tuple(round(x+delta*left+(d-n)/2)
                        for x,delta,d,n in zip(layer.anchor,layer.offset,layer.tile.size,dims))
            tile = layer.tile if dims==layer.tile.size else layer.tile.resize(dims,Image.Resampling.LANCZOS)
            alpha = tile.getchannel('A')
            if a < 1: alpha = alpha.point([round(v*a) for v in range(256)])
            frame.paste(tile,pos,alpha)
            for start,end,x,y in layer.markers:
                if start <= t < end:
                    ease = min(_ease((t-start)/.3),_ease((end-t)/.25))
                    height = round(self.line_height*ease)
                    cx,cy = round(pos[0]+x*dims[0]/layer.tile.width),round(pos[1]+y*dims[1]/layer.tile.height)
                    if height:
                        ImageDraw.Draw(frame).rounded_rectangle((cx-self.line_width,cy-height,cx+self.line_width,cy+height),
                                                               radius=self.line_width,fill='#59decf')
        opacity = min(_ease(t/self.fade_in) if self.fade_in else 1,
                      _ease(((self.count-1-i)/FPS)/self.fade_out) if self.fade_out else 1)
        return Image.blend(self.flat,frame,opacity) if opacity<1 else frame


class _ProofSheet:
    """Select motion beats (not four equally spaced holds). Tap encoding frames only."""
    def __init__(self, scene):
        last = scene.count-1
        first = math.ceil(min(scene.fade_in*FPS,last*.35))
        end = math.floor(last-min(scene.fade_out*FPS,last*.35))
        end = max(first,end)
        candidates = {first,end}
        for layer in scene.layers:
            beats = [layer.start+layer.motion.seconds*.55,layer.start+layer.motion.seconds]
            if layer.motion.settle:
                beats.append(layer.settle_at+layer.travel_seconds*.55)
            beats.extend((a+b)/2 for a,b,_,_ in layer.markers)
            candidates.update(min(end,max(first,round(t*FPS))) for t in beats)
        ordered = sorted(candidates)
        self.indices = frozenset(ordered if len(ordered)<=4 else
                                 [ordered[round(i*(len(ordered)-1)/3)] for i in range(4)])
        self._seen = set()
        self.tiles = []

    def accept(self, index, frame):
        # Never mutate the frame handed to FFmpeg; resize directly (no extra HD copy).
        k=min(1.,480/max(frame.size))
        size=tuple(max(1,round(n*k)) for n in frame.size)
        small=frame.resize(size,Image.Resampling.LANCZOS)
        digest=hashlib.sha256(small.tobytes()).digest()
        if digest not in self._seen:
            self._seen.add(digest)
            self.tiles.append((index,small))

    def save(self, path):
        if not self.tiles: raise RuntimeError('缺少可审核的关键帧')
        n=len(self.tiles)
        w,h=self.tiles[0][1].size
        cols=min(n,2)
        sheet=Image.new('RGB',(cols*w,math.ceil(n/cols)*(h+20)),'#101820')
        draw=ImageDraw.Draw(sheet)
        times=[]
        for i,(index,tile) in enumerate(self.tiles):
            x,y=(i%cols)*w,(i//cols)*(h+20)
            sheet.paste(tile,(x,y))
            t=round(index/FPS,6)
            times.append(t)
            draw.text((x+3,y+h+2),f'{t:.3f} s',fill='white')
        sheet.save(path,format='JPEG',quality=89)
        return times


def _encode_frames(cmd, scene, deadline, proof=None):
    # A blocking pipe write can outlive ordinary loop-based timeout checks. Kill
    # ffmpeg from an independent timer as well; EOF then unblocks the writer.
    proc = subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    expired = threading.Event()

    def terminate():
        expired.set()
        if proc.poll() is None: proc.kill()

    watchdog = threading.Timer(max(.001,deadline-time.monotonic()),terminate)
    watchdog.daemon = True
    watchdog.start()
    try:
        for i in range(scene.count):
            if expired.is_set() or time.monotonic()>=deadline: raise TimeoutError('单镜本地渲染超时')
            frame=scene.frame(i)
            proc.stdin.write(frame.tobytes())
            if proof is not None and i in proof.indices: proof.accept(i,frame)
        proc.stdin.close()
        proc.wait(timeout=max(.001,deadline-time.monotonic()))
        if expired.is_set(): raise TimeoutError('单镜本地渲染超时')
        if proc.returncode: raise RuntimeError('FFmpeg 无法编码单镜，请检查本地编码器与磁盘空间')
    except (BrokenPipeError,OSError) as exc:
        if expired.is_set() or time.monotonic()>=deadline: raise TimeoutError('单镜本地渲染超时') from exc
        raise RuntimeError('FFmpeg 编码中断，请检查本地编码器与磁盘空间') from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError('单镜本地渲染超时') from exc
    finally:
        watchdog.cancel()
        watchdog.join()
        if proc.poll() is None: proc.kill()
        proc.wait()
        if not proc.stdin.closed:
            try: proc.stdin.close()
            except OSError: pass


def _audio_length(path):
    """Verify duration without decoding all samples (incl. float and extensible WAV)."""
    with path.open('rb') as stream:
        head = stream.read(12)
        if head[:4] != b'RIFF' or head[8:] != b'WAVE': raise ValueError('配音必须是本地 WAV')
        end = struct.unpack_from('<I',head,4)[0] + 8
        if end > path.stat().st_size: raise ValueError('配音 WAV 不完整')
        rate = align = 0
        for _ in range(2048):
            if stream.tell()+8 > end: break
            name, size = struct.unpack('<4sI',stream.read(8))
            pos = stream.tell()
            if pos+size > end: raise ValueError('配音 WAV 数据不完整')
            if name == b'fmt ' and size >= 16:
                codec,channels,rate,_,align,bits = struct.unpack('<HHIIHH',stream.read(16))
                if codec not in (1,3,0xfffe) or not (1<=channels<=32 and 1<=rate<=384000 and
                                                      bits in (8,16,24,32,64) and align==channels*(bits//8)):
                    raise ValueError('仅支持 PCM、浮点或兼容的未压缩 WAV')
                if codec == 0xfffe:
                    fields = stream.read(24) if size>=40 else b''
                    if (len(fields)!=24 or fields[8:12] not in (b'\x01\0\0\0',b'\x03\0\0\0') or
                            fields[12:] != bytes.fromhex('00001000800000aa00389b71')):
                        raise ValueError('未支持的扩展 WAV 编码')
            if name == b'data' and rate and align:
                if size % align: raise ValueError('配音 WAV 采样不完整')
                return size/align/rate
            stream.seek(pos+size+(size%2))
    raise ValueError('找不到完整的 WAV 音轨')


def render_preview(*,plan:VibePlan,manifest:dict,assets:dict[str,Path],cues:dict[str,float],audio:Path,
                   start:float,duration:float,output_dir:Path,ffmpeg:str,timeout:float=150) -> dict:
    """Render narrated and silent clips plus a contact sheet in an empty staging directory."""
    if not math.isfinite(start) or start<0 or not .1<=duration<=MAX_SECONDS or not 0<timeout<=300:
        raise ValueError('单镜时段或渲染时限不合法')
    if not audio.is_file(): raise ValueError('未找到最终配音')
    if start+duration > _audio_length(audio)+.001: raise ValueError('最终配音未覆盖整个镜头')
    if not output_dir.is_dir() or any(output_dir.iterdir()): raise ValueError('目标必须是独立空目录')
    began = time.monotonic()
    scene = _Scene(plan,manifest,assets,cues,duration)
    deadline = began+timeout
    silent, preview, sheet = output_dir/'silent.mp4', output_dir/'preview.mp4', output_dir/'proof.jpg'
    proof = _ProofSheet(scene)
    common = [str(ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-n']
    w,h = scene.size
    try:
        _encode_frames(common+['-f','rawvideo','-pixel_format','rgb24','-video_size',f'{w}x{h}',
                               '-framerate',str(FPS),'-i','pipe:0','-an','-threads','4','-c:v','libx264',
                               '-crf','17','-preset','veryfast','-pix_fmt','yuv420p','-movflags','+faststart',str(silent)],
                       scene,deadline,proof)
        if time.monotonic()>=deadline: raise TimeoutError('单镜本地渲染超时')
        proof_times=proof.save(sheet)
        subprocess.run(common+['-i',str(silent),'-ss',f'{start:.6f}','-t',f'{duration:.6f}',
                               '-protocol_whitelist','file','-f','wav','-i',str(audio),'-map','0:v:0','-map','1:a:0',
                               '-c:v','copy','-c:a','aac','-b:a','192k','-movflags','+faststart',str(preview)],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=max(.001,deadline-time.monotonic()))
        if not all(p.is_file() and p.stat().st_size for p in (silent,preview,sheet)):
            raise RuntimeError('FFmpeg 没有输出完整文件')
    except BaseException as exc:
        for p in (silent,preview,sheet): p.unlink(missing_ok=True)
        if isinstance(exc,subprocess.TimeoutExpired): raise TimeoutError('本地封装预览超时') from exc
        if isinstance(exc,subprocess.CalledProcessError): raise RuntimeError('FFmpeg 无法封装本镜配音') from exc
        raise
    return dict(width=w,height=h,fps=FPS,frames=scene.count,duration_seconds=duration,proof_times=proof_times,
                render_seconds=round(time.monotonic()-began,3))
