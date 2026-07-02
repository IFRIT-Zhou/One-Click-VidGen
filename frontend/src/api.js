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
  script: (name) => requestJSON(`/api/scripts/${encodeURIComponent(name)}`),
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
