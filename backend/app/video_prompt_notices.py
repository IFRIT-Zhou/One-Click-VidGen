"""Severity of literal prompt handoff notices, not semantic judgments.

The browser mirrors these stable prefixes for historical string-only records.
Unknown diagnostics are advisory until explicitly assigned a stronger level.
"""


def prompt_notice_level(message: str) -> str:
    text = str(message or '').strip()
    if not text or text.startswith(('提示词分节标题不完整或顺序不同', '分镜图提示词结构或顺序不正确')):
        return 'format'
    if text.startswith(('缺少短文字原文', '规划了画面短文字，却同时要求全面禁止文字',
                        '核心图混入未选用的阶段文字', '核心图混入其他阶段文字')):
        return 'warning'
    return 'info'


def has_prompt_warning(messages) -> bool:
    return any(prompt_notice_level(message) == 'warning' for message in messages or [])
