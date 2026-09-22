"""Shared scene/state contract for video image and motion finalizers.

Beats describe phases within ONE subtitle-bound shot, not extra timeline cuts.
This module checks data and explicit prompt handoffs, not artistic correctness.
"""
import re


def normalize_motion_plan(value):
    if value is None or value == {}:
        return {}
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] not in (1, 2):
        raise ValueError('动态阶段方案版本或格式无效')

    def string(item, limit, label):
        if not isinstance(item, str) or not item.strip() or len(item) > limit:
            raise ValueError(f'动态阶段方案的{label}为空、格式无效或过长')
        return item.strip()

    participants = value.get('participants')
    if not isinstance(participants, list) or len(participants) > 12:
        raise ValueError('动态阶段方案主体列表无效')
    participants = [string(name, 160, '主体名称') for name in participants]
    if len(set(participants)) != len(participants):
        raise ValueError('动态阶段方案主体名称重复')

    def normalize_texts(raw_texts, owners, limit, label):
        if not isinstance(raw_texts, list) or len(raw_texts) > limit:
            raise ValueError(f'{label}的短文字列表无效')
        texts = []
        for entry in raw_texts:
            if not isinstance(entry, dict):
                raise ValueError(f'{label}文字格式无效')
            text = {key: string(entry.get(key), size, name) for key, size, name in (
                ('text', 80, '短文字'), ('owner', 160, '文字归属'), ('container', 80, '文字容器'))}
            if text['owner'] not in owners and text['owner'] != '画面标注':
                raise ValueError(f'{label}文字归属不在主体列表中')
            if text in texts:
                raise ValueError(f'{label}的文字记录重复')
            texts.append(text)
        return texts

    raw_beats = value.get('beats')
    if not isinstance(raw_beats, list) or not 1 <= len(raw_beats) <= 6:
        raise ValueError('动态阶段方案需要1～6个连续阶段')
    reference_beat = value.get('reference_beat')
    if type(reference_beat) is not int or not 1 <= reference_beat <= len(raw_beats):
        raise ValueError('核心参考图对应的阶段编号无效')
    beats = []
    for raw in raw_beats:
        if not isinstance(raw, dict):
            raise ValueError('动态阶段格式无效')
        texts = normalize_texts(raw.get('texts'), participants, 4, '动态阶段')
        beats.append({'action': string(raw.get('action'), 4000, '可见变化'), 'texts': texts})
    plan = {'version': value['version'], 'scene_anchor': string(value.get('scene_anchor'), 2000, '场景基础'),
            'participants': participants, 'beats': beats, 'reference_beat': reference_beat,
            'reference_visual': string(value.get('reference_visual'), 6000, '参考画面状态')}
    if plan['version'] == 2:
        reference_participants = value.get('reference_participants')
        if not isinstance(reference_participants, list) or len(reference_participants) > 12:
            raise ValueError('核心参考图主体列表无效')
        reference_participants = [string(name, 160, '核心图主体名称') for name in reference_participants]
        if len(set(reference_participants)) != len(reference_participants) or any(name not in participants for name in reference_participants):
            raise ValueError('核心参考图主体必须是本镜主体的不重复子集')
        plan['reference_participants'] = reference_participants
        # The reference is a spatial composition, not a frozen beat. Its text
        # can cover multiple phases or only a subset; neither side is a union.
        plan['reference_texts'] = normalize_texts(value.get('reference_texts'), reference_participants, 12, '核心参考图')
    return plan


def render_motion_action(plan):
    """Build the visible action field from the plan instead of asking for a second version."""
    plan = normalize_motion_plan(plan)
    if not plan:
        return ''
    lines = [f"场景基础：{plan['scene_anchor']}"]
    for index, beat in enumerate(plan['beats'], 1):
        text_state = '；'.join(f"{row['owner']}的{row['container']}显示“{row['text']}”" for row in beat['texts'])
        lines.append(f"第{index}阶段：{beat['action']}\n当前可见文字：{text_state or '无'}。")
    lines.append('按阶段依次展开；前一阶段的文字若未在当前阶段列出，应先消失，不累计堆叠。')
    return '\n'.join(lines)


_BLANKET_NO_TEXT = re.compile(
    r'(?:(?:全片|全程|整个视频|整幅画面|整张图|所有阶段)?'
    r'(?:禁止(?:生成|出现|添加)?|不生成|不要(?:生成|出现|添加)|不得(?:生成|出现|添加)?)'
    r'(?:任何|所有)?(?:文字|文本)|'
    r'(?:全片|全程|整个视频|整幅画面|整张图|所有阶段)(?:没有|无)(?:任何|所有)?(?:文字|文本))'
    r'(?:[，,](?:默认)?静音)?'
)
_REMOVE_TEXT = re.compile(r'不要|不得|禁止|不再|不应|不能|不出现|不显示|不包含|移除|消除|去掉|删去|消失|删除|清除')


def _has_blanket_text_ban(prompt):
    # Only unqualified/global directives are contradictions. Local/temporal phrases
    # ("背景墙无文字", "最后一阶段没有文字") are legitimate, not global bans.
    for clause in re.split(r'[。；;\n！？!?]', prompt):
        clause = re.sub(r'【[^】]*】', '', clause).strip()
        if _BLANKET_NO_TEXT.fullmatch(clause):
            return True
    return False


def _requests_quoted_text(prompt, text):
    # Quoting an old label in a removal/negative instruction is NOT requesting it.
    pattern = r'[“"「『]' + re.escape(text) + r'[”"」』]'
    for clause in re.split(r'[。；;\n，,]', prompt):
        if re.search(pattern, clause) and not _REMOVE_TEXT.search(clause):
            return True
    return False


def restore_reference_draft(plan, visual_description):
    """Recover an accidental reference label without redesigning a valid scene."""
    plan = normalize_motion_plan(plan)
    if not plan or not re.fullmatch(r'(?:参考)?图\s*\d+[。.]?', plan['reference_visual']):
        return plan
    plan['reference_visual'] = visual_description.strip()
    if plan['version'] == 2 and not plan['reference_texts']:
        for beat in plan['beats']:
            for entry in beat['texts']:
                if (entry['owner'] in plan['reference_participants'] or entry['owner'] == '画面标注') and \
                        _requests_quoted_text(visual_description, entry['text']) and entry not in plan['reference_texts']:
                    plan['reference_texts'].append(dict(entry))
        # Preserve the schema bound even when several phases repeat different labels.
        plan['reference_texts'] = plan['reference_texts'][:12]
    return normalize_motion_plan(plan)


def prompt_plan_issues(prompt, plan, medium='image'):
    """Catch literal omissions/conflicts only; semantics still require human evaluation."""
    if medium not in ('image', 'video'):
        raise ValueError('未知提示词类型')
    plan = normalize_motion_plan(plan)
    if not plan:
        return []
    issues = []
    participants = plan.get('reference_participants', plan['participants']) if medium == 'image' else plan['participants']
    for name in participants:
        if name not in prompt:
            issues.append(f'缺少既定主体名称：{name}')
    if medium == 'image':
        texts = plan.get('reference_texts', plan['beats'][plan['reference_beat'] - 1]['texts'])
    else:
        texts = [row for beat in plan['beats'] for row in beat['texts']]
    for row in texts:
        for key, label in (('text', '短文字原文'), ('owner', '文字归属'), ('container', '文字容器')):
            if row[key] not in prompt:
                suffix = '' if key == 'text' else f'（对应短文字“{row["text"]}”）'
                issues.append(f'缺少{label}：{row[key]}{suffix}')
    if texts and _has_blanket_text_ban(prompt):
        issues.append('规划了画面短文字，却同时要求全面禁止文字')
    if medium == 'image':
        visible = {row['text'] for row in texts}
        for beat in plan['beats']:
            for row in beat['texts']:
                # A substring of an approved label is not an additional phase.
                quoted = _requests_quoted_text(prompt, row['text'])
                if row['text'] not in visible and not any(row['text'] in text for text in visible) and quoted:
                    label = '核心图混入未选用的阶段文字' if plan['version'] == 2 else '核心图混入其他阶段文字'
                    issues.append(f"{label}：{row['text']}")
    return list(dict.fromkeys(issues))
