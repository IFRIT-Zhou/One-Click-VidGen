"""Compatibility adapter; private instructions live in an optional data plugin."""
from .director_extensions import get_profile

MEDICAL_PAPER = 'medical_paper'
MEDICAL_DESIGN_FIELDS = ('medical_evidence', 'medical_scale', 'medical_change',
                         'medical_camera', 'medical_reference', 'medical_labels')

def is_medical(context):
    return (context.get('video_direction') or {}).get('dynamic_text_mode') == MEDICAL_PAPER

def _profile():
    profile = get_profile(MEDICAL_PAPER)
    if profile is None:
        raise ValueError('本任务所需的可选导演插件未安装或已禁用；已有素材不受影响，请启用原插件或明确选择其他规划方式。')
    return profile

def medical_contract(context, stage, *, include_common=True):
    if not is_medical(context):
        return ''
    profile = _profile()
    return (profile['common'] if include_common else '') + profile['stages'].get(stage, '')

def medical_example(kind):
    return _profile()[kind + '_example']

def medical_design_issues(context, row):
    if not is_medical(context):
        return []
    _profile()
    design = row.get('visual_design')
    return [f'医学设计缺少 {key}（请填写可执行的简短设计依据）'
            for key in MEDICAL_DESIGN_FIELDS
            if not isinstance(design, dict) or not isinstance(design.get(key), str) or not design[key].strip()]
