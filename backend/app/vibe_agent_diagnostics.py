"""Read-only evidence and recovery hints, never automatic artwork edits."""
import json
from PIL import Image


def json_feedback(text):
    raw = text.strip()
    if raw.startswith('```') and '\n' in raw:
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        start, end = max(0, exc.pos-160), min(len(raw), exc.pos+160)
        return dict(kind='json_syntax', message=exc.msg, line=exc.lineno,
                    column=exc.colno, offset=exc.pos, excerpt=raw[start:end],
                    caret_offset=exc.pos-start,
                    next='先修复此字符位置附近的引号、逗号和括号配对；保持完整 tool/summary/arguments 对象。'
                         'subjects/texts/decorations 属于 arguments.inventory 内部，不要提前闭合 inventory；不要改写制作内容来修复语法。')
    return dict(kind='not_object' if not isinstance(value, dict) else 'tool_contract',
                next='返回一个完整 JSON 工具对象，并按具体字段路径修复契约。')


def alpha_evidence(path):
    with Image.open(path) as image:
        alpha = image.convert('RGBA').getchannel('A')
        histogram = alpha.histogram()
        count = image.width * image.height
        bbox = alpha.point(lambda value: 255 if value >= 10 else 0).getbbox()
        return dict(width=image.width, height=image.height,
                    alpha_min=alpha.getextrema()[0], alpha_max=alpha.getextrema()[1],
                    transparent_fraction=round(sum(histogram[:10])/count, 5),
                    opaque_fraction=round(sum(histogram[246:])/count, 5),
                    soft_alpha_fraction=round(sum(histogram[10:246])/count, 5),
                    visible_bbox_px=list(bbox) if bbox else None,
                    edge_contact={
                        'left': round(sum(alpha.crop((0,0,1,image.height)).histogram()[10:])/image.height,5),
                        'right': round(sum(alpha.crop((image.width-1,0,image.width,image.height)).histogram()[10:])/image.height,5),
                        'top': round(sum(alpha.crop((0,0,image.width,1)).histogram()[10:])/image.width,5),
                        'bottom': round(sum(alpha.crop((0,image.height-1,image.width,image.height)).histogram()[10:])/image.width,5)},
                    edge_note='各边非透明像素占比；接触裁片边界只提示可能裁切，不证明缺损或脸部破洞，须对照来源。',
                    interpretation='透明像素统计不等于边缘质量结论。透明区占比明显大于零时，不应仅凭局部蓝边断言整张没有抠图。'
                    '发丝色溢、半透明边缘、原图矩形裁切、代码新增卡片是不同问题；对照原裁片、prepared透明素材和当前成片同区域定位。'
                    '人物原照未包含的身体不能靠放大或遮罩恢复；不要反复加卡片/光晕掩盖未查明的原因。')


def iteration_guidance(events):
    window = []
    for event in reversed(events):
        if event['tool'] in {'prepare_subject', 'select_subject', 'set_inventory', 'edit_background', 'select_background', 'review_evidence', 'locate_elements'}:
            break
        window.append(event)
    previews = [event for event in window if event['tool'] == 'preview_scene']
    if len(previews) < 3:
        return None
    return dict(previews_since_material_or_evidence_change=len(previews), advisory_only=True,
                next='近期多轮仅改代码/看图。继续前先判断是否同一问题反复出现；可自主继续设计，但建议改变诊断方法：'
                '文字缺失先核对 inventory.texts 与 subjects[].embedded_text 的独立来源，别只反复拆分 Text 节点；'
                '人物边缘先看 material_status 的 alpha_evidence、prepared/original 真实绑定，再看当前图层 box/crop/fit、父容器裁切和代码背景。'
                '位置问题用 locate_elements/浏览器测量或 review_evidence；只修已证实的原因。若当前已经达标可 finish，无需为了次数继续修改。')
