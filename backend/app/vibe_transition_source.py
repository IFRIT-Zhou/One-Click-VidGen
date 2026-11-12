"""Resolve the previous adopted shot, not an unadopted preview."""
import subprocess
from PIL import Image, ImageOps
from fastapi import HTTPException

def source(project, shot_id):
    from . import vibe_preview as p
    record=p._read_json(p._safe(project,project/'record.json',file=True),32*1024**2)
    shots=record.get('shots',[])
    index=next((i for i,s in enumerate(shots) if s['id']==shot_id),-1)
    if index<0:raise HTTPException(404,'镜头不存在')
    if index==0:return {'first_shot':True},None
    previous=shots[index-1]
    video=previous.get('kind')=='video'
    relative=previous.get('video' if video else 'image')
    if not relative or previous.get('video_status' if video else 'image_status')!='completed' or previous.get('vibe_reserved'):
        raise HTTPException(409,'上一镜尚无可用于衔接的已完成画面，请先完成或采用上一镜；不会用未采用预览冒充正式画面')
    path=p._safe(project,project/relative,file=True)
    return dict(shot_id=previous['id'],kind='video' if video else 'static',
                duration=previous.get('duration'),sha256=p._stream_hash(path,512*1024**2)),path

def validate(project,shot_id,dependency):
    if dependency is not None and source(project,shot_id)[0]!=dependency:
        raise HTTPException(409,'上一镜采用画面或时长已变化，请重新生成本镜入场转场；已有素材保留')

def extract(project,shot_id,destination,size,dependency):
    from .pipeline import ffmpeg_binary
    from .video_export import _video_duration
    current,path=source(project,shot_id)
    if current!=dependency:raise HTTPException(409,'上一镜已变化，请重新预览转场')
    if path is None:return None
    if current['kind']=='video':
        length=_video_duration(path)
        at=max(0,min(length,float(current['duration']))-1/30)
        subprocess.run([ffmpeg_binary(),'-v','error','-nostdin','-ss',str(at),'-i',str(path),'-frames:v','1','-y',str(destination)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True,timeout=30,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        path=destination
    with Image.open(path) as image:
        ImageOps.fit(image.convert('RGB'),size,method=Image.Resampling.LANCZOS).save(destination)
    return destination
