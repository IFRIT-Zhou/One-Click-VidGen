"""Scene continuity planning after directing, isolated from stable mode."""
import copy
import hashlib
import json
import re
from pathlib import Path

from backend.app.gemini_client import generate_gemini_text, parse_json_response


CONTRACT = """你是场景协调员，不是导演。不得修改任何镜头方案。
只找出最终画面中需要维持同一真实空间外观的重复场景。
旁白谈话地点不等于画面地点；抽象意象、未来设想、混合场景、拼贴不绑定。
同一空间的全景与静物特写可以绑定；不同时间中布局已变化的场景分别处理。
输出前逐项复查未绑定镜头：同一场景中的桌面、文件、家具等局部特写也需要引用该场景，不能因为被标记为 asset_display 就排除。只有外部独立素材而非场景内物件才独立处理。以具体场景关系为依据，不按标签或关键词绑定。
仅返回至少两张画面明确共用的场景，不确定则不绑定。每张图最多一个场景。
返回 {scenes:[{name:"名称",members:[输入镜头的index],
reference_prompt:"无人场景参考图提示词",reason:"同一场景的依据"}]}。
参考图清楚展示空间布局、家具和关键物件；无人、无人影、无人形倒影，不添加原文没有的剧情证据或精确数字。
不强制照搬角度，不把同一关键词视为同一场景。可以返回空列表。最多四个重复场景。
参考图提示词必须保留用户画风，描述背景资产而非人物动作。"""


def assign_scene_ids(raw):
    """Model labels are descriptive only; internal identifiers are program-owned."""
    result = copy.deepcopy(raw)
    if isinstance(result, dict) and isinstance(result.get("scenes"), list):
        for index, row in enumerate(result["scenes"], 1):
            if isinstance(row, dict):
                row["scene_id"] = f"location_{index}"
    return result


def validate_plan(raw, mapping):
    rows = raw.get("scenes") if isinstance(raw, dict) else None
    if not isinstance(rows, list) or len(rows) > 4:
        raise ValueError("场景协调结果格式不正确")
    occupied, identifiers = set(), set()
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(r"location_[a-zA-Z0-9_]+", str(row.get("scene_id", ""))):
            raise ValueError("场景编号无效")
        members = row.get("members")
        if not isinstance(members, list) or len(members) < 2:
            raise ValueError("场景参考必须有至少两个使用镜头")
        if row["scene_id"] in identifiers or any(type(i) is not int or i < 0 or i >= len(mapping) or i in occupied for i in members) or len(set(members)) != len(members):
            raise ValueError("场景绑定重复或越界")
        if not str(row.get("reference_prompt") or "").strip() or not str(row.get("reason") or "").strip():
            raise ValueError("场景参考缺少提示词或依据")
        for i in members:
            design = mapping[i].get("visual_design") or {}
            if design.get("fact_status") in {"hypothetical", "metaphorical"} or design.get("expression") in {"metaphor", "explanatory"}:
                raise ValueError("设想、隐喻或组合镜头不能自动绑定真实场景")
        occupied.update(members)
        identifiers.add(row["scene_id"])
    return rows


def filter_eligible_members(raw, mapping):
    """Optional continuity must not turn an ineligible shot into a job failure."""
    result = copy.deepcopy(raw)
    rows = result.get("scenes") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return result
    accepted, exclusions = [], []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("members"), list):
            accepted.append(row)
            continue
        members = []
        for index in row["members"]:
            # Invalid identities remain errors: never guess a corrected index.
            if type(index) is not int or not 0 <= index < len(mapping):
                members.append(index)
                continue
            design = mapping[index].get("visual_design") or {}
            if design.get("fact_status") in {"hypothetical", "metaphorical"} or design.get("expression") in {"metaphor", "explanatory"}:
                exclusions.append({"scene_id": row.get("scene_id"), "index": index,
                                   "reason": "设想、隐喻或组合镜头不绑定真实场景"})
                print(f"场景参考：跳过 {mapping[index].get('macro_scene_id', index + 1)}，设想、隐喻或组合镜头保持独立出图。", flush=True)
            else:
                members.append(index)
        row["members"] = members
        if len(members) >= 2:
            accepted.append(row)
        elif all(type(i) is int and 0 <= i < len(mapping) for i in members):
            exclusions.append({"scene_id": row.get("scene_id"), "reason": "有效镜头不足两张，取消该场景参考"})
            print(f"场景参考：{row.get('name') or row.get('scene_id')} 有效镜头不足两张，不生成额外参考图。", flush=True)
        else:
            accepted.append(row)
    result["scenes"] = accepted
    result["exclusions"] = exclusions
    return result


def plan_scene_references(mapping, scenes, cache_path, style="", *, contract=None):
    source = {"version": 2, "items": [{"index": i, "prompt": m["image_prompt"],
               "visual_design": m.get("visual_design", {})} for i, m in enumerate(mapping)],
              "source": scenes, "style": style}
    if contract is not None:
        source["coordination_contract"] = contract
    fingerprint = hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cache_path = Path(cache_path)
    if cache_path.is_file():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("fingerprint") == fingerprint:
            validate_plan(cached, mapping)
            return cached
    raw = parse_json_response(generate_gemini_text(system_prompt=contract or CONTRACT,
        user_prompt=json.dumps(source, ensure_ascii=False), temperature=0.05,
        response_mime_type="application/json", max_output_tokens=4096))
    filtered = filter_eligible_members(assign_scene_ids(raw), mapping)
    rows = validate_plan(filtered, mapping)
    result = {"version": 1, "fingerprint": fingerprint, "scenes": rows,
              "exclusions": filtered.get("exclusions", [])}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(cache_path)
    return result


def bind_scene_references(mapping, plan, paths, catalog):
    """Bind exact local inputs and renumber references, never send the whole catalog."""
    rows = validate_plan(plan, mapping)
    result = copy.deepcopy(mapping)
    for scene in rows:
        path = Path(paths[scene["scene_id"]])
        if not path.is_file() or not path.stat().st_size:
            raise ValueError("场景参考图不存在或为空")
        for index in scene["members"]:
            item = result[index]
            selected = list(item.get("reference_image_paths") or [])
            rename = {}
            if not selected:
                for ref in item.get("reference_image_ids", []):
                    if ref not in catalog:
                        raise ValueError("镜头角色参考编号不存在")
                    if catalog[ref] not in selected:
                        selected.append(catalog[ref])
                    rename[ref] = f"图{selected.index(catalog[ref]) + 1}"
            if len(selected) >= 4:
                raise ValueError("当前镜头参考图已满，不能静默丢弃参考图")
            if rename:
                item["image_prompt"] = re.sub(r"图\s*\d+", lambda m: rename.get(m.group().replace(" ", ""), m.group()), item["image_prompt"])
            selected.append(str(path.resolve()))
            item["reference_image_paths"] = selected
            item["scene_reference"] = {"scene_id": scene["scene_id"], "name": scene.get("name", ""),
                                       "prompt": scene["reference_prompt"],
                                       "path": str(path.resolve()), "input_number": len(selected)}
            item["image_prompt"] += f"\n【场景参考】图{len(selected)}仅用于保持同一空间的布局、家具与关键物件外观；以本镜头动作和构图为准，不照搬视角，不引入参考图之外的人物。"
    return result
