"""Opt-in, authenticated production handoff. Never starts model/media jobs."""
from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import require_user
from . import video_studio as studio
from .video_plan import normalize_shots
from .video_group_repair import validate_group_structure
from .video_motion_plan import normalize_motion_plan, render_motion_action, prompt_plan_issues
from .video_agents import audit_storyboard

router = APIRouter(prefix='/api/codex-bridge', tags=['Codex production bridge'])
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT = PROJECT_ROOT / 'workspace' / 'codex_bridge'
PLUGIN_ID = 'codex_bridge'
DRAFT_FIELDS = set('project_name script content_mode video_orientation visual_style_prompt global_character_prompt story_environment_prompt reference_image_ids reference_image_notes reference_image_labels reference_image_kinds tts_engine tts_voice_id tts_speed tts_emotion tts_emotion_weight cluster_voice_type cluster_voice_id qwen_tts_voice qwen_tts_instructions image_resolution video_render_variant video_generation_backend comfyui_profile_id dynamic_text_mode scene_references_enabled'.split())


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Draft(StrictModel):
    # Caller-chosen UUID makes retry after an uncertain network response safe.
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    revision: int = Field(default=0, ge=0)
    parameters: dict[str, Any]


class Shot(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    slide_ids: list[str] = Field(min_length=1, max_length=1000)
    kind: Literal['static', 'video']
    intent: str = Field(min_length=1, max_length=3000)
    image_prompt: str = Field(min_length=1, max_length=20000)
    reference_ids: list[str] = Field(default_factory=list, max_length=8)
    motion_plan: dict[str, Any] = Field(default_factory=dict)
    video_prompt: str = Field(default='', max_length=20000)
    evidence: str = Field(default='', max_length=6000)


class Plan(StrictModel):
    revision: int = Field(ge=1)
    timeline_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    audio_confirmed: Literal[True]
    shots: list[Shot] = Field(min_length=1, max_length=500)


class AudioHandoff(StrictModel):
    job_id: str = Field(min_length=1, max_length=80)
    audio_confirmed: Literal[True]


class ShotPatch(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    intent: str | None = Field(default=None, min_length=1, max_length=3000)
    image_prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    reference_ids: list[str] | None = Field(default=None, max_length=4)
    motion_plan: dict[str, Any] | None = None
    video_prompt: str | None = Field(default=None, max_length=20000)
    evidence: str | None = Field(default=None, max_length=6000)

    @model_validator(mode='after')
    def explicit_values(self):
        fields = self.model_fields_set - {'id'}
        if not fields or any(getattr(self, key) is None for key in fields):
            raise ValueError('每镜至少提供一个修改字段；省略表示保持原值，不接受 null')
        return self


class ShotPatches(StrictModel):
    revision: int = Field(ge=1)
    timeline_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    audio_confirmed: Literal[True]
    shots: list[ShotPatch] = Field(min_length=1, max_length=500)


def plugin_valid(folder: Path) -> bool:
    try:
        root = (PROJECT_ROOT / 'plugins').resolve()
        folder.resolve().relative_to(root)
        if (folder / 'plugin.json').stat().st_size > 256 * 1024:
            return False
        data = json.loads((folder / 'plugin.json').read_text(encoding='utf-8-sig'))
        return (folder.name == PLUGIN_ID and data.get('id') == PLUGIN_ID and
                data.get('manifest_version') == 1 and data.get('type') == 'production_bridge')
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def enabled():
    folder = PROJECT_ROOT / 'plugins' / PLUGIN_ID
    return plugin_valid(folder) and not (folder / 'disabled').exists()


def user_for(request):
    user = require_user(request)
    if not enabled():
        raise HTTPException(404, 'Codex 制作桥未安装或已停用，请在插件与设置中启用')
    return user


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_record(path):
    # Do not call studio.read here: its recovery hooks can change a project.
    try:
        return json.loads((path / 'record.json').read_text(encoding='utf-8-sig'))
    except FileNotFoundError as exc:
        raise HTTPException(404, '项目不存在') from exc


def timeline_token(record, path):
    audio = str(record.get('audio') or '')
    candidate = (path / audio).resolve()
    stamp = None
    if audio and path.resolve() in candidate.parents and candidate.is_file():
        stat = candidate.stat()
        stamp = [stat.st_size, stat.st_mtime_ns]
    return digest({'scenes': record.get('scenes'), 'audio': audio, 'stamp': stamp,
                   'narration_groups': record.get('narration_groups', [])})


def brief(record):
    return {key: record.get(key) for key in ('id', 'revision', 'status', 'updated_at')} | {
        'name': record.get('settings', {}).get('name', ''), 'shot_count': len(record.get('shots', [])),
        'source_job_id': record.get('source_project', {}).get('id', '')}


def compact_pack(record, path):
    keys = ('id', 'slide_ids', 'kind', 'intent', 'image_prompt', 'video_prompt', 'motion_plan', 'reference_ids',
            'image_material_numbers_bound', 'image_prompt_out_of_sync', 'image_status', 'video_status', 'prompt_refresh_note')
    return {'schema_version': 1, **brief(record), 'timeline_token': timeline_token(record, path),
            'settings': {k: record.get('settings', {}).get(k) for k in ('style', 'characters', 'world', 'ratio')},
            'subtitles': [{k: row.get(k) for k in ('slide_id', 'start', 'end', 'text')} for row in record.get('scenes', [])],
            'narration_groups': record.get('narration_groups', []),
            'references': [{k: row.get(k, '') for k in ('id', 'label', 'description', 'kind')} |
                           {'url': f"/api/codex-bridge/projects/{record['id']}/references/{row['id']}"}
                           for row in record.get('references', [])],
            'shots': [{k: row.get(k) for k in keys if k in row} for row in record.get('shots', [])],
            'evidence': record.get('codex_bridge', {}).get('evidence', {}),
            'audio_url': f"/api/video-studio/{record['id']}/audio" if record.get('audio') else None,
            'rules': ['配音须由用户确认；字幕只读，按 slide_id 完整顺序覆盖一次',
                      '动态镜头最长15秒；静态镜头不限；不要自行计算或提交时间戳',
                      '不得在字幕间空隙切镜（会造成合成时间缺口），将空隙两侧字幕放入同镜',
                      '完整导入只用于无素材草案；已有素材使用 patch-validate/patch-apply 局部返修，不会生成素材',
                      '局部返修提示词图号按 reference_ids 的本次选中顺序；完整导入按项目图号',
                      '结构校验不等于内容审核；事实、素材身份与授权由创作流程单独核对']}


@router.get('/info')
def info(request: Request):
    user_for(request)
    return {'version': 1, 'name': 'Codex 制作桥', 'client_path': str(PROJECT_ROOT / 'plugins' / PLUGIN_ID / 'ocv_bridge.py'),
            'skill_path': str(PROJECT_ROOT / 'plugins' / PLUGIN_ID / 'skills' / 'ocv-production-bridge' / 'SKILL.md'),
            'guide_url': '/api/codex-bridge/guide',
            'capabilities': ['drafts', 'audio_handoff', 'compact_pack', 'validate', 'apply', 'shot_patch'],
            'generates_media': False, 'schema_url': '/api/codex-bridge/schema'}


@router.get('/guide')
def guide(request: Request):
    user_for(request)
    path = PROJECT_ROOT / 'plugins' / PLUGIN_ID / 'skills' / 'ocv-production-bridge' / 'SKILL.md'
    try:
        content = path.read_text(encoding='utf-8-sig')
    except FileNotFoundError as exc:
        raise HTTPException(404, '制作桥使用指南缺失，请检查更新是否完整') from exc
    return {'name': 'ocv-production-bridge', 'skill_path': str(path), 'content': content}


@router.get('/schema')
def schema(request: Request):
    user_for(request)
    return {'plan': Plan.model_json_schema(), 'draft': Draft.model_json_schema(), 'shot_patch': ShotPatches.model_json_schema(),
            'draft_fields': sorted(DRAFT_FIELDS),
            'motion_example': {'version': 2, 'scene_anchor': '固定构图，显示本镜空间关系',
                'participants': ['主体'], 'reference_participants': ['主体'], 'reference_texts': [],
                'reference_beat': 1, 'reference_visual': '主体处于初始状态',
                'beats': [{'action': '主体发生本镜所需的可见变化，镜头保持稳定', 'texts': []}]},
            'text_entry_example': {'text': '短标签', 'owner': '主体', 'container': '画面标注'},
            'notes': ['静态镜头不填 motion_plan/video_prompt。动态必须填写 motion_plan。',
                      'shot_patch 只提交稳定 id 与要改的字段；禁止字幕、时间、动静类型、素材路径和生成设置。',
                      '局部返修 image_prompt 图号按本次 reference_ids 顺序；更换引用必须同时提交提示词。',
                      '修改 motion_plan 但省略 video_prompt 时会确定性重编视频提示词，不沿用旧文本。',
                      'video_prompt 可省略，工具按阶段方案生成；不会调用 LLM。',
                      'evidence 可保存来源位置、创作依据及表述边界，不会放进画面提示词。',
                      '参考音色用 tts_voice_id=upload:<素材id>，不能当成品旁白。']}


@router.get('/drafts')
def drafts(request: Request):
    user = user_for(request)
    with studio.LOCK:
        items = [read_record(p.parent) for p in (ROOT / str(user['id']) / 'drafts').glob('*/record.json')]
    return {'items': sorted([{'id': r['id'], 'revision': r['revision'], 'name': r['parameters']['project_name'],
                              'updated_at': r['updated_at']} for r in items], key=lambda r: r['updated_at'], reverse=True)}


def draft_path(user, identity):
    # Reuse the host's strict identity validation, but store outside video tasks.
    studio.directory(user, identity)
    return ROOT / str(user) / 'drafts' / identity


@router.get('/drafts/{identity}')
def draft_get(identity: str, request: Request):
    user = user_for(request)
    with studio.LOCK:
        return read_record(draft_path(user['id'], identity))


@router.put('/drafts')
def draft_put(data: Draft, request: Request):
    user = user_for(request)
    from .main import GenerateRequest, list_uploads
    unknown = set(data.parameters) - DRAFT_FIELDS
    if unknown:
        raise HTTPException(422, '不支持的初始参数：' + ', '.join(sorted(unknown)))
    raw = dict(data.parameters)
    raw.update(dynamic_video=True, step_mode=True, dynamic_auto_advance=False, skip_tts=False,
               content_mode=raw.get('content_mode', 'general'))
    try:
        checked = GenerateRequest(**raw).model_dump()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not checked['project_name'].strip() or not checked['script'].strip() or len(checked['script']) > 12000:
        raise HTTPException(422, '请提供项目名和1～12000字符口播稿')
    assets = {a['id']: a for a in list_uploads(int(user['id']))}
    for identity in checked['reference_image_ids']:
        if identity not in assets or assets[identity].get('kind') != 'image':
            raise HTTPException(422, f'参考素材不属于当前用户或不存在：{identity}')
    voice = checked['tts_voice_id']
    if voice.startswith('upload:') and (voice[7:] not in assets or assets[voice[7:]].get('kind') != 'audio'):
        raise HTTPException(422, '参考音色素材不存在')
    # Store only declared inputs; no API keys, inherited account routing or server defaults.
    params = {k: checked[k] for k in raw}
    with studio.LOCK:
        path = draft_path(user['id'], data.id)
        old = read_record(path) if (path / 'record.json').exists() else None
        if old and old['parameters'] == params and data.revision in (old['revision'], old['revision'] - 1):
            return old | {'generation_started': False}
        if (old['revision'] if old else 0) != data.revision:
            raise HTTPException(409, '草稿已被修改，请重新读取 revision')
        result = {'id': data.id, 'revision': data.revision + 1, 'parameters': params}
        studio.save(path, result)
        return result | {'generation_started': False}


@router.get('/projects')
def projects(request: Request):
    user = user_for(request)
    with studio.LOCK:
        rows = [brief(read_record(p.parent)) for p in (studio.ROOT / str(user['id'])).glob('*/record.json')]
    return {'items': sorted(rows, key=lambda r: r.get('updated_at') or 0, reverse=True)}


@router.get('/audio-tasks')
def audio_tasks(request: Request, page: int = 1):
    user = user_for(request)
    from .pipeline import store
    data = store.list_page(user_id=int(user['id']), page=page, page_size=100)
    return {'page': page, 'total_pages': data.get('total_pages', 1), 'items': [
        {'id': r['id'], 'name': r.get('request', {}).get('project_name', ''),
         'status': r.get('status'), 'message': r.get('message', '')}
        for r in data['jobs'] if r.get('request', {}).get('dynamic_video')]}


@router.post('/from-audio')
def from_audio(data: AudioHandoff, request: Request):
    user = user_for(request)
    with studio.LOCK:
        # This host operation commits already-produced audio, not TTS generation.
        existing = studio.find_audio_storyboard(user['id'], data.job_id)
        record = (studio.sync_edited_audio(existing['id'], studio.Review(revision=existing['revision']), request)
                  if existing else studio.import_new_project(studio.ImportProject(job_id=data.job_id, from_audio_task=True), user))
        # Never auto-generate when opening an originally one-click task.
        if record.get('creation_parameters', {}).get('dynamic_auto_advance'):
            studio.editable(record, record['revision'])
            record['creation_parameters']['dynamic_auto_advance'] = False
            record['revision'] += 1
            studio.save(studio.directory(user['id'], record['id']), record)
        return compact_pack(record, studio.directory(user['id'], record['id']))


@router.get('/projects/{identity}/pack')
def pack(identity: str, request: Request):
    user = user_for(request)
    with studio.LOCK:
        path = studio.directory(user['id'], identity)
        return compact_pack(read_record(path), path)


@router.get('/projects/{identity}/references/{reference_id}')
def reference(identity: str, reference_id: str, request: Request):
    from fastapi.responses import FileResponse
    user = user_for(request)
    path = studio.directory(user['id'], identity)
    record = read_record(path)
    row = next((r for r in record.get('references', []) if r['id'] == reference_id), None)
    target = (path / str((row or {}).get('file', ''))).resolve()
    if not row or path.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404, '参考图不存在')
    return FileResponse(target)


def file_hash(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def check_edit_context(record, path, data, user_id=None):
    studio.editable(record, data.revision)
    audio = (path / str(record.get('audio') or '')).resolve()
    if not record.get('audio') or path.resolve() not in audio.parents or not audio.is_file():
        raise ValueError('缺少真实配音文件，请完成配音并确认后再规划')
    source_job_id = record.get('source_project', {}).get('id')
    if source_job_id:
        from .tts_editor import tts_editor
        if tts_editor.status(source_job_id).get('status') == 'running':
            raise HTTPException(409, '来源配音仍在修改，请完成并同步配音后重新读取资料包')
        if user_id is not None:
            try:
                source = tts_editor._project_dir(source_job_id, int(user_id))
                scenes = studio.parse_srt((source / 'other' / '最终字幕.srt').read_text(encoding='utf-8-sig'))
                same = scenes == record['scenes'] and file_hash(source/'input'/'配音.wav') == file_hash(audio)
            except (OSError, ValueError) as exc:
                raise HTTPException(409, '无法核实来源配音，请先保存配音编辑并同步') from exc
            if not same:
                raise HTTPException(409, '来源配音或字幕已修改但尚未同步，请先保存配音编辑或 from-audio，再重新读取资料包')
    if timeline_token(record, path) != data.timeline_token:
        raise HTTPException(409, '配音或字幕已改变，请重新导出资料包')
    if str(path) in studio.ACTIVE:
        raise HTTPException(409, '项目仍在后台运行，请等待完成后再导入')


def compile_plan(record, path, data, user_id=None):
    check_edit_context(record, path, data, user_id)
    if record['status'] not in {'draft', 'storyboard_review', 'failed'} or any(
            s.get(k) for s in record.get('shots', []) for k in ('image', 'image_file', 'video', 'video_file', 'video_history', 'image_history')):
        raise HTTPException(409, '已有生成素材或不在草案阶段，不能整体覆盖；请使用 patch-validate/patch-apply 局部返修')
    rows = [s.model_dump(exclude={'evidence'}) for s in data.shots]
    validate_group_structure(rows, record['scenes'])
    scenes = {s['slide_id']: s for s in record['scenes']}
    for index, row in enumerate(rows):
        row['visual_description'] = row['motion_plan'].get('reference_visual') or row['image_prompt']
        start, end = float(scenes[row['slide_ids'][0]]['start']), float(scenes[row['slide_ids'][-1]]['end'])
        if index == 0 and start > 0.03:
            raise ValueError('首条字幕前存在空白时间，请先在 OCV 核对配音时间轴')
        if index and abs(start - float(scenes[rows[index-1]['slide_ids'][-1]]['end'])) > 0.03:
            raise ValueError(f"{row['id']} 前存在字幕时间间隙；请将间隙两侧字幕放在同一镜，避免合成错位")
        if row['kind'] == 'video':
            if end - start > 15.0001:
                raise ValueError(f"{row['id']} 动态时长 {end-start:.3f} 秒超过15秒，请按语义拆分或改为静态")
            motion = normalize_motion_plan(row['motion_plan'])
            if not motion:
                raise ValueError(f"{row['id']} 缺少动态阶段方案")
            row['action'] = render_motion_action(motion)
            if not row['video_prompt'].strip():
                row['video_prompt'] = ('图1是单张复合内容参考，不是必须照搬的首帧。按阶段依次展开，始终输出普通全屏视频。\n' +
                    '本镜主体：' + '、'.join(motion['participants']) + '。\n' +
                    row['action'] + '\n只允许阶段方案列出的文字；禁止添加字幕条、水印或将旁白转写到画面。')
            for medium, field in [('image', 'image_prompt'), ('video', 'video_prompt')]:
                issues = prompt_plan_issues(row[field], motion, medium)
                if issues:
                    raise ValueError(f"{row['id']} {field}：" + '；'.join(issues))
        elif row['motion_plan'] or row['video_prompt'].strip():
            raise ValueError(f"{row['id']} 静态镜头不应包含动态阶段/视频提示词")
    if record.get('manual_groups') and [(r['id'], r['slide_ids']) for r in rows] != [(r['id'], r['slide_ids']) for r in record['shots']]:
        raise ValueError('当前项目有手动分组，整体方案必须保留镜头ID与字幕范围')
    refs = [r['id'] for r in record.get('references', [])]
    result = audit_storyboard(normalize_shots(rows, record['scenes'], refs), set(refs))
    return result


def review_plan(identity, data, request, commit):
    user = user_for(request)
    with studio.LOCK:
        path = studio.directory(user['id'], identity)
        record = read_record(path)
        receipt = digest(data.model_dump())
        if record.get('codex_bridge', {}).get('receipt') == receipt and record.get('codex_bridge', {}).get('applied_revision') == record['revision']:
            return {'ok': True, 'already_applied': True, **brief(record), 'generation_started': False}
        try:
            shots = compile_plan(record, path, data, user['id'])
        except (ValueError, KeyError, TypeError) as exc:
            return {'ok': False, 'errors': [str(exc)], 'written': False, 'generation_started': False}
        old = {s['id']: s for s in record.get('shots', [])}
        changed = [s['id'] for s in shots if any(s.get(k) != old.get(s['id'], {}).get(k)
                   for k in ('slide_ids', 'kind', 'image_prompt', 'video_prompt', 'motion_plan', 'reference_ids'))]
        summary = {'ok': True, 'project_id': identity, 'revision': record['revision'],
                   'shot_count': len(shots), 'dynamic_count': sum(s['kind'] == 'video' for s in shots),
                   'changed': changed, 'removed': sorted(set(old) - {s['id'] for s in shots}),
                   'written': False, 'generation_started': False}
        if not commit:
            return summary
        backup_id = f"{record['revision']}-{uuid.uuid4().hex}"
        backup = path / 'codex_bridge_backups' / backup_id
        studio.save(backup, copy.deepcopy(record))
        updated = copy.deepcopy(record)
        updated.update(shots=shots, revision=record['revision'] + 1, status='storyboard_review', error='')
        updated.setdefault('creation_parameters', {})['dynamic_auto_advance'] = False
        updated['codex_bridge'] = {'receipt': receipt, 'applied_revision': updated['revision'], 'backup_id': backup_id, 'at': time.time(),
                                  'evidence': {s.id: s.evidence for s in data.shots if s.evidence}}
        studio.discard_planning_resume(updated)
        updated.setdefault('logs', []).append('Codex 制作桥：分镜方案已校验导入，配音与字幕未修改，等待人工确认；未启动生成。')
        studio.save(path, updated)
        actual = read_record(path)
        if actual['shots'] != shots or actual['scenes'] != record['scenes'] or actual.get('audio') != record.get('audio'):
            studio.save(path, record)
            raise HTTPException(500, '写入回读不一致，已恢复原记录')
        return summary | {'revision': actual['revision'], 'written': True, 'backup_id': backup_id}


@router.post('/projects/{identity}/validate')
def validate(identity: str, data: Plan, request: Request):
    return review_plan(identity, data, request, False)


@router.post('/projects/{identity}/apply')
def apply(identity: str, data: Plan, request: Request):
    return review_plan(identity, data, request, True)


def compile_patches(record, path, data):
    """Validate only requested designs. Never renormalize the existing timeline."""
    original = {s['id']: s for s in record.get('shots', [])}
    ids = [s.id for s in data.shots]
    if len(original) != len(record.get('shots', [])) or len(ids) != len(set(ids)):
        raise ValueError('镜头 ID 重复，请核对资料包与返修请求')
    unknown = set(ids) - set(original)
    if unknown:
        raise ValueError('不存在的镜头 ID：' + ', '.join(sorted(unknown)))
    updated = copy.deepcopy(record)
    target = {s['id']: s for s in updated['shots']}
    evidence = updated.setdefault('codex_bridge', {}).setdefault('evidence', {})
    refs = {r['id']: r for r in record.get('references', [])}
    impacts = []
    for patch in data.shots:
        old, row = original[patch.id], target[patch.id]
        values = patch.model_dump(exclude_unset=True, exclude={'id'})
        changed = {k for k, v in values.items() if v != (evidence.get(patch.id, '') if k == 'evidence' else old.get(k))}
        if not changed:
            continue
        if 'reference_ids' in changed and 'image_prompt' not in values:
            raise ValueError(f'{patch.id} 更换参考图时须同时提交 image_prompt，按本次 reference_ids 顺序核对图号')
        if 'evidence' in values:
            evidence[patch.id] = values.pop('evidence')
        row.update(values)
        design_changed = bool(changed - {'evidence', 'intent'})
        image_changed = bool(changed & {'image_prompt', 'reference_ids'})
        video_changed = bool(changed & {'motion_plan', 'video_prompt'})
        if 'intent' in changed and not row['intent'].strip():
            raise ValueError(f'{patch.id} 表达目的不能为空')
        if design_changed:
            if not str(row.get('image_prompt') or '').strip():
                raise ValueError(f'{patch.id} 核心图提示词不能为空')
            selected = row.get('reference_ids') or []
            if len(selected) != len(set(selected)) or any(r not in refs for r in selected):
                raise ValueError(f'{patch.id} 参考图重复或不存在')
            for rid in selected:
                asset = (path / str(refs[rid].get('file') or '')).resolve()
                if path.resolve() not in asset.parents or not asset.is_file():
                    raise ValueError(f'{patch.id} 参考图文件不可用：{rid}')
            limit = 3 if studio._scene_asset(record, row) and studio.scene_references.enabled(record) else 4
            if len(selected) > limit:
                raise ValueError(f'{patch.id} 当前最多选用 {limit} 张参考图（含场景图时预留一位）')
            if row['kind'] == 'video':
                motion = normalize_motion_plan(row.get('motion_plan'))
                if not motion:
                    raise ValueError(f'{patch.id} 缺少动态阶段方案，请同时补充 motion_plan')
                if 'motion_plan' in changed:
                    row['motion_plan'] = motion
                    row['action'] = render_motion_action(motion)
                    row['visual_description'] = motion.get('reference_visual') or row['image_prompt']
                    before = old.get('motion_plan') or {}
                    image_changed |= any(before.get(k) != motion.get(k) for k in (
                        'scene_anchor', 'participants', 'reference_participants', 'reference_texts', 'reference_beat', 'reference_visual'))
                if ('motion_plan' in changed and 'video_prompt' not in values) or not str(row.get('video_prompt') or '').strip():
                    row['video_prompt'] = ('图1是单张复合内容参考，不是必须照搬的首帧。按阶段依次展开，始终输出普通全屏视频。\n'
                        + '本镜主体：' + '、'.join(motion['participants']) + '。\n' + render_motion_action(motion)
                        + '\n只允许阶段方案列出的文字；禁止添加字幕条、水印或将旁白转写到画面。')
                for medium, field in [('image', 'image_prompt'), ('video', 'video_prompt')]:
                    issues = prompt_plan_issues(row[field], motion, medium)
                    if issues:
                        raise ValueError(f'{patch.id} {field}：' + '；'.join(issues))
                video_changed |= row.get('video_prompt') != old.get('video_prompt')
            elif row.get('motion_plan') or str(row.get('video_prompt') or '').strip():
                raise ValueError(f'{patch.id} 静态镜头不应包含动态阶段/视频提示词')
            audit_storyboard([row], set(refs))
        if 'image_prompt' in values:
            # Repair uses the same local reference numbering as the native editor.
            row['image_material_numbers_bound'] = True
        if image_changed:
            row['image_prompt_out_of_sync'] = bool(row.get('image') or row.get('image_file') or row.get('image_status') == 'completed')
            row['image_prompt_warnings'] = []
        if video_changed:
            row['video_prompt_warnings'] = []
        impacts.append({'id': patch.id, 'fields': sorted(changed), 'image_needs_review': image_changed,
                        'video_needs_review': row['kind'] == 'video' and (image_changed or video_changed)})
    return updated, impacts


def review_patches(identity, data, request, commit):
    user = user_for(request)
    with studio.LOCK:
        path = studio.directory(user['id'], identity)
        record = read_record(path)
        uncertain = [s['id'] for s in record.get('shots', []) if s.get('video_status') != 'completed' and (
            s.get('video_status') in {'running', 'unknown'} or (not s.get('video_terminal') and
            (s.get('video_resume_available') or s.get('video_task_id') or
             (s.get('video_request') and s.get('video_execution_started') is not False))))]
        if uncertain:
            raise HTTPException(409, '仍有运行中或结果未确定的视频任务，请先在 OCV 查询或结束：' + '、'.join(uncertain))
        receipt = digest({'operation': 'shot_patch', 'payload': data.model_dump(exclude_unset=True)})
        previous = record.get('codex_bridge', {}).get('patch_receipt', {})
        if previous.get('receipt') == receipt and previous.get('revision') == record['revision']:
            # Still reject stale source audio, even when the last write was ours.
            retry = data.model_copy(update={'revision': record['revision']})
            check_edit_context(record, path, retry, user['id'])
            return {**previous['summary'], 'written': False, 'already_applied': True}
        try:
            check_edit_context(record, path, data, user['id'])
            updated, impacts = compile_patches(record, path, data)
        except (ValueError, KeyError, TypeError) as exc:
            return {'ok': False, 'errors': [str(exc)], 'written': False, 'generation_started': False}
        summary = {'ok': True, 'project_id': identity, 'revision': record['revision'], 'changed': [r['id'] for r in impacts],
                   'impacts': impacts, 'written': False, 'generation_started': False,
                   'preserved': ['audio', 'subtitles', 'timing', 'shot_order', 'shot_kind', 'generation_settings', 'asset_files']}
        if not commit or not impacts:
            return summary
        backup_id = f"patch-{record['revision']}-{uuid.uuid4().hex}"
        studio.save(path / 'codex_bridge_backups' / backup_id, copy.deepcopy(record))
        by_id = {s['id']: s for s in updated['shots']}
        visual_change = any(i['image_needs_review'] or i['video_needs_review'] for i in impacts)
        if visual_change and updated.get('export'):
            updated.setdefault('codex_bridge', {}).setdefault('export_history', []).append(
                {'export': copy.deepcopy(updated['export']), 'backup_id': backup_id, 'at': time.time()})
            updated.pop('export', None)
        for impact in impacts:
            row = by_id[impact['id']]
            if impact['video_needs_review']:
                studio._invalidate_shot_video(updated, row, 'Codex 局部返修已更新画面方案')
            row['prompt_refresh_note'] = ('Codex 局部返修已保存。' +
                ('旧图保留，请检查并按新提示词重绘。' if impact['image_needs_review'] else '') +
                ('旧视频保留在历史记录，请核查后重新生成或手动选用历史版本。' if impact['video_needs_review'] else '') +
                '配音、字幕、时长与其他镜头未变，未启动生成。')
        if any(i['image_needs_review'] for i in impacts):
            updated['status'] = 'image_review'
        elif any(i['video_needs_review'] for i in impacts):
            updated['status'] = 'video_review'
        if visual_change:
            updated['error'] = ''
            for key in ('reedit_shot_id', 'reedit_return_status'):
                updated.pop(key, None)
        updated.setdefault('creation_parameters', {})['dynamic_auto_advance'] = False
        updated['revision'] += 1
        summary.update(revision=updated['revision'], written=True, backup_id=backup_id)
        updated['codex_bridge']['patch_receipt'] = {'receipt': receipt, 'revision': updated['revision'], 'summary': summary}
        studio.discard_planning_resume(updated)
        updated.setdefault('logs', []).append('Codex 制作桥：局部返修 ' + '、'.join(summary['changed']) + '；素材已保留，未启动生成。')
        studio.save(path, updated)
        actual = read_record(path)
        if any(actual.get(k) != updated.get(k) for k in ('shots', 'scenes', 'audio', 'revision', 'codex_bridge')):
            studio.save(path, record)
            raise HTTPException(500, '局部返修回读不一致，已恢复原记录')
        return summary


@router.post('/projects/{identity}/patch-validate')
def patch_validate(identity: str, data: ShotPatches, request: Request):
    return review_patches(identity, data, request, False)


@router.post('/projects/{identity}/patch-apply')
def patch_apply(identity: str, data: ShotPatches, request: Request):
    return review_patches(identity, data, request, True)
