// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Zhou Ruoyu and He Yun

const API_BASE = ''

function readableError(value, fallback = '') {
  if (typeof value === 'string') return value.trim() || fallback
  if (Array.isArray(value)) {
    const messages = value.map((item) => readableError(item)).filter(Boolean)
    return messages.join('；') || fallback
  }
  if (value && typeof value === 'object') {
    const nested = value.detail ?? value.message ?? value.error
    if (nested !== undefined && nested !== value) return readableError(nested, fallback)
    const location = Array.isArray(value.loc)
      ? value.loc.filter((part) => part !== 'body').join('.')
      : ''
    const message = readableError(value.msg || value.code || '', fallback)
    if (location && message) return `${location}：${message}`
    if (message) return message
    try {
      return JSON.stringify(value)
    } catch {
      return fallback
    }
  }
  return value == null ? fallback : String(value)
}

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
      message = readableError(payload.detail ?? payload.message ?? payload, raw)
    } catch {
      message = raw
    }
    const error = new Error(readableError(message, `Request failed with ${response.status}`))
    error.status = response.status
    throw error
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
      message = readableError(payload.detail ?? payload.message ?? payload, raw)
    } catch {
      // The backend can return a plain text error for startup failures.
    }
    throw new Error(readableError(message, `Request failed with ${response.status}`))
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
  cloudSession: () => requestJSON('/api/cloud/session'),
  cloudRegister: (payload) => requestJSON('/api/cloud/auth/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  cloudLogin: (payload) => requestJSON('/api/cloud/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  cloudLogout: () => requestJSON('/api/cloud/auth/logout', { method: 'POST' }),
  cloudAccount: () => requestJSON('/api/cloud/account'),
  cloudVoices: (type = 'all') => requestJSON(`/api/cloud/voices?type=${encodeURIComponent(type)}`),
  cloudVoiceAudioUrl: (id) => `/api/cloud/voices/${encodeURIComponent(id)}/audio`,
  uploadCloudVoice: (file, displayName, idempotencyKey) => {
    const data = new FormData()
    data.append('file', file)
    data.append('display_name', displayName)
    return requestJSON('/api/cloud/voices', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: data,
    })
  },
  deleteCloudVoice: (id) => requestJSON(`/api/cloud/voices/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  cloudQuote: (payload) => requestJSON('/api/cloud/quote', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  cloudJobs: (page = 1, pageSize = 20) => requestJSON(`/api/cloud/jobs?page=${page}&page_size=${pageSize}`),
  cloudWalletLedger: (page = 1, pageSize = 20) => requestJSON(`/api/cloud/wallet/ledger?page=${page}&page_size=${pageSize}`),
  cloudRechargeProducts: () => requestJSON('/api/cloud/recharge/products'),
  createCloudRechargeOrder: (payload, idempotencyKey) => requestJSON('/api/cloud/recharge/orders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(payload),
  }),
  cloudRechargeOrder: (id) => requestJSON(`/api/cloud/recharge/orders/${encodeURIComponent(id)}`),
  startTts: () => requestJSON('/api/tts/start', { method: 'POST' }),
  settings: () => requestJSON('/api/settings'),
  apiKeySettings: () => requestJSON('/api/api-keys'),
  deleteApiKey: (kind, index, provider = '') => requestJSON(`/api/api-keys/${encodeURIComponent(kind)}/${index}${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`, { method: 'DELETE' }),
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
  regenerateTtsSegments: (id, indices, settings = {}) => requestJSON(`/api/jobs/${id}/tts-editor/regenerate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ indices, ...settings }),
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
