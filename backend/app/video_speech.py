"""Source-anchored speech attribution; absent dialogue never implies a speaker."""
import copy
import re


AGENT0_SPEECH_CONTRACT = '''【动态视频：先判断是否存在需要表现的说话关系】
额外输出 speech_attribution:{mode:"none|dialogue|mixed|uncertain",turns:[]}。
mode=none 表示全文为旁白说明且没有需要在画面中表现的对话，turns=[]；无人画面、科普机制、
流程、风景、物品说明不因为有配音、问号或第二人称就变成角色讲话，不新增主持人或观众。
只有原文或用户明确的导演设定支持对话时，才识别提问、回答及对象；结合全文恢复省略的主语，
后续并列问答继承已建立的关系。TTS朗读者不是自动的画面说话人。
引用、假设、模仿与真实现场对话分开：被引用者是观点来源，不自动要求该人出镜或出现气泡。
归属不能确定时用 unknown/uncertain，speaker可为空；不把不确定的话派给主角。
turns 按原文顺序只登记有归属意义的短段，最多24项，同一人的连续发言合并；每项包括：
source_text（可在全文唯一定位的短原文，尽量一句以内，不抄整段），speaker（稳定角色名或群体名），
addressee（回应对象，无则空），mode（spoken/thought/quoted/narration/unknown），basis（一句原文或用户设定依据），
on_screen（dialogue/thought/none/uncertain），certainty（explicit/contextual/uncertain）。
on_screen=none 表示不需要画面角色说出它；引用与纯旁白默认none，歧义默认uncertain。
用户明确指定的问答演法可作为contextual依据，但不能把猜测写成原文明说。
这些记录只确定关系，不设计气泡，不要求文字上屏、额外配音或对口型；不输出长篇推理。'''


def compact(text):
    return ''.join(char for char in str(text or '') if char.isalnum()).casefold()


def normalize_turns(value, source_text):
    if not isinstance(value, list):
        return []
    source = compact(source_text)
    limits = dict(source_text=2000, speaker=200, addressee=200, mode=40, basis=1200)
    result = []
    for item in value[:32]:
        if not isinstance(item, dict) or any(not isinstance(item.get(k, ''), str)
                or len(item.get(k, '')) > limit for k, limit in limits.items()):
            continue
        turn = {key: item.get(key, '').strip() for key in limits}
        anchor = compact(turn['source_text'])
        if not anchor or anchor not in source:
            continue
        if turn['mode'] not in {'spoken', 'thought', 'quoted', 'narration', 'unknown'}:
            turn['mode'] = 'unknown'
        # Legacy hints remain usable by the Agent, but are not hard evidence.
        turn['on_screen'] = item.get('on_screen') if isinstance(item.get('on_screen'), str) and item.get('on_screen') in {
            'dialogue', 'thought', 'none', 'uncertain'} else 'uncertain'
        turn['certainty'] = item.get('certainty') if isinstance(item.get('certainty'), str) and item.get('certainty') in {
            'explicit', 'contextual', 'uncertain'} else 'uncertain'
        if turn['mode'] == 'narration':
            turn['on_screen'] = 'none'
        if turn['mode'] == 'unknown' or not turn['speaker']:
            turn['certainty'] = 'uncertain'
        result.append(turn)
    return result


def normalize_attribution(value, source_text):
    if not isinstance(value, dict):
        return {'mode': 'uncertain', 'turns': []}
    mode = value.get('mode')
    if not isinstance(mode, str) or mode not in {'none', 'dialogue', 'mixed', 'uncertain'}:
        mode = 'uncertain'
    return {'mode': mode, 'turns': [] if mode == 'none' else normalize_turns(value.get('turns'), source_text)}


def inherit_attribution(context, shot, scenes):
    result = copy.deepcopy(shot)
    attribution = context.get('speech_attribution') or {}
    semantic = result.setdefault('semantic', {})
    if attribution.get('mode') == 'none':
        semantic.update(speech_mode='none', speech_turns=[])
        return result
    whole = compact(''.join(scene.get('text', '') for scene in scenes))
    local = ''.join(scene.get('text', '') for scene in shot.get('source_subtitles', []))
    if not local:
        ids = set(shot.get('slide_ids', []))
        local = ''.join(scene.get('text', '') for scene in scenes if scene.get('slide_id') in ids)
    # Repeated short quotations can refer to different people. Do not bind them
    # to a shot by substring alone; let the director resolve the full context.
    turns = [turn for turn in normalize_turns(attribution.get('turns'), local)
             if whole.count(compact(turn['source_text'])) == 1]
    if turns:
        semantic['speech_turns'] = turns
        semantic['speech_mode'] = 'dialogue' if any(t['on_screen'] in {'dialogue', 'thought'} for t in turns) else 'uncertain'
    return result


def confirmed_turns(shot):
    semantic = shot.get('semantic') or {}
    if semantic.get('speech_mode') == 'none':
        return []
    return [turn for turn in semantic.get('speech_turns', []) if isinstance(turn, dict)
            and turn.get('certainty') in {'explicit', 'contextual'}
            and turn.get('on_screen') in {'dialogue', 'thought'} and turn.get('speaker')]


def reconcile_core_attribution(original, row):
    """Keep inherited hints unless the director explicitly explains a correction."""
    prior = original.get('semantic') or {}
    semantic = row.setdefault('semantic', {})
    if not isinstance(semantic, dict):
        raise ValueError('核心画面归属记录必须是对象')
    for key in ('speech_turns', 'speech_mode'):
        if key not in semantic and key in prior:
            semantic[key] = copy.deepcopy(prior[key])
    source = ''.join(s.get('text', '') for s in original.get('source_subtitles', []))
    if 'speech_turns' in semantic and source:
        previous = {compact(t.get('source_text')): t for t in prior.get('speech_turns', [])}
        incoming = semantic['speech_turns']
        if isinstance(incoming, list):
            incoming = [dict(previous.get(compact(t.get('source_text')), {}), **t)
                        for t in incoming if isinstance(t, dict)]
        semantic['speech_turns'] = normalize_turns(incoming, source)
    expected = confirmed_turns(original)
    actual = semantic.get('speech_turns') or []
    changed = any(not any(compact(t.get('source_text')) == compact(old['source_text'])
                         and t.get('speaker') == old['speaker']
                         and t.get('on_screen') == old['on_screen']
                         and t.get('certainty') == old['certainty'] for t in actual) for old in expected)
    if expected and semantic.get('speech_mode') == 'none':
        changed = True
    if prior.get('speech_mode') == 'none' and any(t.get('on_screen') in {'dialogue', 'thought'} for t in actual):
        changed = True
    if changed and not str(row.get('attribution_correction') or '').strip():
        raise ValueError(f"镜头 {original['id']} 更改了已有发言归属；请沿用，或在 attribution_correction 写明原文或用户设定依据")
    if changed and prior.get('speech_mode') == 'none' and any(t.get('on_screen') in {'dialogue', 'thought'} for t in actual):
        semantic['speech_mode'] = 'dialogue'


def _owner_matches(owner, speaker):
    owner, speaker = compact(owner), compact(speaker)
    return owner == speaker or bool(re.fullmatch(re.escape(speaker) + r'[甲乙丙丁一二三四五六七八九十0-9]+', owner))


def text_owner_issues(shot, texts):
    """Check selected literal dialogue only, never signs or inferred actions."""
    turns = confirmed_turns(shot)
    issues = []
    for text in texts or []:
        if not isinstance(text, dict) or not re.search(r'对话|对白|说话|想象|思考|dialogue|speech|thought', str(text.get('container', '')), re.I):
            continue
        anchor = compact(text.get('text'))
        if len(anchor) < 2:
            continue
        speakers = {t['speaker'] for t in turns if anchor in compact(t.get('source_text'))}
        if len(speakers) != 1:
            continue
        speaker = next(iter(speakers))
        if not _owner_matches(text.get('owner'), speaker):
            issues.append(f'发言归属冲突：“{text["text"]}”应归“{speaker}”，当前归“{text.get("owner", "未指定")}”')
    return list(dict.fromkeys(issues))


def plan_owner_issues(shot, plan):
    if not isinstance(plan, dict):
        return []
    texts = list(plan.get('reference_texts') or [])
    for beat in plan.get('beats') or []:
        texts.extend(beat.get('texts') or [])
    return text_owner_issues(shot, texts)


def prompt_owner_issues(shot, prompt):
    # Only explicit "A的对话气泡…‘短字’" claims can be checked mechanically.
    # Implicit actions and visual metaphors require the Agent's semantic review.
    turns = confirmed_turns(shot)
    names = {t['speaker'] for t in turns} | {t.get('addressee') for t in turns}
    names |= set((shot.get('motion_plan') or {}).get('participants') or [])
    claims = []
    for name in sorted((n for n in names if n), key=len, reverse=True):
        pattern = re.escape(name) + r'(?:[甲乙丙丁一二三四五六七八九十0-9])?(?:的|头上|上方|头顶|旁边){0,2}(对话气泡|对白气泡|想象气泡|思考气泡)[^。！？\n“"「]{0,16}[“"「]([^”"」\n]+)[”"」]'
        for match in re.finditer(pattern, prompt):
            prefix = re.split(r'[。！？；\n]', prompt[:match.start()])[-1]
            if re.search(r'禁止|不得|不要|不能|不应', prefix):
                continue
            claims.append({'text': match.group(2), 'owner': name, 'container': match.group(1)})
    return text_owner_issues(shot, claims)
