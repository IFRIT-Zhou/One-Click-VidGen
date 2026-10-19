export function planningMaximum(parameters = {}) {
  if (parameters.video_generation_backend === 'cloud_h3' && parameters.dynamic_cluster_duration_recommended !== false) return 14
  const value = parameters.dynamic_max_shot_duration
  return Number.isInteger(value) && value >= 4 ? value : 14
}

export function splitMaximum(shot, parameters = {}) {
  const backend = shot?.video_generation_options?.backend || parameters.video_generation_backend || 'api'
  return backend === 'cloud_h3' ? 14 : planningMaximum({...parameters, video_generation_backend: backend})
}

export function planningPreferences(parameters = {}) {
  const recommended = parameters.dynamic_cluster_duration_recommended !== false
  let value = parameters.dynamic_max_shot_duration ?? 14
  if (!Number.isInteger(value) || value < 4) {
    if (parameters.video_generation_backend === 'cloud_h3' && recommended) value = 14
    else throw new Error('分镜规划最长时长请填写至少 4 秒的整数。')
  }
  return {dynamic_max_shot_duration: value, dynamic_cluster_duration_recommended: recommended}
}
