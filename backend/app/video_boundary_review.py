"""Bounded semantic review of adjacent automatic groups, before visual design."""
import copy
import uuid

from .gemini_client import GeminiError
from .video_medical_director import medical_contract
from .video_group_repair import validate_group_structure, legal_ranges, _missing_fields, _semantic_child


SYSTEM = """你是分镜语义边界核对员，只检查相邻镜头的字幕归属，不写图像/视频提示词。
字幕拆开只说明可在此处切镜，不代表每个切口的语义都合理。以原文为准，不服从已有 intent 的解释。
逐个检查边界：后镜开头是否仍在补充前镜的话题、回答前镜的问题、解释前面的例子、
承接前文的指代/省略主语/引用或反应？前镜末尾是否实际上是下一话题的引入？
先识别话题及每句依附的对象，再区分“补充旧话题”和“开始新结论”；句号、字数、
目标节奏或画面方便都不是强行切断的理由。并列案例中的补充应跟随对应案例，不要搬到总结镜头。
narration_groups 是与当前字幕全文核对一致的配音段落，是已有语义线索，不是不可拆的镜头。
默认保留完整解说；有原文依据时可以跨配音段合并补充/反问/回应，或在段内按话题与视觉过程拆镜。
配音文件和字幕本身不变。不得因配音单独生成了一个短句，就要求给它独立一张画面。
只提出高把握的必要移动；归属不明确时保留，不根据关键词或常识虚构联系。
普通移动只选 legal_cuts 中的 after_slide_id，两边非空，不改顺序或字幕。
若短补充句应与相邻镜头共用画面，可返回 repartitions，重组指定两镜的连续字幕为1～3镜；
每个新镜头均须有完整意义，使用 legal_ranges，动态不超过15秒。不能把原动态字幕改成静态来绕过限制。
保留合理的原边界，不为了凑时长或减少镜头数而合并不同话题。不能跨出当前两镜范围。
如果语义正确的移动不满足时长限制，不强改；写入 notes 供用户检查。
移动后给两镜重写 intent、motion_basis、progression_plan、semantic，仅覆盖各自的新字幕。
reason 解释语义依附关系，evidence_slide_ids 列出至少两条原文依据（应跨原边界）。
没有问题返回 moves:[]。不能让相邻的两个移动重复修改同一镜头，优先证据最强的一处。
返回 {repartitions:[{left_id,right_id,reason,evidence_slide_ids:[],shots:[{slide_ids:[],kind:"video|static",intent,motion_basis,progression_plan,semantic:{message,source_basis,fact_status,progression,continuity_requirement}}]}],moves:[{left_id,right_id,after_slide_id,reason,evidence_slide_ids:[],
left:{intent,motion_basis,progression_plan,semantic:{message,source_basis,fact_status,progression,continuity_requirement}},
right:{intent,motion_basis,progression_plan,semantic:{message,source_basis,fact_status,progression,continuity_requirement}}}],notes:[]}。
"""


def review_boundaries(context, scenes, rows, ask, *, narration_groups=(), progress=None, on_draft=None):
    max_duration = 30 if context.get('dynamic_max_shot_duration') == 30 else 15
    validate_group_structure(rows, scenes)
    result = copy.deepcopy(rows)
    by_id = {s['slide_id']: s for s in scenes}
    changed = set()
    pairs = [(left['id'], right['id']) for left, right in zip(result, result[1:])]
    for offset in range(0, len(pairs), 6):
        boundaries = []
        for left_id, right_id in pairs[offset:offset+6]:
            index = next((i for i, row in enumerate(result[:-1])
                          if row['id'] == left_id and result[i+1]['id'] == right_id), None)
            if index is None:
                continue
            left, right = result[index:index+2]
            if left.get('_boundary_review_done') == 'semantic_v2' or left['id'] in changed or right['id'] in changed:
                continue
            ids = left['slide_ids']+right['slide_ids']
            if left.get('boundary_locked') or left.get('manual_locked') or right.get('manual_locked'):
                continue
            cuts = []
            for cut in range(1, len(ids)):
                a = round(by_id[ids[cut-1]]['end']-by_id[ids[0]]['start'], 3)
                b = round(by_id[ids[-1]]['end']-by_id[ids[cut]]['start'], 3)
                if (left['kind'] != 'video' or a <= max_duration) and (right['kind'] != 'video' or b <= max_duration):
                    cuts.append(dict(after_slide_id=ids[cut-1], left_seconds=a, right_seconds=b))
            boundaries.append(dict(left_id=left['id'], right_id=right['id'],
                left_slide_ids=left['slide_ids'], right_slide_ids=right['slide_ids'],
                subtitles=[by_id[s] for s in ids], legal_cuts=cuts,
                legal_ranges=legal_ranges([by_id[s] for s in ids], max_duration),
                shots=[copy.deepcopy(left), copy.deepcopy(right)]))
        if not boundaries:
            continue
        if progress:
            progress(f'Agent 1C：核对相邻镜头语义归属 {offset+1}～{offset+len(boundaries)}')
        try:
            response = ask(SYSTEM.replace('15秒', f'{max_duration}秒') + medical_contract(context, 'groups'), dict(boundaries=boundaries, narration_groups=narration_groups,
                                       source_text='\n'.join(s['text'] for s in scenes)))
            if not isinstance(response, dict) or not isinstance(response.get('moves', []), list) or not isinstance(response.get('repartitions', []), list):
                raise ValueError('语义核对返回格式无效')
        except (GeminiError, ValueError, TypeError) as exc:
            if progress:
                progress(f'边界语义核对暂未完成，保留原分组供检查，不阻塞任务（{type(exc).__name__}）。')
            response = {'moves': []}
        proposals = [(True, proposal) for proposal in response.get('repartitions', [])]
        proposals += [(False, proposal) for proposal in response.get('moves', [])]
        for repartition, move in proposals:
            try:
                if not isinstance(move, dict):
                    raise ValueError('移动格式无效')
                entry = next((b for b in boundaries if b['left_id']==move.get('left_id') and b['right_id']==move.get('right_id')), None)
                if not entry or {entry['left_id'],entry['right_id']} & changed:
                    raise ValueError('边界不存在或修改重叠')
                if not repartition:
                    cut_id = move.get('after_slide_id')
                    if cut_id not in [c['after_slide_id'] for c in entry['legal_cuts']]:
                        raise ValueError('移动后超时或边界无效')
                    if cut_id == entry['left_slide_ids'][-1]:
                        continue
                evidence = move.get('evidence_slide_ids', [])
                if not isinstance(evidence, list) or any(not isinstance(s,str) for s in evidence):
                    raise ValueError('原文依据无效')
                if not (set(evidence)&set(entry['left_slide_ids']) and set(evidence)&set(entry['right_slide_ids'])):
                    raise ValueError('缺少跨边界原文依据')
                if not set(evidence) <= set(entry['left_slide_ids']+entry['right_slide_ids']):
                    raise ValueError('原文依据超出范围')
                if not isinstance(move.get('reason'), str) or not move['reason'].strip():
                    raise ValueError('缺少语义理由')
                index = next(i for i,r in enumerate(result) if r['id']==entry['left_id'])
                ids = entry['left_slide_ids']+entry['right_slide_ids']
                candidate = copy.deepcopy(result)
                if repartition:
                    replacements = copy.deepcopy(move.get('shots'))
                    if not isinstance(replacements, list) or not 1 <= len(replacements) <= 3:
                        raise ValueError('局部镜头数量无效')
                    local_scenes = [by_id[s] for s in ids]
                    validate_group_structure(replacements, local_scenes)
                    if [(r['slide_ids'], r['kind']) for r in replacements] == [
                            (r['slide_ids'], r['kind']) for r in result[index:index+2]]:
                        continue
                    dynamic_ids = {sid for old in result[index:index+2] if old['kind']=='video' for sid in old['slide_ids']}
                    for position, replacement in enumerate(replacements):
                        local = [by_id[sid] for sid in replacement['slide_ids']]
                        seconds = round(local[-1]['end']-local[0]['start'], 3)
                        if replacement['kind']=='video' and seconds>max_duration:
                            raise ValueError('重组后动态镜头超时')
                        if replacement['kind']=='static' and dynamic_ids.intersection(replacement['slide_ids']) and not (len(local)==1 and seconds>max_duration):
                            raise ValueError('不能以静态降级绕过时长限制')
                        if _missing_fields(replacement):
                            raise ValueError('缺少新镜头的完整表达目的')
                        _semantic_child(replacement)
                        # Accept grouping/design fields only, never invented media,
                        # timestamps, locks or stale downstream plans from an LLM.
                        replacement = {key: copy.deepcopy(replacement[key]) for key in
                                       ('slide_ids', 'kind', 'intent', 'motion_basis', 'semantic')}
                        replacement['progression_plan'] = replacements[position].get('progression_plan', '')
                        replacement['id'] = uuid.uuid4().hex[:12]
                        replacement['kind_adjustment'] = 'Agent 1C：'+move['reason'][:1000]
                        replacement['_boundary_review_done'] = 'semantic_v2'
                        replacements[position] = replacement
                    if result[index+1].get('boundary_locked'):
                        replacements[-1]['boundary_locked'] = True
                    candidate[index:index+2] = replacements
                else:
                    cut = ids.index(cut_id)+1
                    for position, side, span in ((index,'left',ids[:cut]), (index+1,'right',ids[cut:])):
                        design = move.get(side)
                        if not isinstance(design,dict) or any(not isinstance(design.get(k),str) for k in ('intent','motion_basis','progression_plan')) or not design['intent'].strip() or not isinstance(design.get('semantic'),dict):
                            raise ValueError('缺少新字幕对应的镜头目的')
                        candidate[position].update({k: design[k] for k in ('intent','semantic','motion_basis','progression_plan')})
                        candidate[position]['slide_ids'] = span
                validate_group_structure(candidate, scenes)
                from .video_plan import normalize_shots
                normalize_shots(candidate, scenes)  # Reject malformed metadata before accepting any change.
                result = candidate
                changed.update((entry['left_id'],entry['right_id']))
                if progress:
                    progress(f'语义边界已修正：{entry["left_id"]} / {entry["right_id"]}；{move["reason"][:500]}')
            except (ValueError, TypeError, KeyError, StopIteration):
                if progress:
                    progress('一项语义边界建议未通过范围或时长校验，已保留原边界，可手动调整分镜。')
        for entry in boundaries:
            row = next((r for r in result if r['id']==entry['left_id']), None)
            if row is not None:
                row['_boundary_review_done'] = 'semantic_v2'
        notes = response.get('notes', [])
        if progress and isinstance(notes,list):
            for note in notes[:8]:
                if isinstance(note,str):
                    progress('边界检查提示：'+note[:500])
        if on_draft:
            on_draft(copy.deepcopy(result))
    return result
