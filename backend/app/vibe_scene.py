"""Declarative, model-directed scene animation; never executes model code.

Unlike source-layer reveals, this scene can lay out native typography, diagrams,
tables, plots and original evidence images on a new canvas. Asset IDs are resolved
by the application. All timestamps remain anchored to the saved subtitle clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Annotated, Literal

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .vibe_render import FPS, MAX_SECONDS, _audio_length, _ease, _encode_frames, _ProofSheet

RENDERER_VERSION = 'ocv-vibe-scene-1'
Colour = Annotated[str, Field(pattern=r'^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$')]
Finite = Annotated[float, Field(allow_inf_nan=False)]
Point = tuple[Finite, Finite]


class SceneElement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    type: Literal['text','image','rect','ellipse','line','polyline','number'] = Field(
        description='One independent flat element, not a group/container. line requires exactly 2 points; polyline requires 2–512 points. Text and image elements are separate siblings and do not inherit a rectangle\'s position, timing, style, or animation.')
    role: Literal['auto','background','content'] = Field(default='auto',
        description='background marks decorative backplates only; they do not satisfy the visible-content check. content marks meaningful typography, imagery or vector diagrams/charts. auto preserves old plans and treats near-full-canvas rectangles as background; set content explicitly for a meaningful large rectangle.')
    box: tuple[Finite, Finite, Finite, Finite] = Field(
        description='[x, y, width, height], NOT [left, top, right, bottom]. Canvas coordinates; width/height >=1 and the entire box must fit inside canvas. Each element has its own box, including text/images drawn over a card.')
    cue: str | None = Field(default=None, max_length=128)
    at: float = Field(default=0, ge=-10, le=60, allow_inf_nan=False,
                      description='Seconds from cue start; without cue, seconds from shot start')
    end: float | None = Field(default=None, ge=0, le=60, allow_inf_nan=False,
                             description='Absolute shot-relative end; omitted means shot end')
    enter: float = Field(default=.65, ge=0, le=5, allow_inf_nan=False)
    exit: float = Field(default=.3, ge=0, le=5, allow_inf_nan=False)
    motion: Literal['none','fade','slide_left','slide_right','slide_up','scale','grow','draw'] = Field(default='fade',
        description='draw is valid ONLY for line/polyline and reveals their points by path distance. For rect/ellipse/text/image/number use none, fade, slide_left, slide_right, slide_up, scale or grow. grow reveals the element from bottom to top; animations apply only to this element, not overlaid text/images.')
    fill: Colour = '#182F3B'
    stroke: Colour | None = None
    stroke_width: float = Field(default=2, ge=0, le=30, allow_inf_nan=False)
    radius: float = Field(default=0, ge=0, le=500, allow_inf_nan=False)
    text: str = Field(default='', max_length=2000)
    font_size: int = Field(default=42, ge=12, le=200)
    bold: bool = False
    align: Literal['left','center','right'] = 'left'
    asset_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_:-]{1,80}$')
    points: list[Point] = Field(default_factory=list, max_length=512,
        description='For line: exactly 2 [x,y] points. For polyline: 2–512 [x,y] points. All coordinates are LOCAL to box top-left, with 0<=x<=box.width and 0<=y<=box.height, NOT canvas coordinates. Example box=[100,200,300,1], points=[[0,0],[300,0]] draws a horizontal line. Omit for other types; no executable path data.')
    value: float | None = Field(default=None, allow_inf_nan=False, ge=-1e12, le=1e12)
    from_value: float = Field(default=0, allow_inf_nan=False, ge=-1e12, le=1e12)
    decimals: int = Field(default=0, ge=0, le=6)
    prefix: str = Field(default='', max_length=40)
    suffix: str = Field(default='', max_length=40)

    @model_validator(mode='after')
    def specific(self):
        x,y,w,h=self.box
        if w<1 or h<1: raise ValueError('元素宽高必须为正数')
        if self.type=='text' and not self.text.strip(): raise ValueError('文字元素不能为空')
        if self.type=='image' and not self.asset_id: raise ValueError('图片元素需要素材 ID')
        if self.type=='number' and self.value is None: raise ValueError('数字元素需要已核对的数值')
        if self.type in ('line','polyline'):
            if not 2<=len(self.points)<=512 or (self.type=='line' and len(self.points)!=2):
                raise ValueError('线条需要至少两个点；直线恰好两个点')
            if any(not (0<=a<=w and 0<=b<=h) for a,b in self.points):
                raise ValueError('折线坐标必须位于本元素框内')
        if self.motion=='draw' and self.type not in ('line','polyline'):
            raise ValueError('draw 仅用于沿路径绘制线条')
        return self


class ScenePlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    canvas: tuple[int,int] = (1920,1080)
    background: Colour = '#F5F2EA'
    fade_in: float = Field(default=.5, ge=0, le=3, allow_inf_nan=False)
    fade_out: float = Field(default=.3, ge=0, le=3, allow_inf_nan=False)
    elements: list[SceneElement] = Field(min_length=1, max_length=80,
                                        description='Back-to-front paint order; original images and native graphics may mix')

    @model_validator(mode='after')
    def geometry(self):
        w,h=self.canvas
        if not 160<=w<=1920 or not 90<=h<=1920 or w*h>1920*1080 or w%2 or h%2:
            raise ValueError('画布须为偶数尺寸，最多 1920×1080 总像素（可竖屏）')
        if len({e.id for e in self.elements})!=len(self.elements): raise ValueError('元素 ID 不能重复')
        if sum(len(e.text) for e in self.elements)>12000: raise ValueError('单镜文字过多，请精简说明文字')
        for e in self.elements:
            x,y,ew,eh=e.box
            if x<0 or y<0 or x+ew>w+.01 or y+eh>h+.01:
                raise ValueError(f'元素 {e.id} 超出画布')
        return self


@lru_cache(maxsize=128)
def _font(size,bold):
    windows=Path(os.environ.get('SystemRoot','C:/Windows'))/'Fonts'
    options=[windows/('msyhbd.ttc' if bold else 'msyh.ttc'),windows/'simhei.ttf',
             Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
             Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    for file in options:
        if file.is_file():
            return ImageFont.truetype(str(file),size)
    raise ValueError('缺少可用的本地字体，请安装微软雅黑或 Noto Sans CJK')


def _wrap(text,font,width):
    # Keep English words together where possible; Chinese can wrap by character.
    lines=[]
    for paragraph in text.split('\n'):
        if not paragraph: lines.append('');continue
        row=''
        for token in re.findall(r'[A-Za-z0-9]+(?:[./%+-][A-Za-z0-9]+)*|[^A-Za-z0-9]',paragraph):
            if font.getlength(row+token)<=width:
                row+=token;continue
            if row: lines.append(row.rstrip());row=''
            if font.getlength(token)<=width: row=token.lstrip();continue
            for ch in token:
                if row and font.getlength(row+ch)>width: lines.append(row);row=''
                row+=ch
        lines.append(row.rstrip())
    return lines


def _text_tile(text,size,font_size,bold,align,colour):
    w,h=size
    selected=None
    for fs in range(font_size,max(11,math.floor(font_size*.72))-1,-1):
        font=_font(fs,bold); lines=_wrap(text,font,max(1,w-4))
        height=math.ceil(fs*1.30)
        if len(lines)*height<=h+1 and all(font.getlength(line)<=w-4 for line in lines):
            selected=(font,lines,height);break
    if selected is None: raise ValueError('文字超出文本框，请增大文字区域或减少内容；未截断文字')
    font,lines,height=selected
    tile=Image.new('RGBA',size,(0,0,0,0));draw=ImageDraw.Draw(tile)
    for i,line in enumerate(lines):
        width=font.getlength(line)
        x=(w-width)/2 if align=='center' else w-width-2 if align=='right' else 2
        # anchor=lt keeps CJK glyphs inside the line rather than baseline offsets.
        draw.text((x,i*height+1),line,font=font,fill=colour,anchor='lt')
    return tile


def _numeric(e,progress):
    value=e.from_value+(e.value-e.from_value)*progress
    return f'{e.prefix}{value:.{e.decimals}f}{e.suffix}'


def _line_tile(e,size,progress=1):
    tile=Image.new('RGBA',size,(0,0,0,0));draw=ImageDraw.Draw(tile)
    points=e.points
    lengths=[math.dist(a,b) for a,b in zip(points,points[1:])]
    budget=sum(lengths)*progress
    visible=[points[0]]
    for a,b,length in zip(points,points[1:],lengths):
        if budget>=length:
            visible.append(b);budget-=length
        elif length:
            visible.append((a[0]+(b[0]-a[0])*budget/length,a[1]+(b[1]-a[1])*budget/length));break
    if len(visible)>=2 and progress>0:
        draw.line(visible,fill=e.stroke or e.fill,width=max(1,round(e.stroke_width)),joint='curve')
    return tile


@dataclass
class _Item:
    spec: SceneElement
    tile: Image.Image
    pos: tuple[int,int]
    start: float
    end: float
    enter: float
    exit: float


class _SceneStopped(Exception):
    """Do not use InterruptedError inside the encoder: it subclasses OSError."""


class Scene:
    def __init__(self,plan:ScenePlan,assets:dict,cues:dict,duration:float,cancelled=None):
        self.plan=plan;self.size=plan.canvas;self.duration=duration
        self.cancelled=cancelled
        self.fade_in=plan.fade_in;self.fade_out=plan.fade_out
        self.count=max(1,round(duration*FPS));self.items=[]
        self.flat=Image.new('RGB',self.size,plan.background)
        area=0
        for e in plan.elements:
            self.check_cancelled()
            at=e.at
            if e.cue is not None:
                if e.cue not in cues or isinstance(cues[e.cue],bool) or not math.isfinite(cues[e.cue]):
                    raise ValueError(f'元素 {e.id} 引用了不存在的字幕 {e.cue}')
                at+=cues[e.cue]
            end=duration if e.end is None else e.end
            if not 0<=at<end<=duration+.001: raise ValueError(f'元素 {e.id} 的显示时间超出本镜')
            if e.enter+e.exit>end-at+.001: raise ValueError(f'元素 {e.id} 的进出场超过显示时间')
            x,y,w,h=(round(v) for v in e.box);w=max(1,w);h=max(1,h)
            area+=w*h
            if area>12*self.size[0]*self.size[1]: raise ValueError('单镜图层面积过大，请合并背景装饰')
            tile=Image.new('RGBA',(w,h),(0,0,0,0));draw=ImageDraw.Draw(tile)
            if e.type=='image':
                if e.asset_id not in assets: raise ValueError(f'缺少素材 {e.asset_id}，不能用空白占位')
                file=Path(assets[e.asset_id])
                if not file.is_file() or file.stat().st_size>32*1024*1024: raise ValueError('图片文件无效或超过 32 MiB')
                with Image.open(file) as source:
                    if source.width*source.height>16_777_216 or source.format not in ('PNG','JPEG','WEBP') or getattr(source,'n_frames',1)!=1:
                        raise ValueError('不支持的图片格式或像素尺寸')
                    image=ImageOps.exif_transpose(source).convert('RGBA')
                image=ImageOps.contain(image,(w,h),Image.Resampling.LANCZOS)
                # Preserve straight-alpha RGBA; applying it as a paste mask here
                # would square alpha once more during final compositing.
                tile.paste(image,((w-image.width)//2,(h-image.height)//2))
            elif e.type=='text':
                try: tile=_text_tile(e.text,(w,h),e.font_size,e.bold,e.align,e.fill)
                except ValueError as exc: raise ValueError(f'元素 {e.id}: {exc}') from exc
            elif e.type=='number':
                # Validate both endpoints before encoding: counter cannot grow outside its box.
                try:
                    _text_tile(_numeric(e,0),(w,h),e.font_size,e.bold,e.align,e.fill)
                    tile=_text_tile(_numeric(e,1),(w,h),e.font_size,e.bold,e.align,e.fill)
                except ValueError as exc: raise ValueError(f'元素 {e.id}: {exc}') from exc
            elif e.type in ('line','polyline'): tile=_line_tile(e,(w,h))
            else:
                kwargs=dict(fill=e.fill,outline=e.stroke,width=max(1,round(e.stroke_width)))
                box=(0,0,w-1,h-1)
                if e.type=='ellipse': draw.ellipse(box,**kwargs)
                else: draw.rounded_rectangle(box,radius=min(e.radius,w/2,h/2),**kwargs)
            self.items.append(_Item(e,tile,(x,y),at,end,e.enter,e.exit))

    def check_cancelled(self):
        if self.cancelled and self.cancelled(): raise _SceneStopped('已停止本地动效渲染')

    def _is_background(self,item):
        if item.spec.role != 'auto':
            return item.spec.role == 'background'
        # Backward-compatible inference for old plans without semantic roles.
        # Do not discard bars, curves, diagram nodes or source images merely
        # because they are vector-only or have no associated text.
        _,_,w,h=item.spec.box
        return (item.spec.type=='rect' and w>=self.size[0]*.8 and h>=self.size[1]*.8
                and w*h>=self.size[0]*self.size[1]*.75)

    def frame(self,index,*,background_only=False):
        self.check_cancelled()
        t=index/FPS;frame=self.flat.copy()
        for item in self.items:
            if background_only and not self._is_background(item): continue
            if t<item.start or t>=item.end: continue
            e=item.spec
            progress=1 if not item.enter else _ease((t-item.start)/item.enter)
            alpha=1 if e.motion in ('none','draw','grow') else progress
            if item.exit: alpha*=min(1,_ease((item.end-t)/item.exit))
            if alpha<=0: continue
            tile=item.tile
            if e.type=='number' and progress<1:
                tile=_text_tile(_numeric(e,progress),tile.size,e.font_size,e.bold,e.align,e.fill)
            elif e.motion=='draw' and progress<1: tile=_line_tile(e,tile.size,progress)
            elif e.motion=='grow' and progress<1:
                height=round(tile.height*progress)
                if not height: continue
                cropped=Image.new('RGBA',tile.size,(0,0,0,0))
                cropped.paste(tile.crop((0,tile.height-height,tile.width,tile.height)),(0,tile.height-height));tile=cropped
            x,y=item.pos
            if e.motion=='slide_left': x-=round(min(self.size[0]*.04,100)*(1-progress))
            elif e.motion=='slide_right': x+=round(min(self.size[0]*.04,100)*(1-progress))
            elif e.motion=='slide_up': y+=round(min(self.size[1]*.04,70)*(1-progress))
            elif e.motion=='scale' and progress<1:
                scale=.9+.1*progress
                small=tile.resize((max(1,round(tile.width*scale)),max(1,round(tile.height*scale))),Image.Resampling.LANCZOS)
                x+=(tile.width-small.width)//2;y+=(tile.height-small.height)//2;tile=small
            mask=tile.getchannel('A')
            if alpha<1: mask=mask.point([round(v*alpha) for v in range(256)])
            frame.paste(tile,(x,y),mask)
        fade=min(_ease(t/self.fade_in) if self.fade_in else 1,
                 _ease(((self.count-1-index)/FPS)/self.fade_out) if self.fade_out else 1)
        return Image.blend(self.flat,frame,fade) if fade<1 else frame


class SceneProof(_ProofSheet):
    def __init__(self,scene):
        candidates={min(scene.count-1,round((scene.fade_in+.2)*FPS)),max(0,scene.count-1-round((scene.fade_out+.2)*FPS))}
        for item in scene.items:
            candidates.update(max(0,min(scene.count-1,round(t*FPS))) for t in [item.start+item.enter*.6,item.start+item.enter,(item.start+item.end)/2])
        ordered=sorted(candidates)
        self.indices=frozenset(ordered if len(ordered)<=6 else [ordered[round(i*(len(ordered)-1)/5)] for i in range(6)])
        self._seen=set();self.tiles=[]


def inspect_scene(scene):
    """Visible-content guard, independent of model self-review. Excludes planned fades."""
    begin=min(scene.duration*.2,scene.fade_in+.05)
    end=max(begin,scene.duration-min(scene.duration*.2,scene.fade_out+.05))
    step=.25
    times=[begin+i*step for i in range(max(1,math.floor((end-begin)/step)+1))]
    blank=longest=0.;ratios=[]
    for t in times:
        frame=scene.frame(min(scene.count-1,round(t*FPS))).resize((160,90),Image.Resampling.BILINEAR)
        # Compare against the actual decorated background, not only its base
        # colour. Otherwise a full-canvas card hides an empty eight-second intro.
        background=scene.frame(min(scene.count-1,round(t*FPS)),background_only=True).resize(frame.size,Image.Resampling.BILINEAR)
        diff=ImageChops.difference(frame,background).convert('L')
        hist=diff.histogram();ratio=sum(hist[14:])/(160*90);ratios.append(ratio)
        blank=blank+step if ratio<.001 else 0.;longest=max(longest,blank)
    if longest>=max(.75,min(1.5,scene.duration*.12)) or max(ratios,default=0)<.001:
        raise ValueError(f'动效有约 {longest:.2f} 秒没有可见内容，请提前呈现标题或主体，不能输出长时间空白')
    return {'max_blank_seconds':round(longest,3),'checked_frames':len(times)}


def _mux(cmd,deadline,cancelled):
    proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        while proc.poll() is None:
            if cancelled and cancelled(): raise _SceneStopped('已停止本地配音封装')
            remaining=deadline-time.monotonic()
            if remaining<=0: raise TimeoutError('本地配音封装超时')
            try: proc.wait(timeout=min(.15,remaining))
            except subprocess.TimeoutExpired: pass
        if proc.returncode: raise subprocess.CalledProcessError(proc.returncode,cmd)
    finally:
        if proc.poll() is None: proc.kill()
        proc.wait()


def render_scene(*,plan:ScenePlan,assets:dict[str,Path],cues:dict[str,float],audio:Path,
                 start:float,duration:float,output_dir:Path,ffmpeg:str,timeout:float=180,cancelled=None)->dict:
    if not math.isfinite(start) or start<0 or not .1<=duration<=MAX_SECONDS or not 0<timeout<=300:
        raise ValueError('单镜时段或渲染时限不合法')
    if not audio.is_file() or start+duration>_audio_length(audio)+.001:
        raise ValueError('最终配音未覆盖整个镜头')
    if not output_dir.is_dir() or any(output_dir.iterdir()): raise ValueError('目标必须是独立空目录')
    began=time.monotonic();deadline=began+timeout
    silent,preview,sheet=output_dir/'silent.mp4',output_dir/'preview.mp4',output_dir/'proof.jpg'
    detail_files={name:output_dir/f'detail_{name}.png' for name in ('early','middle','end')}
    outputs=(silent,preview,sheet,*detail_files.values())
    common=[str(ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-n']
    try:
        scene=Scene(plan,assets,cues,duration,cancelled);checks=inspect_scene(scene)
        if time.monotonic()>=deadline: raise TimeoutError('场景检查超时')
        proof=SceneProof(scene);w,h=scene.size
        _encode_frames(common+['-f','rawvideo','-pixel_format','rgb24','-video_size',f'{w}x{h}',
            '-framerate',str(FPS),'-i','pipe:0','-an','-threads','4','-c:v','libx264','-crf','17',
            '-preset','veryfast','-pix_fmt','yuv420p','-movflags','+faststart',str(silent)],scene,deadline,proof)
        scene.check_cancelled()
        proof_times=proof.save(sheet)
        # The contact sheet shows rhythm, but its small tiles cannot establish
        # spelling/data-label correctness. Keep exact, full-resolution frames,
        # including early content that may leave before the midpoint.
        detail_times={}
        for name,timestamp in [('early',min(duration*.25,max(.5,scene.fade_in+.2))),
                               ('middle',duration/2),('end',max(0,duration-scene.fade_out-.3))]:
            if time.monotonic()>=deadline: raise TimeoutError('核查帧输出超时')
            index=max(0,min(scene.count-1,round(timestamp*FPS)))
            scene.frame(index).save(detail_files[name],format='PNG')
            detail_times[name]=round(index/FPS,6)
        _mux(common+['-i',str(silent),'-ss',f'{start:.6f}','-t',f'{duration:.6f}',
            '-protocol_whitelist','file','-f','wav','-i',str(audio),'-map','0:v:0','-map','1:a:0',
            '-c:v','copy','-c:a','aac','-b:a','192k','-movflags','+faststart',str(preview)],
            deadline,cancelled)
        scene.check_cancelled()
        if not all(p.is_file() and p.stat().st_size for p in outputs): raise RuntimeError('本地编码未生成完整输出')
    except BaseException as exc:
        for p in outputs:p.unlink(missing_ok=True)
        if isinstance(exc,_SceneStopped): raise InterruptedError(str(exc)) from exc
        if isinstance(exc,subprocess.TimeoutExpired):raise TimeoutError('本地场景渲染超时') from exc
        if isinstance(exc,subprocess.CalledProcessError):raise RuntimeError('本地配音封装失败') from exc
        raise
    return dict(renderer=RENDERER_VERSION,width=w,height=h,fps=FPS,frames=scene.count,
                duration_seconds=duration,proof_times=proof_times,detail_times=detail_times,checks=checks,
                render_seconds=round(time.monotonic()-began,3))
