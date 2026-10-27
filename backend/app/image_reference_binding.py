"""Keep image-prompt references bound to asset identity, not request position."""
import re
import hashlib
import json

NUMBER = re.compile(r'图\s*(\d+)(?!\d)')
TOKEN = re.compile(r'\[\[ocv-ref:([^\]\r\n]+)\]\]')


def context_key(shot):
    selection = shot.get('redraw_selection') or {}
    values = [shot.get('image_prompt'), shot.get('reference_ids'), selection.get('reference_ids'),
              selection.get('use_current_image'), shot.get('image_material_numbers_bound')]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def prompt_order(shot):
    saved = shot.get('image_prompt_reference_order')
    if isinstance(saved, list) and shot.get('image_prompt_reference_context_key') == context_key(shot):
        return saved
    selection = shot.get('redraw_selection') or {}
    order = ['reference:' + str(value) for value in selection.get('reference_ids', shot.get('reference_ids', []))]
    if shot.get('image_material_numbers_bound') and selection.get('use_current_image'):
        order.insert(0, 'current_image:' + str(shot['id']))
    return order


def freeze_numbers(prompt, order):
    # Protect already-stable references, and do not mutate the stored prompt.
    def replace(match):
        index = int(match[1]) - 1
        return '[[ocv-ref:' + order[index] + ']]' if 0 <= index < len(order) else match[0]
    return NUMBER.sub(replace, prompt)


def render_numbers(prompt, references):
    by_identity = {str(r['kind']) + ':' + str(r['id']): r['local_image_number'] for r in references}
    def replace(match):
        if match[1] not in by_identity:
            raise ValueError('提示词引用的素材未附入本次请求：' + match[1])
        return '图' + str(by_identity[match[1]])
    return TOKEN.sub(replace, prompt)
