"""Shared pre-render check for label contrast in a validated layered poster.

Works on rendered-colour approximations, not on character names, asset IDs or
image bounding boxes alone. It never edits image bytes. It checks the settled
reading interval, not invisible text or transient positions during transitions.
This is an aid to readability, not segmentation, OCR or a collision solver.
"""
from copy import deepcopy
import math
import unicodedata
from functools import lru_cache

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageOps

VERSION = 'local-text-legibility-v1'
_LUT = np.arange(256, dtype=np.float32) / 255.
_LUT = np.where(_LUT <= .04045, _LUT / 12.92, ((_LUT + .055) / 1.055) ** 2.4)
_WEIGHTS = np.array((.2126, .7152, .0722))


def _lum(rgb):
    return _LUT[np.asarray(rgb, dtype=np.uint8)] @ _WEIGHTS


def _contrast(a, b):
    return (np.maximum(a, b) + .05) / (np.minimum(a, b) + .05)


def _rect(box, scale):
    x,y,w,h=box
    x1,y1=round(x*scale),round(y*scale)
    return x1,y1,max(x1+1,round((x+w)*scale)),max(y1+1,round((y+h)*scale))


def _text_roi(e, scale):
    # Approximate CSS advance incl. CJK full-width glyphs, not the much larger
    # allocated alignment box. Multiline/wrapped text checks its allocated width.
    x,y,w,h=e['box']; fs=e.get('font_size',42); text=e['text']; lh=fs*e.get('line_height',1.2)
    # Browser fitting can change wrapping and vertical placement. Conservatively
    # inspect the assigned region; the wash itself still follows actual glyphs.
    if e.get('min_font_size') is not None:
        return _rect(e['box'],scale)
    advance=sum(1.0 if unicodedata.east_asian_width(c) in ('W','F') else .56 for c in text)*fs
    advance+=max(0,len(text)-1)*e.get('letter_spacing',0)
    one='\n' not in text and advance<=w
    tw=min(w,max(fs*.35,advance)) if one else w
    dx=(w-tw)*{'left':0,'center':.5,'right':1}[e.get('align','left')]
    th=min(h,lh) if one else h
    dy=(h-th)*{'top':0,'middle':.5,'bottom':1}[e.get('valign','top')]
    return _rect([x+dx,y+dy,tw,th],scale)


def _project(e, s, files):
    x,y,r,b=_rect(e['box'],s); size=w,h=r-x,b-y
    typ=e['type']
    if typ=='image':
        with Image.open(files[e['asset_id']]) as raw:
            if raw.width*raw.height>32_000_000:
                raise ValueError('Layer image is too large for legibility analysis')
            src=ImageOps.exif_transpose(raw).convert('RGBA')
        cover=e.get('fit','contain')=='cover'
        if cover:
            img=ImageOps.fit(src,size,method=Image.Resampling.LANCZOS)
            left=0; visible=w
        else:
            ratio=min(w/src.width,h/src.height)
            fw=max(1,min(w,round(src.width*ratio))); fh=max(1,min(h,round(src.height*ratio)))
            visible=fw; left=(w-fw)//2
            img=Image.new('RGBA',size); img.paste(src.resize((fw,fh),Image.Resampling.LANCZOS),(left,(h-fh)//2))
        if e.get('edge_fade_right'):
            alpha=np.asarray(img.getchannel('A')).astype(float)
            mask=np.clip((left+visible-(np.arange(w)+.5))/(visible*e['edge_fade_right']),0,1)
            img.putalpha(Image.fromarray(np.rint(alpha*mask).astype('uint8')))
    else:
        img=Image.new('RGBA',size); draw=ImageDraw.Draw(img)
        sw=max(1,round(e.get('stroke_width',2)*s))
        args={'fill':e.get('fill','#D5B66E'),'outline':e.get('stroke') if e.get('stroke_width',2)>0 else None,'width':sw}
        if typ=='rect':
            draw.rounded_rectangle((0,0,w-1,h-1),radius=min(w/2,h/2,e.get('radius',0)*s),**args)
            if e.get('gradient'):
                radians=math.radians(e['gradient']['angle'])
                dx,dy=math.sin(radians),-math.cos(radians)
                yy,xx=np.mgrid[:h,:w]
                amount=np.clip(.5+((xx+.5-w/2)*dx+(yy+.5-h/2)*dy)/max(1e-6,abs(w*dx)+abs(h*dy)),0,1)[...,None]
                first=np.array(ImageColor.getrgb(e['fill']))
                last=np.array(ImageColor.getrgb(e['gradient']['color']))
                gradient=Image.fromarray(np.rint(first*(1-amount)+last*amount).astype('uint8')).convert('RGBA')
                gradient.putalpha(img.getchannel('A'));img=gradient
                if args['outline']:
                    ImageDraw.Draw(img).rounded_rectangle((0,0,w-1,h-1),radius=min(w/2,h/2,e.get('radius',0)*s),outline=args['outline'],width=sw)
        elif typ=='ellipse':
            draw.ellipse((0,0,w-1,h-1),**args)
        elif typ in ('line','polyline') and e.get('stroke_width',2)>0:
            draw.line([(round(px*s),round(py*s)) for px,py in e['points']],fill=e.get('stroke','#182F3B'),width=sw)
    if e.get('opacity',1)<1:
        img.putalpha(img.getchannel('A').point(lambda a:round(a*e['opacity'])))
    if e.get('rotation'):
        img=img.rotate(-e['rotation'],Image.Resampling.BICUBIC,expand=True)
        x-=(img.width-w)//2; y-=(img.height-h)//2
    return img,x,y


def _over(base, layer, roi):
    """Crop-first alpha composite; transparent contain gutters stay transparent."""
    img,l,t=layer; x,y,r,b=roi
    a=max(x,l); k=max(y,t); c=min(r,l+img.width); d=min(b,t+img.height)
    if c>a and d>k:
        tile=img.crop((a-l,k-t,c-l,d-t))
        base.alpha_composite(tile,dest=(a-x,k-y))


def _risk(back, ink, em):
    lum=_lum(back)
    low=_contrast(lum,_lum(ink))<3.0
    char=max(3,round(em*.9))
    # One partially illegible glyph matters even if most of a long line sits on
    # white. Ignore tiny straggler tiles instead of treating one pixel as failure.
    clusters=[float(low[y:y+char,x:x+char].mean()) for y in range(0,low.shape[0],char) for x in range(0,low.shape[1],max(1,char//2))
              if low[y:y+char,x:x+char].size>=char**2*.25]
    worst=max(clusters,default=0); frac=float(np.mean(low))
    # Contrast against the ink alone misses a blue-grey coat seam crossing
    # navy glyphs on an otherwise bright poster. Measure high-frequency
    # structure at glyph-stroke scale, not overall variance (which would
    # confuse a soft vignette/gradient with texture). Use perceptual lightness
    # so pale-on-dark and dark-on-pale designs behave similarly.
    light=np.sqrt(lum)
    gray=Image.fromarray(np.rint(light*255).astype('uint8'))
    blur=np.asarray(gray.filter(ImageFilter.GaussianBlur(max(.8,em*.10)))).astype(float)/255.
    edge=np.abs(light-blur)>.045
    edge_fraction=float(np.mean(edge))
    return max(frac/.08,worst/.21,edge_fraction/.045),frac,worst,edge_fraction


def _colour(base, raw_ink, theme):
    # Prefer the genuine *local* clean plate, not an average of the person's
    # coat, skin or tie. If its median is unsuitable try a percentile of the
    # plate, the design's fallback tone, then a contrast-corrected palette tone.
    arr=np.asarray(base).reshape(-1,3); ink=_lum(raw_ink)
    median=np.rint(np.median(arr,axis=0)).astype(np.uint8)
    if _contrast(_lum(median),ink)>=6.0: return median
    luminance=_lum(arr)
    for rank in (10,90):
        bound=np.percentile(luminance,rank)
        region=arr[luminance<=bound] if rank<50 else arr[luminance>=bound]
        sample=np.rint(np.median(region,axis=0)).astype(np.uint8)
        if _contrast(_lum(sample),ink)>=6.0: return sample
    palette=np.array(ImageColor.getrgb(theme))
    if _contrast(_lum(palette),ink)>=6.0: return palette
    dark=np.zeros(3); light=np.full(3,255)
    end=dark if _contrast(0,ink)>_contrast(1,ink) else light
    for blend in (.35,.55,.75,.9,1):
        c=np.rint(median*(1-blend)+end*blend).astype(np.uint8)
        if _contrast(_lum(c),ink)>=4.5: return c
    return end.astype(np.uint8)


def protect_text_layers(wrapper):
    """Return a protected copy and non-sensitive audit; no writes or network.

    Caller supplies a validated wrapper with authorized, local, bound assets.
    Work on a downsized canvas (long edge <= 600), cache projected layers, and
    sample up to three snapshots of each text's visible lifetime. Unresolved
    rotations of the *text* itself are flagged instead of guessing its ink area.
    """
    out=deepcopy(wrapper); plan=out['plan']; es=plan['elements']; dur=out['duration']
    scale=min(1.,600/max(plan['canvas'])); theme=plan.get('background','#F3EEE4')
    report={'version':VERSION,'protected':[],'labels':[]}
    @lru_cache(maxsize=80)
    def projected(i):
        return _project(es[i],scale,out['assets'])
    try:
        for i,e in enumerate(es):
            if e['type']!='text': continue
            row={'id':e['id']}; report['labels'].append(row)
            if e.get('readability_halo') or e.get('rotation') or not e.get('opacity',1):
                row['status']='existing' if e.get('readability_halo') else 'not_assessed'; continue
            ink=ImageColor.getrgb(e.get('fill','#182F3B')); em=e.get('font_size',42)*scale
            roi=_text_roi(e,scale); x,y,r,b=roi
            a=e.get('at',0); z=e.get('end',dur); settled=min(z,a+e.get('enter',.6))
            tail=max(settled,z-e.get('exit',0))
            times=sorted(set(min(z-1e-5,t) for t in (settled,(settled+tail)/2,max(settled,tail-.03))))
            samples=[]
            for t in times:
                pix=Image.new('RGBA',(r-x,b-y),theme); clean=pix.copy()
                for j,bg in enumerate(es[:i]):
                    if bg['type']=='text' or not bg.get('opacity',1) or not bg.get('at',0)<=t<bg.get('end',dur): continue
                    # Reading snapshots use the settled geometry of each already
                    # present layer (entrance paths not treated as stationary).
                    layer=projected(j); _over(pix,layer,roi)
                    if bg.get('role')=='background': _over(clean,layer,roi)
                visible=np.asarray(pix.convert('RGB')); base=np.asarray(clean.convert('RGB'))
                score,frac,worst,edges=_risk(visible,ink,em)
                samples.append((score,frac,worst,edges,base,visible))
            score,frac,worst,edges,base,visible=max(samples,key=lambda v:v[0])
            row.update(low_contrast_fraction=round(frac,4),worst_glyph_region=round(worst,4),interfering_edges=round(edges,4))
            if score<1:
                row['status']='clear'; continue
            color=_colour(base,ink,theme)
            # Substantial attenuation at the reading line, feathered by the
            # renderer; choose the weakest safe wash rather than blanking a face.
            for opacity in ((.82,.88,.92) if edges>=.045 else (.72,.8,.88,.92)):
                blended=np.rint(visible*(1-opacity)+color.astype(float)*opacity).astype(np.uint8)
                if float(np.percentile(_contrast(_lum(blended),_lum(ink)),2))>=4.5: break
            e['readability_halo']={'color':'#'+''.join(f'{int(v):02X}' for v in color),'opacity':opacity}
            row.update(status='protected',**e['readability_halo']); report['protected'].append(e['id'])
    finally:
        projected.cache_clear()
    return out,report
