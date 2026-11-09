"""Remove only orphaned standalone illustration labels, never factual text."""
import copy

def clean_illustration_labels(scene):
    result = copy.deepcopy(scene)
    if not isinstance(result, dict):
        return result
    stages = result.get('stages', [])
    if any(stage.get('visual') or any(item.get('asset_id') for item in stage.get('items', [])) for stage in stages):
        return result
    labels = {'医学示意', '医学示意图', '示意图', '仅供示意'}
    targets = [(result, ('subtitle',))] + [(stage, ('eyebrow', 'footer')) for stage in stages]
    targets += [(item, ('note',)) for stage in stages for item in stage.get('items', [])]
    for obj, fields in targets:
        for field in fields:
            if isinstance(obj.get(field), str) and obj[field].strip() in labels:
                obj[field] = ''
    return result
