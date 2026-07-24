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
  jobs: (page = 1, pageSize = 5) => requestJSON(
    `/api/jobs?page=${encodeURIComponent(page)}&page_size=${encodeURIComponent(pageSize)}`,
  ),
  job: (id) => requestJSON(`/api/jobs/${id}`),
  cancelJob: (id) => requestJSON(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  resumeJob: (id) => requestJSON(`/api/jobs/${id}/resume`, { method: 'POST' }),
  openJobOutputFolder: (id) => requestJSON(`/api/jobs/${id}/output-folder`, { method: 'POST' }),
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
  renderVisualEditor: (id, mode) => requestJSON(`/api/jobs/${id}/visual-editor/render`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }),
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
