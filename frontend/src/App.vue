<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <div class="brand">
        <div class="brand-mark">AI</div>
        <div>
          <div class="brand-name">AI 故事视频</div>
          <div class="brand-sub">文案一键转惊悚漫画视频</div>
        </div>
      </div>

      <div class="sidebar-card auth-card">
        <template v-if="session.auth_mode === 'local'">
          <div class="sidebar-label">本地模式</div>
          <div class="sidebar-value">免登录工作台</div>
          <div class="muted small">任务和素材保存在这台电脑。</div>
        </template>
        <template v-else-if="session.user">
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
        <div class="muted small">当前音色: {{ form.tts_voice_id.startsWith('upload:') ? (ttsVoiceUploadName || '本地上传音色') : voiceLabel(form.tts_voice_id) }}</div>
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
          <div class="topbar-title">{{ activePage === 'workspace' ? '故事视频生成工作台' : activePage === 'development' ? '待开发功能' : '模块 1 · 仅配音' }}</div>
        </div>
        <div class="topbar-actions">
          <span class="status-chip" :class="health.tts_online ? 'success' : 'warning'">
            {{ health.tts_online ? 'IndexTTS2 就绪' : 'IndexTTS2 未就绪' }}
          </span>
        </div>
      </header>

      <section class="content stack">
        <nav class="page-tabs" aria-label="页面切换">
          <button
            type="button"
            :class="{ active: activePage === 'workspace' }"
            @click="activePage = 'workspace'"
          >
            <span>生成工作台</span>
            <small>当前可用主流程</small>
          </button>
          <button
            type="button"
            :class="{ active: activePage === 'development' }"
            @click="activePage = 'development'"
          >
            <span>待开发</span>
            <small>实验功能与高级设置</small>
          </button>
          <button
            type="button"
            :class="{ active: activePage === 'module1' }"
            @click="activePage = 'module1'"
          >
            <span>模块 1 · 仅配音</span>
            <small>只运行 IndexTTS2</small>
          </button>
        </nav>

        <section v-if="activePage === 'workspace'" class="workspace-page stack">
        <section id="create">
          <article class="panel hero-panel">
            <div class="panel-head">
              <div>
                <div class="eyebrow">新任务</div>
                <h2>创建故事视频</h2>
                <p class="muted create-summary">输入文案，选择声音和画风，其余步骤交给双 Agent 流水线。</p>
              </div>
              <span class="status-chip success">双 Agent 已启用</span>
            </div>

            <div class="form-grid">
              <label class="project-name-field">
                <span>项目名称</span>
                <input
                  v-model.trim="form.project_name"
                  type="text"
                  maxlength="80"
                  placeholder="用于命名 output 中的项目文件夹"
                />
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
              <label class="check-row source-mode-toggle">
                <input v-model="form.skip_tts" type="checkbox" @change="handleSkipTtsChange" />
                <span>已有配音和文案，不需要 IndexTTS2</span>
              </label>
              <div v-if="form.skip_tts" class="source-audio-main">
                <div class="script-upload-field">
                  <span>上传已有配音</span>
                  <label class="script-file-picker">
                    <input
                      type="file"
                      accept="audio/*,.mp3,.wav,.m4a,.aac,.flac,.ogg"
                      @change="uploadSourceAudio"
                    />
                    <span>{{ sourceAudioUploading ? '上传中' : '浏览文件' }}</span>
                    <strong>{{ sourceAudioName || '选择配音音频' }}</strong>
                  </label>
                  <small v-if="sourceAudioError" class="script-upload-error">{{ sourceAudioError }}</small>
                  <small v-else-if="sourceAudioName" class="muted">生成时会跳过 IndexTTS2，从模块 2 开始识别字幕。</small>
                </div>
              </div>
            </div>

            <div v-if="!form.skip_tts" class="tts-parameter-panel">
              <div class="tts-parameter-head">
                <div>
                  <div class="sidebar-label">官方 IndexTTS2 · 本地 GPU</div>
                  <h3>语音参数</h3>
                </div>
                <span class="muted small">{{ settings.tts?.model || 'official IndexTTS2 2.0.0' }}</span>
              </div>
              <div class="form-grid tts-param-grid">
                <div class="script-upload-field tts-voice-upload">
                  <span>上传本地参考音色</span>
                  <label class="script-file-picker">
                    <input type="file" accept=".wav,.mp3,.flac,audio/wav,audio/mpeg,audio/flac" @change="uploadTtsVoice" />
                    <span>{{ ttsVoiceUploading ? '上传中' : '浏览音频' }}</span>
                    <strong>{{ ttsVoiceUploadName || '选择清晰的 WAV / MP3 / FLAC' }}</strong>
                  </label>
                  <small v-if="ttsVoiceUploadError" class="script-upload-error">{{ ttsVoiceUploadError }}</small>
                  <small v-else class="muted">建议使用 10–30 秒、单人、无背景音乐的干净人声。</small>
                </div>
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
                <label>
                  <span>并行数（1–3）</span>
                  <input v-model.number="form.tts_parallelism" type="number" min="1" max="3" step="1" />
                </label>
                <small class="muted tts-wide-field">
                  4090 建议日常用 2；3 是上限尝试档，显存紧张或报错时调回 2。
                </small>
              </div>
            </div>

            <label class="stack">
              <span>{{ form.skip_text_correction ? '口播文案（已选择无文案，可留空）' : '口播文案' }}</span>
              <textarea
                v-model="form.script"
                rows="14"
                :disabled="form.skip_text_correction"
                :placeholder="scriptPlaceholder"
              ></textarea>
            </label>

            <div class="tts-parameter-panel split-panel">
              <div class="tts-parameter-head">
                <div>
                  <div class="sidebar-label">长文处理</div>
                  <h3>自动分段渲染</h3>
                </div>
                <span class="muted small">模块 2.5 后执行</span>
              </div>
              <div class="form-grid split-grid">
                <label class="check-row">
                  <input v-model="form.auto_split_long_text" type="checkbox" />
                  <span>文案过长时自动拆成多段视频</span>
                </label>
                <label>
                  <span>每段最大字数</span>
                  <input
                    v-model.number="form.split_text_threshold"
                    type="number"
                    min="800"
                    max="12000"
                    step="100"
                    :disabled="!form.auto_split_long_text"
                  />
                </label>
              </div>
              <small class="muted">
                系统会先让大模型通读全文，按主题完整性分段；该数值只是上限，不会为了凑字数硬切。分段视频完成后会按顺序自动拼接。
              </small>
            </div>

            <div class="tts-parameter-panel visual-prompt-panel">
              <div class="content-mode-bar">
                <div class="content-mode-copy">
                  <div class="sidebar-label">作品风格</div>
                  <strong>选择内容与画面模式</strong>
                </div>
                <div class="content-mode-options">
                  <button
                    v-for="mode in contentModeOptions"
                    :key="mode.key"
                    type="button"
                    :class="{ active: form.content_mode === mode.key }"
                    @click="setContentMode(mode.key)"
                  >
                    <span>{{ mode.label }}</span>
                    <small>{{ mode.description }}</small>
                  </button>
                </div>
              </div>
              <div class="tts-parameter-head">
                <div>
                  <div class="sidebar-label">模块 4</div>
                  <h3>画面提示词命令</h3>
                </div>
                <button class="ghost-btn compact-btn" type="button" @click="resetSimpleVisualPrompt">
                  恢复默认
                </button>
              </div>
              <label class="stack">
                <span>统一画面风格</span>
                <textarea
                  v-model="form.visual_style_prompt"
                  @focus="setVisualPromptMode('simple')"
                  @input="rememberVisualPrompt"
                  rows="3"
                  maxlength="1000"
                  :placeholder="form.content_mode === 'science_explainer'
                    ? '描述科教漫画画风、红围巾短发少女、信息表达与画面质感。'
                    : '描述惊悚漫画画风、角色一致性、色彩与悬疑氛围。'"
                ></textarea>
              </label>
              <small class="muted">
                双 Agent 会自动补充人物一致性、分镜、审核规避和画质规则；完整指令已收进“待开发”。
              </small>
            </div>

            <div class="inline-actions">
              <button
                class="ghost-btn stop-btn"
                type="button"
                :disabled="!canCancelGeneration || cancellingGeneration"
                @click="cancelGeneration"
              >
                {{ cancellingGeneration ? '正在停止...' : '停止生成' }}
              </button>
              <button
                class="ghost-btn"
                type="button"
                :disabled="!canResumeGeneration || resumingGeneration"
                @click="resumeGeneration"
              >
                {{ resumingGeneration ? '正在续跑...' : '断点续跑' }}
              </button>
              <button class="primary-btn" type="button" :disabled="submitting || !canSubmitGeneration" @click="submit">
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
          <div class="log-toolbar">
            <span class="muted small">后台日志</span>
            <button class="ghost-btn compact-btn" type="button" @click="showFullLogs = !showFullLogs">
              {{ showFullLogs ? '显示重点' : '显示全部' }}
            </button>
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
              <button
                v-for="(url, key) in activeJob.artifacts"
                :key="key"
                class="artifact-card"
                type="button"
                @click="openArtifactFolder(url)"
              >
                <div class="artifact-label">{{ artifactLabel(key) }}</div>
                <div class="artifact-value">{{ url.split('/').pop() }}</div>
                <div class="artifact-action">打开所在文件夹</div>
              </button>
            </div>
            <div v-if="folderOpenMessage" class="folder-open-message">{{ folderOpenMessage }}</div>
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
                <h3>{{ job.request?.project_name || job.id }}</h3>
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

        </section>

        <section v-else-if="activePage === 'development'" class="development-page stack">
          <article class="panel development-hero">
            <div>
              <div class="eyebrow">待开发</div>
              <h2>实验功能与高级设置</h2>
              <p class="muted large">
                这些入口暂不参与默认故事视频流程，功能代码仍然保留。确认稳定后，再逐项移回生成工作台。
              </p>
            </div>
            <span class="status-chip warning">不影响主流程</span>
          </article>

          <div class="development-grid">
            <article class="panel dev-feature-card">
              <div class="panel-head">
                <div>
                  <div class="eyebrow">高级控制</div>
                  <h2>完整 Gemini 画面指令</h2>
                </div>
                <span class="status-chip warning">专家模式</span>
              </div>
              <div class="prompt-mode-switch" role="group" aria-label="提示词编辑模式">
                <button
                  type="button"
                  :class="{ active: form.visual_prompt_mode === 'simple' }"
                  @click="setVisualPromptMode('simple')"
                >
                  使用默认双 Agent
                </button>
                <button
                  type="button"
                  :class="{ active: form.visual_prompt_mode === 'full' }"
                  @click="setVisualPromptMode('full')"
                >
                  使用完整指令
                </button>
              </div>
              <label class="stack">
                <span>完整画面规划系统指令</span>
                <textarea
                  v-model="form.visual_prompt_system"
                  @input="rememberVisualPrompt"
                  rows="14"
                  maxlength="4000"
                  placeholder="留空时使用程序内置的双 Agent 画面规则。"
                ></textarea>
              </label>
              <div class="dev-notice">
                当前状态：{{ form.visual_prompt_mode === 'full' ? '将覆盖内置 Agent 2 画面指令' : '继续使用内置双 Agent 规则' }}
              </div>
            </article>
          </div>

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

        <section v-else class="module1-page stack">
          <article class="panel module1-panel">
            <div class="panel-head">
              <div>
                <div class="eyebrow">独立工具</div>
                <h2>模块 1 · IndexTTS2 配音</h2>
                <p class="muted create-summary">只执行断句、配音和原始字幕，不启动 ASR、双 Agent、出图及视频合成。</p>
              </div>
              <span class="status-chip" :class="health.tts_online ? 'success' : 'warning'">
                {{ health.tts_online ? 'IndexTTS2 就绪' : 'IndexTTS2 未就绪' }}
              </span>
            </div>

            <div class="module1-layout">
              <div class="module1-copy-column">
                <label>
                  <span>配音任务名称</span>
                  <input v-model.trim="form.project_name" type="text" maxlength="80" />
                </label>
                <div class="script-upload-field">
                  <span>上传本地文案</span>
                  <label class="script-file-picker">
                    <input type="file" accept=".txt,.md,text/plain,text/markdown" @change="uploadLocalScript" />
                    <span>浏览文件</span>
                    <strong>{{ scriptUploadName || '选择 TXT 或 Markdown 文案' }}</strong>
                  </label>
                </div>
                <label class="module1-script-field">
                  <span>配音文案</span>
                  <textarea v-model="form.script" rows="18" placeholder="粘贴需要转换成语音的文案。"></textarea>
                </label>
              </div>

              <div class="module1-settings-column">
                <div class="tts-parameter-panel">
                  <div class="tts-parameter-head">
                    <div>
                      <div class="sidebar-label">参考声音</div>
                      <h3>本地参考音色</h3>
                    </div>
                  </div>
                  <div class="script-upload-field">
                    <label class="script-file-picker">
                      <input type="file" accept=".wav,.mp3,.flac,audio/wav,audio/mpeg,audio/flac" @change="uploadTtsVoice" />
                      <span>{{ ttsVoiceUploading ? '上传中' : '浏览音频' }}</span>
                      <strong>{{ ttsVoiceUploadName || '选择 WAV / MP3 / FLAC' }}</strong>
                    </label>
                    <small v-if="ttsVoiceUploadError" class="script-upload-error">{{ ttsVoiceUploadError }}</small>
                    <small v-else class="muted">建议 10–30 秒、单人、无音乐的干净人声。</small>
                  </div>
                </div>

                <div class="tts-parameter-panel">
                  <div class="form-grid module1-param-grid">
                    <label>
                      <span>情绪</span>
                      <select v-model="form.tts_emotion">
                        <option value="">模型默认</option>
                        <option v-for="emotion in settings.tts?.emotions || []" :key="emotion" :value="emotion">{{ emotionLabel(emotion) }}</option>
                      </select>
                    </label>
                    <label><span>语速</span><input v-model.number="form.tts_speed" type="number" min="0.5" max="2" step="0.01" /></label>
                    <label><span>音量</span><input v-model.number="form.tts_volume" type="number" min="0.1" max="10" step="0.01" /></label>
                    <label><span>音调</span><input v-model.number="form.tts_pitch" type="number" min="-12" max="12" step="1" /></label>
                    <label><span>并行数</span><input v-model.number="form.tts_parallelism" type="number" min="1" max="3" step="1" /></label>
                  </div>
                </div>

                <div class="inline-actions module1-actions">
                  <button class="ghost-btn stop-btn" type="button" :disabled="!module1JobRunning" @click="cancelModule1">停止配音</button>
                  <button class="primary-btn" type="button" :disabled="submittingModule1 || !canSubmitModule1" @click="submitModule1">
                    {{ submittingModule1 ? '正在提交...' : '开始配音' }}
                  </button>
                </div>
              </div>
            </div>
          </article>

          <article class="panel progress-panel">
            <div class="panel-head">
              <div>
                <div class="eyebrow">模块 1 任务</div>
                <h2>{{ module1Job?.message || '等待开始配音' }}</h2>
              </div>
              <span class="progress-percent">{{ module1Job?.progress || 0 }}%</span>
            </div>
            <div class="progress-track"><span :style="{ width: `${module1Job?.progress || 0}%` }"></span></div>
            <audio v-if="module1Job?.artifacts?.audio" class="module1-audio-player" controls :src="module1Job.artifacts.audio"></audio>
            <div v-if="module1ArtifactEntries.length" class="artifact-grid module1-artifacts">
              <button v-for="item in module1ArtifactEntries" :key="item.key" class="artifact-card" type="button" @click="openArtifactFolder(item.url)">
                <div class="artifact-label">{{ artifactLabel(item.key) }}</div>
                <div class="artifact-value">{{ item.url.split('/').pop() }}</div>
                <div class="artifact-action">打开所在文件夹</div>
              </button>
            </div>
            <pre class="log-view">{{ module1LogText }}</pre>
          </article>
        </section>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from './api'

const VISUAL_PROMPT_FULL_STORAGE_KEY = 'visual_prompt_system_story_v3'
const VISUAL_PROMPT_STYLE_STORAGE_KEY = 'visual_prompt_style_story_v3'
const VISUAL_PROMPT_MODE_STORAGE_KEY = 'visual_prompt_mode_v2'
const CONTENT_MODE_STORAGE_KEY = 'content_mode_v1'
const FALLBACK_CONTENT_MODES = {
  urban_suspense: {
    label: '都市惊悚',
    description: '人物、线索与悬念连续的阴森漫画故事',
  },
  science_explainer: {
    label: '口播科普',
    description: '红围巾短发少女的清晰科教漫画',
  },
}

function randomProjectName() {
  const now = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  const date = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
  const time = `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  const code = Math.random().toString(36).slice(2, 6).toUpperCase()
  return `项目_${date}_${time}_${code}`
}

const sidebarOpen = ref(false)
const activePage = ref('workspace')
const scriptUploadName = ref('')
const scriptUploadError = ref('')
const sourceAudioName = ref('')
const sourceAudioError = ref('')
const sourceAudioUploading = ref(false)
const ttsVoiceUploadName = ref('')
const ttsVoiceUploadError = ref('')
const ttsVoiceUploading = ref(false)
const folderOpenMessage = ref('')
const submitting = ref(false)
const submittingModule1 = ref(false)
const cancellingGeneration = ref(false)
const resumingGeneration = ref(false)
const health = ref({ ok: false, tts_online: false })
const settings = ref({ scripts: [], tts: { voices: [], emotions: [], defaults: {} } })
const session = ref({ user: null, auth_mode: 'account', mysql: {} })
const authError = ref('')
const activeJob = ref(null)
const module1Job = ref(null)
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
const showFullLogs = ref(false)
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
  project_name: randomProjectName(),
  script: '',
  content_mode: 'urban_suspense',
  tts_voice_id: 'voice_05.wav',
  tts_speed: 1,
  tts_volume: 1,
  tts_pitch: 0,
  tts_parallelism: 2,
  tts_emotion: '',
  tts_english_normalization: false,
  tts_pronunciation: '',
  visual_backend: 'poster',
  visual_prompt_mode: 'simple',
  visual_style_prompt: '',
  visual_prompt_system: '',
  auto_split_long_text: true,
  split_text_threshold: 3000,
  skip_tts: false,
  source_audio_id: '',
  skip_text_correction: false,
})

const contentModeOptions = computed(() => {
  const modes = settings.value.visual_prompt?.modes || FALLBACK_CONTENT_MODES
  return Object.entries(modes).map(([key, value]) => ({ key, ...value }))
})

function contentModeDefaults(mode = form.content_mode) {
  return settings.value.visual_prompt?.modes?.[mode]
    || (mode === 'urban_suspense' ? settings.value.visual_prompt : null)
    || FALLBACK_CONTENT_MODES[mode]
    || FALLBACK_CONTENT_MODES.urban_suspense
}

function modeStorageKey(baseKey, mode = form.content_mode) {
  return `${baseKey}_${mode}`
}
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
  { key: 'semantic', label: 'Agent 1 规划' },
  { key: 'visual', label: 'Agent 2 / 海报' },
  { key: 'render', label: '视频合成' },
  { key: 'archive', label: '项目归档' },
]

const importantLogPattern = /(失败|错误|异常|Traceback|Error|error|HTTP \d+|退出码|找不到|缺少|拒绝|超时|开始:|完成:|配音进度|开始配音|句配音|提交|等待|返图|云端状态|模块|海报|队列|分段|拼接|Streaming frame|Capturing frame|Encoding video|Assembling final video|Render complete|已停止|全部完成|输出:)/
const streamingFramePattern = /Streaming frame \d+\/\d+/

function compactStreamingFrameLogs(logs) {
  const compacted = []
  let latestStreamingLine = ''
  for (const line of logs) {
    if (streamingFramePattern.test(line)) {
      latestStreamingLine = line
      continue
    }
    if (latestStreamingLine) {
      compacted.push(latestStreamingLine)
      latestStreamingLine = ''
    }
    compacted.push(line)
  }
  if (latestStreamingLine) compacted.push(latestStreamingLine)
  return compacted
}

const visibleJobLogs = computed(() => {
  const logs = activeJob.value?.logs || []
  const compacted = compactStreamingFrameLogs(logs)
  if (showFullLogs.value) return compacted
  const filtered = compacted.filter((line) => importantLogPattern.test(line))
  return filtered.length ? filtered : logs.slice(-40)
})
const logText = computed(() => visibleJobLogs.value.join('\n') || '暂无日志。')
const editorLogText = computed(() => editorJob.value?.logs?.join('\n') || '暂无剪辑日志。')
const videoAssets = computed(() => editorAssets.value.filter((asset) => asset.kind === 'video'))
const audioAssets = computed(() => editorAssets.value.filter((asset) => asset.kind === 'audio'))
const subtitleAssets = computed(() => editorAssets.value.filter((asset) => asset.kind === 'subtitle'))
const selectedVideoAsset = computed(() => videoAssets.value.find((asset) => asset.id === editorForm.video_id))
const canSubmitGeneration = computed(() => {
  if (!session.value.user) return false
  if (!form.project_name.trim()) return false
  if (form.skip_tts) {
    if (!form.source_audio_id) return false
    return form.skip_text_correction || form.script.trim().length > 0
  }
  return form.script.trim().length > 0
})
const canSubmitModule1 = computed(() => Boolean(
  session.value.user
  && health.value.tts_online
  && form.project_name.trim()
  && form.script.trim().length >= 5
  && !module1JobRunning.value
))
const module1JobRunning = computed(() => ['queued', 'running'].includes(module1Job.value?.status))
const module1ArtifactEntries = computed(() => Object.entries(module1Job.value?.artifacts || {})
  .filter(([key]) => ['audio', 'module1_subtitle'].includes(key))
  .map(([key, url]) => ({ key, url })))
const module1LogText = computed(() => (module1Job.value?.logs || []).join('\n') || '模块 1 日志会显示在这里。')
const submitButtonText = computed(() => {
  if (!session.value.user) return '请先登录'
  if (submitting.value) return '任务已提交'
  if (form.skip_tts && !form.source_audio_id) return '请先上传配音'
  if (form.skip_tts) return '从已有配音生成视频'
  return '一键生成视频'
})
const scriptPlaceholder = computed(() => {
  if (form.skip_text_correction) return '已选择“没有文案”，系统会用 ASR 识别结果继续生成画面和字幕。'
  if (form.skip_tts) return '粘贴与已有配音对应的文案，系统会跳过配音并进行字幕校对。'
  return '粘贴完整文案，系统会自动断句、配音、生成字幕和视频页面。'
})
const canCancelGeneration = computed(() => (
  session.value.user
  && ['queued', 'running'].includes(activeJob.value?.status)
))
const canResumeGeneration = computed(() => (
  session.value.user
  && ['failed', 'cancelled'].includes(activeJob.value?.status)
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
    session.value = { user: null, auth_mode: 'account', mysql: {} }
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
    if (module1Job.value?.id) {
      module1Job.value = await api.job(module1Job.value.id)
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
  session.value = { user: null, auth_mode: 'account', mysql: {} }
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
  form.tts_voice_id = defaults.voice_id || 'voice_05.wav'
  form.tts_speed = defaults.speed ?? 1
  form.tts_volume = defaults.volume ?? 1
  form.tts_pitch = defaults.pitch ?? 0
  form.tts_parallelism = defaults.parallelism ?? 2
  form.tts_emotion = defaults.emotion || ''
  form.tts_english_normalization = defaults.english_normalization ?? false
  form.tts_pronunciation = defaults.pronunciation || ''
  const availableModes = settings.value.visual_prompt?.modes || FALLBACK_CONTENT_MODES
  const savedContentMode = window.localStorage.getItem(CONTENT_MODE_STORAGE_KEY)
  form.content_mode = Object.prototype.hasOwnProperty.call(availableModes, savedContentMode)
    ? savedContentMode
    : 'urban_suspense'
  const savedMode = window.localStorage.getItem(VISUAL_PROMPT_MODE_STORAGE_KEY)
  form.visual_prompt_mode = savedMode === 'full' ? 'full' : 'simple'
  const modeDefaults = contentModeDefaults()
  form.visual_style_prompt = window.localStorage.getItem(modeStorageKey(VISUAL_PROMPT_STYLE_STORAGE_KEY))
    || modeDefaults.default_style
    || ''
  form.visual_prompt_system = window.localStorage.getItem(modeStorageKey(VISUAL_PROMPT_FULL_STORAGE_KEY))
    || modeDefaults.default_system
    || ''
  rememberVisualPrompt()
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
    scriptUploadName.value = file.name
  } catch (error) {
    scriptUploadName.value = ''
    scriptUploadError.value = error.message || '读取文案失败，请检查文件编码。'
  }
}

function handleSkipTtsChange() {
  if (!form.skip_tts) {
    form.source_audio_id = ''
    form.skip_text_correction = false
    sourceAudioName.value = ''
    sourceAudioError.value = ''
  }
}

function resetVisualPrompt() {
  const modeDefaults = contentModeDefaults()
  if (form.visual_prompt_mode === 'simple') {
    form.visual_style_prompt = modeDefaults.default_style || ''
  } else {
    form.visual_prompt_system = modeDefaults.default_system || ''
  }
  rememberVisualPrompt()
}

function resetSimpleVisualPrompt() {
  form.visual_prompt_mode = 'simple'
  form.visual_style_prompt = contentModeDefaults().default_style || ''
  rememberVisualPrompt()
}

function setContentMode(mode) {
  if (mode === form.content_mode) return
  rememberVisualPrompt()
  form.content_mode = mode
  form.visual_prompt_mode = 'simple'
  const modeDefaults = contentModeDefaults(mode)
  form.visual_style_prompt = window.localStorage.getItem(modeStorageKey(VISUAL_PROMPT_STYLE_STORAGE_KEY, mode))
    || modeDefaults.default_style
    || ''
  form.visual_prompt_system = window.localStorage.getItem(modeStorageKey(VISUAL_PROMPT_FULL_STORAGE_KEY, mode))
    || modeDefaults.default_system
    || ''
  rememberVisualPrompt()
}

function setVisualPromptMode(mode) {
  form.visual_prompt_mode = mode
  rememberVisualPrompt()
}

function rememberVisualPrompt() {
  window.localStorage.setItem(CONTENT_MODE_STORAGE_KEY, form.content_mode)
  window.localStorage.setItem(VISUAL_PROMPT_MODE_STORAGE_KEY, form.visual_prompt_mode)
  window.localStorage.setItem(modeStorageKey(VISUAL_PROMPT_STYLE_STORAGE_KEY), form.visual_style_prompt || '')
  window.localStorage.setItem(modeStorageKey(VISUAL_PROMPT_FULL_STORAGE_KEY), form.visual_prompt_system || '')
}

async function uploadTtsVoice(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  ttsVoiceUploadError.value = ''
  if (!file) return
  const suffix = file.name.split('.').pop()?.toLowerCase()
  if (!['wav', 'mp3', 'flac'].includes(suffix)) {
    ttsVoiceUploadName.value = ''
    ttsVoiceUploadError.value = '参考音色只支持 WAV、MP3 或 FLAC。'
    return
  }
  ttsVoiceUploading.value = true
  try {
    const payload = await api.uploadEditorAsset(file)
    if (payload.asset?.kind !== 'audio') throw new Error('上传文件不是可识别的音频。')
    form.tts_voice_id = `upload:${payload.asset.id}`
    ttsVoiceUploadName.value = payload.asset.name || file.name
  } catch (error) {
    ttsVoiceUploadName.value = ''
    ttsVoiceUploadError.value = error.message || '上传参考音色失败'
  } finally {
    ttsVoiceUploading.value = false
  }
}

async function openArtifactFolder(url) {
  folderOpenMessage.value = ''
  try {
    await api.openArtifactFolder(url)
    folderOpenMessage.value = '已在资源管理器中定位该文件。'
  } catch (error) {
    folderOpenMessage.value = error.message || '无法打开文件所在文件夹'
  }
}

async function uploadSourceAudio(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  sourceAudioError.value = ''
  if (!file) return

  const suffix = file.name.split('.').pop()?.toLowerCase()
  if (!['mp3', 'wav', 'm4a', 'aac', 'flac', 'ogg'].includes(suffix)) {
    sourceAudioName.value = ''
    form.source_audio_id = ''
    sourceAudioError.value = '仅支持 mp3、wav、m4a、aac、flac、ogg 音频。'
    return
  }

  sourceAudioUploading.value = true
  try {
    const payload = await api.uploadEditorAsset(file)
    if (payload.asset?.kind !== 'audio') {
      throw new Error('上传文件不是可识别的音频。')
    }
    form.source_audio_id = payload.asset.id
    sourceAudioName.value = payload.asset.name || file.name
    await refreshEditor()
  } catch (error) {
    form.source_audio_id = ''
    sourceAudioName.value = ''
    sourceAudioError.value = error.message || '上传配音失败'
  } finally {
    sourceAudioUploading.value = false
  }
}

async function submit() {
  if (!session.value.user) {
    authError.value = '请先登录后再生成视频'
    return
  }
  if (!canSubmitGeneration.value) return
  submitting.value = true
  try {
    activeJob.value = await api.createJob({
      ...form,
      tts_emotion: form.tts_emotion || null,
      tts_pronunciation: form.tts_pronunciation || null,
    })
    form.project_name = randomProjectName()
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

async function submitModule1() {
  if (!canSubmitModule1.value) return
  submittingModule1.value = true
  try {
    module1Job.value = await api.createJob({
      ...form,
      module1_only: true,
      skip_tts: false,
      source_audio_id: null,
      skip_text_correction: false,
      tts_emotion: form.tts_emotion || null,
      tts_pronunciation: form.tts_pronunciation || null,
    })
    jobPage.value = 1
    await refresh()
  } finally {
    submittingModule1.value = false
  }
}

async function cancelModule1() {
  if (!module1JobRunning.value || !module1Job.value?.id) return
  module1Job.value = await api.cancelJob(module1Job.value.id)
  await refresh()
}

async function resumeGeneration() {
  if (!canResumeGeneration.value || !activeJob.value?.id) return
  resumingGeneration.value = true
  try {
    activeJob.value = await api.resumeJob(activeJob.value.id)
    await refresh()
  } finally {
    resumingGeneration.value = false
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
    'voice_01.wav': '官方示例音色 01',
    'voice_02.wav': '官方示例音色 02',
    'voice_03.wav': '官方示例音色 03',
    'voice_04.wav': '官方示例音色 04',
    'voice_05.wav': '官方示例音色 05 · 默认叙事',
    'voice_06.wav': '官方示例音色 06',
    'voice_07.wav': '官方示例音色 07',
    'voice_08.wav': '官方示例音色 08',
    'voice_09.wav': '官方示例音色 09',
    'voice_11.wav': '官方示例音色 11',
    'voice_12.wav': '官方示例音色 12',
  }[voice] || voice
}

function emotionLabel(emotion) {
  return {
      happy: '开心',
      angry: '愤怒',
      sad: '悲伤',
      afraid: '恐惧',
      disgusted: '厌恶',
      melancholic: '低落',
      surprised: '惊讶',
      calm: '平静',
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
    module1_subtitle: '模块 1 原始字幕',
    story_plan: 'Agent 1 全文规划',
    visual_prompt_plan: 'Agent 2 分镜提示词',
    poster_mapping: '海报映射',
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
