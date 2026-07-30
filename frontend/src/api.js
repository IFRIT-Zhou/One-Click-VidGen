const API_BASE = ''

export async function requestJSON(url, options = {}) {
  const response = await fetch(`${API_BASE}${url}`, {
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  })
  if (!response.ok) {
    const raw = await response.text()
    let message = raw
    try {
      const payload = JSON.parse(raw)
      message = payload.detail || payload.message || raw
    } catch {
      message = raw
    }
    throw new Error(message || `Request failed with ${response.status}`)
  }
  return response.json()
}

async function downloadFile(url) {
  const response = await fetch(`${API_BASE}${url}`, { credentials: 'include' })
  if (!response.ok) {
    const raw = await response.text()
    let message = raw
    try {
      const payload = JSON.parse(raw)
      message = payload.detail || payload.message || raw
    } catch {
      // The backend can return a plain text error for startup failures.
    }
    throw new Error(message || `Request failed with ${response.status}`)
  }
  const disposition = response.headers.get('content-disposition') || ''
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const plainName = disposition.match(/filename="?([^";]+)"?/i)?.[1]
  return {
    blob: await response.blob(),
    filename: decodeURIComponent(encodedName || plainName || '问题诊断包.zip'),
  }
}

export const api = {
  health: () => requestJSON('/api/health'),
  session: () => requestJSON('/api/session'),
  login: (payload) => requestJSON('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),
  register: (payload) => requestJSON('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),
  logout: () => requestJSON('/api/auth/logout', { method: 'POST' }),
  startTts: () => requestJSON('/api/tts/start', { method: 'POST' }),
  settings: () => requestJSON('/api/settings'),
  apiKeySettings: () => requestJSON('/api/api-keys'),
  saveApiKeySettings: (payload) => requestJSON('/api/api-keys', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  script: (name) => requestJSON(`/api/scripts/${encodeURIComponent(name)}`),
  parameterPresets: () => requestJSON('/api/parameter-presets'),
  parameterPreset: (name) => requestJSON(`/api/parameter-presets/${encodeURIComponent(name)}`),
  deleteParameterPreset: (name) => requestJSON(`/api/parameter-presets/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  }),
  saveParameterPreset: (payload) => requestJSON('/api/parameter-presets', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  agentPromptPresets: () => requestJSON('/api/agent-prompt-presets'),
  agentPromptPreset: (name) => requestJSON(`/api/agent-prompt-presets/${encodeURIComponent(name)}`),
  saveAgentPromptPreset: (payload) => requestJSON('/api/agent-prompt-presets', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  createJob: (payload) => requestJSON('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),
  preflightJob: (payload) => requestJSON('/api/jobs/preflight', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),
  jobs: (page = 1, pageSize = 5) => requestJSON(
    `/api/jobs?page=${encodeURIComponent(page)}&page_size=${encodeURIComponent(pageSize)}`,
  ),
  job: (id) => requestJSON(`/api/jobs/${id}`),
  deleteJob: (id) => requestJSON(`/api/jobs/${id}`, { method: 'DELETE' }),
  cancelJob: (id) => requestJSON(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  resumeJob: (id) => requestJSON(`/api/jobs/${id}/resume`, { method: 'POST' }),
  retryJobTts: (id) => requestJSON(`/api/jobs/${id}/retry-tts`, { method: 'POST' }),
  downloadDiagnosticPackage: (id) => downloadFile(`/api/jobs/${id}/diagnostic-package`),
  openJobOutputFolder: (id) => requestJSON(`/api/jobs/${id}/output-folder`, { method: 'POST' }),
  subtitleFonts: () => requestJSON('/api/subtitle-fonts'),
  renderSubtitleVideo: (id, payload) => requestJSON(`/api/jobs/${id}/subtitle-render`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  openStepModeVisualPreviewFolder: (id) => requestJSON(`/api/jobs/${id}/step-mode/visual-preview-folder`, { method: 'POST' }),
  visualEditor: (id) => requestJSON(`/api/jobs/${id}/visual-editor`),
  visualEditorStatus: (id) => requestJSON(`/api/jobs/${id}/visual-editor/status`),
  visualEditorProjects: () => requestJSON('/api/visual-editor/projects'),
  redrawVisualImage: (id, imageId, prompt, referenceMacroIds = [], referenceUploadIds = []) => requestJSON(`/api/jobs/${id}/visual-editor/${encodeURIComponent(imageId)}/redraw`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt, reference_macro_ids: referenceMacroIds, reference_upload_ids: referenceUploadIds }),
  }),
  uploadVisualImage: (id, imageId, file) => {
    const data = new FormData()
    data.append('file', file)
    return requestJSON(`/api/jobs/${id}/visual-editor/${encodeURIComponent(imageId)}/upload`, { method: 'POST', body: data })
  },
  undoVisualImage: (id, imageId) => requestJSON(`/api/jobs/${id}/visual-editor/${encodeURIComponent(imageId)}/undo`, { method: 'POST' }),
  resetVisualPrompt: (id, imageId) => requestJSON(`/api/jobs/${id}/visual-editor/${encodeURIComponent(imageId)}/reset-prompt`, { method: 'POST' }),
  commitVisualBaseline: (id, imageId, prompt) => requestJSON(`/api/jobs/${id}/visual-editor/${encodeURIComponent(imageId)}/commit-baseline`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt }),
  }),
  commitAllVisualBaselines: (id) => requestJSON(`/api/jobs/${id}/visual-editor/commit-all-baselines`, { method: 'POST' }),
  adjustVisualTiming: (id, imageId, action) => requestJSON(`/api/jobs/${id}/visual-editor/${encodeURIComponent(imageId)}/timing`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action }),
  }),
  resetVisualTiming: (id) => requestJSON(`/api/jobs/${id}/visual-editor/timing/reset`, { method: 'POST' }),
  commitVisualTiming: (id) => requestJSON(`/api/jobs/${id}/visual-editor/timing/commit`, { method: 'POST' }),
  restoreVisualTimingHistory: (id, historyId) => requestJSON(`/api/jobs/${id}/visual-editor/timing/history`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ history_id: historyId }),
  }),
  removeVisualTimingPicture: (id, imageId) => requestJSON(`/api/jobs/${id}/visual-editor/${encodeURIComponent(imageId)}/timing/remove`, { method: 'POST' }),
  ttsEditor: (id) => requestJSON(`/api/jobs/${id}/tts-editor`),
  ttsEditorStatus: (id) => requestJSON(`/api/jobs/${id}/tts-editor/status`),
  regenerateTtsSegments: (id, indices) => requestJSON(`/api/jobs/${id}/tts-editor/regenerate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ indices }),
  }),
  renderVisualEditor: (id, payload) => requestJSON(`/api/jobs/${id}/visual-editor/render`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(typeof payload === 'string' ? { mode: payload } : payload),
  }),
  cancelVisualRender: (id) => requestJSON(`/api/jobs/${id}/visual-editor/cancel`, { method: 'POST' }),
  openArtifactFolder: (artifactUrl) => requestJSON(`${artifactUrl}/open-folder`, { method: 'POST' }),
  editorUploads: () => requestJSON('/api/editor/uploads'),
  uploadEditorAsset: (file) => {
    const data = new FormData()
    data.append('file', file)
    return requestJSON('/api/editor/uploads', {
      method: 'POST',
      body: data,
    })
  },
  createEditorJob: (payload) => requestJSON('/api/editor/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),
  editorJobs: () => requestJSON('/api/editor/jobs'),
  editorJob: (id) => requestJSON(`/api/editor/jobs/${id}`),
}
