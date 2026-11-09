// Single-shot, local-only preview. Project and shot identity always come from
// the open project, never from an imported animation plan.
export const MAX_PLAN_BYTES = 64 * 1024
export function previewRoute(project, shot) {
  return '/api/video-studio/' + encodeURIComponent(project) + '/shots/' + encodeURIComponent(shot)
}
// This is a new-attempt option, never inferred from a prior task or preview.
export function layeredRequestOptions(engine, advancedSubjectSeparation = false) {
  return engine === 'layered' ? {advanced_subject_separation: advancedSubjectSeparation === true} : {}
}
export function layeredStrategyDescription(advancedSubjectSeparation = false) {
  return advancedSubjectSeparation === true
    ? '进阶主体分离（试用）：允许模型尝试分离复杂器官、输液袋等主体，可能增加边缘缺损、接缝和返工。'
    : '稳定分层：复杂器官、输液袋、透明物体及其光影留在底图；人物按需独立，使用本地 RMBG。文字、文本框和简单标注由代码编排。'
}
export function layeredTaskStrategy(task) {
  if (task?.template_mode !== 'layered') return ''
  if (task.advanced_subject_separation === true) return '进阶主体分离（试用）· 允许复杂主体分离'
  if (task.advanced_subject_separation === false) return '稳定分层 · 复杂主体留在底图'
  return '旧版分层策略 · 继续时沿用原任务记录'
}
export function freshCloudStartNotice(task, preview) {
  if (!task?.task_id && !preview?.preview_id) return '本次新建独立任务，从当前原图、参考素材和要求开始设计。'
  const limit = task?.max_model_calls
  const used = Array.isArray(task?.usage?.model_calls) ? task.usage.model_calls.length : null
  const budget = Number.isInteger(limit) && limit > 0 && used != null
    ? `上一轮已用 ${used}/${limit} 次，剩余 ${Math.max(0,limit-used)} 次；这些次数不转入本次，也不会从本次预算中扣除。\n` : ''
  return '重新开始确认：本镜已有制作记录。确认后将按当前设置另开任务，从原图和绑定参考素材重新设计，不带入旧派生素材、代码或审核结论。\n'
    + budget + '旧记录及正式镜头保留，不会删除；本次费用和调用次数另计。'
    + (task?.can_continue_steps ? '如想沿用本轮保存步骤和剩余次数，请取消并使用“继续未完成步骤”。'
      : task?.can_continue_review ? '如想利用上轮已有成果或剩余次数，请取消并使用“继续检查与返工”。' : '')
}
export function stepContinuationBudget(task) {
  const modelUsed = Array.isArray(task?.usage?.model_calls) ? task.usage.model_calls.length : null
  const modelLimit = task?.max_model_calls, imageUsed = task?.usage?.image_requests, imageLimit = task?.max_image_requests
  const valid = [modelUsed, modelLimit, imageUsed, imageLimit].every(value => Number.isInteger(value) && value >= 0)
  const modelRemaining = valid ? Math.max(0, modelLimit-modelUsed) : 0
  const remainingImages = valid ? Math.max(0, imageLimit-imageUsed) : 0
  const imageRemaining = Number.isInteger(task?.continue_steps_image_remaining)
    ? Math.max(0, Math.min(remainingImages, task.continue_steps_image_remaining)) : 0
  return {valid, modelUsed, modelLimit, modelRemaining, imageUsed, imageLimit, imageRemaining,
    imagesAllowed: valid && task?.continue_steps_images_allowed === true && task?.new_images_allowed !== false && imageRemaining > 0}
}
export function stepContinuationNotice(task, allowImages = false) {
  const budget = stepContinuationBudget(task)
  return `继续本轮未完成步骤，不从头重新设计。已保存的素材、代码、预览及步骤记录会保留；即使尚无清单或预览，也可从当前保存状态继续，由 DS 决定下一步，不自动重放已执行的工具。\n`
    + `模型已用 ${budget.modelUsed}/${budget.modelLimit} 次，原授权剩余 ${budget.modelRemaining} 次；本次不追加次数，后续模型调用仍可能计费（含失败请求与交付核查）。\n`
    + (allowImages && budget.imagesAllowed
      ? `图片已用 ${budget.imageUsed}/${budget.imageLimit} 次；允许使用原授权剩余最多 ${budget.imageRemaining} 次图片请求，可能计费。不增加图片额度，不切换原图片渠道。\n`
      : '本次不授权新增图片请求，仅复用已有素材；不会自动重放旧图片任务。本轮后续续接也不会恢复图片授权，若需新增出图请另开新任务。\n')
    + '沿用原任务的要求、策略和渠道，上方新任务设置不用于本次继续。不覆盖正式镜头，不自动重试；再次中断后仍需你手动确认。'
}
export function vibeCloudSubmissionError(error) {
  const message = typeof error?.message === 'string' ? error.message : String(error ?? '云端任务提交失败')
  // Only the rejected request field identifies this stale-backend contract.
  // Do not reinterpret provider/network errors or other validation failures.
  const unsupportedSeparation = /\badvanced_subject_separation\b\s*[:：]?\s*(?:Extra inputs are not permitted|(?:\[\s*)?(?:type\s*=\s*)?extra_forbidden\b)/i.test(message)
  if ((error?.status == null || error.status === 422) && unsupportedSeparation) {
    return 'OCV 后端尚未加载新的分层策略参数（advanced_subject_separation），前后端版本或运行状态不一致。请在当前任务安全结束后重启 OCV 应用服务，并确认前后端为同一版本，再手动开始。本次请求在参数校验阶段被拒绝，未启动制作；未删除参数、自动重试或切换模式。'
  }
  const unsupportedFresh = /\bstart_mode\b\s*[:：]?\s*(?:Extra inputs are not permitted|(?:\[\s*)?(?:type\s*=\s*)?extra_forbidden\b)/i.test(message)
  if ((error?.status == null || error.status === 422) && unsupportedFresh) {
    return 'OCV 后端尚未加载“从头重新设计”参数（start_mode）。请在当前任务安全结束后重启 OCV 应用服务，并确认前后端为同一版本，再手动开始。本次请求在参数校验阶段被拒绝，未启动制作；不会删除参数后重试或悄悄沿用旧任务。'
  }
  return message + '；未切换到其他模型或本地模式。'
}
export function parsePreviewPlan(text) {
  const invalid = '请选择本镜的动画方案 JSON（含 pack_id、source_sha256 和 plan.sequence）。'
  let body
  try { body = JSON.parse(text.replace(/^\uFEFF/, '')) } catch { throw new Error(invalid) }
  if (!body || typeof body !== 'object' || Array.isArray(body) ||
      Object.keys(body).some(k => !['pack_id','source_sha256','input_stamp','plan'].includes(k)) ||
      (body.input_stamp != null && !/^[a-f0-9]{64}$/.test(body.input_stamp)) ||
      !/^[a-f0-9]{32}$/.test(body.pack_id) || !/^[a-f0-9]{64}$/.test(body.source_sha256) ||
      !Array.isArray(body.plan?.sequence) || !body.plan.sequence.length) throw new Error(invalid)
  return body
}
export function previewFileUrl(project, receipt, asset) {
  if (!receipt || !/^[a-f0-9]{32}$/.test(receipt.preview_id) || !['preview','silent','proof'].includes(asset)) return ''
  return '/api/video-studio/' + encodeURIComponent(project) + '/vibe-previews/' + receipt.preview_id + '/' + asset
}
export function vibeBrief(project, shot, number) {
  return `用 OCV MCP 为项目 ${project} 第 ${number} 镜（shot_id: ${shot}）制作分层动效预览。先读取本镜当前核心图和最终配音字幕，再用 OCV 本地图层提取和单镜动效预览工具。按口播分步呈现，保留原图细节，给我带配音预览和无声版。只做本地预览，不替换正式镜头，不调用付费模型。如需外发素材或能力未开放，请先说明。`
}
