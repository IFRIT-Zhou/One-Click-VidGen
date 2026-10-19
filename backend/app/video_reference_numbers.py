"""Video inputs have a shot-local namespace: core=1, selected identities=2..9."""
import re

ANCHOR = re.compile(r'(?:图\s*|<Picture\s*)(\d+)(?:>)?\s*[（(]\s*([\w-]+)\s*[）)]', re.I)
NUMBER = re.compile(r'(?:图\s*|<Picture\s*)(\d+)', re.I)


def normalize_video_references(prompt, shot, references):
    """Identity-anchored references are safe to normalize repeatedly, including old projects.

    Bare numbers are already local. Never guess their identity from their magnitude.
    """
    ids = list(shot.get('reference_ids', shot.get('reference_image_ids', [])))
    catalog = {row['id']: row for row in references}
    prefix = f"镜头 {shot.get('id', '?')}："
    def replace(match):
        identity = match.group(2)
        if identity not in catalog or identity not in ids:
            raise ValueError(prefix + f'视频参考素材 {identity} 不存在或未绑定，请恢复对应素材')
        number = ids.index(identity) + 2
        return f'图{number}（{identity}）'
    result = ANCHOR.sub(replace, str(prompt or ''))
    for match in NUMBER.finditer(result):
        number = int(match.group(1))
        if not 1 <= number <= len(ids) + 1 or number > 9:
            raise ValueError(prefix + f'视频图{number}没有对应的单镜输入；图1为核心图。'
                             '若使用项目图号，请补充素材 ID（如 图3（ref_03）），不能推测素材身份')
    return result
