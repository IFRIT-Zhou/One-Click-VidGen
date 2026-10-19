"""Durable cluster H3 clip client; no direct GPU or ComfyUI access."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import threading
import time
import uuid
from decimal import Decimal
from fastapi import HTTPException
from .cloud_client import cloud_client_for, CloudApiError
from . import video_studio as studio
from .h3_limits import fits_dimensions, validate_dimensions


def accept_quote(saved, quote, spec):
    if Decimal(str(quote.get('credits_total', quote.get('credits', '-1')))) != 0 or quote.get('test_mode') is not True:
        raise ValueError('集群 H3 已不再免费测试，请确认新价格后再接入；本次未提交')
    normalized=quote.get('normalized_spec',quote.get('spec',spec))
    if any(normalized.get(key)!=spec[key] for key in ('prompt','reference_image_asset_id','width','height','duration_seconds','seed')):
        raise ValueError('云端报价更改了生成参数，本次未提交，请刷新配置')
    saved.update(spec=spec,quote_id=quote['quote_id'],normalized_spec=normalized)


def create_or_renew(client, saved, persist):
    """Renew only a definitively expired quote after an authoritative identity miss."""
    try:
        return client.create_video_job(saved['quote_id'],saved['client_job_id'])
    except CloudApiError as exc:
        expired=exc.status_code in {409,410} and (exc.code=='QUOTE_EXPIRED' or str(exc).strip()=='Quote expired; request a new quote')
        if not expired:
            raise
        try:
            return client.video_job(saved['client_job_id'],by_client=True)
        except CloudApiError as lookup:
            if lookup.status_code!=404:
                raise
        # Same assets, seed, dimensions and client identity; a fresh quote never
        # authorizes a second job because the server also locks that identity.
        spec=saved['spec']
        quote=client.video_quote(spec)
        accept_quote(saved,quote,spec)
        persist(saved)
        return client.create_video_job(saved['quote_id'],saved['client_job_id'])


def workflow(capabilities):
    candidates = capabilities.get('workflows') or [{**capabilities, 'workflow_id': 'h3-fast', 'available': capabilities.get('enabled', False)}]
    result = next((row for row in candidates if (row.get('workflow_id') or row.get('id')) == 'h3-fast'), None)
    if not result or result.get('available', capabilities.get('available', False)) is not True:
        raise ValueError('集群 H3 暂不可用，请稍后刷新')
    return result


def dimensions_for(capabilities, options, ratio, seconds=14):
    selected = workflow(capabilities)
    raw = selected.get('dimensions') or selected.get('supported_dimensions') or selected.get('resolutions') or []
    allowed = [(int(row['width']), int(row['height'])) if isinstance(row, dict) else tuple(map(int, row)) for row in raw]
    advertised = list(allowed)
    allowed = [pair for pair in allowed if fits_dimensions(*pair, seconds)]
    portrait = ratio == '9:16'
    if options.get('resolution') == 'custom':
        dimensions = (options.get('width'), options.get('height'))
        validate_dimensions(*dimensions, seconds)
    elif options.get('resolution'):
        candidates = [pair for pair in allowed if (pair[1] > pair[0]) == portrait and min(pair) == {'480p':480,'720p':720,'1080p':1080}.get(options['resolution'])]
        if not candidates:
            raise ValueError('集群当前不支持所选分辨率，请选择能力列表中的尺寸')
        dimensions = candidates[0]
    else:
        candidates = [pair for pair in allowed if (pair[1] > pair[0]) == portrait and pair[0] != pair[1]]
        if not candidates:
            raise ValueError('集群没有可用的对应画幅尺寸')
        defaults = selected.get('default_resolutions')
        if defaults is not None:
            default = defaults.get('portrait' if portrait else 'landscape', {})
            dimensions = (default.get('width'), default.get('height'))
            if dimensions in advertised and not fits_dimensions(*dimensions, seconds):
                # No saved selection: choose a supported default within this clip's limit.
                dimensions = max(candidates, key=lambda pair: pair[0]*pair[1])
            if dimensions not in candidates:
                raise ValueError('集群默认尺寸与能力列表不一致，请刷新配置；不会自动降低分辨率')
        else:
            # Older servers did not name a default. Preserve their supported
            # sizes, but do not silently choose the lowest-quality thumbnail.
            dimensions = max(candidates, key=lambda pair: pair[0]*pair[1])
    if dimensions not in allowed:
        raise ValueError('集群当前不支持所选尺寸，不会自动降低分辨率')
    return selected, dimensions


def validate_duration(shot, selected):
    """A new clip must cover its timeline and fit the H3 request limits."""
    if type(shot.get('generation_duration')) is not int:
        raise ValueError('视频请求时长必须是整数秒，请重新规划分镜')
    try:
        seconds = float(shot.get('generation_duration') or 0)
        timeline = float(shot.get('duration') or 0)
        limits = selected.get('duration_seconds') or selected.get('duration', {})
        minimum = max(2, float(selected.get('min_duration_seconds', limits.get('min', 2))))
        maximum = min(15, float(selected.get('max_duration_seconds', limits.get('max', 15))))
    except (TypeError, ValueError, OverflowError):
        raise ValueError('镜头时长无效，请重新规划分镜') from None
    if not all(math.isfinite(value) for value in (seconds, timeline, minimum, maximum)):
        raise ValueError('镜头时长无效，请重新规划分镜')
    if not 0 < timeline <= seconds or not minimum <= seconds <= maximum:
        raise ValueError('镜头时长超出集群允许范围或请求时长不足以覆盖分镜，请拆分分镜；不会自动裁短')


def handles(identity, data, user_id):
    with studio.LOCK:
        record = studio.read(studio.directory(user_id, identity))
        chosen = [s for s in record['shots'] if s['kind'] == 'video' and (not data.shot_ids or s['id'] in data.shot_ids)]
        return any(((data.options or data.shot_options.get(s['id'])).backend if (data.options or data.shot_options.get(s['id'])) else
                   (s.get('video_generation_options') or {}).get('backend') or (s.get('video_request') or {}).get('backend') or
                   record.get('creation_parameters', {}).get('video_generation_backend')) == 'cloud_h3' for s in chosen)


def generate_cloud(identity, data, user_id):
    from . import video_generation as vg
    client = cloud_client_for(user_id)
    try:
        capabilities = client.video_capabilities()
    except CloudApiError as exc:
        raise HTTPException(exc.status_code or 503,str(exc)) from exc
    with studio.LOCK:
        path = studio.directory(user_id, identity)
        record = studio.read(path)
        studio.editable(record, data.revision, allow_image_edits=True)
        if record['status'] not in vg.VIDEO_GENERATION_ENTRY_STAGES or str(path) in studio.ACTIVE:
            raise HTTPException(409, '请先完成当前阶段并确认核心分镜图')
        catalog = {s['id']: s for s in record['shots']}
        if len(set(data.shot_ids)) != len(data.shot_ids) or any(k not in catalog or catalog[k]['kind'] != 'video' for k in data.shot_ids):
            raise HTTPException(400, '选择的动态镜头无效')
        if (data.options and (len(data.shot_ids) != 1 or data.shot_options)) or set(data.shot_options)-set(data.shot_ids):
            raise HTTPException(400, '镜头配置与本次选择不一致')
        if data.regenerate_completed and not data.shot_ids:
            raise HTTPException(400, '重新生成必须明确选择镜头')
        shots = [s for s in record['shots'] if s['kind']=='video' and (not data.shot_ids or s['id'] in data.shot_ids) and (data.regenerate_completed or s.get('video_status')!='completed')]
        if not shots:
            raise HTTPException(409, '没有需要生成的镜头')
        resolved = {}
        try:
            for shot in shots:
                options = vg._resolve_generation_options(record, shot, user_id, data.options or data.shot_options.get(shot['id']))
                if options['backend'] != 'cloud_h3':
                    raise ValueError('本次请单独选择集群 H3 镜头，或将全部动态镜头统一设置为集群 H3')
                if shot.get('video_request') and not shot.get('video_terminal') and shot.get('video_status') != 'completed':
                    if shot['video_request'].get('backend') != 'cloud_h3' or data.regenerate_completed:
                        raise ValueError('原任务尚未确认结束，请先继续查询，不能重复生成')
                vg._inputs(path, record, shot)
                if not str(shot.get('video_prompt') or '').strip():
                    raise ValueError('视频提示词不能为空')
                if studio.image_edit_key(record['id'], shot['id']) in studio.IMAGE_EDITS or (shot.get('image_task') or {}).get('status')=='running':
                    raise ValueError('请等待图片编辑完成')
                previous=shot.get('video_request') or {}
                if previous and not shot.get('video_terminal') and shot.get('video_status')!='completed':
                    frozen_dimensions=(previous.get('width'),previous.get('height'))
                    if options.get('resolution')=='custom' and (options.get('width'),options.get('height'))!=frozen_dimensions:
                        raise ValueError('原任务尚未结束，不能更换尺寸，请先查询原任务')
                    resolved[shot['id']] = (options, {'workflow_revision':previous.get('workflow_revision'),'model':previous.get('model')}, frozen_dimensions)
                    continue
                validate_duration(shot, workflow(capabilities))
                selected, dimensions = dimensions_for(capabilities, options, record['settings'].get('ratio','16:9'), shot['generation_duration'])
                from .presenter_mode import audio_policy
                if audio_policy(record.get('creation_parameters',{}), shot, options['h3_prompt_agent'], record.get('references',[]))[0]:
                    raise ValueError('集群 H3 测试阶段暂不支持参考音频或对口型，请关闭此功能')
                resolved[shot['id']] = (options, selected, dimensions)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
        for shot in shots:
            if data.regenerate_completed or (shot.get('video_terminal') and (shot.get('video_request') or {}).get('backend') != 'cloud_h3'):
                studio._invalidate_shot_video(record,shot,'用户明确重新生成集群 H3 片段')
            shot['video_generation_options']=resolved[shot['id']][0]
            shot['video_queued']=True
            shot.pop('video_queue_cancelled',None)
        record.pop('cloud_login_required',None)
        record.update(status='video_generating', active_video_backend='cloud_h3', error='', revision=record['revision']+1)
        studio.save(path,record)
        event=threading.Event()
        studio.ACTIVE.add(str(path));studio.CANCEL_EVENTS[str(path)]=event
        threading.Thread(target=_worker,args=(path,user_id,[s['id'] for s in shots],resolved,event),daemon=True,name='ocv-cloud-h3').start()
        return record


def _worker(path,user_id,identities,resolved,event):
    from . import video_generation as vg
    client=cloud_client_for(user_id)
    current_id=None
    def update(identity, **values):
        with studio.LOCK:
            record=studio.read(path);shot=studio._find_shot(record,identity)
            shot.update(values);record['revision']+=1;studio.save(path,record)
    try:
        for identity in identities:
            if event.is_set(): break
            current_id=identity
            options,selected,dimensions=resolved[identity]
            with studio.LOCK:
                record=studio.read(path);live=studio._find_shot(record,identity)
                if live.get('video_queue_cancelled'):continue
                update(identity,video_queued=False,video_status='running',video_started_at=time.time(),video_error='',video_progress_message='正在准备集群 H3 请求')
                record=studio.read(path);shot=copy.deepcopy(studio._find_shot(record,identity))
            saved=shot.get('video_request') or {}
            if not saved or shot.get('video_terminal'):
                prompt=shot['video_prompt']
                if options['h3_prompt_agent']:
                    with studio.project_language_scope(user_id,studio.planning_parameters(record)):
                        prompt,_=vg.convert_for_h3(shot,int(shot['generation_duration']),image_path=studio._storyboard_image_path(path,shot))
                if event.is_set(): break
                with studio.LOCK:
                    record=studio.read(path);live=studio._find_shot(record,identity)
                    vg._freeze_local_request(path,record,live,'h3-fast',prompt=prompt,prompt_format='h3_ref2va' if options['h3_prompt_agent'] else 'generic',dimensions=dimensions,resolution=options['resolution'])
                    saved=live['video_request'];saved.update(backend='cloud_h3',seed=int(saved['seed']) % (2**32),client_job_id='ocv-h3-'+uuid.uuid4().hex,workflow_revision=selected['workflow_revision'],model=selected.get('model','minimax-h3'))
                    live.update(video_backend='cloud_h3',video_terminal=False,video_resume_available=True,video_execution_started=False,video_status='running')
                    request_file=vg._asset(path,live['video']).parent/'request.json'
                    request_file.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
                    studio.save(path,record);shot=copy.deepcopy(live)
            client_id=saved['client_job_id']
            remote=None
            try:
                remote=client.video_job(shot['video_task_id']) if shot.get('video_task_id') else client.video_job(client_id,by_client=True)
            except CloudApiError as exc:
                if exc.status_code!=404: raise
                if shot.get('video_task_id'): raise ValueError('已保存的云端任务不存在，请联系管理员核实；不会重新提交') from exc
            if remote is None:
                if event.is_set(): break
                # Recovery can query old tasks; an absent task must obey today's limits
                # before any new upload, quote or creation, even with a saved quote ID.
                try:
                    validate_dimensions(saved.get('width'), saved.get('height'), saved.get('duration'))
                except ValueError as exc:
                    update(identity, video_status='failed', video_terminal=True, video_resume_available=False,
                           video_error=str(exc), video_progress_message='原任务不存在，保存的参数超出当前限制，请修改后重新生成')
                    continue
                if not saved.get('quote_id'):
                    asset=client.video_asset(vg._asset(path,saved['reference_files'][0]))
                    spec=dict(prompt=saved['prompt'],reference_image_asset_id=asset['asset_id'],width=saved['width'],height=saved['height'],duration_seconds=saved['duration'],seed=saved['seed'])
                    quote=client.video_quote(spec)
                    accept_quote(saved,quote,spec)
                    update(identity,video_request=saved)
                if event.is_set(): break
                update(identity,video_execution_started=True)
                remote=create_or_renew(client,saved,lambda value:update(identity,video_request=value))
            remote_id=str(remote.get('id') or remote.get('job_id') or '')
            if not remote_id: raise ValueError('云端返回缺少任务编号，已保留幂等键')
            update(identity,video_task_id=remote_id)
            deadline=time.monotonic()+max(7200,client.config.max_wait_seconds)
            cancel_sent=False
            while True:
                state=remote.get('status')
                update(identity,video_progress_message=str(remote.get('message') or remote.get('stage') or state),video_resume_available=True)
                if state in {'succeeded','completed'}:
                    output=vg._asset(path,shot['video'])
                    client.download_to('/cloud/video/jobs/'+remote_id+'/result',output,max_bytes=1024*1024*1024)
                    from .pipeline import probe_media_duration
                    try:
                        duration=probe_media_duration(output)
                        if not math.isfinite(duration) or duration <= 0:
                            raise ValueError('集群返回的视频无法解码，请继续查询或联系管理员')
                        expected=(remote.get('result') or {}).get('sha256')
                        if expected and hashlib.sha256(output.read_bytes()).hexdigest()!=expected:
                            raise ValueError('视频下载校验失败，请继续查询并重新下载原结果')
                    except Exception:
                        output.unlink(missing_ok=True)
                        raise
                    update(identity,video_status='completed',video_terminal=False,video_resume_available=False,video_version=vg._video_version(output),video_design_changed=False,video_finished_at=time.time(),video_progress_message='集群视频已完成并下载')
                    break
                if state in {'failed','cancelled','expired'}:
                    error=remote.get('error')
                    if isinstance(error,dict):
                        error='：'.join(str(value) for value in (error.get('code'),error.get('message')) if value)
                    update(identity,video_status='stopped' if state=='cancelled' else 'failed',video_terminal=True,video_resume_available=False,video_error=str(error or remote.get('message') or state),video_finished_at=time.time())
                    break
                if event.is_set() and not cancel_sent:
                    remote=client.cancel_video_job(remote_id);cancel_sent=True;continue
                if time.monotonic()>deadline: raise TimeoutError('云端仍未完成，已保留任务编号，请继续查询')
                time.sleep(max(1,min(10,float(remote.get('poll_after_seconds') or 2))))
                remote=client.video_job(remote_id)
            if event.is_set() or state in {'failed','cancelled','expired'}: break
    except Exception as exc:
        if current_id:
            update(current_id,video_status='unknown',video_terminal=False,video_resume_available=True,
                   video_progress_message='本地查询或下载已中断，尚未确认结果；请继续查询原任务',video_error=str(exc)[:1800])
        with studio.LOCK:
            record=studio.read(path);record['error']=str(exc)[:1800]
            if isinstance(exc,CloudApiError) and exc.status_code==401: record['cloud_login_required']=True
            studio.save(path,record)
    finally:
        with studio.LOCK:
            record=studio.read(path)
            for shot in record['shots']:
                shot['video_queued']=False
                if shot.get('video_status')=='running':shot.update(video_status='unknown',video_resume_available=True,video_terminal=False)
            record.update(status='video_review',revision=record['revision']+1)
            studio.save(path,record)
            studio.ACTIVE.discard(str(path));studio.CANCEL_EVENTS.pop(str(path),None)
