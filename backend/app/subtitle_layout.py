"""Shared canvas and subtitle geometry for browser, libass and previews."""
import html
import json
import os
import re
import math
from functools import lru_cache
from pathlib import Path


def normalize(request=None):
    request = request if isinstance(request, dict) else {}
    portrait = request.get('video_orientation') == 'portrait'
    saved = request.get('subtitle_layouts') or {}
    saved = saved if isinstance(saved, dict) else {}
    values = saved.get('portrait' if portrait else 'landscape') or {}
    values = values if isinstance(values, dict) else {}
    def number(key, default, low, high):
        try:
            value = float(values.get(key, default))
            return max(low, min(high, value)) if math.isfinite(value) else default
        except (ValueError, TypeError):
            return default
    def color(key, default):
        value = str(values.get(key, default))
        return value if re.fullmatch(r'#[0-9a-fA-F]{6}', value) else default
    font = ' '.join(re.sub(r'[^\w\s\-\u3400-\u9fff]', '', str(values.get('font') or 'Microsoft YaHei')).split())[:100]
    return dict(width=1080 if portrait else 1920, height=1920 if portrait else 1080,
                portrait=portrait, font=font, size=number('size',56 if portrait else 36,20,100),
                position=number('position',75 if portrait else 95,10,96),
                width_percent=number('width_percent',78 if portrait else 92,35,95),
                max_chars=int(number('max_chars',14 if portrait else 44,6,60)),
                color=color('color','#ffffff'), outline_color=color('outline_color','#000000'),
                outline=number('outline',2,0,8), background=bool(values.get('background',True)),
                opacity=number('opacity',0.75,0,1))


def from_env():
    try:
        return normalize(json.loads(os.getenv('OCV_PRESENTATION_JSON','{}')))
    except (ValueError,TypeError):
        return normalize()


def presentation_env(request):
    data = {key: request.get(key) for key in ('video_orientation','subtitle_layouts')}
    result = {'OCV_PRESENTATION_JSON':json.dumps(data,ensure_ascii=False)}
    if data['video_orientation'] == 'portrait':
        result['RUNNINGHUB_TARGET_RATIO'] = '9:16'
    return result


def wrap(text, layout):
    text = re.sub(r'\s+', ' ', text).strip()
    # Respect both the requested character count and the physical text width.
    limit = max(6,min(layout['max_chars'],int(layout['width']*layout['width_percent']/100/layout['size'])))
    return '\n'.join(text[i:i+limit] for i in range(0,len(text),limit))


def css(layout):
    p=layout
    background=f"rgba(7,24,52,{p['opacity']})" if p['background'] else 'transparent'
    return f'''html,body{{width:{p['width']}px!important;height:{p['height']}px!important}}
    #subtitle-overlay{{top:{p['position']}%!important;left:50%!important;width:{p['width_percent']}%!important;height:auto!important;transform:translate(-50%,-50%);padding:0!important;overflow:visible!important}}
    .subtitle-inner{{font-family:"{p['font']}","Microsoft YaHei",sans-serif!important;font-size:{p['size']}px!important;line-height:1.2!important;color:{p['color']}!important;white-space:pre-wrap;overflow-wrap:anywhere;max-height:none!important;max-width:100%!important;background:{background}!important;-webkit-text-stroke:{p['outline']*2}px {p['outline_color']};paint-order:stroke fill;padding:5px 12px!important}}
    ''' + ('.poster-item{left:0!important;top:0!important;width:100%!important;height:100%!important}' if p['portrait'] else '.poster-item{left:2.1875%!important;top:5%!important;width:95.625%!important;height:85%!important}')


def apply_html(page, layout):
    page=re.sub(r'<style id="ocv-presentation">.*?</style>', '', page, flags=re.DOTALL)
    page=re.sub(r'data-width="\d+"',f'data-width="{layout["width"]}"',page)
    page=re.sub(r'data-height="\d+"',f'data-height="{layout["height"]}"',page)
    page=page.replace("lines.slice(2).join(' ')", "lines.slice(2).join('\\n')")
    return page.replace('</head>','<style id="ocv-presentation">'+css(layout)+'</style></head>',1)


@lru_cache(maxsize=128)
def ass_font_scale(font):
    """libass sizes by font ascent/descent, CSS by em; compensate installed fonts."""
    if os.name != 'nt':
        return 1.0
    try:
        import winreg
        from PIL import ImageFont
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')
            except OSError:
                continue
            with key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    name, filename, _ = winreg.EnumValue(key, index)
                    families = name.split('(', 1)[0].strip().split(' & ')
                    if font.casefold() not in [family.casefold() for family in families]:
                        continue
                    face = ImageFont.truetype(str(filename), 1000)
                    ascent, descent = face.getmetrics()
                    return max(0.75, min(2.0, (ascent + descent) / 1000))
    except (ImportError, OSError, ValueError):
        pass
    return 1.0


def srt_cues(path):
    text=Path(path).read_text(encoding='utf-8-sig')
    result=[]
    for block in re.split(r'\n\s*\n',text.strip()):
        lines=block.splitlines()
        for index,line in enumerate(lines):
            if '-->' in line:
                start,end=line.split('-->')
                result.append((start.strip(),end.strip(),' '.join(lines[index+1:])))
                break
    return result


def styled_srt(source, target, layout):
    cues=srt_cues(source)
    target.write_text('\n\n'.join(f'{i}\n{a} --> {b}\n{wrap(t,layout)}' for i,(a,b,t) in enumerate(cues,1))+'\n',encoding='utf-8')
    return target


def write_ass(source, target, layout):
    p=layout
    def col(value,alpha=0):
        return f'&H{alpha:02X}'+value[5:7]+value[3:5]+value[1:3]
    def stamp(value):
        return value.replace(',', '.')[:-1]
    header=f'''[Script Info]
ScriptType: v4.00+
PlayResX: {p['width']}
PlayResY: {p['height']}
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{p['font']},{p['size']*ass_font_scale(p['font']):.3f},{col(p['color'])},&H000000FF,{col(p['outline_color'])},&HFF000000,-1,0,0,0,100,100,0,0,1,{p['outline']},0,5,0,0,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    events=[]
    x=p['width']/2;y=p['height']*p['position']/100
    for start,end,text in srt_cues(source):
        lines=wrap(text,p).splitlines()
        safe='\\N'.join(line.replace('\\','').replace('{','').replace('}','') for line in lines)
        if p['background']:
            half=min(p['width']*p['width_percent']/200,max(map(len,lines),default=1)*p['size']/2+12)
            height=len(lines)*p['size']*1.2+10
            drawing=f'm 0 0 l {half*2:.1f} 0 l {half*2:.1f} {height:.1f} l 0 {height:.1f}'
            tag=f'{{\\an7\\pos({x-half:.1f},{y-height/2:.1f})\\p1\\bord0\\shad0\\1c&H341807&\\1a&H{round((1-p["opacity"])*255):02X}&}}'
            events.append(f'Dialogue: 0,{stamp(start)},{stamp(end)},Default,,0,0,0,,{tag}{drawing}')
        events.append(f'Dialogue: 1,{stamp(start)},{stamp(end)},Default,,0,0,0,,{{\\pos({x:.1f},{y:.1f})}}{safe}')
    target.write_text(header+'\n'.join(events)+'\n',encoding='utf-8')
    return target
