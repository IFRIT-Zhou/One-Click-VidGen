
<template>
 <div class="studio live-studio">
  <aside class="rail">
   <button class="brand" @click="goHome"><img src="/one-click-vidgen-logo.png" alt="OCV"/><span>OCV<small>创作工作台</small></span></button>
   <button class="new-button" @click="newProject()">＋ 新建图文视频</button>
   <button class="new-button" @click="newProject('dynamic')">▷ 新建动态视频</button>
   <nav><button :class="{active:studioPage==='home'}" @click="goHome"><span>▦</span>项目首页</button><button :class="{active:studioPage==='logs'}" @click="openLogs()" aria-label="任务日志"><span>≋</span>任务日志</button><p>附加功能</p><button @click="newProject('audio')"><span>◉</span>配音工作室</button><button @click="newProject('subtitle')"><span>≡</span>字幕识别</button><button :class="{active:studioPage==='images'}" @click="studioPage='images'"><span>▧</span>图片工作室</button><button :class="{active:studioPage==='comfyui'}" @click="studioPage='comfyui'"><span>⌘</span>ComfyUI 工作台</button></nav>
   <button v-for="job in studioLiveJobs" :key="job.id" class="running running-project-entry" @click="openProject(job)" :aria-label="'返回进行中项目：'+(job.request?.project_name||job.id)" title="返回此项目当前进度页面"><b>{{job.progress}}%</b><span class="dot"/> 任务进行中<progress :value="job.progress" max="100"/><span class="running-project-name">{{job.request?.project_name||job.id}} →</span></button>
   <div class="rail-bottom"><Announcements :enabled="!!session.user" /><button :class="{active:studioPage==='services'}" @click="studioDrawer='接口与服务'">⚙ <span>接口与服务</span><i v-if="health.ok" class="dot"/></button><button @click="studioPage='plugins';openPluginsPage()">⊞ <span>插件与设置</span></button><a class="legacy-link" href="/legacy.html">返回经典界面 ↗</a><div class="local"><span class="avatar">本</span><div>本地工作空间<small>{{health.ok?'后端已连接':'正在连接后端…'}}</small></div></div>
   </div>
  </aside>
  <main>
   <header class="studio-topbar"><div>工作空间 <span>/</span> {{studioPage==='services'?'接口与服务':studioPage==='videos'?'动态视频':studioPage==='images'?'图片工作室':studioPage==='comfyui'?'ComfyUI 工作台':studioPage==='home'?'项目首页':studioPage==='logs'?'任务日志':studioPage==='plugins'?'插件与设置':studioTitle}}</div><div class="cloud-account-entry">
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
        </div></header>
   <div v-if="studioError" class="studio-notice error" role="alert">{{studioError}}<button @click="studioError=''">×</button></div>
   <div v-if="!health.ok" class="studio-notice">尚未连接本地后端，请通过 Launcher 启动 OCV。<button :disabled="studioBusy" @click="reconnectStudio">重新连接</button></div>
   <VideoStudio v-if="studioPage==='videos'" :pending-image-route="pendingVideoImageRoute" @project-change="activeVideoProjectId=$event" @image-route-applied="imageRouteApplied" @new-project="newProject('dynamic')" @duplicate-config="newProject('dynamic',$event)" @edit-config="newProject('dynamic',$event)" @edit-audio="openDynamicAudio($event)" />
   <ImageStudio v-else-if="studioPage==='images'" />
   <ComfyUIWorkbench v-else-if="studioPage==='comfyui'" />
   <section v-else-if="studioPage==='home'" class="content home">
    <div class="page-heading"><div><p class="eyebrow">YOUR CREATIVE SPACE</p><h1>让想法，成为作品。</h1><p class="muted">从一段文字开始，继续上次的创作。</p></div><button class="primary" @click="newProject()">＋ 新建图文视频</button></div>
    <div v-if="!session.user" class="resume"><div><h3>连接你的工作空间</h3><p>登录本地工作台后，查看项目并开始创作。</p></div><button @click="studioDrawer='接口与服务'">打开登录与配置</button></div>
    <div class="home-layout">
    <div class="home-main">
    <div class="section-heading"><h2>我的项目 <small>{{jobTotal}}</small></h2><input v-model="studioSearch" class="search" placeholder="搜索当前页项目…" aria-label="搜索项目"/></div>
    <div class="filters"><button v-for="f in [{id:'all',label:'全部'},{id:'video',label:'图文视频'},{id:'dynamic',label:'动态视频'},{id:'audio',label:'仅配音'},{id:'subtitle',label:'仅字幕识别'}]" :key="f.id" :class="{selected:studioFilter===f.id}" @click="studioFilter=f.id">{{f.label}}</button></div>
    <div class="project-table"><div class="table-head"><span>项目名称</span><span class="col-wide">类型</span><span>状态</span><span>进度</span><span class="col-wide">更新</span><span/></div><div v-for="job in studioJobs" :key="job.id" class="project-row"><button class="project-title" @click="openProject(job)"><span class="project-icon">{{typeOf(job)==='dynamic'?'▷':typeOf(job)==='video'?'▧':typeOf(job)==='audio'?'◉':'≡'}}</span><span><b>{{job.request?.project_name||job.id}}</b><small><span class="project-type-inline">{{typeLabel(job)}} · </span>{{job.message}}</small></span></button><span class="col-wide project-type">{{typeLabel(job)}}</span><span class="status" :class="['is-'+job.status,{waiting:job.status==='waiting_confirmation'}]">{{statusLabel(job.status)}}</span><span class="project-progress"><span class="project-progress-bar" aria-hidden="true"><i :style="{width:Math.max(0,Math.min(100,Number(job.progress)||0))+'%'}"></i></span><span class="muted">{{job.progress}}%</span></span><span class="col-wide project-updated" :title="formatJobTime(job.updated_at,true)">{{formatJobTime(job.updated_at)}}</span><span class="project-actions"><button type="button" class="col-wide project-quick" :aria-label="'查看日志：'+(job.request?.project_name||job.id)" @click="openLogs(job)">日志</button><details class="project-menu"><summary>更多</summary><button @click="openProject(job)">打开项目</button><button @click="openLogs(job)">查看日志</button><button @click="exportDiagnosticPackage(job)">诊断包</button><button :disabled="['running','queued'].includes(job.status)" @click="deleteGenerationJob(job)">删除任务</button></details></span></div><div v-if="!studioJobs.length" class="empty">当前分类暂无项目。</div></div>
    <div v-if="jobTotalPages>1" class="studio-pagination"><button :disabled="jobPage<=1" @click="changeStudioPage(jobPage-1)">上一页</button><span>{{jobPage}} / {{jobTotalPages}}</span><button :disabled="jobPage>=jobTotalPages" @click="changeStudioPage(jobPage+1)">下一页</button></div>
    </div>
    <aside class="home-side" aria-label="快速开始与草稿">
     <section class="home-quick" aria-labelledby="home-quick-title"><h2 id="home-quick-title">快速开始</h2>
      <div class="home-quick-grid"><button type="button" @click="newProject()"><span aria-hidden="true">▧</span><b>图文视频</b><small>文案配音 + 分镜图</small></button><button type="button" @click="newProject('dynamic')"><span aria-hidden="true">▷</span><b>动态视频</b><small>分步制作动态镜头</small></button><button type="button" @click="newProject('audio')"><span aria-hidden="true">◉</span><b>仅配音</b><small>生成后逐句精修</small></button><button type="button" @click="newProject('subtitle')"><span aria-hidden="true">≡</span><b>字幕识别</b><small>音视频转 SRT</small></button></div>
     </section>
    <div v-if="studioDrafts.length" class="studio-drafts">
     <header class="draft-heading"><h2>未完成的草稿 <small>{{studioDrafts.length}}</small></h2><button @click="clearStudioDrafts">清空草稿</button></header>
     <div class="draft-rows"><div v-for="draft in visibleStudioDrafts" :key="draft.id" class="draft-row"><button class="draft-open" @click="newProject(draft.kind,draft)"><span>{{draft.name||'未命名草稿'}}</span><small>{{typeLabel(draft.form?.dynamic_video?'dynamic':draft.kind)}}</small></button><button class="draft-delete" :aria-label="`删除草稿：${draft.name||'未命名草稿'}`" @click="deleteStudioDraft(draft.id)">删除</button></div></div>
     <button v-if="studioDrafts.length>3" class="draft-expand" @click="studioDraftsExpanded=!studioDraftsExpanded">{{studioDraftsExpanded?'收起':'查看其余 '+(studioDrafts.length-3)+' 条草稿'}}</button>
    </div>
     <CodexBridgePanel compact @open-draft="openCodexDraft" @open-project="openCodexProject"/>
    </aside>
    </div>
   </section>
   <section v-else-if="studioPage==='logs'" class="content logs-page">
    <div class="page-heading"><div><p class="eyebrow">TASK CONSOLE</p><h1>任务日志</h1><p class="muted">后台执行过程、生成进度与报错原文。</p></div><button :disabled="!studioJob" @click="studioPage='editor'">返回当前项目 ↗</button></div>
    <div class="logs-layout">
    <aside class="logs-list" aria-label="任务列表">
     <div class="logs-list-head"><h2>任务 <small>{{jobTotal}}</small></h2></div>
     <div class="logs-jobs"><button v-for="job in jobs" :key="job.id" type="button" class="logs-job" :class="{active:studioJob?.id===job.id}" :aria-current="studioJob?.id===job.id?'true':null" @click="openLogs(job)"><b>{{job.request?.project_name||job.id}}</b><small><span class="status" :class="['is-'+job.status,{waiting:job.status==='waiting_confirmation'}]">{{statusLabel(job.status)}}</span> · {{typeLabel(job)}} · {{formatJobTime(job.updated_at)}}</small><span class="project-progress-bar" aria-hidden="true"><i :style="{width:Math.max(0,Math.min(100,Number(job.progress)||0))+'%'}"></i></span></button><div v-if="!jobs.length" class="empty">暂无任务。</div></div>
     <div v-if="jobTotalPages>1" class="studio-pagination"><button :disabled="jobPage<=1" @click="changeStudioPage(jobPage-1)">上一页</button><span>{{jobPage}} / {{jobTotalPages}}</span><button :disabled="jobPage>=jobTotalPages" @click="changeStudioPage(jobPage+1)">下一页</button></div>
    </aside>
    <div class="logs-main">
    <div class="log-toolbar"><label>查看任务<select :value="studioJob?.id||''" @change="openLogs(jobs.find(j=>j.id===$event.target.value))"><option value="" disabled>选择任务</option><option v-for="job in jobs" :key="job.id" :value="job.id">{{job.request?.project_name||job.id}}</option></select></label><span class="muted">{{studioJob?.message||'请选择任务'}} · {{studioJob?.progress||0}}%</span></div>
    <TaskConsole :lines="studioTaskLogs" :diagnostic-available="!!studioJob" :diagnostic-exporting="diagnosticExporting" @export-diagnostic="exportDiagnosticPackage(studioJob)"/>
    <div class="log-help" role="status"><span>{{diagnosticMessage}}</span><button :disabled="!studioJob" @click="studioDrawer='参数回顾'">参数回顾 · 只读</button></div>
    </div>
    </div>
   </section>
   <section v-else-if="studioPage==='services'" class="content services-page"><div class="page-heading"><div><h1>接口与服务</h1><p class="muted">每类服务独立配置与保存，先测试单张或单镜，再批量运行。</p></div><div class="service-header-actions"><button @click="apiGuideOpen=true">API 设置说明 ↗</button><button @click="closeServicesChecked">返回工作页面</button></div></div><div class="services-nav" role="tablist" aria-label="接口分类" @keydown="onServiceTabKey"><button v-for="tab in serviceTabs" :id="'service-tab-'+tab.id" :key="tab.id" type="button" role="tab" :class="{active:serviceTab===tab.id}" :aria-selected="serviceTab===tab.id" :aria-controls="tab.panel" :tabindex="serviceTab===tab.id?0:-1" @click="serviceTab=tab.id">{{tab.label}}<template v-if="serviceMark(tab.id)"><span class="service-tab-mark" :class="serviceMark(tab.id).cls" aria-hidden="true">{{serviceMark(tab.id).text}}</span><span class="service-sr-only">（{{serviceMark(tab.id).title}}）</span></template></button></div><div class="studio-service-settings">
      <div class="brand">
        <img class="brand-mark brand-logo" src="/one-click-vidgen-logo.png" alt="One-Click VidGen Logo" />
        <div>
          <div class="brand-name">一键生成视频</div>
          <div class="brand-sub">One-Click VidGen</div>
        </div>
      </div>
      <button v-show="serviceTab==='overview'" class="sidebar-preflight-button" type="button" :disabled="preflightRunning || !session.user" @click="runManualPreflight">
        <span class="sidebar-preflight-icon" aria-hidden="true">⚡</span>
        <span>
          <strong>{{ preflightRunning ? '正在检测…' : '启动前自动检测' }}</strong>
          <small>API、TTS、素材与运行环境</small>
        </span>
      </button>

      <div v-if="session.auth_mode !== 'local'" class="sidebar-card auth-card">
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

      <div v-if="session.user" v-show="serviceTab==='overview'" class="sidebar-card cloud-pool-priority-card" :class="{ active: form.use_cloud_image_pool }">
        <div class="cloud-pool-toggle-row">
          <div>
            <div class="sidebar-label">OCV 托管服务</div>
            <strong>使用号池</strong>
            <small>{{ form.use_cloud_image_pool ? 'Agent 与出图均由云端号池托管' : '不会配置 API 的用户，强烈建议使用号池托管服务' }}</small>
          </div>
          <label class="inline-switch cloud-pool-switch" :title="cloudSession.authenticated ? '切换云端号池' : '需先登录右上角云端账户'">
            <input v-model="form.use_cloud_image_pool" type="checkbox" />
            <span class="switch-track"><span></span></span>
          </label>
        </div>
        <p class="cloud-pool-price-note">无需自行配置语言和图像 API；稳定版渠道仅收取 5% 服务费（该比例仅适用于稳定版）。稳定版与 ICAN 每张图片约 0.05 积分；每 1000 字文案的 LLM 消耗通常约 0.1 元，实际费用会随模型、输出长度及重试次数略有浮动。</p>
        <div v-if="form.use_cloud_image_pool"><label class="stack"><span>图片生成渠道</span><IcanChannelHint :active="form.method === 'ican'"><select v-model="form.method"><option value="running">稳定版</option><option value="ican">ICAN · GPT Image 2.5</option></select></IcanChannelHint></label><CloudImageQuality :form="form" /></div>
        <div v-if="form.use_cloud_image_pool" class="cloud-pool-status" :class="cloudSession.authenticated ? 'ready' : 'warning'">
          <span v-if="cloudSession.authenticated">文本 + 图像号池已启用 · 可用积分 {{ cloudAvailableCredits }}</span>
          <button v-else type="button" @click="openCloudLogin">请先登录云端账户</button>
        </div>
      </div>

      <div v-if="session.user" v-show="['language','images'].includes(serviceTab)" class="sidebar-card service-config-card">
        <div class="sidebar-label">{{ serviceTab==='language' ? '语言模型 · 文案与分镜规划' : '图像接口 · 分镜图生成与重绘' }}</div>
        <div class="muted small">密钥仅保存到本机 `.env`，页面不会回显原文。OCV 不指定或推荐第三方接口，请只向可信地址发送 API Key。</div>
        <div v-show="serviceTab==='language'" id="service-language" role="tabpanel" aria-labelledby="service-tab-language"><LanguageServicePanel :status="apiKeyStatus.language || {}" :form="apiKeyForm" :save-account="saveLanguageAccount" :saving="savingApiKeys" :pool-mode="form.use_cloud_image_pool" :runtime-error="apiKeyRuntimeErrors.language" @keys="setApiKeyStatus" @state="serviceState.language=$event" /></div>
        <div v-show="serviceTab==='images'" id="service-images" role="tabpanel" aria-labelledby="service-tab-images"><ImageServicePanel :form="form" :concurrency="apiKeyStatus.image?.concurrency || {}" :save-concurrency="saveImageConcurrency" @saved="serviceImageProfileSaved" @state="serviceState.images=$event" /></div>
      </div>
      <div v-if="session.user" v-show="serviceTab==='video'" class="sidebar-card service-config-card">
        <div class="sidebar-label">视频接口 · 动态片段生成</div>
        <div class="muted small">密钥仅保存到本机 `.env`，页面不会回显原文。视频号池尚未接入。OCV 不指定或推荐第三方接口，请只向可信地址发送 API Key。</div>
        <div id="service-video" role="tabpanel" aria-labelledby="service-tab-video"><VideoServicePanel @state="serviceState.video=$event" /></div>
      </div>

      <div v-if="session.user" v-show="serviceTab==='vibe'" class="sidebar-card service-config-card">
        <div class="sidebar-label">Vibe 动效 · 看图设计与渲染检查</div>
        <div class="muted small">密钥仅保存在本机，不写入项目，页面不会回显原文。</div>
        <div id="service-vibe" role="tabpanel" aria-labelledby="service-tab-vibe"><VibeModelSettings @state="serviceState.vibe=$event" /></div>
      </div>
      <div v-show="serviceTab==='tts'" class="sidebar-card service-config-card">
        <div class="sidebar-label">TTS 状态 · 配音引擎</div>
        <div id="service-tts" role="tabpanel" aria-labelledby="service-tab-tts" class="svc-panel">
          <div class="svc-current" aria-live="polite">
            <div class="svc-current-top"><span class="svc-eyebrow">当前配音引擎</span><span class="svc-scope">只读状态</span></div>
            <div class="svc-row-title"><span class="svc-current-main">{{ ttsStatusText }}</span><span class="svc-badge" :class="ttsReady ? 'ok' : startingTts ? 'muted' : 'warn'">{{ ttsReady ? '● 可用' : startingTts ? '检测中…' : '▲ 未连接' }}</span></div>
            <div class="svc-current-sub">{{ health.tts_provider || 'TTS' }} · 当前音色：{{ form.tts_voice_id.startsWith('upload:') ? (ttsVoiceUploadName || '本地上传音色') : voiceLabel(form.tts_voice_id) }}</div>
            <div class="svc-row-actions"><button type="button" :disabled="startingTts || health.tts_online || !session.user" @click="startTts">{{ startingTts ? '检测中…' : '重新检测' }}</button></div>
          </div>
        </div>
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
    </div></section>
   <section v-else-if="studioPage==='plugins'" class="content plugins-content"><section  class="plugins-page stack">
          <article class="panel plugins-hero">
            <div>
              <div class="eyebrow">OCV EXTENSIONS</div>
              <h2>扩展、设置与外观</h2>
              <p class="muted large">管理插件，并按自己的习惯调整 OCV 的界面颜色。</p>
            </div>
            <span class="status-chip warning">框架预览</span>
          </article>

          <article class="panel creation-preference-panel">
            <div class="creation-preference-copy">
              <div class="eyebrow">CREATION PREFERENCES</div>
              <h2>新建任务参数</h2>
              <p class="muted">控制新任务是使用 OCV 推荐默认值，还是沿用你上一次使用的常用选择。</p>
              <small>文案、上传音频、参考图和 BGM 等任务素材始终会清空，不会串到新项目。</small>
            </div>
            <div class="creation-preference-options">
            <label class="creation-preference-toggle">
              <span>
                <strong>新建任务时恢复推荐默认值</strong>
                <small v-if="restoreDefaultsOnNewProject">图文分步关闭 · 动态画面优先 · 节奏标准 · 情绪参考原音频</small>
                <small v-else>沿用上次使用的分步模式、节奏与情绪</small>
              </span>
              <span class="inline-switch">
                <input v-model="restoreDefaultsOnNewProject" type="checkbox" />
                <span class="switch-track"><span></span></span>
              </span>
            </label>
            <label class="creation-preference-toggle">
              <span>
                <strong>上传参考图后自动识别用途</strong>
                <small v-if="form.auto_analyze_reference_images">已开启 · 每张新图片调用一次当前语言模型，结果会缓存</small>
                <small v-else>默认关闭 · 不调用识图 API，可手动填写素材用途</small>
              </span>
              <span class="inline-switch">
                <input v-model="form.auto_analyze_reference_images" type="checkbox" />
                <span class="switch-track"><span></span></span>
              </span>
            </label>
            </div>
          </article>

          <article class="panel appearance-panel">
            <div class="panel-head appearance-head">
              <div>
                <div class="eyebrow">APPEARANCE</div>
                <h2>外观与色彩</h2>
                <p class="muted">选择一个预设，或自由调整颜色。设置只保存在这台电脑的浏览器中。</p>
              </div>
              <button class="ghost-btn" type="button" @click="restoreAppearanceDefault">恢复石墨薄荷</button>
            </div>
            <div class="appearance-presets" role="list" aria-label="内置界面主题">
              <button v-for="theme in appearancePresets" :key="theme.id" type="button" :class="{ active: activeThemeId===theme.id }" @click="chooseTheme(theme)">
                <span class="appearance-swatches"><i v-for="color in [theme.colors.background,theme.colors.panel,theme.colors.accent]" :key="color" :style="{background:color}"></i></span>
                <b>{{theme.name}}</b><small>{{theme.note}}</small>
              </button>
            </div>
            <div class="appearance-custom">
              <div><strong>自定义色盘</strong><small>输入任何你喜欢的颜色。若对比度不足，文字可能看不清；这由当前自定义配色决定。</small></div>
              <label v-for="[key,label] in [['background','页面背景'],['panel','卡片与侧栏'],['text','主要文字'],['muted','辅助文字'],['accent','强调色与按钮'],['danger','错误与删除'],['warning','提醒与警告']]" :key="key">
                <span>{{label}}</span><input v-model="palette[key]" type="color" :aria-label="label" @input="activeThemeId='custom'"/><input v-model="palette[key]" class="appearance-hex" maxlength="7" :aria-label="label+'十六进制颜色'" @input="activeThemeId='custom'"/>
              </label>
            </div>
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
                  <label class="inline-switch plugin-toggle" :title="plugin.valid ? (plugin.framework_only ? '仅记录启用状态' : '启用或停用此插件功能') : '清单无效，无法启用'">
                    <input :checked="plugin.enabled" type="checkbox" :disabled="!plugin.valid || pluginToggling === plugin.folder" @change="togglePlugin(plugin)" />
                    <span class="switch-track"><span></span></span>
                  </label>
                </div>
                <p>{{ plugin.description || '插件作者尚未填写说明。' }}</p>
                <div class="plugin-meta">
                  <span>类型：{{ plugin.type }}</span>
                  <span v-if="plugin.ocv_version">OCV：{{ plugin.ocv_version }}</span>
                  <span>{{ plugin.enabled ? (plugin.framework_only ? '已启用（仅记录）' : '已启用') : '已停用' }}</span>
                </div>
                <div v-if="plugin.permissions?.length" class="plugin-permissions">声明权限：{{ plugin.permissions.join('、') }}</div>
                <div v-if="plugin.issue" class="board-error">清单错误：{{ plugin.issue }}</div>
                <CodexBridgePanel v-if="plugin.id==='codex_bridge' && plugin.enabled && plugin.valid" @open-draft="openCodexDraft" @open-project="openCodexProject"/>
              </article>
            </div>

            <div class="plugin-security-note">
              <strong>安全说明</strong>
              <span>当前版本不会加载插件入口文件。第三方插件不代表 OCV 官方审核或担保；后续开放执行能力时，插件将默认禁用并明确展示权限。</span>
            </div>
          </article>
        </section></section>
   <section v-else-if="studioPage==='new'" class="content creation">
    <nav v-if="form.dynamic_video&&form._rerun_source_project" class="rerun-stage-nav" aria-label="动态视频任务阶段">
      <button v-for="(stage,index) in form._rerun_stages" :key="stage.key" type="button" :class="[stage.state,{active:stage.key==='script'}]" @click="stage.key==='script'?null:form._return_dynamic_stage(stage.key)"><span>{{String(index+1).padStart(2,'0')}}</span><b>{{stage.label}}</b><small>{{stage.key==='script'?'正在编辑':stage.state==='done'?'已完成':'查看阶段'}}</small></button>
    </nav>
<div class="page-heading creation-heading-compact"><label class="creation-project-title"><span>项目名称</span><input v-model.trim="form.project_name" type="text" maxlength="80" placeholder="输入项目名称" aria-label="项目名称" /></label><button @click="studioDrawer='我的预设'">预设 · {{selectedParameterPreset||'选择预设'}}⌄</button></div>
    <div class="creation-layout" :class="'kind-'+studioKind">
    <div class="creation-main">
    <template v-if="studioKind!=='subtitle'"><div class="create-copy-column">
            <div class="form-grid">
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
              <span>{{ form.skip_tts && form.skip_text_correction ? '口播文案（已选择无文案，可留空）' : '口播文案' }}</span>
              <textarea
                ref="workspaceScriptTextarea"
                v-model="form.script"
                rows="14"
                :disabled="form.skip_tts && form.skip_text_correction"
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
            </div></template>
    <template v-else><article class="panel module1-panel">
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
                  当前将使用语言模型校对。语言模型尚未配置时，请先到「接口与服务 → 语言模型」完成配置。
                </div>
                <div class="inline-actions module1-actions">
                  <button class="ghost-btn stop-btn" type="button" :disabled="!subtitleJobRunning" @click="cancelSubtitleJob">停止识别</button>
                  <button class="primary-btn" type="button" :disabled="submittingSubtitle || !canSubmitSubtitle" @click="launch">
                    {{ submittingSubtitle ? '正在提交...' : '开始识别字幕' }}
                  </button>
                </div>
              </div>
            </div>
          </article></template>
    </div>
    <aside class="creation-side" aria-label="制作设置">
      <div v-if="studioKind!=='subtitle'" class="creation-side-head"><b>制作设置</b><small>点击卡片修改对应设置</small></div>
<div class="setting-summaries creation-settings-top"><button v-if="studioKind!=='subtitle'" @click="studioDrawer='声音设置'"><span>◉</span><div><small>配音</small><b>{{ttsEngine==='cluster'?'集群 GPU · IndexTTS-2.5':ttsEngine==='qwen'?'Qwen TTS':'IndexTTS-2.5'}} · {{form.tts_emotion?emotionLabel(form.tts_emotion):'参考原音频'}}</b></div><span>›</span></button><button v-if="studioKind==='video'" @click="studioDrawer='作品风格'"><span>▧</span><div><small>作品风格</small><b>{{styleName||contentModeOptions.find(m=>m.key===form.content_mode)?.label||'自定义'}}{{styleDirty?' · 已修改':''}}</b></div><span>›</span></button><button v-if="studioKind==='video'" @click="studioDrawer='画面编排'"><span>⊞</span><div><small>画面编排 · {{form.video_orientation==='portrait'?'竖屏 9:16':'横屏 16:9'}}</small><b>{{visualPacingSummary}} · {{form.dynamic_video?(form.video_generation_backend==='cloud_h3'?'集群 H3':form.video_generation_backend==='comfyui'?'本地 ComfyUI'+(form.comfyui_h3_prompt_agent?' · H3 Agent':''):'视频 API')+' · '+dynamicTextModeLabel(form.dynamic_text_mode):form.director_strategy==='enhanced_beta'?'叙事增强':'稳健还原'}}</b></div><span>›</span></button><button v-if="studioKind==='video'" @click="studioDrawer='字幕样式'"><span>字</span><div><small>字幕样式</small><b>{{form.video_render_variant==='raw'?'仅无字幕版':form.video_render_variant==='subtitles'?'仅字幕版':'字幕版 + 无字幕版'}}</b></div><span>›</span></button></div>
    <LanguageModelPresets v-if="studioKind!=='subtitle'" :disabled="studioBusy" :cloud-pool="form.use_cloud_image_pool"/>
      <div class="creation-side-actions">
    <div v-if="studioKind==='video'" class="create-footer dynamic-create-footer">
      <label v-if="!form.dynamic_video"><input v-model="form.step_mode" type="checkbox"/>逐步确认<small>配音与画面完成后，由你确认再继续</small></label>
      <label v-else class="one-click-video-option"><input v-model="form.dynamic_auto_advance" type="checkbox"/><span><b>一键出视频</b><small>仍使用完整分步流程；阶段成功后由 OCV 自动确认并继续，异常时停下等待处理。</small></span></label>
      <button class="primary" :disabled="studioBusy||studioHasRunning||!canSubmitGeneration" @click="launch">{{studioBusy?'正在提交…':form._rerun_source_project?'重新运行 →':form.dynamic_video?(form.dynamic_auto_advance?'一键开始制作 →':'生成配音与字幕 →'):'开始生成 →'}}</button>
    </div>
    <div v-else-if="studioKind==='audio'" class="create-footer"><span class="muted">生成后可以逐句精修、断句与添加停顿。</span><button class="primary" :disabled="studioBusy||studioHasRunning||!canSubmitModule1" @click="launch">生成配音 →</button></div>
    <div v-else class="create-footer"><span class="muted">识别完成后，可以校对文字并导出字幕。</span><button class="primary" :disabled="studioBusy||studioHasRunning||!canSubmitSubtitle" @click="launch">开始识别 →</button></div>
    <p v-if="studioHasRunning" class="studio-notice">已有任务正在执行，可先保存草稿，待当前任务结束后开始。</p><p v-if="studioKind==='video'&&generationBlockReason" class="muted">{{generationBlockReason.message}}</p>
      </div>
    </aside>
    </div>
   </section>
   <section v-else class="editor studio-editor">
<div class="editor-heading"><div><h1>{{studioTitle}}</h1><span class="muted">{{typeLabel(studioKind)}} · {{statusLabel(studioJob?.status)}}</span></div><button v-if="studioTab!=='参数回顾'" @click="studioDrawer='我的预设'">我的预设⌄</button></div>
    <div v-if="!editingDynamicAudio" class="editor-tabs"><button v-for="t in studioTabs" :key="t" :class="{active:studioTab===t}" @click="chooseTab(t)">{{t}}</button><span class="tab-spacer"/><button @click="refreshEditorData">刷新资产 ↻</button></div>
    <div v-if="studioBusy" class="empty">正在读取项目…</div>
    <div v-if="['failed','cancelled','completed'].includes(studioJob?.status)" class="studio-notice" :class="{error:studioJob?.status==='failed'}"><span>{{studioJob.message}}</span><button v-if="['failed', 'cancelled'].includes(studioJob?.status)" :disabled="resumingGeneration" @click="resumeGeneration">{{ resumingGeneration ? '正在续跑…' : '断点续跑' }}</button></div>
    <ParameterReview v-if="studioTab==='参数回顾'" :request="studioJob?.request||{}" :reference-assets="studioReferenceAssets" :status="studioJob?.status" :busy="studioBusy" @duplicate="duplicateStudioProject" @reset="resetStudioProject"/>
    <template v-else-if="studioTab==='文案'"><div class="studio-original-script"><h2>本次任务文案</h2><p class="muted">原始文案用于核对。发音修正请在“配音”里操作，显示文字请在“画面与字幕”里修改。</p><textarea :value="studioJob?.request?.script||''" readonly rows="14"/></div></template>
    <template v-else-if="studioTab==='素材'"><p class="muted">当前任务已创建，替换素材请新建字幕识别任务。</p><button @click="newProject('subtitle')">新建字幕任务</button></template>
    <template v-else-if="studioTab==='配音'"><AudioWorkbench /></template>
    <template v-else-if="studioTab==='画面与字幕'"><VisualWorkbench /></template>
    <template v-else-if="studioTab==='字幕'">
     <audio v-if="studioAudio" :src="studioAudio" controls/>
     <p class="muted">校对后可导出新的 SRT，原始识别文件保留。</p>
     <div class="studio-subtitle-list"><div v-for="(row,i) in studioSubtitles" :key="i"><span>{{i+1}}</span><label>开始（秒）<input v-model.number="row.start" type="number" min="0" step="0.1"/></label><label>结束（秒）<input v-model.number="row.end" type="number" min="0" step="0.1"/></label><textarea v-model="row.text" rows="2"/></div></div><p v-if="!studioSubtitles.length" class="muted">{{studioSubtitleMessage||'识别完成后可在这里校对字幕。'}}</p><button v-if="studioSubtitles.length" class="primary" @click="exportStudioSubtitles">导出校对字幕</button><p>{{studioSubtitleMessage}}</p>
    </template>
    <template v-else-if="studioTab==='导出'">
<VideoBookends v-if="studioKind==='video'" :settings="visualPresentation" :disabled="visualEditor.task?.status==='running'" />
<OperationStatus title="合成状态" :task="visualEditor.task"/><div class="studio-export"><div><video v-if="studioKind==='video'&&studioVideo" controls :src="studioVideo" preload="metadata"/><audio v-else-if="studioAudio" controls :src="studioAudio"/><div v-else class="empty">生成完成后，可以在这里预览作品。</div><button v-if="studioJob?.status==='completed'" @click="openProjectOutputFolder(studioJob.id)">打开项目输出目录</button><small v-if="studioJob?.status==='completed'" class="muted">包含成片、文案、配音、图片、字幕、提示词和重绘历史。</small></div><div class="studio-export-controls"><h2>导出设置</h2><button v-if="studioKind==='video'" @click="studioDrawer='背景音乐'">背景音乐与音量 →</button><template v-if="studioKind==='video'"><div
                v-if="!(form.step_mode && isGuidedWorkflowJob(activeJob) && activeJob?.id === visualEditorProjectId && guidedStage !== 'completed')"
                class="visual-render-footer"
              >
                <label>渲染设置
                  <button type="button" @click="studioDrawer='导出字幕样式'">成片版本与字幕样式</button>
                </label>
                <button class="primary-btn" type="button" :disabled="visualEditor.task?.status === 'running' || visualEditor.has_active_image_tasks || ttsEditor.task?.status === 'running'" @click="renderEditedVideo">
                  重新渲染
                </button>
                <button class="ghost-btn stop-btn" type="button" :disabled="visualEditor.task?.status !== 'running' || visualEditor.task?.action !== 'render'" @click="cancelVisualRender">停止渲染</button>
              </div></template><button v-if="studioKind==='subtitle'&&studioSubtitles.length" class="primary" @click="exportStudioSubtitles">导出校对 SRT</button><a v-for="(url,key) in studioJob?.artifacts||{}" :key="key" :href="url" target="_blank" rel="noopener">{{artifactLabel(key)}} ↗</a></div></div>
     <template v-if="studioKind==='subtitle'"><article class="panel subtitle-style-panel">
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
          </article></template>
    </template>
    <section v-if="['video','dynamic'].includes(studioKind) && isGuidedWorkflowJob(studioJob) && studioJob?.status !== 'completed' && !['配音','画面与字幕'].includes(studioTab)" class="guided-workflow-shell" aria-label="分步制作流程">
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
              <div v-else-if="guidedStage === 'audio_review' && studioJob?.request?.dynamic_video" class="bridge-tools">
                <button class="primary-btn guided-primary-action" type="button" :disabled="studioBusy || guidedSubtitleSaving || ttsEditor.task?.status === 'running'" @click="enterDynamicStoryboard">确认配音与字幕，进入自动分镜 →</button>
                <button v-if="codexBridgeAvailable" type="button" :disabled="studioBusy || guidedSubtitleSaving || ttsEditor.task?.status === 'running'" @click="handoffToCodex">确认配音，交给 AI 规划</button>
              </div>
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
<div class="task-strip"><button v-if="studioTab!=='画面与字幕'||!visualEditor.items.length" @click="studioLogsOpen=!studioLogsOpen">{{studioLogsOpen?'⌄':'›'}} 任务日志</button><span class="muted">{{studioJob?.message}} · {{studioJob?.progress||0}}%</span><span class="tab-spacer"/><button v-if="['running','queued'].includes(studioJob?.status)" @click="studioKind==='audio'?cancelModule1():studioKind==='subtitle'?cancelSubtitleJob():cancelGeneration()">停止任务</button><button @click="openLogs()">查看完整日志 ↗</button></div><TaskConsole v-if="studioLogsOpen&&(studioTab!=='画面与字幕'||!visualEditor.items.length)" :lines="studioTaskLogs" compact :diagnostic-available="!!studioJob" :diagnostic-exporting="diagnosticExporting" @export-diagnostic="exportDiagnosticPackage(studioJob)"/><p v-if="studioLogsOpen && diagnosticMessage&&(studioTab!=='画面与字幕'||!visualEditor.items.length)" class="muted" role="status">{{diagnosticMessage}}</p>
   </section>
<footer class="legal-footer" aria-label="开源许可证与官方源码">
        <strong class="official-disclaimer">
          官方声明：OCV 目前未授权任何培训机构、付费课程或软件售卖方；第三方收费服务不代表 OCV 官方授权，其交付与售后由第三方自行承担。
        </strong>
        <span>{{ health.release?.label || 'OCV · 版本读取中' }} · Copyright © 2026 周若雨、何允</span>
        <span>本程序不附带任何担保</span>
        <a href="https://github.com/IFRIT-Zhou/One-Click-VidGen" target="_blank" rel="noopener noreferrer">获取对应源代码</a>
        <a href="https://github.com/IFRIT-Zhou/One-Click-VidGen/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">AGPL-3.0-only</a>
        <a href="https://github.com/IFRIT-Zhou/One-Click-VidGen/blob/main/ADDITIONAL_TERMS.md" target="_blank" rel="noopener noreferrer">附加条款</a>
        <a href="https://github.com/IFRIT-Zhou/One-Click-VidGen/blob/main/TRADEMARKS.md" target="_blank" rel="noopener noreferrer">品牌规则</a>
      </footer>
  </main>
  <ApiSetupGuide v-if="apiGuideOpen" @close="apiGuideOpen=false"/>
  <div v-if="studioDrawer" class="drawer-backdrop" :class="{'is-beside':drawerRight>0}" :style="drawerRight>0?{'--drawer-right':drawerRight+'px'}:null" @click.self="studioDrawer=''"><aside class="drawer" :class="{'is-wide':drawerWide}" role="dialog" :aria-label="studioDrawer"><header><div><p class="eyebrow">WORKSPACE SETTINGS</p><h2>{{studioDrawer}}</h2><nav v-if="drawerSections.length>1" class="drawer-sections" aria-label="跳到分节"><button v-for="section in drawerSections" :key="section.index" type="button" @click="jumpDrawerSection(section.index)">{{section.label}}</button></nav></div><button aria-label="关闭设置" @click="studioDrawer=''">×</button></header><div ref="drawerBody" class="drawer-body">
   <template v-if="studioDrawer==='重配参数'"><div class="tts-refine-parameter-panel" :class="{ locked: ttsEditor.task?.status === 'running' }">
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
                  </div></template>
   <template v-else-if="studioDrawer==='声音设置'"><div class="drawer-cols"><div class="drawer-col" data-drawer-section="配音参数"><div
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
                      <select v-model="ttsEngine" @change="handleTtsEngineChanged">
                        <option value="indextts25">本地 GPU · IndexTTS-2.5</option>
                        <option value="cluster">集群 GPU · IndexTTS-2.5</option>
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
                <div>
                  <strong>本地配音需要 NVIDIA 显卡</strong>
                  <span>建议至少 8GB 显存，并保持并行数 1；6GB 及以下显存建议改用集群 GPU 或 Qwen-TTS。</span>
                </div>
                <button v-if="!health.tts25_online" class="ghost-btn compact-btn" type="button" @click="openLocalTtsInstaller">
                  安装本地语音模型
                </button>
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
                  <button type="button" :disabled="deletingApiKey" @click="deleteConfiguredApiKey('qwen_tts', 0)">删除密钥</button>
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
            </div><div class="drawer-col">
            <section class="bgm-panel bgm-full-row creation-bgm-panel" data-drawer-section="背景音乐" :class="{ expanded: form.bgm_enabled }">
              <div class="bgm-panel-head">
                <div>
                  <div class="sidebar-label">背景音乐（可选）</div>
                  <strong>为最终成片添加 BGM</strong>
                  <small class="muted">不影响配音、字幕或画面生成；最终渲染时按列表顺序循环播放。</small>
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
                    <input type="file" accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,audio/*" :disabled="bgmUploading" @change="uploadBgmTrack" />
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
                    <input v-model.number="form.bgm_fade_duration" type="number" min="0.1" max="30" step="0.1" :disabled="!form.bgm_fade_enabled" />
                  </label>
                  <small class="muted">默认 1 秒；关闭后音乐会按顺序直接衔接。</small>
                </div>
              </div>
            </section>
            <details class="drawer-optional" data-drawer-section="长文处理" :open="form.auto_split_long_text"><summary>长文处理 <small>{{ form.auto_split_long_text ? '已开启自动分段' : '未开启 · 可选' }}</small></summary>
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
            </div></details></div></div></template>
   <template v-else-if="studioDrawer==='参数回顾'"><ParameterReview :request="studioJob?.request||{}" :reference-assets="studioReferenceAssets" :status="studioJob?.status" :busy="studioBusy" @duplicate="duplicateStudioProject" @reset="resetStudioProject"/></template>
   <template v-else-if="studioDrawer==='作品风格'">
    <div class="style-library">
     <p class="muted">风格只改变内容与画面表现，不修改配音、节奏、导演策略、API 或出图分辨率。当前任务的修改不会自动覆盖风格库。</p>
     <div class="drawer-cols"><div class="drawer-col" data-drawer-section="选择风格">
     <h3>内置示范</h3>
     <div class="style-example-list"><button v-for="mode in contentModeOptions" :key="mode.key" @click="useExample(mode.key)">{{mode.label}}<small>{{mode.key==='general'?'自由创作起点':'以此为基础创作'}}</small></button></div>
     <h3>我的风格 <small>{{styles.length}}</small></h3>
     <p v-if="!styles.length" class="muted">还没有保存的风格。在下方调整画风，再为它取一个名字。</p>
     <div class="style-saved-list"><div v-for="style in styles" :key="style.id" class="style-saved-row"><button @click="applyStyle(style)"><b>{{style.name}}</b><small>{{contentModeOptions.find(m=>m.key===style.values.content_mode)?.label}} · {{Object.hasOwn(style.values,'global_character_prompt')?'含人物':'不含人物'}} · {{Object.hasOwn(style.values,'story_environment_prompt')?'含环境':'不含环境'}}</small></button><button :aria-label="'删除风格 '+style.name" @click="deleteStyle(style)">×</button></div></div>
     </div><div class="drawer-col" data-drawer-section="当前画面设置">
     <h3>保存当前画面设置</h3>
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
              <ReferenceMaterials :form="form" :assets="editorAssets" :names="referenceImageNames" :uploading="protagonistReferenceUploading" :error="protagonistReferenceImageError" :auto-analyze="form.auto_analyze_reference_images" @upload="uploadReferenceImages" @remove="removeReferenceImage" />
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
            </div><details><summary>高级导演提示词</summary><section class="agent-prompt-full-row advanced-agent-console">
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
            </section></details>
     <label class="stack"><span>风格名称（修改名称后可更新，或另存为新风格）</span><input v-model="styleName" maxlength="60" placeholder="例如：温暖水彩纪实" /></label>
     <label class="style-check"><input type="checkbox" v-model="styleCharacters" /> 同时保存人物设定</label>
     <label class="style-check"><input type="checkbox" v-model="styleEnvironment" /> 同时保存故事环境</label>
     <p class="muted">默认只保存画风和内容表现方式。系列作品可勾选人物、环境；参考图片不随风格保存，需在任务中单独上传。未勾选的项目在套用时保持当前任务设置。</p>
     <div class="style-save-actions"><button class="primary" @click="saveStyle(false)">保存为新风格</button><button :disabled="!styleId" @click="saveStyle(true)">更新所选风格</button></div>
     <p role="status" class="muted">{{styleMessage}}</p>
     <p class="muted">风格库保存在当前浏览器本机，不会上传到云端。</p>
     </div></div>
    </div>
   </template>
   <template v-else-if="studioDrawer==='画面编排'"><p class="muted">决定画面如何表达文案、多久切换。与作品风格独立保存；完整配置可使用右上角参数预设。</p><div class="drawer-cols"><div class="drawer-col" data-drawer-section="出图与生成方式"><ImageProfileSelector :form="form" :authenticated="cloudSession.authenticated" /><ComfyUIVideoSelector v-if="form.dynamic_video" :form="form" @open-workbench="studioDrawer='';studioPage='comfyui'" /></div><div class="drawer-col" data-drawer-section="画面表达与节奏"><DynamicTextModeSelector v-if="form.dynamic_video" v-model="form.dynamic_text_mode" /><div v-else class="director-strategy-row">
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
            <div class="tts-parameter-panel" :style="{ opacity: form.dynamic_video || form.director_strategy === 'enhanced_beta' ? 1 : 0.55 }">
              <label class="checkbox-row" :class="{ 'dynamic-scene-toggle': form.dynamic_video }">
                <input type="checkbox" v-model="form.scene_references_enabled" :disabled="!form.dynamic_video && form.director_strategy !== 'enhanced_beta'" />
                <span>启用场景参考</span>
              </label>
              <small class="muted">{{ form.dynamic_video ? '文字辅助与画面优先均可使用。为重复出现的场景生成场景参考图' : '仅叙事增强可用。为重复的真实场景生成无人参考图' }}，仅用于相关镜头；每个场景额外生成一张图片并计费。关闭后不生成场景参考图。</small>
            </div>
            <div class="tts-parameter-panel visual-pacing-standalone">
              <div class="visual-pacing-panel">
                <div class="visual-pacing-copy">
                  <div class="sidebar-label">画面节奏</div>
                  <strong>{{ visualPacingSummary }}</strong>
                  <small class="muted">根据字幕时间戳分组；Agent 的快节奏建议不会突破最低停留时长。</small>
                </div>
                <label class="visual-pacing-select" style="grid-column:1 / -1">
                  <span>节奏预设</span>
                  <select v-model="form.visual_pacing_preset" @change="rememberVisualPacing">
                    <option value="auto">按作品风格自动</option>
                    <option value="slow">舒缓</option>
                    <option value="standard">标准</option>
                    <option value="fast">紧凑</option>
                    <option value="custom">自定义</option>
                  </select>
                </label>
                <ShotPlanningLimit v-if="form.dynamic_video" v-model:max-duration="form.dynamic_max_shot_duration" v-model:recommended="form.dynamic_cluster_duration_recommended" :backend="form.video_generation_backend" />
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
            <div class="tts-parameter-panel render-subtitle-panel">
              <div class="tts-parameter-head">
                <div>
                  <div class="sidebar-label">画布</div>
                  <h3>渲染比例</h3>
                  <small class="muted">竖屏使用 9:16，横屏使用 16:9。动态项目含已完成的 2K 集群 H3 片段时，导出保持 2K；其他项目沿用 1080P 画布。</small>
                </div>
                <label class="visual-pacing-select render-variant-select">
                  <span>视频比例</span>
                  <select v-model="form.video_orientation">
                    <option value="landscape">横屏 16:9</option>
                    <option value="portrait">竖屏 9:16</option>
                  </select>
                </label>
              </div>
            </div>
            </div></div></div><details class="drawer-optional" data-drawer-section="片头与片尾" :open="!!(form.intro_video_id||form.outro_video_id)"><summary>片头与片尾 <small>可选 · {{ form.intro_video_id||form.outro_video_id ? '已设置' : '未设置' }}</small></summary><VideoBookends :settings="form" /></details></template>
   <template v-else-if="studioDrawer==='字幕样式'"><SubtitleStyleEditor split :settings="form" :dynamic="form.dynamic_video" v-model:variant="form.video_render_variant" /></template>
   <template v-else-if="studioDrawer==='导出字幕样式'"><SubtitleStyleEditor split :settings="visualPresentation" v-model:variant="visualRenderMode" orientation-control :readonly="visualEditor.task?.status==='running'" /></template>
   <template v-else-if="studioDrawer==='背景音乐' && !(isGuidedWorkflowJob(studioJob) && guidedStage === 'render_setup')"><section class="bgm-panel visual-editor-bgm" :class="{ expanded: visualBgm.enabled }">
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
              </section></template>
   <template v-else-if="studioDrawer==='背景音乐'"><section class="bgm-panel bgm-full-row" :class="{ expanded: form.bgm_enabled }">
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
            </section></template>
   <template v-else-if="studioDrawer==='我的预设'"><p class="muted">载入常用设置；项目里的临时修改不会自动覆盖预设。</p><select v-model="selectedParameterPreset"><option value="">选择预设</option><option v-for="preset in parameterPresets" :key="preset.name" :value="preset.name">{{preset.name}}</option></select><button :disabled="!selectedParameterPreset||loadingParameterPresets" @click="loadSelectedParameterPreset">套用预设</button><button :disabled="savingParameterPreset" @click="saveCurrentParameterPreset">保存当前配置为预设</button><button :disabled="!selectedParameterPreset||deletingParameterPreset" @click="deleteSelectedParameterPreset">删除所选预设</button><p class="muted">{{parameterPresetMessage}}</p></template>
  </div><footer><span class="muted">{{studioDrawer==='参数回顾'?'只读查看，不会修改任务':studioPage==='new'?studioSaveState+' · '+(form._rerun_source_project?'重新运行后应用到项目':'开始制作时使用此配置'):'需要保存的修改，请使用对应保存按钮；关闭不会触发生成'}}</span><button class="primary" @click="studioDrawer=''">关闭设置</button></footer></aside></div>
  <div v-if="cloudLoginNotice" class="studio-notice error" role="alert">{{ cloudLoginNotice }} <button type="button" @click="openCloudLogin">重新登录</button></div>
      <div v-if="cloudLoginOpen" class="cloud-auth-overlay" @click.self="cloudLoginOpen = false">
        <section class="cloud-auth-dialog" role="dialog" aria-modal="true" aria-labelledby="cloud-auth-title">
          <div class="cloud-auth-dialog-head">
            <div>
              <span class="cluster-card-kicker">ONE-CLICK VIDGEN CLOUD</span>
              <h2 id="cloud-auth-title">{{ cloudRecoveryOpen ? '忘记密码' : cloudSession.authenticated ? '云端账户' : '登录云端服务' }}</h2>
            </div>
            <button class="cloud-auth-close" type="button" aria-label="关闭登录窗口" @click="cloudLoginOpen = false">×</button>
          </div>

          <CloudPasswordRecovery v-if="cloudRecoveryOpen" :initial-email="cloudLoginForm.email" @back="closeCloudRecovery" @reset="onCloudPasswordReset" />
          <div v-else-if="!cloudSession.configured" class="cluster-notice warning">
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
            <button class="ghost-btn cloud-auth-recovery" type="button" @click="openCloudRecovery">忘记密码？通过邮箱找回</button>
            <button class="primary-btn cloud-auth-submit" type="submit" :disabled="cloudBusy">
              {{ cloudBusy ? '正在登录…' : '登录' }}
            </button>
            <button class="ghost-btn" type="button" :disabled="cloudBusy" @click="registerCloud">注册新账户</button>
          </form>
          <div v-if="cloudError" class="board-error cloud-auth-feedback">{{ cloudError }}</div>
          <div v-else-if="cloudMessage" class="api-key-message cloud-auth-feedback">{{ cloudMessage }}</div>
          <p class="cloud-auth-security">登录凭据由本机后端与云端服务安全交换，浏览器不会保存集群访问令牌。</p>
        </section>
      </div><div v-if="cloudRechargeOpen" class="cloud-recharge-overlay" role="dialog" aria-modal="true" aria-labelledby="cloud-recharge-title" @click.self="closeCloudRecharge">
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
    </div><div
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
    </div><div v-if="localTtsInstallerOpen" class="local-tts-install-overlay" role="dialog" aria-modal="true" aria-labelledby="local-tts-install-title" @click.self="closeLocalTtsInstaller">
      <section class="local-tts-install-dialog">
        <header>
          <div>
            <div class="eyebrow">可选本地组件</div>
            <h2 id="local-tts-install-title">安装 IndexTTS-2.5 语音模型</h2>
          </div>
          <button class="ghost-btn compact-btn" type="button" @click="closeLocalTtsInstaller">关闭</button>
        </header>
        <ModelInstallGuide kind="tts" />
        <div v-if="localTtsComponent.ready" class="local-tts-install-result success">
          <strong>本地语音模型已就绪</strong>
          <p>现在可以正常使用本地 GPU 配音，功能与完整整合包一致。</p>
        </div>
        <template v-else>
          <div class="local-tts-install-copy">
            <strong>{{ localTtsComponent.message }}</strong>
            <p>轻便包只省略约 {{ localTtsEstimatedGb }} GB 的模型权重。PyTorch、字幕、图像与渲染环境均已保留；不安装也不会影响集群 GPU、Qwen-TTS 或已有配音模式。</p>
          </div>
          <div v-if="localTtsComponent.installing || localTtsComponent.downloaded_bytes" class="local-tts-progress-block">
            <div><span>{{ localTtsComponent.installing ? '正在后台下载与校验' : '已发现部分模型，可继续安装' }}</span><strong>{{ localTtsDownloadedGb }} / 约 {{ localTtsEstimatedGb }} GB</strong></div>
            <div class="local-tts-progress"><i :style="{ width: `${localTtsInstallProgress}%` }"></i></div>
            <small>可以关闭此窗口继续使用其他功能；OCV 会保留已下载内容并支持继续安装。</small>
          </div>
          <p v-if="localTtsInstallError" class="local-tts-install-error">{{ localTtsInstallError }}</p>
          <p v-if="localTtsComponent.runtime_missing?.length" class="local-tts-install-error">基础运行文件不完整，请重新下载 OCV 轻便包或完整包。</p>
        </template>
        <footer>
          <button class="ghost-btn" type="button" @click="switchToClusterTts">改用集群 GPU</button>
          <button
            class="primary-btn"
            type="button"
            :disabled="localTtsInstallBusy || localTtsComponent.installing || localTtsComponent.ready || localTtsComponent.runtime_missing?.length"
            @click="startLocalTtsInstall"
          >
            {{ localTtsComponent.installing ? '正在安装…' : (localTtsComponent.downloaded_bytes ? '继续安装' : '下载并安装') }}
          </button>
        </footer>
      </section>
    </div><div v-if="preflightOpen" class="preflight-overlay" role="dialog" aria-modal="true" aria-labelledby="preflight-title">
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
    </div><div v-if="visualPreviewItem" class="visual-preview-modal" role="dialog" aria-modal="true" @click.self="visualPreviewItem = null">
          <div class="visual-preview-content">
            <div class="visual-preview-head">
              <strong>{{ visualPreviewItem.id }}</strong>
              <button class="icon-action" type="button" title="关闭预览" @click="visualPreviewItem = null">×</button>
            </div>
            <img :src="visualPreviewItem.image_url" :alt="visualPreviewItem.id" />
          </div>
        </div>
 </div>
  <TtsDraftResolutionDialog v-if="ttsDraftResolution.open" :items="ttsDraftResolution.items" @close="closeTtsDraftResolution" @resolve="resolveTtsDrafts" />
</template>
<script>
import Announcements from './components/Announcements.vue'
import IcanChannelHint from './components/IcanChannelHint.vue'
import TtsStructureControls from './components/TtsStructureControls.vue'
import TtsEditorFeedback from './components/TtsEditorFeedback.vue'
import TtsSentenceControls from './components/TtsSentenceControls.vue'
import TtsRebuildButton from './components/TtsRebuildButton.vue'
import TtsFindReplace from './components/TtsFindReplace.vue'
import TtsDraftResolutionDialog from './components/TtsDraftResolutionDialog.vue'
import CloudPasswordRecovery from './components/CloudPasswordRecovery.vue'
import CloudImageQuality from './components/CloudImageQuality.vue'
import { useWorkspace } from './useWorkspace'
import { useStudio } from './useStudio'
import CodexBridgePanel from './components/CodexBridgePanel.vue'
import ModelInstallGuide from './components/ModelInstallGuide.vue'
import { useStyleLibrary } from './useStyleLibrary'
import { useAppearance } from './useAppearance'
import TaskConsole from './components/TaskConsole.vue'
import OperationStatus from './components/OperationStatus.vue'
import ApiSetupGuide from './components/ApiSetupGuide.vue'
import LanguageModelPresets from './components/LanguageModelPresets.vue'
import {ref,reactive,computed,watch,nextTick,onMounted,onUnmounted,provide} from 'vue'
import AudioWorkbench from './components/workbench/AudioWorkbench.vue'
import VisualWorkbench from './components/workbench/VisualWorkbench.vue'
import LanguageServicePanel from './components/services/LanguageServicePanel.vue'
import ImageServicePanel from './components/services/ImageServicePanel.vue'
import VideoServicePanel from './components/services/VideoServicePanel.vue'
import ImageStudio from './components/ImageStudio.vue'
import ComfyUIWorkbench from './components/ComfyUIWorkbench.vue'
import ComfyUIVideoSelector from './components/ComfyUIVideoSelector.vue'
import VideoStudio from './components/VideoStudio.vue'
import VibeModelSettings from './components/VibeModelSettings.vue'
import ImageProfileSelector from './components/ImageProfileSelector.vue'
import TtsBoundaryEditor from './components/TtsBoundaryEditor.vue'
import SubtitleStyleEditor from './components/SubtitleStyleEditor.vue'
import VideoBookends from './components/VideoBookends.vue'
import { visualPresentation } from './videoPresentation'
import ParameterReview from './components/ParameterReview.vue'
import ShotPlanningLimit from './components/ShotPlanningLimit.vue'
import SceneAssets from './components/SceneAssets.vue'
import ReferenceMaterials from './components/ReferenceMaterials.vue'
import DynamicTextModeSelector from './components/DynamicTextModeSelector.vue'
import ResizableShotWorkspace from './components/ResizableShotWorkspace.vue'
import WorkspacePanels from './components/WorkspacePanels.vue'
import { dynamicTextModeLabel } from './dynamicTextMode'
export default { components: { AudioWorkbench, VisualWorkbench, VideoBookends, Announcements, IcanChannelHint, TtsFindReplace, TtsStructureControls, ShotPlanningLimit, TtsEditorFeedback, TtsSentenceControls, TtsRebuildButton, TtsDraftResolutionDialog, TtsBoundaryEditor, CloudPasswordRecovery, CloudImageQuality, ModelInstallGuide, CodexBridgePanel, LanguageModelPresets, ApiSetupGuide, OperationStatus, ResizableShotWorkspace, WorkspacePanels, DynamicTextModeSelector, ReferenceMaterials, SceneAssets, TaskConsole, ParameterReview, ImageStudio, ComfyUIWorkbench, ComfyUIVideoSelector, VideoStudio, VibeModelSettings, ImageProfileSelector, SubtitleStyleEditor, LanguageServicePanel, ImageServicePanel, VideoServicePanel }, setup() { const workspace = useWorkspace(); const studio = useStudio(workspace); const bag = { serviceTab:ref('overview'),apiGuideOpen:ref(false), ...workspace, ...studio, ...useServicesPage(workspace, studio), ...useWideDrawer(studio), formatJobTime, ...useStyleLibrary(workspace), ...useAppearance(), visualPresentation, dynamicTextModeLabel }; provide('ocv', reactive(bag)); return bag } }

// 任务时间：列表里显示相对时间，悬停显示完整时间。后端时间戳为秒。
function formatJobTime(seconds, full = false) {
  const value = Number(seconds)
  if (!value) return full ? '' : '—'
  const date = new Date(value * 1000), pad = (n) => String(n).padStart(2, '0')
  const absolute = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
  if (full) return '更新于 ' + absolute
  const diff = Math.max(0, Date.now() / 1000 - value)
  if (diff < 60) return '刚刚'
  if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前'
  if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前'
  if (diff < 7 * 86400) return Math.floor(diff / 86400) + ' 天前'
  return absolute.slice(5)
}

// 设置抽屉：宽屏时变成宽面板；在创作页从「制作设置」栏左侧展开，不遮住设置卡和开始按钮。
// 分节导航来自抽屉内容里的 data-drawer-section 标记。
function useWideDrawer(studio) {
  const COMPACT = ['我的预设']
  const drawerRight = ref(0), drawerSections = ref([]), drawerBody = ref(null)
  const drawerWide = computed(() => !!studio.studioDrawer.value && !COMPACT.includes(studio.studioDrawer.value))
  function measureDrawer() {
    const side = studio.studioDrawer.value && studio.studioPage.value === 'new' && window.innerWidth >= 1200 ? document.querySelector('.creation-side') : null
    const rect = side?.getBoundingClientRect()
    drawerRight.value = rect && rect.width ? Math.max(0, Math.round(window.innerWidth - rect.left + 12)) : 0
  }
  function collectSections() {
    const nodes = drawerBody.value ? [...drawerBody.value.querySelectorAll('[data-drawer-section]')] : []
    drawerSections.value = nodes.map((node, index) => ({ index, label: node.dataset.drawerSection }))
  }
  function jumpDrawerSection(index) {
    const node = drawerBody.value?.querySelectorAll('[data-drawer-section]')[index]
    if (!node) return
    if (node.tagName === 'DETAILS') node.open = true
    node.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
  }
  watch(() => [studio.studioDrawer.value, studio.studioPage.value, studio.studioKind.value], async () => { await nextTick(); measureDrawer(); collectSections() })
  onMounted(() => window.addEventListener('resize', measureDrawer))
  onUnmounted(() => window.removeEventListener('resize', measureDrawer))
  return { drawerRight, drawerSections, drawerBody, drawerWide, jumpDrawerSection }
}

// 接口与服务页：分类标签（tablist）、各分类状态标记、语言账号显式保存与离开前提醒。
function useServicesPage(workspace, studio) {
  const serviceTabs = [{id:'overview',label:'服务概览'},{id:'language',label:'语言模型',panel:'service-language'},{id:'images',label:'图像接口',panel:'service-images'},{id:'video',label:'视频接口',panel:'service-video'},{id:'vibe',label:'Vibe 动效',panel:'service-vibe'},{id:'tts',label:'TTS 状态',panel:'service-tts'}]
  const serviceState = reactive({ language: null, images: null, video: null, vibe: null, tts: null })
  const TTS_READY = ['IndexTTS-2.5 在线', '集群在线', 'Qwen 已配置']
  const ttsReady = computed(() => TTS_READY.includes(workspace.ttsStatusText.value))
  watch([ttsReady, workspace.startingTts], () => { serviceState.tts = workspace.startingTts.value ? null : { level: ttsReady.value ? 'ready' : 'missing' } }, { immediate: true })
  function serviceMark(id) {
    const state = serviceState[id]
    if (!state) return null
    if (state.dirty) return { text: '未保存', cls: 'dirty', title: '有未保存修改' }
    if (state.level === 'error') return { text: '▲', cls: 'error', title: '有错误' }
    if (state.level === 'missing') return { text: '○', cls: 'missing', title: id === 'tts' ? '未连接' : '未配置完整' }
    if (state.level === 'ready') return { text: '●', cls: 'ready', title: '已配置' }
    return null
  }
  function onServiceTabKey(event) {
    const keys = { ArrowRight: 1, ArrowLeft: -1, Home: 'first', End: 'last' }
    if (!(event.key in keys) || event.target?.getAttribute('role') !== 'tab') return
    event.preventDefault()
    const tabs = [...event.currentTarget.querySelectorAll('[role="tab"]')]
    const index = tabs.indexOf(event.target)
    const step = keys[event.key]
    const next = step === 'first' ? 0 : step === 'last' ? tabs.length - 1 : (index + step + tabs.length) % tabs.length
    tabs[next].focus(); tabs[next].click()
  }
  function setApiKeyStatus(keys) { if (keys) workspace.apiKeyStatus.value = keys }
  // Shared saveApiKeySettings reports through apiKeyMessage and replaces
  // apiKeyStatus only on success; translate that into an explicit result.
  async function saveLanguageAccount() {
    const before = workspace.apiKeyStatus.value
    await workspace.saveApiKeySettings()
    return { ok: workspace.apiKeyStatus.value !== before, message: workspace.apiKeyMessage.value }
  }
  // 出图并发 keeps the shared saveImageConcurrencySettings payload; the panel
  // edits a draft and only writes it into apiKeyForm at save time.
  async function saveImageConcurrency(draft) {
    const f = workspace.apiKeyForm, keys = ['image_concurrency_mode', 'image_per_key_concurrency', 'image_total_concurrency']
    const before = Object.fromEntries(keys.map((k) => [k, f[k]]))
    Object.assign(f, { image_concurrency_mode: draft.mode, image_per_key_concurrency: draft.per_key, image_total_concurrency: draft.total })
    const status = workspace.apiKeyStatus.value
    await workspace.saveImageConcurrencySettings()
    const ok = workspace.apiKeyStatus.value !== status
    if (!ok) Object.assign(f, before)
    return { ok, message: workspace.apiKeyMessage.value }
  }
  function closeServicesChecked() {
    if (Object.values(serviceState).some((state) => state?.dirty) && !window.confirm('接口配置有未保存的修改，返回后草稿会丢失。仍要返回工作页面？')) return
    studio.closeServices()
  }
  return { serviceTabs, serviceState, serviceMark, onServiceTabKey, setApiKeyStatus, saveLanguageAccount, saveImageConcurrency, ttsReady, closeServicesChecked }
}
</script>
<style scoped>
.live-studio .studio-sentence-list>div.narration-current{background:rgba(70,180,220,.17);box-shadow:inset 4px 0 #66dcff}.narration-marker{display:block;color:#66dcff;font-size:10px;white-space:nowrap;margin-top:4px}.studio-sentence-list{scroll-behavior:auto}
.service-header-actions{display:flex;gap:10px;flex-wrap:wrap}.services-page .services-nav{border-bottom:1px solid var(--border,#35423f);gap:6px;padding-bottom:14px}.services-nav button{padding:10px 18px}.services-nav button.active{background:var(--accent,#81d9bd);color:#132720;border-color:var(--accent,#81d9bd)}.live-studio .services-page .api-key-card{display:flex;flex-direction:column;gap:18px}.services-page .api-key-card>*{width:100%;min-width:0}.services-page .api-key-card>.sidebar-label,.services-page .service-config-card>.sidebar-label{font-size:16px;color:var(--text,#eef3f1)}.services-page .service-config-card{display:grid;gap:12px}.services-page .service-config-card>.muted{margin-bottom:6px}.services-nav button:focus-visible{outline:2px solid var(--accent,#81d9bd);outline-offset:2px}.service-tab-mark{margin-left:7px;font-size:10px;vertical-align:1px}.service-tab-mark.ready{color:var(--accent,#81d9bd)}.service-tab-mark.missing{color:var(--warning,#d6bc87)}.service-tab-mark.error{color:var(--danger,#ed9292)}.service-tab-mark.dirty{font-size:11px;padding:1px 6px;border-radius:99px;border:1px solid currentColor;color:var(--warning,#d6bc87);vertical-align:1px}.services-nav button.active .service-tab-mark{color:inherit}.service-sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.services-page :deep(.video-model-settings),.services-page :deep(.image-profile-selector){margin:0}
.services-page{max-width:2400px;margin:0 auto;width:100%}
/* 插件与设置：宽屏两栏，外观单独一栏 */
@media(min-width:1600px){.live-studio .content.plugins-content{max-width:2400px}.plugins-page{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));grid-template-areas:"hero hero" "pref look" "plug look";gap:20px 24px;align-items:start}.plugins-page>*{margin:0!important;min-width:0}.plugins-page>.plugins-hero{grid-area:hero}.plugins-page>.creation-preference-panel{grid-area:pref}.plugins-page>.appearance-panel{grid-area:look}.plugins-page>.plugin-manager-panel{grid-area:plug}}

/* 项目首页 / 任务日志 */
.home-layout,.logs-layout{display:flex;flex-direction:column}.home-main,.logs-main{min-width:0}.home-main{container-type:inline-size}.home-side{display:contents}.home-quick{display:none}.home-side>.studio-drafts{order:-1}.home-side>:last-child{order:-2}.logs-list{display:none}
.project-table .col-wide{display:none}.project-actions{display:flex;align-items:center;gap:6px;justify-content:flex-end}.project-progress{display:flex;align-items:center;gap:10px;min-width:0}.project-progress-bar{display:block;flex:1;max-width:120px;height:4px;border-radius:99px;background:var(--line,#ffffff12);overflow:hidden}.project-progress-bar i{display:block;height:100%;background:var(--accent,#83dec5);border-radius:inherit}.status.is-failed{color:var(--danger,#ed9292)}.status.is-running,.status.is-queued{color:var(--accent,#83dec5)}.status.is-completed{color:var(--muted,#a0aaa8)}
@media(min-width:1200px){.live-studio .content.home,.live-studio .content.logs-page{max-width:2400px}
.home-layout{display:grid;grid-template-columns:minmax(0,1fr) clamp(320px,24vw,440px);gap:28px;align-items:start}.home-side{display:grid;gap:18px;position:sticky;top:16px;align-content:start}.home-side>.studio-drafts,.home-side>:last-child{order:0;margin:0}.home-quick{display:grid;gap:12px;padding:16px 18px;border:1px solid var(--border,#35423f);border-radius:12px}.home-quick h2{font-size:15px;margin:0}.home-quick-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.home-quick-grid button{display:grid;grid-template-columns:auto 1fr;grid-template-rows:auto auto;column-gap:10px;row-gap:2px;align-items:center;text-align:left;white-space:normal;padding:12px}.home-quick-grid button>span{grid-row:1/3;font-size:18px;color:var(--accent,#83dec5)}.home-quick-grid button small{font-size:11px;color:var(--muted,#a0aaa8)}
.logs-layout{display:grid;grid-template-columns:clamp(280px,22vw,400px) minmax(0,1fr);gap:24px;align-items:start}.logs-list{display:grid;gap:12px;position:sticky;top:16px;align-content:start}.logs-list-head h2{font-size:15px;margin:0}.logs-list-head small{color:var(--muted,#a0aaa8);margin-left:6px}.logs-jobs{display:grid;gap:6px}.logs-job{display:grid;gap:5px;text-align:left;white-space:normal;padding:11px 13px;border:1px solid var(--border,#35423f);border-radius:10px;background:transparent}.logs-job b{font-weight:600;font-size:13px;overflow-wrap:anywhere}.logs-job small{font-size:12px;color:var(--muted,#a0aaa8)}.logs-job .project-progress-bar{max-width:none}.logs-job.active{border-color:var(--accent,#83dec5);background:color-mix(in srgb,var(--accent,#83dec5) 8%,transparent)}.logs-job:focus-visible{outline:2px solid var(--accent,#83dec5);outline-offset:2px}.logs-list .studio-pagination{margin:0}.logs-main .log-toolbar label{display:none}.logs-main :deep(.console-output){height:max(360px,calc(100vh - 540px))}}
/* Extra project columns only when the table itself is wide enough. */
@container (min-width:900px){.project-table .col-wide{display:block}.project-table .project-type-inline{display:none}.project-table .table-head,.project-table .project-row{grid-template-columns:minmax(260px,1fr) 100px 96px 190px 110px 150px}.project-type,.project-updated{font-size:12px;color:var(--muted,#a0aaa8)}.project-quick{font-size:12px;padding:6px 12px}}

/* 设置抽屉 */
.drawer-sections{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.drawer-sections button{padding:4px 11px;font-size:12px;border-radius:99px}.drawer-sections button:focus-visible{outline:2px solid var(--accent,#83dec5);outline-offset:2px}
.drawer-cols,.drawer-col{display:grid;gap:18px;min-width:0;align-content:start}.drawer-col:empty{display:none}.drawer-col>*{margin-top:0;margin-bottom:0}
.drawer-optional{border:1px solid var(--border,#35423f);border-radius:10px;padding:12px 14px;min-width:0}.drawer-optional>summary{cursor:pointer;display:flex;gap:10px;align-items:baseline;font-weight:600;font-size:14px}.drawer-optional>summary small{font-weight:400;font-size:12px;color:var(--muted,#a0aaa8)}.drawer-optional[open]>summary{margin-bottom:14px}.drawer-optional>summary:focus-visible{outline:2px solid var(--accent,#83dec5);outline-offset:3px}
.drawer-body{container-type:inline-size}@container (min-width:760px){.drawer-cols{grid-template-columns:repeat(2,minmax(0,1fr));gap:22px;align-items:start}}
@media(min-width:1200px){.drawer.is-wide{width:max(470px,min(1100px,calc(100vw - var(--drawer-right,0px) - 240px)));max-width:none}.drawer-backdrop.is-beside{right:var(--drawer-right)}}

/* 创作页（新建图文 / 动态视频、配音、字幕识别）: below 1200px keep the original top-to-bottom order; on desktop write on the left, settings + start on a sticky right column. */
.live-studio .creation-layout{display:flex;flex-direction:column}.live-studio .creation-side{display:contents}.live-studio .creation-side-head{display:none}.live-studio .creation-side>.setting-summaries{order:1}.live-studio .creation-side>.language-presets{order:2}.live-studio .creation-main{order:3;min-width:0}.live-studio .creation-side-actions{order:4}
@media(min-width:1200px){.live-studio .content.creation{max-width:2400px}.live-studio .creation-main{order:0}.live-studio .creation-side{order:1}.live-studio .creation-layout{display:grid;grid-template-columns:minmax(0,1fr) clamp(340px,28vw,520px);gap:28px;align-items:start}.live-studio .creation-side{display:flex;flex-direction:column;gap:14px;position:sticky;top:16px;padding:18px;border:1px solid var(--border,#35423f);border-radius:12px;background:var(--panel,#202526)}.live-studio .creation-side-head{display:grid;gap:3px}.live-studio .creation-side-head b{font-size:15px}.live-studio .creation-side-head small{font-size:12px;color:var(--muted,#a0aaa8)}.live-studio .creation-side .setting-summaries{grid-template-columns:minmax(0,1fr);margin:0;gap:10px}.live-studio .creation-side .setting-summaries:empty{display:none}.live-studio .creation-side .setting-summaries>button{min-height:0;padding:12px 14px}.live-studio .creation-side .language-presets{margin:0}.live-studio .creation-side-actions{display:grid;gap:12px;padding:14px 18px 18px;margin:0 -18px -18px;border-top:1px solid var(--border,#35423f);border-radius:0 0 12px 12px;position:sticky;bottom:0;background:var(--panel,#202526);z-index:1}.live-studio .creation-side .create-footer{display:grid;gap:12px;justify-content:stretch;margin:0}.live-studio .creation-side .create-footer>button.primary{width:100%;padding:13px 16px;font-size:14px}.live-studio .creation-side .create-footer .one-click-video-option{max-width:none}.live-studio .creation-side .studio-notice{margin:0}.live-studio .creation-side-actions>p{margin:0}.live-studio .kind-subtitle .creation-side-actions{border-top:0;padding-top:0}.live-studio .creation-main textarea{min-height:max(260px,calc(100vh - 620px))}}
.services-nav{display:flex;gap:10px;flex-wrap:wrap;padding:12px 0;margin-bottom:20px}.services-nav a{padding:9px 14px;border:1px solid var(--border,#35423f);border-radius:8px;color:var(--text,#eef3f1);text-decoration:none}.services-nav a:hover{border-color:var(--accent,#81d9bd);color:var(--accent,#81d9bd)}.services-page .studio-service-settings{width:100%;max-width:none;display:grid;gap:18px}.services-page .brand{display:none}.services-page .sidebar-card{margin:0;padding:22px;min-width:0}.services-page .api-key-card{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:24px;align-items:start}.services-page .api-key-card>.sidebar-label,.services-page .api-key-card>.muted,.services-page .api-key-message{grid-column:1/-1}.services-page [id^="service-"]{min-width:0;scroll-margin-top:90px}.services-page .api-key-entry{margin:0}.services-page input,.services-page select{max-width:100%}.services-page .sidebar-secondary-entry{display:none}@media(max-width:1050px){.services-page .api-key-card{grid-template-columns:minmax(0,1fr)}.services-page .sidebar-card{padding:16px}}
.live-studio .creation-heading-compact{margin-bottom:16px;gap:16px;align-items:center}.live-studio .creation-heading-compact h1{font-size:20px;font-weight:600;line-height:1.4}.live-studio .creation-heading-compact .eyebrow{font-size:9px;letter-spacing:1.5px;margin:0 0 4px;opacity:.7}.live-studio .creation-heading-compact .muted{font-size:12px;margin:5px 0 0}.live-studio .creation-settings-top{margin:0 0 24px;gap:12px}.live-studio .creation-settings-top>button{padding:14px 16px;min-height:76px}.live-studio .creation-settings-top b{font-size:13px;line-height:1.5}.live-studio .creation-settings-top small{font-size:11px}.live-studio .creation-heading-compact>button{font-size:12px;padding:9px 12px}
.live-studio .creation-project-title{display:grid;gap:6px;flex:1;min-width:0;max-width:900px;margin:0}.live-studio .creation-project-title>span{font-size:12px;color:var(--muted,#9aa9bb)}.live-studio .creation-project-title>input{width:100%;min-width:0;box-sizing:border-box;font-size:22px;font-weight:600;line-height:1.4;padding:8px 10px;border:1px solid transparent;border-radius:8px;background:transparent;color:inherit;text-overflow:ellipsis}.live-studio .creation-project-title>input:hover{border-color:var(--border,#34403e)}.live-studio .creation-project-title>input:focus{outline:2px solid var(--accent,#83dec5);outline-offset:2px;background:var(--bg,#111827)}.live-studio .creation-heading-compact>button{flex-shrink:0}
@media(max-width:680px){.live-studio .creation-project-title{flex-basis:100%}.live-studio .creation-project-title>input{font-size:18px}.live-studio .creation-heading-compact{align-items:flex-start;flex-wrap:wrap}.live-studio .creation-settings-top>button{min-height:62px;padding:10px 12px}}
.studio-visual-layout .studio-shot-list{max-height:calc(100vh - 150px);overflow-y:auto;min-width:0}.studio-visual-layout .studio-shot-detail{min-width:0}.studio-visual-layout .visual-image-card{display:block}.studio-visual-layout :deep(.workspace-panels){width:100%;min-width:0}.studio-visual-layout :deep(.raw-console){margin-top:0;max-width:100%}
@media(max-width:900px){.studio-visual-layout .studio-shot-list{display:flex;overflow-x:auto;gap:8px;max-height:190px}.studio-visual-layout .studio-shot-list .list-heading{display:none}.studio-visual-layout .studio-shot-list>button{flex:0 0 130px;margin:0}.studio-visual-layout .studio-shot-list img{height:60px}}
.live-studio .drawer label.dynamic-scene-toggle{display:flex;align-items:center;gap:8px}.live-studio .drawer label.dynamic-scene-toggle input{width:16px;height:16px;min-height:0;margin:0}
.live-studio .setting-summaries{grid-template-columns:repeat(4,minmax(0,1fr))}
.live-studio .dynamic-create-footer .one-click-video-option{display:flex;align-items:flex-start;gap:11px;max-width:650px}.live-studio .dynamic-create-footer .one-click-video-option>input{flex:0 0 auto;margin-top:3px}.live-studio .one-click-video-option>span{display:grid;gap:4px}.live-studio .one-click-video-option b{font-size:14px}.live-studio .one-click-video-option small{margin:0;line-height:1.55}
@media(max-width:1400px){.live-studio .setting-summaries{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:680px){.live-studio .setting-summaries{grid-template-columns:1fr}}
.live-studio .drawer .visual-pacing-panel{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.live-studio .drawer .visual-pacing-copy{grid-column:1/-1;display:grid;gap:6px}.live-studio .drawer .visual-pacing-copy strong{font-size:15px}.live-studio .drawer .visual-pacing-select{min-width:0;display:grid;gap:8px;align-content:start}.live-studio .drawer .visual-pacing-select select{width:100%}.live-studio .drawer .visual-pacing-custom{grid-column:1/-1}.live-studio .tts-boundary-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.live-studio .tts-boundary-row label{display:flex;align-items:center;gap:6px}.live-studio .tts-boundary-row input{width:76px}.live-studio .boundary-add{margin-left:auto}.live-studio button.primary-btn:disabled{background:#344554;color:#93a3af;opacity:.6;cursor:not-allowed;box-shadow:none}@media(max-width:540px){.live-studio .drawer .visual-pacing-panel{grid-template-columns:1fr}.live-studio .boundary-add{margin-left:0}}
</style>
