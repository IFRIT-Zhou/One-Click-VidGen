"""Reconcile an agent's stopping claim with host-owned budget facts.

This module neither executes tools nor grants more authority. One contradictory
image-budget claim may be returned for reconsideration; the creator can still
stop for quality or another real blocker without spending the remaining money.
"""
from __future__ import annotations

import re


_CLAUSES = re.compile(r'[。；;\n.!?！？]')
_IMAGE = r'(?:图片|图像|出图|背景编辑|修图)'
_BUDGET = r'(?:预算|额度|配额|次数|限额)'
_CLAIMS = tuple(re.compile(pattern, re.I) for pattern in (
    rf'(?:增加|扩大|扩充|补充|提高|追加|更多)(?:的)?\s*{_IMAGE}[^，,。；;\n]{{0,12}}{_BUDGET}',
    rf'{_IMAGE}[^，,。；;\n]{{0,12}}{_BUDGET}(?:已|已经|本次|目前|现在|仍|全部|都|被|已被|完全|\s)*'
    r'(?:不足|不够|耗尽|用完|用尽|为\s*0|为零|没有剩余)',
    rf'(?:没有|已无|耗尽|用完|用尽)(?:可用|剩余|授权|的|\s)*{_IMAGE}[^，,。；;\n]{{0,12}}{_BUDGET}',
    r'\b(?:increase|expand|raise|extend|add|more)\s+(?:the\s+)?image(?:\s+(?:editing|generation|request))?\s+(?:budget|quota|allowance|limit)\b',
    r'\bimage(?:\s+(?:editing|generation|request))?\s+(?:budget|quota|allowance|limit)\s+(?:(?:is|was|has been|already)\s+)*'
    r'(?:exhausted|insufficient|used up|depleted|reached)\b',
    r'\b(?:no|insufficient)\s+(?:remaining\s+)?image(?:\s+(?:editing|generation|request))?\s+(?:budget|quota|allowance)\b',
))
_NEGATED_REQUEST = re.compile(r'(?:不需要|无需|不必|并非|不是|没有必要|不用|不要求)\s*$|'
                              r"(?:do not need|don't need|no need to|not necessary to|without needing to)\s*$", re.I)


def claims_image_budget(message, reason='other'):
    """Compatibility with old agents that only sent free-form messages.

    Prefer the explicit reason. For old responses, only recognize clear image
    quota assertions/requests, not general quality concerns or model budgets.
    This is a diagnostic classifier, never an authorization decision.
    """
    if reason == 'image_budget':
        return True
    for clause in _CLAUSES.split(str(message)):
        for pattern in _CLAIMS:
            for match in pattern.finditer(clause):
                if not _NEGATED_REQUEST.search(clause[:match.start()]):
                    return True
    return False


def reconcile_blocker(message, reason, budget, *, already_corrected=False,
                      pending_image=False, edit_available=False,
                      required_image_requests=None):
    """Return a bounded response to a possibly contradictory stop request.

    The caller persists whether a correction was already issued. ``retry``
    means return feedback to the agent once, NOT retry an image/model request.
    No mutable input is changed, no usage counters are reset, and no tool runs.
    """
    facts = {key: budget.get(key) for key in (
        'image_limit', 'image_used', 'image_remaining', 'new_images_allowed',
        'model_remaining',
    )}
    remaining = budget.get('image_remaining', 0)
    claimed = claims_image_budget(message, reason)
    # A nonzero balance does not prove a multi-step plan fits the remaining
    # allowance. This is the creator's estimate, not newly granted authority.
    needs_more = bool(reason == 'image_budget' and required_image_requests is not None
                      and required_image_requests > remaining)
    contradiction = bool(claimed and not needs_more and remaining > 0
                         and budget.get('new_images_allowed'))
    receipt = dict(ok=False, blocker_reason=reason, budget=facts,
                   pending_image=bool(pending_image), edit_available=bool(edit_available),
                   budget_claim_corrected=contradiction)
    if required_image_requests is not None:
        receipt['required_image_requests'] = required_image_requests
        receipt['required_requests_exceed_remaining'] = required_image_requests > remaining
    if not contradiction:
        return dict(retry=False, message=message, receipt=receipt)

    fact_text = f'图片请求额度已用 {budget.get("image_used", 0)}/{budget.get("image_limit", 0)} 次，仍剩 {remaining} 次，并未耗尽。'
    receipt['error'] = '图片预算不足的停止理由与实际授权不一致。' + fact_text
    if pending_image:
        final = fact_text + '当前仍有已提交图片任务待核对，不能重复提交；需先核对原任务结果。'
    elif not edit_available:
        final = fact_text + '当前图片编辑工具尚不可用，请核对本项目图片渠道、配置及任务状态；不是增加图片预算即可解决。'
    elif budget.get('model_remaining', 0) <= 0:
        final = fact_text + '但本轮模型调用额度已用完，未继续调用或新增出图；工作记录已保留。'
    elif already_corrected:
        # Keep the raw model response in the caller's audit, not as a misleading
        # instruction telling the user to buy more quota in the visible error.
        final = fact_text + '模型复核后仍选择停止，当前素材或画面问题尚未解决；未自动出图，可检查已保存素材后手动继续。'
    else:
        receipt.update(code='image_budget_claim_mismatch',
            next='仍有授权图片额度，可自主选择 edit_background，以 remove_regions 定位待移除元素，'
                 '并将保留内容与编辑目标分开；也可以采用其他已授权工具或报告真实的质量/素材/工具阻碍。'
                 '不强制继续出图，不要求消耗剩余额度；一次编辑质量不佳不等于额度已耗尽或工具不支持编辑。'
                 '若选择停止，请使用对应 reason 并说明尚未解决的具体问题，不再称图片预算不足。')
        return dict(retry=True, message=receipt['error'], receipt=receipt)
    receipt['error'] = final
    return dict(retry=False, message=final, receipt=receipt)
