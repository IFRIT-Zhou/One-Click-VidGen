"""User-selected material strategy, independent of a shot or visual template."""
from __future__ import annotations


def subject_policy(advanced):
    if advanced:
        return dict(advanced_subject_separation=True, mode='advanced_subject_separation',
            label='进阶主体分离（试用）',
            instruction='用户允许进一步分离复杂物体。由你根据边缘质量与设计需要选择原裁片、RMBG或底图保留；'
                        '不是要求所有主体都抠图。照片/研究证据保真要求不变。')
    return dict(advanced_subject_separation=False, mode='stable_background_retention',
        label='稳定分层 · 复杂主体保留底图',
        instruction='默认稳定路线：编辑core原海报，只移除需要重建的文字、文本框、与文字关联的标注线及需独立还原的人物。'
                    '器官、器械、液体、半透明物体、成组图标等非证据性主体原位留在底图，'
                    '在background_retained逐项登记core区域，不独立裁切/抠图，不再覆盖一份。'
                    '人物使用role=person，按需使用独立参考或core取材；边缘不可靠时反馈，不得直接丢弃。'
                    '真实照片与原始病理/实验/数据图使用photo/evidence+preserve，最终显示原像素，不能生成式重画。'
                    '普通文本框与标注优先用代码重建，复杂原框可用text_panel保真。'
                    '文字、框线、指示线的排版和动效由你设计，不要求底图中的复杂主体独立入场。'
                    '保持原海报背景装饰和基本风格，不用纯色替换有内容的底图；不删导管、血管、器械轮廓等主体结构线。'
                    '若没有兼容底图，按授权调用edit_background；无图片预算就说明缺少底图/授权，不偷偷切换进阶路线。')


def validate_subject_policy(inventory, advanced):
    """Don't guess a reference crop's core coordinates or rewrite model choices."""
    if advanced:
        return
    unsupported = [f'subject_{index}（{subject.name}）'
                   for index, subject in enumerate(inventory.subjects) if subject.role == 'object']
    if unsupported:
        raise ValueError('当前未开启“进阶主体分离（试用）”，不能独立裁切/抠图普通物体：'
                         + '、'.join(unsupported)
                         + '。请对照完整core，把这些非证据性物体登记到background_retained并使用保留它们的底图；'
                         '不要按参考图裁区猜测core坐标，也不要删除主体。人物请明确role=person；'
                         '真实照片/研究证据分别用photo/evidence+preserve。需要复杂主体独立运动时由用户另开进阶任务。')
