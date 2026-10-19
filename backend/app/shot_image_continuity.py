"""Bind explicit previous-shot visual continuity at the image request boundary.

Only core-image prompts opt in. Narration, video prompts and generic stylistic
similarity do not silently add images. Existing material numbers stay intact.
"""
import re
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

HINT = re.compile(r'\n?【自动承接参考】[^\n]*')
PREVIOUS = r'(?:上一(?:个)?(?:镜头|分镜|镜)|前一(?:个)?(?:镜头|分镜|镜)|上一张(?:分镜图|核心图))'
REQUEST = re.compile(r'(?:延续|沿用|承接|继承|接续|参考|参照|保持|保留|复用)\s*' + PREVIOUS
                     + '|' + PREVIOUS + r'.{0,18}(?:保持一致|保持连续|保持相同|作为参考|为参考|一致|相同)')
NEGATIVE = re.compile(r'不(?:要|得|必|再|用|应|需要)?|无需|禁止|避免|并非')


def strip_hint(prompt):
    return HINT.sub('', str(prompt or '')).rstrip()


def needs_previous(prompt):
    # Text to be drawn is not a request to supply a reference image.
    clean = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"', '', strip_hint(prompt))
    for clause in re.split(r'[，,。；;！!？?\n]', clean):
        match = REQUEST.search(clause)
        if match and not NEGATIVE.search(clause[max(0, match.start() - 8):match.start()]):
            return True
    return False


def previous_shot(record, shot, prompt=None):
    if not needs_previous(shot.get('image_prompt') if prompt is None else prompt):
        return None
    shots = record.get('shots', [])
    index = next((i for i, item in enumerate(shots) if item.get('id') == shot.get('id')), None)
    # Scene assets are not storyboard shots and must not gain this dependency.
    if index is None:
        return None
    if index == 0:
        raise ValueError('第 01 镜提示词要求承接上一镜，但它已是首镜；请删除承接描述或选择明确参考素材')
    return shots[index - 1]


def previous_image_path(path, record, shot, prompt=None):
    source = previous_shot(record, shot, prompt)
    if source is None:
        return None
    shots = record['shots']
    index = next(i for i, item in enumerate(shots) if item.get('id') == shot.get('id'))
    label = f'第 {index + 1:02d} 镜需要承接第 {index:02d} 镜（{source["id"]}）'
    candidate = (path / str(source.get('image') or '')).resolve()
    if ((source.get('image_task') or {}).get('status') in {'queued', 'running'}
            or source.get('image_status') not in {None, 'completed'}):
        raise ValueError(label + '，上一镜核心图尚未完成；请先生成或完成上一镜重绘后重试')
    if (path.resolve() not in candidate.parents or not candidate.is_file() or not candidate.stat().st_size):
        raise ValueError(label + '，但上一镜核心图缺失；请先生成或上传上一镜核心图，不会替换为其他图片')
    return str(candidate)


def bind_previous(path, record, shot, prompt, references, *, limit=9):
    prompt = strip_hint(prompt)
    paths = list(references)
    source_path = previous_image_path(path, record, shot, prompt)
    if source_path is None:
        return prompt, paths
    normalized = [str(Path(value).resolve()) for value in paths]
    if source_path not in normalized:
        if len(paths) >= limit:
            raise ValueError('承接上一镜还需 1 张参考图，但本次参考图已满；请取消一张可选参考图后重试，不会自动删除已选素材')
        paths.append(source_path)
        normalized.append(source_path)
    number = normalized.index(source_path) + 1
    source = previous_shot(record, shot, prompt)
    prompt += (f'\n【自动承接参考】图{number}为上一镜核心图（镜头 ID：{source["id"]}）。'
               f'提示词中的上一镜仅指图{number}；仅承接所要求的主体外观、空间结构和对应位置，'
               '本镜仍按当前内容表现新阶段，不照搬上一镜全部内容，不继承未选用的画中文字。')
    return prompt, paths


def dependency_preview(record):
    result = {}
    for index, shot in enumerate(record.get('shots', [])):
        if not needs_previous(shot.get('image_prompt')):
            continue
        source = record['shots'][index - 1] if index else None
        result[shot['id']] = {'shot_id': source['id'] if source else None,
                             'index': index, 'error': '' if source else '首镜没有上一镜，请修改承接描述'}
    return result


def run_with_dependencies(record, pending, generate_one, max_workers, cancelled):
    """Wait outside worker threads: dependent shots cannot exhaust the pool."""
    remaining = {shot['id']: (index, shot) for index, shot in pending}
    unfinished = set(remaining)
    dependencies = {}
    for identity, (_, shot) in remaining.items():
        try:
            source = previous_shot(record, shot)
        except ValueError:
            source = None  # generate_one records a shot-local input error.
        dependencies[identity] = source['id'] if source else None
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='video-storyboard') as pool:
        running = {}
        while remaining or running:
            if not cancelled.is_set():
                for identity, item in list(remaining.items()):
                    if len(running) >= max_workers:
                        break
                    if dependencies[identity] in unfinished:
                        continue
                    running[pool.submit(generate_one, *item)] = identity
                    del remaining[identity]
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                identity = running.pop(future)
                future.result()
                unfinished.discard(identity)
