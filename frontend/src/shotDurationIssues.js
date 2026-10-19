// Match the saved per-shot route used by the backend storyboard confirmation.
// Historical requests and unsaved option drafts must not change this verdict.
export function shotDurationIssue(shot, parameters={}) {
  if (shot?.kind !== 'video') return null
  const duration = Number(shot.duration)
  if (!Number.isFinite(duration) || duration <= 0) return null
  const backend = shot.video_generation_options?.backend || parameters.video_generation_backend || 'api'
  // Planning preference is not a hard generation cap for personal APIs/workflows.
  if (backend !== 'cloud_h3') return null
  const maximum = 14
  const requested = Math.max(4, Math.ceil(duration))
  if (requested <= maximum) return null
  const seconds = duration.toFixed(3).replace(/\.?0+$/, '')
  return {backend, duration, requested, maximum, seconds,
    label: `需拆分 · ${seconds} 秒`,
    detail: `请求 ${requested} 秒，超过${backend === 'cloud_h3' ? '集群' : '当前通道'} ${maximum} 秒上限`}
}

export function projectDurationIssues(shots=[], parameters={}) {
  return shots.flatMap((shot,index) => {
    const issue = shotDurationIssue(shot, parameters)
    return issue ? [{shot,index,...issue}] : []
  })
}
