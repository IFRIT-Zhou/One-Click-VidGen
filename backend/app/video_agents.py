"""Small, single-responsibility Agents for dynamic-video storyboard planning."""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from .gemini_client import generate_gemini_text, parse_json_response
from .video_director_contracts import (MOTION_CONTRACT, SEMANTIC_CONTRACT, SINGLE_REFERENCE_GUIDE,
                                     SPEECH_ATTRIBUTION_CONTRACT, VISUAL_CONTRACT,
                                     enforce_no_auto_subtitles)
from .video_director_examples import (CORE_DESIGN_EXAMPLE, MOTION_DESIGN_EXAMPLE,
                                     VISUAL_FIRST_CORE_DESIGN_EXAMPLE, VISUAL_FIRST_MOTION_DESIGN_EXAMPLE)
from .video_motion_plan import normalize_motion_plan, prompt_plan_issues, render_motion_action, restore_reference_draft
from .video_text_policy import (VISUAL_FIRST, dynamic_text_mode, text_mode_contract,
                                visual_first_plan_issues, visual_first_prompt_issues)
from .video_image_prompt import assemble_image_body, without_leading_intent
from .video_prompt_notices import prompt_notice_level


Ask = Callable[[str, dict[str, Any]], dict[str, Any]]


def ask_json(system: str, data: dict[str, Any]) -> dict[str, Any]:
    result = parse_json_response(generate_gemini_text(
        system_prompt=system, user_prompt=json.dumps(data, ensure_ascii=False),
        temperature=0.15, max_output_tokens=10000,
        response_mime_type="application/json", json_root="object"))
    # Some compatible providers ignore the requested root schema and return
    # either a top-level shots array or JSON serialized once more as a string.
    for _ in range(2):
        if not isinstance(result, str):
            break
        result = parse_json_response(result)
    if isinstance(result, list) and all(isinstance(row, dict) for row in result):
        result = {'shots': result}
    if not isinstance(result, dict):
        raise ValueError("视频 Agent 未返回 JSON 对象")
    return result


def _complete_rows(response: dict[str, Any], expected: list[str], label: str) -> list[dict[str, Any]]:
    rows = response.get("shots")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{label}未返回有效镜头列表")
    identities = [row.get("id") for row in rows]
    if identities != expected:
        raise ValueError(f"{label}镜头缺失、重复或顺序改变")
    return rows


def _validate_narration_partition(rows, scenes, narration_groups):
    """Confirmed narration chunks are hard edit boundaries for video shots."""
    if not narration_groups:
        return
    ordered = [scene['slide_id'] for scene in scenes]
    positions = {identity: index for index, identity in enumerate(ordered)}
    shot_ends = {row['slide_ids'][-1] for row in rows[:-1]}
    for group in narration_groups:
        ids = group.get('slide_ids') if isinstance(group, dict) else None
        if not isinstance(ids, list) or not ids or any(identity not in positions for identity in ids):
            raise ValueError('配音语义分组资料无效')
        indexes = [positions[identity] for identity in ids]
        if indexes != list(range(indexes[0], indexes[-1] + 1)):
            raise ValueError('配音语义分组不是连续字幕')
        if ids[-1] != ordered[-1] and ids[-1] not in shot_ends:
            raise ValueError(f'镜头跨越已确认的配音段落边界：{ids[-1]} 之后必须切镜')
        duration = float(scenes[indexes[-1]]['end']) - float(scenes[indexes[0]]['start'])
        if duration <= 15.0001:
            internal = set(ids[:-1]) & shot_ends
            if internal:
                raise ValueError(f'完整配音段落不超过15秒，不应在中间切镜：{sorted(internal)[0]}')


def _coalesce_short_narration_groups(rows, scenes, narration_groups):
    """Deterministically restore confirmed short TTS chunks after Agent mistakes.

    The regular validator has already established that every confirmed narration
    boundary is a shot boundary.  Therefore a short group can only be invalid
    because the Agent inserted one or more extra cuts inside it.  Merge those
    rows and combine their planning notes.  This must be deterministic: sending
    the merged row back through Agent 1B would allow that Agent to insert the
    same forbidden cut again.
    """
    if not narration_groups:
        return rows
    ordered = [scene['slide_id'] for scene in scenes]
    positions = {identity: index for index, identity in enumerate(ordered)}
    scene_by_id = {scene['slide_id']: scene for scene in scenes}
    result = list(rows)
    for group in narration_groups:
        ids = group.get('slide_ids') if isinstance(group, dict) else None
        if not isinstance(ids, list) or not ids or any(identity not in positions for identity in ids):
            continue
        duration = float(scene_by_id[ids[-1]]['end']) - float(scene_by_id[ids[0]]['start'])
        if duration > 15.0001:
            continue
        indexes = [index for index, row in enumerate(result)
                   if any(identity in set(ids) for identity in row.get('slide_ids', []))]
        if len(indexes) <= 1 or indexes != list(range(indexes[0], indexes[-1] + 1)):
            continue
        covered = [identity for index in indexes for identity in result[index].get('slide_ids', [])]
        if covered != ids:
            # Do not guess if an Agent also crossed a confirmed outer boundary.
            continue
        parts = [result[index] for index in indexes]
        def joined(key):
            values = [str(part.get(key) or '').strip() for part in parts]
            return '；'.join(dict.fromkeys(value for value in values if value))
        semantics = [part.get('semantic') for part in parts if isinstance(part.get('semantic'), dict)]
        semantic = {}
        for key in ('message', 'source_basis', 'progression', 'continuity_requirement'):
            values = [str(item.get(key) or '').strip() for item in semantics]
            semantic[key] = '；'.join(dict.fromkeys(value for value in values if value))
        statuses = list(dict.fromkeys(str(item.get('fact_status') or '').strip()
                                     for item in semantics if item.get('fact_status')))
        semantic['fact_status'] = statuses[0] if len(statuses) == 1 else ('quoted' if statuses else '')
        merged = dict(parts[0])
        merged.update(slide_ids=list(ids), kind='video', intent=joined('intent'),
                      motion_basis=joined('motion_basis'), progression_plan=joined('progression_plan'),
                      semantic=semantic)
        merged['narration_partition_repair'] = '已自动撤销完整配音段落内部的错误切镜'
        result[indexes[0]:indexes[-1] + 1] = [merged]
    return result


def plan_groups(context: dict[str, Any], scenes: list[dict[str, Any]], arrangement: dict[str, Any],
                ask: Ask = ask_json, *, progress=None, on_draft=None, resume_rows=None,
                boundary_review=False) -> list[dict[str, Any]]:
    system = """你是动态视频的镜头规划 Agent 1，只负责划分镜头和选择静态/动态，不写提示词。
每个镜头覆盖连续的一条或多条字幕，所有字幕按原顺序恰好覆盖一次，只能在字幕边界切换。
先理解一段完整意思，再决定镜头；不要机械地一条字幕一镜。动态用于人物互动、提问与回应、
过程、状态变化、因果展开、空间变化和能增强理解的动作；这些内容不要因为核心图可以静态表达就判为 static。
纯信息展示、短句或运动没有表达增益时使用 static。开头两个镜头承担观众留存，必须规划为 video，
并控制在15秒以内；可在字幕边界合理缩短分组。单条字幕自身超过15秒才允许受限为 static，不能私自拆句。
intent 用一句话说明观众看完这一镜应该理解什么，不复述字幕，不编造事实。
先判断表达是否需要依次展开，再决定时间分组。不能以“超过15秒”为由把可拆分的动态内容选为静态。
每镜增加 motion_basis（为什么动态有增益，或静态已经足够）和 progression_plan（应依次展开的关系，
例如提问到回应、状态前后变化；静态填空）。这里只描述表达步骤，不设计气泡、运镜等具体手法。
动态长段沿自然递进的字幕边界拆成多个不超过15秒的镜头，各自有具体 intent；保留问题与回答的关系。
字幕短句是时间单位，不是独立语义单位：尾随的补充、反应、指代、例证必须先判断依附对象。
不得仅因一句出现新名词就分给下一话题，也不能把前一案例的末尾补充当成下一结论的开场。
先圈定完整语义，再按15秒硬上限安排镜头；不要为了凑8秒、12秒或固定字幕条数切断语义。
narration_groups 若存在，是已校验与全文一致的配音段落线索；允许在其内部拆镜，
不超过15秒的段落必须完整对应一个镜头；超过15秒才可在其内部沿字幕边界拆镜。
禁止跨配音段落重新组合。时间始终以 scenes 为准。
引用、想象、刻板印象始终保持其主观属性，不能画成对现实的事实断言。
返回 {shots:[{slide_ids:[...],kind:"static|video",intent:"...",semantic:{...},motion_basis:"...",progression_plan:"..."}]}。
""" + SEMANTIC_CONTRACT
    video_arrangement = {k:v for k,v in arrangement.items() if k not in {
        'visual_pacing_preset', 'visual_min_duration', 'visual_target_duration', 'visual_max_duration', 'visual_max_slides'}}
    payload = {"story_context": context, "scenes": scenes,
               "narration_groups": arrangement.get('narration_groups', []),
               "arrangement": {**video_arrangement, "opening_motion_shots": 2, "max_video_duration": 15}}
    from .video_group_repair import repair_groups, validate_group_structure
    def finish(rows):
        rows = repair_groups(context, scenes, rows, ask, progress=progress, on_draft=on_draft)
        if boundary_review:
            from .video_boundary_review import review_boundaries
            rows = review_boundaries(context, scenes, rows, ask,
                narration_groups=arrangement.get('narration_groups', []), progress=progress, on_draft=on_draft)
        # Every later Agent is advisory. Re-apply the confirmed short narration
        # invariant at the final exit so a repair/review Agent cannot reinsert
        # an illegal internal cut after the initial validation succeeded.
        final_rows = _coalesce_short_narration_groups(
            rows, scenes, arrangement.get('narration_groups', []))
        if final_rows != rows and progress:
            progress('最终核对已撤销完整配音段落内部的非法切口。')
        rows = final_rows
        validate_group_structure(rows, scenes)
        _validate_narration_partition(rows, scenes, arrangement.get('narration_groups', []))
        return rows
    if resume_rows is not None:
        try:
            validate_group_structure(resume_rows, scenes)
            _validate_narration_partition(resume_rows, scenes, arrangement.get('narration_groups', []))
        except ValueError:
            pass
        else:
            if progress:
                progress('已读取保存的镜头划分，只继续处理尚未完成的局部规划。')
            return finish(resume_rows)
    response = ask(system, payload)
    for attempt in range(2):
        rows = response.get('shots') if isinstance(response, dict) else None
        try:
            validate_group_structure(rows, scenes)
            _validate_narration_partition(rows, scenes, arrangement.get('narration_groups', []))
        except ValueError as exc:
            issue = str(exc)
        else:
            return finish(rows)
        if attempt:
            repaired = _coalesce_short_narration_groups(
                rows, scenes, arrangement.get('narration_groups', []))
            if repaired != rows:
                try:
                    validate_group_structure(repaired, scenes)
                    _validate_narration_partition(
                        repaired, scenes, arrangement.get('narration_groups', []))
                except ValueError:
                    pass
                else:
                    if progress:
                        progress('Agent 1 连续在完整配音段落内切镜，已自动撤销非法切口并继续规划。')
                    return finish(repaired)
            raise ValueError('分镜字幕覆盖修订仍未通过：' + issue)
        if progress:
            progress('Agent 1：修订字幕覆盖与镜头顺序')
        response = ask(system + '\n只修订字幕覆盖、顺序与数据结构问题，返回完整 shots。保持全文覆盖与原文含义。',
                       {**payload, 'previous_shots': rows, 'validation_errors': [issue]})
    raise ValueError('镜头规划失败')


def design_core_images(context: dict[str, Any], scenes: list[dict[str, Any]], shots: list[dict[str, Any]],
                  references: list[dict[str, Any]], ask: Ask = ask_json) -> list[dict[str, Any]]:
    expected = [shot["id"] for shot in shots]
    system = """你是核心画面导演 Agent 2，只设计已分组镜头的核心画面与按需参考素材。
不得改变镜头id、顺序、字幕分组或kind。不设计动态过程或视频提示词。
通常保留intent；若 design_needs_review=true，说明用户调整了字幕归属：以新的source_subtitles为准，
先重新理解本镜含义并返回新的intent和semantic，再设计核心画面。旧提示词与previous_designs仅供风格、
人物与表达手段延续参考，不得把已移出的字幕内容继续塞入本镜；必须表达新移入的补充说明。
调整字幕范围时，semantic 仍须重新填写 message、source_basis、fact_status、progression、continuity_requirement 与 speech_turns。
设计前核对 semantic.speech_turns 与本镜原文及全文的问答关系，返回核对后的 semantic.speech_turns；
旧任务没有该字段时本次补齐，不需要改分组。原文显示上游误把被转述者的发言归给旁白主角时，
可仅修正 intent、semantic.source_basis、progression_plan 中的错误角色归属，并用 attribution_correction
写一句依据；不要趁机更换用户的表达目标、主题、场景或创意。没有归属错误时不返回这项修正。
参考素材不是首帧约束，只在确实有用时选择 reference_ids，最多8张；用户写明全程使用的人物素材必须选择。
人物参考图同时约束人物服装、配色、发型和整体造型。原文或用户设定没有明确要求换装时，必须沿用参考图，
不得根据“演讲者、主持人、专业、正式”等身份或场景自行推断西装、职业装、演讲服等新服装。
不要新增证据、数字或人物关系。
method_example 是一个独立方法示例，当前任务的事实、角色、画风与主题只取自当前输入，不能照搬示例。
返回 {shots:[{id,visual_description,visual_design:{...},reference_ids:[],semantic:{speech_turns:[]},
intent:"仅调整字幕范围或纠正错归属时填写",progression_plan:"仅纠正错归属时填写",attribution_correction:"仅纠正错归属时填写依据"}]}。
未调整的镜头除有原文依据的归属纠正外，不改写既定 intent。""" + VISUAL_CONTRACT + text_mode_contract(context) + SPEECH_ATTRIBUTION_CONTRACT
    payload = {"story_context": context, "scenes": scenes, "shots": shots, "references": references,
               "method_example": (VISUAL_FIRST_CORE_DESIGN_EXAMPLE
                                  if dynamic_text_mode(context) == VISUAL_FIRST else CORE_DESIGN_EXAMPLE)}
    response = ask(system, payload)
    for attempt in range(2):
        try:
            rows = _complete_rows(response, expected, "核心画面导演")
            break
        except ValueError as exc:
            if attempt:
                raise
            response = ask(system + '\n只修复返回格式；必须返回对象 {shots:[...]}，镜头顺序和设计内容保持不变。',
                           {**payload, 'previous_result': response, 'validation_errors': [str(exc)]})
    for row in rows:
        if not isinstance(row.get('visual_description'), str) or not row['visual_description'].strip():
            raise ValueError(f"镜头 {row['id']} 缺少核心画面设计，请重新规划")
        original = next(shot for shot in shots if shot['id'] == row['id'])
        if original.get('design_needs_review') and (not isinstance(row.get('intent'), str) or not row['intent'].strip()):
            raise ValueError(f"镜头 {row['id']} 调整后缺少新的表达目的，原设计已保留，可继续更新")
    return rows


def write_image_prompts(context: dict[str, Any], style: str, shots: list[dict[str, Any]],
                        references: list[dict[str, Any]], ask: Ask = ask_json, on_draft=None) -> list[dict[str, Any]]:
    expected = [shot["id"] for shot in shots]
    text_handoff = (
        '主体名称保持清楚，保留所选图案的具体内容和归属；仅将 reference_texts 中已选的必要少量文字'
        '（可含简短反应词、标签和场景文字）写明原文、owner 和 container；不擅自扩写为对白长句，'
        '不照抄 source_text 或整段旁白。把属性、服装与动作合并成自然句子。\n'
        if dynamic_text_mode(context) == VISUAL_FIRST else
        '主体名称保持清楚，逐条保留所选短字原文、owner 和 container；把属性、服装与动作合并成自然句子。\n')
    system = """你是核心分镜图定稿 Agent 4，不是导演。保持既定 intent、人物、场景和参考素材。
静态镜头将 visual_description 和 visual_design 整理成可直接用于图像模型的 image_prompt。
动态镜头以 motion_plan.reference_visual 为核心图依据，保留 Agent2 已选定的场景和具体表达，
只整理 Agent3 为动作所作的局部适配。version=2 时，reference_beat 表示前段场面基准，不是元素白名单；
可见主体按 reference_participants，可见短字按 reference_texts，允许有归属的复合参考信息。
不要求把全部视频阶段都画出来，后续新状态和景物也不必先出现在图中；参考图是单幅连贯构图，不画多宫格。
旧 version=1 没有上述字段时，沿用 participants 与 reference_beat 对应阶段 texts。
""" + text_handoff + """逐镜核对 source_subtitles 原文；不得丢掉既定方案的具体参与者、因果、假设与引用边界。
不得新增计划以外的道具、数字、剧情或重新选择表达。核心图不是强制首帧，不改成动作前的空泛准备状态。
把人物与画风写入 image_sections.characters_and_style，具体画面写入 scene，少量必要限制写入 constraints。
程序会按人物与画风、画面内容、必要限制的顺序统一排版；你只填各部分正文，不自行编写分节标题。
constraints 没有额外限制时填空字符串，不为凑格式补造“禁止文字”“无人”等限制。合并重复人物描述，
删除内部管理措辞。纯环境或静物镜头要明确“无人物出镜”。避免画中出现无必要文字。
观众、学生、行人等已选可见群体也属于人物；存在此类群体时不得写“无人物出镜”。
把所选群体的位置、朝向和既定反应写入画面内容，不能只写主角“面对观众”而省略观众的可见描绘。
只写本图必须执行的构图、主体关系和少量必要限制；不重复角色卡、候选方案、程序字段或全片管理规则。
人物档案不代表出镜名单。保留用户画风和本镜具体服装、布局、可见关系与必要示意元素。
本镜选择了人物参考素材时，服装、配色、发型和整体造型必须继承参考图；除非原文或用户设定明确换装，
不得写“专业演讲服装、正式服装、职业装、商务装、西装、西服”等推断性服装描述。
不要输出【本图旨在】，程序会把 intent 原文写入可见的最终提示词；这个目的不是画中要写的字。
semantic.speech_turns 用于核对谁说/想，不能把它打印成提示词的字段；将选定的归属写成自然句子，
图案气泡和短字气泡同样保留说话者/想象者。不要因主角是旁白音色而重新分配角色的发言。
下列规则的标题（例如“画面文字与归属”）是内部工作说明，不是输出分节；选定的短文字与归属自然写入 scene。
只返回 {shots:[{id,image_sections:{characters_and_style:"人物与画风正文",scene:"画面内容正文",constraints:"必要限制正文或空字符串"}}]}，
顺序不变。不要另写 image_prompt，程序负责拼出可直接提交图像模型的最终提示词。""" + text_mode_contract(context)
    payload = {"story_context": context, "global_style": style, "shots": shots, "references": references}
    by_id = {shot['id']: shot for shot in shots}
    def present(rows):
        result = []
        for row in rows:
            prompt = without_leading_intent(row['image_prompt'])
            shot = by_id[row['id']]
            source = ' '.join(str(item.get('text') or '') for item in shot.get('source_subtitles', []))
            explicit_change = re.search(r'换装|换上|改穿|身穿|穿着|西装|西服|制服|礼服|职业装|商务装', source)
            if shot.get('reference_ids') and not explicit_change:
                inferred = re.compile(
                    r'(?:身穿|穿着|一身)?\s*(?:专业|正式|职业|商务)(?:的)?(?:演讲)?'
                    r'(?:服装|服饰|装束|着装|西装|西服)')
                prompt = inferred.sub('服装、配色、发型与整体造型严格沿用所选人物参考图', prompt)
            result.append({**row, 'image_prompt': f"【本图旨在】{by_id[row['id']]['intent']}\n{prompt}"})
        return result
    rows = _finalize_prompts(system, payload, shots, 'image_prompt', 'image', expected, ask,
                            assemble=assemble_image_body,
                            on_draft=(lambda rows: on_draft(present(rows))) if on_draft else None)
    return present(rows)


def _numbered_dynamic_shots(shots, references):
    dynamic = [shot for shot in shots if shot["kind"] == "video"]
    if not dynamic:
        return []
    prepared = []
    by_reference = {row["id"]: row for row in references}
    for shot in dynamic:
        numbered = [{"number": "图1", "purpose": "本镜头核心分镜图；内容/人物/画风/场景参考，不强制作为首帧"}]
        numbered.extend({"number": f"图{index + 2}", **by_reference[identity]}
                        for index, identity in enumerate(shot.get("reference_ids", [])))
        prepared.append({**shot, "numbered_references": numbered})
    return prepared


def direct_motion(context: dict[str, Any], shots: list[dict[str, Any]],
                  references: list[dict[str, Any]], ask: Ask = ask_json) -> list[dict[str, Any]]:
    prepared = _numbered_dynamic_shots(shots, references)
    if not prepared:
        return []
    for shot in prepared:
        if not str(shot.get('visual_description') or '').strip():
            raise ValueError('动态设计需要先完成核心画面草案')
        # Old final prompts must not become an accidental constraint when re-planning.
        for field in ('image_prompt', 'video_prompt', 'action', 'motion_plan'):
            shot.pop(field, None)
    expression_handoff = (
        '将递进关系落实为动作、表情与具体景物依次发生的可见变化，遵循对应字幕的大致先后。'
        '需要表达主观想象时可以使用有归属的图案气泡，\n'
        if dynamic_text_mode(context) == VISUAL_FIRST else
        '将递进关系落实为依次发生的可见变化，遵循对应字幕的大致先后。可以使用有归属的短字或图案气泡，\n')
    system = """你是动态过程导演 Agent 3。图片尚未定稿，依据 intent、source_subtitles、
visual_description 草案、visual_design、motion_basis、progression_plan，为每个动态镜头设计共享阶段方案。
核心图的主体场面优先接近视频前段，可作为自然起点；图中复合素材不要求在视频里同时存在。
""" + expression_handoff + """按原文和 Agent2 的表达手段展开，不给没有气泡的实际场景强行加气泡。
只负责动态表达：怎样通过动作、状态变化、空间关系或合理切镜，让观众理解本段含义。
不要只对静态画面泛泛推拉镜头；如果轻微动作足以表达，也不要无意义加戏。
保留既定人物身份、画风、核心场景和原文立场，不选择新的参考素材。
引用或假设不能变成事实；不新增人物关系、数字、证据，不将字幕内所有名词都变成道具。
以可见的先后过程描述动作与必要运镜，关键表达在 duration 秒内完成，不逐字卡点。
generation_duration 超出 duration 的部分只自然停留，避免关键动作被裁掉。
允许围绕同一表达的合理切镜；不安排超出时长的大段剧情，不要求对口型，静音。
引用现有参考图时只使用 numbered_references 中的图号，图1是即将定稿的核心画面参考。
后续没有参考图的景物仍可按有原文依据的方案描述生成；检查动作能否在构图中成立。
method_example 仅说明方法，不能将示例的题材、人物、画风、镜头数或情绪结局套入当前段落。
返回 {shots:[{id,motion_plan:{version:2,scene_anchor:"稳定场景",participants:["主体名"],
beats:[{action:"本阶段的具体可见变化",texts:[{text:"短字原文",owner:"主体名或画面标注",container:"容器"}]}],
reference_beat:1,reference_visual:"前段场面为主的单张核心参考画面",
reference_participants:["核心图中实际可见主体"],
reference_texts:[{text:"参考图实际短字",owner:"核心图主体名或画面标注",container:"容器"}]}}]}。
id及顺序保持不变，不输出多格数量、面板或额外镜头。
若提供 validation_errors，只修复指出的方案交接问题，保持原文、既定表达与顺序，返回完整 shots。""" + MOTION_CONTRACT + text_mode_contract(context) + SPEECH_ATTRIBUTION_CONTRACT
    payload = {'story_context': context, 'shots': prepared,
               'method_example': (VISUAL_FIRST_MOTION_DESIGN_EXAMPLE
                                  if dynamic_text_mode(context) == VISUAL_FIRST else MOTION_DESIGN_EXAMPLE)}
    for attempt in range(2):
        response = ask(system, payload)
        try:
            rows = _complete_rows(response, [shot['id'] for shot in prepared], '动态过程导演')
            for shot, row in zip(prepared, rows):
                plan = restore_reference_draft(row.get('motion_plan'), shot['visual_description'])
                if not plan:
                    raise ValueError(f"镜头 {row['id']} 缺少动态阶段方案")
                if plan['version'] != 2:
                    raise ValueError(f"镜头 {row['id']} 需要 version=2，分别填写核心图与各阶段的主体、短字")
                if dynamic_text_mode(context) == VISUAL_FIRST:
                    text_issues = visual_first_plan_issues(plan)
                    if text_issues:
                        # Text density is an artistic preference, not a broken
                        # motion-plan contract. Give the director one repair
                        # pass, then preserve an otherwise valid plan with a
                        # review warning instead of trapping the whole job.
                        if attempt == 0:
                            raise ValueError(f"镜头 {row['id']} 的少字方案建议精简：" + '；'.join(text_issues))
                        row['text_policy_warnings'] = text_issues
                issues = prompt_plan_issues(plan['reference_visual'], plan, 'image')
                # This is a director's draft, not the submitted image prompt.
                # The image finalizer receives the structured subjects/texts
                # and resolves these omissions before the final prompt gate.
                row['handoff_notes'] = issues
                row['motion_plan'] = plan
                row['action'] = render_motion_action(plan)
            return rows
        except ValueError as exc:
            if attempt:
                raise ValueError(f'动态阶段方案修订仍未通过：{exc}') from exc
            payload = {**payload, 'previous_result': response, 'validation_errors': [str(exc)]}
    raise ValueError('动态阶段规划失败')


def write_video_prompts(context: dict[str, Any], shots: list[dict[str, Any]],
                        references: list[dict[str, Any]], ask: Ask = ask_json, on_draft=None) -> list[dict[str, Any]]:
    prepared = _numbered_dynamic_shots(shots, references)
    if not prepared:
        return []
    expected = [shot['id'] for shot in prepared]
    text_handoff = (
        '按 beats 顺序逐阶段写清动作、表情、反应和图案的出现与消失；保留 participants 名称与想象归属，\n'
        '仅延续 texts 中已选的少量必要文字（可含简短反应词、标签和场景文字）的精确原文、owner 名称与合理 container，'
        '不擅自新增文字或把短词扩写成大段对白、口播解释。'
        '前一阶段短字未在下一阶段列出，就明确它消失；保留的则延续，不能累计所有气泡。\n'
        if dynamic_text_mode(context) == VISUAL_FIRST else
        '按 beats 顺序逐阶段写清动作、反应和气泡/图案的出现与消失；保留 participants 名称、texts 的精确原文、\n'
        'owner 名称与 container。前一阶段短字未在下一阶段列出，就明确它消失；保留的则延续，不能累计所有气泡。\n')
    system = """你是视频提示词定稿 Agent 5，不是导演。将既定 motion_plan 和 action 整理成视频模型最终提示词，
不得改变表达目的或重新设计剧情，只整理计划里的主体、动作和后续景物。开头说明本镜应保持的人物、
画风和场景，全部从当前任务取值。仅可引用 numbered_references 中实际存在的图号。
程序会在保存前加上固定的单图参考指导语，不要自行改写它，也不要重复解释参考图与首帧的关系。
按时间顺序写可见动作和必要镜头运动，
明确关键动作在 duration 秒内完成，generation_duration 多出的时间自然停留。默认静音，不要求逐字对口型。
所有视频都禁止擅自生成字幕、自动字幕、底部字幕条、旁白转写、标题卡或水印。阶段方案明确批准的
少量场景内文字仍按原文和归属出现，但不得把 source_subtitles、旁白或动作说明复制到屏幕上。
""" + text_handoff + """核对 semantic.speech_turns：旁白转述不等于画面主角发言；问题、回应与主观想象保持各自归属，
不把不同人的气泡移到主角头上。动作正文的说话/思考主体、气泡指向和 texts.owner 必须一致；
图案气泡也一样，不能用“大家讨论”等泛称抹平具体的提问方与回应方。
image_prompt 是单张核心参考，可能包含跨阶段的素材；视频按完整 beats 展开，不能只把参考图推拉摇移。
参考图里的 reference_texts 不要求全程保留，也不强制加入没有该字的阶段。后续景物可以不在核心图里，
若已由动态方案设计就完整写入；不因“不新增角色/元素”的泛化限制而删掉方案中的后续内容。
完成最后阶段后停在对应结果或自然延续，除非原文要求回返，不回到初始素材拼全的画面。
ending_prompt 必须写具体结束状态：哪些主体、表情、物体或气泡保留，哪些已经消失，余下时间如何自然停留；
不能用“恢复参考图”“保持图1构图”代替结局。造型与画风一致不意味着尾帧必须复现参考图。
提示词详细到动作能执行，但不加散文评价、不复述全部原文、不添加不存在的动作，不写接口参数。
version=2 每镜返回 {id,continuity_prompt:"本镜的造型场景保持要求",
beat_prompts:[{beat:1,prompt:"第一阶段的自然语言动作段落"},...],ending_prompt:"结束状态、尾部停留与必要限制"}。
beat_prompts 必须按 beats 原顺序一一对应，不省略任何阶段，也不插入新阶段；每段明确主体、动作对象、
反应与图文变化。使用连贯简洁的句子，图案写具体景物，不能只写“展示对应内容”。
旧 version=1 或无 motion_plan 的镜头仍返回 {id,video_prompt:"..."}。
顶层统一返回 {shots:[...]}，镜头顺序不变。""" + text_mode_contract(context)
    def present(rows):
        result = []
        for row in rows:
            body = row['video_prompt'].strip()
            while body.startswith(SINGLE_REFERENCE_GUIDE):
                body = body[len(SINGLE_REFERENCE_GUIDE):].lstrip()
            result.append({'id': row['id'], 'video_prompt': enforce_no_auto_subtitles(SINGLE_REFERENCE_GUIDE + '\n' + body),
                           'video_prompt_warnings': row.get('video_prompt_warnings', [])})
        return result
    rows = _finalize_prompts(system, {"story_context": context, "shots": prepared}, prepared,
                             'video_prompt', 'video', expected, ask, assemble=_assemble_video_body,
                             on_draft=(lambda rows: on_draft(present(rows))) if on_draft else None)
    return present(rows)


def _assemble_video_body(shot, row):
    """Preserve one finalized paragraph for every planned phase before persistence."""
    plan = shot.get('motion_plan') or {}
    if plan.get('version') != 2:
        return
    parts = row.get('beat_prompts')
    if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
        raise ValueError(f"{shot['id']} 缺少逐阶段定稿段落")
    expected = list(range(1, len(plan['beats']) + 1))
    if any(type(part.get('beat')) is not int for part in parts) or [part['beat'] for part in parts] != expected:
        raise ValueError(f"{shot['id']} 的定稿阶段缺失、重复或顺序改变，必须对应 {expected}")
    texts = [row.get('continuity_prompt')] + [part.get('prompt') for part in parts] + [row.get('ending_prompt')]
    if any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError(f"{shot['id']} 的场景、阶段或结尾定稿为空")
    row['video_prompt'] = '\n'.join(text.strip() for text in texts)


def _finalize_prompts(system, payload, shots, field, medium, expected, ask, assemble=None, on_draft=None):
    # One bounded correction on a concrete handoff error, not another director pass.
    system += '\n若提供 validation_errors，仅修复指出的漏项或矛盾，不重新导演；仍返回完整 shots。'
    for attempt in range(2):
        response = ask(system, payload)
        try:
            rows = _complete_rows(response, expected, '提示词定稿 Agent')
            problems, notes, candidates = [], [], []
            for shot, row in zip(shots, rows):
                if assemble:
                    try:
                        assemble(shot, row)
                    except ValueError as exc:
                        problems.append(str(exc))
                        continue
                prompt = row.get(field)
                if not isinstance(prompt, str) or not prompt.strip():
                    problems.append(f"{shot['id']} 缺少最终提示词")
                    continue
                # Purpose text is metadata, not a requirement to draw these words.
                content = without_leading_intent(prompt) if medium == 'image' else prompt
                if not content:
                    problems.append(f"{shot['id']} 缺少实际画面内容，不能只有表达目的")
                    continue
                row[field + '_warnings'] = prompt_plan_issues(content, shot.get('motion_plan'), medium)
                if dynamic_text_mode(payload.get('story_context') or {}) == VISUAL_FIRST:
                    # Unlike lexical handoff notices, this is an actionable
                    # policy violation: Agent 5 invented visible dialogue that
                    # the structured motion director never selected.
                    visual_issues = visual_first_prompt_issues(content, shot.get('motion_plan'))
                    if visual_issues:
                        problems.extend(f"{shot['id']}：{issue}" for issue in visual_issues)
                        continue
                # Names/container synonyms are lexical advisories, not grounds
                # for another paid finalizer call. Only actionable conflicts or
                # missing selected display text get one bounded correction.
                notes.extend(f"{shot['id']}：{issue}" for issue in row[field + '_warnings']
                             if prompt_notice_level(issue) == 'warning')
                candidates.append(row)
            # Preserve usable candidates even when another shot or a later repair fails.
            # Warnings describe a literal comparison, not a reliable semantic verdict.
            if on_draft and candidates:
                on_draft(candidates)
            if not problems and (not notes or attempt):
                return rows
            raise ValueError('；'.join(problems + notes))
        except ValueError as exc:
            if attempt:
                raise ValueError(f'提示词与阶段方案核对失败：{exc}') from exc
            payload = {**payload, 'previous_result': response, 'validation_errors': [str(exc)]}
    raise ValueError('提示词定稿失败')


def audit_storyboard(shots: list[dict[str, Any]], reference_ids: set[str]) -> list[dict[str, Any]]:
    """Deterministic final gate; it never asks an Agent to reinterpret a shot."""
    result = []
    for shot in shots:
        problems = []
        if shot.get('design_needs_review'):
            problems.append('字幕范围已调整，请先更新受影响镜头设计，或检查后确认沿用当前设计')
        if not str(shot.get("intent") or "").strip():
            problems.append("缺少本镜头表达目的")
        prompt = str(shot.get("image_prompt") or "").strip()
        if not prompt:
            problems.append("缺少核心分镜图提示词")
        selected = shot.get("reference_ids", [])
        if any(identity not in reference_ids for identity in selected):
            problems.append("引用了不存在的参考素材")
        if shot.get("kind") == "video":
            # Legacy/manual drafts may have no plan. Never regenerate them just by opening.
            try:
                normalize_motion_plan(shot.get('motion_plan'))
            except ValueError as exc:
                problems.append(str(exc))
            if not str(shot.get("action") or "").strip():
                problems.append("动态镜头缺少动作过程")
            video_prompt = str(shot.get("video_prompt") or "").strip()
            if not video_prompt:
                problems.append("动态镜头缺少视频提示词")
            elif not re.search(r"(?:参考)?图\s*1", video_prompt):
                problems.append("视频提示词没有说明图1核心分镜参考")
            if not 4 <= int(shot.get("generation_duration") or 0) <= 15:
                problems.append("视频请求时长不在4～15秒")
            if float(shot.get("duration") or 0) > 15:
                problems.append("动态镜头可用时长超过15秒")
        elif shot.get("action") or shot.get("video_prompt"):
            problems.append("静态镜头残留动态指令")
        audited = dict(shot)
        # Presentation is not an execution requirement. Also retire saved
        # cosmetic warnings from the older gate without touching the prompt.
        audited['image_prompt_warnings'] = [message for message in shot.get('image_prompt_warnings', [])
                                            if prompt_notice_level(message) != 'format']
        audited["audit"] = {"ready": not problems, "problems": problems}
        result.append(audited)
    failures = [(row["id"], row["audit"]["problems"]) for row in result if not row["audit"]["ready"]]
    if failures:
        raise ValueError(f"动态分镜最终校验未通过：{failures}")
    return result
