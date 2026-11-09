"""Version-scoped, compact working context; the full audit remains on disk."""
from __future__ import annotations

from copy import deepcopy
import hashlib


def binding(candidate):
    result = dict(preview_id=candidate['preview_id'], code_sha256=candidate['code_sha256'],
                  material_fingerprint=candidate['fingerprint'])
    if candidate.get('measurement_stamp'):
        result['measurement_stamp'] = candidate['measurement_stamp']
    return result


def review_matches(review, candidate):
    return bool(review and all(review.get(key) == value for key, value in binding(candidate).items()))


def _material_notices(value, path='materials'):
    notices = []
    if isinstance(value, dict):
        for key, item in value.items():
            location = path+'.'+key
            if item and any(word in key.lower() for word in ('error', 'warning', 'rejected')):
                notices.append(dict(path=location, value=deepcopy(item)))
            elif isinstance(item, (dict, list)):
                notices.extend(_material_notices(item, location))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            notices.extend(_material_notices(item, f'{path}[{index}]'))
    return notices


def _receipt(value):
    # Measurements and image diagnostics are sent once for the current candidate,
    # not again in every historical tool event and latest-by-tool observation.
    result = {key: deepcopy(item) for key, item in value.items()
              if key not in {'dom', 'inspection', 'inventory', 'material_status',
                             'review', 'previous_response', 'previous_outcome', 'materials', 'evidence'}}
    if 'evidence' in value:
        result['evidence_available_in'] = 'current_review_evidence (only matching current versions); full audit retained locally'
    if 'review' in value:
        result['review_available_in'] = 'review_context'
    if 'previous_response' in value:
        result['previous_response'] = str(value['previous_response'])[-2500:]
    if 'previous_outcome' in value:
        result['prior_attempt_note'] = '只复用已核验的素材；旧任务自评或失败结论不代表本次结果，完整旧回执仍保存在本地。'
    if 'materials' in value:
        result['materials_available_in'] = 'material_status'
        result['material_notices'] = _material_notices(value['materials'])
    return result


def working_context(state, fingerprint):
    candidates = list(state['previews'].values())
    latest_code = (state.get('latest_code') or {}).get('code', '')
    code_hash = hashlib.sha256(latest_code.encode()).hexdigest()
    latest = candidates[-1] if candidates else None
    current = latest if latest and latest['fingerprint'] == fingerprint and latest['code_sha256'] == code_hash else None
    reviews = list(state.get('review_history', []))
    reviews += [event['receipt']['review'] for event in state['events']
                if event['tool'] == 'finish' and isinstance(event.get('receipt', {}).get('review'), dict)]
    reviews += list(state.get('delivery_reviews', {}).values())
    # Resume old state without silently losing a failure; it has no binding and
    # therefore cannot be used to approve or reject a current candidate.
    legacy = state.get('observations', {}).get('delivery_review')
    if legacy and legacy not in reviews:
        reviews.append(legacy)
    pending = []
    for review in reviews:
        if review.get('passed') is False:
            for issue in review.get('issues', []):
                found = next((item for item in pending if item['observation'] == issue), None)
                origin = {key: review.get(key) for key in ('preview_id', 'code_sha256', 'material_fingerprint', 'measurement_stamp')}
                if found:
                    if origin not in found['observed_on']:
                        found['observed_on'].append(origin)
                else:
                    pending.append(dict(observation=issue, observed_on=[origin],
                        status='needs_visual_recheck', current_fact=False))
    current_review = next((r for r in reversed(reviews) if current and review_matches(r, current)), None)
    # A valid tool invocation resolves the previous JSON/tool-call format error,
    # even if that tool later reports a different, task-local failure. Older
    # checkpoints kept the format observation forever; recover those safely too.
    last_event = state['events'][-1] if state['events'] else None
    format_pending = not last_event or last_event.get('tool') == 'response_format'
    observations = {name: _receipt(receipt) for name, receipt in state['observations'].items()
                    if name != 'delivery_review' and (name != 'response_format' or format_pending)
                    and (name not in {'preview_scene', 'finish'}
                        or receipt.get('error') and not receipt.get('review'))}
    for receipt in observations.values():
        if (receipt.get('validation_feedback') and
                (not current or receipt.get('preview_id') != current['preview_id'])):
            receipt.pop('validation_feedback')
            receipt['validation_feedback_scope'] = 'historical attempt; full evidence retained in local record'
            if receipt.get('error'):
                receipt['error'] = str(receipt['error'])[:280]
                receipt['error_scope'] = 'historical_attempt_only; not an instruction for current code'
    recent = []
    for item in state['events'][-6:]:
        if item['tool'] == 'response_format':
            continue
        receipt = _receipt(item['receipt'])
        historical = item is not last_event
        if historical and receipt.get('error'):
            receipt['error'] = str(receipt['error'])[:280]
            receipt['error_scope'] = 'historical_attempt_only; not an instruction for current code'
            if receipt.pop('validation_feedback', None):
                receipt['validation_feedback_scope'] = 'historical attempt; full evidence retained in local record'
        recent.append(dict(tool=item['tool'], summary=item['summary'][:300] if historical else item['summary'], receipt=receipt))
    summaries = []
    for item in candidates:
        is_current = bool(current and item['preview_id'] == current['preview_id'])
        error = item.get('validation_error')
        summaries.append(dict(**binding(item), locally_valid=item['locally_valid'],
            preview_level=item.get('preview_level', 'full'),
            full_motion_check_required=item.get('preview_level') == 'layout',
            validation_error=error if is_current or not error else str(error)[:280],
            validation_scope='current_candidate' if is_current else 'historical_attempt_only',
            image_ids=item['image_ids'] if is_current else [], current=is_current))
    return dict(observations=observations, recent_tool_results=recent, previews=summaries,
                review_context=dict(current_candidate=binding(current) if current else None,
                    current_review=deepcopy(current_review), previous_findings_to_recheck=pending,
                    rule='旧问题是指定版本的待复查观察，不是新图的既定事实。看当前完整结果/局部图核实；'
                         '仍存在就修复，不存在就说明依据并请求当前版本finish独立核查。新预览不自动通过，旧结论也不自动沿用。'),
                current_preview=current)


def image_roles(images, sources, state):
    """Label pixels by role, including tool-requested zooms; never a verdict."""
    candidates = state.get('previews', {})
    result = {}
    for name in images:
        if name in sources:
            result[name] = dict(role='source_poster' if name == 'core' else 'source_reference',
                                is_rendered_result=False)
            continue
        target, lineage, visited = name, [], set()
        while target not in visited:
            visited.add(target)
            event = next((event for event in reversed(state.get('events', []))
                          if event['tool'] == 'inspect_image' and event['receipt'].get('asset_id') == target
                          and event['receipt'].get('requested_asset_id') != target), None)
            if not event:
                break
            receipt = event['receipt']
            lineage.append(dict(asset_id=target, parent_id=receipt['requested_asset_id'],
                                region_in_parent=receipt.get('region')))
            target = receipt['requested_asset_id']
        region = lineage[0]['region_in_parent'] if lineage else None
        evidence = next((item for item in reversed(list(state.get('review_evidence', {}).values()))
                         if target in item.get('image_ids', [])), None)
        if evidence:
            result[name] = dict(role='measured_same_window_review_evidence',
                **{key:evidence.get(key) for key in ('preview_id','code_sha256','material_fingerprint','measurement_stamp')},
                evidence_id=evidence['evidence_id'], root_image_id=target, region=region, lineage=lineage,
                comparison_order='root image: left core; middle actual background; right rendered poster',
                is_rendered_result=False, contains_rendered_comparison=True,
                note='本地同窗对照，不是质量结论；region只是搜索窗口，位置以绑定的OCR/浏览器实测及其不确定性为准。')
            continue
        candidate = next((c for c in candidates.values() if target in c['image_ids']), None)
        if candidate:
            role = ('rendered_poster' if target == candidate['preview_id'] else
                    'animation_keyframes' if target.endswith('_keyframes') else 'source_result_comparison')
            result[name] = dict(role=role, **binding(candidate), region=region, lineage=lineage,
                                root_image_id=target,
                                comparison_order=('root image: left source; right rendered result; this crop may contain only one side'
                                    if lineage else 'left: source; right: rendered result') if role == 'source_result_comparison' else None)
        else:
            result[name] = dict(role='source_poster' if target == 'core' else 'source_or_prepared_material_only', requested_asset_id=target,
                                region=region, lineage=lineage, is_rendered_result=False,
                                note='这是输入素材/底图，不是完整结果；缺少文字或装饰不代表成片缺失。')
    return result
