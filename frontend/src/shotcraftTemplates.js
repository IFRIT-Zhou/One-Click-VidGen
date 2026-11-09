// Catalog guidance is editorial; only runtime limits disable a manual choice.
export const MAX_TEMPLATE_CHOICES = 3

export function templateMediaUrl(value) {
  return typeof value === 'string' && /^\/api\/vibe-templates\/[a-z0-9-]+\/(preview|poster)(\?v=[a-f0-9]{16})?$/i.test(value) ? value : ''
}

export function templateLimits(item) {
  const limits = item?.capabilities?.hard_constraints || {}
  const minimum = Number(limits.min_stage_seconds)
  const maximum = Number(limits.max_stage_seconds)
  const count = limits.items || {}
  const seconds = Number.isFinite(minimum) && minimum > 0
    ? `${maximum > 0 ? `${minimum}–${maximum}` : `至少 ${minimum}`} 秒` : ''
  const items = Number.isInteger(count.minimum) && Number.isInteger(count.maximum)
    ? `${count.minimum === count.maximum ? count.minimum : `${count.minimum}–${count.maximum}`} 项` : ''
  return [seconds, items].filter(Boolean).join(' · ')
}

export function templateTags(item, vocabulary = {}) {
  const capabilities = item?.capabilities || {}
  const content = (capabilities.content_tags || []).map(tag => vocabulary.content?.[tag] || tag)
  const materials = (capabilities.material_tags || []).filter(tag => tag !== 'mixed_text_images')
    .map(tag => vocabulary.material?.[tag] || tag)
  return [...new Set([...content, ...materials])]
}

export function templateSelectionIssue(ids, items, duration, loaded = true) {
  if (!Array.isArray(ids) || !ids.length) return ''
  if (ids.length > MAX_TEMPLATE_CHOICES || new Set(ids).size !== ids.length) return '请选择 1–3 款不同动效。'
  if (!loaded) return '模板目录尚未就绪，可重试读取，或改为让 AI 自动选择。'
  const selected = ids.map(id => items.find(item => item.id === id))
  const missing = selected.findIndex(item => !item || !item.available)
  if (missing >= 0) return selected[missing]?.reason || '所选动效当前不可用，请更换或让 AI 自动选择。'
  if (Number.isFinite(duration) && duration > 0) {
    const minimum = selected.reduce((sum, item) => sum + (Number(item.capabilities?.hard_constraints?.min_stage_seconds) || 0), 0)
    if (minimum > duration + 0.000001) return `所选动效合计至少需要 ${Number(minimum.toFixed(2))} 秒，本镜为 ${Number(duration.toFixed(2))} 秒；请减少选择或换款。`
    if (selected.length === 1) {
      const maximum = Number(selected[0].capabilities?.hard_constraints?.max_stage_seconds)
      if (maximum > 0 && duration > maximum + 0.001) return `“${selected[0].name}”最多适合 ${maximum} 秒，请搭配另一款或改为自动选择。`
    }
  }
  return ''
}

export function toggleTemplate(ids, id) {
  if (ids.includes(id)) return ids.filter(value => value !== id)
  return ids.length < MAX_TEMPLATE_CHOICES ? [...ids, id] : [...ids]
}

export function animatedTemplateIds(items, visibleIds, activeId, enabled, limit = 4) {
  if (!enabled) return new Set()
  const visible = items.filter(item => visibleIds.has(item.id) && templateMediaUrl(item.preview_url))
  const priority = visible.filter(item => item.id === activeId)
  return new Set([...priority, ...visible.filter(item => item.id !== activeId)].slice(0, limit).map(item => item.id))
}
