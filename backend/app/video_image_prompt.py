"""Render Agent-authored image sections without delegating layout to the LLM."""
from __future__ import annotations

import re


SECTION_FIELDS = (('characters_and_style', '人物与画风'), ('scene', '画面内容'), ('constraints', '必要限制'))


def without_leading_intent(prompt: str) -> str:
    """Only the leading legacy metadata block is replaceable, not quoted labels."""
    return re.sub(r'^\s*【本图旨在】.*?(?=【|\Z)', '', prompt, count=1, flags=re.S).strip()


def assemble_image_body(shot: dict, row: dict) -> None:
    """Compile new structured output; keep legacy free text losslessly compatible.

    Only automatic finalizer output passes here. Saved/manual prompts are never
    reflowed by an audit or by opening a task. Empty optional restrictions do not
    manufacture a global no-text/no-people rule.
    """
    if 'image_sections' not in row:
        # Older compatible providers/mocked responses may still use this field.
        return
    sections = row['image_sections']
    if not isinstance(sections, dict):
        raise ValueError(f"{shot['id']} 的核心图分节内容须为对象")
    unknown = set(sections) - {key for key, _ in SECTION_FIELDS}
    if unknown:
        raise ValueError(f"{shot['id']} 的核心图包含未知分节：{', '.join(sorted(unknown))}，请合并到对应的画面内容，不要丢弃")
    output = []
    for key, label in SECTION_FIELDS:
        value = sections.get(key, '' if key == 'constraints' else None)
        if not isinstance(value, str) or (key != 'constraints' and not value.strip()):
            raise ValueError(f"{shot['id']} 的{label}内容为空或不是文本")
        value = value.strip()
        # Tolerate a redundant own heading but never delete body/quoted text.
        value = re.sub(r'^【' + re.escape(label) + r'】\s*', '', value, count=1).strip()
        if key != 'constraints' and not value:
            raise ValueError(f"{shot['id']} 的{label}只有标题，缺少内容")
        if value:
            output.append(f'【{label}】{value}')
    row['image_prompt'] = '\n'.join(output)
