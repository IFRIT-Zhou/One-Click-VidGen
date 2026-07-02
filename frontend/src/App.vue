<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <div class="brand">
        <div class="brand-mark">VV</div>
        <div>
          <div class="brand-name">口播视频生成台</div>
          <div class="brand-sub">文案到 PPT 翻页视频</div>
        </div>
      </div>

      <div class="sidebar-card auth-card">
        <template v-if="session.user">
          <div class="sidebar-label">账号</div>
          <div class="sidebar-value">{{ session.user.name }}</div>
          <div class="muted small">{{ session.user.email }}</div>
          <button class="ghost-btn full-btn" type="button" @click="logout">退出登录</button>
        </template>
        <template v-else>
          <div class="sidebar-label">账号登录</div>
          <div v-if="authError" class="board-error">{{ authError }}</div>
          <form class="account-form" @submit.prevent="login">
            <label>
              <span>邮箱</span>
              <input v-model="loginForm.email" type="email" autocomplete="email" required />
            </label>
            <label>
              <span>密码</span>
              <input v-model="loginForm.password" type="password" autocomplete="current-password" required />
            </label>
            <button class="primary-btn full-btn" type="submit">登录</button>
          </form>
          <form class="account-form register-form" @submit.prevent="register">
            <div class="sidebar-label">注册</div>
            <label>
              <span>昵称</span>
              <input v-model="registerForm.name" autocomplete="nickname" />
            </label>
            <label>
              <span>邮箱</span>
              <input v-model="registerForm.email" type="email" autocomplete="email" required />
            </label>
            <label>
              <span>密码</span>
              <input v-model="registerForm.password" type="password" minlength="8" autocomplete="new-password" required />
            </label>
            <button class="ghost-btn full-btn" type="submit">注册并登录</button>
          </form>
        </template>
      </div>

      <div class="sidebar-card">
        <div class="sidebar-label">TTS 引擎</div>
        <div class="sidebar-value">{{ ttsStatusText }}</div>
        <div class="muted small">{{ health.tts_provider || 'TTS' }}</div>
        <div class="muted small">当前 voice_id: {{ form.tts_voice_id }}</div>
        <button class="ghost-btn full-btn" type="button" :disabled="startingTts || health.tts_online || !session.user" @click="startTts">
          {{ startingTts ? '检测中...' : '重新检测' }}
        </button>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <button class="icon-btn" type="button" @click="sidebarOpen = !sidebarOpen" aria-label="切换侧边栏">
          <span></span><span></span><span></span>
        </button>
        <div class="topbar-copy">
          <div class="eyebrow">工作台</div>
          <div class="topbar-title">一键生成口播翻页视频</div>
        </div>
        <div class="topbar-actions"></div>
      </header>

      <section class="content stack">
        <section id="create">
          <article class="panel hero-panel">
            <div class="panel-head">
              <div>
                <div class="eyebrow">输入</div>
              </div>
              <span class="status-chip" :class="health.tts_online ? 'success' : 'warning'">
                {{ health.tts_online ? 'TTS 就绪' : '远程 TTS 未连接' }}
              </span>
            </div>

            <div class="form-grid">
              <label>
                <span>加载服务器文案</span>
                <select v-model="selectedScript" @change="loadSelectedScript">
                  <option value="">不加载</option>
                  <option v-for="item in settings.scripts" :key="item.path" :value="item.path">{{ item.name }}</option>
                </select>
              </label>
              <div class="script-upload-field">
                <span>上传本地文案</span>
                <label class="script-file-picker">
                  <input
                    type="file"
                    accept=".txt,.md,text/plain,text/markdown"
                    @change="uploadLocalScript"
                  />
                  <span>浏览文件</span>
                  <strong>{{ scriptUploadName || '选择 TXT 或 Markdown 文案' }}</strong>
                </label>
                <small v-if="scriptUploadError" class="script-upload-error">
                  {{ scriptUploadError }}
                </small>
                <small v-else-if="scriptUploadName" class="muted">
                  已载入 {{ form.script.length }} 个字符，可继续编辑后生成。
                </small>
              </div>
            </div>

            <div class="tts-parameter-panel">
              <div class="tts-parameter-head">
                <div>
                  <div class="sidebar-label">RunningHub MiniMax</div>
                  <h3>语音参数</h3>
                </div>
                <span class="muted small">{{ settings.tts?.model || 'minimax/speech-2.8-hd' }}</span>
              </div>
              <div class="form-grid tts-param-grid">
                <label>
                  <span>系统音色</span>
                  <select v-model="form.tts_voice_id">
                    <option v-for="voice in settings.tts?.voices || []" :key="voice" :value="voice">
                      {{ voiceLabel(voice) }}
                    </option>
                  </select>
                </label>
                <label>
                  <span>情绪</span>
                  <select v-model="form.tts_emotion">
                    <option value="">模型默认</option>
                    <option v-for="emotion in settings.tts?.emotions || []" :key="emotion" :value="emotion">
                      {{ emotionLabel(emotion) }}
                    </option>
                  </select>
                </label>
                <label>
                  <span>语速（0.5–2）</span>
                  <input v-model.number="form.tts_speed" type="number" min="0.5" max="2" step="0.01" />
                </label>
                <label>
                  <span>音量（0.1–10）</span>
                  <input v-model.number="form.tts_volume" type="number" min="0.1" max="10" step="0.01" />
                </label>
                <label>
                  <span>音调（-12–12）</span>
                  <input v-model.number="form.tts_pitch" type="number" min="-12" max="12" step="1" />
                </label>
                <label class="tts-check-row">
                  <input v-model="form.tts_english_normalization" type="checkbox" />
                  <span>启用英文文本规范化</span>
                </label>
                <label class="tts-wide-field">
                  <span>发音词典（可选，最多一条）</span>
                  <input
                    v-model.trim="form.tts_pronunciation"
                    type="text"
                    maxlength="200"
                    placeholder="例如：ASAP/As soon as possible"
                  />
                </label>
              </div>
            </div>

            <label class="stack">
              <span>口播文案</span>
              <textarea v-model="form.script" rows="14" placeholder="粘贴完整文案，系统会自动断句、配音、生成字幕和视频页面。"></textarea>
            </label>

            <div class="inline-actions">
              <button
                class="ghost-btn stop-btn"
                type="button"
                :disabled="!canCancelGeneration || cancellingGeneration"
                @click="cancelGeneration"
              >
                {{ cancellingGeneration ? '正在停止...' : '停止生成' }}
              </button>
              <button class="primary-btn" type="button" :disabled="submitting || !form.script.trim() || !session.user" @click="submit">
                {{ submitButtonText }}
              </button>
            </div>
          </article>
        </section>

        <section id="jobs" class="panel progress-panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">任务</div>
              <h2>{{ activeJob?.message || '等待任务' }}</h2>
            </div>
            <span class="progress-percent">{{ activeJob?.progress || 0 }}%</span>
          </div>
          <div class="progress-track" role="progressbar" :aria-valuenow="activeJob?.progress || 0" aria-valuemin="0" aria-valuemax="100">
            <span :style="{ width: `${activeJob?.progress || 0}%` }"></span>
          </div>
          <div class="progress-steps">
            <div v-for="step in steps" :key="step.key" class="progress-step" :class="stepClass(step.key)">
              <span></span>
              <div>{{ step.label }}</div>
            </div>
          </div>
          <pre class="log-view">{{ logText }}</pre>
        </section>

        <section id="outputs" class="grid-2">
          <article class="panel">
            <div class="panel-head">
              <div>
                <div class="eyebrow">产物</div>
                <h2>最终视频</h2>
              </div>
              <span
                class="status-chip"
                :class="activeJob?.status === 'completed'
                  ? 'success'
                  : ['failed', 'cancelled'].includes(activeJob?.status)
                    ? 'danger'
                    : 'warning'"
              >
                {{ statusLabel(activeJob?.status) }}
              </span>
            </div>
            <video
              v-if="activeJob?.artifacts?.video_with_subtitles"
              class="project-video"
              controls
              :src="activeJob.artifacts.video_with_subtitles"
            ></video>
            <div v-else class="empty-state">生成完成后，字幕版视频会显示在这里。</div>
            <div v-if="activeJob?.artifacts" class="artifact-grid">
              <a v-for="(url, key) in activeJob.artifacts" :key="key" class="artifact-card" :href="url" target="_blank" rel="noreferrer">
                <div class="artifact-label">{{ artifactLabel(key) }}</div>
                <div class="artifact-value">{{ url.split('/').pop() }}</div>
              </a>
            </div>
          </article>

          <article class="panel">
            <div class="panel-head">
              <div>
                <div class="eyebrow">最近</div>
                <h2>任务列表</h2>
              </div>
            </div>
            <div class="board-list">
              <button
                v-for="job in jobs"
                :key="job.id"
                type="button"
                class="project-card"
                :class="{ active: activeJob?.id === job.id }"
                @click="selectJob(job.id)"
              >
                <div class="project-top">
                  <span class="status-chip" :class="statusClass(job.status)">{{ statusLabel(job.status) }}</span>
                  <span class="muted small">{{ job.progress }}%</span>
                </div>
                <h3>{{ job.id }}</h3>
                <p>{{ job.message }}</p>
              </button>
              <div v-if="!jobs.length" class="empty-state">暂无任务。</div>
            </div>
            <div v-if="jobTotal > 0" class="task-pagination" aria-label="任务列表分页">
              <button
                class="ghost-btn pagination-btn"
                type="button"
                :disabled="jobPage <= 1"
                @click="changeJobPage(jobPage - 1)"
              >
                上一页
              </button>
              <span class="muted small">
                第 {{ jobPage }} / {{ jobTotalPages }} 页 · 共 {{ jobTotal }} 条
              </span>
              <button
                class="ghost-btn pagination-btn"
                type="button"
                :disabled="jobPage >= jobTotalPages"
                @click="changeJobPage(jobPage + 1)"
              >
                下一页
              </button>
            </div>
          </article>
        </section>

        <section id="editor" class="panel editor-panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">剪辑</div>
              <h2>视频 / 音频 / 字幕剪辑</h2>
            </div>
            <span class="status-chip" :class="editorJob?.status === 'completed' ? 'success' : 'warning'">
              {{ statusLabel(editorJob?.status) }}
            </span>
          </div>

          <div class="editor-grid">
            <div class="editor-column">
              <div class="tool-section">
                <div class="sidebar-label">素材</div>
                <div class="upload-row">
                  <label class="file-picker">
                    <span>上传视频</span>
                    <input type="file" accept="video/*" @change="uploadAsset($event)" />
                  </label>
                  <label class="file-picker">
                    <span>上传音频</span>
                    <input type="file" accept="audio/*" @change="uploadAsset($event)" />
                  </label>
                  <label class="file-picker">
                    <span>上传字幕</span>
                    <input type="file" accept=".srt,.ass,.vtt" @change="uploadAsset($event)" />
                  </label>
                </div>
                <div v-if="uploading" class="muted small">素材上传中...</div>
              </div>

              <div class="tool-section">
                <div class="sidebar-label">轨道选择</div>
                <label>
                  <span>主视频</span>
                  <select v-model="editorForm.video_id">
                    <option value="">请选择视频</option>
                    <option v-for="asset in videoAssets" :key="asset.id" :value="asset.id">{{ asset.name }}</option>
                  </select>
                </label>
                <label>
                  <span>配乐 / 音频</span>
                  <select v-model="editorForm.audio_id">
                    <option value="">不添加配乐</option>
                    <option v-for="asset in audioAssets" :key="asset.id" :value="asset.id">{{ asset.name }}</option>
                  </select>
                </label>
                <label>
                  <span>字幕文件</span>
                  <select v-model="editorForm.subtitle_id">
                    <option value="">不添加字幕</option>
                    <option v-for="asset in subtitleAssets" :key="asset.id" :value="asset.id">{{ asset.name }}</option>
                  </select>
                </label>
              </div>

              <div class="asset-list">
                <article v-for="asset in editorAssets" :key="asset.id" class="asset-chip">
                  <span>{{ kindLabel(asset.kind) }}</span>
                  <strong>{{ asset.name }}</strong>
                </article>
                <div v-if="!editorAssets.length" class="empty-state compact-empty">还没有上传素材。</div>
              </div>
            </div>

            <div class="editor-column">
              <div class="tool-section">
                <div class="sidebar-label">剪辑参数</div>
                <div class="form-grid editor-form-grid">
                  <label>
                    <span>开始秒</span>
                    <input v-model.number="editorForm.trim_start" type="number" min="0" step="0.1" />
                  </label>
                  <label>
                    <span>结束秒</span>
                    <input v-model.number="editorForm.trim_end" type="number" min="0" step="0.1" />
                  </label>
                  <label>
                    <span>原声音量</span>
                    <input v-model.number="editorForm.video_volume" type="number" min="0" max="3" step="0.1" />
                  </label>
                  <label>
                    <span>配乐音量</span>
                    <input v-model.number="editorForm.audio_volume" type="number" min="0" max="3" step="0.1" />
                  </label>
                  <label>
                    <span>配乐延迟秒</span>
                    <input v-model.number="editorForm.audio_offset" type="number" min="0" step="0.1" />
                  </label>
                  <label class="check-row">
                    <input v-model="editorForm.burn_subtitles" type="checkbox" />
                    <span>烧录字幕</span>
                  </label>
                </div>
                <button class="primary-btn" type="button" :disabled="editing || !session.user || !editorForm.video_id" @click="renderEdit">
                  {{ editing ? '剪辑任务已提交' : '渲染剪辑视频' }}
                </button>
              </div>

              <div class="preview-stack">
                <video
                  v-if="selectedVideoAsset"
                  class="project-video editor-preview"
                  controls
                  :src="selectedVideoAsset.url"
                ></video>
                <video
                  v-if="editorJob?.artifacts?.video"
                  class="project-video editor-preview"
                  controls
                  :src="editorJob.artifacts.video"
                ></video>
                <div v-if="!selectedVideoAsset" class="empty-state compact-empty">选择主视频后可预览。</div>
              </div>
            </div>
          </div>

          <div class="grid-2 editor-bottom">
            <pre class="log-view">{{ editorLogText }}</pre>
            <div class="board-list">
              <button
                v-for="job in editorJobs"
                :key="job.id"
                type="button"
                class="project-card"
                :class="{ active: editorJob?.id === job.id }"
                @click="selectEditorJob(job.id)"
              >
                <div class="project-top">
                  <span class="status-chip" :class="statusClass(job.status)">{{ statusLabel(job.status) }}</span>
                  <span class="muted small">{{ job.progress }}%</span>
                </div>
                <h3>{{ job.id }}</h3>
                <p>{{ job.message }}</p>
              </button>
              <div v-if="!editorJobs.length" class="empty-state compact-empty">暂无剪辑任务。</div>
            </div>
          </div>
        </section>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from './api'

const sidebarOpen = ref(false)
const selectedScript = ref('')
const scriptUploadName = ref('')
const scriptUploadError = ref('')
const submitting = ref(false)
const cancellingGeneration = ref(false)
const health = ref({ ok: false, tts_online: false })
const settings = ref({ scripts: [], tts: { voices: [], emotions: [], defaults: {} } })
const session = ref({ user: null, mysql: {} })
const authError = ref('')
const activeJob = ref(null)
const jobs = ref([])
const jobPage = ref(1)
const jobTotal = ref(0)
const jobTotalPages = ref(1)
const JOB_PAGE_SIZE = 5
const editorAssets = ref([])
const editorJobs = ref([])
const editorJob = ref(null)
const uploading = ref(false)
const editing = ref(false)
const startingTts = ref(false)
const ttsStartMessage = ref('')
let timer = null
const MAX_SCRIPT_FILE_SIZE = 2 * 1024 * 1024

const loginForm = reactive({
  email: '',
  password: '',
})
const registerForm = reactive({
  name: '',
  email: '',
  password: '',
})
const form = reactive({
  script: '',
  tts_voice_id: 'Wise_Woman',
  tts_speed: 1,
  tts_volume: 1,
  tts_pitch: 0,
  tts_emotion: '',
  tts_english_normalization: false,
  tts_pronunciation: '',
  visual_backend: 'poster',
})
const editorForm = reactive({
  video_id: '',
  audio_id: '',
  subtitle_id: '',
  trim_start: 0,
  trim_end: 0,
  video_volume: 1,
  audio_volume: 0.8,
  audio_offset: 0,
  burn_subtitles: true,
})

const steps = [
  { key: 'tts', label: '断句配音' },
  { key: 'scene', label: 'ASR 分镜' },
  { key: 'correct', label: '文本校准' },
  { key: 'semantic', label: '语义剧本' },
  { key: 'visual', label: '在线海报' },
  { key: 'render', label: '视频合成' },
  { key: 'archive', label: '项目归档' },
]

const logText = computed(() => activeJob.value?.logs?.join('\n') || '暂无日志。')
const editorLogText = computed(() => editorJob.value?.logs?.join('\n') || '暂无剪辑日志。')
const videoAssets = computed(() => editorAssets.value.filter((asset) => asset.kind === 'video'))
const audioAssets = computed(() => editorAssets.value.filter((asset) => asset.kind === 'audio'))
const subtitleAssets = computed(() => editorAssets.value.filter((asset) => asset.kind === 'subtitle'))
const selectedVideoAsset = computed(() => videoAssets.value.find((asset) => asset.id === editorForm.video_id))
const submitButtonText = computed(() => {
  if (!session.value.user) return '请先登录'
  if (submitting.value) return '任务已提交'
  return '一键生成视频'
})
const canCancelGeneration = computed(() => (
  session.value.user
  && ['queued', 'running'].includes(activeJob.value?.status)
))
const ttsStatusText = computed(() => {
  if (health.value.tts_online) return '在线'
  if (startingTts.value) return '检测中'
  if (ttsStartMessage.value) return ttsStartMessage.value
  return '未连接'
})

async function refresh() {
  try {
    health.value = await api.health()
  } catch {
    health.value = { ok: false, tts_online: false }
  }
  try {
    session.value = await api.session()
  } catch {
    session.value = { user: null, mysql: {} }
  }
  try {
    if (!session.value.user) {
      jobs.value = []
      jobPage.value = 1
      jobTotal.value = 0
      jobTotalPages.value = 1
      activeJob.value = null
      return
    }
    const payload = await api.jobs(jobPage.value, JOB_PAGE_SIZE)
    jobs.value = payload.jobs || []
    jobPage.value = payload.page || 1
    jobTotal.value = payload.total || 0
    jobTotalPages.value = payload.total_pages || 1
    if (activeJob.value?.id) {
      await selectJob(activeJob.value.id, false)
    } else if (jobs.value.length) {
      activeJob.value = jobs.value[0]
    }
    await refreshEditor()
  } catch {
    jobs.value = []
    jobTotal.value = 0
    jobTotalPages.value = 1
  }
}

async function refreshEditor() {
  if (!session.value.user) {
    editorAssets.value = []
    editorJobs.value = []
    editorJob.value = null
    return
  }
  const [uploadsPayload, jobsPayload] = await Promise.all([api.editorUploads(), api.editorJobs()])
  editorAssets.value = uploadsPayload.assets || []
  editorJobs.value = jobsPayload.jobs || []
  if (!editorForm.video_id && videoAssets.value[0]) editorForm.video_id = videoAssets.value[0].id
  if (editorJob.value?.id) {
    await selectEditorJob(editorJob.value.id, false)
  } else if (editorJobs.value.length) {
    editorJob.value = editorJobs.value[0]
  }
}

async function login() {
  authError.value = ''
  try {
    session.value = await api.login({ ...loginForm })
    loginForm.password = ''
    await refresh()
  } catch (error) {
    authError.value = error.message || '登录失败'
  }
}

async function register() {
  authError.value = ''
  try {
    session.value = await api.register({ ...registerForm })
    registerForm.password = ''
    await refresh()
  } catch (error) {
    authError.value = error.message || '注册失败'
  }
}

async function logout() {
  await api.logout()
  session.value = { user: null, mysql: {} }
  jobs.value = []
  jobPage.value = 1
  jobTotal.value = 0
  jobTotalPages.value = 1
  activeJob.value = null
  editorAssets.value = []
  editorJobs.value = []
  editorJob.value = null
}

async function startTts() {
  if (!session.value.user || health.value.tts_online) return
  startingTts.value = true
  ttsStartMessage.value = ''
  try {
    const payload = await api.startTts()
    ttsStartMessage.value = payload.message || '已发送启动指令'
    await refresh()
  } catch (error) {
    ttsStartMessage.value = error.message || '启动失败'
  } finally {
    startingTts.value = false
  }
}

async function loadSettings() {
  settings.value = await api.settings()
  const defaults = settings.value.tts?.defaults || {}
  form.tts_voice_id = defaults.voice_id || 'Wise_Woman'
  form.tts_speed = defaults.speed ?? 1
  form.tts_volume = defaults.volume ?? 1
  form.tts_pitch = defaults.pitch ?? 0
  form.tts_emotion = defaults.emotion || ''
  form.tts_english_normalization = defaults.english_normalization ?? false
  form.tts_pronunciation = defaults.pronunciation || ''
}

async function loadSelectedScript() {
  if (!selectedScript.value) return
  const payload = await api.script(selectedScript.value)
  form.script = payload.content
  scriptUploadName.value = ''
  scriptUploadError.value = ''
}

async function uploadLocalScript(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  scriptUploadError.value = ''
  if (!file) return

  const suffix = file.name.split('.').pop()?.toLowerCase()
  if (!['txt', 'md'].includes(suffix)) {
    scriptUploadName.value = ''
    scriptUploadError.value = '仅支持 .txt 或 .md 文案文件。'
    return
  }
  if (file.size > MAX_SCRIPT_FILE_SIZE) {
    scriptUploadName.value = ''
    scriptUploadError.value = '文案文件不能超过 2 MB。'
    return
  }

  try {
    const buffer = await file.arrayBuffer()
    let content
    try {
      content = new TextDecoder('utf-8', { fatal: true }).decode(buffer)
    } catch {
      content = new TextDecoder('gb18030', { fatal: true }).decode(buffer)
    }
    content = content.replace(/^\uFEFF/, '')
    if (!content.trim()) {
      throw new Error('文案文件内容为空。')
    }
    form.script = content
    selectedScript.value = ''
    scriptUploadName.value = file.name
  } catch (error) {
    scriptUploadName.value = ''
    scriptUploadError.value = error.message || '读取文案失败，请检查文件编码。'
  }
}

async function submit() {
  if (!session.value.user) {
    authError.value = '请先登录后再生成视频'
    return
  }
  submitting.value = true
  try {
    activeJob.value = await api.createJob({
      ...form,
      tts_emotion: form.tts_emotion || null,
      tts_pronunciation: form.tts_pronunciation || null,
    })
    jobPage.value = 1
    await refresh()
  } finally {
    submitting.value = false
  }
}

async function selectJob(id, replace = true) {
  const payload = await api.job(id)
  if (replace) activeJob.value = payload
  else activeJob.value = payload
}

async function cancelGeneration() {
  if (!canCancelGeneration.value || !activeJob.value?.id) return
  cancellingGeneration.value = true
  try {
    activeJob.value = await api.cancelJob(activeJob.value.id)
    await refresh()
  } finally {
    cancellingGeneration.value = false
  }
}

async function changeJobPage(page) {
  const target = Math.min(Math.max(1, page), jobTotalPages.value)
  if (target === jobPage.value) return
  jobPage.value = target
  const payload = await api.jobs(jobPage.value, JOB_PAGE_SIZE)
  jobs.value = payload.jobs || []
  jobPage.value = payload.page || 1
  jobTotal.value = payload.total || 0
  jobTotalPages.value = payload.total_pages || 1
  activeJob.value = jobs.value[0] || null
}

async function uploadAsset(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || !session.value.user) return
  uploading.value = true
  try {
    const payload = await api.uploadEditorAsset(file)
    await refreshEditor()
    const asset = payload.asset
    if (asset?.kind === 'video') editorForm.video_id = asset.id
    if (asset?.kind === 'audio') editorForm.audio_id = asset.id
    if (asset?.kind === 'subtitle') editorForm.subtitle_id = asset.id
  } finally {
    uploading.value = false
  }
}

async function renderEdit() {
  if (!session.value.user || !editorForm.video_id) return
  editing.value = true
  try {
    editorJob.value = await api.createEditorJob({ ...editorForm })
    await refreshEditor()
  } finally {
    editing.value = false
  }
}

async function selectEditorJob(id, replace = true) {
  const payload = await api.editorJob(id)
  if (replace) editorJob.value = payload
  else editorJob.value = payload
}

function stepClass(key) {
  const order = steps.map((item) => item.key)
  const current = activeJob.value?.step
  if (activeJob.value?.status === 'completed') return 'done'
  if (current === key) return 'active'
  if (order.indexOf(key) < order.indexOf(current)) return 'done'
  return ''
}

function statusLabel(status) {
  return {
    queued: '排队中',
    running: '生成中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已停止',
  }[status] || '未开始'
}

function statusClass(status) {
  return {
    completed: 'success',
    failed: 'danger',
    cancelled: 'danger',
    running: 'warning',
  }[status] || ''
}

function voiceLabel(voice) {
  return {
    Wise_Woman: '智慧女声 · Wise_Woman',
    Friendly_Person: '亲切讲述 · Friendly_Person',
    Inspirational_girl: '励志少女 · Inspirational_girl',
    Deep_Voice_Man: '低沉男声 · Deep_Voice_Man',
    Calm_Woman: '沉静女声 · Calm_Woman',
    Casual_Guy: '随和男声 · Casual_Guy',
    Lively_Girl: '活泼少女 · Lively_Girl',
    Patient_Man: '耐心男声 · Patient_Man',
    Young_Knight: '青年骑士 · Young_Knight',
    Determined_Man: '坚定男声 · Determined_Man',
    Lovely_Girl: '甜美女声 · Lovely_Girl',
    Decent_Boy: '端正少年 · Decent_Boy',
    Imposing_Manner: '威严气场 · Imposing_Manner',
    Elegant_Man: '优雅男声 · Elegant_Man',
    Abbess: '师太音色 · Abbess',
    Sweet_Girl_2: '甜美女声 2 · Sweet_Girl_2',
    Exuberant_Girl: '热情少女 · Exuberant_Girl',
  }[voice] || voice
}

function emotionLabel(emotion) {
  return {
    happy: '开心',
    sad: '悲伤',
    angry: '愤怒',
    fearful: '恐惧',
    disgusted: '厌恶',
    surprised: '惊讶',
    neutral: '中性',
  }[emotion] || emotion
}

function artifactLabel(key) {
  return {
    video_with_subtitles: '字幕版视频',
    video_raw: '纯净版视频',
    audio: '配音音频',
    subtitle: '短字幕',
    scene_timeline: '分镜 JSON',
    fine_grained_timeline: '语义剧本',
    html: 'HTML 模板',
    archive_manifest: '归档清单',
  }[key] || key
}

function kindLabel(kind) {
  return {
    video: '视频',
    audio: '音频',
    subtitle: '字幕',
  }[kind] || '文件'
}

onMounted(async () => {
  await loadSettings()
  await refresh()
  timer = window.setInterval(refresh, 2500)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>
