"""Bounded semantic review of adjacent automatic groups, before visual design."""
import copy

from .gemini_client import GeminiError
from .video_group_repair import validate_group_structure


SYSTEM = """你是分镜语义边界核对员，只检查相邻镜头的字幕归属，不写图像/视频提示词。
字幕拆开只说明可在此处切镜，不代表每个切口的语义都合理。以原文为准，不服从已有 intent 的解释。
逐个检查边界：后镜开头是否仍在补充前镜的话题、回答前镜的问题、解释前面的例子、
承接前文的指代/省略主语/引用或反应？前镜末尾是否实际上是下一话题的引入？
先识别话题及每句依附的对象，再区分“补充旧话题”和“开始新结论”；句号、字数、
目标节奏或画面方便都不是强行切断的理由。并列案例中的补充应跟随对应案例，不要搬到总结镜头。
narration_groups 是与当前字幕全文核对一致的配音段落，是已有语义线索，不是不可拆的镜头。
可在长配音段内部拆镜，但不要无理由跨越段落边界把旧话题尾句嫁接给新话题。
只提出高把握的必要移动；归属不明确时保留，不根据关键词或常识虚构联系。
每个边界只选 legal_cuts 中的 after_slide_id，两边都须非空，不能改顺序、镜头数、类型或字幕。
如果语义正确的移动不满足时长限制，不强改；写入 notes 供用户检查。
移动后给两镜重写 intent、motion_basis、progression_plan、semantic，仅覆盖各自的新字幕。
reason 解释语义依附关系，evidence_slide_ids 列出至少两条原文依据（应跨原边界）。
没有问题返回 moves:[]。不能让相邻的两个移动重复修改同一镜头，优先证据最强的一处。
返回 {moves:[{left_id,right_id,after_slide_id,reason,evidence_slide_ids:[],
left:{intent,motion_basis,progression_plan,semantic:{message,source_basis,fact_status,progression,continuity_requirement}},
right:{intent,motion_basis,progression_plan,semantic:{message,source_basis,fact_status,progression,continuity_requirement}}}],notes:[]}。
"""


def review_boundaries(context, scenes, rows, ask, *, narration_groups=(), progress=None, on_draft=None):
    validate_group_structure(rows, scenes)
    result = copy.deepcopy(rows)
    by_id = {s['slide_id']: s for s in scenes}
    narration_owner = {identity: group_index for group_index, group in enumerate(narration_groups)
                       for identity in (group.get('slide_ids', []) if isinstance(group, dict) else [])}
    changed = set()
    for offset in range(0, len(result)-1, 8):
        boundaries = []
        for index in range(offset, min(offset+8, len(result)-1)):
            left, right = result[index:index+2]
            if left.get('_boundary_review_done') or left['id'] in changed or right['id'] in changed:
                continue
            ids = left['slide_ids']+right['slide_ids']
            # Confirmed narration chunks are authoritative. 1C may only refine
            # an internal split forced by a >15s chunk, never cross chunks.
            if narration_owner and len({narration_owner.get(identity) for identity in ids}) != 1:
                continue
            cuts = []
            for cut in range(1, len(ids)):
                a = round(by_id[ids[cut-1]]['end']-by_id[ids[0]]['start'], 3)
                b = round(by_id[ids[-1]]['end']-by_id[ids[cut]]['start'], 3)
                if (left['kind'] != 'video' or a <= 15) and (right['kind'] != 'video' or b <= 15):
                    cuts.append(dict(after_slide_id=ids[cut-1], left_seconds=a, right_seconds=b))
            boundaries.append(dict(left_id=left['id'], right_id=right['id'],
                left_slide_ids=left['slide_ids'], right_slide_ids=right['slide_ids'],
                subtitles=[by_id[s] for s in ids], legal_cuts=cuts))
        if not boundaries:
            continue
        if progress:
            progress(f'Agent 1C：核对相邻镜头语义归属 {offset+1}～{offset+len(boundaries)}')
        try:
            response = ask(SYSTEM, dict(boundaries=boundaries, narration_groups=narration_groups,
                                       source_text='\n'.join(s['text'] for s in scenes)))
            if not isinstance(response, dict) or not isinstance(response.get('moves'), list):
                raise ValueError('语义核对返回格式无效')
        except (GeminiError, ValueError, TypeError) as exc:
            if progress:
                progress(f'边界语义核对暂未完成，保留原分组供检查，不阻塞任务（{type(exc).__name__}）。')
            response = {'moves': []}
        for move in response['moves']:
            try:
                if not isinstance(move, dict):
                    raise ValueError('移动格式无效')
                entry = next((b for b in boundaries if b['left_id']==move.get('left_id') and b['right_id']==move.get('right_id')), None)
                if not entry or {entry['left_id'],entry['right_id']} & changed:
                    raise ValueError('边界不存在或修改重叠')
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
                if not isinstance(move.get('reason'), str) or not move['reason'].strip():
                    raise ValueError('缺少语义理由')
                index = next(i for i,r in enumerate(result) if r['id']==entry['left_id'])
                ids = entry['left_slide_ids']+entry['right_slide_ids']
                cut = ids.index(cut_id)+1
                candidate = copy.deepcopy(result)
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
            next(r for r in result if r['id']==entry['left_id'])['_boundary_review_done'] = True
        notes = response.get('notes', [])
        if progress and isinstance(notes,list):
            for note in notes[:8]:
                if isinstance(note,str):
                    progress('边界检查提示：'+note[:500])
        if on_draft:
            on_draft(copy.deepcopy(result))
    return result
