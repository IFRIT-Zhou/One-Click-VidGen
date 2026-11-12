"""Bounded dedicated-model -> pinned recipes -> trusted Remotion -> review.

No GPT service, source code execution, TTS, automatic adoption or
project-record writes. Every paid call and retained attempt is auditable.
An explicitly authorized image request extracts the decorative background.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
import uuid

from fastapi import HTTPException
from pydantic import ValidationError

from .vibe_shotcraft_library import ShotcraftLibrary
from .vibe_shotcraft_plan import (RECIPES, RENDERER_VERSION, VERSION, Selection, ShotcraftProposal,
                                 bind_style, validate_proposal, crop_specs, RECIPE_RULES, minimum_frames)
from .vibe_shotcraft_guidance import (SELECTION, DIRECTOR, REVIEW, GUIDANCE_VERSION, ADAPTER_LIMITS)
from .vibe_shotcraft_capabilities import (VERSION as CAPABILITY_VERSION, capabilities_for,
                                        selection_taxonomy, motion_families, validate_capability_catalog)
from .vibe_cloud_client import CloudCallError
from .vibe_materials import MaterialError, crop_asset, generate_asset

BACKGROUND_PROMPT = '''编辑输入分镜海报，提取可供重新排版的干净背景，不重新设计海报。
只保留原图背景的色调、渐变、纹理、光影、氛围和背景装饰元素（如边缘光带、粒子、抽象纹样）。
完全移除所有文字、数字、标题、标签、文本框、卡片、表格、图表、数据条、坐标轴、指示线和标注箭头。
完全移除前景人物、器官、医疗器械、输液袋、产品、照片、期刊及其他复杂主体，不要将这些主体留在背景中。
移除区域按周围原始背景自然补全，消除残字、轮廓、矩形色块、接缝和重影。
不要新增主体、文字、水印或新装饰。保留原图画幅与背景装饰位置，交付一张完整干净底图。'''


def prepare_original_crops(proposal, assets, output_dir):
    """Keep every attempt isolated; copy original pixels, never alter inputs."""
    specs = crop_specs(proposal, assets)
    prepared = dict(assets)
    for index, (identity, spec) in enumerate(specs.items()):
        # IDs may contain ':' and Windows reserved names; never use model IDs
        # as filesystem names. The data binding retains the original identity.
        prepared[identity] = crop_asset(assets, spec['source'], spec['box'], output_dir, f'crop_{index:02d}')
    return prepared


def preflight_library(context):
    w, h = context['canvas']
    if w/h < 1.6 or not 2.5 <= context['duration'] <= 60:
        raise ValueError('首批云端模板支持横屏、2.5至60秒单镜；当前镜头不适配。未调用云端，可使用本地模式或其他视频渠道')
    try:
        validate_capability_catalog()
        library = ShotcraftLibrary()
        catalog = library.compact_catalog(ready_only=True, max_chars=100000)
        if not catalog.get('complete'):
            raise ValueError('模板目录被截断，不能在目录不完整时选择模板')
        available = {row['id'] for row in catalog['items']}
        if not set(RECIPES).issubset(available):
            raise ValueError('Shotcraft模板源文件不完整')
        # Validate cards/sources before billing, not just catalog file sizes.
        for identity in RECIPES:
            if not library.read_recipe(identity, max_chars=100000)['complete']:
                raise ValueError('模板卡片过长，不能截断后交给模型')
            if not library.read_sources(identity, max_chars_per_file=100000, include_dependencies=True)['complete']:
                raise ValueError('模板实现过长，不能截断后交给模型')
        return library
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError('Shotcraft 本地模板库未就绪；请先运行 tools/sync_video_shotcraft.py 安装/校验模板库。本次未调用模型。'+str(exc)[:300]) from None


def recipe_context(library, selected):
    # Full source truth including camera/easing helpers; shared files sent once.
    seen = set()
    result = []
    total = 0
    for identity in selected:
        card = library.read_recipe(identity, max_chars=100000)
        sources = library.read_sources(identity, max_chars_per_file=100000, include_dependencies=True)
        if not card['complete'] or not sources['complete']:
            raise ValueError('模板资料不能在截断状态下交给模型')
        files, reused = [], []
        total += len(card['text'])
        for source in sources['files']:
            if source['path'] in seen:
                reused.append(source['path'])
            else:
                seen.add(source['path'])
                files.append(source)
                total += len(source['text'])
        capabilities = capabilities_for(identity)
        total += len(json.dumps(capabilities, ensure_ascii=False))
        result.append({'id': identity, 'card': card, 'implementations': files,
                       'capabilities': capabilities,
                       'shared_sources_already_included': reused})
    if total > 250000:
        raise ValueError('选中模板资料超过本次上下文预算，不能截断模板；请减少模板数量')
    return result


def eligible_catalog(library, duration):
    catalog = library.compact_catalog(ready_only=True, max_chars=100000)
    if not catalog.get('complete'):
        raise ValueError('模板目录被截断，不能将缺失模板误报为完整候选目录')
    items = [{**row, 'adapter_limits': RECIPE_RULES[row['id']],
              'capabilities': capabilities_for(row['id'], duration)} for row in catalog['items']
             if row['id'] in RECIPES and duration >= minimum_frames(row['id'], RECIPE_RULES[row['id']]['min_items'])/30]
    return {**catalog, 'capability_version': CAPABILITY_VERSION,
            'items': items, 'total_styles': len(items), 'returned_styles': len(items),
            'omitted_styles': 0, 'complete': True}


def validate_requested_templates(library, duration, requested):
    """Reject impossible explicit choices before billing, without guessing assets.

    Choices are a set of intended stages, not a sequence and not permission to
    substitute another recipe. Soft evidence/content labels never reject here.
    """
    if not requested:
        return
    if len(requested) > 3 or len(set(requested)) != len(requested) or any(identity not in RECIPES for identity in requested):
        raise ValueError('请选择 1 至 3 个不同的已支持模板；本次未调用模型')
    catalog = eligible_catalog(library, duration)
    eligible = {row['id'] for row in catalog['items']}
    missing = [identity for identity in requested if identity not in eligible]
    if missing:
        names = '、'.join(RECIPE_RULES[identity].get('label', identity) for identity in missing)
        raise ValueError(f'所选模板未就绪或当前镜头时长不足：{names}。请更换模板或选择自动；本次未调用模型')
    minimum = sum(minimum_frames(identity, RECIPE_RULES[identity]['min_items']) / 30 for identity in requested)
    if duration + 1e-6 < minimum:
        raise ValueError(f'所选模板至少需要合计 {minimum:g} 秒，当前镜头仅 {duration:.2f} 秒；请减少选择，不会缩短阅读时间。本次未调用模型')
    if requested == ['outro-group-photo-launch'] and duration > 6.001:
        raise ValueError('聚合收束模板最多适合 6 秒尾段，不能单独覆盖当前整镜；请搭配另一种模板或选择自动。本次未调用模型')


def neighbor_templates(api, project, sid):
    """Advisory only; never part of this shot's asset/timeline validity stamp.

    Read only completed adjacent previews. Bad/missing historical pointers must
    not block production, and a later neighbor render must not stale this shot.
    """
    result = []
    try:
        record = api.previews._read_json(project/'record.json', 32*1024*1024)
        shots = record.get('shots', [])
        index = next(i for i, shot in enumerate(shots) if shot.get('id') == sid)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration, HTTPException):
        return result
    for i in (index-1, index+1):
        if not 0 <= i < len(shots):
            continue
        identity = shots[i].get('id', '') if isinstance(shots[i], dict) else ''
        if not identity:
            continue
        try:
            pointer = project/'assets'/'vibe_cloud'/('latest-'+api.previews._hash(identity)[:32]+'.json')
            pointer = api.previews._safe(project, pointer, file=True)
            job = api._read_job(project, api.previews._read_json(pointer, 4096).get('task_id', ''))
            if job.get('status') != 'completed' or job.get('shot_id') != identity:
                continue
            recipes = [r for r in job.get('template_ids', []) if r in RECIPES]
            if recipes:
                result.append({'shot_id': identity, 'position': 'previous' if i < index else 'next',
                               'recipes': recipes, 'source': 'completed_preview'})
        except (OSError, ValueError, KeyError, TypeError, AttributeError, HTTPException):
            continue
    return result


def run_worker(project, sid, job_id, config, data, event):
    # Imported lazily to keep the legacy route and its public API stable.
    from . import vibe_cloud as api
    path = api._job(project, job_id)
    folder = path.parent
    started = time.monotonic()

    def cancelled():
        return event.is_set()

    def check():
        if cancelled():
            raise InterruptedError('已停止模板动效；未采用到正式镜头，已提交请求可能仍计费')
        if time.monotonic()-started > 900:
            raise CloudCallError('模板动效达到15分钟上限；试做结果保留，未采用')

    def call(system, payload, images, **kwargs):
        check()
        count = len(api._read_job(project, job_id)['usage']['model_calls'])
        if count >= 8:
            raise CloudCallError('达到8次模型调用上限，未继续计费；试做结果保留')
        return api._call(path, config, system, payload, images, cancelled,
                         timeout=min(180, max(1, 900-(time.monotonic()-started))), **kwargs)

    try:
        check()
        snapshot, shot, context, source_assets = api._context(project, sid)
        stamp, im_hash, a_hash, audio, start, end, cues = snapshot
        cloud_stamp = api._cloud_stamp(snapshot, shot, context, source_assets)
        if cloud_stamp != api._read_job(project, job_id)['cloud_input_stamp']:
            raise CloudCallError('启动前本镜输入已变，请核对后重试')
        library = preflight_library(context)
        requested_templates = list(data.template_ids)
        validate_requested_templates(library, end-start, requested_templates)
        api.preflight_remotion(cancelled=cancelled, required_renderer=RENDERER_VERSION,
                              required_plan_version=VERSION)
        assets = {}
        resources = folder/'materials'
        resources.mkdir()
        for index, (identity, source) in enumerate(source_assets.items()):
            target = resources/f'source_{index:02d}{source.suffix}'
            shutil.copyfile(source, target)
            assets[identity] = target
        digest, seek = api.previews._audio(audio, start, end, folder/'narration.wav')
        if digest != a_hash:
            raise CloudCallError('配音已改变，请按最新时间轴重试')
        if data.max_image_requests:
            check()
            api._update(path, stage='background', message='按本项目图像渠道提取干净背景：保留背景装饰，移除文字、文本框及复杂主体')
            def submitted(identity):
                usage = api._read_job(project, job_id)['usage']
                usage.update(image_requests=1, image_task_ids=[identity])
                api._update(path, usage=usage)
            background = generate_asset(config['layered_owner_id'], BACKGROUND_PROMPT,
                [assets['core']], resources, 'template_background', cancelled,
                background_canvas=context['canvas'], on_submitted=submitted,
                image_config=config['layered_image_config'], image_route=config['layered_image_route'])
            assets['template_background'] = background['path']
            context['template_background'] = {'asset_id':'template_background',
                'usage':'已由图像模型移除文字和复杂主体的固定全画布背景。所有模板自动使用，不得裁成卡片；参考此底图安排可读文字。'}
        neighbors = neighbor_templates(api, project, sid)
        for neighbor in neighbors:
            neighbor['motion_families'] = motion_families(neighbor['recipes'])
        context.update(instruction=data.instruction, pace=data.pace,
                       template_taxonomy=selection_taxonomy(), neighbor_templates=neighbors,
                       template_selection={'mode': 'manual' if requested_templates else 'auto',
                           'requested_template_ids': requested_templates,
                           'must_use_all': bool(requested_templates),
                           'allow_other_templates': not bool(requested_templates), 'order': 'director'},
                       original_asset_ids=[k for k in assets if k != 'template_background'], display_asset_ids=[k for k in assets if k not in ('core','template_background')],
                       material_capabilities={'original_pixel_crop': True, 'max_crops': 4,
                                              'matting': False, 'image_generation': False})
        api._write(folder/'input_context.json', context)
        api._update(path, status='running', stage='select', input_stamp=stamp,
                    template_mode='shotcraft', renderer=RENDERER_VERSION, planner='ds_api',
                    guidance_version=GUIDANCE_VERSION, capability_version=CAPABILITY_VERSION,
                    message='专用模型正在按内容、素材与时长标签选择模板，并提取原片风格')
        selection = None
        selection_feedback = ''
        catalog = eligible_catalog(library, end-start)
        if requested_templates:
            catalog['items'] = [item for item in catalog['items'] if item['id'] in requested_templates]
            catalog.update(total_styles=len(catalog['items']), returned_styles=len(catalog['items']), omitted_styles=0)
        eligible = {item['id'] for item in catalog['items']}
        for attempt in range(2):
            raw = call(SELECTION, {**context, 'catalog': catalog,
                       'schema': Selection.model_json_schema(), 'feedback': selection_feedback}, assets, max_tokens=2500)
            api._write(folder/f'selection_{attempt}.json', raw)
            try:
                selection = Selection.model_validate(raw)
                if not set(selection.recipes).issubset(eligible):
                    selection = None
                    raise ValueError('选中了当前时长无法容纳的模板；请只从当前catalog选择')
                if requested_templates and set(selection.recipes) != set(requested_templates):
                    selection = None
                    raise ValueError('必须采用用户选中的全部模板，不能省略或换用其他模板；顺序由导演决定')
                break
            except (ValidationError, ValueError) as exc:
                selection_feedback = str(exc)[:2000]
        if selection is None:
            raise CloudCallError('模板/色板选择未通过校验：'+selection_feedback)
        check()
        cards = recipe_context(library, selection.recipes)
        frozen_style = selection.source_style
        api._write(folder/'selected_recipes.json', cards)
        api._update(path, template_ids=selection.recipes, requested_template_ids=requested_templates,
                    selection_reason=selection.reason,
                    source_style=frozen_style.model_dump(mode='json'),
                    source_commit=library.manifest['upstream']['commit'])
        previous = None
        feedback = ''
        for attempt in range(3):
            check()
            api._update(path, stage='design', attempt=attempt,
                        message='【初次编排】专用模型正在编排模板与配音节奏' if attempt == 0 else f'【第 {attempt} 轮修正（最多 2 轮）】专用模型正在按核查结果修正模板方案')
            raw = call(DIRECTOR, {**context, 'schema': ShotcraftProposal.model_json_schema(),
                       'selected_recipes': cards, 'adapter_limits': ADAPTER_LIMITS,
                       'runtime_lessons_version': GUIDANCE_VERSION,
                       'frozen_source_style': frozen_style.model_dump(mode='json'),
                       'previous_proposal': previous, 'feedback': feedback}, assets, max_tokens=16000, thinking=True)
            previous = raw
            api._write(folder/f'proposal_{attempt}.json', raw)
            check()
            try:
                proposal = ShotcraftProposal.model_validate(bind_style(raw, frozen_style))
                if proposal.questions:
                    api._update(path, status='failed', stage='needs_input', message='需要补充：'+'；'.join(proposal.questions))
                    return
                requested = {stage.recipe for stage in proposal.scene.stages} if proposal.scene else set()
                if requested_templates and requested != set(requested_templates):
                    raise ValueError('方案必须使用用户选择的每一种模板，且不得新增未选择模板；请按 template_selection 修正，不得自动替换用户选型')
                extra = requested-set(selection.recipes)
                if extra and extra.issubset(eligible):
                    # A director may discover a better known recipe after reading
                    # the detailed cards. Supply its real source, then ask it to
                    # revise; never render an unseen or arbitrary template.
                    selection.recipes.extend(sorted(extra))
                    cards = recipe_context(library, selection.recipes)
                    api._write(folder/'selected_recipes.json', cards)
                    api._update(path, template_ids=selection.recipes)
                    raise ValueError('设计需要新增已验证模板 '+', '.join(sorted(extra))+'；完整说明与实现已补入selected_recipes，请阅读后重新提交。该步仍计入两次修正额度')
                compiled = validate_proposal(proposal, canvas=context['canvas'], duration=end-start,
                                            cues=cues, assets=assets, selected=selection.recipes)
                render_assets = prepare_original_crops(proposal, assets, folder/f'crops_{attempt}')
                api._write(folder/f'crops_{attempt}.json', crop_specs(proposal, assets))
                api._update(path, stage='render', message='正在本机用真实Shotcraft适配模板渲染，并核查文字与素材布局')
                out = folder/f'render_{attempt}'
                out.mkdir()
                while not api._render_lock.acquire(timeout=.25):
                    check()
                try:
                    check()
                    metrics = api.render_scene(plan=proposal.scene, assets=render_assets, cues=cues,
                        audio=folder/'narration.wav', start=seek, duration=end-start,
                        output_dir=out, ffmpeg=api.ffmpeg_binary(), cancelled=cancelled,
                        timeout=min(240, max(.1, 900-(time.monotonic()-started))))
                finally:
                    api._render_lock.release()
            except (ValidationError, ValueError, KeyError, MaterialError) as exc:
                feedback = '本地校验失败：'+str(exc)[:3200]
                api._write(folder/f'feedback_{attempt}.json', {'stage': 'validation', 'message': feedback})
                if attempt == 2:
                    raise CloudCallError(feedback+'；已达到两次修正上限，未采用')
                continue
            check()
            api._update(path, stage='review', message='专用模型正在核对分阶段截图、原素材与最终字幕')
            images = dict(render_assets)
            images['render_proof'] = out/'proof.jpg'
            for label in ('early', 'middle', 'end'):
                images['render_'+label] = out/f'detail_{label}.png'
            for row in [*metrics.get('stage_frames', []), *metrics.get('item_frames', [])]:
                name = row.get('file', '')
                if len(images) >= 32:
                    raise ValueError('审查素材超过32张；未静默省略关键帧')
                if not name or Path(name).name != name or not name.endswith('.png') or not (out/name).is_file():
                    raise ValueError('模板核查帧缺失或路径无效，未跳过审查：'+str(name)[:100])
                images['render_'+Path(name).stem] = out/name
            review_raw = call(REVIEW, {'subtitles': context['subtitles'], 'plan': compiled,
                'template_capabilities': {s.recipe: capabilities_for(s.recipe) for s in proposal.scene.stages},
                'visual_basis': proposal.visual_basis.model_dump(mode='json'),
                'original_crops': crop_specs(proposal, assets),
                'source_notes': proposal.source_notes, 'frozen_source_style': frozen_style.model_dump(mode='json'),
                'stage_windows': [{'id': s['id'], 'start': s['start'], 'end': s['end']} for s in compiled['stages']],
                'metrics': metrics, 'schema': api.Review.model_json_schema()}, images, max_tokens=2500)
            api._write(folder/f'review_{attempt}.json', review_raw)
            try:
                review = api.Review.model_validate(review_raw)
            except ValidationError:
                review = api.Review(passed=False, issues=['视觉检查返回格式无效'])
            if not review.passed or review.issues:
                feedback = '视觉检查失败：'+json.dumps(review.issues, ensure_ascii=False)[:3000]
                api._write(folder/f'feedback_{attempt}.json', {'stage': 'review', 'message': feedback})
                if attempt == 2:
                    raise CloudCallError(feedback+'；已达到两次修正上限，未采用')
                continue
            check()
            if cloud_stamp != api._cloud_stamp(*api._context(project, sid)):
                raise CloudCallError('本镜画面/最终配音/字幕/参考素材已变；未发布过期预览')
            identity = uuid.uuid4().hex
            base = f'/api/video-studio/{project.name}/vibe-previews/{identity}/'
            used = list(dict.fromkeys(s.recipe for s in proposal.scene.stages))
            receipt = {'identity': project.name, 'shot_id': sid, 'preview_id': identity,
                'source_sha256': im_hash, 'input_stamp': stamp, 'cloud_input_stamp': cloud_stamp,
                'engine': 'cloud', 'template_mode': 'shotcraft', 'template_ids': used, 'planner': 'ds_api',
                'requested_template_ids': requested_templates,
                'renderer': RENDERER_VERSION, 'guidance_version': GUIDANCE_VERSION,
                'capability_version': CAPABILITY_VERSION, 'selection_reason': selection.reason,
                'motion_families': motion_families(used),
                'source_commit': library.manifest['upstream']['commit'],
                'plan': proposal.scene.model_dump(mode='json'), 'metrics': metrics,
                'preview_url': base+'preview', 'silent_url': base+'silent', 'proof_url': base+'proof',
                'cached': False, 'may_charge': True, 'summary': proposal.summary, 'source_notes': proposal.source_notes,
                'visual_basis': proposal.visual_basis.model_dump(mode='json'),
                'source_style': frozen_style.model_dump(mode='json'),
                'material_policy': 'original_subjects_with_generated_background' if 'template_background' in assets else ('original_pixels_with_crops' if proposal.crops else 'original_inputs_only'),
                'original_crops': crop_specs(proposal, assets),
                'max_image_requests': data.max_image_requests or 0, 'cloud_task_id': job_id,
                'usage': api._read_job(project, job_id)['usage']}
            api._write(out/'receipt.json', receipt)
            store = api.previews._store(project)
            store.mkdir(parents=True, exist_ok=True)
            with api._lock:
                check()
                api.publish(out, store/identity)
                api.previews._publish_pointer(project, sid, identity)
                api._update(path, status='completed', stage='completed', template_ids=used, preview=receipt,
                    message='DS模板动效预览已就绪；未采用到正式镜头', seconds=round(time.monotonic()-started, 2))
            return
    except InterruptedError as exc:
        api._update(path, status='stopped', message=str(exc))
    except HTTPException as exc:
        api._update(path, status='stopped' if cancelled() else 'failed', message=str(exc.detail)[:1600])
    except (CloudCallError, MaterialError, ValueError, RuntimeError, TimeoutError, OSError, KeyError) as exc:
        api._update(path, status='stopped' if cancelled() else 'failed', message=str(exc)[:1600])
    except Exception:
        api._update(path, status='stopped' if cancelled() else 'failed', message='模板动效执行失败；试做结果已保留，正式镜头未修改')
    finally:
        with api._lock:
            api._active.pop((str(project), sid), None)
