"""Opt-in presenter performance, independent of visual/text direction mode."""
import copy

PRESENTER_CONTRACT = '''
【用户显式开启的讲解员模式，优先于默认静音/不要求口型规则】
video_direction.presenter 指定唯一讲解员参考素材，不要求每镜出现讲解员。
Agent 2 必须在 visual_design 输出 presenter_visible 和 presenter_speaking 两个布尔值。
只有该参考人物实际可见、且本镜由其直接讲述，才两项都为 true，并选择其 reference_id。
纯机制示意、风景、物品特写、只沿用人物画风、人物背影或闭嘴听众均不得标为开口讲解。
讲解镜头自然开口，口型、表情与手势跟随本镜参考配音，不能保持嘴闭合；其余人物不跟读。
本镜 source_subtitles 是台词内容，原配音决定发音、停顿和速度；不得改词、另造对白或上屏字幕。
这属于用户授权的讲解表演，不代表原文新增角色对话，不改写既有 speech_turns 归属。
Agent 3/5 根据已确认 presenter_speaking 安排讲话表演；禁止额外配音不等于禁止嘴部运动。
'''

def presenter_config(parameters, references):
    identity = parameters.get('presenter_reference_id') if parameters.get('presenter_mode') else ''
    reference = next((r for r in references if identity and (r.get('id') == identity or r.get('source_asset_id') == identity)), None)
    if identity and not reference:
        # Legacy dynamic imports renamed upload IDs to ref_01; stable labels
        # retain the mapping even before source_asset_id was stored explicitly.
        from .reference_materials import request_reference_catalog
        source = next((r for r in request_reference_catalog(parameters) if r['asset_id'] == identity), None)
        matches = [r for r in references if source and r.get('label') == source['label']]
        if len(matches) == 1:
            reference = matches[0]
    if not reference:
        return {}
    return {'reference_id': reference['id'], 'label': reference.get('label', ''),
            'description': reference.get('description', reference.get('note', reference.get('notes', '')))}

def presenter_contract(context):
    return PRESENTER_CONTRACT if context.get('video_direction', {}).get('presenter') else ''

def presenter_requested(parameters, shot, references=None):
    design = shot.get('visual_design') or {}
    identity = (presenter_config(parameters, references).get('reference_id') if references is not None
                else parameters.get('presenter_reference_id'))
    return bool(parameters.get('presenter_mode') and identity
                and identity in shot.get('reference_ids', [])
                and design.get('presenter_visible') is True and design.get('presenter_speaking') is True
                and shot.get('reference_audio_enabled', True) and shot.get('reference_audio_lipsync', True))

def audio_policy(parameters, shot, h3_agent, references=None):
    presenter = presenter_requested(parameters, shot, references)
    enabled = presenter or bool(parameters.get('comfyui_reference_audio') and not h3_agent
                                and shot.get('reference_audio_enabled', True))
    return enabled, bool(enabled and shot.get('reference_audio_lipsync', True))

def spoken_text(shot):
    return '\n'.join(str(row.get('text') or '') for row in shot.get('source_subtitles', []))

def with_optional_h3_audio(profile):
    """Add the same optional audio edge used by the existing H3 audio workflow."""
    profile = copy.deepcopy(profile)
    if (profile.get('mappings', {}).get('audio') or {}).get('node_id'):
        return profile
    graph = profile.get('workflow') or {}
    nodes = [node for node in graph.values() if node.get('class_type') == 'MiniMaxH3ReferenceToVideo']
    if not nodes:
        return profile
    identity = 'ocv_presenter_audio'
    if identity in graph:
        return profile
    graph[identity] = {'class_type': 'LoadAudio', 'inputs': {'audio': ''},
                       '_meta': {'title': 'OCV 可选讲解配音'}}
    for node in nodes:
        node['inputs']['ref_audios.ref_audio_0'] = [identity, 0]
    profile.setdefault('mappings', {})['audio'] = {'node_id': identity, 'input_name': 'audio'}
    return profile
