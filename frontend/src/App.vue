<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright (C) 2026 Zhou Ruoyu and He Yun -->
<!-- AGPL-3.0 Section 7 terms: ../../ADDITIONAL_TERMS.md -->

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
          <small class="api-model-field-label">接口来源</small>
          <select v-model="apiKeyForm.language_source" class="language-provider-select" :disabled="form.use_cloud_image_pool" @change="onLanguageSourceChanged">
            <option value="official">官方 API</option>
            <option value="custom">自定义兼容接口（高级）</option>
          </select>
          <small class="api-model-field-label">模型家族</small>
          <select v-model="apiKeyForm.language_provider" class="language-provider-select" :disabled="form.use_cloud_image_pool" @change="onLanguageProviderChanged">
            <option v-for="provider in visibleLanguageProviderOptions" :key="provider.value" :value="provider.value" :disabled="provider.disabled">
              {{ provider.family_label || provider.label }}
            </option>
          </select>
          <small class="api-model-field-label">Agent 模型</small>
          <select v-if="currentLanguageModels.length && !customLanguageProvider" v-model="apiKeyForm.language_model" class="language-provider-select language-model-select" :disabled="form.use_cloud_image_pool" @change="onLanguageModelChanged">
            <option v-for="model in currentLanguageModels" :key="model.value" :value="model.value">{{ model.label }}</option>
          </select>
          <input v-else v-model="apiKeyForm.language_model" class="language-model-input" name="ocv-language-model-id" type="text" autocomplete="one-time-code" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore readonly :disabled="form.use_cloud_image_pool" :placeholder="currentLanguageProvider.configured ? '已保存；不修改可留空' : '填写服务商提供的模型 ID'" @focus="unlockProtectedInput" @change="onLanguageModelChanged" />
          <div v-if="customLanguageProvider" class="api-custom-provider-guide">
            <strong>高级功能：仅支持 OpenAI 兼容接口</strong>
            <label>
              <span>API Base URL</span>
              <input v-model="apiKeyForm.language_api_base_url" name="ocv-language-api-base-url" type="url" inputmode="url" autocomplete="one-time-code" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore readonly :placeholder="currentLanguageProvider.configured ? '已保存；不修改可留空' : '例如 https://api.example.com/v1'" @focus="unlockProtectedInput" />
            </label>
            <label>
              <span>API Key（本地无鉴权可留空）</span>
              <input v-model="apiKeyForm.language_api_key" name="ocv-language-api-secret" type="password" autocomplete="new-password" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore readonly :placeholder="currentLanguageProvider.configured ? '已保存；不修改可留空' : '填写服务商提供的 API Key'" @focus="unlockProtectedInput" />
            </label>
            <small>需兼容 <code>/chat/completions</code>、具备足够上下文长度并能稳定输出 JSON。</small>
            <small class="api-custom-security-note">安全提示：API Key 会随请求发送到此网址，请只填写你信任的服务地址。</small>
            <button class="primary-btn full-btn api-inline-save" type="button" :disabled="savingApiKeys" @click="saveApiKeySettings">
              {{ savingApiKeys ? '保存中…' : '保存语言接口设置' }}
            </button>
          </div>
          <input v-else-if="apiKeyFieldOpen('language')" v-model="apiKeyForm.language_api_key" name="ocv-official-language-api-secret" type="password" autocomplete="new-password" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore readonly :placeholder="`${currentLanguageProviderLabel} API Key`" @focus="unlockProtectedInput" />
          <div v-else-if="!customLanguageProvider" class="api-key-state-bar api-key-pool-state" :class="{ error: !form.use_cloud_image_pool && apiKeyRuntimeErrors.language }">
            <div class="api-key-pool-heading">
              <strong>{{ form.use_cloud_image_pool ? '号池已接管文本模型' : (apiKeyRuntimeErrors.language ? 'ERROR' : `${currentLanguageProviderLabel} · ${currentLanguageModelLabel}`) }}</strong>
              <small v-if="!form.use_cloud_image_pool && apiKeyRuntimeErrors.language">{{ apiKeyRuntimeErrors.language }}</small>
            </div>
            <details v-if="!form.use_cloud_image_pool && !apiKeyRuntimeErrors.language && (currentLanguageProvider.key_hints || []).length" class="api-key-account-details">
              <summary>查看已配置账号（{{ (currentLanguageProvider.key_hints || []).length }}）</summary>
              <div class="api-key-account-list">
                <div v-for="(hint, index) in currentLanguageProvider.key_hints || []" :key="hint + index" class="api-key-account-row">
                  <code>{{ hint }}</code>
                  <button type="button" class="api-key-delete-btn" :disabled="deletingApiKey" :title="`删除此 ${currentLanguageProviderLabel} API Key`" @click="deleteConfiguredApiKey('language', index)">×</button>
                </div>
              </div>
            </details>
            <button v-if="!form.use_cloud_image_pool" class="api-key-corner-add" type="button" title="添加语言模型 API Key" aria-label="添加语言模型 API Key" @click="addApiKeyAccount('language')">＋</button>
          </div>
          <button v-if="!customLanguageProvider && apiKeyFieldOpen('language')" class="primary-btn full-btn api-inline-save" type="button" :disabled="savingApiKeys || form.use_cloud_image_pool" @click="saveApiKeySettings">
            {{ savingApiKeys ? '保存中…' : '保存语言接口设置' }}
          </button>
        </div>
        <div class="api-key-pool-field api-key-entry" :class="{ 'cloud-pool-disabled': form.use_cloud_image_pool }">
          <span>图像模型接口</span>
          <div class="api-custom-provider-guide image-api-config">
            <label>
              <span>API Base URL</span>
              <input v-model="apiKeyForm.image_api_base_url" name="ocv-image-api-base-url" type="url" inputmode="url" autocomplete="one-time-code" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore readonly :disabled="form.use_cloud_image_pool" :placeholder="apiKeyStatus.image?.configured ? '已保存；不修改可留空' : 'https://example.com'" @focus="unlockProtectedInput" />
            </label>
            <label>
              <span>模型 ID</span>
              <input v-model="apiKeyForm.image_model" name="ocv-image-model-id" type="text" autocomplete="one-time-code" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore readonly :disabled="form.use_cloud_image_pool" :placeholder="apiKeyStatus.image?.configured ? '已保存；不修改可留空' : 'rhart-image-g-2'" @focus="unlockProtectedInput" />
            </label>
            <label>
              <span>出图分辨率</span>
              <select v-model="apiKeyForm.image_resolution_preset" :disabled="form.use_cloud_image_pool">
                <option value="1k">1K（推荐）</option>
                <option value="2k">2K</option>
                <option value="4k">4K</option>
                <option value="custom">自定义</option>
              </select>
            </label>
            <label v-if="apiKeyForm.image_resolution_preset === 'custom'">
              <span>自定义分辨率参数</span>
              <input v-model="apiKeyForm.image_resolution_custom" type="text" autocomplete="off" :disabled="form.use_cloud_image_pool" placeholder="填写服务商支持的参数，例如 2048x2048" />
            </label>
            <small><strong>强烈推荐 Image 2（rhart-image-g-2）</strong>，这是当前完整验证的模型。其他模型 ID 不拦截，但服务商接口需兼容 OCV 当前的异步出图协议。</small>
            <small v-if="form.use_cloud_image_pool">号池分辨率由云端统一决定。</small>
            <small class="api-custom-security-note">安全提示：API Key 和图像提示词会发送到此网址，请只填写你信任的服务地址。</small>
          </div>
          <span class="api-model-field-label">API Key</span>
          <template v-if="apiKeyFieldOpen('image')">
            <input v-model="apiKeyForm.image_api_key" name="ocv-image-api-secret" type="password" autocomplete="new-password" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore readonly :disabled="form.use_cloud_image_pool" :placeholder="apiKeyStatus.image?.configured ? '已保存；新增账号时填写' : '填写服务商提供的 API Key'" @focus="unlockProtectedInput" />
          </template>
          <div v-else class="api-key-state-bar api-key-pool-state" :class="{ error: apiKeyRuntimeErrors.image }">
            <div class="api-key-pool-heading">
              <strong>{{ apiKeyRuntimeErrors.image ? 'ERROR' : `已配置 ${apiKeyStatus.image?.count || 0} 个出图账号` }}</strong>
              <small v-if="apiKeyRuntimeErrors.image">{{ apiKeyRuntimeErrors.image }}</small>
            </div>
            <details v-if="!apiKeyRuntimeErrors.image && (apiKeyStatus.image?.key_hints || []).length" class="api-key-account-details">
              <summary>查看已配置账号（{{ (apiKeyStatus.image?.key_hints || []).length }}）</summary>
              <div class="api-key-account-list">
                <div v-for="(hint, index) in apiKeyStatus.image?.key_hints || []" :key="hint + index" class="api-key-account-row">
                  <code>{{ hint }}</code>
                  <button type="button" class="api-key-delete-btn" :disabled="form.use_cloud_image_pool || deletingApiKey" title="删除此图像 API Key" @click="deleteConfiguredApiKey('image', index)">×</button>
                </div>
              </div>
            </details>
            <button class="api-key-corner-add" type="button" :disabled="form.use_cloud_image_pool" title="新增图像模型并行账号" aria-label="新增图像模型并行账号" @click="addApiKeyAccount('image')">＋</button>
          </div>
          <button class="primary-btn full-btn api-inline-save" type="button" :disabled="savingApiKeys || form.use_cloud_image_pool" @click="saveApiKeySettings">
            {{ savingApiKeys ? '保存中…' : '保存图像接口设置' }}
          </button>
          <div class="image-concurrency-panel" :class="{ disabled: form.use_cloud_image_pool }">
            <div class="image-concurrency-summary">
              <div>
                <strong>出图并发</strong>
                <small v-if="form.use_cloud_image_pool">由云端号池服务器动态调度</small>
                <small v-else>{{ imageConcurrencyPreview }}</small>
              </div>
              <select v-model="apiKeyForm.image_concurrency_mode" :disabled="form.use_cloud_image_pool" @change="saveImageConcurrencySettings">
                <option value="auto">自动（推荐）</option>
                <option value="manual">手动限制</option>
              </select>
            </div>
            <details v-if="!form.use_cloud_image_pool" class="image-concurrency-details">
              <summary>高级设置</summary>
              <label>
                <span>单 Key 并发</span>
                <input v-model.number="apiKeyForm.image_per_key_concurrency" type="number" min="1" max="16" />
              </label>
              <label v-if="apiKeyForm.image_concurrency_mode === 'manual'">
                <span>总并发上限</span>
                <input v-model.number="apiKeyForm.image_total_concurrency" type="number" min="1" max="64" />
              </label>
              <button type="button" class="ghost-btn image-concurrency-save" :disabled="savingImageConcurrency" @click="saveImageConcurrencySettings">
                {{ savingImageConcurrency ? '保存中…' : '保存并发设置' }}
              </button>
              <small>三方接口通常保持单 Key 并发 1；官方或高并发接口可按服务商额度提高。</small>
            </details>
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
        <div class="muted small">{{ form.use_cloud_image_pool ? '号池模式不需要填写个人的语言或图像 API Key。' : 'OCV 不指定或推荐第三方接口；请自行选择服务商，并只向可信地址发送 API Key。' }}</div>
        <div v-if="apiKeyMessage" class="api-key-message">{{ apiKeyMessage }}</div>
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
        class="sidebar-secondary-entry sidebar-plugin-entry"
        type="button"
        :class="{ active: activePage === 'plugins' }"
        @click="openPluginsPage"
      >
        扩展与插件
      </button>
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
          <a
            class="cloud-website-entry"
            href="https://oneclickvidgen.com/"
            target="_blank"
            rel="noopener noreferrer"
            title="打开 One-Click VidGen 官网"
          >
            官网
          </a>
          <button
            class="cloud-recharge-entry cloud-top-recharge-entry"
            type="button"
            :disabled="cloudBusy"
            title="使用支付宝为云端账户充值"
            @click="openCloudRecharge"
          >
            充值
          </button>
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
            <input v-model="form.step_mode" type="checkbox" @change="handleStepModeToggle" />
            <span class="page-tab-step-switch" aria-hidden="true"><i></i></span>
            <span class="page-tab-step-copy"><strong>分步模式</strong><small>试听、验图后继续</small></span>
          </label>
          <button
            type="button"
            :class="{ active: activePage === 'module1' }"
            @click="activePage = 'module1'"
          >
            <span>模块 1 · 仅配音</span>
            <small>只运行 IndexTTS-2.5</small>
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
            <div v-if="activePage === 'workspace' && !form.step_mode" class="generation-top-actions">
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
              <div class="launch-generation-control">
                <button class="primary-btn launch-generation-btn" type="button" :disabled="!canSubmitGeneration" @click="submit">
                  {{ submitButtonText }}
                </button>
              </div>
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
          <div
            v-if="activePage === 'workspace' && !form.step_mode && (generationBlockReason || generationSubmitMessage)"
            class="generation-nav-status"
            role="status"
          >
            <small v-if="generationBlockReason" class="generation-block-reason">
              <span>{{ generationBlockReason.code }}</span>{{ generationBlockReason.message }}
            </small>
            <small v-if="generationSubmitMessage" class="generation-submit-message">
              <span>[SUBMIT_FAILED]</span>{{ generationSubmitMessage }}
            </small>
          </div>
        </nav>

        <section v-if="activePage === 'workspace'" class="workspace-page stack" :class="guidedWorkspaceClass">
        <section v-if="form.step_mode" class="guided-workflow-shell" aria-label="分步制作流程">
          <div class="guided-stage-strip">
            <div v-for="stage in guidedStageSteps" :key="stage.key" class="guided-stage-chip" :class="guidedStageChipClass(stage.key)">
              <span>{{ stage.index }}</span>
              <div><strong>{{ stage.label }}</strong><small>{{ stage.description }}</small></div>
            </div>
          </div>
          <div class="guided-stage-console">
            <div>
              <div class="eyebrow">{{ guidedStageEyebrow }}</div>
              <h2>{{ guidedStageTitle }}</h2>
              <p class="muted">{{ guidedStageDescription }}</p>
            </div>
            <div class="guided-stage-actions">
              <button
                v-if="guidedHasExistingWorkflow && guidedStage !== 'completed'"
                class="ghost-btn danger-btn guided-cancel-workflow"
                type="button"
                :disabled="guidedCancelling"
                @click="cancelGuidedWorkflow"
              >{{ guidedCancelling ? '正在取消…' : '取消本次分步任务' }}</button>
              <button v-if="guidedCanStop" class="ghost-btn stop-btn" type="button" :disabled="cancellingGeneration" @click="cancelGeneration">
                {{ cancellingGeneration ? '正在停止…' : '停止当前阶段' }}
              </button>
              <button v-if="guidedCanResume" class="primary-btn guided-primary-action" type="button" :disabled="resumingGeneration" @click="resumeGeneration">
                {{ resumingGeneration ? '正在恢复…' : '继续当前阶段' }}
              </button>
              <button v-else-if="guidedStage === 'audio_setup'" class="primary-btn guided-primary-action" type="button" :disabled="!canSubmitGeneration" @click="submit">
                {{ submitting ? '正在提交…' : '开始生成配音与字幕' }}
              </button>
              <button v-else-if="guidedStage === 'audio_review'" class="primary-btn guided-primary-action" type="button" :disabled="guidedAdvancing || guidedSubtitleSaving || ttsEditor.task?.status === 'running'" @click="advanceGuidedWorkflow('confirm_audio')">
                {{ guidedAdvancing ? '正在确认…' : '确认配音与字幕' }}
              </button>
              <button v-else-if="guidedStage === 'visual_setup'" class="primary-btn guided-primary-action" type="button" :disabled="guidedAdvancing" @click="advanceGuidedWorkflow('start_visual')">
                {{ guidedAdvancing ? '正在启动…' : '确认设置并开始作图' }}
              </button>
              <button v-else-if="guidedStage === 'visual_review'" class="primary-btn guided-primary-action" type="button" :disabled="guidedAdvancing || visualEditor.has_active_image_tasks || ttsEditor.task?.status === 'running'" @click="advanceGuidedWorkflow('confirm_visual')">
                {{ guidedAdvancing ? '正在确认…' : '确认画面与时序' }}
              </button>
              <button v-else-if="guidedStage === 'render_setup'" class="primary-btn guided-primary-action" type="button" :disabled="guidedAdvancing || (form.bgm_enabled && !form.bgm_tracks.length)" @click="advanceGuidedWorkflow('start_render')">
                {{ guidedAdvancing ? '正在启动…' : '开始最终渲染' }}
              </button>
              <button v-else-if="guidedStage === 'completed'" class="primary-btn guided-primary-action" type="button" @click="enterGuidedPostProduction">
                进入成片精修
              </button>
            </div>
          </div>
          <div v-if="guidedStageMessage" class="guided-stage-message" :class="{ error: guidedStageError }">{{ guidedStageMessage }}</div>
          <div v-if="guidedStage === 'visual_setup'" class="guided-cost-estimate">
            <span>本阶段预估</span>
            <strong>约 {{ guidedVisualEstimate.images }} 张图片</strong>
            <small>文本导演约 3–4 次请求；图片按实际成功张数计费。开始前请确认接口或号池余额。</small>
          </div>
          <div v-if="guidedRunning" class="guided-inline-progress">
            <span><i :style="{ width: `${activeJob?.progress || 0}%` }"></i></span>
            <small>{{ activeJob?.message || '正在处理当前阶段' }} · {{ activeJob?.progress || 0 }}%</small>
          </div>
        </section>
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
                <span>已有配音和文案，不需要 IndexTTS-2.5</span>
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
                  <small v-else-if="sourceAudioName" class="muted">生成时会跳过 IndexTTS-2.5，从模块 2 开始识别字幕。</small>
                </div>
              </div>
            </div>

            <label class="stack">
              <span>{{ form.skip_text_correction ? '口播文案（已选择无文案，可留空）' : '口播文案' }}</span>
              <textarea
                ref="workspaceScriptTextarea"
                v-model="form.script"
                rows="14"
                :disabled="form.skip_text_correction"
                :placeholder="scriptPlaceholder"
              ></textarea>
              <div v-if="!form.skip_tts" class="structural-blank-toolbar">
                <span>结构留白</span>
                <input v-model.number="structuralBlankSeconds" type="number" min="0.2" max="30" step="0.1" aria-label="留白秒数" />
                <button type="button" class="ghost-btn" @click="insertStructuralBlank(workspaceScriptTextarea)">在光标处插入</button>
                <small>强制断开配音与画面，留白时保持上一张图；可能增加图片数量及费用。</small>
              </div>
              <small class="script-character-count" :class="{ error: scriptTooLong }">
                {{ scriptCharacterCount.toLocaleString() }} / {{ MAX_SCRIPT_CHARACTERS.toLocaleString() }} 字符
                <template v-if="scriptTooLong"> · 超出单次上限，请按完整章节拆分后分批生成</template>
              </small>
            </label>
            </div>

            <div class="create-settings-column">
            <div class="create-audio-column">
            <div
              v-if="!form.skip_tts"
              class="tts-parameter-panel"
              :class="{ 'refinement-locked': ttsRefinementActive }"
              :inert="ttsRefinementActive"
              :aria-disabled="ttsRefinementActive ? 'true' : 'false'"
              :title="ttsRefinementActive ? '配音精修已展开，请使用下方精修参数' : ''"
            >
              <div v-if="ttsRefinementActive" class="refinement-lock-note">配音精修已展开 · 请使用下方精修参数</div>
              <div class="tts-parameter-head">
                <div>
                  <div class="tts-engine-row">
                    <div class="sidebar-label">{{ ttsEngineLabel }}</div>
                    <label class="tts-engine-select" title="选择配音执行方式">
                      <span>执行方式</span>
                      <select v-model="ttsEngine">
                        <option value="indextts25">本地 GPU · IndexTTS-2.5</option>
                        <option value="cluster">集群 GPU</option>
                        <option value="qwen">Qwen-TTS</option>
                      </select>
                    </label>
                  </div>
                  <h3>语音参数</h3>
                </div>
                <div class="tts-parameter-meta">
                  <span v-if="ttsEngine === 'indextts25'" class="status-chip" :class="health.tts25_online ? 'success' : 'warning'">
                    {{ health.tts25_online ? 'IndexTTS-2.5 就绪' : 'IndexTTS-2.5 未就绪' }}
                  </span>
                  <span v-else-if="ttsEngine === 'cluster'" class="status-chip" :class="cloudReady ? 'success' : 'warning'">
                    {{ cloudReady ? '集群已登录' : '集群未登录' }}
                  </span>
                  <span class="muted small">{{ ttsEngineProviderLabel }}</span>
                </div>
              </div>
              <div v-if="ttsEngine === 'indextts25'" class="local-tts-hardware-note">
                <strong>本地配音需要 NVIDIA 显卡</strong>
                <span>建议至少 8GB 显存，并保持并行数 1；6GB 及以下显存建议改用集群 GPU 或 Qwen-TTS。</span>
              </div>
              <div v-if="ttsEngine === 'indextts25'" class="form-grid tts-param-grid">
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
                    <option value="">参考原音频</option>
                    <option v-for="emotion in settings.tts?.emotions || []" :key="emotion" :value="emotion">
                      {{ emotionLabel(emotion) }}
                    </option>
                  </select>
                </label>
                <label class="tts-emotion-strength">
                  <span>情绪强度（0–1）· {{ Number(form.tts_emotion_weight).toFixed(2) }}</span>
                  <input v-model.number="form.tts_emotion_weight" type="range" min="0" max="1" step="0.05" :disabled="!form.tts_emotion" />
                  <small class="muted">选择具体情绪后生效；默认 0.65。</small>
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
                  默认并行数为 1；高显存显卡确认运行稳定后，再尝试提高并行数。
                </small>
              </div>
              <div v-else-if="ttsEngine === 'cluster'" class="cluster-tts-config">
                <div v-if="!cloudSession.configured" class="cluster-notice cluster-login-prompt warning">
                  <span>云端集群连接配置异常，请尝试登录；仍无法连接时请检查网络。</span>
                  <button class="primary-btn compact-btn" type="button" @click="openCloudLogin">前往登录</button>
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
                    <button class="cloud-recharge-entry" type="button" :disabled="cloudBusy" @click="openCloudRecharge">支付宝充值</button>
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
                    <label><span>情绪</span><select v-model="form.tts_emotion"><option value="">参考原音频</option><option v-for="emotion in settings.tts?.emotions || []" :key="emotion" :value="emotion">{{ emotionLabel(emotion) }}</option></select></label>
                    <label class="tts-emotion-strength"><span>情绪强度（0–1）· {{ Number(form.tts_emotion_weight).toFixed(2) }}</span><input v-model.number="form.tts_emotion_weight" type="range" min="0" max="1" step="0.05" :disabled="!form.tts_emotion" /><small class="muted">选择具体情绪后生效。</small></label>
                    <label><span>语速（0.5–2）</span><input v-model.number="form.tts_speed" type="number" min="0.5" max="2" step="0.01" /></label>
                    <label><span>音量（0.1–10）</span><input v-model.number="form.tts_volume" type="number" min="0.1" max="10" step="0.01" /></label>
                    <label><span>音调（-12–12）</span><input v-model.number="form.tts_pitch" type="number" min="-12" max="12" step="1" /></label>
                  </div>
                  <div class="cluster-notice">所有文本分块会同时进入集群队列，由空闲 GPU 自动领取；每个分块生成后会立即下载到本机。</div>
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
                  <input v-model="apiKeyForm.qwen_tts_api_key" name="ocv-qwen-tts-api-secret" type="password" autocomplete="new-password" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore readonly placeholder="DashScope API Key（sk-...）" @focus="unlockProtectedInput" />
                </label>
          <div v-else class="api-key-state-bar qwen-key-state" :class="{ error: apiKeyRuntimeErrors.qwen_tts }">
            <span>
              <strong>{{ apiKeyRuntimeErrors.qwen_tts ? 'ERROR' : 'API 已配置' }}</strong>
              <small v-if="apiKeyRuntimeErrors.qwen_tts">{{ apiKeyRuntimeErrors.qwen_tts }}</small>
              <span v-else class="api-key-hints"><code v-for="hint in apiKeyStatus.qwen_tts?.key_hints || []" :key="hint">{{ hint }}</code></span>
            </span>
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
            <section v-if="form.step_mode && guidedStage === 'audio_review'" class="guided-audio-editor">
              <div class="guided-editor-head">
                <div><div class="sidebar-label">配音精修</div><strong>逐句试听与选择重配</strong></div>
                <button class="ghost-btn compact-btn" type="button" :disabled="!ttsEditor.history_count || ttsBoundaryBusy" @click="undoLastTtsEdit">撤销 · {{ ttsEditor.history_count || 0 }}/{{ ttsEditor.history_limit || 20 }}</button>
                <button v-if="ttsEditor.structural_edit_available !== false" class="primary-btn compact-btn" type="button" :disabled="!selectedTtsSegmentIndices.length || ttsEditor.task?.status === 'running'" @click="regenerateSelectedTtsSegments">
                  {{ ttsEditor.task?.status === 'running' ? '重配音中…' : '重配选中句' }}
                </button>
              </div>
              <div v-if="ttsEditorLoading" class="empty-state">正在读取逐句配音…</div>
              <div v-else-if="!ttsEditor.available" class="timing-unavailable">{{ ttsEditor.message || '当前配音没有可逐句精修的素材。' }}</div>
              <template v-else>
              <details class="guided-tts-settings">
                <summary>本次重配参数（默认沿用当前项目）</summary>
                <div class="tts-refine-parameter-panel" :class="{ locked: ttsEditor.task?.status === 'running' }">
                  <div class="tts-refine-parameter-head">
                    <div><span class="sidebar-label">{{ ttsRefineEngineLabel }}</span><h4>只作用于本次选中的句子</h4></div>
                    <button class="ghost-btn compact-btn" type="button" :disabled="ttsEditor.task?.status === 'running'" @click="hydrateTtsRefineSettings(ttsEditor)">恢复项目参数</button>
                  </div>
                  <div v-if="ttsEditor.engine === 'indextts25'" class="tts-refine-voice-row">
                    <label class="script-file-picker">
                      <input type="file" accept=".wav,.mp3,.flac,audio/wav,audio/mpeg,audio/flac" :disabled="ttsRefineVoiceUploading || ttsEditor.task?.status === 'running'" @change="uploadTtsRefineVoice" />
                      <span>{{ ttsRefineVoiceUploading ? '上传中' : '更换音源' }}</span>
                      <strong>{{ ttsRefineVoiceName || '沿用该项目当前参考音色' }}</strong>
                    </label>
                  </div>
                  <label v-else-if="ttsEditor.engine === 'cluster'" class="tts-refine-wide-field">
                    <span>云端音色</span>
                    <select v-model="ttsRefineForm.cluster_voice_key">
                      <optgroup label="云端默认音色"><option v-for="voice in cloudPresetVoiceOptions" :key="`guided-refine-preset:${voice.id}`" :value="`preset:${voice.id}`">{{ voice.display_name || voice.id }}</option></optgroup>
                      <optgroup v-if="cloudUploadedVoiceOptions.length" label="我上传的音色"><option v-for="voice in cloudUploadedVoiceOptions" :key="`guided-refine-uploaded:${voice.id}`" :value="`uploaded:${voice.id}`">{{ voice.display_name || voice.id }}</option></optgroup>
                    </select>
                  </label>
                  <div v-else-if="ttsEditor.engine === 'qwen'" class="form-grid tts-refine-qwen-grid">
                    <label><span>Qwen 系统音色</span><select v-model="ttsRefineForm.qwen_voice"><optgroup v-for="group in qwenVoiceGroups" :key="`guided-refine-${group.label}`" :label="group.label"><option v-for="voice in group.voices" :key="`guided-refine-${voice.value}`" :value="voice.value">{{ voice.label }}</option></optgroup></select></label>
                    <label><span>配音描述</span><textarea v-model="ttsRefineForm.qwen_instructions" rows="3" maxlength="1600"></textarea></label>
                  </div>
                  <div class="form-grid tts-refine-grid">
                    <label v-if="ttsEditor.engine !== 'qwen'"><span>情绪</span><select v-model="ttsRefineForm.tts_emotion"><option value="">参考原音频</option><option v-for="emotion in settings.tts?.emotions || []" :key="`guided-refine-${emotion}`" :value="emotion">{{ emotionLabel(emotion) }}</option></select></label>
                    <label v-if="ttsEditor.engine !== 'qwen'" class="tts-emotion-strength"><span>情绪强度 · {{ Number(ttsRefineForm.tts_emotion_weight).toFixed(2) }}</span><input v-model.number="ttsRefineForm.tts_emotion_weight" type="range" min="0" max="1" step="0.05" :disabled="!ttsRefineForm.tts_emotion" /></label>
                    <label><span>语速</span><input v-model.number="ttsRefineForm.tts_speed" type="number" min="0.5" max="2" step="0.01" /></label>
                    <label><span>音量</span><input v-model.number="ttsRefineForm.tts_volume" type="number" min="0.1" max="10" step="0.01" /></label>
                    <label><span>音调</span><input v-model.number="ttsRefineForm.tts_pitch" type="number" min="-12" max="12" step="1" /></label>
                    <label v-if="ttsEditor.engine === 'indextts25'"><span>并行数</span><input v-model.number="ttsRefineForm.tts_parallelism" type="number" min="1" max="3" step="1" /></label>
                  </div>
                </div>
              </details>
              <div v-if="ttsEditor.structural_edit_available === false" class="timing-unavailable">{{ ttsEditor.structural_edit_message }}</div>
              <div class="guided-tts-grid">
                <article v-for="item in ttsEditor.segments" :key="`guided-tts-${item.index}-${item.audio_url}`" class="guided-tts-card" :class="{ selected: selectedTtsSegmentIndices.includes(item.index) }">
                  <div><strong>第 {{ item.index }} 句</strong><small>{{ Number(item.duration || 0).toFixed(2) }} 秒</small></div>
                  <p>{{ item.text }}</p>
                  <audio controls preload="none" :src="item.audio_url"></audio>
                  <label v-if="ttsEditor.structural_edit_available !== false"><span>选中</span><input v-model="selectedTtsSegmentIndices" type="checkbox" :value="item.index" :disabled="ttsEditor.task?.status === 'running'" /></label>
                  <button v-if="ttsEditor.structural_edit_available !== false" class="tts-pronunciation-toggle" type="button" :disabled="ttsEditor.task?.status === 'running'" @click="toggleTtsPronunciationEditor(item)">{{ isTtsPronunciationOpen(item.index) ? '收起发音修正' : '发音修正' }}</button>
                  <div v-if="isTtsPronunciationOpen(item.index)" class="guided-pronunciation-editor">
                    <small>朗读文本只发送给配音引擎，字幕仍保留原文字。</small>
                    <textarea :value="ttsReadingDrafts[item.index]" rows="2" maxlength="1200" :disabled="ttsEditor.task?.status === 'running'" placeholder="例如：点击chong2绘按钮" @input="updateTtsReadingDraft(item, $event)"></textarea>
                    <button class="ghost-btn compact-btn" type="button" :disabled="ttsEditor.task?.status === 'running' || !isTtsReadingModified(item)" @click="resetTtsReadingDraft(item)">恢复原文</button>
                  </div>
                  <div v-if="item.index < ttsEditor.segments.length" class="tts-boundary-row">
                    <label><span>停顿</span><input v-model.number="ttsPauseDrafts[item.index]" type="number" min="0" max="30" step="0.1" /><small>秒</small></label>
                    <button class="ghost-btn compact-btn" type="button" @click="previewTtsBoundary(item)">试听</button>
                    <button class="ghost-btn compact-btn" type="button" @click="saveTtsPause(item)">保存</button>
                    <button v-if="ttsEditor.structural_edit_available !== false" class="ghost-btn compact-btn" type="button" @click="openTtsBoundaryEditor(item, true)">调整断点</button>
                  </div>
                  <button v-if="ttsEditor.structural_edit_available !== false" class="tts-pronunciation-toggle" type="button" @click="openTtsBoundaryEditor(item, false)">新增断点</button>
                </article>
              </div>
              <div v-if="ttsBoundary.open" class="tts-boundary-editor">
                <div class="tts-refine-parameter-head"><div><span class="sidebar-label">断句与停顿</span><h4>{{ ttsBoundary.replaceCount === 2 ? '调整现有断点' : '新增断点' }}</h4></div><button class="ghost-btn compact-btn" type="button" @click="closeTtsBoundaryEditor">关闭</button></div>
                <label><span>点击文字中的断开位置</span><textarea :value="ttsBoundary.sourceText" rows="3" readonly @click="updateTtsBoundaryParts($event.target.selectionStart)" @keyup="updateTtsBoundaryParts($event.target.selectionStart)"></textarea></label>
                <label v-if="ttsBoundary.replaceCount === 2" class="switch-row"><input v-model="ttsBoundary.merge" type="checkbox" @change="refreshTtsBoundaryTokenCounts" /><span class="switch-track"><i></i></span><strong>合并为一句</strong></label>
                <div v-if="!ttsBoundary.merge" class="tts-boundary-preview-grid"><label><span>前半句朗读文本</span><textarea v-model="ttsBoundary.leftReading" rows="2" @input="refreshTtsBoundaryTokenCounts"></textarea><small>{{ ttsBoundary.counts[0] ?? '-' }}<template v-if="ttsBoundary.limit"> / {{ ttsBoundary.limit }} token</template></small></label><label><span>后半句朗读文本</span><textarea v-model="ttsBoundary.rightReading" rows="2" @input="refreshTtsBoundaryTokenCounts"></textarea><small>{{ ttsBoundary.counts[1] ?? '-' }}<template v-if="ttsBoundary.limit"> / {{ ttsBoundary.limit }} token</template></small></label></div>
                <div class="tts-boundary-actions"><label v-if="!ttsBoundary.merge"><span>额外停顿</span><input v-model.number="ttsBoundary.pause" type="number" min="0" max="30" step="0.1" /> 秒</label><span v-if="ttsBoundaryOverLimit()" class="script-upload-error">存在超出 {{ ttsBoundary.limit }} token 的片段。</span><button class="primary-btn compact-btn" type="button" :disabled="ttsBoundaryBusy || ttsBoundaryOverLimit()" @click="submitTtsBoundary">确认并重配</button></div>
              </div>
              </template>
              <div class="guided-editor-head guided-subtitle-head">
                <div><div class="sidebar-label">字幕校对</div><strong>修改文字，不改变当前时间边界</strong></div>
                <button class="primary-btn compact-btn" type="button" :disabled="!guidedSubtitleDirtyCount || guidedSubtitleSaving" @click="saveGuidedSubtitles">
                  {{ guidedSubtitleSaving ? '保存中…' : `保存字幕修改${guidedSubtitleDirtyCount ? `（${guidedSubtitleDirtyCount}）` : ''}` }}
                </button>
              </div>
              <div v-if="guidedSubtitleLoading" class="empty-state">正在读取字幕…</div>
              <div v-else class="guided-subtitle-list">
                <label v-for="(item, index) in guidedSubtitles" :key="item.slide_id">
                  <span>第 {{ index + 1 }} 句 · {{ formatGuidedTimestamp(item.start) }}–{{ formatGuidedTimestamp(item.end) }}</span>
                  <textarea v-model="guidedSubtitleDrafts[item.slide_id]" rows="2" maxlength="1200"></textarea>
                </label>
              </div>
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
              <div class="director-strategy-row">
                <div class="director-strategy-copy">
                  <span>导演策略</span>
                  <small>控制文字如何转成画面，不改变作品风格、配音、字幕和渲染。</small>
                </div>
                <div class="director-strategy-options" role="group" aria-label="导演策略">
                  <button
                    type="button"
                    :class="{ active: form.director_strategy === 'stable' }"
                    @click="setDirectorStrategy('stable')"
                  >
                    稳健还原
                  </button>
                  <button
                    type="button"
                    class="enhanced"
                    :class="{ active: form.director_strategy === 'enhanced_beta' }"
                    @click="setDirectorStrategy('enhanced_beta')"
                  >
                    叙事增强 <em>Beta</em>
                  </button>
                </div>
                <small class="director-strategy-hint">
                  {{ form.director_strategy === 'enhanced_beta'
                    ? '减少逐句字面配图，优先使用有依据的现实切片、克制隐喻和镜头变化；测试功能可能增加少量规划耗时。'
                    : '忠实、克制地还原原文，保持当前已经验证的画面规划方式。' }}
                </small>
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
          <div v-if="activeJob?.status === 'waiting_confirmation' && !isGuidedWorkflowJob(activeJob)" class="step-confirmation-card">
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
                  <div class="inline-actions"><span class="status-chip" :class="statusClass(job.status)">{{ statusLabel(job.status) }}</span><span v-if="job.request?.module1_only" class="status-chip module1-task-chip">仅配音</span></div>
                  <div class="project-actions">
                    <span class="muted small">{{ job.progress }}%</span>
                    <button
                      type="button"
                      class="task-delete-btn"
                      :disabled="['queued', 'running'].includes(job.status) || (job.status === 'waiting_confirmation' && !isGuidedWorkflowJob(job))"
                      title="删除任务及其专属产物"
                      @click.stop="deleteGenerationJob(job)"
                    >删除</button>
                  </div>
                </div>
                <h3>{{ job.request?.project_name || job.id }}</h3>
                <p>{{ job.message }}</p>
                <button
                  v-if="isGuidedWorkflowJob(job) && job.status !== 'completed'"
                  class="ghost-btn compact-btn guided-task-resume"
                  type="button"
                  @click.stop="continueGuidedJob(job)"
                >继续分步制作</button>
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
              <section class="visual-timing-panel" :class="{ locked: ttsEditor.task?.status === 'running' }">
                <div class="visual-timing-head">
                  <div>
                    <div class="eyebrow">画面时序</div>
                    <h3>按字幕调整画面位置与文字</h3>
                    <p class="muted small">移动字幕句可调整画面边界；点击单句右侧的编辑键可修正最终字幕文字，不会改变配音内容。</p>
                  </div>
                  <div class="visual-timing-head-actions">
                    <button class="ghost-btn compact-btn" type="button" :disabled="!ttsEditor.history_count || ttsBoundaryBusy" @click="undoLastTtsEdit">撤销上一步 · {{ ttsEditor.history_count || 0 }}/{{ ttsEditor.history_limit || 20 }}</button>
                    <select v-model="selectedVisualTimingHistory" class="timing-history-select" :disabled="visualTimingAdjusting || ttsEditor.task?.status === 'running' || !visualEditor.timing_history?.length" @change="restoreSelectedVisualTimingHistory">
                      <option value="">历史时序</option>
                      <option v-for="entry in visualEditor.timing_history || []" :key="entry.id" :value="entry.id">{{ entry.label }}</option>
                    </select>
                    <button class="ghost-btn compact-btn timing-save-btn" type="button" :disabled="!visualEditor.timing_available || visualTimingAdjusting || ttsEditor.task?.status === 'running'" @click="commitEditedTiming">✅ 保存当前时序</button>
                    <button class="ghost-btn compact-btn" type="button" :disabled="!visualEditor.timing_available || visualTimingAdjusting || ttsEditor.task?.status === 'running'" @click="resetEditedTiming">恢复初始时序</button>
                  </div>
                </div>
                <div v-if="ttsEditor.task?.status === 'running'" class="visual-subtitle-lock-note">配音精修正在改变句子时长，画面时序与字幕编辑已暂时锁定，完成后会自动刷新。</div>
                <div v-if="!visualEditor.timing_available" class="timing-unavailable">{{ visualEditor.timing_message || '该历史项目缺少可用的字幕时间线。' }}</div>
                <template v-else>
                  <div class="visual-subtitle-toolbar">
                    <div>
                      <strong>字幕文字精修</strong>
                      <small>直接修改最终显示文字；保存时自动同步字幕文件与画面时间线。</small>
                    </div>
                    <div class="visual-subtitle-toolbar-actions">
                      <select v-model="selectedVisualSubtitleHistory" class="timing-history-select" :disabled="visualSubtitleSaving || ttsEditor.task?.status === 'running' || !visualEditor.subtitle_history?.length" @change="restoreSelectedVisualSubtitleHistory">
                        <option value="">字幕历史</option>
                        <option v-for="entry in visualEditor.subtitle_history || []" :key="entry.id" :value="entry.id">{{ entry.label }}</option>
                      </select>
                      <span v-if="visualSubtitleDirtyCount" class="visual-subtitle-dirty-count">{{ visualSubtitleDirtyCount }} 处未保存</span>
                      <button class="primary-btn compact-btn" type="button" :disabled="!visualSubtitleDirtyCount || visualSubtitleSaving || ttsEditor.task?.status === 'running'" @click="saveVisualSubtitles">{{ visualSubtitleSaving ? '保存中…' : '保存字幕修改' }}</button>
                    </div>
                  </div>
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
                        <div
                          v-for="sentence in selectedVisualTimingItem.timing?.sentences || []"
                          :key="sentence.slide_id"
                          class="timing-sentence"
                          :class="{ editing: visualSubtitleEditingId === sentence.slide_id, modified: isVisualSubtitleModified(sentence), hidden: sentence.subtitle_hidden }"
                        >
                          <span class="timing-sentence-meta"><strong>{{ sentence.slide_id }}</strong><small>{{ formatSubtitleTiming(sentence) }}</small></span>
                          <div v-if="sentence.subtitle_hidden" class="timing-sentence-content hidden-subtitle-copy">
                            <p>已隐藏字幕 · {{ visualSubtitleHiddenModeLabel(sentence.subtitle_hidden_mode) }}</p>
                            <small>配音、图片与画面时序仍保留</small>
                          </div>
                          <div v-else class="timing-sentence-content">
                            <textarea
                              v-if="visualSubtitleEditingId === sentence.slide_id"
                              v-model="visualSubtitleDrafts[sentence.slide_id]"
                              rows="2"
                              maxlength="1200"
                              :disabled="visualSubtitleSaving || ttsEditor.task?.status === 'running'"
                              @keydown.ctrl.enter.prevent="finishVisualSubtitleEdit(sentence)"
                            ></textarea>
                            <p v-else>{{ visualSubtitleDrafts[sentence.slide_id] ?? sentence.text }}</p>
                            <small v-if="visualSubtitlePaceWarning(sentence)" class="visual-subtitle-pace-warning">{{ visualSubtitlePaceWarning(sentence) }}</small>
                          </div>
                          <div class="timing-sentence-actions">
                            <button v-if="sentence.subtitle_hidden" class="ghost-btn compact-btn subtitle-restore-btn" type="button" :disabled="visualSubtitleSaving || ttsEditor.task?.status === 'running'" @click="restoreHiddenVisualSubtitle(sentence)">恢复</button>
                            <template v-else>
                              <button class="ghost-btn compact-btn" type="button" :disabled="visualSubtitleSaving || ttsEditor.task?.status === 'running'" @click="toggleVisualSubtitleEdit(sentence)">{{ visualSubtitleEditingId === sentence.slide_id ? '完成' : '✏️' }}</button>
                              <button v-if="isVisualSubtitleModified(sentence)" class="ghost-btn compact-btn" type="button" :disabled="visualSubtitleSaving || ttsEditor.task?.status === 'running'" title="撤销本句未保存修改" @click="resetVisualSubtitleDraft(sentence)">↶</button>
                              <button v-if="hasNextVisualSubtitle(sentence)" class="ghost-btn compact-btn subtitle-align-trigger" type="button" :disabled="visualBoundaryAlign.status === 'loading' || visualSubtitleSaving || ttsEditor.task?.status === 'running'" title="只移动本句结束与下一句开始的共同边界" @click="previewVisualSubtitleBoundary(sentence)">调整与下一句分界</button>
                              <button class="ghost-btn compact-btn subtitle-hide-btn" type="button" :disabled="visualSubtitleSaving || ttsEditor.task?.status === 'running'" title="从成片与 SRT 中隐藏这条字幕" @click="openVisualSubtitleRemove(sentence)">×</button>
                            </template>
                          </div>
                        </div>
                      </div>
                      <div v-if="visualSubtitleRemoveDialog.open" class="subtitle-remove-panel">
                        <div class="subtitle-remove-head">
                          <div>
                            <strong>隐藏 {{ visualSubtitleRemoveDialog.sentence?.slide_id }} 的字幕</strong>
                            <small>{{ visualSubtitleRemoveDialog.sentence?.text }}</small>
                          </div>
                          <button class="icon-action" type="button" title="取消" @click="closeVisualSubtitleRemove">×</button>
                        </div>
                        <p>只移除成片和 SRT 中的显示文字，不删除配音、图片或画面时间线。请选择这段时间如何处理：</p>
                        <div class="subtitle-remove-options">
                          <button class="ghost-btn compact-btn" type="button" :disabled="visualSubtitleSaving" @click="hideVisualSubtitle('blank')"><strong>留空</strong><small>这段时间不显示字幕</small></button>
                          <button class="ghost-btn compact-btn" type="button" :disabled="visualSubtitleSaving || !canMergeHiddenSubtitle(visualSubtitleRemoveDialog.sentence, 'previous')" @click="hideVisualSubtitle('merge_previous')"><strong>并入前句</strong><small>前一条可见字幕延长到这里</small></button>
                          <button class="ghost-btn compact-btn" type="button" :disabled="visualSubtitleSaving || !canMergeHiddenSubtitle(visualSubtitleRemoveDialog.sentence, 'next')" @click="hideVisualSubtitle('merge_next')"><strong>并入后句</strong><small>后一条可见字幕提前到这里</small></button>
                        </div>
                      </div>
                      <div class="timing-actions">
                        <button class="ghost-btn compact-btn" type="button" :disabled="!selectedVisualTimingItem.timing?.can_extend_prev || visualTimingAdjusting || ttsEditor.task?.status === 'running'" @click="adjustEditedTiming('extend_prev')">← 前面多一句</button>
                        <button class="ghost-btn compact-btn" type="button" :disabled="!selectedVisualTimingItem.timing?.can_shrink_prev || visualTimingAdjusting || ttsEditor.task?.status === 'running'" @click="adjustEditedTiming('shrink_prev')">前面少一句 →</button>
                        <button class="ghost-btn compact-btn" type="button" :disabled="!selectedVisualTimingItem.timing?.can_shrink_next || visualTimingAdjusting || ttsEditor.task?.status === 'running'" @click="adjustEditedTiming('shrink_next')">← 后面少一句</button>
                        <button class="ghost-btn compact-btn" type="button" :disabled="!selectedVisualTimingItem.timing?.can_extend_next || visualTimingAdjusting || ttsEditor.task?.status === 'running'" @click="adjustEditedTiming('extend_next')">后面多一句 →</button>
                        <button class="ghost-btn compact-btn timing-remove-btn" type="button" :disabled="visualEditor.items.length <= 1 || visualTimingAdjusting || ttsEditor.task?.status === 'running'" @click="removeEditedTimingPicture">移除这张画面</button>
                      </div>
                    </div>
                  </div>
                  <div v-if="visualBoundaryAlign.open" class="subtitle-boundary-panel">
                    <div class="subtitle-boundary-head">
                      <div>
                        <strong>相邻字幕时间边界</strong>
                        <small>{{ visualBoundaryAlign.left_slide_id }} → {{ visualBoundaryAlign.right_slide_id }}</small>
                      </div>
                      <button class="icon-action" type="button" title="关闭" @click="closeVisualBoundaryAlign">×</button>
                    </div>
                    <div v-if="visualBoundaryAlign.status === 'loading'" class="subtitle-boundary-loading">正在读取相邻两句的音频边界…</div>
                    <div v-else-if="visualBoundaryAlign.status === 'failed'" class="subtitle-boundary-loading error">{{ visualBoundaryAlign.message || '读取字幕边界失败' }}</div>
                    <template v-else-if="visualBoundaryAlign.status === 'ready'">
                      <div class="subtitle-boundary-copy"><span>{{ visualBoundaryAlign.left_text }}</span><i>｜</i><span>{{ visualBoundaryAlign.right_text }}</span></div>
                      <label class="subtitle-boundary-slider">
                        <span>共同边界：{{ formatBoundaryOffset(visualBoundaryAlign.boundary, visualBoundaryAlign.pair_start) }} · {{ formatBoundaryDelta(visualBoundaryAlign.boundary, visualBoundaryAlign.current_boundary) }}</span>
                        <input v-model.number="visualBoundaryAlign.boundary" type="range" :min="visualBoundaryAlign.minimum_boundary" :max="visualBoundaryAlign.maximum_boundary" step="0.01" />
                      </label>
                      <small class="muted small">只会移动这两句的共享分界：前句结束时间与后句开始时间同步变化；后句结束、下一张画面和更后面的内容保持不变。</small>
                      <div class="subtitle-boundary-actions">
                        <button class="ghost-btn compact-btn" type="button" @click="playVisualBoundaryRange(visualBoundaryAlign.pair_start, visualBoundaryAlign.boundary)">▶ 试听前句</button>
                        <button class="ghost-btn compact-btn" type="button" @click="playVisualBoundaryRange(visualBoundaryAlign.boundary, visualBoundaryAlign.pair_end)">▶ 试听后句</button>
                        <button class="ghost-btn compact-btn" type="button" @click="playVisualBoundaryRange(visualBoundaryAlign.clip_start, visualBoundaryAlign.clip_end)">▶ 连续试听</button>
                        <button class="ghost-btn compact-btn" type="button" :disabled="Math.abs(Number(visualBoundaryAlign.boundary) - Number(visualBoundaryAlign.current_boundary)) < 0.005" @click="visualBoundaryAlign.boundary = Number(visualBoundaryAlign.current_boundary)">恢复原边界</button>
                        <button class="primary-btn compact-btn" type="button" :disabled="visualBoundaryApplying" @click="applyVisualSubtitleBoundary">{{ visualBoundaryApplying ? '应用中…' : '确认应用边界' }}</button>
                      </div>
                    </template>
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
                      :disabled="ttsEditor.structural_edit_available === false || !selectedTtsSegmentIndices.length || ttsEditor.task?.status === 'running'"
                      @click="regenerateSelectedTtsSegments"
                    >{{ ttsEditor.task?.status === 'running' ? '重配音中…' : '重配选中句' }}</button>
                  </div>
                </div>
                <div v-if="ttsEditorLoading" class="timing-unavailable">正在读取逐句配音…</div>
                <div v-else-if="!ttsEditor.available" class="timing-unavailable">{{ ttsEditor.message || '该项目没有可编辑的逐句配音。' }}</div>
                <template v-else>
                  <div v-if="ttsEditor.task?.message" class="visual-task-message" :class="ttsEditor.task?.status">{{ ttsEditor.task.message }}</div>
                  <div class="tts-refine-parameter-panel" :class="{ locked: ttsEditor.task?.status === 'running' }">
                    <div class="tts-refine-parameter-head">
                      <div>
                        <span class="sidebar-label">{{ ttsRefineEngineLabel }}</span>
                        <h4>本次重配参数</h4>
                        <small class="muted">只作用于本次选中的句子；成功后保存为该项目后续精修参数。</small>
                      </div>
                      <button class="ghost-btn compact-btn" type="button" :disabled="ttsEditor.task?.status === 'running'" @click="hydrateTtsRefineSettings(ttsEditor)">恢复项目参数</button>
                    </div>

                    <div v-if="ttsEditor.engine === 'indextts25'" class="tts-refine-voice-row">
                      <label class="script-file-picker">
                        <input type="file" accept=".wav,.mp3,.flac,audio/wav,audio/mpeg,audio/flac" :disabled="ttsRefineVoiceUploading || ttsEditor.task?.status === 'running'" @change="uploadTtsRefineVoice" />
                        <span>{{ ttsRefineVoiceUploading ? '上传中' : '更换音源' }}</span>
                        <strong>{{ ttsRefineVoiceName || '沿用该项目当前参考音色' }}</strong>
                      </label>
                      <small v-if="ttsRefineVoiceError" class="script-upload-error">{{ ttsRefineVoiceError }}</small>
                    </div>
                    <label v-else-if="ttsEditor.engine === 'cluster'" class="tts-refine-wide-field">
                      <span>云端音色</span>
                      <select v-model="ttsRefineForm.cluster_voice_key">
                        <optgroup label="云端默认音色"><option v-for="voice in cloudPresetVoiceOptions" :key="`refine-preset:${voice.id}`" :value="`preset:${voice.id}`">{{ voice.display_name || voice.id }}</option></optgroup>
                        <optgroup v-if="cloudUploadedVoiceOptions.length" label="我上传的音色"><option v-for="voice in cloudUploadedVoiceOptions" :key="`refine-uploaded:${voice.id}`" :value="`uploaded:${voice.id}`">{{ voice.display_name || voice.id }}</option></optgroup>
                      </select>
                    </label>
                    <div v-else-if="ttsEditor.engine === 'qwen'" class="form-grid tts-refine-qwen-grid">
                      <label><span>Qwen 系统音色</span><select v-model="ttsRefineForm.qwen_voice"><optgroup v-for="group in qwenVoiceGroups" :key="`refine-${group.label}`" :label="group.label"><option v-for="voice in group.voices" :key="`refine-${voice.value}`" :value="voice.value">{{ voice.label }}</option></optgroup></select></label>
                      <label><span>配音描述</span><textarea v-model="ttsRefineForm.qwen_instructions" rows="3" maxlength="1600"></textarea></label>
                    </div>

                    <div class="form-grid tts-refine-grid">
                      <label v-if="ttsEditor.engine !== 'qwen'"><span>情绪</span><select v-model="ttsRefineForm.tts_emotion"><option value="">参考原音频</option><option v-for="emotion in settings.tts?.emotions || []" :key="`refine-${emotion}`" :value="emotion">{{ emotionLabel(emotion) }}</option></select></label>
                      <label v-if="ttsEditor.engine !== 'qwen'" class="tts-emotion-strength"><span>情绪强度 · {{ Number(ttsRefineForm.tts_emotion_weight).toFixed(2) }}</span><input v-model.number="ttsRefineForm.tts_emotion_weight" type="range" min="0" max="1" step="0.05" :disabled="!ttsRefineForm.tts_emotion" /></label>
                      <label><span>语速</span><input v-model.number="ttsRefineForm.tts_speed" type="number" min="0.5" max="2" step="0.01" /></label>
                      <label><span>音量</span><input v-model.number="ttsRefineForm.tts_volume" type="number" min="0.1" max="10" step="0.01" /></label>
                      <label><span>音调</span><input v-model.number="ttsRefineForm.tts_pitch" type="number" min="-12" max="12" step="1" /></label>
                      <label v-if="ttsEditor.engine === 'indextts25'"><span>并行数</span><input v-model.number="ttsRefineForm.tts_parallelism" type="number" min="1" max="3" step="1" /></label>
                    </div>
                  </div>
                  <div v-if="ttsEditor.structural_edit_available === false" class="timing-unavailable">{{ ttsEditor.structural_edit_message }}</div>
                  <div class="tts-segment-grid">
                    <article
                      v-for="item in ttsEditor.segments"
                      :key="`tts-segment-${item.index}-${item.audio_url}`"
                      class="tts-segment-card"
                      :class="{
                        selected: selectedTtsSegmentIndices.includes(item.index),
                        'pronunciation-open': isTtsPronunciationOpen(item.index),
                      }"
                    >
                      <p class="tts-segment-text">{{ item.text }}</p>
                      <div class="tts-segment-controls">
                        <div class="tts-segment-meta">
                          <strong>第 {{ item.index }} 句</strong>
                          <span>{{ Number(item.duration || 0).toFixed(2) }} 秒</span>
                        </div>
                        <span v-if="isTtsReadingModified(item)" class="tts-pronunciation-badge">已修音</span>
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
                      <div class="tts-pronunciation-tools">
                        <button
                          class="tts-pronunciation-toggle"
                          type="button"
                          :disabled="ttsEditor.task?.status === 'running'"
                          @click="toggleTtsPronunciationEditor(item)"
                        >{{ isTtsPronunciationOpen(item.index) ? '收起发音修正' : '发音修正' }}</button>
                        <template v-if="isTtsPronunciationOpen(item.index)">
                          <div class="tts-pronunciation-editor">
                            <label>
                              <span>朗读文本 <small>只发送给配音引擎，不会写进字幕</small></span>
                              <textarea
                                :value="ttsReadingDrafts[item.index]"
                                rows="2"
                                maxlength="1200"
                                :disabled="ttsEditor.task?.status === 'running'"
                                placeholder="例如：点击chong2绘按钮"
                                @input="updateTtsReadingDraft(item, $event)"
                              ></textarea>
                            </label>
                            <button
                              class="ghost-btn compact-btn"
                              type="button"
                              :disabled="ttsEditor.task?.status === 'running' || !isTtsReadingModified(item)"
                              @click="resetTtsReadingDraft(item)"
                            >恢复原文</button>
                          </div>
                          <small class="tts-subtitle-preview">成片字幕保持：{{ item.text }}</small>
                        </template>
                      </div>
                      <div v-if="item.index < ttsEditor.segments.length" class="tts-boundary-row">
                        <label><span>额外停顿</span><input v-model.number="ttsPauseDrafts[item.index]" type="number" min="0" max="30" step="0.1" /><small>秒</small></label>
                        <button class="ghost-btn compact-btn" type="button" :disabled="ttsBoundaryBusy" @click="previewTtsBoundary(item)">试听交界</button>
                        <button class="ghost-btn compact-btn" type="button" :disabled="ttsBoundaryBusy" @click="saveTtsPause(item)">保存停顿</button>
                        <button v-if="ttsEditor.structural_edit_available !== false" class="ghost-btn compact-btn" type="button" :disabled="ttsEditor.task?.status === 'running'" @click="openTtsBoundaryEditor(item, true)">调整断点</button>
                      </div>
                      <button v-if="ttsEditor.structural_edit_available !== false" class="tts-pronunciation-toggle" type="button" :disabled="ttsEditor.task?.status === 'running' || String(item.text || '').length < 2" @click="openTtsBoundaryEditor(item, false)">在本句内新增断点</button>
                    </article>
                  </div>
                  <div v-if="ttsBoundary.open" class="tts-boundary-editor">
                    <div class="tts-refine-parameter-head"><div><span class="sidebar-label">断句与停顿</span><h4>{{ ttsBoundary.replaceCount === 2 ? '调整现有断点' : '在本句内新增断点' }}</h4></div><button class="ghost-btn compact-btn" type="button" @click="closeTtsBoundaryEditor">关闭</button></div>
                    <label><span>在文字中点击需要断开的位置</span><textarea :value="ttsBoundary.sourceText" rows="3" readonly @click="updateTtsBoundaryParts($event.target.selectionStart)" @keyup="updateTtsBoundaryParts($event.target.selectionStart)"></textarea></label>
                    <label v-if="ttsBoundary.replaceCount === 2" class="switch-row"><input v-model="ttsBoundary.merge" type="checkbox" @change="refreshTtsBoundaryTokenCounts" /><span class="switch-track"><i></i></span><strong>不再断开，合并为一句</strong></label>
                    <div v-if="!ttsBoundary.merge" class="tts-boundary-preview-grid"><label><span>前半句</span><textarea v-model="ttsBoundary.leftReading" rows="2" @input="refreshTtsBoundaryTokenCounts"></textarea><small>{{ ttsBoundary.counts[0] ?? '-' }}<template v-if="ttsBoundary.limit"> / {{ ttsBoundary.limit }} token</template></small></label><label><span>后半句</span><textarea v-model="ttsBoundary.rightReading" rows="2" @input="refreshTtsBoundaryTokenCounts"></textarea><small>{{ ttsBoundary.counts[1] ?? '-' }}<template v-if="ttsBoundary.limit"> / {{ ttsBoundary.limit }} token</template></small></label></div>
                    <div v-else class="tts-boundary-preview-grid"><label><span>合并后的朗读文本</span><textarea :value="ttsBoundary.leftReading + ttsBoundary.rightReading" rows="3" readonly></textarea><small>{{ ttsBoundary.counts[0] ?? '-' }}<template v-if="ttsBoundary.limit"> / {{ ttsBoundary.limit }} token</template></small></label></div>
                    <div class="tts-boundary-actions"><label v-if="!ttsBoundary.merge"><span>额外停顿</span><input v-model.number="ttsBoundary.pause" type="number" min="0" max="30" step="0.1" /> 秒</label><span v-if="ttsBoundaryOverLimit()" class="script-upload-error">存在超过 {{ ttsBoundary.limit }} token 的片段，请重新选择断点。</span><button class="primary-btn compact-btn" type="button" :disabled="ttsBoundaryBusy || ttsBoundary.checking || ttsBoundaryOverLimit()" @click="submitTtsBoundary">{{ ttsBoundaryBusy ? '处理中…' : '确认并重配' }}</button></div>
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
              <div
                v-if="!(form.step_mode && isGuidedWorkflowJob(activeJob) && activeJob?.id === visualEditorProjectId && guidedStage !== 'completed')"
                class="visual-render-footer"
              >
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

        <section v-else-if="activePage === 'plugins'" class="plugins-page stack">
          <article class="panel plugins-hero">
            <div>
              <div class="eyebrow">OCV EXTENSIONS</div>
              <h2>扩展与插件</h2>
              <p class="muted large">为 ComfyUI、本地模型和社区工作流预留的安全扩展入口。</p>
            </div>
            <span class="status-chip warning">框架预览</span>
          </article>

          <article class="panel plugin-manager-panel">
            <div class="panel-head plugin-manager-head">
              <div>
                <div class="eyebrow">插件管理</div>
                <h2>已安装插件</h2>
                <p class="muted">{{ pluginNotice || '当前版本只读取插件清单，不执行第三方代码。' }}</p>
              </div>
              <div class="plugin-manager-actions">
                <button class="ghost-btn" type="button" :disabled="pluginsLoading" @click="loadPlugins">
                  {{ pluginsLoading ? '扫描中…' : '重新扫描' }}
                </button>
                <button class="ghost-btn" type="button" @click="openPluginsFolder">打开插件目录</button>
                <button class="ghost-btn" type="button" disabled title="后续版本开放本地插件包安装">安装本地插件包（待开放）</button>
              </div>
            </div>

            <div v-if="pluginMessage" class="api-key-message">{{ pluginMessage }}</div>
            <div v-if="!pluginsLoading && !plugins.length" class="plugin-empty-state">
              <strong>尚未发现插件清单</strong>
              <span>把插件文件夹放入根目录的 plugins 文件夹，然后点击“重新扫描”。</span>
            </div>
            <div v-else class="plugin-grid">
              <article v-for="plugin in plugins" :key="plugin.folder" class="plugin-card" :class="{ invalid: !plugin.valid, disabled: !plugin.enabled }">
                <div class="plugin-card-head">
                  <div>
                    <strong>{{ plugin.name }}</strong>
                    <span>v{{ plugin.version }} · {{ plugin.author }}</span>
                  </div>
                  <label class="inline-switch plugin-toggle" :title="plugin.valid ? '记录插件启用状态；当前框架不会执行插件代码' : '清单无效，无法启用'">
                    <input :checked="plugin.enabled" type="checkbox" :disabled="!plugin.valid || pluginToggling === plugin.folder" @change="togglePlugin(plugin)" />
                    <span class="switch-track"><span></span></span>
                  </label>
                </div>
                <p>{{ plugin.description || '插件作者尚未填写说明。' }}</p>
                <div class="plugin-meta">
                  <span>类型：{{ plugin.type }}</span>
                  <span v-if="plugin.ocv_version">OCV：{{ plugin.ocv_version }}</span>
                  <span>{{ plugin.enabled ? '已启用（仅记录）' : '已停用' }}</span>
                </div>
                <div v-if="plugin.permissions?.length" class="plugin-permissions">声明权限：{{ plugin.permissions.join('、') }}</div>
                <div v-if="plugin.issue" class="board-error">清单错误：{{ plugin.issue }}</div>
              </article>
            </div>

            <div class="plugin-security-note">
              <strong>安全说明</strong>
              <span>当前版本不会加载插件入口文件。第三方插件不代表 OCV 官方审核或担保；后续开放执行能力时，插件将默认禁用并明确展示权限。</span>
            </div>
          </article>
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
                  <option value="indextts25">本地 GPU · IndexTTS-2.5</option>
                  <option value="cluster">集群 GPU</option>
                  <option value="qwen">Qwen-TTS</option>
                </select>
                <span class="status-chip" :class="((ttsEngine === 'indextts25' && health.tts25_online) || (ttsEngine === 'cluster' && cloudReady) || (ttsEngine === 'qwen' && apiKeyStatus.qwen_tts?.configured)) ? 'success' : 'warning'">
                  {{ ttsEngine === 'indextts25' ? (health.tts25_online ? '2.5 已就绪' : '2.5 未就绪') : (ttsEngine === 'cluster' ? (cloudReady ? '集群已就绪' : '集群未就绪') : (apiKeyStatus.qwen_tts?.configured ? 'Qwen 已就绪' : 'Qwen 未配置')) }}
                </span>
              </div>
            </div>

            <div v-if="ttsEngine === 'indextts25'" class="local-tts-hardware-note module1-hardware-note">
              <strong>本地配音需要 NVIDIA 显卡</strong>
              <span>建议至少 8GB 显存，并保持并行数 1；6GB 及以下显存建议改用集群 GPU 或 Qwen-TTS。</span>
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
                  <textarea ref="module1ScriptTextarea" v-model="form.script" rows="18" placeholder="粘贴需要转换成语音的文案。"></textarea>
                  <div class="structural-blank-toolbar">
                    <span>结构留白</span>
                    <input v-model.number="structuralBlankSeconds" type="number" min="0.2" max="30" step="0.1" aria-label="留白秒数" />
                    <button type="button" class="ghost-btn" @click="insertStructuralBlank(module1ScriptTextarea)">在光标处插入</button>
                    <small>建议放在完整句之间；标记不会被朗读或显示为字幕。</small>
                  </div>
                </label>
              </div>

              <div class="module1-settings-column">
                <div class="tts-parameter-panel">
                  <div class="tts-parameter-head">
                    <div>
                      <div class="sidebar-label">参考声音</div>
                      <h3>{{ ttsEngine === 'indextts25' ? '本地参考音色' : (ttsEngine === 'cluster' ? '集群参考音色' : 'Qwen 系统音色') }}</h3>
                    </div>
                  </div>
                  <div v-if="ttsEngine === 'indextts25'" class="script-upload-field">
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
                        <option value="">参考原音频</option>
                        <option v-for="emotion in settings.tts?.emotions || []" :key="emotion" :value="emotion">{{ emotionLabel(emotion) }}</option>
                      </select>
                    </label>
                    <label class="tts-emotion-strength">
                      <span>情绪强度（0–1）· {{ Number(form.tts_emotion_weight).toFixed(2) }}</span>
                      <input v-model.number="form.tts_emotion_weight" type="range" min="0" max="1" step="0.05" :disabled="!form.tts_emotion" />
                      <small class="muted">选择具体情绪后生效。</small>
                    </label>
                    <label><span>语速</span><input v-model.number="form.tts_speed" type="number" min="0.5" max="2" step="0.01" /></label>
                    <label><span>音量</span><input v-model.number="form.tts_volume" type="number" min="0.1" max="10" step="0.01" /></label>
                    <label><span>音调</span><input v-model.number="form.tts_pitch" type="number" min="-12" max="12" step="1" /></label>
                    <label v-if="ttsEngine !== 'cluster'"><span>并行数</span><input v-model.number="form.tts_parallelism" type="number" min="1" max="3" step="1" /></label>
                    <div v-else class="cluster-notice">集群自动将全部文本分块分配给空闲 GPU。</div>
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
            <audio v-if="module1Job?.artifacts?.audio" class="module1-audio-player" controls :src="module1AudioPreviewUrl"></audio>
            <div v-if="module1ArtifactEntries.length" class="artifact-grid module1-artifacts">
              <button v-for="item in module1ArtifactEntries" :key="item.key" class="artifact-card" type="button" @click="openArtifactFolder(item.url)">
                <div class="artifact-label">{{ artifactLabel(item.key) }}</div>
                <div class="artifact-value">{{ item.url.split('/').pop() }}</div>
                <div class="artifact-action">打开所在文件夹</div>
              </button>
            </div>
            <section v-if="module1Job?.status === 'completed'" class="guided-audio-editor module1-refine-editor">
              <div class="guided-editor-head">
                <div><div class="sidebar-label">配音精修</div><strong>逐句试听、重配、断句与停顿</strong></div>
                <div class="inline-actions"><button class="ghost-btn compact-btn" type="button" :disabled="!ttsEditor.history_count || ttsBoundaryBusy" @click="undoLastTtsEdit">撤销 · {{ ttsEditor.history_count || 0 }}/{{ ttsEditor.history_limit || 20 }}</button><button class="primary-btn compact-btn" type="button" :disabled="!selectedTtsSegmentIndices.length || ttsEditor.task?.status === 'running'" @click="regenerateSelectedTtsSegments">重配选中句</button></div>
              </div>
              <div v-if="ttsEditorLoading" class="empty-state">正在读取逐句配音…</div>
              <div v-else-if="!ttsEditor.available" class="timing-unavailable">{{ ttsEditor.message || '当前任务没有可精修的逐句音频。' }}</div>
              <template v-else>
                <details class="guided-tts-settings">
                  <summary>本次重配参数（默认沿用项目）</summary>
                  <div class="tts-refine-parameter-panel" :class="{ locked: ttsEditor.task?.status === 'running' }">
                    <div class="tts-refine-parameter-head">
                      <div><span class="sidebar-label">{{ ttsRefineEngineLabel }}</span><h4>只作用于本次选中的句子</h4></div>
                      <button class="ghost-btn compact-btn" type="button" :disabled="ttsEditor.task?.status === 'running'" @click="hydrateTtsRefineSettings(ttsEditor)">恢复项目参数</button>
                    </div>
                    <div v-if="ttsEditor.engine === 'indextts25'" class="tts-refine-voice-row">
                      <label class="script-file-picker"><input type="file" accept=".wav,.mp3,.flac,audio/wav,audio/mpeg,audio/flac" :disabled="ttsRefineVoiceUploading || ttsEditor.task?.status === 'running'" @change="uploadTtsRefineVoice" /><span>{{ ttsRefineVoiceUploading ? '上传中' : '更换音源' }}</span><strong>{{ ttsRefineVoiceName || '沿用该任务当前参考音色' }}</strong></label>
                      <small v-if="ttsRefineVoiceError" class="script-upload-error">{{ ttsRefineVoiceError }}</small>
                    </div>
                    <label v-else-if="ttsEditor.engine === 'cluster'" class="tts-refine-wide-field"><span>云端音色</span><select v-model="ttsRefineForm.cluster_voice_key"><optgroup label="云端默认音色"><option v-for="voice in cloudPresetVoiceOptions" :key="`module1-refine-preset:${voice.id}`" :value="`preset:${voice.id}`">{{ voice.display_name || voice.id }}</option></optgroup><optgroup v-if="cloudUploadedVoiceOptions.length" label="我上传的音色"><option v-for="voice in cloudUploadedVoiceOptions" :key="`module1-refine-uploaded:${voice.id}`" :value="`uploaded:${voice.id}`">{{ voice.display_name || voice.id }}</option></optgroup></select></label>
                    <div v-else-if="ttsEditor.engine === 'qwen'" class="form-grid tts-refine-qwen-grid"><label><span>Qwen 系统音色</span><select v-model="ttsRefineForm.qwen_voice"><optgroup v-for="group in qwenVoiceGroups" :key="`module1-refine-${group.label}`" :label="group.label"><option v-for="voice in group.voices" :key="`module1-refine-${voice.value}`" :value="voice.value">{{ voice.label }}</option></optgroup></select></label><label><span>配音描述</span><textarea v-model="ttsRefineForm.qwen_instructions" rows="3" maxlength="1600"></textarea></label></div>
                    <div class="form-grid tts-refine-grid">
                      <label v-if="ttsEditor.engine !== 'qwen'"><span>情绪</span><select v-model="ttsRefineForm.tts_emotion"><option value="">参考原音频</option><option v-for="emotion in settings.tts?.emotions || []" :key="`module1-refine-${emotion}`" :value="emotion">{{ emotionLabel(emotion) }}</option></select></label>
                      <label v-if="ttsEditor.engine !== 'qwen'" class="tts-emotion-strength"><span>情绪强度 · {{ Number(ttsRefineForm.tts_emotion_weight).toFixed(2) }}</span><input v-model.number="ttsRefineForm.tts_emotion_weight" type="range" min="0" max="1" step="0.05" :disabled="!ttsRefineForm.tts_emotion" /></label>
                      <label><span>语速</span><input v-model.number="ttsRefineForm.tts_speed" type="number" min="0.5" max="2" step="0.01" /></label><label><span>音量</span><input v-model.number="ttsRefineForm.tts_volume" type="number" min="0.1" max="10" step="0.01" /></label><label><span>音调</span><input v-model.number="ttsRefineForm.tts_pitch" type="number" min="-12" max="12" step="1" /></label><label v-if="ttsEditor.engine === 'indextts25'"><span>并行数</span><input v-model.number="ttsRefineForm.tts_parallelism" type="number" min="1" max="3" step="1" /></label>
                    </div>
                  </div>
                </details>
                <div v-if="ttsEditor.structural_edit_available === false" class="timing-unavailable">{{ ttsEditor.structural_edit_message }}</div>
                <div class="guided-tts-grid">
                  <article v-for="item in ttsEditor.segments" :key="`module1-edit-${item.index}-${item.audio_url}`" class="guided-tts-card" :class="{ selected: selectedTtsSegmentIndices.includes(item.index) }">
                    <div><strong>第 {{ item.index }} 句</strong><small>{{ Number(item.duration || 0).toFixed(2) }} 秒</small></div><p>{{ item.text }}</p><audio controls preload="none" :src="item.audio_url"></audio>
                    <label><span>选中</span><input v-model="selectedTtsSegmentIndices" type="checkbox" :value="item.index" /></label>
                    <button class="tts-pronunciation-toggle" type="button" @click="toggleTtsPronunciationEditor(item)">{{ isTtsPronunciationOpen(item.index) ? '收起发音修正' : '发音修正' }}</button>
                    <div v-if="isTtsPronunciationOpen(item.index)" class="guided-pronunciation-editor"><small>朗读文本只发送给配音引擎，字幕仍保留原文字。</small><textarea :value="ttsReadingDrafts[item.index]" rows="2" @input="updateTtsReadingDraft(item, $event)"></textarea></div>
                    <div v-if="item.index < ttsEditor.segments.length" class="tts-boundary-row"><label><span>停顿</span><input v-model.number="ttsPauseDrafts[item.index]" type="number" min="0" max="30" step="0.1" /><small>秒</small></label><button class="ghost-btn compact-btn" type="button" @click="previewTtsBoundary(item)">试听</button><button class="ghost-btn compact-btn" type="button" @click="saveTtsPause(item)">保存</button><button v-if="ttsEditor.structural_edit_available !== false" class="ghost-btn compact-btn" type="button" @click="openTtsBoundaryEditor(item, true)">调整断点</button></div>
                    <button v-if="ttsEditor.structural_edit_available !== false" class="tts-pronunciation-toggle" type="button" @click="openTtsBoundaryEditor(item, false)">新增断点</button>
                  </article>
                </div>
                <div v-if="ttsBoundary.open" class="tts-boundary-editor"><div class="tts-refine-parameter-head"><div><span class="sidebar-label">断句与停顿</span><h4>{{ ttsBoundary.replaceCount === 2 ? '调整现有断点' : '新增断点' }}</h4></div><button class="ghost-btn compact-btn" type="button" @click="closeTtsBoundaryEditor">关闭</button></div><label><span>点击文字中的断开位置</span><textarea :value="ttsBoundary.sourceText" rows="3" readonly @click="updateTtsBoundaryParts($event.target.selectionStart)"></textarea></label><label v-if="ttsBoundary.replaceCount === 2" class="switch-row"><input v-model="ttsBoundary.merge" type="checkbox" @change="refreshTtsBoundaryTokenCounts" /><span class="switch-track"><i></i></span><strong>合并为一句</strong></label><div v-if="!ttsBoundary.merge" class="tts-boundary-preview-grid"><label><span>前半句朗读文本</span><textarea v-model="ttsBoundary.leftReading" rows="2" @input="refreshTtsBoundaryTokenCounts"></textarea><small>{{ ttsBoundary.counts[0] ?? '-' }} / {{ ttsBoundary.limit || '-' }} token</small></label><label><span>后半句朗读文本</span><textarea v-model="ttsBoundary.rightReading" rows="2" @input="refreshTtsBoundaryTokenCounts"></textarea><small>{{ ttsBoundary.counts[1] ?? '-' }} / {{ ttsBoundary.limit || '-' }} token</small></label></div><div class="tts-boundary-actions"><label v-if="!ttsBoundary.merge"><span>额外停顿</span><input v-model.number="ttsBoundary.pause" type="number" min="0" max="30" step="0.1" /> 秒</label><span v-if="ttsBoundaryOverLimit()" class="script-upload-error">存在超限片段。</span><button class="primary-btn compact-btn" type="button" :disabled="ttsBoundaryBusy || ttsBoundaryOverLimit()" @click="submitTtsBoundary">确认并重配</button></div></div>
              </template>
            </section>
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
      <footer class="legal-footer" aria-label="开源许可证与官方源码">
        <strong class="official-disclaimer">
          官方声明：OCV 目前未授权任何培训机构、付费课程或软件售卖方；第三方收费服务不代表 OCV 官方授权，其交付与售后由第三方自行承担。
        </strong>
        <span>OCV v1.1.1 · Copyright © 2026 周若雨、何允</span>
        <span>本程序不附带任何担保</span>
        <a href="https://github.com/IFRIT-Zhou/One-Click-VidGen" target="_blank" rel="noopener noreferrer">获取对应源代码</a>
        <a href="https://github.com/IFRIT-Zhou/One-Click-VidGen/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">AGPL-3.0-only</a>
        <a href="https://github.com/IFRIT-Zhou/One-Click-VidGen/blob/main/ADDITIONAL_TERMS.md" target="_blank" rel="noopener noreferrer">附加条款</a>
        <a href="https://github.com/IFRIT-Zhou/One-Click-VidGen/blob/main/TRADEMARKS.md" target="_blank" rel="noopener noreferrer">品牌规则</a>
      </footer>
    </main>

    <div v-if="cloudRechargeOpen" class="cloud-recharge-overlay" role="dialog" aria-modal="true" aria-labelledby="cloud-recharge-title" @click.self="closeCloudRecharge">
      <section class="cloud-recharge-dialog">
        <header class="cloud-recharge-head">
          <div>
            <span class="cloud-recharge-kicker">ALIPAY · CLOUD CREDITS</span>
            <h2 id="cloud-recharge-title">充值云端积分</h2>
            <p>选择积分套餐后前往支付宝官方收银台，支付结果由集群服务端确认。</p>
          </div>
          <button class="cloud-recharge-close" type="button" aria-label="关闭充值窗口" @click="closeCloudRecharge">×</button>
        </header>

        <div class="cloud-recharge-account">
          <div><small>充值账户</small><strong>{{ cloudSession.user?.email || '云端账户' }}</strong></div>
          <div><small>当前可用积分</small><strong>{{ cloudAccount.credits?.available ?? '—' }}</strong></div>
          <span class="cloud-recharge-secure"><i></i> HTTPS · RSA2</span>
        </div>

        <div v-if="cloudRechargeLoadingProducts" class="cloud-recharge-loading">
          <span class="preflight-spinner"></span>正在读取可用套餐…
        </div>
        <template v-else>
          <div class="cloud-recharge-section-title"><strong>选择充值套餐</strong><span>积分支付成功后自动到账</span></div>
          <div class="cloud-recharge-products">
            <button
              v-for="product in cloudRechargeProducts"
              :key="product.product_id"
              class="cloud-recharge-product"
              :class="{ selected: cloudRechargeSelectedId === product.product_id }"
              type="button"
              :disabled="cloudRechargeBusy || cloudRechargeOrder?.status === 'pending'"
              @click="cloudRechargeSelectedId = product.product_id"
            >
              <small>{{ cloudRechargeProductLabel(product) }}</small>
              <strong>{{ Number(product.credits).toLocaleString('zh-CN') }}<em>积分</em></strong>
              <span>¥{{ formatCloudRechargeAmount(product.amount_fen) }}</span>
              <i aria-hidden="true">✓</i>
            </button>
          </div>
        </template>

        <div v-if="cloudRechargeOrder" class="cloud-recharge-order" :class="cloudRechargeOrder.status">
          <div class="cloud-order-state-icon">
            <span v-if="cloudRechargeOrder.status === 'paid'">✓</span>
            <span v-else-if="['cancelled', 'expired', 'refunded'].includes(cloudRechargeOrder.status)">×</span>
            <span v-else class="cloud-order-spinner"></span>
          </div>
          <div>
            <small>订单 {{ cloudRechargeOrder.order_id }}</small>
            <strong>{{ cloudRechargeStatusLabel }}</strong>
            <p>{{ cloudRechargeStatusDescription }}</p>
          </div>
          <b>¥{{ formatCloudRechargeAmount(cloudRechargeOrder.amount_fen) }}</b>
        </div>

        <div v-if="cloudRechargeError" class="cloud-recharge-message error">{{ cloudRechargeError }}</div>
        <div v-else-if="cloudRechargeMessage" class="cloud-recharge-message success">{{ cloudRechargeMessage }}</div>

        <footer class="cloud-recharge-actions">
          <div>
            <span>应付金额</span>
            <strong>¥{{ formatCloudRechargeAmount(cloudRechargeSelectedProduct?.amount_fen) }}</strong>
          </div>
          <button v-if="!cloudRechargeOrder || cloudRechargeOrder.status !== 'pending'" class="cloud-recharge-pay" type="button" :disabled="cloudRechargeBusy || !cloudRechargeSelectedProduct" @click="startCloudRecharge">
            {{ cloudRechargeBusy ? '正在创建订单…' : '前往支付宝支付' }}
          </button>
          <template v-else>
            <button class="ghost-btn" type="button" :disabled="!cloudRechargePaymentUrl" @click="openCloudPaymentPage">重新打开支付宝</button>
            <button class="cloud-recharge-pay" type="button" :disabled="cloudRechargeChecking" @click="checkCloudRechargeOrder(true)">
              {{ cloudRechargeChecking ? '正在查询…' : '我已完成支付' }}
            </button>
          </template>
        </footer>
        <p class="cloud-recharge-footnote">客户端不会接触支付宝密码或银行卡信息；积分只根据支付宝服务端异步通知入账。</p>
      </section>
    </div>

    <div
      v-if="cloudRechargeSuccess"
      class="cloud-recharge-success-overlay"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="cloud-recharge-success-title"
    >
      <section class="cloud-recharge-success-dialog">
        <button class="cloud-recharge-success-close" type="button" aria-label="关闭充值成功提示" @click="finishCloudRecharge">×</button>
        <div class="cloud-recharge-success-icon" aria-hidden="true">
          <span>✓</span>
          <i></i>
        </div>
        <span class="cloud-recharge-kicker">PAYMENT CONFIRMED</span>
        <h2 id="cloud-recharge-success-title">充值成功</h2>
        <p>支付宝支付结果已由云端确认，积分已存入你的账户。</p>
        <div class="cloud-recharge-success-credit">
          <small>本次到账</small>
          <strong>
            +{{ Number(cloudRechargeSuccess.credits || 0).toLocaleString('zh-CN') }}
            <em>积分</em>
          </strong>
        </div>
        <div class="cloud-recharge-success-meta">
          <div><span>充值金额</span><strong>¥{{ formatCloudRechargeAmount(cloudRechargeSuccess.amount_fen) }}</strong></div>
          <div><span>订单状态</span><strong>已到账</strong></div>
        </div>
        <small class="cloud-recharge-success-order">订单 {{ cloudRechargeSuccess.order_id }}</small>
        <footer class="cloud-recharge-success-actions">
          <button class="ghost-btn" type="button" @click="continueCloudRecharge">继续充值</button>
          <button class="cloud-recharge-pay" type="button" @click="finishCloudRecharge">完成</button>
        </footer>
      </section>
    </div>

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

<script>
import { useWorkspace } from './useWorkspace'
export default { setup: useWorkspace }
</script>
