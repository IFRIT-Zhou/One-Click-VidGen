"""Deterministic local entrance transitions; never generate or redraw content."""
import math
from typing import Literal
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

TransitionName = Literal['layered','brush','dissolve','black','white','left','right','up','down']

class Entrance(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: TransitionName = 'layered'
    seconds: float = Field(default=.8, ge=.1, le=3, allow_inf_nan=False)

def composite(previous, current, progress, kind):
    p=max(0.,min(1.,progress))
    if p>=1:return current
    if p<=0:return previous.copy()
    w,h=current.size
    if kind in ('layered','dissolve'):
        return Image.blend(previous,current,p)
    if kind in ('black','white'):
        flash=Image.new('RGB',current.size,'black' if kind=='black' else 'white')
        return Image.blend(previous,flash,p*2) if p<.5 else Image.blend(flash,current,(p-.5)*2)
    if kind in ('left','right','up','down'):
        frame=previous.copy()
        dx=round(w*(1-p)) * (-1 if kind=='left' else 1) if kind in ('left','right') else 0
        dy=round(h*(1-p)) * (-1 if kind=='up' else 1) if kind in ('up','down') else 0
        frame.paste(current,(dx,dy))
        return frame
    # A deterministic serpentine brush, with overlapping rounded strokes.
    mask=Image.new('L',(w,h),0);draw=ImageDraw.Draw(mask)
    rows=8;step=h/rows;stroke=max(3,math.ceil(step*1.35));travel=p*rows
    tip=(0,0)
    for row in range(min(rows,math.ceil(travel))):
        fraction=min(1.,travel-row)
        y=round((row+.5)*step)
        start=0 if row%2==0 else w
        end=round(w*fraction) if row%2==0 else round(w*(1-fraction))
        draw.line((start,y,end,y),fill=255,width=stroke)
        radius=stroke//2
        draw.ellipse((end-radius,y-radius,end+radius,y+radius),fill=255)
        tip=(end,y)
    frame=Image.composite(current,previous,mask)
    # Small code-drawn pencil follows the reveal; disappears before the hold.
    x,y=tip;d=max(8,round(min(w,h)*.025));pen=ImageDraw.Draw(frame)
    pen.line((x+d,y-d*2,x,y),fill='#efbb45',width=max(3,d//3))
    pen.polygon([(x,y),(x+d//3,y-d//5),(x+d//7,y-d//2)],fill='#383838')
    return frame

def prioritize_entrance(motions, elements, rows, duration):
    """At least 60% of layers AND their area settles by one second."""
    areas={e['asset_id']:max(0,(e['box'][2]-e['box'][0])*(e['box'][3]-e['box'][1])) for e in elements}
    ordered=sorted(motions,key=lambda m:areas.get(m['asset_id'],0),reverse=True)
    target=sum(areas.values())*.6;covered=0;chosen=[]
    for motion in ordered:
        chosen.append(motion);covered+=areas.get(motion['asset_id'],0)
        if len(chosen)>=math.ceil(len(motions)*.6) and covered>=target:break
    finish=min(1.,duration*.8)
    for index,m in enumerate(chosen):
        seconds=min(float(m.get('seconds',.55)),finish*.75)
        at=(finish-seconds)*index/max(1,len(chosen)-1)
        m.update(cue=rows[0]['slide_id'],delay=round(at-rows[0]['at'],4),seconds=round(seconds,4))
        m.pop('settle',None)
    return motions
