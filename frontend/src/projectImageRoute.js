export function imageRouteSelection(parameters = {}) {
  return {
    channel: parameters.use_cloud_image_pool ? (parameters.method === 'ican' ? 'ican' : 'running') : 'personal',
    image_profile_id: parameters.image_profile_id || parameters.image_profile_snapshot?.id || '',
    image_resolution: parameters.image_resolution || parameters.image_profile_snapshot?.resolution || '2k',
    size: parameters.size || '2560x1440',
  }
}

export function imageRouteKey(selection) {
  const common = [selection.channel]
  if (selection.channel === 'ican') common.push(selection.size)
  else if (selection.channel === 'running') common.push(selection.image_resolution)
  else common.push(selection.image_profile_id, selection.image_resolution)
  return JSON.stringify(common)
}

export function imageRouteLabel(selection, profile = null) {
  if (selection.channel === 'ican') return `ICAN 号池 · GPT Image 2.5 · ${selection.size.replace('x', '×')}`
  if (selection.channel === 'running') return `稳定版号池 · ${selection.image_resolution.toUpperCase()}`
  const quality = profile?.protocol === 'openai_sync' ? profile.size || 'auto' : selection.image_resolution.toUpperCase()
  return `个人 API · ${profile?.name || selection.image_profile_id || '接口默认配置'}${profile?.model_id ? ' · ' + profile.model_id : ''} · ${quality}`
}

// Only an explicit settings change made from the current project is handed back.
// Merely visiting settings, login expiry, or opening another project is not a route change.
export function serviceImageRouteUpdate(before, form, projectId) {
  const selection = imageRouteSelection(form)
  return projectId && before && imageRouteKey(before) !== imageRouteKey(selection)
    ? { projectId, selection } : null
}
