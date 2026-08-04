<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <div class="brand">
        <img class="brand-mark brand-logo" src="/one-click-vidgen-logo.png" alt="One-Click VidGen Logo" />
        <div>
          <div class="brand-name">一键生成视频</div>
          <div class="brand-sub">One-Click VidGen</div>
        </div>
      </div>
      <button class="sidebar-preflight-button" type="button" :disabled="preflightRunning || !session.user" @click="runManualPreflight">
        <span class="sidebar-preflight-icon" aria-hidden="true">⚡</span>
        <span>
          <strong>{{ preflightRunning ? '正在检测…' : '启动前自动检测' }}</strong>
          <small>API、TTS、素材与运行环境</small>
        </span>
      </button>

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

      <div v-if="session.user" class="sidebar-card api-key-card" :class="{ 'pool-mode-active': form.use_cloud_image_pool }">
        <div class="sidebar-label">模型 API Key</div>
        <div class="muted small">密钥仅保存到本机 `.env`，页面不会回显原文。</div>
        <div class="api-key-entry" :class="{ 'cloud-pool-disabled': form.use_cloud_image_pool }">
          <span>语言模型</span>
          <select v-model="apiKeyForm.language_provider" class="language-provider-select" :disabled="form.use_cloud_image_pool" @change="onLanguageProviderChanged">
            <option v-for="provider in languageProviderOptions" :key="provider.value" :value="provider.value">
              {{ provider.label }}{{ provider.configured ? '（已配置）' : '' }}
            </option>
          </select>
          <input v-if="apiKeyFieldOpen('language')" v-model="apiKeyForm.language_api_key" type="password" autocomplete="off" :placeholder="`${currentLanguageProviderLabel} API Key`" />
          <div v-else class="api-key-state-bar" :class="{ error: !form.use_cloud_image_pool && apiKeyRuntimeErrors.language }">
            <span><strong>{{ form.use_cloud_image_pool ? '号池已接管文本模型' : (apiKeyRuntimeErrors.language ? 'ERROR' : `${currentLanguageProviderLabel} 已配置`) }}</strong><small v-if="!form.use_cloud_image_pool && apiKeyRuntimeErrors.language">{{ apiKeyRuntimeErrors.language }}</small></span>
            <button type="button" :disabled="form.use_cloud_image_pool" :title="`重新输入 ${currentLanguageProviderLabel} API Key`" @click="editApiKey('language')">✏️</button>
          </div>
        </div>
        <div class="api-key-pool-field api-key-entry" :class="{ 'cloud-pool-disabled': form.use_cloud_image_pool }">
          <span>图像模型 API Key</span>
          <template v-if="apiKeyFieldOpen('image')">
            <input v-model="apiKeyForm.image_api_key" type="password" autocomplete="off" :disabled="form.use_cloud_image_pool" placeholder="第三方图像接口 API Key" />
            <div v-for="(_, index) in apiKeyForm.image_api_keys" :key="`image-key-${index}`" class="api-key-extra-row">
              <input v-model="apiKeyForm.image_api_keys[index]" type="password" autocomplete="off" :disabled="form.use_cloud_image_pool" :placeholder="`新增图像账号 ${index + 2}`" />
              <button type="button" :disabled="form.use_cloud_image_pool" title="移除此账号输入框" @click="removeApiKeyField('image_api_keys', index)">×</button>
            </div>
            <div class="api-key-field-footer">
              <span class="muted small">可继续添加并行账号</span>
              <button class="api-key-add-btn" type="button" :disabled="form.use_cloud_image_pool" title="增加图像模型账号" @click="addApiKeyField('image_api_keys')">＋</button>
            </div>
          </template>
          <div v-else class="api-key-state-bar" :class="{ error: apiKeyRuntimeErrors.image }">
            <span><strong>{{ apiKeyRuntimeErrors.image ? 'ERROR' : 'API 已配置' }}</strong><small v-if="apiKeyRuntimeErrors.image">{{ apiKeyRuntimeErrors.image }}</small></span>
            <div class="api-key-state-actions">
              <button type="button" :disabled="form.use_cloud_image_pool" title="新增图像模型并行账号" @click="addApiKeyAccount('image')">＋</button>
              <button type="button" :disabled="form.use_cloud_image_pool" title="重新输入图像模型 API Key" @click="editApiKey('image')">✏️</button>
            </div>
          </div>
        </div>
        <div class="api-key-pool-field api-key-entry" :class="{ 'cloud-pool-disabled': form.use_cloud_image_pool }">
          <span>通用 API Key</span>
          <template v-if="apiKeyFieldOpen('common')">
            <input v-model="apiKeyForm.common_api_key" type="password" autocomplete="off" :disabled="form.use_cloud_image_pool" placeholder="仅填此项会同时用于语言和图像" />
            <div v-for="(_, index) in apiKeyForm.common_api_keys" :key="`common-key-${index}`" class="api-key-extra-row">
              <input v-model="apiKeyForm.common_api_keys[index]" type="password" autocomplete="off" :disabled="form.use_cloud_image_pool" :placeholder="`新增通用账号 ${index + 2}`" />
              <button type="button" :disabled="form.use_cloud_image_pool" title="移除此账号输入框" @click="removeApiKeyField('common_api_keys', index)">×</button>
            </div>
            <div class="api-key-field-footer">
              <span class="muted small">可继续添加通用账号</span>
              <button class="api-key-add-btn" type="button" :disabled="form.use_cloud_image_pool" title="增加通用账号" @click="addApiKeyField('common_api_keys')">＋</button>
            </div>
          </template>
          <div v-else class="api-key-state-bar" :class="{ error: apiKeyRuntimeErrors.common }">
            <span><strong>{{ apiKeyRuntimeErrors.common ? 'ERROR' : 'API 已配置' }}</strong><small v-if="apiKeyRuntimeErrors.common">{{ apiKeyRuntimeErrors.common }}</small></span>
            <div class="api-key-state-actions">
              <button type="button" :disabled="form.use_cloud_image_pool" title="新增通用并行账号" @click="addApiKeyAccount('common')">＋</button>
              <button type="button" :disabled="form.use_cloud_image_pool" title="重新输入通用 API Key" @click="editApiKey('common')">✏️</button>
            </div>
          </div>
        </div>
        <div class="cloud-pool-toggle-row">
          <div>
            <strong>使用号池</strong>
            <small>{{ form.use_cloud_image_pool ? 'Agent 与出图均使用云端号池，并从账户积分扣除' : '关闭时使用本机保存的模型 API Key' }}</small>
          </div>
          <label class="inline-switch cloud-pool-switch" :title="cloudSession.authenticated ? '切换云端号池' : '需先登录右上角云端账户'">
            <input v-model="form.use_cloud_image_pool" type="checkbox" />
            <span class="switch-track"><span></span></span>
          </label>
        </div>
        <div v-if="form.use_cloud_image_pool" class="cloud-pool-status" :class="cloudSession.authenticated ? 'ready' : 'warning'">
          <span v-if="cloudSession.authenticated">文本 + 图像号池已启用 · 可用积分 {{ cloudAvailableCredits }}</span>
          <button v-else type="button" @click="openCloudLogin">请先登录云端账户</button>
        </div>
        <div class="muted small">{{ form.use_cloud_image_pool ? '号池模式不需要填写个人文本、图像或通用 API Key。' : '语言模型可独立选择 Gemini、DeepSeek、GPT、Kimi 或 GLM；各家的 Key 分开保存。通用 Key 仍只用于原有第三方接口工作流。' }}</div>
        <div v-if="apiKeyMessage" class="api-key-message">{{ apiKeyMessage }}</div>
        <button v-if="apiKeyEditorVisible" class="primary-btn full-btn" type="button" :disabled="savingApiKeys" @click="saveApiKeySettings">
          {{ savingApiKeys ? '保存中...' : '保存 API Key' }}
        </button>
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
      <button
        class="sidebar-secondary-entry"
        type="button"
        :class="{ active: activePage === 'development' }"
        @click="activePage = 'development'"
      >
        待开发功能
      </button>
    </aside>

    <main class="main">
      <header class="topbar">
        <button class="product-title" type="button" @click="sidebarOpen = !sidebarOpen" aria-label="一键生成视频，点击切换侧边栏">
          一键生成视频 <span>/</span> One-Click VidGen
        </button>
        <div class="cloud-account-entry">
          <button
            v-if="cloudSession.authenticated"
            class="cloud-account-summary"
            type="button"
            title="查看云端账户"
            @click="openCloudLogin"
          >
            <span class="cloud-account-avatar" aria-hidden="true">{{ cloudDisplayName.slice(0, 1).toUpperCase() }}</span>
            <span class="cloud-account-copy">
              <strong>{{ cloudDisplayName }}</strong>
              <small>剩余积分 {{ cloudAvailableCredits }}</small>
            </span>
          </button>
          <button v-else class="primary-btn cloud-login-entry" type="button" @click="openCloudLogin">
            登录
          </button>
        </div>
      </header>

      <div v-if="cloudLoginOpen" class="cloud-auth-overlay" @click.self="cloudLoginOpen = false">
        <section class="cloud-auth-dialog" role="dialog" aria-modal="true" aria-labelledby="cloud-auth-title">
          <div class="cloud-auth-dialog-head">
            <div>
              <span class="cluster-card-kicker">ONE-CLICK VIDGEN CLOUD</span>
              <h2 id="cloud-auth-title">{{ cloudSession.authenticated ? '云端账户' : '登录云端服务' }}</h2>
            </div>
            <button class="cloud-auth-close" type="button" aria-label="关闭登录窗口" @click="cloudLoginOpen = false">×</button>
          </div>

          <div v-if="!cloudSession.configured" class="cluster-notice warning">
            云端服务正在部署中，登录入口已经准备完毕。服务上线后会由程序自动连接，无需用户填写服务器地址。
          </div>
          <template v-else-if="cloudSession.authenticated">
            <div class="cloud-auth-profile">
              <span class="cloud-auth-profile-avatar">{{ cloudDisplayName.slice(0, 1).toUpperCase() }}</span>
              <div><strong>{{ cloudDisplayName }}</strong><small>{{ cloudSession.user?.email || '云端用户' }}</small></div>
            </div>
            <div class="cloud-auth-stats">
              <div><span>可用积分</span><strong>{{ cloudAvailableCredits }}</strong></div>
              <div><span>冻结积分</span><strong>{{ cloudAccount.credits?.reserved ?? 0 }}</strong></div>
              <div><span>运行任务</span><strong>{{ cloudAccount.quota?.running_jobs ?? 0 }}/{{ cloudAccount.quota?.max_concurrent_jobs ?? '-' }}</strong></div>
            </div>
            <div class="cloud-auth-actions">
              <button class="ghost-btn" type="button" :disabled="cloudBusy" @click="refreshCloudState">刷新账户</button>
              <button class="ghost-btn danger-btn" type="button" :disabled="cloudBusy" @click="logoutCloud">退出登录</button>
            </div>
          </template>
          <form v-else class="cloud-auth-form" @submit.prevent="loginCloud">
            <label><span>邮箱</span><input v-model.trim="cloudLoginForm.email" type="email" autocomplete="email" placeholder="请输入注册邮箱" required /></label>
            <label><span>密码</span><input v-model="cloudLoginForm.password" type="password" autocomplete="current-password" placeholder="请输入密码" required /></label>
            <button class="primary-btn cloud-auth-submit" type="submit" :disabled="cloudBusy">
              {{ cloudBusy ? '正在登录…' : '登录' }}
            </button>
            <button class="ghost-btn" type="button" :disabled="cloudBusy" @click="registerCloud">注册新账户</button>
          </form>
          <div v-if="cloudError" class="board-error cloud-auth-feedback">{{ cloudError }}</div>
          <div v-else-if="cloudMessage" class="api-key-message cloud-auth-feedback">{{ cloudMessage }}</div>
          <p class="cloud-auth-security">登录凭据由本机后端与云端服务安全交换，浏览器不会保存集群访问令牌。</p>
        </section>
      </div>

      <section class="content stack">
        <nav class="page-tabs" aria-label="页面切换">
          <button
            type="button"
            :class="{ active: activePage === 'workspace' }"
            @click="form.step_mode = false; activePage = 'workspace'"
          >
            <span>生成工作台</span>
            <small>当前可用主流程</small>
          </button>
          <label
            class="page-tab-step-toggle"
            :class="{ active: activePage === 'workspace' && form.step_mode }"
          >
            <input v-model="form.step_mode" type="checkbox" @change="activePage = 'workspace'" />
            <span class="page-tab-step-switch" aria-hidden="true"><i></i></span>
            <span class="page-tab-step-copy"><strong>分步模式</strong><small>试听、验图后继续</small></span>
          </label>
          <button
            type="button"
            :class="{ active: activePage === 'module1' }"
            @click="activePage = 'module1'"
          >
            <span>模块 1 · 仅配音</span>
            <small>只运行 IndexTTS2</small>
          </button>
          <button
            type="button"
            :class="{ active: activePage === 'subtitle' }"
            @click="activePage = 'subtitle'"
          >
            <span>模块 2 · 字幕识别</span>
            <small>音频转 SRT 与校对</small>
          </button>
          <div class="page-tabs-actions">
            <div v-if="activePage === 'workspace'" class="generation-top-actions">
              <label class="main-render-variant">
                <span>成片版本</span>
                <select v-model="form.video_render_variant">
                  <option value="subtitles">仅字幕版</option>
                  <option value="raw">仅无字幕版</option>
                  <option value="both">双版本</option>
                </select>
              </label>
              <button class="ghost-btn stop-btn" type="button" :disabled="!canCancelGeneration || cancellingGeneration" @click="cancelGeneration">
                {{ cancellingGeneration ? '正在停止...' : '停止生成' }}
              </button>
              <button class="ghost-btn" type="button" :disabled="!canResumeGeneration || resumingGeneration" @click="resumeGeneration">
                {{ resumingGeneration ? '正在续跑...' : '断点续跑' }}
              </button>
              <button v-if="canContinueStepMode" class="primary-btn" type="button" :disabled="resumingGeneration" @click="resumeGeneration">
                {{ stepModeContinueLabel }}
              </button>
              <button class="primary-btn launch-generation-btn" type="button" :disabled="submitting || !canSubmitGeneration" @click="submit">
                {{ submitButtonText }}
              </button>
              <small v-if="generationSubmitMessage" class="generation-submit-message">{{ generationSubmitMessage }}</small>
            </div>
            <template v-if="session.user">
              <button class="toolbar-save-button" type="button" :disabled="savingParameterPreset" @click="saveCurrentParameterPreset">
                {{ savingParameterPreset ? '保存中…' : '保存当前参数' }}
              </button>
              <select v-model="selectedParameterPreset" class="parameter-preset-select" :disabled="loadingParameterPresets || !parameterPresets.length">
                <option value="">读取已保存参数</option>
                <option v-for="preset in parameterPresets" :key="preset.name" :value="preset.name">{{ preset.name }}</option>
              </select>
              <button class="toolbar-load-button" type="button" :disabled="!selectedParameterPreset || loadingParameterPresets" @click="loadSelectedParameterPreset">读取参数</button>
              <button
                class="toolbar-delete-button"
                type="button"
                :disabled="!selectedParameterPreset || deletingParameterPreset || loadingParameterPresets"
                @click="deleteSelectedParameterPreset"
              >
                {{ deletingParameterPreset ? '删除中…' : '删除参数' }}
              </button>
            </template>
            <span v-if="parameterPresetMessage" class="muted small page-tabs-message">{{ parameterPresetMessage }}</span>
          </div>
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
            </div>

            <div class="create-copy-column">
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

            <label class="stack">
              <span>{{ form.skip_text_correction ? '口播文案（已选择无文案，可留空）' : '口播文案' }}</span>
              <textarea
                v-model="form.script"
                rows="14"
                :disabled="form.skip_text_correction"
                :placeholder="scriptPlaceholder"
              ></textarea>
              <small class="script-character-count" :class="{ error: scriptTooLong }">
                {{ scriptCharacterCount.toLocaleString() }} / {{ MAX_SCRIPT_CHARACTERS.toLocaleString() }} 字符
                <template v-if="scriptTooLong"> · 超出单次上限，请按完整章节拆分后分批生成</template>
              </small>
            </label>
            </div>

            <div class="create-settings-column">
            <div class="create-audio-column">
            <div v-if="!form.skip_tts" class="tts-parameter-panel">
              <div class="tts-parameter-head">
                <div>
                  <div class="tts-engine-row">
                    <div class="sidebar-label">{{ ttsEngineLabel }}</div>
                    <label class="tts-engine-select" title="选择配音执行方式">
                      <span>执行方式</span>
                      <select v-model="ttsEngine">
                        <option value="indextts2">本地 GPU</option>
                        <option value="cluster">集群 GPU</option>
                        <option value="qwen">Qwen-TTS</option>
                      </select>
                    </label>
                  </div>
                  <h3>语音参数</h3>
                </div>
                <div class="tts-parameter-meta">
                  <span v-if="ttsEngine === 'indextts2'" class="status-chip" :class="health.tts_online ? 'success' : 'warning'">
                    {{ health.tts_online ? 'IndexTTS2 就绪' : 'IndexTTS2 未就绪' }}
                  </span>
                  <span v-else-if="ttsEngine === 'cluster'" class="status-chip" :class="cloudReady ? 'success' : 'warning'">
                    {{ cloudReady ? '集群已登录' : '集群未登录' }}
                  </span>
                  <span class="muted small">{{ ttsEngineProviderLabel }}</span>
                </div>
              </div>
              <div v-if="ttsEngine === 'indextts2'" class="form-grid tts-param-grid">
                <div class="script-upload-field tts-voice-upload">
                  <span>上传本地参考音色</span>
                  <div class="tts-voice-picker-row">
                    <label class="script-file-picker">
                      <input type="file" accept=".wav,.mp3,.flac,audio/wav,audio/mpeg,audio/flac" @change="uploadTtsVoice" />
                      <span>{{ ttsVoiceUploading ? '上传中' : '浏览音频' }}</span>
                      <strong>{{ ttsVoiceUploadName || '选择清晰的 WAV / MP3 / FLAC' }}</strong>
                    </label>
                    <button class="voice-preview-btn" type="button" :disabled="!ttsVoicePreviewUrl" :title="ttsVoicePreviewPlaying ? '暂停试听' : '播放试听'" @click="toggleTtsVoicePreview">
                      {{ ttsVoicePreviewPlaying ? '❚❚' : '▶' }}
                    </button>
                  </div>
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
              <div v-else-if="ttsEngine === 'cluster'" class="cluster-tts-config">
                <div v-if="!cloudSession.configured" class="cluster-notice warning">
                  云端集群服务尚未开放。服务上线后程序会自动连接，不需要手动填写服务器地址。
                </div>
                <template v-else-if="!cloudSession.authenticated">
                  <div class="cluster-notice cluster-login-prompt">
                    <span>使用集群 GPU 前，请先登录云端账户。</span>
                    <button class="primary-btn compact-btn" type="button" @click="openCloudLogin">前往登录</button>
                  </div>
                </template>
                <template v-else>
                  <div class="cluster-account-bar">
                    <span><strong>{{ cloudSession.user?.email || '云端账户' }}</strong></span>
                    <span>可用积分 <strong>{{ cloudAccount.credits?.available ?? '-' }}</strong></span>
                    <span>冻结 <strong>{{ cloudAccount.credits?.reserved ?? '-' }}</strong></span>
                    <span>并发 <strong>{{ cloudAccount.quota?.running_jobs ?? 0 }}/{{ cloudAccount.quota?.max_concurrent_jobs ?? '-' }}</strong></span>
                    <button class="ghost-btn compact-btn" type="button" :disabled="cloudBusy" @click="refreshCloudState">刷新</button>
                    <button class="ghost-btn compact-btn" type="button" :disabled="cloudBusy" @click="logoutCloud">退出云端</button>
                  </div>
                  <div class="cluster-voice-workspace">
                    <section class="cluster-voice-card cluster-library-card">
                      <div class="cluster-card-head">
                        <div><span class="cluster-card-kicker">VOICE LIBRARY</span><h4>选择云端音色</h4></div>
                        <span class="cluster-count">{{ cloudPresetVoiceOptions.length + cloudUploadedVoiceOptions.length }} 个可用</span>
                      </div>
                      <label class="cluster-main-select">
                        <span>当前音色</span>
                        <span class="cluster-main-select-row">
                          <select v-model="cloudVoiceModel">
                            <option value="">自动选择 · {{ firstDefaultCloudVoice?.display_name || '第一个默认音色' }}</option>
                            <optgroup label="云端默认音色">
                              <option v-for="voice in cloudPresetVoiceOptions" :key="`preset:${voice.id}`" :value="`preset:${voice.id}`">{{ voice.display_name || voice.id }}</option>
                            </optgroup>
                            <optgroup v-if="cloudUploadedVoiceOptions.length" label="我上传的音色">
                              <option v-for="voice in cloudUploadedVoiceOptions" :key="`uploaded:${voice.id}`" :value="`uploaded:${voice.id}`">{{ voice.display_name || voice.id }}</option>
                            </optgroup>
                          </select>
                          <button class="cloud-voice-preview-btn" type="button" :disabled="!previewableCloudPresetVoice || cloudVoicePreviewLoading" :title="previewableCloudPresetVoice ? `试听 ${previewableCloudPresetVoice.display_name || previewableCloudPresetVoice.id}` : '请选择一个云端默认音色'" @click="toggleCloudVoicePreview">
                            {{ cloudVoicePreviewLoading ? '加载中…' : (cloudVoicePreviewPlaying ? 'Ⅱ 暂停' : '▶ 试听') }}
                          </button>
                        </span>
                      </label>
                      <div class="uploaded-voice-section">
                        <div class="uploaded-voice-title"><strong>我上传的音色</strong><span>{{ cloudUploadedVoiceOptions.length }}/{{ cloudVoiceLimits.max_uploaded_voices || 20 }}</span></div>
                        <div v-if="cloudUploadedVoiceOptions.length" class="uploaded-voice-list">
                          <button v-for="voice in cloudUploadedVoiceOptions" :key="`mine:${voice.id}`" type="button" class="uploaded-voice-item" :class="{ active: selectedCloudVoice?.id === voice.id }" @click="selectCloudVoice(voice)">
                            <span class="voice-avatar">{{ (voice.display_name || '音').slice(0, 1) }}</span>
                            <span><strong>{{ voice.display_name || voice.id }}</strong><small>{{ voice.audio?.format?.toUpperCase() || 'AUDIO' }} · 已保存到云端</small></span>
                          </button>
                        </div>
                        <div v-else class="uploaded-voice-empty">还没有上传音色。上传后会永久显示在这里。</div>
                      </div>
                      <button v-if="selectedCloudVoice && selectedCloudVoice.type !== 'preset'" class="ghost-btn compact-btn cloud-voice-delete" type="button" :disabled="cloudBusy" @click="deleteSelectedCloudVoice">删除当前音色</button>
                    </section>
                    <section class="cluster-voice-card cluster-upload-card">
                      <div class="cluster-card-head">
                        <div><span class="cluster-card-kicker">UPLOAD</span><h4>上传我的音色</h4></div>
                      </div>
                      <label class="cluster-upload-name"><span>音色名称</span><input v-model.trim="cloudVoiceDisplayName" type="text" maxlength="80" placeholder="给这个音色取一个容易识别的名字" /></label>
                      <label class="cluster-drop-zone" :class="{ disabled: cloudVoiceUploading || !cloudVoiceApiAvailable }">
                        <input type="file" accept=".wav,.mp3,.flac,audio/wav,audio/mpeg,audio/flac" :disabled="cloudVoiceUploading || !cloudVoiceApiAvailable" @change="uploadCloudVoice" />
                        <span class="cluster-upload-icon">＋</span>
                        <strong>{{ cloudVoiceUploading ? '正在上传和保存…' : (cloudVoiceApiAvailable ? '选择音频并上传' : '云端暂未开放上传') }}</strong>
                        <small>WAV / MP3 / FLAC · 建议 3–30 秒 · 最大 20 MiB</small>
                      </label>
                    </section>
                  </div>
                  <div class="form-grid cluster-parameter-grid">
                    <label><span>情绪</span><select v-model="form.tts_emotion"><option value="">模型默认</option><option v-for="emotion in settings.tts?.emotions || []" :key="emotion" :value="emotion">{{ emotionLabel(emotion) }}</option></select></label>
                    <label><span>语速（0.5–2）</span><input v-model.number="form.tts_speed" type="number" min="0.5" max="2" step="0.01" /></label>
                    <label><span>音量（0.1–10）</span><input v-model.number="form.tts_volume" type="number" min="0.1" max="10" step="0.01" /></label>
                    <label><span>音调（-12–12）</span><input v-model.number="form.tts_pitch" type="number" min="-12" max="12" step="1" /></label>
                    <label><span>并行分块（1–3）</span><input v-model.number="form.tts_parallelism" type="number" min="1" max="3" step="1" /></label>
                  </div>
                  <div class="cluster-quote-bar">
                    <span>预计积分：<strong>{{ cloudQuote.estimated_credits ?? '尚未报价' }}</strong></span>
                    <button class="ghost-btn compact-btn" type="button" :disabled="cloudQuoteLoading || form.script.trim().length < 5" @click="refreshCloudQuote">
                      {{ cloudQuoteLoading ? '报价中…' : '刷新报价' }}
                    </button>
                  </div>
                </template>
                <small v-if="cloudError" class="script-upload-error">{{ cloudError }}</small>
                <small v-else-if="cloudMessage" class="api-key-message">{{ cloudMessage }}</small>
              </div>
              <div v-else class="qwen-tts-config">
                <label v-if="apiKeyFieldOpen('qwen_tts')" class="qwen-key-field">
                  <input v-model="apiKeyForm.qwen_tts_api_key" type="password" autocomplete="off" placeholder="DashScope API Key（sk-...）" />
                </label>
                <div v-else class="api-key-state-bar qwen-key-state" :class="{ error: apiKeyRuntimeErrors.qwen_tts }">
                  <span><strong>{{ apiKeyRuntimeErrors.qwen_tts ? 'ERROR' : 'API 已配置' }}</strong><small v-if="apiKeyRuntimeErrors.qwen_tts">{{ apiKeyRuntimeErrors.qwen_tts }}</small></span>
                  <button type="button" title="重新输入 Qwen-TTS API Key" @click="editApiKey('qwen_tts')">✏️</button>
                </div>
                <div class="qwen-voice-controls">
                  <label>
                    <span>系统音色</span>
                    <select v-model="form.qwen_tts_voice">
                      <optgroup v-for="group in qwenVoiceGroups" :key="group.label" :label="group.label">
                        <option v-for="voice in group.voices" :key="voice.value" :value="voice.value">
                          {{ voice.label }}
                        </option>
                      </optgroup>
                    </select>
                    <small v-if="!qwenSelectedVoiceSupportsInstructions && form.qwen_tts_instructions.trim()" class="qwen-voice-warning">
                      当前音色仅支持基础合成；请清空“配音描述”，或换用“支持配音描述”的音色。
                    </small>
                  </label>
                </div>
                <label class="qwen-instruction-field">
                  <span>配音描述（指令控制）</span>
                  <textarea
                    v-model="form.qwen_tts_instructions"
                    rows="7"
                    maxlength="1600"
                    placeholder="例如：沉稳的中年女性，语速偏慢，吐字清晰，带有克制而渐进的悬疑感，适合都市怪谈叙述。"
                  ></textarea>
                </label>
                <div v-if="apiKeyFieldOpen('qwen_tts')" class="qwen-tts-actions">
                  <button class="primary-btn qwen-save-btn" type="button" :disabled="savingQwenTtsKey" @click="saveQwenTtsKey">
                    {{ savingQwenTtsKey ? '保存中...' : '保存 API Key' }}
                  </button>
                </div>
                <small class="muted">系统会严格、原样执行配音描述：整篇文案固定音色、模型、语言与描述，并采用长分段合成后统一响度。</small>
                <small v-if="qwenTtsKeyMessage" class="api-key-message">{{ qwenTtsKeyMessage }}</small>
              </div>
            </div>

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
            <section v-if="stepModeAudioUrl" class="step-audio-review-card">
              <div>
                <div class="sidebar-label">{{ activeJob?.request?.step_mode ? '分步模式 · 配音试听' : '配音试听' }}</div>
                <strong>{{ activeJob?.request?.step_mode ? '确认配音后再继续配图' : '本次任务的配音已生成，可随时试听' }}</strong>
              </div>
              <div class="step-audio-controls">
                <audio
                  ref="stepAudioPlayer"
                  preload="metadata"
                  :src="stepModeAudioUrl"
                  @loadedmetadata="syncStepAudioMetadata"
                  @timeupdate="syncStepAudioProgress"
                  @ended="stepAudioPlaying = false"
                ></audio>
                <button class="step-audio-play" type="button" @click="toggleStepAudioPlayback">
                  {{ stepAudioPlaying ? '❚❚' : '▶' }}
                </button>
                <input
                  class="step-audio-seek"
                  type="range"
                  min="0"
                  :max="Math.max(stepAudioDuration, 0.01)"
                  step="0.01"
                  :value="stepAudioCurrentTime"
                  aria-label="配音播放进度"
                  @input="seekStepAudio"
                />
                <span class="step-audio-time">{{ formatStepAudioTime(stepAudioCurrentTime) }} / {{ formatStepAudioTime(stepAudioDuration) }}</span>
                <button
                  class="step-audio-download"
                  type="button"
                  :disabled="savingStepAudio"
                  title="将本次配音另存到指定位置"
                  aria-label="下载配音"
                  @click="saveStepAudioAs"
                >
                  {{ savingStepAudio ? '…' : '⇩' }}
                </button>
                <button
                  v-if="canRetryTts"
                  class="ghost-btn compact-btn step-audio-retry"
                  type="button"
                  :disabled="retryingTts"
                  @click="retryTts"
                >
                  {{ retryingTts ? '正在重新配音…' : '不满意，重新配音' }}
                </button>
              </div>
              <small v-if="stepAudioSaveMessage" class="muted">{{ stepAudioSaveMessage }}</small>
              <small v-if="canRetryTts" class="muted">重新配音会清理本任务的当前中间产物，并再次停在试听确认。</small>
            </section>
            </div>
            </div>

            <section class="bgm-panel bgm-full-row" :class="{ expanded: form.bgm_enabled }">
              <div class="bgm-panel-head">
                <div>
                  <div class="sidebar-label">背景音乐</div>
                  <strong>为最终成片添加 BGM</strong>
                  <small class="muted">按上传顺序播放；最后一首结束后从第一首开始列表循环。</small>
                </div>
                <label class="switch-row bgm-switch">
                  <input v-model="form.bgm_enabled" type="checkbox" />
                  <span class="switch-track"><i></i></span>
                  <strong>添加 BGM</strong>
                </label>
              </div>
              <div v-if="form.bgm_enabled" class="bgm-panel-body">
                <div class="bgm-track-list">
                  <div v-if="form.bgm_tracks.length" class="bgm-track-list-head">
                    <span>播放列表（按此顺序循环）</span>
                    <button class="ghost-btn compact-btn" type="button" @click="clearBgmTracks('main')">清空列表</button>
                  </div>
                  <div v-for="(track, index) in form.bgm_tracks" :key="`${track.asset_id}-${index}`" class="bgm-track-row">
                    <div class="bgm-track-file">
                      <span class="bgm-order">{{ index + 1 }}</span>
                      <div>
                        <strong>{{ track.name || track.asset_id }}</strong>
                        <small class="muted">第 {{ index + 1 }} 首 · {{ formatBgmDuration(track.duration_seconds) }}</small>
                      </div>
                    </div>
                    <label class="bgm-volume-field">
                      <span>音量（dB）</span>
                      <input v-model.number="track.volume_db" type="number" min="-60" max="6" step="1" />
                    </label>
                    <div class="bgm-track-actions">
                      <button class="ghost-btn compact-btn" type="button" :disabled="!bgmTrackUrl(track)" :title="isBgmPreviewing(track) ? '暂停试听' : '播放试听'" @click="toggleBgmPreview(track)">{{ isBgmPreviewing(track) ? 'Ⅱ' : '▶' }}</button>
                      <button class="ghost-btn compact-btn" type="button" :disabled="index === 0" title="上移" @click="moveBgmTrack(form.bgm_tracks, index, -1)">↑</button>
                      <button class="ghost-btn compact-btn" type="button" :disabled="index === form.bgm_tracks.length - 1" title="下移" @click="moveBgmTrack(form.bgm_tracks, index, 1)">↓</button>
                      <button class="ghost-btn compact-btn" type="button" title="移除" @click="removeBgmTrack(index)">×</button>
                    </div>
                  </div>
                  <label class="script-file-picker bgm-upload-picker" :class="{ disabled: bgmUploading }">
                    <input
                      type="file"
                      accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,audio/*"
                      :disabled="bgmUploading"
                      @change="uploadBgmTrack"
                    />
                    <span>{{ bgmUploading ? '上传中…' : (form.bgm_tracks.length ? '添加下一首' : '上传 BGM') }}</span>
                    <strong>MP3 / WAV / M4A / AAC / FLAC / OGG</strong>
                  </label>
                  <small v-if="bgmError" class="script-upload-error">{{ bgmError }}</small>
                </div>
                <div class="bgm-fade-card">
                  <label class="check-row">
                    <input v-model="form.bgm_fade_enabled" type="checkbox" />
                    <span>切换音乐及视频结束时开启渐弱</span>
                  </label>
                  <label>
                    <span>渐弱时长（秒）</span>
                    <input
                      v-model.number="form.bgm_fade_duration"
                      type="number"
                      min="0.1"
                      max="30"
                      step="0.1"
                      :disabled="!form.bgm_fade_enabled"
                    />
                  </label>
                  <small class="muted">默认 1 秒；关闭后音乐会按顺序直接衔接。</small>
                </div>
              </div>
            </section>

            <div class="content-mode-bar content-mode-full-row">
              <div class="content-mode-copy">
                <div class="sidebar-label">作品风格</div>
                <strong>选择内容与画面模式</strong>
              </div>
              <div class="content-mode-options">
                <button
                  v-for="mode in contentModeOptions"
                  :key="mode.key"
                  type="button"
                  :class="{ active: form.content_mode === mode.key && form.visual_prompt_mode !== 'full' }"
                  @click="setContentMode(mode.key)"
                >
                  <span>{{ mode.label }}</span>
                  <small>{{ mode.description }}</small>
                </button>
              </div>
            </div>

            <div class="tts-parameter-panel visual-pacing-standalone">
              <div class="visual-pacing-panel">
                <div class="visual-pacing-copy">
                  <div class="sidebar-label">画面节奏</div>
                  <strong>{{ visualPacingSummary }}</strong>
                  <small class="muted">根据字幕时间戳分组；Agent 的快节奏建议不会突破最低停留时长。</small>
                </div>
                <label class="visual-pacing-select">
                  <span>节奏预设</span>
                  <select v-model="form.visual_pacing_preset" @change="rememberVisualPacing">
                    <option value="auto">按作品风格自动</option>
                    <option value="slow">舒缓</option>
                    <option value="standard">标准</option>
                    <option value="fast">紧凑</option>
                    <option value="custom">自定义</option>
                  </select>
                </label>
                <div v-if="form.visual_pacing_preset === 'custom'" class="form-grid visual-pacing-custom">
                  <label>
                    <span>最低停留（秒）</span>
                    <input v-model.number="form.visual_min_duration" type="number" min="4" max="20" step="0.5" @input="rememberVisualPacing" />
                  </label>
                  <label>
                    <span>目标时长（秒）</span>
                    <input v-model.number="form.visual_target_duration" type="number" min="5" max="30" step="0.5" @input="rememberVisualPacing" />
                  </label>
                  <label>
                    <span>最长时长（秒）</span>
                    <input v-model.number="form.visual_max_duration" type="number" min="6" max="40" step="0.5" @input="rememberVisualPacing" />
                  </label>
                  <label>
                    <span>单图最多字幕片段</span>
                    <input v-model.number="form.visual_max_slides" type="number" min="1" max="12" step="1" @input="rememberVisualPacing" />
                  </label>
                </div>
              </div>
            </div>

            <div class="tts-parameter-panel visual-prompt-panel">
              <template v-if="form.visual_prompt_mode !== 'full'">
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
                    : form.content_mode === 'pure_science'
                      ? '描述跨学科教材插图、结构图、实验、公式、地图、时间轴或流程图的画面质感。'
                    : form.content_mode === 'general'
                      ? '可自由填写：例如日系治愈动画、赛博朋克电影、写实水墨、儿童绘本等。'
                      : '描述惊悚漫画画风、角色一致性、色彩与悬疑氛围。'"
                ></textarea>
                <small v-if="visualMediumWarning" class="visual-medium-warning">
                  <span aria-hidden="true">⚠</span>
                  {{ visualMediumWarning }}
                </small>
              </label>
              <label class="stack">
                <span>全局人物设定</span>
                <textarea
                  v-model="form.global_character_prompt"
                  @focus="setVisualPromptMode('simple')"
                  @input="rememberVisualPrompt"
                  rows="3"
                  maxlength="2000"
                  :placeholder="form.content_mode === 'general'
                    ? '可留空；如需固定角色，可填写外貌、服装和标志物。'
                    : '可留空：使用当前模式默认主角。推荐写法：主角：固定外貌；前期造型；后期造型与触发条件。'"
                ></textarea>
              </label>
              <div class="visual-reference-panel">
                <div class="sidebar-label">角色一致性增强（可选）</div>
                <strong>上传角色形象参考图（最多 3 张）</strong>
                <label class="script-file-picker compact-reference-picker">
                  <input type="file" multiple accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" @change="uploadReferenceImages" />
                  <span>{{ protagonistReferenceUploading ? '上传中' : '浏览图片' }}</span>
                  <small>{{ referenceImageNames.length ? `已选择 ${referenceImageNames.length} 张（按上传顺序为图 1 至图 ${referenceImageNames.length}）` : 'JPG / PNG / WebP，建议单人清晰半身或正脸图' }}</small>
                </label>
                <div v-if="referenceImageNames.length" class="reference-image-chips">
                  <span v-for="(name, index) in referenceImageNames" :key="`${name}-${index}`" class="reference-image-chip">
                    图 {{ index + 1 }} · {{ name }}
                    <button type="button" :title="`移除图 ${index + 1}`" @click="removeReferenceImage(index)">×</button>
                  </span>
                </div>
                <div v-if="protagonistReferenceImageError" class="board-error">{{ protagonistReferenceImageError }}</div>
                <small class="muted">可在全局人物设定中写“男主角图 1、女主角图 2”。Agent 2 会在镜头提示词中标注“角色形象参考图 1”，并只把实际出场角色对应的图片传给 Image2。</small>
              </div>
              <label class="stack">
                <span>故事世界与环境设定（可选）</span>
                <textarea
                  v-model="form.story_environment_prompt"
                  @focus="setVisualPromptMode('simple')"
                  @input="rememberVisualPrompt"
                  rows="3"
                  maxlength="2000"
                  placeholder="例如：2010 年代中国北方小城，老旧居民楼与宠物医院；冬末阴天、冷白灯、潮湿街道。指定时代、城市气质、常驻场景、天气或关键环境道具。"
                ></textarea>
              </label>
              <small class="muted">
                {{ form.content_mode === 'general'
                  ? '通用模式默认不预设主角：可留空，Agent 会仅按原文建立必要角色档案；填写后会作为全局人物设定严格保持。'
                  : '可留空：采用当前模式默认主角。未登记角色会按文案建立临时档案并保持一致；人物造型会按镜头阶段自动锁定。' }}
              </small>
              </template>
              <div v-else class="expert-mode-takeover">
                <div class="sidebar-label">模块 4 · 专家模式</div>
                <strong>Agent 提示词正在接管画面规划</strong>
                <small class="muted">基础画风、人物与环境输入已收起，避免与下方 Agent 指令产生冲突。关闭专家模式后即可恢复基础编辑。</small>
              </div>
            </div>

            <section class="agent-prompt-full-row advanced-agent-console">
              <div class="advanced-agent-console-header">
                <div class="sidebar-label">高级创作控制台</div>
                <strong>提示词预设与 Agent DIY</strong>
              </div>
              <div class="advanced-agent-console-body">
              <div class="agent-prompt-toggle-row">
                <button class="expert-mode-switch" :class="{ active: form.visual_prompt_mode === 'full' }" type="button" @click="setVisualPromptMode(form.visual_prompt_mode === 'full' ? 'simple' : 'full')">
                  <span class="expert-mode-switch-track"><span></span></span>
                  <span><strong>{{ form.visual_prompt_mode === 'full' ? '专家模式已开启' : '开启专家模式' }}</strong><small>修改 Agent 提示词</small></span>
                </button>
                <button v-if="form.visual_prompt_mode === 'full'" class="ghost-btn compact-btn" type="button" :disabled="savingAgentPromptPreset" @click="saveCurrentAgentPromptPreset">
                  {{ savingAgentPromptPreset ? '保存中…' : '保存 Agent 提示词' }}
                </button>
              </div>
              <label v-if="form.visual_prompt_mode === 'full'" class="agent-preset-picker">
                <span>Agent 提示词预设</span>
                <select v-model="selectedAgentPromptPreset" :disabled="loadingAgentPromptPresets" @change="loadSelectedAgentPromptPreset">
                  <option value="">选择 Agent 提示词</option>
                  <optgroup v-if="defaultAgentPromptPresets.length" label="默认参考提示词">
                    <option v-for="preset in defaultAgentPromptPresets" :key="preset.key" :value="preset.key">{{ preset.name }}</option>
                  </optgroup>
                  <optgroup v-if="userAgentPromptPresets.length" label="我保存的提示词">
                    <option v-for="preset in userAgentPromptPresets" :key="preset.key" :value="preset.key">{{ preset.name }}</option>
                  </optgroup>
                </select>
                <small>所有提示词均从 saved_agent_prompts 文件夹读取</small>
              </label>
              <label v-if="form.visual_prompt_mode === 'full'" class="stack agent-prompt-editor">
                <label class="agent2-director-theme-field">
                  <span>导演题材（填空）</span>
                  <input v-model="agent2DirectorThemeModel" type="text" maxlength="40" :placeholder="agent2DirectorThemePlaceholder" />
                </label>
                <span>系统锁定协议（不可修改）</span>
                <textarea class="agent-prompt-locked" :value="activeAgent2LockedProtocol" rows="7" readonly aria-label="Agent 2 系统锁定协议"></textarea>
                <span>{{ form.content_mode === 'general' ? '可编辑的创作指令（Agent 2）承接系统提示词，开头默认为分镜规则' : '完整 Gemini 画面指令（Agent 2）' }}</span>
                <textarea
                  v-model="editableVisualPromptSystem"
                  @input="rememberVisualPrompt"
                  rows="14"
                  :maxlength="form.content_mode === 'general' ? 3400 : 4000"
                  :placeholder="form.content_mode === 'general'
                    ? '在这里补充镜头语言、叙事节奏、画面构图、风格执行或特殊限制。系统输出格式与固定分组规则已锁定。'
                    : '需保留 JSON 输出格式、includes_slides 与 image_prompt 字段约定。'"
                ></textarea>
                <small class="muted">此处编辑 Agent 2 的画面规划指令；下方可按执行顺序调整 Agent 0 与 Agent 1。</small>
              </label>
              <p v-if="form.visual_prompt_mode === 'full'" class="agent1-danger-note">以下两项均为高危参数：Agent 0 负责全文资料，Agent 1 负责字幕时间轴与画面节奏。必须保留各自严格 JSON 输出约定和字段结构，否则可能导致任务失败、连续性错误或严重画面错乱。</p>
              <details v-if="form.visual_prompt_mode === 'full'" class="agent1-prompt-editor">
                <summary>Agent 0 全文资料指令 <span class="agent1-risk-label">（高危参数）</span></summary>
                <label class="stack">
                  <span>完整 Agent 0 全文指令</span>
                  <textarea v-model="form.agent0_prompt_system" @input="rememberVisualPrompt" rows="14" maxlength="12000" placeholder="保留默认内容即可；此项不应出现字幕时间、slide_id 或生图提示词。"></textarea>
                </label>
              </details>
              <details v-if="form.visual_prompt_mode === 'full'" class="agent1-prompt-editor">
                <summary>Agent 1 时间轴分镜指令 <span class="agent1-risk-label">（高危参数）</span></summary>
                <label class="stack">
                  <span>完整 Agent 1 时间轴指令</span>
                  <textarea v-model="form.agent1_prompt_system" @input="rememberVisualPrompt" rows="18" maxlength="12000" placeholder="保留默认内容即可；仅建议熟悉 JSON 输出结构和全文规划流程的用户修改。"></textarea>
                </label>
              </details>
              </div>
            </section>

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
          <div v-if="activeJob?.status === 'waiting_confirmation'" class="step-confirmation-card">
            <template v-if="activeJob.request?._step_mode_stage === 'audio'">
              <strong>配音已生成，等待你的确认</strong>
              <span class="muted small">请使用上方“分步模式 · 配音试听”播放器检查内容、音色与语气；确认后点击右上角“确认配音，开始配图”。</span>
            </template>
            <template v-else>
              <strong>画面已生成，等待你的确认</strong>
              <span class="muted small">请打开画面检查文件夹查看全部图片；确认后点击上方“确认画面，开始渲染”。</span>
              <button class="ghost-btn compact-btn" type="button" @click="openStepModeVisualPreviewFolder">打开画面检查文件夹</button>
            </template>
          </div>
          <div class="log-toolbar">
            <span class="muted small">后台日志</span>
            <div class="log-toolbar-actions">
              <button class="ghost-btn compact-btn" type="button" :disabled="diagnosticExporting || !activeJob" @click="exportDiagnosticPackage(activeJob)">
                {{ diagnosticExporting ? '正在导出…' : '导出问题诊断包' }}
              </button>
              <button class="ghost-btn compact-btn" type="button" @click="showFullLogs = !showFullLogs">
                {{ showFullLogs ? '显示重点' : '显示全部' }}
              </button>
            </div>
          </div>
          <pre class="log-view">{{ logText }}</pre>
          <small v-if="diagnosticMessage" class="diagnostic-message">{{ diagnosticMessage }}</small>
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
              :key="activeJob.id"
              class="project-video"
              controls
              preload="metadata"
              :src="activeJob.artifacts.video_with_subtitles"
            ></video>
            <div v-else class="empty-state">生成完成后，字幕版视频会显示在这里。</div>
            <div v-if="activeJob?.status === 'completed'" class="output-folder-action">
              <button class="ghost-btn" type="button" @click="openProjectOutputFolder">
                打开项目输出文件夹
              </button>
              <small class="muted">包含文案、配音、全部图片、提示词、字幕和最终视频。</small>
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
              <div
                v-for="job in jobs"
                :key="job.id"
                class="project-card"
                :class="{ active: activeJob?.id === job.id }"
                role="button"
                tabindex="0"
                @click="selectJob(job.id)"
                @keydown.enter="selectJob(job.id)"
              >
                <div class="project-top">
                  <span class="status-chip" :class="statusClass(job.status)">{{ statusLabel(job.status) }}</span>
                  <div class="project-actions">
                    <span class="muted small">{{ job.progress }}%</span>
                    <button
                      type="button"
                      class="task-delete-btn"
                      :disabled="['queued', 'running', 'waiting_confirmation'].includes(job.status)"
                      title="删除任务及其专属产物"
                      @click.stop="deleteGenerationJob(job)"
                    >删除</button>
                  </div>
                </div>
                <h3>{{ job.request?.project_name || job.id }}</h3>
                <p>{{ job.message }}</p>
              </div>
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

        <section id="visual-editor" class="panel visual-editor-panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">模块 4 / 5</div>
              <h2>画面修改</h2>
              <p class="muted">只重绘选中的图片；确认后再重新合成视频，不会重新配音、断句或调用 Agent。</p>
            </div>
            <div class="visual-editor-controls">
              <label v-if="visualEditorOpen">编辑项目
                <select v-model="visualEditorProjectId" :disabled="!visualEditorProjects.length || visualEditorLoading" @change="selectVisualEditorProject">
                  <option v-for="project in visualEditorProjects" :key="project.id" :value="project.id">{{ project.name }}</option>
                </select>
              </label>
              <button class="ghost-btn" type="button" :disabled="visualEditorLoading" @click="toggleVisualEditor">
                {{ visualEditorOpen ? '收起画面修改' : '展开画面修改' }}
              </button>
            </div>
          </div>
          <div v-if="visualEditorOpen" class="visual-editor-body">
            <div v-if="!visualEditorProjects.length" class="empty-state">暂未找到可编辑的已完成任务。请先完成一次视频生成。</div>
            <div v-else-if="visualEditorLoading" class="empty-state">正在读取该项目的图片与提示词…</div>
            <template v-else>
              <div class="visual-task-message" :class="visualEditor.task?.status">
                {{ visualEditor.task?.message || '可逐张修改提示词、重绘或替换本地 JPG 图片。' }}
              </div>
              <div class="visual-editor-toolbar">
                <span class="muted small">共 {{ visualEditor.items.length }} 张 · 每页 24 张</span>
                <span v-if="visualReferenceSummary" class="visual-reference-summary">
                  重绘参考图：{{ visualReferenceSummary }}
                </span>
                <button v-if="visualReferenceSummary" class="ghost-btn compact-btn" type="button" @click="clearVisualReferenceImages">清空参考图</button>
                <button class="ghost-btn compact-btn commit-all-baselines" type="button" :disabled="visualEditorLoading || visualEditor.has_active_image_tasks" @click="commitAllVisualBaselines">✅ 确认全部为原图</button>
                <button class="ghost-btn compact-btn" type="button" :disabled="visualEditorLoading" @click="loadVisualEditor({ preservePage: true, hydrateBgm: true })">刷新图片</button>
              </div>
              <div class="visual-image-grid">
                <article v-for="item in visibleVisualEditorItems" :key="item.id" class="visual-image-card" :class="{ processing: item.task?.status === 'running' }">
                  <div class="visual-image-actions">
                    <strong>{{ item.id }}</strong>
                    <button
                      type="button"
                      class="icon-action self-reference-action"
                      :class="{ selected: visualSelfReferenceMacroId === item.id }"
                      :title="visualSelfReferenceMacroId === item.id ? '已作为图1参考，再次点击取消' : '将这张项目内图片作为图1参考'"
                      :aria-label="visualSelfReferenceMacroId === item.id ? '取消图1参考' : '将本图作为图1参考'"
                      :disabled="item.task?.status === 'running'"
                      @click="toggleVisualSelfReferenceImage(item.id)"
                    >⬆️</button>
                    <label class="icon-action replace-action reference-image-action" title="上传本地重绘参考图（最多 3 张）" aria-label="上传本地重绘参考图">
                      ▣<input type="file" multiple accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" @change="uploadVisualReferenceImages($event, item.id)" />
                    </label>
                    <button type="button" class="icon-action" title="按当前提示词重绘" aria-label="按当前提示词重绘" :disabled="item.task?.status === 'running'" @click="redrawVisualImage(item)">▶</button>
                    <button type="button" class="icon-action" title="撤回图片" aria-label="撤回图片" :disabled="item.task?.status === 'running'" @click="undoVisualImage(item)">↶</button>
                    <button type="button" class="icon-action" title="重置提示词" aria-label="重置提示词" :disabled="item.task?.status === 'running'" @click="resetVisualImagePrompt(item)">↺</button>
                    <label class="icon-action replace-action" title="替换本地 JPG 图片" aria-label="替换本地 JPG 图片">
                      ↕<input type="file" accept="image/jpeg" @change="uploadVisualImage($event, item)" />
                    </label>
                    <button type="button" class="icon-action commit-baseline-action" title="将当前图片和提示词确认为新的原图" aria-label="确认当前图片为新的原图" :disabled="item.task?.status === 'running'" @click="commitVisualBaseline(item)">✅</button>
                  </div>
                  <button class="visual-image-preview" type="button" title="点击放大图片" @click="visualPreviewItem = item">
                    <img :src="item.image_url" :alt="item.id" />
                    <span>点击放大预览</span>
                    <em v-if="item.task?.status === 'running'" class="visual-image-running">{{ item.task?.action === 'upload' ? '替换中…' : '重绘中…' }}</em>
                  </button>
                  <label class="stack compact-stack">
                    <span>提示词</span>
                    <textarea v-model="item.prompt" rows="3" maxlength="12000"></textarea>
                  </label>
                  <label class="stack compact-stack">
                    <span>对应文案（暂只读）</span>
                    <textarea :value="item.text" rows="2" readonly></textarea>
                  </label>
                </article>
              </div>
              <div v-if="visualEditorPageCount > 1" class="visual-editor-pagination">
                <button class="ghost-btn compact-btn" type="button" :disabled="visualEditorPage <= 1" @click="visualEditorPage -= 1">上一页</button>
                <span>第 {{ visualEditorPage }} / {{ visualEditorPageCount }} 页</span>
                <button class="ghost-btn compact-btn" type="button" :disabled="visualEditorPage >= visualEditorPageCount" @click="visualEditorPage += 1">下一页</button>
              </div>
              <section class="visual-timing-panel">
                <div class="visual-timing-head">
                  <div>
                    <div class="eyebrow">画面时序</div>
                    <h3>按字幕调整画面位置</h3>
                    <p class="muted small">每次在相邻字幕句之间移动一格。画面始终连续，不会产生空白、重叠或影响配音。</p>
                  </div>
                  <div class="visual-timing-head-actions">
                    <select v-model="selectedVisualTimingHistory" class="timing-history-select" :disabled="visualTimingAdjusting || !visualEditor.timing_history?.length" @change="restoreSelectedVisualTimingHistory">
                      <option value="">历史时序</option>
                      <option v-for="entry in visualEditor.timing_history || []" :key="entry.id" :value="entry.id">{{ entry.label }}</option>
                    </select>
                    <button class="ghost-btn compact-btn timing-save-btn" type="button" :disabled="!visualEditor.timing_available || visualTimingAdjusting" @click="commitEditedTiming">✅ 保存当前时序</button>
                    <button class="ghost-btn compact-btn" type="button" :disabled="!visualEditor.timing_available || visualTimingAdjusting" @click="resetEditedTiming">恢复初始时序</button>
                  </div>
                </div>
                <div v-if="!visualEditor.timing_available" class="timing-unavailable">{{ visualEditor.timing_message || '该历史项目缺少可用的字幕时间线。' }}</div>
                <template v-else>
                  <div class="visual-timing-track" role="list" aria-label="画面时序列表">
                    <button
                      v-for="item in visualEditor.items"
                      :key="`timing-${item.id}`"
                      type="button"
                      class="timing-track-item"
                      :class="{ selected: visualTimingSelectedId === item.id }"
                      @click="visualTimingSelectedId = item.id"
                    >
                      <strong>{{ item.id }}</strong>
                      <span>{{ formatTimingRange(item.timing) }}</span>
                      <small>{{ item.timing?.sentences?.length || 0 }} 句</small>
                    </button>
                  </div>
                  <div v-if="selectedVisualTimingItem" class="visual-timing-editor">
                    <button class="timing-image" type="button" @click="visualPreviewItem = selectedVisualTimingItem">
                      <img :src="selectedVisualTimingItem.image_url" :alt="selectedVisualTimingItem.id" />
                    </button>
                    <div class="timing-detail">
                      <div class="timing-detail-title">
                        <strong>{{ selectedVisualTimingItem.id }}</strong>
                        <span>{{ formatTimingRange(selectedVisualTimingItem.timing) }} · {{ selectedVisualTimingItem.timing?.duration?.toFixed(1) || '0.0' }} 秒</span>
                      </div>
                      <div class="timing-sentences">
                        <div v-for="sentence in selectedVisualTimingItem.timing?.sentences || []" :key="sentence.slide_id" class="timing-sentence">
                          <span>{{ sentence.slide_id }}</span><p>{{ sentence.text }}</p>
                        </div>
                      </div>
                      <div class="timing-actions">
                        <button class="ghost-btn compact-btn" type="button" :disabled="!selectedVisualTimingItem.timing?.can_extend_prev || visualTimingAdjusting" @click="adjustEditedTiming('extend_prev')">← 前面多一句</button>
                        <button class="ghost-btn compact-btn" type="button" :disabled="!selectedVisualTimingItem.timing?.can_shrink_prev || visualTimingAdjusting" @click="adjustEditedTiming('shrink_prev')">前面少一句 →</button>
                        <button class="ghost-btn compact-btn" type="button" :disabled="!selectedVisualTimingItem.timing?.can_shrink_next || visualTimingAdjusting" @click="adjustEditedTiming('shrink_next')">← 后面少一句</button>
                        <button class="ghost-btn compact-btn" type="button" :disabled="!selectedVisualTimingItem.timing?.can_extend_next || visualTimingAdjusting" @click="adjustEditedTiming('extend_next')">后面多一句 →</button>
                        <button class="ghost-btn compact-btn timing-remove-btn" type="button" :disabled="visualEditor.items.length <= 1 || visualTimingAdjusting" @click="removeEditedTimingPicture">移除这张画面</button>
                      </div>
                    </div>
                  </div>
                </template>
              </section>
              <section class="tts-segment-editor">
                <div class="visual-timing-head">
                  <div>
                    <div class="eyebrow">配音精修</div>
                    <h3>逐句试听与重配音</h3>
                    <p class="muted small">按原始 TTS 断句试听。可单选或多选重配；新时长会自动更新整条配音、字幕及画面时间线。</p>
                  </div>
                  <div class="visual-timing-head-actions">
                    <span v-if="selectedTtsSegmentIndices.length" class="muted small">已选 {{ selectedTtsSegmentIndices.length }} 句</span>
                    <button
                      class="primary-btn compact-btn"
                      type="button"
                      :disabled="!selectedTtsSegmentIndices.length || ttsEditor.task?.status === 'running'"
                      @click="regenerateSelectedTtsSegments"
                    >{{ ttsEditor.task?.status === 'running' ? '重配音中…' : '重配选中句' }}</button>
                  </div>
                </div>
                <div v-if="ttsEditorLoading" class="timing-unavailable">正在读取逐句配音…</div>
                <div v-else-if="!ttsEditor.available" class="timing-unavailable">{{ ttsEditor.message || '该项目没有可编辑的逐句配音。' }}</div>
                <template v-else>
                  <div v-if="ttsEditor.task?.message" class="visual-task-message" :class="ttsEditor.task?.status">{{ ttsEditor.task.message }}</div>
                  <div class="tts-segment-grid">
                    <article
                      v-for="item in ttsEditor.segments"
                      :key="`tts-segment-${item.index}-${item.audio_url}`"
                      class="tts-segment-card"
                      :class="{ selected: selectedTtsSegmentIndices.includes(item.index) }"
                    >
                      <p class="tts-segment-text">{{ item.text }}</p>
                      <div class="tts-segment-controls">
                        <div class="tts-segment-meta">
                          <strong>第 {{ item.index }} 句</strong>
                          <span>{{ Number(item.duration || 0).toFixed(2) }} 秒</span>
                        </div>
                        <label class="tts-segment-select">
                          <span>选中</span>
                          <input v-model="selectedTtsSegmentIndices" type="checkbox" :value="item.index" :disabled="ttsEditor.task?.status === 'running'" />
                        </label>
                      </div>
                      <div class="tts-segment-player">
                        <button
                          class="tts-segment-play"
                          type="button"
                          :title="ttsSegmentPlayingIndex === item.index && ttsSegmentIsPlaying ? '暂停' : '播放本句'"
                          @click="toggleTtsSegmentAudio(item)"
                        >{{ ttsSegmentPlayingIndex === item.index && ttsSegmentIsPlaying ? '❚❚' : '▶' }}</button>
                        <input
                          class="tts-segment-progress"
                          type="range"
                          min="0"
                          :max="Math.max(0.01, ttsSegmentPlayingIndex === item.index ? ttsSegmentDuration : Number(item.duration || 0))"
                          step="0.01"
                          :value="ttsSegmentPlayingIndex === item.index ? ttsSegmentCurrentTime : 0"
                          aria-label="本句播放进度"
                          @input="seekTtsSegmentAudio(item, $event)"
                        />
                      </div>
                    </article>
                  </div>
                </template>
              </section>
              <section class="bgm-panel visual-editor-bgm" :class="{ expanded: visualBgm.enabled }">
                <div class="bgm-panel-head">
                  <div>
                    <div class="eyebrow">后期配乐</div>
                    <h3>更改项目 BGM</h3>
                    <small class="muted">只影响当前画面修改项目；重新渲染时按列表顺序循环播放。</small>
                  </div>
                  <label class="switch-row bgm-switch">
                    <input v-model="visualBgm.enabled" type="checkbox" />
                    <span class="switch-track"><i></i></span>
                    <strong>添加 BGM</strong>
                  </label>
                </div>
                <div v-if="visualBgm.enabled" class="bgm-panel-body">
                  <div class="bgm-track-list">
                    <div v-if="visualBgm.tracks.length" class="bgm-track-list-head">
                      <span>播放列表（按此顺序循环）</span>
                      <button class="ghost-btn compact-btn" type="button" @click="clearBgmTracks('visual')">清空列表</button>
                    </div>
                    <div v-for="(track, index) in visualBgm.tracks" :key="`${track.asset_id || track.archived_filename}-${index}`" class="bgm-track-row">
                      <div class="bgm-track-file">
                        <span class="bgm-order">{{ index + 1 }}</span>
                        <div>
                          <strong>{{ track.name || track.asset_id || track.archived_filename }}</strong>
                          <small class="muted">第 {{ index + 1 }} 首 · {{ formatBgmDuration(track.duration_seconds) }}</small>
                        </div>
                      </div>
                      <label class="bgm-volume-field">
                        <span>音量（dB）</span>
                        <input v-model.number="track.volume_db" type="number" min="-60" max="6" step="1" />
                      </label>
                      <div class="bgm-track-actions">
                        <button class="ghost-btn compact-btn" type="button" :disabled="!bgmTrackUrl(track)" :title="isBgmPreviewing(track) ? '暂停试听' : '播放试听'" @click="toggleBgmPreview(track)">{{ isBgmPreviewing(track) ? 'Ⅱ' : '▶' }}</button>
                        <button class="ghost-btn compact-btn" type="button" :disabled="index === 0" title="上移" @click="moveBgmTrack(visualBgm.tracks, index, -1)">↑</button>
                        <button class="ghost-btn compact-btn" type="button" :disabled="index === visualBgm.tracks.length - 1" title="下移" @click="moveBgmTrack(visualBgm.tracks, index, 1)">↓</button>
                        <button class="ghost-btn compact-btn" type="button" title="移除" @click="removeVisualBgmTrack(index)">×</button>
                      </div>
                    </div>
                    <label class="script-file-picker bgm-upload-picker" :class="{ disabled: visualBgmUploading }">
                      <input
                        type="file"
                        accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,audio/*"
                        :disabled="visualBgmUploading"
                        @change="uploadVisualBgmTrack"
                      />
                      <span>{{ visualBgmUploading ? '上传中…' : (visualBgm.tracks.length ? '添加下一首' : '上传 BGM') }}</span>
                      <strong>MP3 / WAV / M4A / AAC / FLAC / OGG</strong>
                    </label>
                    <small v-if="visualBgmError" class="script-upload-error">{{ visualBgmError }}</small>
                  </div>
                  <div class="bgm-fade-card">
                    <label class="check-row">
                      <input v-model="visualBgm.fade_enabled" type="checkbox" />
                      <span>切换音乐及视频结束时开启渐弱</span>
                    </label>
                    <label>
                      <span>渐弱时长（秒）</span>
                      <input v-model.number="visualBgm.fade_duration" type="number" min="0.1" max="30" step="0.1" :disabled="!visualBgm.fade_enabled" />
                    </label>
                    <small class="muted">设置会随 BGM 一起保存到当前项目。</small>
                  </div>
                </div>
              </section>
              <div class="visual-render-footer">
                <label>渲染设置
                  <select v-model="visualRenderMode">
                    <option value="subtitles">仅渲染字幕版</option>
                    <option value="raw">仅渲染无字幕版</option>
                    <option value="both">双版本渲染</option>
                  </select>
                </label>
                <button class="primary-btn" type="button" :disabled="visualEditor.task?.status === 'running' || visualEditor.has_active_image_tasks || ttsEditor.task?.status === 'running'" @click="renderEditedVideo">
                  重新渲染
                </button>
                <button class="ghost-btn stop-btn" type="button" :disabled="visualEditor.task?.status !== 'running' || visualEditor.task?.action !== 'render'" @click="cancelVisualRender">停止渲染</button>
              </div>
            </template>
          </div>
          <div v-else class="muted small">展开后可选择当前任务或任意历史项目进行画面修改。</div>
        </section>

        <div v-if="visualPreviewItem" class="visual-preview-modal" role="dialog" aria-modal="true" @click.self="visualPreviewItem = null">
          <div class="visual-preview-content">
            <div class="visual-preview-head">
              <strong>{{ visualPreviewItem.id }}</strong>
              <button class="icon-action" type="button" title="关闭预览" @click="visualPreviewItem = null">×</button>
            </div>
            <img :src="visualPreviewItem.image_url" :alt="visualPreviewItem.id" />
          </div>
        </div>

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

        <section v-else-if="activePage === 'module1'" class="module1-page stack">
          <article class="panel module1-panel">
            <div class="panel-head">
              <div>
                <div class="eyebrow">独立工具</div>
                <h2>模块 1 · {{ ttsEngineLabel }}</h2>
                <p class="muted create-summary">只执行断句、配音和原始字幕，不启动 ASR、双 Agent、出图及视频合成。</p>
              </div>
              <div class="module1-engine-control">
                <select v-model="ttsEngine">
                  <option value="indextts2">本地 GPU</option>
                  <option value="cluster">集群 GPU</option>
                  <option value="qwen">Qwen-TTS</option>
                </select>
                <span class="status-chip" :class="((ttsEngine === 'indextts2' && health.tts_online) || (ttsEngine === 'cluster' && cloudReady) || (ttsEngine === 'qwen' && apiKeyStatus.qwen_tts?.configured)) ? 'success' : 'warning'">
                  {{ ttsEngine === 'indextts2' ? (health.tts_online ? '本地已就绪' : '本地未就绪') : (ttsEngine === 'cluster' ? (cloudReady ? '集群已就绪' : '集群未就绪') : (apiKeyStatus.qwen_tts?.configured ? 'Qwen 已就绪' : 'Qwen 未配置')) }}
                </span>
              </div>
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
                      <h3>{{ ttsEngine === 'indextts2' ? '本地参考音色' : (ttsEngine === 'cluster' ? '集群参考音色' : 'Qwen 系统音色') }}</h3>
                    </div>
                  </div>
                  <div v-if="ttsEngine === 'indextts2'" class="script-upload-field">
                    <div class="tts-voice-picker-row">
                      <label class="script-file-picker">
                        <input type="file" accept=".wav,.mp3,.flac,audio/wav,audio/mpeg,audio/flac" @change="uploadTtsVoice" />
                        <span>{{ ttsVoiceUploading ? '上传中' : '浏览音频' }}</span>
                        <strong>{{ ttsVoiceUploadName || '选择 WAV / MP3 / FLAC' }}</strong>
                      </label>
                      <button class="voice-preview-btn" type="button" :disabled="!ttsVoicePreviewUrl" :title="ttsVoicePreviewPlaying ? '暂停试听' : '播放试听'" @click="toggleTtsVoicePreview">
                        {{ ttsVoicePreviewPlaying ? '❚❚' : '▶' }}
                      </button>
                    </div>
                    <small v-if="ttsVoiceUploadError" class="script-upload-error">{{ ttsVoiceUploadError }}</small>
                    <small v-else class="muted">建议 10–30 秒、单人、无音乐的干净人声。</small>
                  </div>
                  <div v-else-if="ttsEngine === 'cluster'" class="module1-cloud-voice">
                    <template v-if="cloudReady">
                      <label>
                        <span>云端音色</span>
                        <select v-model="cloudVoiceModel">
                          <option value="">不选择（自动使用 {{ firstDefaultCloudVoice?.display_name || '第一个默认音色' }}）</option>
                          <optgroup label="云端默认音色">
                            <option v-for="voice in cloudPresetVoiceOptions" :key="`module1-preset:${voice.id}`" :value="`preset:${voice.id}`">{{ voice.display_name || voice.id }}</option>
                          </optgroup>
                          <optgroup v-if="cloudUploadedVoiceOptions.length" label="我上传的音色">
                            <option v-for="voice in cloudUploadedVoiceOptions" :key="`module1-uploaded:${voice.id}`" :value="`uploaded:${voice.id}`">{{ voice.display_name || voice.id }}</option>
                          </optgroup>
                        </select>
                      </label>
                      <div v-if="cloudUploadedVoiceOptions.length" class="uploaded-voice-list compact-uploaded-list">
                        <button v-for="voice in cloudUploadedVoiceOptions" :key="`module1-mine:${voice.id}`" type="button" class="uploaded-voice-item" :class="{ active: selectedCloudVoice?.id === voice.id }" @click="selectCloudVoice(voice)">
                          <span class="voice-avatar">{{ (voice.display_name || '音').slice(0, 1) }}</span>
                          <span><strong>{{ voice.display_name || voice.id }}</strong><small>我的云端音色</small></span>
                        </button>
                      </div>
                      <div class="module1-cloud-upload">
                        <label><span>上传音色名称</span><input v-model.trim="cloudVoiceDisplayName" type="text" maxlength="80" placeholder="例如：我的旁白音色" /></label>
                        <label class="cluster-drop-zone module1-drop-zone">
                          <input type="file" accept=".wav,.mp3,.flac,audio/wav,audio/mpeg,audio/flac" :disabled="cloudVoiceUploading || !cloudVoiceApiAvailable" @change="uploadCloudVoice" />
                          <span class="cluster-upload-icon">＋</span>
                          <strong>{{ cloudVoiceUploading ? '上传中…' : (cloudVoiceApiAvailable ? '选择并上传音频' : '云端暂未开放上传') }}</strong>
                          <small>WAV / MP3 / FLAC · 3–30 秒</small>
                        </label>
                      </div>
                      <small class="muted">可用积分 {{ cloudAccount.credits?.available ?? '-' }}，任务会在云端 GPU 合成后下载到本机。</small>
                    </template>
                    <template v-else>
                      <p class="muted">请先登录集群云端账户并选择音色。</p>
                      <div class="cluster-login-grid compact-cloud-login">
                        <label><span>云端邮箱</span><input v-model.trim="cloudLoginForm.email" type="email" /></label>
                        <label><span>云端密码</span><input v-model="cloudLoginForm.password" type="password" /></label>
                        <button class="primary-btn" type="button" :disabled="cloudBusy" @click="loginCloud">登录集群</button>
                      </div>
                    </template>
                    <small v-if="cloudError" class="script-upload-error">{{ cloudError }}</small>
                  </div>
                  <div v-else class="module1-qwen-voice">
                    <label><span>Qwen 系统音色</span><select v-model="form.qwen_tts_voice"><optgroup v-for="group in qwenVoiceGroups" :key="group.label" :label="group.label"><option v-for="voice in group.voices" :key="voice.value" :value="voice.value">{{ voice.label }}</option></optgroup></select></label>
                    <label><span>配音描述</span><textarea v-model="form.qwen_tts_instructions" rows="4" maxlength="1600"></textarea></label>
                    <small v-if="!apiKeyStatus.qwen_tts?.configured" class="script-upload-error">请先在一键生成页配置 Qwen-TTS API Key。</small>
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
            <div class="log-toolbar compact-log-toolbar">
              <span class="muted small">模块 1 日志</span>
              <button class="ghost-btn compact-btn" type="button" :disabled="diagnosticExporting || !module1Job" @click="exportDiagnosticPackage(module1Job)">
                {{ diagnosticExporting ? '正在导出…' : '导出问题诊断包' }}
              </button>
            </div>
            <pre class="log-view">{{ module1LogText }}</pre>
          </article>
        </section>

        <section v-else class="module1-page stack">
          <article class="panel module1-panel">
            <div class="panel-head">
              <div>
                <div class="eyebrow">独立工具</div>
                <h2>模块 2 · 音频字幕识别</h2>
                <p class="muted create-summary">只运行 Faster-Whisper 字幕识别和可选的模块 2.5 校对，最终输出 SRT 文件。</p>
              </div>
              <span class="status-chip success">不生成画面和视频</span>
            </div>

            <div class="module1-layout">
              <div class="module1-copy-column">
                <label>
                  <span>字幕任务名称</span>
                  <input v-model.trim="subtitleForm.project_name" type="text" maxlength="80" />
                </label>
                <div class="script-upload-field">
                  <span>上传需要识别的音频或视频</span>
                  <label class="script-file-picker">
                    <input type="file" accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,.mp4,.mov,.mkv,.webm,.avi,.m4v,audio/*,video/*" @change="uploadSubtitleAudio" />
                    <span>{{ subtitleAudioUploading ? '上传中' : '浏览音频/视频' }}</span>
                    <strong>{{ subtitleAudioName || '选择音频或 MP4 / MOV / MKV 视频' }}</strong>
                  </label>
                  <div v-if="subtitleAudioError" class="board-error">{{ subtitleAudioError }}</div>
                </div>
                <div class="muted small">视频会先自动提取音轨；识别会保留原始时间轴，长媒体会在后台任务中顺序处理。</div>
              </div>

              <div class="module1-settings-column">
                <div class="tts-parameter-panel subtitle-options">
                  <label class="checkbox-row">
                    <input v-model="subtitleForm.use_correction" type="checkbox" />
                    <span>使用字幕校对（模块 2.5）</span>
                  </label>
                  <p class="muted small">有参考文案时按文案逐段对齐；没有参考文案时自动调用语言模型修正 ASR 错别字、标点和同音字。</p>
                </div>
                <div v-if="subtitleForm.use_correction" class="script-upload-field">
                  <span>可选：上传参考文案</span>
                  <label class="script-file-picker">
                    <input type="file" accept=".txt,.md,text/plain,text/markdown" @change="loadSubtitleReference" />
                    <span>浏览文案</span>
                    <strong>{{ subtitleReferenceName || '不上传则使用语言模型校对' }}</strong>
                  </label>
                  <div v-if="subtitleReferenceError" class="board-error">{{ subtitleReferenceError }}</div>
                </div>
                <div v-if="subtitleForm.use_correction && !subtitleForm.reference_text" class="muted small">
                  当前将使用语言模型校对。语言模型或通用 API Key 未配置时，任务会提示你先在左侧填写。
                </div>
                <div class="inline-actions module1-actions">
                  <button class="ghost-btn stop-btn" type="button" :disabled="!subtitleJobRunning" @click="cancelSubtitleJob">停止识别</button>
                  <button class="primary-btn" type="button" :disabled="submittingSubtitle || !canSubmitSubtitle" @click="submitSubtitleJob">
                    {{ submittingSubtitle ? '正在提交...' : '开始识别字幕' }}
                  </button>
                </div>
              </div>
            </div>
          </article>

          <article class="panel subtitle-style-panel">
            <div class="panel-head">
              <div>
                <div class="eyebrow">字幕后处理</div>
                <h2>添加字幕</h2>
                <p class="muted create-summary">不重新识别字幕：直接把本次 SRT 烧录进原视频；若上传的是音频，则自动生成深色背景字幕视频。</p>
              </div>
              <label class="inline-switch">
                <input v-model="subtitleAddEnabled" type="checkbox" @change="subtitleAddEnabled && loadSubtitleFonts()" />
                <span class="switch-track"><span></span></span>
                <strong>启动字幕添加</strong>
              </label>
            </div>
            <div v-if="subtitleAddEnabled" class="subtitle-style-body">
              <div class="subtitle-style-grid">
                <button v-for="style in subtitleStyleOptions" :key="style.key" class="subtitle-style-option" :class="[{ active: subtitleRenderForm.style === style.key }, style.key]" type="button" @click="subtitleRenderForm.style = style.key">
                  <span class="subtitle-style-sample">先说在前头</span>
                  <small>{{ style.label }}</small>
                </button>
              </div>
              <label class="stack subtitle-font-field">
                <span>字幕字体（本机字体）</span>
                <select v-model="subtitleRenderForm.font_name" :disabled="subtitleFontsLoading">
                  <option v-if="!subtitleFonts.length" value="Microsoft YaHei">Microsoft YaHei</option>
                  <option v-for="font in subtitleFonts" :key="font" :value="font">{{ font }}</option>
                </select>
              </label>
              <div class="inline-actions">
                <button class="ghost-btn stop-btn" type="button" :disabled="!subtitleJobRunning" @click="cancelSubtitleJob">停止渲染</button>
                <button class="primary-btn" type="button" :disabled="!canRenderSubtitleVideo" @click="renderSubtitleVideo">{{ subtitleJobRunning ? '正在渲染…' : '添加字幕并渲染视频' }}</button>
              </div>
              <small v-if="!subtitleJob?.artifacts?.subtitle" class="muted">请先完成一次字幕识别，生成 SRT 后再添加字幕。</small>
              <div v-if="subtitleRenderMessage" class="muted small">{{ subtitleRenderMessage }}</div>
            </div>
          </article>

          <article class="panel subtitle-bgm-standalone">
            <div class="panel-head">
              <div>
                <div class="eyebrow">音频后处理</div>
                <h2>添加 BGM</h2>
                <p class="muted create-summary">为上方即将渲染的字幕视频添加背景音乐，按上传顺序进行列表循环。</p>
              </div>
              <label class="inline-switch">
                <input v-model="subtitleRenderForm.bgm_enabled" type="checkbox" />
                <span class="switch-track"><span></span></span>
                <strong>启动 BGM 添加</strong>
              </label>
            </div>
            <div v-if="subtitleRenderForm.bgm_enabled" class="bgm-panel-body subtitle-bgm-body">
              <div class="bgm-track-list">
                <div v-if="subtitleRenderForm.bgm_tracks.length" class="bgm-track-list-head">
                  <span>播放列表（按此顺序循环）</span>
                  <button class="ghost-btn compact-btn" type="button" @click="clearBgmTracks('subtitle')">清空列表</button>
                </div>
                <div v-for="(track, index) in subtitleRenderForm.bgm_tracks" :key="`${track.asset_id}-${index}`" class="bgm-track-row">
                  <div class="bgm-track-file">
                    <span class="bgm-order">{{ index + 1 }}</span>
                    <div>
                      <strong>{{ track.name || track.asset_id }}</strong>
                      <small class="muted">第 {{ index + 1 }} 首 · {{ formatBgmDuration(track.duration_seconds) }}</small>
                    </div>
                  </div>
                  <label class="bgm-volume-field">
                    <span>音量（dB）</span>
                    <input v-model.number="track.volume_db" type="number" min="-60" max="6" step="1" />
                  </label>
                  <div class="bgm-track-actions">
                    <button class="ghost-btn compact-btn" type="button" :disabled="!bgmTrackUrl(track)" :title="isBgmPreviewing(track) ? '暂停试听' : '播放试听'" @click="toggleBgmPreview(track)">{{ isBgmPreviewing(track) ? 'Ⅱ' : '▶' }}</button>
                    <button class="ghost-btn compact-btn" type="button" :disabled="index === 0" title="上移" @click="moveBgmTrack(subtitleRenderForm.bgm_tracks, index, -1)">↑</button>
                    <button class="ghost-btn compact-btn" type="button" :disabled="index === subtitleRenderForm.bgm_tracks.length - 1" title="下移" @click="moveBgmTrack(subtitleRenderForm.bgm_tracks, index, 1)">↓</button>
                    <button class="ghost-btn compact-btn" type="button" title="移除" @click="removeSubtitleBgmTrack(index)">×</button>
                  </div>
                </div>
                <label class="script-file-picker bgm-upload-picker" :class="{ disabled: subtitleBgmUploading }">
                  <input
                    type="file"
                    accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,audio/*"
                    :disabled="subtitleBgmUploading"
                    @change="uploadSubtitleBgmTrack"
                  />
                  <span>{{ subtitleBgmUploading ? '上传中…' : (subtitleRenderForm.bgm_tracks.length ? '添加下一首' : '上传 BGM') }}</span>
                  <strong>MP3 / WAV / M4A / AAC / FLAC / OGG</strong>
                </label>
                <small v-if="subtitleBgmError" class="script-upload-error">{{ subtitleBgmError }}</small>
              </div>
              <div class="bgm-fade-card">
                <label class="check-row">
                  <input v-model="subtitleRenderForm.bgm_fade_enabled" type="checkbox" />
                  <span>切换音乐及视频结束时开启渐弱</span>
                </label>
                <label>
                  <span>渐弱时长（秒）</span>
                  <input
                    v-model.number="subtitleRenderForm.bgm_fade_duration"
                    type="number"
                    min="0.1"
                    max="30"
                    step="0.1"
                    :disabled="!subtitleRenderForm.bgm_fade_enabled"
                  />
                </label>
                <small class="muted">默认 1 秒；关闭后音乐按顺序直接衔接。</small>
              </div>
            </div>
          </article>

          <article class="panel progress-panel">
            <div class="panel-head">
              <div>
                <div class="eyebrow">字幕任务</div>
                <h2>{{ subtitleJob?.message || '等待上传音频并开始识别' }}</h2>
              </div>
              <span class="progress-percent">{{ subtitleJob?.progress || 0 }}%</span>
            </div>
            <div class="progress-track"><span :style="{ width: `${subtitleJob?.progress || 0}%` }"></span></div>
            <div class="log-toolbar compact-log-toolbar">
              <span class="muted small">字幕任务日志</span>
              <button class="ghost-btn compact-btn" type="button" :disabled="diagnosticExporting || !subtitleJob" @click="exportDiagnosticPackage(subtitleJob)">
                {{ diagnosticExporting ? '正在导出…' : '导出问题诊断包' }}
              </button>
            </div>
            <pre class="log-view">{{ subtitleLogText }}</pre>
            <div v-if="subtitleJob?.artifacts?.subtitle" class="subtitle-output-result">
              <div>
                <div class="artifact-label">最终产物</div>
                <a class="artifact-value" :href="subtitleJob.artifacts.subtitle" download>final_short.srt</a>
                <div class="muted small">点击文件名可下载 SRT；也可直接在资源管理器中打开所在位置。</div>
              </div>
              <button class="ghost-btn" type="button" @click="openSubtitleOutputFolder">
                打开产物所在目录
              </button>
            </div>
            <div v-if="subtitleJob?.artifacts?.subtitle_video" class="subtitle-output-result">
              <div>
                <div class="artifact-label">带字幕视频</div>
                <video class="subtitle-preview-video" :src="subtitleJob.artifacts.subtitle_video" controls></video>
              </div>
              <a class="ghost-btn" :href="subtitleJob.artifacts.subtitle_video" download="带字幕视频.mp4">下载视频</a>
            </div>
            <div v-if="folderOpenMessage" class="folder-open-message">{{ folderOpenMessage }}</div>
          </article>
        </section>
      </section>
    </main>

    <div v-if="preflightOpen" class="preflight-overlay" role="dialog" aria-modal="true" aria-labelledby="preflight-title">
      <section class="preflight-dialog">
        <div class="preflight-head">
          <div>
            <div class="eyebrow">启动前体检</div>
            <h2 id="preflight-title">{{ preflightResult?.message || '正在检查本次任务…' }}</h2>
            <p class="muted">检查结果只针对当前填写的参数和本机环境，不会消耗生图次数。</p>
          </div>
          <button class="ghost-btn compact-btn" type="button" :disabled="preflightRunning" @click="closePreflight">关闭</button>
        </div>
        <div v-if="preflightRunning" class="preflight-loading">
          <span class="preflight-spinner"></span>
          <strong>正在检查 API、TTS、素材、渲染环境和磁盘空间…</strong>
        </div>
        <div v-else class="preflight-body">
          <div class="preflight-summary">
            <span class="preflight-count passed">✓ {{ preflightPassedCount }} 项通过</span>
            <span v-if="preflightResult?.warning_count" class="preflight-count warning">! {{ preflightResult.warning_count }} 项提醒</span>
            <span v-if="preflightResult?.error_count" class="preflight-count error">× {{ preflightResult.error_count }} 项必须处理</span>
          </div>
          <div class="preflight-list">
            <article v-for="item in preflightResult?.items || []" :key="item.id" class="preflight-item" :class="item.status">
              <span class="preflight-item-icon">{{ item.status === 'passed' ? '✓' : (item.status === 'warning' ? '!' : '×') }}</span>
              <div>
                <strong>{{ item.label }}</strong>
                <p>{{ item.message }}</p>
              </div>
            </article>
          </div>
          <div class="preflight-actions">
            <button class="primary-btn" type="button" @click="closePreflight">完成检测</button>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { api } from './api'

const VISUAL_PROMPT_FULL_STORAGE_KEY = 'visual_prompt_system_story_v3'
const LOCKED_GENERAL_AGENT2_PROTOCOL = `你是通用视频的分镜视觉导演，也是本流水线的 Agent 2。

【输出格式】
- 只输出严格 JSON 数组，不要 Markdown，不要解释。
- 每项必须包含 includes_slides（slide_id 数组）和 image_prompt（中文生图提示词）。
- 严格使用系统给出的固定 slide 分组；每组生成一张 2:1 横版视频画面，覆盖全部 slide_id，不遗漏、重复或合并分组。

`
const LEGACY_LOCKED_GENERAL_AGENT2_PROTOCOL = `你是通用视频的分镜视觉导演，也是本流水线的 Agent 2。

【输出格式】
- 只输出严格 JSON 数组，不要 Markdown，不要解释。
- 每项必须包含 includes_slides（slide_id 数组）和 image_prompt（中文生图提示词）。

【分镜规则】
- 严格使用系统给出的固定 slide 分组；每组生成一张 2:1 横版视频画面，覆盖全部 slide_id，不遗漏、重复或合并分组。`
const EDITABLE_GENERAL_AGENT2_PREFIX = '【分镜规则】'
const AGENT2_DIRECTOR_THEME_STORAGE_KEY = 'agent2_director_theme_v1'
const AGENT2_DIRECTOR_THEME_DEFAULTS = {
  urban_suspense: '惊悚漫画',
  science_explainer: '科普科技口播视频',
  pure_science: '跨学科严肃科普与知识可视化视频',
  general: '通用视频',
}
const VISUAL_PROMPT_STYLE_STORAGE_KEY = 'visual_prompt_style_story_v3'
const GLOBAL_CHARACTER_STORAGE_KEY = 'global_character_prompt_v1'
const STORY_ENVIRONMENT_STORAGE_KEY = 'story_environment_prompt_v1'
const AGENT0_PROMPT_STORAGE_KEY = 'agent0_prompt_system_v1'
const AGENT1_PROMPT_STORAGE_KEY = 'agent1_prompt_system_v1'
const VISUAL_PROMPT_MODE_STORAGE_KEY = 'visual_prompt_mode_v2'
const CONTENT_MODE_STORAGE_KEY = 'content_mode_v1'
const VISUAL_PACING_STORAGE_KEY = 'visual_pacing_v1'
const VISUAL_PACING_DEFAULTS = {
  urban_suspense: { min: 6, target: 8, max: 12, slides: 6 },
  science_explainer: { min: 7, target: 9, max: 14, slides: 6 },
  pure_science: { min: 7, target: 10, max: 16, slides: 8 },
  general: { min: 6, target: 8, max: 12, slides: 6 },
}
const FALLBACK_CONTENT_MODES = {
  urban_suspense: {
    label: '都市惊悚',
    description: '人物、线索与悬念连续的阴森漫画故事',
  },
  science_explainer: {
    label: '口播科普',
    description: '红围巾短发少女的清晰科教漫画',
  },
  pure_science: {
    label: '纯科普',
    description: '无固定人物的跨学科严肃知识可视化',
    default_style: '跨学科严肃科普与现代教材级知识可视化，准确、克制、清晰；允许必要术语、公式、坐标、地图、时间轴、结构标签和流程示意。',
    default_character: '',
    default_system: '',
  },
  general: {
    label: '通用自定义',
    description: '自由定义画风与人物的通用视频模式',
    default_style: '通用横版叙事画面：请填写希望的画风、色彩、质感与镜头气质。',
    default_character: '',
    default_system: '',
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
const ttsVoicePreviewUrl = ref('')
const ttsVoicePreviewPlaying = ref(false)
let ttsVoicePreviewAudio = null
const stepAudioPlayer = ref(null)
const stepAudioPlaying = ref(false)
const stepAudioCurrentTime = ref(0)
const stepAudioDuration = ref(0)
const savingStepAudio = ref(false)
const stepAudioSaveMessage = ref('')
const retryingTts = ref(false)
const folderOpenMessage = ref('')
const visualEditorOpen = ref(false)
const visualEditorLoading = ref(false)
const visualEditor = ref({ items: [], task: { status: 'idle', message: '' }, version: 0 })
const visualEditorProjects = ref([])
const visualEditorProjectId = ref('')
const visualEditorPage = ref(1)
const visualTimingSelectedId = ref('')
const selectedVisualTimingHistory = ref('')
const visualTimingAdjusting = ref(false)
const ttsEditor = ref({ available: false, message: '', segments: [], task: { status: 'idle', message: '' } })
const ttsEditorLoading = ref(false)
const selectedTtsSegmentIndices = ref([])
const ttsSegmentPlayingIndex = ref(0)
const ttsSegmentIsPlaying = ref(false)
const ttsSegmentCurrentTime = ref(0)
const ttsSegmentDuration = ref(0)
let ttsSegmentAudio = null
const visualSelfReferenceMacroId = ref('')
const visualReferenceUploads = ref([])
const visualReferenceUploading = ref(false)
const visualReferenceOwnerMacroId = ref('')
const VISUAL_EDITOR_PAGE_SIZE = 24
const visualPreviewItem = ref(null)
const visualRenderMode = ref('both')
const visualBgmUploading = ref(false)
const visualBgmError = ref('')
const visualBgm = reactive({
  enabled: false,
  tracks: [],
  fade_enabled: false,
  fade_duration: 1,
})
const submitting = ref(false)
const preflightRunning = ref(false)
const preflightOpen = ref(false)
const preflightResult = ref(null)
const submittingModule1 = ref(false)
const submittingSubtitle = ref(false)
const cancellingGeneration = ref(false)
const resumingGeneration = ref(false)
const health = ref({ ok: false, tts_online: false })
const settings = ref({ scripts: [], tts: { voices: [], emotions: [], defaults: {} } })
const session = ref({ user: null, auth_mode: 'account', mysql: {} })
const authError = ref('')
const activeJob = ref(null)
const followLiveJob = ref(true)
const module1Job = ref(null)
const subtitleJob = ref(null)
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
const diagnosticExporting = ref(false)
const diagnosticMessage = ref('')
const generationSubmitMessage = ref('')
const apiKeyStatus = ref({ language: {}, image: {}, common: {}, qwen_tts: {} })
const apiKeyMessage = ref('')
const apiKeyEditing = reactive({ language: false, image: false, common: false, qwen_tts: false })
const apiKeyRuntimeErrors = reactive({ language: '', image: '', common: '', qwen_tts: '' })
const savingApiKeys = ref(false)
const savingQwenTtsKey = ref(false)
const qwenTtsKeyMessage = ref('')
const ttsEngine = ref('indextts2')
const cloudSession = ref({ configured: false, authenticated: false, user: null, base_url: '' })
const cloudAccount = ref({ credits: {}, quota: {} })
const cloudVoices = ref([])
const cloudVoiceLimits = ref({})
const cloudQuote = ref({})
const cloudBusy = ref(false)
const cloudQuoteLoading = ref(false)
const cloudVoiceUploading = ref(false)
const cloudVoiceDisplayName = ref('')
const cloudVoiceApiAvailable = ref(true)
const cloudVoicePreviewLoading = ref(false)
const cloudVoicePreviewPlayingId = ref('')
const cloudError = ref('')
const cloudMessage = ref('')
const cloudLoginForm = reactive({ email: '', password: '' })
const cloudLoginOpen = ref(false)
let cloudVoicePreviewAudio = null
// Qwen 官方非实时 TTS 系统音色。原生 select 在选项较多时会自动提供滚动，
// 分组同时明确哪些声音可搭配 qwen3-tts-instruct-flash 的“配音描述”。
const qwenVoiceGroups = [
  {
    label: '推荐叙述 · 支持配音描述',
    voices: [
      { value: 'Elias', label: '墨讲师 · 女性讲述感（默认）', supportsInstructions: true },
      { value: 'Eldric Sage', label: '沧明子 · 沉稳睿智老者', supportsInstructions: true },
      { value: 'Vincent', label: '田叔 · 沙哑烟嗓男声', supportsInstructions: true },
      { value: 'Neil', label: '阿闻 · 新闻主持男声', supportsInstructions: true },
      { value: 'Arthur', label: '徐大爷 · 沧桑老者', supportsInstructions: true },
      { value: 'Seren', label: '小婉 · 舒缓女声', supportsInstructions: true },
      { value: 'Maia', label: '四月 · 知性温柔女声', supportsInstructions: true },
      { value: 'Serena', label: '苏瑶 · 温柔自然女声', supportsInstructions: true },
    ],
  },
  {
    label: '其他普通话 · 支持配音描述',
    voices: [
      { value: 'Cherry', label: '芊悦 · 阳光亲切女声', supportsInstructions: true },
      { value: 'Ethan', label: '晨煦 · 温暖活力男声', supportsInstructions: true },
      { value: 'Chelsie', label: '千雪 · 二次元女友', supportsInstructions: true },
      { value: 'Momo', label: '茉兔 · 撒娇搞怪女声', supportsInstructions: true },
      { value: 'Vivian', label: '十三 · 可爱小暴躁女声', supportsInstructions: true },
      { value: 'Moon', label: '月白 · 率性帅气男声', supportsInstructions: true },
      { value: 'Kai', label: '凯 · 温柔耳语男声', supportsInstructions: true },
      { value: 'Nofish', label: '不吃鱼 · 设计师男声', supportsInstructions: true },
      { value: 'Bella', label: '萌宝 · 萝莉女声', supportsInstructions: true },
      { value: 'Mia', label: '乖小妹 · 温顺女声', supportsInstructions: true },
      { value: 'Mochi', label: '沙小弥 · 小大人男声', supportsInstructions: true },
      { value: 'Bellona', label: '燕铮莺 · 洪亮鲜活女声', supportsInstructions: true },
      { value: 'Bunny', label: '萌小姬 · 小萝莉女声', supportsInstructions: true },
      { value: 'Nini', label: '邻家妹妹 · 亲切女声', supportsInstructions: true },
      { value: 'Pip', label: '顽屁小孩 · 男童声', supportsInstructions: true },
      { value: 'Stella', label: '少女阿月 · 少女声', supportsInstructions: true },
    ],
  },
  {
    label: '国际音色 · 仅基础合成（不填配音描述）',
    voices: [
      { value: 'Jennifer', label: '詹妮弗 · 电影感美语女声', supportsInstructions: false },
      { value: 'Ryan', label: '甜茶 · 戏感美语男声', supportsInstructions: false },
      { value: 'Katerina', label: '卡捷琳娜 · 成熟御姐', supportsInstructions: false },
      { value: 'Aiden', label: '艾登 · 美语大男孩', supportsInstructions: false },
      { value: 'Bodega', label: '博德加 · 西班牙语男声', supportsInstructions: false },
      { value: 'Sonrisa', label: '索尼莎 · 拉美女声', supportsInstructions: false },
      { value: 'Alek', label: '阿列克 · 俄语男声', supportsInstructions: false },
      { value: 'Dolce', label: '多尔切 · 意大利语男声', supportsInstructions: false },
      { value: 'Sohee', label: '素熙 · 韩语女声', supportsInstructions: false },
      { value: 'Ono Anna', label: '小野杏 · 日语女声', supportsInstructions: false },
      { value: 'Lenn', label: '莱恩 · 德语男声', supportsInstructions: false },
      { value: 'Emilien', label: '埃米尔安 · 法语男声', supportsInstructions: false },
      { value: 'Andre', label: '安德雷 · 磁性沉稳男声', supportsInstructions: false },
      { value: 'Radio Gol', label: '拉迪奥·戈尔 · 男声', supportsInstructions: false },
    ],
  },
  {
    label: '方言音色 · 仅基础合成（不填配音描述）',
    voices: [
      { value: 'Jada', label: '上海-阿珍 · 上海女声', supportsInstructions: false },
      { value: 'Dylan', label: '北京-晓东 · 北京男声', supportsInstructions: false },
      { value: 'Li', label: '南京-老李 · 南京男声', supportsInstructions: false },
      { value: 'Marcus', label: '陕西-秦川 · 陕西男声', supportsInstructions: false },
      { value: 'Roy', label: '闽南-阿杰 · 闽南男声', supportsInstructions: false },
      { value: 'Peter', label: '天津-李彼得 · 天津男声', supportsInstructions: false },
      { value: 'Sunny', label: '四川-晴儿 · 四川女声', supportsInstructions: false },
      { value: 'Eric', label: '四川-程川 · 四川男声', supportsInstructions: false },
      { value: 'Rocky', label: '粤语-阿强 · 粤语男声', supportsInstructions: false },
      { value: 'Kiki', label: '粤语-阿清 · 粤语女声', supportsInstructions: false },
    ],
  },
]
let apiKeyStatusLoaded = false
let timer = null
let visualEditorTaskTimer = null
let ttsEditorTaskTimer = null
let completionAudioContext = null
let completionFlashTimer = null
let completionFlashState = false
let completionFaviconLink = null
let completionFaviconCreated = false
let originalFaviconHref = ''
let originalDocumentTitle = ''
let lastCompletionAlertAt = 0
const MAX_SCRIPT_FILE_SIZE = 2 * 1024 * 1024
const MAX_SCRIPT_CHARACTERS = 12_000

const loginForm = reactive({
  email: '',
  password: '',
})
const registerForm = reactive({
  name: '',
  email: '',
  password: '',
})
const subtitleForm = reactive({
  project_name: '字幕识别任务',
  source_audio_id: '',
  use_correction: true,
  reference_text: '',
})
const subtitleAudioName = ref('')
const subtitleAudioError = ref('')
const subtitleAudioUploading = ref(false)
const subtitleReferenceName = ref('')
const subtitleReferenceError = ref('')
const subtitleAddEnabled = ref(false)
const subtitleFonts = ref([])
const subtitleFontsLoading = ref(false)
const subtitleRenderMessage = ref('')
const subtitleBgmUploading = ref(false)
const subtitleBgmError = ref('')
const subtitleRenderForm = reactive({
  style: 'navy_bg_white',
  font_name: 'Microsoft YaHei',
  bgm_enabled: false,
  bgm_tracks: [],
  bgm_fade_enabled: false,
  bgm_fade_duration: 1,
})
const subtitleStyleOptions = [
  { key: 'black_white_outline', label: '黑字白描边' },
  { key: 'white_black_outline', label: '白字黑描边' },
  { key: 'yellow_bg_black', label: '黄底黑字' },
  { key: 'white_bg_black', label: '白底黑字' },
  { key: 'navy_bg_white', label: '默认成片样式' },
]
const referenceImageNames = ref([])
const protagonistReferenceImageError = ref('')
const protagonistReferenceUploading = ref(false)
const apiKeyForm = reactive({
  language_provider: 'gemini',
  language_api_key: '',
  image_api_key: '',
  image_api_keys: [],
  common_api_key: '',
  common_api_keys: [],
  qwen_tts_api_key: '',
})
const languageProviderOptions = computed(() => apiKeyStatus.value.language?.providers || [
  { value: 'gemini', label: 'Google Gemini', configured: false },
  { value: 'runninghub', label: '第三方兼容接口', configured: false },
  { value: 'deepseek', label: 'DeepSeek', configured: false },
  { value: 'openai', label: 'OpenAI GPT', configured: false },
  { value: 'kimi', label: 'Kimi', configured: false },
  { value: 'glm', label: '智谱 GLM', configured: false },
])
const currentLanguageProvider = computed(() => (
  languageProviderOptions.value.find((item) => item.value === apiKeyForm.language_provider)
  || languageProviderOptions.value[0]
  || { value: 'gemini', label: 'Google Gemini', configured: false }
))
const currentLanguageProviderLabel = computed(() => currentLanguageProvider.value.label || '语言模型')
const apiKeyEditorVisible = computed(() => (
  !form.use_cloud_image_pool
  && ['language', 'image', 'common'].some((kind) => apiKeyFieldOpen(kind))
))
const parameterPresets = ref([])
const selectedParameterPreset = ref('')
const loadingParameterPresets = ref(false)
const savingParameterPreset = ref(false)
const deletingParameterPreset = ref(false)
const parameterPresetMessage = ref('')
const agentPromptPresets = ref([])
const selectedAgentPromptPreset = ref('')
const loadingAgentPromptPresets = ref(false)
const savingAgentPromptPreset = ref(false)
const bgmUploading = ref(false)
const bgmError = ref('')
const bgmPreviewTrack = ref(null)
let bgmPreviewAudio = null
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
  cluster_voice_type: 'preset',
  // Empty is an intentional UI state: submission resolves it to the first
  // displayed preset instead of forcing the user to make a selection.
  cluster_voice_id: '',
  qwen_tts_instructions: '',
  qwen_tts_voice: 'Elias',
  visual_backend: 'poster',
  use_cloud_image_pool: false,
  video_render_variant: 'both',
  bgm_enabled: false,
  bgm_tracks: [],
  bgm_fade_enabled: false,
  bgm_fade_duration: 1,
  step_mode: false,
  visual_prompt_mode: 'simple',
  visual_pacing_preset: 'auto',
  visual_min_duration: 6,
  visual_target_duration: 8,
  visual_max_duration: 12,
  visual_max_slides: 6,
  visual_style_prompt: '',
  global_character_prompt: '',
  protagonist_reference_image_id: '',
  reference_image_ids: [],
  story_environment_prompt: '',
  visual_prompt_system: '',
  agent0_prompt_system: '',
  agent1_prompt_system: '',
  agent2_director_theme: '',
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
const visualMediumWarning = computed(() => {
  if (form.visual_prompt_mode === 'full') return ''
  const style = String(form.visual_style_prompt || '').trim()
  if (!style) return ''
  const mediumMarkers = [
    '插画', '漫画', '绘本', '手绘', '条漫', '平涂', '厚涂', '水彩', '国画', '水墨', '油画', '素描', '版画',
    '二维', '2D', '动画', '三维', '3D', 'CG', '渲染',
    '摄影', '真人', '实拍', '照片级', '纪实', '剧照',
  ]
  if (mediumMarkers.some((marker) => style.toLowerCase().includes(marker.toLowerCase()))) return ''
  return '当前画风只描述了氛围，没有指定插画、漫画、真人摄影等视觉媒介，长视频可能出现风格漂移。建议补充一种明确媒介。'
})
const defaultAgentPromptPresets = computed(() => agentPromptPresets.value.filter((preset) => preset.kind === 'default'))
const userAgentPromptPresets = computed(() => agentPromptPresets.value.filter((preset) => preset.kind !== 'default'))
const activeAgent2LockedProtocol = computed(() => {
  if (form.content_mode === 'urban_suspense') {
    const theme = String(form.agent2_director_theme || AGENT2_DIRECTOR_THEME_DEFAULTS.urban_suspense).trim()
      || AGENT2_DIRECTOR_THEME_DEFAULTS.urban_suspense
    return `你是鬼故事与都市小说视频的${theme}分镜导演。

【输出格式】
- 只输出严格 JSON 数组，不要 Markdown，不要解释。
- 每项必须包含 includes_slides（slide_id 数组）和 image_prompt（中文生图提示词）。

【分镜规则】
- 严格按照系统为本次任务提供的固定 slide 分组，每组生成一张 2:1 横版电影感漫画分镜。`
  }
  if (form.content_mode === 'science_explainer') {
    const theme = String(form.agent2_director_theme || AGENT2_DIRECTOR_THEME_DEFAULTS.science_explainer).trim()
      || AGENT2_DIRECTOR_THEME_DEFAULTS.science_explainer
    return `你是${theme}的分镜视觉导演，也是本流水线的 Agent 2。

【输出格式】
- 只输出严格 JSON 数组，不要 Markdown，不要解释。
- 每项必须包含 includes_slides（slide_id 数组）和 image_prompt（中文生图提示词）。

【分镜规则】
- 严格按照系统提供的固定 slide 分组，每组生成一张 2:1 横版解说漫画。`
  }
  if (form.content_mode === 'pure_science') {
    const theme = String(form.agent2_director_theme || AGENT2_DIRECTOR_THEME_DEFAULTS.pure_science).trim()
      || AGENT2_DIRECTOR_THEME_DEFAULTS.pure_science
    return `你是${theme}的分镜视觉导演，也是本流水线的 Agent 2。

【输出格式】
- 只输出严格 JSON 数组，不要 Markdown，不要解释。
- 每项必须包含 includes_slides（slide_id 数组）和 image_prompt（中文生图提示词）。

【分镜规则】
- 严格按照系统提供的固定 slide 分组，每组生成一张 2:1 横版科学画面，不遗漏、重复、合并或人为限制海报数量。`
  }
  const theme = String(form.agent2_director_theme || AGENT2_DIRECTOR_THEME_DEFAULTS.general).trim()
    || AGENT2_DIRECTOR_THEME_DEFAULTS.general
  return LOCKED_GENERAL_AGENT2_PROTOCOL.replace(
    '你是通用视频的分镜视觉导演，也是本流水线的 Agent 2。',
    `你是${theme}的分镜视觉导演，也是本流水线的 Agent 2。`,
  )
})
const editableVisualPromptSystem = computed({
  get() {
    const prompt = String(form.visual_prompt_system || '')
    const locked = activeAgent2LockedProtocol.value
    let editable = prompt.startsWith(locked)
      ? prompt.slice(locked.length).replace(/^\s+/, '')
      : prompt
    if (form.content_mode === 'general' && prompt.startsWith(LEGACY_LOCKED_GENERAL_AGENT2_PROTOCOL)) {
      editable = prompt.slice(LEGACY_LOCKED_GENERAL_AGENT2_PROTOCOL.length).replace(/^\s+/, '')
    }
    if (form.content_mode !== 'general') return editable
    return editable.startsWith(EDITABLE_GENERAL_AGENT2_PREFIX)
      ? editable
      : `${EDITABLE_GENERAL_AGENT2_PREFIX}\n${editable}`.trimEnd()
  },
  set(value) {
    let editable = String(value || '').trim()
    if (form.content_mode === 'general' && !editable.startsWith(EDITABLE_GENERAL_AGENT2_PREFIX)) {
      editable = `${EDITABLE_GENERAL_AGENT2_PREFIX}${editable ? `\n${editable}` : ''}`
    }
    form.visual_prompt_system = `${activeAgent2LockedProtocol.value}${editable ? `\n\n${editable}` : ''}`
  },
})
const agent2DirectorThemeModel = computed({
  get: () => String(form.agent2_director_theme || ''),
  set(value) {
    const editable = editableVisualPromptSystem.value
    form.agent2_director_theme = String(value || '').trim()
    editableVisualPromptSystem.value = editable
    rememberVisualPrompt()
  },
})
const agent2DirectorThemePlaceholder = computed(() => `例如：${AGENT2_DIRECTOR_THEME_DEFAULTS[form.content_mode] || '电影叙事视频'}`)

const visualEditorPageCount = computed(() => Math.max(1, Math.ceil(visualEditor.value.items.length / VISUAL_EDITOR_PAGE_SIZE)))
const visibleVisualEditorItems = computed(() => {
  const start = (visualEditorPage.value - 1) * VISUAL_EDITOR_PAGE_SIZE
  return visualEditor.value.items.slice(start, start + VISUAL_EDITOR_PAGE_SIZE)
})
const selectedVisualTimingItem = computed(() => (
  visualEditor.value.items.find((item) => item.id === visualTimingSelectedId.value)
  || visualEditor.value.items.find((item) => item.timing)
  || null
))
const visualReferenceSummary = computed(() => {
  const parts = []
  if (visualSelfReferenceMacroId.value) parts.push(`图1 ${visualSelfReferenceMacroId.value}`)
  const offset = visualSelfReferenceMacroId.value ? 2 : 1
  visualReferenceUploads.value.forEach((asset, index) => parts.push(`图${offset + index} ${asset.name}`))
  if (!parts.length) return ''
  return `${visualReferenceOwnerMacroId.value || '当前卡片'}：${parts.join(' · ')}`
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

function visualPacingDefaults(mode = form.content_mode) {
  return VISUAL_PACING_DEFAULTS[mode] || VISUAL_PACING_DEFAULTS.urban_suspense
}

function applyVisualPacing(mode = form.content_mode) {
  const defaults = visualPacingDefaults(mode)
  let saved = null
  try {
    saved = JSON.parse(window.localStorage.getItem(modeStorageKey(VISUAL_PACING_STORAGE_KEY, mode)) || 'null')
  } catch {
    saved = null
  }
  form.visual_pacing_preset = ['auto', 'slow', 'standard', 'fast', 'custom'].includes(saved?.preset)
    ? saved.preset
    : 'auto'
  form.visual_min_duration = Number(saved?.min) || defaults.min
  form.visual_target_duration = Number(saved?.target) || defaults.target
  form.visual_max_duration = Number(saved?.max) || defaults.max
  form.visual_max_slides = Number(saved?.slides) || defaults.slides
}

const visualPacingSummary = computed(() => {
  const defaults = visualPacingDefaults()
  const labels = {
    auto: '自动', slow: '舒缓', standard: '标准', fast: '紧凑', custom: '自定义',
  }
  let min = defaults.min
  let target = defaults.target
  if (form.visual_pacing_preset === 'slow') target += 2
  if (form.visual_pacing_preset === 'fast') target = Math.max(min, target - 2)
  if (form.visual_pacing_preset === 'custom') {
    return `自定义：至少 ${form.visual_min_duration} 秒，目标 ${form.visual_target_duration} 秒`
  }
  return `${labels[form.visual_pacing_preset] || '自动'}：至少 ${min} 秒，目标 ${target} 秒`
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
  { key: 'semantic', label: 'Agent 1 规划' },
  { key: 'visual', label: 'Agent 2 / 海报' },
  { key: 'render', label: '视频合成' },
  { key: 'archive', label: '项目归档' },
]

const importantLogPattern = /(失败|错误|异常|Traceback|Error|error|HTTP \d+|退出码|找不到|缺少|拒绝|超时|开始:|完成:|配音进度|TTS_HEARTBEAT|正在生成|开始配音|句配音|提交|等待|返图|云端状态|模块|海报|队列|分段|拼接|Streaming frame|Capturing frame|Encoding video|Assembling final video|Render complete|已停止|全部完成|输出:)/
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
const qwenSelectedVoice = computed(() => qwenVoiceGroups
  .flatMap((group) => group.voices)
  .find((voice) => voice.value === form.qwen_tts_voice))
const qwenSelectedVoiceSupportsInstructions = computed(() => qwenSelectedVoice.value?.supportsInstructions !== false)
const ttsEngineLabel = computed(() => ({
  indextts2: 'IndexTTS2 · 本地 GPU',
  cluster: 'IndexTTS2 · 集群 GPU',
  qwen: 'Qwen-TTS · 云端 API',
}[ttsEngine.value] || '配音引擎'))
const ttsEngineProviderLabel = computed(() => ({
  indextts2: settings.value.tts?.model || 'official IndexTTS2 2.0.0',
  cluster: cloudSession.value.base_url || 'cloud-api / Ray 集群',
  qwen: 'DashScope / 百炼',
}[ttsEngine.value] || ''))
const activeRemoteCloudVoices = computed(() => cloudVoices.value.filter((voice) => voice?.status === 'active'))
const cloudPresetVoiceOptions = computed(() => activeRemoteCloudVoices.value.filter((voice) => voice.type === 'preset'))
const cloudUploadedVoiceOptions = computed(() => activeRemoteCloudVoices.value.filter((voice) => (
  voice.type === 'uploaded' || voice.type === 'custom'
)))
const activeCloudVoices = computed(() => [
  ...cloudPresetVoiceOptions.value,
  ...cloudUploadedVoiceOptions.value,
])
const firstDefaultCloudVoice = computed(() => cloudPresetVoiceOptions.value[0] || null)
const selectedCloudVoice = computed(() => {
  if (!form.cluster_voice_id) return null
  const expectedType = form.cluster_voice_type === 'preset' ? 'preset' : 'uploaded'
  return activeCloudVoices.value.find((voice) => (
    (voice.type === 'preset' ? 'preset' : 'uploaded') === expectedType && voice.id === form.cluster_voice_id
  )) || null
})
const effectiveCloudVoice = computed(() => selectedCloudVoice.value || firstDefaultCloudVoice.value)
const previewableCloudPresetVoice = computed(() => (
  effectiveCloudVoice.value?.type === 'preset' ? effectiveCloudVoice.value : null
))
const cloudVoicePreviewPlaying = computed(() => Boolean(
  previewableCloudPresetVoice.value?.id
  && cloudVoicePreviewPlayingId.value === previewableCloudPresetVoice.value.id
  && cloudVoicePreviewAudio
  && !cloudVoicePreviewAudio.paused
))
const cloudReady = computed(() => Boolean(
  cloudSession.value.configured
  && cloudSession.value.authenticated
  && effectiveCloudVoice.value?.id
))
const cloudDisplayName = computed(() => {
  const user = cloudSession.value.user || {}
  return String(user.name || user.display_name || user.username || user.email?.split('@')[0] || '云端用户')
})
const cloudAvailableCredits = computed(() => cloudAccount.value.credits?.available ?? '-')
const cloudVoiceModel = computed({
  get: () => {
    if (!selectedCloudVoice.value) return ''
    return `${selectedCloudVoice.value.type === 'preset' ? 'preset' : 'uploaded'}:${selectedCloudVoice.value.id}`
  },
  set(value) {
    if (!value) {
      form.cluster_voice_type = 'preset'
      form.cluster_voice_id = ''
      cloudQuote.value = {}
      return
    }
    const [type, ...idParts] = String(value || '').split(':')
    form.cluster_voice_type = type === 'preset' ? 'preset' : 'uploaded'
    form.cluster_voice_id = idParts.join(':')
    cloudQuote.value = {}
  },
})
const hasPendingGeneration = computed(() => (
  [activeJob.value, ...jobs.value].filter(Boolean).some((job) => (
    ['queued', 'running', 'waiting_confirmation'].includes(job.status)
  ))
))
const canSubmitGeneration = computed(() => {
  if (!session.value.user) return false
  if (hasPendingGeneration.value) return false
  if (!form.project_name.trim()) return false
  if (scriptTooLong.value) return false
  if (form.bgm_enabled && !form.bgm_tracks.length) return false
  if (form.use_cloud_image_pool && !cloudSession.value.authenticated) return false
  if (form.skip_tts) {
    if (!form.source_audio_id) return false
    return form.skip_text_correction || form.script.trim().length > 0
  }
  if (ttsEngine.value === 'qwen' && !apiKeyStatus.value.qwen_tts?.configured) return false
  if (ttsEngine.value === 'qwen' && !qwenSelectedVoiceSupportsInstructions.value && form.qwen_tts_instructions.trim()) return false
  if (ttsEngine.value === 'cluster' && !cloudReady.value) return false
  return form.script.trim().length > 0
})
const canSubmitModule1 = computed(() => Boolean(
  session.value.user
  && (
    (ttsEngine.value === 'indextts2' && health.value.tts_online)
    || (ttsEngine.value === 'cluster' && cloudReady.value)
    || (ttsEngine.value === 'qwen' && apiKeyStatus.value.qwen_tts?.configured)
  )
  && form.project_name.trim()
  && form.script.trim().length >= 5
  && !scriptTooLong.value
  && !module1JobRunning.value
))
const scriptCharacterCount = computed(() => String(form.script || '').length)
const scriptTooLong = computed(() => scriptCharacterCount.value > MAX_SCRIPT_CHARACTERS)
const module1JobRunning = computed(() => ['queued', 'running'].includes(module1Job.value?.status))
const module1ArtifactEntries = computed(() => Object.entries(module1Job.value?.artifacts || {})
  .filter(([key]) => ['audio', 'module1_subtitle'].includes(key))
  .map(([key, url]) => ({ key, url })))
const module1LogText = computed(() => (module1Job.value?.logs || []).join('\n') || '模块 1 日志会显示在这里。')
const subtitleJobRunning = computed(() => ['queued', 'running'].includes(subtitleJob.value?.status))
const canSubmitSubtitle = computed(() => Boolean(
  session.value.user
  && subtitleForm.project_name.trim()
  && subtitleForm.source_audio_id
  && !subtitleJobRunning.value
  && (!subtitleForm.use_correction || subtitleForm.reference_text || apiKeyStatus.value.language?.configured),
))
const canRenderSubtitleVideo = computed(() => Boolean(
  subtitleJob.value?.id
  && subtitleJob.value?.artifacts?.subtitle
  && !subtitleJobRunning.value
  && (!subtitleRenderForm.bgm_enabled || subtitleRenderForm.bgm_tracks.length > 0)
))
const subtitleLogText = computed(() => (subtitleJob.value?.logs || []).join('\n') || '字幕识别日志会显示在这里。')
const submitButtonText = computed(() => {
  if (!session.value.user) return '请先登录'
  if (submitting.value) return '任务已提交'
  if (hasPendingGeneration.value) return '当前任务进行中'
  if (form.step_mode) return '开始分步生成'
  if (form.skip_tts && !form.source_audio_id) return '请先上传配音'
  if (form.skip_tts) return '从已有配音生成视频'
  if (ttsEngine.value === 'qwen' && !apiKeyStatus.value.qwen_tts?.configured) return '请先保存 Qwen-TTS API Key'
  if (ttsEngine.value === 'qwen' && !qwenSelectedVoiceSupportsInstructions.value && form.qwen_tts_instructions.trim()) return '该音色不支持配音描述'
  if (ttsEngine.value === 'cluster' && !cloudSession.value.authenticated) return '请先登录集群云端账户'
  if (ttsEngine.value === 'cluster' && !cloudReady.value) return '请选择可用的集群音色'
  return '一键生成视频'
})
const preflightPassedCount = computed(() => (
  preflightResult.value?.items || []
).filter((item) => item.status === 'passed').length)
const scriptPlaceholder = computed(() => {
  if (form.skip_text_correction) return '已选择“没有文案”，系统会用 ASR 识别结果继续生成画面和字幕。'
  if (form.skip_tts) return '粘贴与已有配音对应的文案，系统会跳过配音并进行字幕校对。'
  return '粘贴完整文案，系统会自动断句、配音、生成字幕和视频页面。'
})
const canCancelGeneration = computed(() => (
  session.value.user
  && ['queued', 'running', 'waiting_confirmation'].includes(activeJob.value?.status)
))
const canResumeGeneration = computed(() => (
  session.value.user
  && ['failed', 'cancelled'].includes(activeJob.value?.status)
))
const canContinueStepMode = computed(() => Boolean(
  session.value.user
  && activeJob.value?.status === 'waiting_confirmation'
))
const stepModeContinueLabel = computed(() => {
  const stage = activeJob.value?.request?._step_mode_stage
  return stage === 'visual' ? '确认画面，开始渲染' : '确认配音，开始配图'
})
const stepModeAudioUrl = computed(() => {
  return activeJob.value?.artifacts?.audio || ''
})
const canRetryTts = computed(() => Boolean(
  session.value.user
  && activeJob.value?.status === 'waiting_confirmation'
  && activeJob.value?.request?._step_mode_stage === 'audio'
  && !activeJob.value?.request?.skip_tts
))
const ttsStatusText = computed(() => {
  if (health.value.tts_online) return '在线'
  if (startingTts.value) return '检测中'
  if (ttsStartMessage.value) return ttsStartMessage.value
  return '未连接'
})

const COMPLETION_FAVICON_BLUE = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="#62c8ff"/><path d="M17 33l10 10 21-24" fill="none" stroke="#0b1728" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/></svg>')}`
const COMPLETION_FAVICON_GREEN = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="#55e6bd"/><path d="M17 33l10 10 21-24" fill="none" stroke="#0b1728" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/></svg>')}`

function prepareCompletionAlerts(requestNotificationPermission = false) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext
  if (AudioContextClass && !completionAudioContext) {
    try {
      completionAudioContext = new AudioContextClass()
    } catch {
      completionAudioContext = null
    }
  }
  if (completionAudioContext?.state === 'suspended') {
    completionAudioContext.resume().catch(() => {})
  }
  if (requestNotificationPermission && 'Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {})
  }
}

function playCompletionSound() {
  prepareCompletionAlerts(false)
  const context = completionAudioContext
  if (!context || context.state === 'closed') return
  context.resume().then(() => {
    const start = context.currentTime + 0.03
    ;[659.25, 783.99, 1046.5].forEach((frequency, index) => {
      const oscillator = context.createOscillator()
      const gain = context.createGain()
      const noteStart = start + index * 0.16
      oscillator.type = 'sine'
      oscillator.frequency.value = frequency
      gain.gain.setValueAtTime(0.0001, noteStart)
      gain.gain.exponentialRampToValueAtTime(0.16, noteStart + 0.025)
      gain.gain.exponentialRampToValueAtTime(0.0001, noteStart + 0.34)
      oscillator.connect(gain)
      gain.connect(context.destination)
      oscillator.start(noteStart)
      oscillator.stop(noteStart + 0.36)
    })
  }).catch(() => {})
}

function stopCompletionFlash() {
  if (completionFlashTimer) window.clearInterval(completionFlashTimer)
  completionFlashTimer = null
  completionFlashState = false
  if (originalDocumentTitle) document.title = originalDocumentTitle
  if (completionFaviconLink) {
    if (completionFaviconCreated) completionFaviconLink.remove()
    else completionFaviconLink.href = originalFaviconHref
  }
  completionFaviconLink = null
  completionFaviconCreated = false
  originalFaviconHref = ''
}

function startCompletionFlash() {
  if (!document.hidden) return
  stopCompletionFlash()
  originalDocumentTitle = document.title || '一键生成视频 / One-Click VidGen'
  completionFaviconLink = document.querySelector('link[rel~="icon"]')
  if (!completionFaviconLink) {
    completionFaviconLink = document.createElement('link')
    completionFaviconLink.rel = 'icon'
    document.head.appendChild(completionFaviconLink)
    completionFaviconCreated = true
  } else {
    originalFaviconHref = completionFaviconLink.href
  }
  const flash = () => {
    completionFlashState = !completionFlashState
    document.title = completionFlashState ? '✅ 视频生成完成！' : originalDocumentTitle
    completionFaviconLink.href = completionFlashState ? COMPLETION_FAVICON_GREEN : COMPLETION_FAVICON_BLUE
  }
  flash()
  completionFlashTimer = window.setInterval(flash, 700)
}

function notifyVideoCompleted(job, message = '视频已经生成完成，可以回来检查成片了。') {
  const now = Date.now()
  if (now - lastCompletionAlertAt < 5000) return
  lastCompletionAlertAt = now
  playCompletionSound()
  startCompletionFlash()
  if ('Notification' in window && Notification.permission === 'granted') {
    const projectName = job?.project_name || job?.request?.project_name || 'One-Click VidGen'
    const notification = new Notification('一键成片 · 视频生成完成', {
      body: `${projectName}\n${message}`,
      tag: `vidgen-completed-${job?.id || 'current'}`,
    })
    notification.onclick = () => {
      window.focus()
      stopCompletionFlash()
      notification.close()
    }
  }
}

function handleActiveJobCompletion(previous, current) {
  if (!previous || !current || previous.id !== current.id) return
  if (!['queued', 'running', 'waiting_confirmation'].includes(previous.status)) return
  if (!['completed', 'failed', 'cancelled'].includes(current.status)) return
  if (current.request?.use_cloud_image_pool) void refreshCloudState()
  if (current.status !== 'completed') return
  if (current.request?.module1_only || current.request?.subtitle_only) return
  notifyVideoCompleted(current)
}

function apiFailureReason(text) {
  const value = String(text || '')
  if (/积分不足|余额不足|insufficient\s*(credit|balance)|quota|额度不足|HTTP\s*402/i.test(value)) return '积分或额度不足'
  if (/timed?\s*out|timeout|超时/i.test(value)) return '请求超时'
  if (/HTTP\s*429|rate\s*limit|限流|too many requests/i.test(value)) return '请求限流'
  if (/HTTP\s*(401|403)|unauthorized|forbidden|API\s*Key.*(无效|错误)|invalid.*key/i.test(value)) return 'Key 无效或无权限'
  return ''
}

function apiJobKinds(job) {
  const request = job?.request || {}
  const kinds = []
  if (request.subtitle_only) {
    if (request.subtitle_use_correction && !request.reference_text) kinds.push('language')
    return kinds
  }
  if (!request.module1_only) kinds.push('language', 'image')
  if (!request.skip_tts && request.tts_engine === 'qwen') kinds.push('qwen_tts')
  return kinds
}

function syncApiRuntimeErrors(jobCandidates) {
  const unique = new Map()
  for (const job of jobCandidates.filter(Boolean)) unique.set(job.id, job)
  const ordered = [...unique.values()].sort((left, right) => Number(right.updated_at || 0) - Number(left.updated_at || 0))
  const contexts = {
    language: /gemini|openai|语言模型|chat\.completion|\bllm\b|agent\s*[012]/i,
    image: /image2|runninghub|海报|poster_|生图|返图|图像模型|工作流/i,
    qwen_tts: /qwen|dashscope|百炼|云端\s*tts/i,
  }
  for (const kind of ['language', 'image', 'qwen_tts']) {
    const latest = ordered.find((job) => ['completed', 'failed', 'cancelled'].includes(job.status) && apiJobKinds(job).includes(kind))
    if (!latest || latest.status !== 'failed') {
      apiKeyRuntimeErrors[kind] = ''
      continue
    }
    const text = [latest.error, latest.message, ...(latest.logs || []).slice(-80)].filter(Boolean).join('\n')
    const reason = apiFailureReason(text)
    apiKeyRuntimeErrors[kind] = reason && contexts[kind].test(text) ? reason : ''
  }
  apiKeyRuntimeErrors.common = apiKeyStatus.value.common?.configured
    ? (apiKeyRuntimeErrors.language || apiKeyRuntimeErrors.image)
    : ''
}

async function refresh() {
  const previousActiveJob = activeJob.value
    ? { id: activeJob.value.id, status: activeJob.value.status }
    : null
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
      apiKeyStatusLoaded = false
      apiKeyStatus.value = { language: {}, image: {}, common: {}, qwen_tts: {} }
      jobs.value = []
      jobPage.value = 1
      jobTotal.value = 0
      jobTotalPages.value = 1
      activeJob.value = null
      return
    }
    if (!apiKeyStatusLoaded) await loadApiKeySettings()
    const payload = await api.jobs(jobPage.value, JOB_PAGE_SIZE)
    jobs.value = payload.jobs || []
    jobPage.value = payload.page || 1
    jobTotal.value = payload.total || 0
    jobTotalPages.value = payload.total_pages || 1
    const runningJob = jobs.value.find((job) => job.status === 'running')
    const queuedJob = jobs.value.find((job) => job.status === 'queued')
    const liveJob = runningJob || queuedJob
    if (followLiveJob.value && liveJob?.id) {
      activeJob.value = await api.job(liveJob.id)
    } else if (activeJob.value?.id) {
      await selectJob(activeJob.value.id, false)
    } else if (jobs.value.length) {
      activeJob.value = jobs.value[0]
    }
    if (module1Job.value?.id) {
      module1Job.value = await api.job(module1Job.value.id)
    }
    if (subtitleJob.value?.id) {
      subtitleJob.value = await api.job(subtitleJob.value.id)
    }
    syncApiRuntimeErrors([...jobs.value, activeJob.value, module1Job.value, subtitleJob.value])
    await refreshEditor()
    handleActiveJobCompletion(previousActiveJob, activeJob.value)
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
    await refreshParameterPresets()
    await refreshAgentPromptPresets()
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
    await refreshParameterPresets()
    await refreshAgentPromptPresets()
  } catch (error) {
    authError.value = error.message || '注册失败'
  }
}

async function logout() {
  if (cloudSession.value.authenticated) {
    try { await api.cloudLogout() } catch { /* local logout must still proceed */ }
  }
  await api.logout()
  session.value = { user: null, auth_mode: 'account', mysql: {} }
  parameterPresets.value = []
  selectedParameterPreset.value = ''
  agentPromptPresets.value = []
  selectedAgentPromptPreset.value = ''
  jobs.value = []
  jobPage.value = 1
  jobTotal.value = 0
  jobTotalPages.value = 1
  activeJob.value = null
  editorAssets.value = []
  editorJobs.value = []
  editorJob.value = null
  apiKeyStatusLoaded = false
  apiKeyStatus.value = { language: {}, image: {}, common: {}, qwen_tts: {} }
  apiKeyMessage.value = ''
  for (const kind of Object.keys(apiKeyEditing)) apiKeyEditing[kind] = false
  for (const kind of Object.keys(apiKeyRuntimeErrors)) apiKeyRuntimeErrors[kind] = ''
  cloudSession.value = { configured: false, authenticated: false, user: null, base_url: '' }
  cloudAccount.value = { credits: {}, quota: {} }
  cloudVoices.value = []
  cloudVoiceLimits.value = {}
  cloudVoiceApiAvailable.value = true
  cloudQuote.value = {}
}

async function refreshCloudState() {
  if (!session.value.user) return
  cloudBusy.value = true
  cloudError.value = ''
  try {
    cloudSession.value = await api.cloudSession()
    if (!cloudSession.value.authenticated) {
      cloudAccount.value = { credits: {}, quota: {} }
      cloudVoices.value = []
      cloudVoiceLimits.value = {}
      return
    }
    cloudAccount.value = await api.cloudAccount() || { credits: {}, quota: {} }
    try {
      const voices = await api.cloudVoices()
      cloudVoices.value = voices.items || []
      cloudVoiceLimits.value = voices.limits || {}
      cloudVoiceApiAvailable.value = voices.capabilities?.upload !== false
      if (!cloudPresetVoiceOptions.value.length) {
        cloudError.value = '集群当前没有返回可用的默认音色，请检查 PRESET_VOICE_IDS 配置。'
      }
    } catch (voiceError) {
      cloudVoices.value = []
      cloudVoiceLimits.value = {}
      cloudVoiceApiAvailable.value = voiceError?.status !== 404
      cloudError.value = voiceError?.status === 404
        ? '当前云端版本尚未部署音色查询接口，请先更新 cloud-api。'
        : (voiceError.message || '无法查询集群当前支持的默认音色。')
    }
    if (form.cluster_voice_id && !selectedCloudVoice.value) {
      form.cluster_voice_type = 'preset'
      form.cluster_voice_id = ''
    }
  } catch (error) {
    cloudError.value = error.message || '无法读取集群云端状态。'
  } finally {
    cloudBusy.value = false
  }
}

async function openCloudLogin() {
  cloudLoginOpen.value = true
  await refreshCloudState()
}

async function loginCloud() {
  cloudError.value = ''
  cloudMessage.value = ''
  if (!cloudLoginForm.email || !cloudLoginForm.password) {
    cloudError.value = '请输入云端邮箱和密码。'
    return
  }
  cloudBusy.value = true
  try {
    cloudSession.value = await api.cloudLogin({ ...cloudLoginForm })
    cloudLoginForm.password = ''
    cloudMessage.value = '集群云端登录成功。'
    await refreshCloudState()
    cloudLoginOpen.value = false
  } catch (error) {
    cloudError.value = error.message || '集群云端登录失败。'
  } finally {
    cloudBusy.value = false
  }
}

async function registerCloud() {
  cloudError.value = ''
  cloudMessage.value = ''
  if (!cloudLoginForm.email || cloudLoginForm.password.length < 8) {
    cloudError.value = '注册密码至少需要 8 位。'
    return
  }
  cloudBusy.value = true
  try {
    const payload = await api.cloudRegister({ ...cloudLoginForm })
    cloudMessage.value = payload.verification_required
      ? '注册成功，请先完成邮箱验证后再登录。'
      : '注册成功，现在可以登录集群。'
  } catch (error) {
    cloudError.value = error.message || '集群云端注册失败。'
  } finally {
    cloudBusy.value = false
  }
}

async function logoutCloud() {
  cloudBusy.value = true
  cloudError.value = ''
  try {
    await api.cloudLogout()
    cloudSession.value = await api.cloudSession()
    cloudAccount.value = { credits: {}, quota: {} }
    cloudVoices.value = []
    cloudVoiceLimits.value = {}
    cloudVoiceApiAvailable.value = true
    cloudQuote.value = {}
    cloudMessage.value = '已退出集群云端账户。'
  } catch (error) {
    cloudError.value = error.message || '退出集群失败。'
  } finally {
    cloudBusy.value = false
  }
}

function selectCloudVoice(voice) {
  if (!voice?.id) return
  form.cluster_voice_type = voice.type === 'preset' ? 'preset' : 'uploaded'
  form.cluster_voice_id = voice.id
  cloudQuote.value = {}
}

function stopCloudVoicePreview() {
  if (cloudVoicePreviewAudio) {
    cloudVoicePreviewAudio.pause()
    cloudVoicePreviewAudio.removeAttribute('src')
    cloudVoicePreviewAudio.load()
    cloudVoicePreviewAudio = null
  }
  cloudVoicePreviewLoading.value = false
  cloudVoicePreviewPlayingId.value = ''
}

async function toggleCloudVoicePreview() {
  const voice = previewableCloudPresetVoice.value
  if (!voice?.id) return
  if (cloudVoicePreviewAudio && cloudVoicePreviewPlayingId.value === voice.id) {
    if (!cloudVoicePreviewAudio.paused) {
      cloudVoicePreviewAudio.pause()
      cloudVoicePreviewPlayingId.value = ''
      return
    }
    try {
      await cloudVoicePreviewAudio.play()
      cloudVoicePreviewPlayingId.value = voice.id
    } catch (error) {
      cloudError.value = error.message || '云端默认音色无法播放。'
    }
    return
  }

  stopCloudVoicePreview()
  cloudError.value = ''
  cloudVoicePreviewLoading.value = true
  const audio = new Audio(api.cloudVoiceAudioUrl(voice.id))
  cloudVoicePreviewAudio = audio
  audio.preload = 'auto'
  audio.addEventListener('playing', () => {
    if (cloudVoicePreviewAudio === audio) {
      cloudVoicePreviewLoading.value = false
      cloudVoicePreviewPlayingId.value = voice.id
    }
  })
  audio.addEventListener('pause', () => {
    if (cloudVoicePreviewAudio === audio && !audio.ended) {
      cloudVoicePreviewPlayingId.value = ''
    }
  })
  audio.addEventListener('ended', () => {
    if (cloudVoicePreviewAudio === audio) {
      cloudVoicePreviewPlayingId.value = ''
      audio.currentTime = 0
    }
  })
  audio.addEventListener('error', () => {
    if (cloudVoicePreviewAudio === audio) {
      cloudVoicePreviewLoading.value = false
      cloudVoicePreviewPlayingId.value = ''
      cloudError.value = '云端默认音色加载失败，请刷新后重试。'
    }
  })
  try {
    await audio.play()
  } catch (error) {
    cloudVoicePreviewLoading.value = false
    cloudVoicePreviewPlayingId.value = ''
    cloudError.value = error.message || '云端默认音色无法播放。'
  }
}

async function uploadCloudVoice(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  cloudError.value = ''
  cloudMessage.value = ''
  if (!file) return
  const suffix = file.name.split('.').pop()?.toLowerCase()
  if (!['wav', 'mp3', 'flac'].includes(suffix)) {
    cloudError.value = '云端参考音色只支持 WAV、MP3 或 FLAC。'
    return
  }
  if (file.size > 20 * 1024 * 1024) {
    cloudError.value = '云端参考音色不能超过 20 MiB。'
    return
  }
  const automaticName = file.name.replace(/\.[^.]+$/, '').trim() || '我的云端音色'
  const displayName = cloudVoiceDisplayName.value.trim() || automaticName
  cloudVoiceUploading.value = true
  try {
    const randomId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`
    const payload = await api.uploadCloudVoice(file, displayName, `voice-upload-${randomId}`)
    const voice = payload.voice
    if (!voice?.id) throw new Error('云端上传响应缺少 voice_id。')
    form.cluster_voice_type = voice.type === 'preset' ? 'preset' : 'uploaded'
    form.cluster_voice_id = voice.id
    cloudMessage.value = payload.deduplicated ? '音色已存在，已直接选中。' : '参考音色上传成功。'
    cloudVoiceDisplayName.value = ''
    await refreshCloudState()
  } catch (error) {
    cloudError.value = error.message || '上传云端参考音色失败。'
  } finally {
    cloudVoiceUploading.value = false
  }
}

async function deleteSelectedCloudVoice() {
  const voice = selectedCloudVoice.value
  if (!voice || voice.type === 'preset') return
  if (!window.confirm(`确定删除云端音色“${voice.display_name || voice.id}”？`)) return
  cloudBusy.value = true
  cloudError.value = ''
  try {
    await api.deleteCloudVoice(voice.id)
    cloudMessage.value = '云端自定义音色已删除。'
    await refreshCloudState()
  } catch (error) {
    cloudError.value = error.message || '删除云端音色失败。'
  } finally {
    cloudBusy.value = false
  }
}

async function refreshCloudQuote() {
  if (!cloudReady.value || form.script.trim().length < 5) return
  cloudQuoteLoading.value = true
  cloudError.value = ''
  try {
    cloudQuote.value = await api.cloudQuote(generationRequestPayload())
  } catch (error) {
    cloudQuote.value = {}
    cloudError.value = error.message || '获取集群报价失败。'
  } finally {
    cloudQuoteLoading.value = false
  }
}

function apiKeyFieldOpen(kind) {
  if (form.use_cloud_image_pool && ['language', 'image', 'common'].includes(kind)) return false
  if (kind === 'language') {
    return !currentLanguageProvider.value?.configured || Boolean(apiKeyEditing.language)
  }
  return !apiKeyStatus.value[kind]?.configured || Boolean(apiKeyEditing[kind])
}

function editApiKey(kind) {
  if (form.use_cloud_image_pool && kind !== 'qwen_tts') return
  apiKeyEditing[kind] = true
  apiKeyMessage.value = ''
  if (kind === 'qwen_tts') qwenTtsKeyMessage.value = ''
}

function onLanguageProviderChanged() {
  // Selecting a provider is a pending configuration change.  Keep the key box
  // visible even when this provider had been configured previously, so the
  // user can either replace its key or simply save to activate it.
  apiKeyEditing.language = true
  apiKeyRuntimeErrors.language = ''
  apiKeyMessage.value = ''
}

function addApiKeyAccount(kind) {
  if (form.use_cloud_image_pool) return
  const field = kind === 'image' ? 'image_api_keys' : 'common_api_keys'
  editApiKey(kind)
  addApiKeyField(field)
}

async function loadApiKeySettings() {
  if (!session.value.user) return
  try {
    const payload = await api.apiKeySettings()
    apiKeyStatus.value = payload.keys || { language: {}, image: {}, common: {}, qwen_tts: {} }
    apiKeyForm.language_provider = apiKeyStatus.value.language?.provider || 'gemini'
    apiKeyStatusLoaded = true
  } catch (error) {
    apiKeyMessage.value = error.message || '无法读取 API Key 配置状态'
  }
}

async function saveApiKeySettings() {
  const payload = { language_provider: apiKeyForm.language_provider }
  for (const key of ['language_api_key', 'image_api_key', 'common_api_key']) {
    const value = String(apiKeyForm[key] || '').trim()
    if (value) payload[key] = value
  }
  for (const key of ['image_api_keys', 'common_api_keys']) {
    const values = apiKeyForm[key].map((value) => String(value || '').trim()).filter(Boolean)
    if (values.length) payload[key] = values
  }
  if (!apiKeyForm.language_provider && Object.keys(payload).length === 0) {
    apiKeyMessage.value = '请至少填写一个 API Key。'
    return
  }
  savingApiKeys.value = true
  apiKeyMessage.value = ''
  try {
    const result = await api.saveApiKeySettings(payload)
    apiKeyStatus.value = result.keys || apiKeyStatus.value
    apiKeyStatusLoaded = true
    apiKeyMessage.value = result.message || 'API Key 已保存。'
    const touched = new Set()
    if (payload.language_api_key || payload.language_provider) touched.add('language')
    if (payload.image_api_key || payload.image_api_keys?.length) touched.add('image')
    if (payload.common_api_key || payload.common_api_keys?.length) {
      touched.add('common')
      touched.add('language')
      touched.add('image')
    }
    for (const kind of touched) {
      apiKeyEditing[kind] = false
      apiKeyRuntimeErrors[kind] = ''
    }
    apiKeyForm.language_api_key = ''
    apiKeyForm.image_api_key = ''
    apiKeyForm.image_api_keys = []
    apiKeyForm.common_api_key = ''
    apiKeyForm.common_api_keys = []
  } catch (error) {
    apiKeyMessage.value = error.message || '保存 API Key 失败。'
  } finally {
    savingApiKeys.value = false
  }
}

function addApiKeyField(key) {
  if (apiKeyForm[key].length >= 9) {
    apiKeyMessage.value = '单次最多可追加 9 个账号。'
    return
  }
  apiKeyForm[key].push('')
}

function removeApiKeyField(key, index) {
  apiKeyForm[key].splice(index, 1)
}

async function saveQwenTtsKey() {
  const key = String(apiKeyForm.qwen_tts_api_key || '').trim()
  if (!key) {
    qwenTtsKeyMessage.value = '请填写 DashScope API Key。'
    return
  }
  savingQwenTtsKey.value = true
  qwenTtsKeyMessage.value = ''
  try {
    const result = await api.saveApiKeySettings({ qwen_tts_api_key: key })
    apiKeyStatus.value = result.keys || apiKeyStatus.value
    apiKeyStatusLoaded = true
    apiKeyForm.qwen_tts_api_key = ''
    apiKeyEditing.qwen_tts = false
    apiKeyRuntimeErrors.qwen_tts = ''
    qwenTtsKeyMessage.value = 'Qwen-TTS API Key 已保存到本机 .env。'
  } catch (error) {
    qwenTtsKeyMessage.value = error.message || '保存 Qwen-TTS API Key 失败。'
  } finally {
    savingQwenTtsKey.value = false
  }
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
  form.global_character_prompt = window.localStorage.getItem(modeStorageKey(GLOBAL_CHARACTER_STORAGE_KEY))
    || modeDefaults.default_character
    || ''
  form.story_environment_prompt = window.localStorage.getItem(modeStorageKey(STORY_ENVIRONMENT_STORAGE_KEY)) || ''
  form.visual_prompt_system = window.localStorage.getItem(modeStorageKey(VISUAL_PROMPT_FULL_STORAGE_KEY))
    || modeDefaults.default_system
    || ''
  form.agent0_prompt_system = window.localStorage.getItem(modeStorageKey(AGENT0_PROMPT_STORAGE_KEY))
    || modeDefaults.default_agent0_system
    || ''
  form.agent1_prompt_system = window.localStorage.getItem(modeStorageKey(AGENT1_PROMPT_STORAGE_KEY))
    || modeDefaults.default_agent1_system
    || ''
  form.agent2_director_theme = window.localStorage.getItem(modeStorageKey(AGENT2_DIRECTOR_THEME_STORAGE_KEY))
    || AGENT2_DIRECTOR_THEME_DEFAULTS[form.content_mode]
    || ''
  applyVisualPacing()
  rememberVisualPrompt()
}

async function refreshParameterPresets() {
  if (!session.value.user) {
    parameterPresets.value = []
    selectedParameterPreset.value = ''
    return
  }
  loadingParameterPresets.value = true
  try {
    const payload = await api.parameterPresets()
    parameterPresets.value = payload.presets || []
  } catch (error) {
    parameterPresetMessage.value = error.message || '无法读取已保存参数'
  } finally {
    loadingParameterPresets.value = false
  }
}

async function refreshAgentPromptPresets() {
  if (!session.value.user) {
    agentPromptPresets.value = []
    selectedAgentPromptPreset.value = ''
    return
  }
  loadingAgentPromptPresets.value = true
  try {
    const payload = await api.agentPromptPresets()
    agentPromptPresets.value = payload.presets || []
  } finally {
    loadingAgentPromptPresets.value = false
  }
}

async function loadSelectedAgentPromptPreset() {
  if (!selectedAgentPromptPreset.value) return
  try {
    const presetKey = selectedAgentPromptPreset.value
    const payload = await api.agentPromptPreset(presetKey)
    if (contentModeOptions.value.some((item) => item.key === payload.content_mode)) {
      setContentMode(payload.content_mode)
    }
    form.visual_prompt_system = payload.visual_prompt_system || ''
    form.agent0_prompt_system = payload.agent0_prompt_system || contentModeDefaults().default_agent0_system || ''
    form.agent1_prompt_system = payload.agent1_prompt_system || contentModeDefaults().default_agent1_system || ''
    form.agent2_director_theme = payload.agent2_director_theme || AGENT2_DIRECTOR_THEME_DEFAULTS[form.content_mode] || ''
    form.visual_prompt_mode = 'full'
    selectedAgentPromptPreset.value = presetKey
    rememberVisualPrompt()
  } catch (error) {
    parameterPresetMessage.value = error.message || '读取 Agent 提示词失败'
  }
}

async function saveCurrentAgentPromptPreset() {
  const prompt = String(form.visual_prompt_system || '').trim()
  if (!prompt) {
    parameterPresetMessage.value = '请先填写完整 Agent 2 画面指令。'
    return
  }
  const name = window.prompt('请输入 Agent 提示词保存名：', selectedAgentPromptPreset.value || form.project_name || '')
  if (!name?.trim()) return
  savingAgentPromptPreset.value = true
  try {
    const payload = await api.saveAgentPromptPreset({
      name: name.trim(),
      visual_prompt_system: prompt,
      agent0_prompt_system: form.agent0_prompt_system,
      agent1_prompt_system: form.agent1_prompt_system,
      agent2_director_theme: form.agent2_director_theme,
      content_mode: form.content_mode,
    })
    selectedAgentPromptPreset.value = payload.key || `user:${payload.name || name.trim()}`
    parameterPresetMessage.value = payload.message || 'Agent 提示词已保存。'
    await refreshAgentPromptPresets()
  } catch (error) {
    parameterPresetMessage.value = error.message || '保存 Agent 提示词失败'
  } finally {
    savingAgentPromptPreset.value = false
  }
}

async function saveCurrentParameterPreset() {
  const name = String(form.project_name || '').trim()
  if (!name) {
    parameterPresetMessage.value = '请先填写项目名称，再保存参数。'
    return
  }
  savingParameterPreset.value = true
  parameterPresetMessage.value = ''
  try {
    const payload = await api.saveParameterPreset({
      name,
      parameters: {
        ...form,
        manual_script: String(form.script || ''),
        tts_engine: ttsEngine.value,
      },
    })
    selectedParameterPreset.value = payload.name || name
    parameterPresetMessage.value = payload.message || '参数已保存。'
    await refreshParameterPresets()
  } catch (error) {
    parameterPresetMessage.value = error.message || '保存参数失败'
  } finally {
    savingParameterPreset.value = false
  }
}

async function loadSelectedParameterPreset() {
  if (!selectedParameterPreset.value) return
  parameterPresetMessage.value = ''
  try {
    const payload = await api.parameterPreset(selectedParameterPreset.value)
    const parameters = payload.parameters || {}
    Object.assign(form, parameters)
    form.bgm_enabled = Boolean(parameters.bgm_enabled)
    form.bgm_tracks = Array.isArray(parameters.bgm_tracks)
      ? parameters.bgm_tracks.map((track) => ({
          asset_id: String(track.asset_id || ''),
          name: String(track.name || editorAssets.value.find((asset) => asset.id === track.asset_id)?.name || track.asset_id || ''),
          volume_db: Number.isFinite(Number(track.volume_db)) ? Number(track.volume_db) : -10,
          duration_seconds: Number.isFinite(Number(track.duration_seconds)) ? Number(track.duration_seconds) : null,
          url: String(editorAssets.value.find((asset) => asset.id === track.asset_id)?.url || ''),
        })).filter((track) => track.asset_id)
      : []
    form.bgm_fade_enabled = Boolean(parameters.bgm_fade_enabled)
    form.bgm_fade_duration = Number.isFinite(Number(parameters.bgm_fade_duration))
      ? Number(parameters.bgm_fade_duration)
      : 1
    form.script = typeof parameters.script === 'string' ? parameters.script : ''
    ttsEngine.value = ['indextts2', 'cluster', 'qwen'].includes(parameters.tts_engine)
      ? parameters.tts_engine
      : 'indextts2'
    form.visual_prompt_mode = parameters.visual_prompt_mode === 'full' ? 'full' : 'simple'
    await restoreSavedTtsVoiceLabel()
    await restoreSavedProtagonistReferenceImageLabel()
    void hydrateBgmTrackDurations(form.bgm_tracks)
    rememberVisualPrompt()
    rememberVisualPacing()
    parameterPresetMessage.value = `已读取参数：${payload.name || selectedParameterPreset.value}`
  } catch (error) {
    parameterPresetMessage.value = error.message || '读取参数失败'
  }
}

async function deleteSelectedParameterPreset() {
  const name = String(selectedParameterPreset.value || '').trim()
  if (!name) return
  if (!window.confirm(`确定删除已保存参数“${name}”？此操作无法撤销。`)) return
  deletingParameterPreset.value = true
  parameterPresetMessage.value = ''
  try {
    const payload = await api.deleteParameterPreset(name)
    selectedParameterPreset.value = ''
    await refreshParameterPresets()
    parameterPresetMessage.value = payload.message || `已删除参数：${name}`
  } catch (error) {
    parameterPresetMessage.value = error.message || '删除参数失败'
  } finally {
    deletingParameterPreset.value = false
  }
}

function formatBgmDuration(value) {
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds <= 0) return '时长读取中'
  const total = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(total / 60)
  return `${minutes}:${String(total % 60).padStart(2, '0')}`
}

function readAudioDuration(file) {
  return new Promise((resolve) => {
    const objectUrl = URL.createObjectURL(file)
    const audio = new Audio()
    const finish = (value) => {
      URL.revokeObjectURL(objectUrl)
      audio.removeAttribute('src')
      audio.load()
      resolve(Number.isFinite(value) && value > 0 ? Number(value.toFixed(2)) : null)
    }
    audio.preload = 'metadata'
    audio.onloadedmetadata = () => finish(audio.duration)
    audio.onerror = () => finish(null)
    audio.src = objectUrl
  })
}

function readAudioUrlDuration(url) {
  return new Promise((resolve) => {
    const audio = new Audio()
    const finish = (value) => {
      audio.removeAttribute('src')
      audio.load()
      resolve(Number.isFinite(value) && value > 0 ? Number(value.toFixed(2)) : null)
    }
    audio.preload = 'metadata'
    audio.onloadedmetadata = () => finish(audio.duration)
    audio.onerror = () => finish(null)
    audio.src = url
  })
}

async function hydrateBgmTrackDurations(tracks) {
  await Promise.all((tracks || []).map(async (track) => {
    if (Number.isFinite(Number(track.duration_seconds)) && Number(track.duration_seconds) > 0) return
    const url = bgmTrackUrl(track)
    if (url) track.duration_seconds = await readAudioUrlDuration(url)
  }))
}

function bgmTrackUrl(track) {
  if (track?.url) return track.url
  return editorAssets.value.find((asset) => String(asset.id) === String(track?.asset_id))?.url || ''
}

function isBgmPreviewing(track) {
  return bgmPreviewTrack.value === track && Boolean(bgmPreviewAudio && !bgmPreviewAudio.paused)
}

function stopBgmPreview() {
  if (bgmPreviewAudio) {
    bgmPreviewAudio.pause()
    bgmPreviewAudio.currentTime = 0
  }
  bgmPreviewAudio = null
  bgmPreviewTrack.value = null
}

async function toggleBgmPreview(track) {
  const url = bgmTrackUrl(track)
  if (!url) return
  if (bgmPreviewTrack.value === track && bgmPreviewAudio) {
    if (bgmPreviewAudio.paused) {
      try {
        await bgmPreviewAudio.play()
      } catch (error) {
        bgmError.value = error.message || 'BGM 试听无法播放。'
      }
    } else {
      bgmPreviewAudio.pause()
    }
    return
  }
  if (bgmPreviewAudio) bgmPreviewAudio.pause()
  const audio = new Audio(url)
  bgmPreviewAudio = audio
  bgmPreviewTrack.value = track
  audio.addEventListener('ended', () => {
    if (bgmPreviewAudio === audio) {
      bgmPreviewAudio = null
      bgmPreviewTrack.value = null
    }
  })
  audio.addEventListener('pause', () => {
    if (bgmPreviewAudio === audio && audio.currentTime >= audio.duration) {
      bgmPreviewAudio = null
      bgmPreviewTrack.value = null
    }
  })
  try {
    await audio.play()
  } catch (error) {
    if (bgmPreviewAudio === audio) {
      bgmPreviewAudio = null
      bgmPreviewTrack.value = null
    }
    bgmError.value = error.message || 'BGM 试听无法播放。'
  }
}

function moveBgmTrack(tracks, index, direction) {
  const target = index + direction
  if (!Array.isArray(tracks) || target < 0 || target >= tracks.length) return
  const [track] = tracks.splice(index, 1)
  tracks.splice(target, 0, track)
}

function clearBgmTracks(scope) {
  const tracks = scope === 'subtitle'
    ? subtitleRenderForm.bgm_tracks
    : scope === 'visual'
      ? visualBgm.tracks
      : form.bgm_tracks
  if (!tracks.length) return
  if (!window.confirm('确定清空当前 BGM 播放列表吗？')) return
  if (bgmPreviewTrack.value && tracks.includes(bgmPreviewTrack.value)) stopBgmPreview()
  tracks.splice(0, tracks.length)
}

async function uploadVisualBgmTrack(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  visualBgmUploading.value = true
  visualBgmError.value = ''
  try {
    const durationSeconds = await readAudioDuration(file)
    const payload = await api.uploadEditorAsset(file)
    if (payload.asset?.kind !== 'audio') throw new Error('上传文件不是可识别的音频。')
    visualBgm.tracks.push({
      asset_id: payload.asset.id,
      archived_filename: '',
      name: payload.asset.name || file.name,
      volume_db: -10,
      duration_seconds: durationSeconds,
      url: payload.asset.url || '',
    })
    if (!editorAssets.value.some((asset) => asset.id === payload.asset.id)) editorAssets.value.push(payload.asset)
  } catch (error) {
    visualBgmError.value = error.message || 'BGM 上传失败'
  } finally {
    visualBgmUploading.value = false
  }
}

function removeVisualBgmTrack(index) {
  const [track] = visualBgm.tracks.splice(index, 1)
  if (track && bgmPreviewTrack.value === track) stopBgmPreview()
}

async function uploadBgmTrack(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  bgmUploading.value = true
  bgmError.value = ''
  try {
    const durationSeconds = await readAudioDuration(file)
    const payload = await api.uploadEditorAsset(file)
    if (payload.asset?.kind !== 'audio') throw new Error('上传文件不是可识别的音频。')
    form.bgm_tracks.push({
      asset_id: payload.asset.id,
      name: payload.asset.name || file.name,
      volume_db: -10,
      duration_seconds: durationSeconds,
      url: payload.asset.url || '',
    })
    if (!editorAssets.value.some((asset) => asset.id === payload.asset.id)) {
      editorAssets.value.push(payload.asset)
    }
  } catch (error) {
    bgmError.value = error.message || 'BGM 上传失败'
  } finally {
    bgmUploading.value = false
  }
}

function removeBgmTrack(index) {
  const [track] = form.bgm_tracks.splice(index, 1)
  if (track && bgmPreviewTrack.value === track) stopBgmPreview()
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
    if (content.length > MAX_SCRIPT_CHARACTERS) {
      throw new Error(`单次文案最多 ${MAX_SCRIPT_CHARACTERS.toLocaleString()} 个字符，当前 ${content.length.toLocaleString()}。请按完整章节拆分后分批生成。`)
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
    form.global_character_prompt = modeDefaults.default_character || ''
  } else {
    form.visual_prompt_system = modeDefaults.default_system || ''
  }
  rememberVisualPrompt()
}

function resetSimpleVisualPrompt() {
  form.visual_prompt_mode = 'simple'
  form.visual_style_prompt = contentModeDefaults().default_style || ''
  form.global_character_prompt = contentModeDefaults().default_character || ''
  form.story_environment_prompt = ''
  rememberVisualPrompt()
}

function setContentMode(mode) {
  if (mode === form.content_mode) return
  rememberVisualPrompt()
  form.content_mode = mode
  form.visual_prompt_mode = 'simple'
  selectedAgentPromptPreset.value = ''
  const modeDefaults = contentModeDefaults(mode)
  form.visual_style_prompt = window.localStorage.getItem(modeStorageKey(VISUAL_PROMPT_STYLE_STORAGE_KEY, mode))
    || modeDefaults.default_style
  form.global_character_prompt = window.localStorage.getItem(modeStorageKey(GLOBAL_CHARACTER_STORAGE_KEY, mode))
    || modeDefaults.default_character
    || ''
  form.story_environment_prompt = window.localStorage.getItem(modeStorageKey(STORY_ENVIRONMENT_STORAGE_KEY, mode)) || ''
  form.visual_prompt_system = window.localStorage.getItem(modeStorageKey(VISUAL_PROMPT_FULL_STORAGE_KEY, mode))
    || modeDefaults.default_system
    || ''
  form.agent0_prompt_system = window.localStorage.getItem(modeStorageKey(AGENT0_PROMPT_STORAGE_KEY, mode))
    || modeDefaults.default_agent0_system
    || ''
  form.agent1_prompt_system = window.localStorage.getItem(modeStorageKey(AGENT1_PROMPT_STORAGE_KEY, mode))
    || modeDefaults.default_agent1_system
    || ''
  form.agent2_director_theme = window.localStorage.getItem(modeStorageKey(AGENT2_DIRECTOR_THEME_STORAGE_KEY, mode))
    || AGENT2_DIRECTOR_THEME_DEFAULTS[mode]
    || ''
  applyVisualPacing(mode)
  rememberVisualPrompt()
}

function setVisualPromptMode(mode) {
  form.visual_prompt_mode = mode
  if (mode === 'full' && !String(form.agent0_prompt_system || '').trim()) {
    form.agent0_prompt_system = contentModeDefaults().default_agent0_system || ''
  }
  if (mode === 'full' && !String(form.agent1_prompt_system || '').trim()) {
    form.agent1_prompt_system = contentModeDefaults().default_agent1_system || ''
  }
  if (mode !== 'full') selectedAgentPromptPreset.value = ''
  rememberVisualPrompt()
}

function rememberVisualPrompt() {
  window.localStorage.setItem(CONTENT_MODE_STORAGE_KEY, form.content_mode)
  window.localStorage.setItem(VISUAL_PROMPT_MODE_STORAGE_KEY, form.visual_prompt_mode)
  window.localStorage.setItem(modeStorageKey(VISUAL_PROMPT_STYLE_STORAGE_KEY), form.visual_style_prompt || '')
  window.localStorage.setItem(modeStorageKey(GLOBAL_CHARACTER_STORAGE_KEY), form.global_character_prompt || '')
  window.localStorage.setItem(modeStorageKey(STORY_ENVIRONMENT_STORAGE_KEY), form.story_environment_prompt || '')
  window.localStorage.setItem(modeStorageKey(VISUAL_PROMPT_FULL_STORAGE_KEY), form.visual_prompt_system || '')
  window.localStorage.setItem(modeStorageKey(AGENT0_PROMPT_STORAGE_KEY), form.agent0_prompt_system || '')
  window.localStorage.setItem(modeStorageKey(AGENT1_PROMPT_STORAGE_KEY), form.agent1_prompt_system || '')
  window.localStorage.setItem(modeStorageKey(AGENT2_DIRECTOR_THEME_STORAGE_KEY), form.agent2_director_theme || '')
}

function rememberVisualPacing() {
  window.localStorage.setItem(modeStorageKey(VISUAL_PACING_STORAGE_KEY), JSON.stringify({
    preset: form.visual_pacing_preset,
    min: form.visual_min_duration,
    target: form.visual_target_duration,
    max: form.visual_max_duration,
    slides: form.visual_max_slides,
  }))
}

async function uploadTtsVoice(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  stopTtsVoicePreview()
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
    ttsVoicePreviewUrl.value = payload.asset.url || ''
  } catch (error) {
    ttsVoiceUploadName.value = ''
    ttsVoicePreviewUrl.value = ''
    ttsVoiceUploadError.value = error.message || '上传参考音色失败'
  } finally {
    ttsVoiceUploading.value = false
  }
}

async function restoreSavedTtsVoiceLabel() {
  const savedVoiceId = String(form.tts_voice_id || '')
  ttsVoiceUploadError.value = ''
  if (!savedVoiceId.startsWith('upload:')) {
    ttsVoiceUploadName.value = ''
    ttsVoicePreviewUrl.value = ''
    return
  }

  const assetId = savedVoiceId.slice('upload:'.length)
  try {
    if (!editorAssets.value.length) {
      const payload = await api.editorUploads()
      editorAssets.value = payload.assets || []
    }
    const asset = editorAssets.value.find((item) => String(item.id) === assetId)
    if (asset) {
      ttsVoiceUploadName.value = asset.name || '已恢复本地参考音色'
      ttsVoicePreviewUrl.value = asset.url || ''
      return
    }
    ttsVoiceUploadName.value = '已保存的参考音色'
    ttsVoicePreviewUrl.value = ''
    ttsVoiceUploadError.value = '该参考音色文件当前不存在，请重新上传后再运行。'
  } catch {
    ttsVoiceUploadName.value = '已保存的参考音色'
    ttsVoicePreviewUrl.value = ''
  }
}

function stopTtsVoicePreview() {
  if (ttsVoicePreviewAudio) {
    ttsVoicePreviewAudio.pause()
    ttsVoicePreviewAudio.currentTime = 0
  }
  ttsVoicePreviewPlaying.value = false
}

async function toggleTtsVoicePreview() {
  const source = ttsVoicePreviewUrl.value
  if (!source) return
  if (ttsVoicePreviewAudio && ttsVoicePreviewAudio.src.endsWith(source)) {
    if (ttsVoicePreviewAudio.paused) {
      await ttsVoicePreviewAudio.play()
      ttsVoicePreviewPlaying.value = true
    } else {
      ttsVoicePreviewAudio.pause()
      ttsVoicePreviewPlaying.value = false
    }
    return
  }
  stopTtsVoicePreview()
  const player = new Audio(source)
  ttsVoicePreviewAudio = player
  player.addEventListener('ended', () => {
    if (ttsVoicePreviewAudio === player) ttsVoicePreviewPlaying.value = false
  })
  player.addEventListener('pause', () => {
    if (ttsVoicePreviewAudio === player && player.currentTime < player.duration) ttsVoicePreviewPlaying.value = false
  })
  try {
    await player.play()
    ttsVoicePreviewPlaying.value = true
  } catch (error) {
    ttsVoicePreviewPlaying.value = false
    ttsVoiceUploadError.value = error.message || '音色试听无法播放。'
  }
}

async function restoreSavedProtagonistReferenceImageLabel() {
  let assetIds = Array.isArray(form.reference_image_ids)
    ? form.reference_image_ids.map((value) => String(value || '')).filter(Boolean).slice(0, 3)
    : []
  if (!assetIds.length && form.protagonist_reference_image_id) {
    assetIds = [String(form.protagonist_reference_image_id)]
  }
  form.reference_image_ids = assetIds
  form.protagonist_reference_image_id = assetIds[0] || ''
  protagonistReferenceImageError.value = ''
  if (!assetIds.length) {
    referenceImageNames.value = []
    return
  }
  try {
    if (!editorAssets.value.length) {
      const payload = await api.editorUploads()
      editorAssets.value = payload.assets || []
    }
    const assets = assetIds.map((assetId) => editorAssets.value.find(
      (item) => String(item.id) === assetId && item.kind === 'image',
    ))
    if (assets.some((asset) => !asset)) throw new Error('保存的角色参考图当前不存在，请重新上传后再运行。')
    referenceImageNames.value = assets.map((asset, index) => asset.name || `角色参考图 ${index + 1}`)
  } catch (error) {
    referenceImageNames.value = assetIds.map((_, index) => `已保存的参考图 ${index + 1}`)
    protagonistReferenceImageError.value = error.message || '无法恢复主角参考图。'
  }
}

async function exportDiagnosticPackage(job) {
  if (!job?.id || diagnosticExporting.value) return
  diagnosticExporting.value = true
  diagnosticMessage.value = ''
  try {
    const { blob, filename } = await api.downloadDiagnosticPackage(job.id)
    const objectUrl = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = filename || '问题诊断包.zip'
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000)
    diagnosticMessage.value = '问题诊断包已下载：不含 API Key、文案原文、提示词、媒体和模型文件。'
  } catch (error) {
    diagnosticMessage.value = error.message || '问题诊断包导出失败。'
  } finally {
    diagnosticExporting.value = false
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

async function openSubtitleOutputFolder() {
  folderOpenMessage.value = ''
  if (!subtitleJob.value?.id) return
  try {
    const payload = await api.openJobOutputFolder(subtitleJob.value.id)
    folderOpenMessage.value = `已打开字幕任务输出：${payload.path || ''}`
  } catch (error) {
    folderOpenMessage.value = error.message || '暂时找不到字幕任务输出文件夹'
  }
}

async function openProjectOutputFolder() {
  folderOpenMessage.value = ''
  if (!activeJob.value?.id) return
  try {
    const payload = await api.openJobOutputFolder(activeJob.value.id)
    folderOpenMessage.value = `已打开项目输出：${payload.path || ''}`
  } catch (error) {
    folderOpenMessage.value = error.message || '暂时找不到项目输出文件夹'
  }
}

async function openStepModeVisualPreviewFolder() {
  if (!activeJob.value?.id) return
  folderOpenMessage.value = ''
  try {
    const payload = await api.openStepModeVisualPreviewFolder(activeJob.value.id)
    folderOpenMessage.value = `已打开画面检查文件夹：${payload.path || ''}`
  } catch (error) {
    folderOpenMessage.value = error.message || '画面检查文件夹暂不可用'
  }
}

function hydrateVisualBgm(settings = {}) {
  visualBgm.enabled = Boolean(settings.enabled)
  visualBgm.tracks = Array.isArray(settings.tracks) ? settings.tracks.map((track) => ({ ...track })) : []
  visualBgm.fade_enabled = Boolean(settings.fade_enabled)
  visualBgm.fade_duration = Number(settings.fade_duration || 1)
  visualBgmError.value = ''
}

async function loadVisualEditor({ preservePage = false, hydrateBgm = false } = {}) {
  if (!visualEditorProjectId.value) return
  visualEditorLoading.value = true
  try {
    visualEditor.value = await api.visualEditor(visualEditorProjectId.value)
    if (hydrateBgm) hydrateVisualBgm(visualEditor.value.bgm)
    if (!preservePage) visualEditorPage.value = 1
    if (visualEditorPage.value > visualEditorPageCount.value) visualEditorPage.value = visualEditorPageCount.value
    if (!visualEditor.value.items.some((item) => item.id === visualTimingSelectedId.value)) {
      visualTimingSelectedId.value = visualEditor.value.items.find((item) => item.timing)?.id || ''
    }
    if (!visualEditor.value.timing_history?.some((item) => item.id === selectedVisualTimingHistory.value)) {
      selectedVisualTimingHistory.value = ''
    }
  } catch (error) {
    visualEditor.value = { items: [], task: { status: 'failed', message: error.message || '无法读取画面修改资料' }, version: 0 }
  } finally {
    visualEditorLoading.value = false
  }
}

async function loadTtsEditor() {
  if (!visualEditorProjectId.value) return
  ttsEditorLoading.value = true
  try {
    ttsEditor.value = await api.ttsEditor(visualEditorProjectId.value)
    const valid = new Set((ttsEditor.value.segments || []).map((item) => item.index))
    selectedTtsSegmentIndices.value = selectedTtsSegmentIndices.value.filter((value) => valid.has(value))
  } catch (error) {
    ttsEditor.value = { available: false, message: error.message || '无法读取逐句配音', segments: [], task: { status: 'failed', message: '' } }
  } finally {
    ttsEditorLoading.value = false
  }
}

function resetTtsSegmentAudio({ clearSource = true } = {}) {
  if (ttsSegmentAudio) {
    ttsSegmentAudio.pause()
    if (clearSource) {
      ttsSegmentAudio.removeAttribute('src')
      ttsSegmentAudio.load()
    }
  }
  ttsSegmentPlayingIndex.value = 0
  ttsSegmentIsPlaying.value = false
  ttsSegmentCurrentTime.value = 0
  ttsSegmentDuration.value = 0
}

function prepareTtsSegmentAudio(item) {
  if (!ttsSegmentAudio) {
    ttsSegmentAudio = new Audio()
    ttsSegmentAudio.preload = 'metadata'
    ttsSegmentAudio.addEventListener('timeupdate', () => {
      ttsSegmentCurrentTime.value = Number(ttsSegmentAudio?.currentTime || 0)
    })
    ttsSegmentAudio.addEventListener('loadedmetadata', () => {
      ttsSegmentDuration.value = Number.isFinite(ttsSegmentAudio?.duration)
        ? Number(ttsSegmentAudio.duration)
        : Number(item.duration || 0)
    })
    ttsSegmentAudio.addEventListener('play', () => { ttsSegmentIsPlaying.value = true })
    ttsSegmentAudio.addEventListener('pause', () => { ttsSegmentIsPlaying.value = false })
    ttsSegmentAudio.addEventListener('ended', () => {
      ttsSegmentIsPlaying.value = false
      ttsSegmentCurrentTime.value = 0
    })
  }
  if (ttsSegmentPlayingIndex.value !== item.index || ttsSegmentAudio.dataset.source !== item.audio_url) {
    ttsSegmentAudio.pause()
    ttsSegmentPlayingIndex.value = item.index
    ttsSegmentCurrentTime.value = 0
    ttsSegmentDuration.value = Number(item.duration || 0)
    ttsSegmentAudio.dataset.source = item.audio_url
    const separator = item.audio_url.includes('?') ? '&' : '?'
    ttsSegmentAudio.src = `${item.audio_url}${separator}play=${Date.now()}`
    ttsSegmentAudio.load()
  }
  return ttsSegmentAudio
}

async function toggleTtsSegmentAudio(item) {
  const audio = prepareTtsSegmentAudio(item)
  if (!audio.paused) {
    audio.pause()
    return
  }
  try {
    await audio.play()
  } catch {
    ttsSegmentIsPlaying.value = false
  }
}

function seekTtsSegmentAudio(item, event) {
  const audio = prepareTtsSegmentAudio(item)
  const value = Math.max(0, Number(event?.target?.value || 0))
  audio.currentTime = Math.min(value, Number.isFinite(audio.duration) ? audio.duration : value)
  ttsSegmentCurrentTime.value = value
}

function stopTtsEditorPolling() {
  if (ttsEditorTaskTimer) window.clearInterval(ttsEditorTaskTimer)
  ttsEditorTaskTimer = null
}

async function pollTtsEditorStatus() {
  if (!visualEditorOpen.value || !visualEditorProjectId.value) return
  try {
    const payload = await api.ttsEditorStatus(visualEditorProjectId.value)
    const previous = ttsEditor.value.task?.status
    ttsEditor.value.task = payload.task || ttsEditor.value.task
    if (previous === 'running' && payload.task?.status !== 'running') {
      stopTtsEditorPolling()
      if (payload.task?.status === 'completed') {
        resetTtsSegmentAudio()
        selectedTtsSegmentIndices.value = []
        await Promise.all([loadTtsEditor(), loadVisualEditor({ preservePage: true })])
      }
    }
  } catch {
    // Main job log remains visible if one polling request fails.
  }
}

function startTtsEditorPolling() {
  if (ttsEditorTaskTimer) return
  ttsEditorTaskTimer = window.setInterval(pollTtsEditorStatus, 1600)
}

async function regenerateSelectedTtsSegments() {
  if (!visualEditorProjectId.value || !selectedTtsSegmentIndices.value.length || ttsEditor.value.task?.status === 'running') return
  const count = selectedTtsSegmentIndices.value.length
  if (!window.confirm(`重新生成选中的 ${count} 句配音？\n\n完成后整条音频、字幕时间戳和画面时间线会自动更新，现有视频需点击“重新渲染”才能应用。`)) return
  try {
    await api.regenerateTtsSegments(visualEditorProjectId.value, selectedTtsSegmentIndices.value)
    ttsEditor.value.task = { status: 'running', progress: 0, message: `正在重配 ${count} 句，请留意上方任务日志。` }
    startTtsEditorPolling()
  } catch (error) {
    ttsEditor.value.task = { status: 'failed', message: error.message || '无法启动单句重配音' }
  }
}

function formatTimingRange(timing) {
  if (!timing || !Number.isFinite(Number(timing.start)) || !Number.isFinite(Number(timing.end))) return '暂无时间'
  const asClock = (seconds) => {
    const value = Math.max(0, Number(seconds) || 0)
    const minutes = Math.floor(value / 60)
    const remainder = value - minutes * 60
    return `${String(minutes).padStart(2, '0')}:${remainder.toFixed(1).padStart(4, '0')}`
  }
  return `${asClock(timing.start)} – ${asClock(timing.end)}`
}

async function adjustEditedTiming(action) {
  const item = selectedVisualTimingItem.value
  if (!visualEditorProjectId.value || !item || visualTimingAdjusting.value) return
  const originalIndex = visualEditor.value.items.findIndex((entry) => entry.id === item.id)
  visualTimingAdjusting.value = true
  try {
    const payload = await api.adjustVisualTiming(visualEditorProjectId.value, item.id, action)
    visualEditor.value = payload
    visualTimingSelectedId.value = item.id
    visualEditor.value.task = { status: 'completed', action: 'timing', message: '画面时序已调整；确认后点击下方“重新渲染”生成新视频。' }
  } catch (error) {
    visualEditor.value.task = { status: 'failed', action: 'timing', message: error.message || '画面时序调整失败' }
  } finally {
    visualTimingAdjusting.value = false
  }
}

async function resetEditedTiming() {
  if (!visualEditorProjectId.value || visualTimingAdjusting.value) return
  if (!window.confirm('恢复所有画面到首次调整前的字幕时序？重绘后的图片和提示词不会受影响。')) return
  visualTimingAdjusting.value = true
  try {
    const payload = await api.resetVisualTiming(visualEditorProjectId.value)
    visualEditor.value = payload
    visualEditor.value.task = { status: 'completed', action: 'timing', message: '已恢复初始画面时序；确认后可重新渲染视频。' }
  } catch (error) {
    visualEditor.value.task = { status: 'failed', action: 'timing', message: error.message || '恢复初始时序失败' }
  } finally {
    visualTimingAdjusting.value = false
  }
}

async function commitEditedTiming() {
  if (!visualEditorProjectId.value || visualTimingAdjusting.value) return
  if (!window.confirm('将当前所有画面与字幕的分配保存为新的初始时序？\n\n以后点击“恢复初始时序”将恢复到这次保存的状态；旧基准仍会归档保留。')) return
  visualTimingAdjusting.value = true
  try {
    const payload = await api.commitVisualTiming(visualEditorProjectId.value)
    visualEditor.value = payload
    visualEditor.value.task = { status: 'completed', action: 'commit_timing_baseline', message: '当前画面时序已保存为新的初始时序。' }
  } catch (error) {
    visualEditor.value.task = { status: 'failed', action: 'commit_timing_baseline', message: error.message || '保存当前时序失败' }
  } finally {
    visualTimingAdjusting.value = false
  }
}

async function restoreSelectedVisualTimingHistory() {
  if (!visualEditorProjectId.value || !selectedVisualTimingHistory.value || visualTimingAdjusting.value) return
  const selected = visualEditor.value.timing_history?.find((item) => item.id === selectedVisualTimingHistory.value)
  if (!window.confirm(`切换到历史时序“${selected?.label || selectedVisualTimingHistory.value}”？\n\n当前保存的初始时序不会被覆盖，仍可点击“恢复初始时序”返回。`)) {
    selectedVisualTimingHistory.value = ''
    return
  }
  visualTimingAdjusting.value = true
  try {
    const payload = await api.restoreVisualTimingHistory(visualEditorProjectId.value, selectedVisualTimingHistory.value)
    visualEditor.value = payload
    visualEditor.value.task = { status: 'completed', action: 'restore_timing_history', message: '已切换到所选历史时序；满意后可保存为新的初始时序。' }
  } catch (error) {
    visualEditor.value.task = { status: 'failed', action: 'restore_timing_history', message: error.message || '读取历史时序失败' }
    selectedVisualTimingHistory.value = ''
  } finally {
    visualTimingAdjusting.value = false
  }
}

async function removeEditedTimingPicture() {
  const item = selectedVisualTimingItem.value
  if (!visualEditorProjectId.value || !item || visualTimingAdjusting.value) return
  const originalIndex = visualEditor.value.items.findIndex((entry) => entry.id === item.id)
  const sentenceCount = item.timing?.sentences?.length || 0
  if (!window.confirm(`移除 ${item.id} 这张画面？它本身不会从磁盘删除，但覆盖的 ${sentenceCount} 句字幕会按顺序尽量平均分给相邻画面。可使用“恢复初始时序”撤销。`)) return
  visualTimingAdjusting.value = true
  try {
    const payload = await api.removeVisualTimingPicture(visualEditorProjectId.value, item.id)
    visualEditor.value = payload
    visualTimingSelectedId.value = visualEditor.value.items[Math.max(0, originalIndex - 1)]?.id
      || visualEditor.value.items[0]?.id || ''
    visualEditor.value.task = { status: 'completed', action: 'timing_remove', message: `${item.id} 已从时序移除；确认后点击下方“重新渲染”生成新视频。` }
  } catch (error) {
    visualEditor.value.task = { status: 'failed', action: 'timing_remove', message: error.message || '移除画面失败' }
  } finally {
    visualTimingAdjusting.value = false
  }
}

function stopVisualEditorTaskPolling() {
  if (visualEditorTaskTimer) window.clearInterval(visualEditorTaskTimer)
  visualEditorTaskTimer = null
}

async function pollVisualEditorTaskStatus() {
  if (!visualEditorOpen.value || !visualEditorProjectId.value) return
  try {
    const status = await api.visualEditorStatus(visualEditorProjectId.value)
    visualEditor.value.task = status.task || visualEditor.value.task
    let changedImage = false
    for (const item of visualEditor.value.items) {
      const previousStatus = item.task?.status
      const nextTask = status.image_tasks?.[item.id] || { status: 'idle', message: '' }
      item.task = nextTask
      if (previousStatus === 'running' && nextTask.status === 'completed') {
        item.image_url = `${item.image_url.split('?')[0]}?v=${Date.now()}`
        changedImage = true
      }
    }
    if (!status.has_active_image_tasks && status.task?.status !== 'running') stopVisualEditorTaskPolling()
  } catch {
    // The main log remains the source of truth if a short status request fails.
  }
}

function startVisualEditorTaskPolling() {
  if (visualEditorTaskTimer) return
  visualEditorTaskTimer = window.setInterval(pollVisualEditorTaskStatus, 1800)
}

async function selectVisualEditorProject() {
  if (!visualEditorProjectId.value) return
  resetTtsSegmentAudio()
  clearVisualReferenceImages()
  try {
    activeJob.value = await api.job(visualEditorProjectId.value)
  } catch {
    // The editor can still be loaded even if the task list has just refreshed.
  }
  selectedTtsSegmentIndices.value = []
  await Promise.all([loadVisualEditor({ hydrateBgm: true }), loadTtsEditor()])
}

async function toggleVisualEditor() {
  visualEditorOpen.value = !visualEditorOpen.value
  if (visualEditorOpen.value) {
    const payload = await api.visualEditorProjects()
    visualEditorProjects.value = payload.projects || []
    if (!visualEditorProjectId.value || !visualEditorProjects.value.some((item) => item.id === visualEditorProjectId.value)) {
      const activeMatch = visualEditorProjects.value.find((item) => item.id === activeJob.value?.id)
      visualEditorProjectId.value = activeMatch?.id || visualEditorProjects.value[0]?.id || ''
    }
    await selectVisualEditorProject()
  }
  else {
    stopVisualEditorTaskPolling()
    stopTtsEditorPolling()
    visualPreviewItem.value = null
  }
}

async function redrawVisualImage(item) {
  if (!visualEditorProjectId.value) {
    visualEditor.value.task = { status: 'failed', message: '请先选择要编辑的项目。' }
    return
  }
  if (!item.prompt.trim()) {
    item.task = { status: 'failed', action: 'redraw', message: '提示词为空，无法重绘。' }
    visualEditor.value.task = { status: 'failed', message: `${item.id} 的提示词为空，无法重绘。` }
    return
  }
  const usesCurrentReference = visualReferenceOwnerMacroId.value === item.id
  const referenceCount = usesCurrentReference
    ? (visualSelfReferenceMacroId.value ? 1 : 0) + visualReferenceUploads.value.length
    : 0
  const referenceNote = referenceCount ? `，使用 ${referenceCount} 张参考图` : ''
  // Mark the card before the request completes. This gives immediate feedback
  // and prevents repeat clicks while the browser is waiting for the API.
  item.task = { status: 'running', action: 'redraw', message: `正在提交重绘${referenceNote}` }
  try {
    activeJob.value = await api.job(visualEditorProjectId.value)
    await api.redrawVisualImage(
      visualEditorProjectId.value,
      item.id,
      item.prompt,
      usesCurrentReference && visualSelfReferenceMacroId.value ? [visualSelfReferenceMacroId.value] : [],
      usesCurrentReference ? visualReferenceUploads.value.map((asset) => asset.id) : [],
    )
    item.task = { status: 'running', action: 'redraw', message: `重绘中${referenceNote}` }
    visualEditor.value.task = { status: 'running', action: 'redraw', message: `${item.id} 已开始重绘${referenceNote}。` }
    startVisualEditorTaskPolling()
  } catch (error) {
    item.task = { status: 'failed', action: 'redraw', message: error.message || '图片重绘失败' }
    visualEditor.value.task = { status: 'failed', action: 'redraw', message: `${item.id} 重绘未启动：${error.message || '未知错误'}` }
  }
}

function beginVisualReferenceSelection(itemId) {
  if (visualReferenceOwnerMacroId.value && visualReferenceOwnerMacroId.value !== itemId) {
    clearVisualReferenceImages()
  }
  visualReferenceOwnerMacroId.value = itemId
}

function toggleVisualSelfReferenceImage(itemId) {
  beginVisualReferenceSelection(itemId)
  visualSelfReferenceMacroId.value = visualSelfReferenceMacroId.value === itemId ? '' : itemId
}

function clearVisualReferenceImages() {
  visualSelfReferenceMacroId.value = ''
  visualReferenceUploads.value = []
  visualReferenceOwnerMacroId.value = ''
}

async function uploadVisualReferenceImages(event, itemId) {
  const input = event.target
  const files = Array.from(input.files || [])
  input.value = ''
  if (!files.length) return
  beginVisualReferenceSelection(itemId)
  const slots = 3 - visualReferenceUploads.value.length
  if (slots <= 0) {
    visualEditor.value.task = { ...visualEditor.value.task, status: 'idle', message: '本地重绘参考图最多上传 3 张。' }
    return
  }
  const selected = files.slice(0, slots)
  if (selected.some((file) => !['jpg', 'jpeg', 'png', 'webp'].includes(file.name.split('.').pop()?.toLowerCase()))) {
    visualEditor.value.task = { ...visualEditor.value.task, status: 'failed', message: '重绘参考图仅支持 JPG、JPEG、PNG 或 WebP。' }
    return
  }
  visualReferenceUploading.value = true
  try {
    const uploaded = await Promise.all(selected.map((file) => api.uploadEditorAsset(file)))
    if (uploaded.some((payload) => payload.asset?.kind !== 'image')) throw new Error('上传文件不是可用图片。')
    visualReferenceUploads.value = [
      ...visualReferenceUploads.value,
      ...uploaded.map((payload, index) => ({ id: payload.asset.id, name: payload.asset.name || selected[index].name })),
    ].slice(0, 3)
    visualEditor.value.task = { ...visualEditor.value.task, status: 'idle', message: `已添加 ${selected.length} 张本地重绘参考图。` }
  } catch (error) {
    visualEditor.value.task = { ...visualEditor.value.task, status: 'failed', message: error.message || '重绘参考图上传失败。' }
  } finally {
    visualReferenceUploading.value = false
  }
}

async function uploadVisualImage(event, item) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || !visualEditorProjectId.value) return
  try {
    await api.uploadVisualImage(visualEditorProjectId.value, item.id, file)
    item.task = { status: 'running', action: 'upload', message: '替换中' }
    startVisualEditorTaskPolling()
  } catch (error) {
    visualEditor.value.task = { status: 'failed', message: error.message || '图片替换失败' }
  }
}

async function undoVisualImage(item) {
  if (!visualEditorProjectId.value) return
  try {
    await api.undoVisualImage(visualEditorProjectId.value, item.id)
    await loadVisualEditor()
  } catch (error) {
    visualEditor.value.task = { status: 'failed', message: error.message || '没有可撤回的图片版本' }
  }
}

async function resetVisualImagePrompt(item) {
  if (!visualEditorProjectId.value) return
  try {
    const payload = await api.resetVisualPrompt(visualEditorProjectId.value, item.id)
    item.prompt = payload.prompt || item.prompt
    await loadVisualEditor()
  } catch (error) {
    visualEditor.value.task = { status: 'failed', message: error.message || '没有可重置的初始提示词' }
  }
}

async function commitVisualBaseline(item) {
  if (!visualEditorProjectId.value) return
  if (!window.confirm(`将 ${item.id} 当前显示的图片和提示词确认为新的原图？\n\n以后重置提示词会回到此版本，撤回也不会越过此版本；旧版本仍会归档保留。`)) return
  try {
    item.task = { status: 'running', action: 'commit_baseline', message: '正在确认新原图' }
    const payload = await api.commitVisualBaseline(visualEditorProjectId.value, item.id, item.prompt)
    visualEditor.value.task = { status: 'completed', action: 'commit_baseline', message: payload.message || `${item.id} 已确认为新的原图。` }
    await loadVisualEditor({ preservePage: true })
  } catch (error) {
    item.task = { status: 'failed', action: 'commit_baseline', message: error.message || '确认新原图失败' }
    visualEditor.value.task = { status: 'failed', action: 'commit_baseline', message: error.message || '确认新原图失败' }
  }
}

async function commitAllVisualBaselines() {
  if (!visualEditorProjectId.value) return
  if (!window.confirm('将该项目当前全部图片及其提示词确认为新的原图？\n\n适合在全部重绘满意后使用。旧原图和撤回记录仍会归档保留。')) return
  try {
    visualEditorLoading.value = true
    const payload = await api.commitAllVisualBaselines(visualEditorProjectId.value)
    await loadVisualEditor({ preservePage: true })
    visualEditor.value.task = { status: 'completed', action: 'commit_all_baselines', message: payload.message || '已确认全部当前图片。' }
  } catch (error) {
    visualEditor.value.task = { status: 'failed', action: 'commit_all_baselines', message: error.message || '确认全部新原图失败' }
  } finally {
    visualEditorLoading.value = false
  }
}

async function renderEditedVideo() {
  if (!visualEditorProjectId.value) return
  prepareCompletionAlerts(true)
  try {
    if (visualBgm.enabled && !visualBgm.tracks.length) {
      visualBgmError.value = '已开启 BGM，请先上传至少一首音乐。'
      return
    }
    const renderPayload = {
      mode: visualRenderMode.value,
      bgm_enabled: Boolean(visualBgm.enabled),
      bgm_tracks: visualBgm.tracks.map((track) => ({
        asset_id: track.asset_id || null,
        archived_filename: track.archived_filename || null,
        volume_db: Number(track.volume_db ?? -10),
        duration_seconds: Number.isFinite(Number(track.duration_seconds)) ? Number(track.duration_seconds) : null,
      })),
      bgm_fade_enabled: Boolean(visualBgm.fade_enabled),
      bgm_fade_duration: Number(visualBgm.fade_duration || 1),
    }
    await api.renderVisualEditor(visualEditorProjectId.value, renderPayload)
    activeJob.value = await api.job(visualEditorProjectId.value)
    visualEditor.value.task = {
      status: 'running',
      action: 'render',
      message: visualBgm.enabled && visualBgm.tracks.length
        ? `已开始重新渲染，并应用当前项目设置的 ${visualBgm.tracks.length} 首 BGM。`
        : '已开始重新渲染，进度显示在上方主进度条。',
    }
    startVisualEditorTaskPolling()
  } catch (error) {
    visualEditor.value.task = { status: 'failed', action: 'render', message: error.message || '重新渲染启动失败' }
  }
}

async function cancelVisualRender() {
  if (!visualEditorProjectId.value) return
  try {
    const payload = await api.cancelVisualRender(visualEditorProjectId.value)
    visualEditor.value.task = {
      status: payload.ok ? 'cancelled' : 'failed',
      action: 'render',
      message: payload.message || '已请求停止重新渲染。',
    }
  } catch (error) {
    visualEditor.value.task = { status: 'failed', action: 'render', message: error.message || '停止渲染失败' }
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

async function uploadReferenceImages(event) {
  const input = event.target
  const files = Array.from(input.files || [])
  input.value = ''
  protagonistReferenceImageError.value = ''
  if (!files.length) return
  const availableSlots = 3 - form.reference_image_ids.length
  if (availableSlots <= 0) {
    protagonistReferenceImageError.value = '最多只能保留 3 张角色参考图。'
    return
  }
  const selectedFiles = files.slice(0, availableSlots)
  if (files.length > availableSlots) {
    protagonistReferenceImageError.value = `最多只能保留 3 张，本次仅添加前 ${availableSlots} 张。`
  }
  if (selectedFiles.some((file) => !['jpg', 'jpeg', 'png', 'webp'].includes(file.name.split('.').pop()?.toLowerCase()))) {
    protagonistReferenceImageError.value = '参考图仅支持 JPG、JPEG、PNG 或 WebP。'
    return
  }
  if (selectedFiles.some((file) => file.size > 30 * 1024 * 1024)) {
    protagonistReferenceImageError.value = '参考图不能超过 30 MB。'
    return
  }
  protagonistReferenceUploading.value = true
  try {
    const uploaded = await Promise.all(selectedFiles.map((file) => api.uploadEditorAsset(file)))
    if (uploaded.some((payload) => payload.asset?.kind !== 'image')) throw new Error('上传文件不是可用的图片。')
    form.reference_image_ids = [
      ...form.reference_image_ids,
      ...uploaded.map((payload) => payload.asset.id),
    ].slice(0, 3)
    form.protagonist_reference_image_id = form.reference_image_ids[0] || ''
    referenceImageNames.value = [
      ...referenceImageNames.value,
      ...uploaded.map((payload, index) => payload.asset.name || selectedFiles[index].name),
    ].slice(0, 3)
  } catch (error) {
    protagonistReferenceImageError.value = error.message || '角色参考图上传失败。'
  } finally {
    protagonistReferenceUploading.value = false
  }
}

function removeReferenceImage(index) {
  form.reference_image_ids = form.reference_image_ids.filter((_, currentIndex) => currentIndex !== index)
  form.protagonist_reference_image_id = form.reference_image_ids[0] || ''
  referenceImageNames.value = referenceImageNames.value.filter((_, currentIndex) => currentIndex !== index)
  protagonistReferenceImageError.value = ''
}

function generationRequestPayload() {
  const resolvedCloudVoice = effectiveCloudVoice.value
  const payload = {
    ...form,
    tts_engine: ttsEngine.value,
    tts_emotion: form.tts_emotion || null,
    tts_pronunciation: form.tts_pronunciation || null,
    ...(ttsEngine.value === 'cluster' && resolvedCloudVoice ? {
      cluster_voice_type: resolvedCloudVoice.type === 'preset' ? 'preset' : 'uploaded',
      cluster_voice_id: resolvedCloudVoice.id,
    } : {}),
  }
  // An empty cluster voice is a valid idle UI state, but it must not be sent
  // to non-cluster jobs where the backend correctly enforces a real voice ID.
  if (ttsEngine.value !== 'cluster') delete payload.cluster_voice_id
  return payload
}

async function runManualPreflight() {
  if (!session.value.user || preflightRunning.value) return
  preflightResult.value = null
  preflightOpen.value = true
  preflightRunning.value = true
  try {
    preflightResult.value = await api.preflightJob(generationRequestPayload())
  } catch (error) {
    const errorMessage = String(error?.message || '')
    const staleBackend = /method not allowed|\b405\b/i.test(errorMessage)
    preflightResult.value = {
      ok: false,
      error_count: 1,
      warning_count: 0,
      message: staleBackend ? '后台服务尚未更新' : '启动前体检未完成',
      items: [{
        id: 'preflight_api',
        label: '体检服务',
        status: 'error',
        message: staleBackend
          ? '当前仍是修改前启动的旧后台进程。请关闭程序并重新启动一次，任务和产物不会受影响。'
          : (errorMessage || '无法连接后端体检接口'),
      }],
    }
  } finally {
    preflightRunning.value = false
  }
}

function closePreflight() {
  if (preflightRunning.value) return
  preflightOpen.value = false
  preflightResult.value = null
}

async function submit() {
  if (!session.value.user) {
    authError.value = '请先登录后再生成视频'
    return
  }
  if (!canSubmitGeneration.value) return
  generationSubmitMessage.value = ''
  prepareCompletionAlerts(true)
  followLiveJob.value = true
  submitting.value = true
  try {
    activeJob.value = await api.createJob(generationRequestPayload())
    jobPage.value = 1
    await refresh()
  } catch (error) {
    generationSubmitMessage.value = error.message || '无法创建任务，请稍后重试。'
  } finally {
    submitting.value = false
  }
}

async function selectJob(id, replace = true) {
  const payload = await api.job(id)
  if (replace) followLiveJob.value = false
  if (replace) activeJob.value = payload
  else activeJob.value = payload
  // Background refreshes also call selectJob(..., false). They must not close
  // the post-production editor the user is currently working in.
  if (replace) {
    visualEditorOpen.value = false
  }
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

async function deleteGenerationJob(job) {
  if (!job?.id) return
  const name = job.request?.project_name || job.id
  if (!window.confirm(`确定删除任务“${name}”？\n将同时删除它的专属 workspace、output/TTS_Output 归档和日志，此操作不可撤销。`)) return
  try {
    await api.deleteJob(job.id)
    if (activeJob.value?.id === job.id) activeJob.value = null
    if (module1Job.value?.id === job.id) module1Job.value = null
    if (subtitleJob.value?.id === job.id) subtitleJob.value = null
    await refresh()
    if (!jobs.value.length && jobPage.value > 1) await changeJobPage(jobPage.value - 1)
  } catch (error) {
    window.alert(error.message || '删除任务失败')
  }
}

async function submitModule1() {
  if (!canSubmitModule1.value) return
  submittingModule1.value = true
  try {
    module1Job.value = await api.createJob({
      ...form,
      tts_engine: ttsEngine.value,
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

async function uploadSubtitleAudio(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  subtitleAudioError.value = ''
  if (!file) return
  const suffix = file.name.split('.').pop()?.toLowerCase()
  if (!['mp3', 'wav', 'm4a', 'aac', 'flac', 'ogg', 'mp4', 'mov', 'mkv', 'webm', 'avi', 'm4v'].includes(suffix)) {
    subtitleAudioName.value = ''
    subtitleForm.source_audio_id = ''
    subtitleAudioError.value = '仅支持 MP3、WAV、M4A、AAC、FLAC、OGG 音频，或 MP4、MOV、MKV、WebM、AVI、M4V 视频。'
    return
  }
  subtitleAudioUploading.value = true
  try {
    const payload = await api.uploadEditorAsset(file)
    if (!['audio', 'video'].includes(payload.asset?.kind)) throw new Error('上传文件不是可识别的音频或视频。')
    subtitleForm.source_audio_id = payload.asset.id
    subtitleAudioName.value = payload.asset.name || file.name
    await refreshEditor()
  } catch (error) {
    subtitleForm.source_audio_id = ''
    subtitleAudioName.value = ''
    subtitleAudioError.value = error.message || '音频或视频上传失败。'
  } finally {
    subtitleAudioUploading.value = false
  }
}

async function loadSubtitleReference(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  subtitleReferenceError.value = ''
  if (!file) return
  if (!['txt', 'md'].includes(file.name.split('.').pop()?.toLowerCase())) {
    subtitleReferenceName.value = ''
    subtitleForm.reference_text = ''
    subtitleReferenceError.value = '参考文案仅支持 TXT 或 Markdown 文件。'
    return
  }
  if (file.size > MAX_SCRIPT_FILE_SIZE) {
    subtitleReferenceError.value = '参考文案不能超过 2 MB。'
    return
  }
  try {
    const content = (await file.text()).trim()
    if (!content) throw new Error('参考文案为空。')
    subtitleForm.reference_text = content
    subtitleReferenceName.value = file.name
  } catch (error) {
    subtitleReferenceName.value = ''
    subtitleForm.reference_text = ''
    subtitleReferenceError.value = error.message || '读取参考文案失败。'
  }
}

async function loadSubtitleFonts() {
  if (subtitleFontsLoading.value || subtitleFonts.value.length) return
  subtitleFontsLoading.value = true
  try {
    const payload = await api.subtitleFonts()
    subtitleFonts.value = Array.isArray(payload.fonts) ? payload.fonts : []
    if (subtitleFonts.value.length && !subtitleFonts.value.includes(subtitleRenderForm.font_name)) {
      subtitleRenderForm.font_name = subtitleFonts.value.includes('Microsoft YaHei')
        ? 'Microsoft YaHei'
        : subtitleFonts.value[0]
    }
  } catch (error) {
    subtitleRenderMessage.value = error.message || '读取本机字体失败，将使用默认字体。'
  } finally {
    subtitleFontsLoading.value = false
  }
}

async function uploadSubtitleBgmTrack(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  subtitleBgmUploading.value = true
  subtitleBgmError.value = ''
  try {
    const durationSeconds = await readAudioDuration(file)
    const payload = await api.uploadEditorAsset(file)
    if (payload.asset?.kind !== 'audio') throw new Error('上传文件不是可识别的音频。')
    subtitleRenderForm.bgm_tracks.push({
      asset_id: payload.asset.id,
      name: payload.asset.name || file.name,
      volume_db: -10,
      duration_seconds: durationSeconds,
      url: payload.asset.url || '',
    })
    if (!editorAssets.value.some((asset) => asset.id === payload.asset.id)) {
      editorAssets.value.push(payload.asset)
    }
  } catch (error) {
    subtitleBgmError.value = error.message || 'BGM 上传失败'
  } finally {
    subtitleBgmUploading.value = false
  }
}

function removeSubtitleBgmTrack(index) {
  const [track] = subtitleRenderForm.bgm_tracks.splice(index, 1)
  if (track && bgmPreviewTrack.value === track) stopBgmPreview()
}

async function renderSubtitleVideo() {
  if (!canRenderSubtitleVideo.value || !subtitleJob.value?.id) return
  subtitleRenderMessage.value = ''
  try {
    subtitleJob.value = await api.renderSubtitleVideo(subtitleJob.value.id, { ...subtitleRenderForm })
    subtitleRenderMessage.value = '已开始渲染，进度会显示在下方字幕任务日志中。'
    await refresh()
  } catch (error) {
    subtitleRenderMessage.value = error.message || '字幕渲染启动失败。'
  }
}

async function submitSubtitleJob() {
  if (!canSubmitSubtitle.value) return
  submittingSubtitle.value = true
  try {
    subtitleJob.value = await api.createJob({
      project_name: subtitleForm.project_name,
      script: subtitleForm.reference_text,
      subtitle_only: true,
      subtitle_use_correction: subtitleForm.use_correction,
      skip_tts: true,
      source_audio_id: subtitleForm.source_audio_id,
      skip_text_correction: !subtitleForm.use_correction,
    })
    jobPage.value = 1
    await refresh()
  } finally {
    submittingSubtitle.value = false
  }
}

async function cancelSubtitleJob() {
  if (!subtitleJobRunning.value || !subtitleJob.value?.id) return
  subtitleJob.value = await api.cancelJob(subtitleJob.value.id)
  await refresh()
}

function syncStepAudioMetadata() {
  const player = stepAudioPlayer.value
  stepAudioDuration.value = Number.isFinite(player?.duration) ? player.duration : 0
  stepAudioCurrentTime.value = Number(player?.currentTime || 0)
}

function syncStepAudioProgress() {
  const player = stepAudioPlayer.value
  stepAudioCurrentTime.value = Number(player?.currentTime || 0)
}

async function toggleStepAudioPlayback() {
  const player = stepAudioPlayer.value
  if (!player) return
  if (player.paused) {
    try {
      await player.play()
      stepAudioPlaying.value = true
    } catch {
      stepAudioPlaying.value = false
    }
  } else {
    player.pause()
    stepAudioPlaying.value = false
  }
}

function seekStepAudio(event) {
  const player = stepAudioPlayer.value
  const target = Number(event.target.value)
  if (!player || !Number.isFinite(target)) return
  player.currentTime = target
  stepAudioCurrentTime.value = target
}

async function saveStepAudioAs() {
  if (!stepModeAudioUrl.value || savingStepAudio.value) return
  savingStepAudio.value = true
  stepAudioSaveMessage.value = ''
  try {
    const response = await fetch(stepModeAudioUrl.value, { credentials: 'include' })
    if (!response.ok) throw new Error(`下载配音失败（HTTP ${response.status}）`)
    const blob = await response.blob()
    const safeProjectName = String(activeJob.value?.request?.project_name || form.project_name || '本次任务')
      .replace(/[\\/:*?"<>|]+/g, '_')
      .slice(0, 80)
    const suggestedName = `${safeProjectName}_配音.wav`
    if (typeof window.showSaveFilePicker === 'function') {
      const handle = await window.showSaveFilePicker({
        suggestedName,
        types: [{ description: 'WAV 音频', accept: { 'audio/wav': ['.wav'] } }],
      })
      const writable = await handle.createWritable()
      await writable.write(blob)
      await writable.close()
      stepAudioSaveMessage.value = `配音已另存为：${handle.name || suggestedName}`
    } else {
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = suggestedName
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      stepAudioSaveMessage.value = '浏览器不支持选择保存目录，已转为普通下载。'
    }
  } catch (error) {
    if (error?.name !== 'AbortError') {
      stepAudioSaveMessage.value = error.message || '配音保存失败'
    }
  } finally {
    savingStepAudio.value = false
  }
}

function formatStepAudioTime(value) {
  const seconds = Math.max(0, Number(value) || 0)
  const minutes = Math.floor(seconds / 60)
  return `${String(minutes).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
}

async function retryTts() {
  if (!canRetryTts.value || !activeJob.value?.id || retryingTts.value) return
  if (!window.confirm('重新配音会清理本次任务当前的中间产物，并从模块 1 重新开始。是否继续？')) return
  stepAudioPlayer.value?.pause()
  stepAudioPlaying.value = false
  stepAudioCurrentTime.value = 0
  retryingTts.value = true
  try {
    activeJob.value = await api.retryJobTts(activeJob.value.id)
    await refresh()
  } finally {
    retryingTts.value = false
  }
}

async function resumeGeneration() {
  if ((!canResumeGeneration.value && !canContinueStepMode.value) || !activeJob.value?.id) return
  prepareCompletionAlerts(true)
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
    waiting_confirmation: '等待确认',
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
    story_context: 'Agent 0 全文资料',
    story_plan: 'Agent 1 时间轴分镜',
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
  originalDocumentTitle = document.title || '一键生成视频 / One-Click VidGen'
  window.addEventListener('focus', stopCompletionFlash)
  document.addEventListener('visibilitychange', stopCompletionFlash)
  await loadSettings()
  await refresh()
  await refreshParameterPresets()
  await refreshAgentPromptPresets()
  await refreshCloudState()
  timer = window.setInterval(refresh, 2500)
})

watch(ttsEngine, (engine) => {
  if (engine !== 'cluster') stopCloudVoicePreview()
  if (engine === 'cluster') void refreshCloudState()
})

watch(() => `${form.cluster_voice_type}:${form.cluster_voice_id}`, () => {
  if (cloudVoicePreviewAudio) stopCloudVoicePreview()
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
  stopVisualEditorTaskPolling()
  stopTtsVoicePreview()
  stopCloudVoicePreview()
  resetTtsSegmentAudio()
  stopBgmPreview()
  stopCompletionFlash()
  window.removeEventListener('focus', stopCompletionFlash)
  document.removeEventListener('visibilitychange', stopCompletionFlash)
  if (completionAudioContext && completionAudioContext.state !== 'closed') {
    completionAudioContext.close().catch(() => {})
  }
})
</script>
