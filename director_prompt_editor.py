"""Beta-only final copy editing; never owns image identities or timing."""
import copy
import json
import re

from backend.app.gemini_client import GeminiError, generate_gemini_text, parse_json_response
from backend.app.reference_materials import material_is_character, reference_metadata, required_every_shot_labels


EDITOR_CONTRACT = """你是提示词定稿员，不是导演。按输入既定方案整理出图文字。
合并重复人物描述，删除‘用户明确设定’‘本镜头服装=’等内部管理措辞。
保留画风、人物外貌与服装、动作、构图、事实/设想边界、参考图编号和必要限制。
不得新增人物、道具、数字、剧情，不得改变表达目的，不得重新选择场景。
如果组装文字与既定方案矛盾，返回 conflicts 说明，禁止自行选择牺牲哪条要求。
返回 JSON {items:[{index:原编号,image_prompt:整理后的文字,human_presence:"none/present/partial/unspecified",conflicts:[]}]}。
human_presence 依据本镜头既定画面：明确纯环境或静物、无人出镜为 none；有人含背景群众为 present；仅手部或身体局部为 partial；无法确定为 unspecified。角色ID为空不等于无人，不能删除群众、手部、照片中的人物等既定内容。不得因全文存在主角就在空镜里添加主角。
文字按【人物与画风】【画面内容】【必要限制】组织；不要自行写‘本图旨在’，程序会使用原定目的。
必须返回所有项目，顺序不变。不要输出其他字段。
若输入包含 reference_materials，它是本镜头已经选中的素材：description 是用户可编辑用途，以此为准，kind 仅为初次自动识别提示。不得把所有素材视为角色。
素材 description 若明确要求“每张图、每个画面、所有镜头或全程”使用，则该素材必须实际出现在画面中，不得判定为与之冲突的无人镜头，也不得从 retained_reference_image_ids 删除。
核对后确认空镜时额外返回 retained_reference_image_ids，仅保留仍符合空镜既定用途的已选素材编号；无关人物参考应移除，界面/物件/环境及只借用非人物部分的素材不因空镜而移除。不得新增或重新选择参考图。"""


def _order_sections(prompt, no_people=False):
    titles = ("人物与画风", "画面内容", "必要限制")
    parts = re.split(r"【(人物与画风|画面内容|必要限制)】", prompt.strip())
    if len(parts) == 7 and not parts[0].strip() and set(parts[1::2]) == set(titles):
        sections = dict(zip(parts[1::2], parts[2::2]))
        if no_people:
            sections["人物与画风"] = sections["人物与画风"].strip() + "；纯场景或静物画面，无人物出镜，不添加人物、人体局部、人影或人形倒影。"
        return "\n".join(f"【{title}】{sections[title].strip()}" for title in titles)
    if no_people:
        prompt += "\n【必要限制】纯场景或静物画面，无人物出镜，不添加人物、人体局部、人影或人形倒影。"
    return prompt.strip()


def finalize_prompts(mapping, scenes, story_plan, *, allow_correction=True):
    """One batched call. Validate all results before mutating any prompt."""
    material_catalog = {row.get('label'): row for row in reference_metadata()}
    inputs = [{"index": i, "image_prompt": item["image_prompt"],
               "character_ids": item.get("character_ids", []),
               "visual_design": item.get("visual_design", {}),
               "reference_image_ids": item.get("reference_image_ids", [])}
              for i, item in enumerate(mapping)]
    if material_catalog:
        for item in inputs:
            item['reference_materials'] = [material_catalog[label] for label in item['reference_image_ids'] if label in material_catalog]
    response = parse_json_response(generate_gemini_text(
        system_prompt=EDITOR_CONTRACT,
        user_prompt=json.dumps({"items": inputs, "source": scenes,
                                "characters": story_plan.get("characters", [])}, ensure_ascii=False),
        temperature=0.05, response_mime_type="application/json", max_output_tokens=12288))
    rows = response.get("items") if isinstance(response, dict) else None
    if not isinstance(rows, list) or len(rows) != len(mapping):
        raise ValueError("提示词定稿结果缺项，已在出图前停止")
    # Validate identity before associating a model response with a frame.
    if any(not isinstance(row, dict) or row.get("index") != i for i, row in enumerate(rows)):
        raise ValueError("提示词定稿顺序不一致，已在出图前停止")
    required_character_indices = {
        i for i, item in enumerate(inputs)
        if any(material_is_character(material)
               and material.get('label') in required_every_shot_labels(item.get('reference_materials', []))
               for material in item.get('reference_materials', []))
    }
    presence_conflicts = [i for i, row in enumerate(rows)
                          if row.get("human_presence") == "none"
                          and (mapping[i].get("character_ids") or i in required_character_indices)]
    presence_audit = {}
    if presence_conflicts:
        for i in presence_conflicts:
            presence_audit[i] = {"original_row": copy.deepcopy(rows[i]), "original_input": inputs[i]}
            print(f"定稿人物核对：{mapping[i].get('macro_scene_id', i + 1)} 被判无人，但角色名单为 {mapping[i].get('character_ids')}；正在核对一次。", flush=True)
        try:
            checked = parse_json_response(generate_gemini_text(
                system_prompt=EDITOR_CONTRACT + "\n仅核对下列冲突项。角色卡不能单独作为出镜证据。以既定 visual_design、镜头正文的主体和动作及本组原文为依据。若正文明确有人，保留人物动作并纠正无人标签。若明确只展示环境或静物、角色名单是沿用全文人物而非实际出镜，允许返回 human_presence=none，并额外返回 empty_scene_confirmed=true、evidence=具体判定依据；同时从整理文字删除所有人物造型、人物动作及人物参考指令。涉及手部、群众、人像或无法确认时不得确认空镜，返回 conflicts。不能因为原文没有姓名就判断无人。",
                user_prompt=json.dumps({"items": [inputs[i] for i in presence_conflicts],
                                        "previous": [rows[i] for i in presence_conflicts],
                                        "source": [{"index": i, "scenes": [s for s in scenes if s.get("slide_id") in mapping[i].get("includes_slides", [])]} for i in presence_conflicts]}, ensure_ascii=False),
                temperature=0.05, response_mime_type="application/json", max_output_tokens=4096))
            checked_rows = checked.get("items") if isinstance(checked, dict) else None
            if not isinstance(checked_rows, list) or [r.get("index") for r in checked_rows if isinstance(r, dict)] != presence_conflicts:
                raise ValueError("人物核对结果缺项或顺序不符")
            for row in checked_rows:
                rows[row["index"]] = row
                presence_audit[row["index"]]["checked_row"] = copy.deepcopy(row)
        except (GeminiError, ValueError, TypeError, RuntimeError) as exc:
            for i in presence_conflicts:
                presence_audit[i]["check_error"] = str(exc)
        for i in presence_conflicts:
            row = rows[i]
            confirmed_empty = (i not in required_character_indices
                               and row.get("human_presence") == "none"
                               and row.get("empty_scene_confirmed") is True
                               and isinstance(row.get("evidence"), str) and bool(row["evidence"].strip()))
            if (row.get("human_presence") not in {"present", "partial"} or row.get("conflicts") != []
                    or not isinstance(row.get("image_prompt"), str) or not row["image_prompt"].strip()):
                if confirmed_empty and row.get("conflicts") == [] and isinstance(row.get("image_prompt"), str) and row["image_prompt"].strip():
                    presence_audit[i]["resolution"] = "confirmed_empty_scene"
                    continue
                rows[i] = {"index": i, "image_prompt": mapping[i]["image_prompt"],
                           "human_presence": "unspecified", "conflicts": []}
                presence_audit[i]["resolution"] = "original_prompt_fallback"
                print(f"定稿人物核对：{mapping[i].get('macro_scene_id', i + 1)} 仍无法确认，保留原导演提示词和参考绑定，不添加无人限制，继续任务。", flush=True)
            else:
                presence_audit[i]["resolution"] = "corrected"
    conflicts = [{"index": i, "conflicts": row.get("conflicts")}
                 for i, row in enumerate(rows)
                 if isinstance(row, dict) and row.get("index") == i and row.get("conflicts")]
    if conflicts and allow_correction:
        corrected = parse_json_response(generate_gemini_text(
            system_prompt="你是 Agent2 画面导演。仅修复报告的提示词矛盾，不改变表达目的、原文事实、角色、用户设定、参考图和画风。返回 {items:[{index,image_prompt}]}，仅返回有冲突的项。无法安全修复则返回空列表。",
            user_prompt=json.dumps({"items": inputs, "conflicts": conflicts, "source": scenes}, ensure_ascii=False),
            temperature=0.1, response_mime_type="application/json", max_output_tokens=8192))
        fixes = corrected.get("items") if isinstance(corrected, dict) else None
        expected = [row["index"] for row in conflicts]
        if not isinstance(fixes, list) or [row.get("index") for row in fixes if isinstance(row, dict)] != expected:
            raise ValueError("导演未能安全修复提示词冲突，已在出图前停止")
        updated = copy.deepcopy(mapping)
        for fix in fixes:
            if not isinstance(fix.get("image_prompt"), str) or not fix["image_prompt"].strip():
                raise ValueError("导演纠正返回空提示词")
            item = updated[fix["index"]]
            item["prompt_correction"] = {"before_prompt": item["image_prompt"], "conflicts": conflicts,
                                         "after_prompt": fix["image_prompt"]}
            item["image_prompt"] = fix["image_prompt"]
        return finalize_prompts(updated, scenes, story_plan, allow_correction=False)
    result = copy.deepcopy(mapping)
    for index, (item, row) in enumerate(zip(result, rows)):
        if not isinstance(row, dict) or row.get("index") != index:
            raise ValueError("提示词定稿顺序不一致，已在出图前停止")
        if row.get("conflicts") != []:
            raise ValueError(f"画面 {index + 1} 提示词存在冲突，需重新规划：{row.get('conflicts')}")
        prompt = row.get("image_prompt")
        purpose = str(item.get("visual_design", {}).get("message") or "").strip()
        if not isinstance(prompt, str) or not prompt.strip() or not purpose:
            raise ValueError("提示词定稿缺少正文或表达目的，已在出图前停止")
        presence = row.get("human_presence", "unspecified")
        if presence_audit.get(index, {}).get("resolution") == "confirmed_empty_scene":
            # Remove unused character inputs without dropping non-character
            # materials. Generated scene references are bound separately later.
            item["character_ids"] = []
            selected = item.get('reference_image_ids', [])
            retained = row.get('retained_reference_image_ids')
            if (material_catalog and isinstance(retained, list)
                    and all(isinstance(label, str) and label in selected and label in material_catalog for label in retained)):
                item['reference_image_ids'] = list(dict.fromkeys(retained))
            else:
                item['reference_image_ids'] = [label for label in selected
                    if label in material_catalog and material_catalog[label].get('kind') != 'character']
            item.pop("reference_image_paths", None)
            item["human_presence"] = "none"
            print(f"定稿人物核对：{item.get('macro_scene_id', index + 1)} 确认为纯场景，已移除角色名单和人物参考；依据：{row['evidence']}", flush=True)
        prompt = _order_sections(prompt, no_people=presence == "none")
        item["prompt_editor"] = {"version": 2, "human_presence": presence, "before_prompt": item["image_prompt"],
                                 "purpose": purpose, "status": "completed"}
        item["image_prompt"] = f"【本图旨在】{purpose}\n{prompt.strip()}"
        item["prompt_editor"]["after_prompt"] = item["image_prompt"]
        if index in presence_audit:
            item["prompt_editor"]["presence_check"] = presence_audit[index]
            item["prompt_editor"]["status"] = presence_audit[index]["resolution"]
    return result
