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

      <div v-if="session.user" class="sidebar-card api-key-card">
        <div class="sidebar-label">模型 API Key</div>
        <div class="muted small">密钥仅保存到本机 `.env`，页面不会回显原文。</div>
        <label>
          <span>语言模型 API Key</span>
          <input v-model="apiKeyForm.language_api_key" type="password" autocomplete="off" placeholder="Gemini / RunningHub LLM" />
        </label>
        <div class="api-key-status" :class="{ configured: apiKeyStatus.language?.configured }">
          {{ apiKeyStatus.language?.configured ? '已配置' : '未配置' }}
        </div>
        <label>
          <span>图像模型 API Key</span>
          <input v-model="apiKeyForm.image_api_key" type="password" autocomplete="off" placeholder="RunningHub Image2" />
        </label>
        <div class="api-key-status" :class="{ configured: apiKeyStatus.image?.configured }">
          {{ apiKeyStatus.image?.configured ? '已配置' : '未配置' }}
        </div>
        <label>
          <span>通用 API Key</span>
          <input v-model="apiKeyForm.common_api_key" type="password" autocomplete="off" placeholder="仅填此项会同时用于语言和图像" />
        </label>
        <div class="api-key-status" :class="{ configured: apiKeyStatus.common?.configured }">
          {{ apiKeyStatus.common?.configured ? '已配置' : '未配置' }}
        </div>
        <div class="muted small">填写通用 Key 时，会自动补全未单独填写的语言与图像 Key。</div>
        <div v-if="apiKeyMessage" class="api-key-message">{{ apiKeyMessage }}</div>
        <button class="primary-btn full-btn" type="button" :disabled="savingApiKeys" @click="saveApiKeySettings">
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
    </aside>

    <main class="main">
      <header class="topbar">
        <button class="icon-btn" type="button" @click="sidebarOpen = !sidebarOpen" aria-label="切换侧边栏">
          <span></span><span></span><span></span>
        </button>
        <div class="topbar-copy">
          <div class="eyebrow">工作台</div>
          <div class="topbar-title">{{ activePage === 'workspace' ? '故事视频生成工作台' : activePage === 'development' ? '待开发功能' : activePage === 'subtitle' ? '模块 2 · 字幕识别' : '模块 1 · 仅配音' }}</div>
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
          <button
            type="button"
            :class="{ active: activePage === 'subtitle' }"
            @click="activePage = 'subtitle'"
          >
            <span>模块 2 · 字幕识别</span>
            <small>音频转 SRT 与校对</small>
          </button>
          <div class="page-tabs-actions">
            <template v-if="session.user">
              <button class="toolbar-save-button" type="button" :disabled="savingParameterPreset" @click="saveCurrentParameterPreset">
                {{ savingParameterPreset ? '保存中…' : '保存当前参数' }}
              </button>
              <select v-model="selectedParameterPreset" class="parameter-preset-select" :disabled="loadingParameterPresets || !parameterPresets.length">
                <option value="">读取已保存参数</option>
                <option v-for="preset in parameterPresets" :key="preset.name" :value="preset.name">{{ preset.name }}</option>
              </select>
              <button class="toolbar-load-button" type="button" :disabled="!selectedParameterPreset || loadingParameterPresets" @click="loadSelectedParameterPreset">读取参数</button>
            </template>
            <span class="status-chip" :class="health.tts_online ? 'success' : 'warning'">
              {{ health.tts_online ? 'IndexTTS2 就绪' : 'IndexTTS2 未就绪' }}
            </span>
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
            </label>
            </div>

            <div class="create-settings-column">
            <div class="create-audio-column">
            <div v-if="!form.skip_tts" class="tts-parameter-panel">
              <div class="tts-parameter-head">
                <div>
                  <div class="tts-engine-row">
                    <div class="sidebar-label">{{ ttsEngine === 'indextts2' ? '官方 IndexTTS2 · 本地 GPU' : 'Qwen-TTS · 云端 API' }}</div>
                    <label class="tts-engine-switch" title="切换本地 IndexTTS2 与 Qwen-TTS 云端配音">
                      <input v-model="ttsEngine" type="checkbox" true-value="qwen" false-value="indextts2" />
                      <span class="tts-engine-track" aria-hidden="true"></span>
                      <span>Qwen-TTS</span>
                    </label>
                  </div>
                  <h3>语音参数</h3>
                </div>
                <span class="muted small">{{ ttsEngine === 'indextts2' ? (settings.tts?.model || 'official IndexTTS2 2.0.0') : 'DashScope / 百炼' }}</span>
              </div>
              <div v-if="ttsEngine === 'indextts2'" class="form-grid tts-param-grid">
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
              <div v-else class="qwen-tts-config">
                <label class="qwen-key-field">
                  <input v-model="apiKeyForm.qwen_tts_api_key" type="password" autocomplete="off" placeholder="DashScope API Key（sk-...）" />
                </label>
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
                <div class="qwen-tts-actions">
                  <span class="api-key-status" :class="{ configured: apiKeyStatus.qwen_tts?.configured }">
                    {{ apiKeyStatus.qwen_tts?.configured ? '已配置' : '未配置' }}
                  </span>
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
                    : form.content_mode === 'general'
                      ? '可自由填写：例如日系治愈动画、赛博朋克电影、写实水墨、儿童绘本等。'
                      : '描述惊悚漫画画风、角色一致性、色彩与悬疑氛围。'"
                ></textarea>
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
                <small class="muted">此处编辑 Agent 2 的画面规划指令；下方可按需展开 Agent 1 的全文规划指令。</small>
              </label>
              <details v-if="form.visual_prompt_mode === 'full'" class="agent1-prompt-editor">
                <summary>Agent 1 全文规划指令 <span class="agent1-risk-label">（高危参数）</span></summary>
                <p class="agent1-danger-note">修改后会直接影响全文理解、人物档案、场景关系与画面节奏规划。必须保留严格 JSON 对象输出与既有字段结构，否则可能导致任务失败或严重画面错乱。</p>
                <label class="stack">
                  <span>完整 Agent 1 指令</span>
                  <textarea v-model="form.agent1_prompt_system" @input="rememberVisualPrompt" rows="18" maxlength="12000" placeholder="保留默认内容即可；仅建议熟悉 JSON 输出结构和全文规划流程的用户修改。"></textarea>
                </label>
              </details>
              </div>
            </section>

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
                <button class="ghost-btn compact-btn" type="button" :disabled="visualEditorLoading" @click="loadVisualEditor({ preservePage: true })">刷新图片</button>
              </div>
              <div class="visual-image-grid">
                <article v-for="item in visibleVisualEditorItems" :key="item.id" class="visual-image-card" :class="{ processing: item.task?.status === 'running' }">
                  <div class="visual-image-actions">
                    <strong>{{ item.id }}</strong>
                    <button type="button" class="icon-action" title="按当前提示词重绘" aria-label="按当前提示词重绘" :disabled="item.task?.status === 'running'" @click="redrawVisualImage(item)">▶</button>
                    <button type="button" class="icon-action" title="撤回图片" aria-label="撤回图片" :disabled="item.task?.status === 'running'" @click="undoVisualImage(item)">↶</button>
                    <button type="button" class="icon-action" title="重置提示词" aria-label="重置提示词" :disabled="item.task?.status === 'running'" @click="resetVisualImagePrompt(item)">↺</button>
                    <label class="icon-action replace-action" title="替换本地 JPG 图片" aria-label="替换本地 JPG 图片">
                      ↕<input type="file" accept="image/jpeg" @change="uploadVisualImage($event, item)" />
                    </label>
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
              <div class="visual-render-footer">
                <label>渲染设置
                  <select v-model="visualRenderMode">
                    <option value="subtitles">仅渲染字幕版</option>
                    <option value="raw">仅渲染无字幕版</option>
                    <option value="both">双版本渲染</option>
                  </select>
                </label>
                <button class="primary-btn" type="button" :disabled="visualEditor.task?.status === 'running' || visualEditor.has_active_image_tasks" @click="renderEditedVideo">
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
                  <span>上传需要识别的音频</span>
                  <label class="script-file-picker">
                    <input type="file" accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,audio/*" @change="uploadSubtitleAudio" />
                    <span>{{ subtitleAudioUploading ? '上传中' : '浏览音频' }}</span>
                    <strong>{{ subtitleAudioName || '选择 WAV / MP3 / M4A / FLAC' }}</strong>
                  </label>
                  <div v-if="subtitleAudioError" class="board-error">{{ subtitleAudioError }}</div>
                </div>
                <div class="muted small">识别会保留音频原始时间轴；长音频会在后台任务中顺序处理。</div>
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

          <article class="panel progress-panel">
            <div class="panel-head">
              <div>
                <div class="eyebrow">字幕任务</div>
                <h2>{{ subtitleJob?.message || '等待上传音频并开始识别' }}</h2>
              </div>
              <span class="progress-percent">{{ subtitleJob?.progress || 0 }}%</span>
            </div>
            <div class="progress-track"><span :style="{ width: `${subtitleJob?.progress || 0}%` }"></span></div>
            <div v-if="subtitleJob?.artifacts?.subtitle" class="artifact-grid module1-artifacts">
              <button class="artifact-card" type="button" @click="openArtifactFolder(subtitleJob.artifacts.subtitle)">
                <div class="artifact-label">最终 SRT 字幕</div>
                <div class="artifact-value">final_short.srt</div>
                <div class="artifact-action">打开所在文件夹</div>
              </button>
            </div>
            <pre class="log-view">{{ subtitleLogText }}</pre>
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
  general: '通用视频',
}
const VISUAL_PROMPT_STYLE_STORAGE_KEY = 'visual_prompt_style_story_v3'
const GLOBAL_CHARACTER_STORAGE_KEY = 'global_character_prompt_v1'
const STORY_ENVIRONMENT_STORAGE_KEY = 'story_environment_prompt_v1'
const AGENT1_PROMPT_STORAGE_KEY = 'agent1_prompt_system_v1'
const VISUAL_PROMPT_MODE_STORAGE_KEY = 'visual_prompt_mode_v2'
const CONTENT_MODE_STORAGE_KEY = 'content_mode_v1'
const VISUAL_PACING_STORAGE_KEY = 'visual_pacing_v1'
const VISUAL_PACING_DEFAULTS = {
  urban_suspense: { min: 6, target: 8, max: 12, slides: 6 },
  science_explainer: { min: 7, target: 9, max: 14, slides: 6 },
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
const folderOpenMessage = ref('')
const visualEditorOpen = ref(false)
const visualEditorLoading = ref(false)
const visualEditor = ref({ items: [], task: { status: 'idle', message: '' }, version: 0 })
const visualEditorProjects = ref([])
const visualEditorProjectId = ref('')
const visualEditorPage = ref(1)
const VISUAL_EDITOR_PAGE_SIZE = 24
const visualPreviewItem = ref(null)
const visualRenderMode = ref('both')
const submitting = ref(false)
const submittingModule1 = ref(false)
const submittingSubtitle = ref(false)
const cancellingGeneration = ref(false)
const resumingGeneration = ref(false)
const health = ref({ ok: false, tts_online: false })
const settings = ref({ scripts: [], tts: { voices: [], emotions: [], defaults: {} } })
const session = ref({ user: null, auth_mode: 'account', mysql: {} })
const authError = ref('')
const activeJob = ref(null)
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
const apiKeyStatus = ref({ language: {}, image: {}, common: {}, qwen_tts: {} })
const apiKeyMessage = ref('')
const savingApiKeys = ref(false)
const savingQwenTtsKey = ref(false)
const qwenTtsKeyMessage = ref('')
const ttsEngine = ref('indextts2')
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
const apiKeyForm = reactive({
  language_api_key: '',
  image_api_key: '',
  common_api_key: '',
  qwen_tts_api_key: '',
})
const parameterPresets = ref([])
const selectedParameterPreset = ref('')
const loadingParameterPresets = ref(false)
const savingParameterPreset = ref(false)
const parameterPresetMessage = ref('')
const agentPromptPresets = ref([])
const selectedAgentPromptPreset = ref('')
const loadingAgentPromptPresets = ref(false)
const savingAgentPromptPreset = ref(false)
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
  qwen_tts_instructions: '',
  qwen_tts_voice: 'Elias',
  visual_backend: 'poster',
  visual_prompt_mode: 'simple',
  visual_pacing_preset: 'auto',
  visual_min_duration: 6,
  visual_target_duration: 8,
  visual_max_duration: 12,
  visual_max_slides: 6,
  visual_style_prompt: '',
  global_character_prompt: '',
  story_environment_prompt: '',
  visual_prompt_system: '',
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
const canSubmitGeneration = computed(() => {
  if (!session.value.user) return false
  if (!form.project_name.trim()) return false
  if (form.skip_tts) {
    if (!form.source_audio_id) return false
    return form.skip_text_correction || form.script.trim().length > 0
  }
  if (ttsEngine.value === 'qwen' && !apiKeyStatus.value.qwen_tts?.configured) return false
  if (ttsEngine.value === 'qwen' && !qwenSelectedVoiceSupportsInstructions.value && form.qwen_tts_instructions.trim()) return false
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
const subtitleJobRunning = computed(() => ['queued', 'running'].includes(subtitleJob.value?.status))
const canSubmitSubtitle = computed(() => Boolean(
  session.value.user
  && subtitleForm.project_name.trim()
  && subtitleForm.source_audio_id
  && !subtitleJobRunning.value
  && (!subtitleForm.use_correction || subtitleForm.reference_text || apiKeyStatus.value.language?.configured),
))
const subtitleLogText = computed(() => (subtitleJob.value?.logs || []).join('\n') || '字幕识别日志会显示在这里。')
const submitButtonText = computed(() => {
  if (!session.value.user) return '请先登录'
  if (submitting.value) return '任务已提交'
  if (form.skip_tts && !form.source_audio_id) return '请先上传配音'
  if (form.skip_tts) return '从已有配音生成视频'
  if (ttsEngine.value === 'qwen' && !apiKeyStatus.value.qwen_tts?.configured) return '请先保存 Qwen-TTS API Key'
  if (ttsEngine.value === 'qwen' && !qwenSelectedVoiceSupportsInstructions.value && form.qwen_tts_instructions.trim()) return '该音色不支持配音描述'
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
    if (activeJob.value?.id) {
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
}

async function loadApiKeySettings() {
  if (!session.value.user) return
  try {
    const payload = await api.apiKeySettings()
    apiKeyStatus.value = payload.keys || { language: {}, image: {}, common: {}, qwen_tts: {} }
    apiKeyStatusLoaded = true
  } catch (error) {
    apiKeyMessage.value = error.message || '无法读取 API Key 配置状态'
  }
}

async function saveApiKeySettings() {
  const payload = {}
  for (const key of ['language_api_key', 'image_api_key', 'common_api_key']) {
    const value = String(apiKeyForm[key] || '').trim()
    if (value) payload[key] = value
  }
  if (!Object.keys(payload).length) {
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
    apiKeyForm.language_api_key = ''
    apiKeyForm.image_api_key = ''
    apiKeyForm.common_api_key = ''
  } catch (error) {
    apiKeyMessage.value = error.message || '保存 API Key 失败。'
  } finally {
    savingApiKeys.value = false
  }
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
    const payload = await api.saveParameterPreset({ name, parameters: { ...form, tts_engine: ttsEngine.value } })
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
    ttsEngine.value = parameters.tts_engine === 'qwen' ? 'qwen' : 'indextts2'
    form.visual_prompt_mode = parameters.visual_prompt_mode === 'full' ? 'full' : 'simple'
    await restoreSavedTtsVoiceLabel()
    rememberVisualPrompt()
    rememberVisualPacing()
    parameterPresetMessage.value = `已读取参数：${payload.name || selectedParameterPreset.value}`
  } catch (error) {
    parameterPresetMessage.value = error.message || '读取参数失败'
  }
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

async function restoreSavedTtsVoiceLabel() {
  const savedVoiceId = String(form.tts_voice_id || '')
  ttsVoiceUploadError.value = ''
  if (!savedVoiceId.startsWith('upload:')) {
    ttsVoiceUploadName.value = ''
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
      return
    }
    ttsVoiceUploadName.value = '已保存的参考音色'
    ttsVoiceUploadError.value = '该参考音色文件当前不存在，请重新上传后再运行。'
  } catch {
    ttsVoiceUploadName.value = '已保存的参考音色'
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

async function loadVisualEditor({ preservePage = false } = {}) {
  if (!visualEditorProjectId.value) return
  visualEditorLoading.value = true
  try {
    visualEditor.value = await api.visualEditor(visualEditorProjectId.value)
    if (!preservePage) visualEditorPage.value = 1
    if (visualEditorPage.value > visualEditorPageCount.value) visualEditorPage.value = visualEditorPageCount.value
  } catch (error) {
    visualEditor.value = { items: [], task: { status: 'failed', message: error.message || '无法读取画面修改资料' }, version: 0 }
  } finally {
    visualEditorLoading.value = false
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
    if (!status.has_active_image_tasks) stopVisualEditorTaskPolling()
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
  try {
    activeJob.value = await api.job(visualEditorProjectId.value)
  } catch {
    // The editor can still be loaded even if the task list has just refreshed.
  }
  await loadVisualEditor()
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
    visualPreviewItem.value = null
  }
}

async function redrawVisualImage(item) {
  if (!visualEditorProjectId.value || !item.prompt.trim()) return
  try {
    activeJob.value = await api.job(visualEditorProjectId.value)
    await api.redrawVisualImage(visualEditorProjectId.value, item.id, item.prompt)
    item.task = { status: 'running', action: 'redraw', message: '重绘中' }
    startVisualEditorTaskPolling()
  } catch (error) {
    item.task = { status: 'failed', action: 'redraw', message: error.message || '图片重绘失败' }
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

async function renderEditedVideo() {
  if (!visualEditorProjectId.value) return
  try {
    await api.renderVisualEditor(visualEditorProjectId.value, visualRenderMode.value)
    activeJob.value = await api.job(visualEditorProjectId.value)
    visualEditor.value.task = { status: 'running', action: 'render', message: '已开始重新渲染，进度显示在上方主进度条。' }
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
      tts_engine: ttsEngine.value,
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

async function uploadSubtitleAudio(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  subtitleAudioError.value = ''
  if (!file) return
  const suffix = file.name.split('.').pop()?.toLowerCase()
  if (!['mp3', 'wav', 'm4a', 'aac', 'flac', 'ogg'].includes(suffix)) {
    subtitleAudioName.value = ''
    subtitleForm.source_audio_id = ''
    subtitleAudioError.value = '仅支持 MP3、WAV、M4A、AAC、FLAC 或 OGG 音频。'
    return
  }
  subtitleAudioUploading.value = true
  try {
    const payload = await api.uploadEditorAsset(file)
    if (payload.asset?.kind !== 'audio') throw new Error('上传文件不是可识别的音频。')
    subtitleForm.source_audio_id = payload.asset.id
    subtitleAudioName.value = payload.asset.name || file.name
    await refreshEditor()
  } catch (error) {
    subtitleForm.source_audio_id = ''
    subtitleAudioName.value = ''
    subtitleAudioError.value = error.message || '音频上传失败。'
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
  await refreshParameterPresets()
  await refreshAgentPromptPresets()
  timer = window.setInterval(refresh, 2500)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
  stopVisualEditorTaskPolling()
})
</script>
