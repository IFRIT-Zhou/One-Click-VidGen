"""Non-mutating editorial suggestions. The configured renderer is always the user's choice."""
from __future__ import annotations

import re

from fastapi import APIRouter, Request

from . import video_studio as studio
from .image_layer_packs import owned_project
from .vibe_preview import _read_json

router = APIRouter(prefix='/api/video-studio')

# Look at what the shot is meant to *show*. Narrating an operation is not a
# request to animate one: a chart about surgical outcomes is still a chart.
_EVIDENCE = re.compile(r'病理切片|组织切片|原文切片|染色切片|显微照片|镜下原图|病理原图|组织学原图|病理显微图')
_MOTION = re.compile(r'演示.{0,12}(?:术式|手术操作|切除过程|运动轨迹)|(?:手术刀|器械|机器人|机械臂).{0,10}(?:游动|爬行|进入|旋转|沿|切开|游走|夹闭|绕过|移动|切除)|(?:剥离|切开|缝合|夹闭|游离).{0,5}(?:血管|组织|肝|胃壁)|(?:肿瘤|病灶).{0,6}(?:逐渐|慢慢|逐步)(?:缩小|长大)|形变过程|立体旋转|对口型|lip[ -]?sync',re.I)
_LAYOUT = re.compile(r'流程图|柱状图|柱形图|条形图|折线图|森林图|散点图|饼图|表格|时间线|署名|研究人员|共同第一作者|发表出处|期刊|研究团队|分组比较|多因素模型|分母|入组流程|队列选择|指标面板|组织架构|步骤图|数据对比|信息卡|清单')
_METRICS = re.compile(r'百分之|\d(?:\.\d+)?\s*[%％]|比值比|置信区间|风险比|样本量|反应率|P\s*值',re.I)

def recommend_shot(shot, subtitles=()):
    def result(route, reason):
        return {'shot_id': shot.get('id', ''), 'route': route, 'reason': reason}
    design = shot.get('visual_design') if isinstance(shot.get('visual_design'), dict) else {}
    intent = str(shot.get('intent') or '')[:1500]
    prompt = str(shot.get('image_prompt') or '')[:4000]
    narration = ' '.join(str(c.get('text','') if isinstance(c,dict) else c)[:500] for c in subtitles)[:4000]
    visual = ' '.join((intent, str(design.get('visible_evidence') or '')[:500],prompt))
    if design.get('presenter_speaking') is True or shot.get('presenter_speaking') is True:
        return result('video', '本镜需要人物开口，对口型应交给视频模型。')
    if _EVIDENCE.search(visual):
        return result('static', '本镜用原始切片或显微照片作证据，建议保持原图，让观众看清细节。')
    if _MOTION.search(intent) or (_MOTION.search(visual+' '+str(shot.get('action') or '')[:1500]) and not _LAYOUT.search(intent)):
        return result('video', '本镜要表现连续动作或形变，视频模型比平面图层更合适。')
    if _LAYOUT.search(visual) or len(_METRICS.findall(narration)) >= 2:
        return result('vibe', '画面有可分开阅读的信息，适合跟随旁白依次呈现，保留原图文字和数值。')
    if shot.get('kind')=='video':
        return result('video', '现有方案按动态镜头设计，没有足够依据改成平面动效。')
    return result('static', '没有明确需要分步呈现的内容，建议沿用静态画面。')

@router.get('/{identity}/vibe-recommendations')
def recommendations(identity: str, request: Request):
    project = owned_project(request, identity)
    with studio.LOCK:
        record = _read_json(project/'record.json', 32*1024**2)
    by_id = {s.get('slide_id'):s for s in record.get('scenes',[]) if isinstance(s,dict)}
    items = []
    for shot in record.get('shots',[]):
        cues = [by_id[s] for s in (shot.get('slide_ids') or []) if s in by_id] or shot.get('source_subtitles') or []
        items.append(recommend_shot(shot, cues))
    return {'items':items, 'source':'local_rules', 'may_charge':False}
