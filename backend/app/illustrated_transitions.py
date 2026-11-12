"""Local-only, project-wide image-video entrance selection."""
from typing import Literal

IllustratedTransition = Literal['none','layered','brush','dissolve','black','white','left','right','up','down']
KINDS={'none','layered','brush','dissolve','black','white','left','right','up','down'}

def normalize(value):
    value=value or 'none'
    if value not in KINDS:raise ValueError('不支持的图文入场转场')
    return value

def xfade(kind):
    kind=normalize(kind)
    names={'dissolve':'fade','black':'fadeblack','white':'fadewhite',
           'left':'coverright','right':'coverleft','up':'coverdown','down':'coverup'}
    if kind in names:return 'transition='+names[kind]
    if kind=='brush':
        row='floor(Y/H*8)';progress=f'((1-P)*8-{row})'
        mask=f'if(eq(mod({row},2),0),lte(X/W,{progress}),gte(X/W,1-{progress}))'
        return f"transition=custom:expr='if({mask},B,A)'"
    if kind=='layered':
        # Simple staggered tiles, not AI planning or content extraction.
        index='(floor(Y/H*3)*4+floor(X/W*4))'
        alpha=f'clip((1-P)*2-{index}/12,0,1)'
        return f"transition=custom:expr='A*(1-{alpha})+B*{alpha}'"
    raise ValueError('无转场使用直接拼接')
