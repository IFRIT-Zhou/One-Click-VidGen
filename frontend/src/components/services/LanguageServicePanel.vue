<script setup>
// 接口与服务 · 语言模型：当前生效 → 接口账号与密钥 → 模型预设。
// Account drafts stay local until an explicit save; requests and payloads are
// the existing /api/api-keys and /api/language-presets contracts.
import './service-panel.css'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { api, requestJSON } from '../../api'
import SecretField from './SecretField.vue'
import { draftChanged, languageAccountRules, languagePresetRules, missingFields, snapshotDraft } from '../../serviceDraft'

const props = defineProps({
  status: { type: Object, default: () => ({}) },
  form: { type: Object, required: true },
  saveAccount: { type: Function, required: true },
  saving: Boolean,
  poolMode: Boolean,
  runtimeError: { type: String, default: '' },
})
const emit = defineEmits(['keys', 'state'])

const THINKING = { follow: '跟随接口默认', disabled: '强制关闭', enabled: '强制开启' }

// ---------- presets & current (shared source: /api/language-presets) ----------
const presets = ref([]), current = ref(null), loading = ref(true), loadError = ref('')
const presetBusy = ref(''), presetMsg = ref(''), presetErr = ref(''), flashId = ref('')
async function loadPresets() {
  loading.value = !current.value; loadError.value = ''
  try {
    const result = await requestJSON('/api/language-presets')
    presets.value = result.presets || []; current.value = result.current || null
  } catch (e) { loadError.value = e.message || '无法读取语言模型配置' } finally { loading.value = false }
}
async function retry() { await Promise.all([loadPresets(), refreshKeys()]) }
async function refreshKeys() {
  try { emit('keys', (await api.apiKeySettings()).keys) } catch { /* status card keeps last known values */ }
}
const providerLabel = (value) => (current.value?.providers || props.status?.providers || []).find((p) => p.value === value)?.label || value
const isCurrentPreset = (row) => !!current.value && row.provider === current.value.provider && row.model === current.value.model
const currentPreset = computed(() => presets.value.find(isCurrentPreset))
const currentLevel = computed(() => (props.runtimeError || loadError.value) ? 'error' : !current.value ? '' : current.value.configured ? 'ready' : 'missing')
const currentSecondary = computed(() => {
  const parts = []
  const hints = props.status?.key_hints || []
  parts.push(hints.length ? `已保存 Key ${hints.join('、')}` : '尚未保存 Key')
  if (props.status?.provider === 'custom') parts.push(`思考：${THINKING[props.status.custom_thinking_mode] || '跟随接口默认'}`)
  if (currentPreset.value) parts.push(`来自预设「${currentPreset.value.name}」`)
  return parts.join(' · ')
})

// ---------- accounts ----------
const accounts = computed(() => (props.status?.providers || []).filter((p) => !p.disabled || (p.key_hints || []).length))
const acctOpen = ref(false), acctBaseline = ref(null), acctMsg = ref(''), acctErr = ref(''), acctDeleting = ref('')
const acct = reactive({ source: 'official', provider: '', model: '', base_url: '', api_key: '', thinking: 'follow', reasoning_compat: true, reply_timeout: 120 })
const acctProvider = computed(() => (props.status?.providers || []).find((p) => p.value === acct.provider) || {})
const acctCustom = computed(() => acctProvider.value.source === 'custom' || acct.provider === 'custom')
const acctFamilies = computed(() => (props.status?.providers || []).filter((p) => p.source === acct.source))
const acctModels = computed(() => {
  const models = [...(acctProvider.value.models || [])]
  const selected = String(acctProvider.value.selected_model || '').trim()
  if (selected && !models.some((m) => m.value === selected)) models.unshift({ value: selected, label: `${selected}（当前配置）` })
  return models
})
const acctMissing = computed(() => acctOpen.value ? missingFields(acct, languageAccountRules(acctProvider.value)) : [])
const acctDirty = computed(() => acctOpen.value && draftChanged(acct, acctBaseline.value))
const acctFirstField = ref(null)
let returnFocus = null
function fillAccount(provider) {
  const p = (props.status?.providers || []).find((x) => x.value === provider) || {}
  Object.assign(acct, {
    source: p.source || 'official', provider: p.value || '',
    model: p.selected_model || (p.source === 'custom' ? '' : p.models?.[0]?.value || ''),
    base_url: p.source === 'custom' ? (p.base_url || '') : '', api_key: '',
    thinking: props.status?.custom_thinking_mode || 'follow',
    reasoning_compat: props.status?.custom_reasoning_compat ?? true,
    reply_timeout: props.status?.custom_reply_timeout || 120,
  })
}
function openAccount(provider) {
  if (acctOpen.value && acctDirty.value && !confirm('当前账号草稿有未保存的修改，放弃并编辑另一个账号？')) return
  // One editor at a time in the right-hand column.
  if (presetOpen.value) { if (presetDirty.value && !confirm('模型预设草稿有未保存的修改，放弃并编辑账号？')) return; presetOpen.value = false; presetBaseline.value = null }
  returnFocus = document.activeElement
  fillAccount(provider); acctBaseline.value = snapshotDraft(acct); acctOpen.value = true; acctMsg.value = ''; acctErr.value = ''
  nextTick(() => acctFirstField.value?.focus())
}
function closeAccount(force = false) {
  if (!force && acctDirty.value && !confirm('放弃未保存的账号修改？')) return
  acctOpen.value = false; acctBaseline.value = null
  nextTick(() => returnFocus?.isConnected && returnFocus.focus())
}
function changeSource() {
  const family = acctProvider.value.family
  const next = acctFamilies.value.find((p) => p.family === family) || acctFamilies.value[0]
  if (next) changeProvider(next.value)
}
function changeProvider(value) {
  const keep = { thinking: acct.thinking, reasoning_compat: acct.reasoning_compat, reply_timeout: acct.reply_timeout }
  fillAccount(value); Object.assign(acct, keep)
}
async function submitAccount() {
  if (props.poolMode || props.saving || acctMissing.value.length) return
  const label = acctProvider.value.label || acct.provider
  const model = acct.model || (acct.provider === current.value?.provider && current.value?.model) || '已保存的模型'
  if (!confirm(`保存后，全局语言模型会切换为「${label} · ${model}」，影响之后的规划与提示词生成。\n不会自动重规划，也不会改写已有提示词。继续？`)) return
  const fields = ['language_source', 'language_provider', 'language_model', 'language_api_base_url', 'custom_llm_thinking_mode', 'custom_llm_reasoning_compat', 'custom_llm_reply_timeout', 'language_api_key']
  const before = Object.fromEntries(fields.map((k) => [k, props.form[k]]))
  Object.assign(props.form, {
    language_source: acct.source, language_provider: acct.provider, language_model: acct.model,
    language_api_base_url: acctCustom.value ? acct.base_url : '', language_api_key: acct.api_key,
    ...(acctCustom.value ? { custom_llm_thinking_mode: acct.thinking, custom_llm_reasoning_compat: acct.reasoning_compat, custom_llm_reply_timeout: Number(acct.reply_timeout) } : {}),
  })
  acctMsg.value = ''; acctErr.value = ''
  // saveAccount resolves to { ok, message } (wraps the shared saveApiKeySettings).
  const result = await props.saveAccount()
  if (result?.ok) {
    acctMsg.value = String(result.message || '接口账号已保存').replace(/[。.]\s*$/, '') + '，已设为当前语言模型。'
    flash(acct.provider); closeAccount(true); await loadPresets()
  } else {
    Object.assign(props.form, before, { language_api_key: '' })
    acctErr.value = String(result?.message || '保存失败').replace(/[。.]\s*$/, '') + '。草稿已保留，请检查后重试。'
  }
}
async function deleteAccountKey(provider) {
  if (acctDeleting.value) return
  const inUse = provider.value === props.status?.provider
  if (!confirm(`清除「${provider.label}」在本机保存的 API Key？\n只清除本机副本，不会撤销服务商密钥，也不会切换当前模型；已有作品不受影响。${inUse ? '\n这是当前使用的账号：清除后之后的规划调用会失败，直到重新填写 Key。' : ''}\n部分旧渠道共用同一密钥，清除后会一起失效。`)) return
  acctDeleting.value = provider.value; acctMsg.value = ''; acctErr.value = ''
  try {
    const result = await api.deleteApiKey('language', 0, provider.value)
    if (result.keys) emit('keys', result.keys)
    acctMsg.value = result.message || 'API Key 已清除。'
    await loadPresets()
  } catch (e) { acctErr.value = e.message || '清除 API Key 失败。' } finally { acctDeleting.value = '' }
}

// ---------- presets editor ----------
const presetOpen = ref(false), presetBaseline = ref(null)
const draft = reactive({ id: '', name: '', provider: '', model: '', thinking: 'follow' })
const sources = computed(() => (current.value?.providers || []).filter((p) => p.configured && !p.disabled))
const draftSources = computed(() => {
  const list = [...sources.value]
  if (draft.provider && !list.some((p) => p.value === draft.provider)) list.push({ value: draft.provider, label: providerLabel(draft.provider) + '（账号未配置）', source: 'unknown' })
  return list
})
const draftProvider = computed(() => draftSources.value.find((p) => p.value === draft.provider) || {})
const presetMissing = computed(() => presetOpen.value ? missingFields(draft, languagePresetRules) : [])
const presetDirty = computed(() => presetOpen.value && draftChanged(draft, presetBaseline.value))
const presetFirstField = ref(null)
function openPreset(row = null) {
  if (presetOpen.value && presetDirty.value && !confirm('当前预设草稿有未保存的修改，放弃并继续？')) return
  if (acctOpen.value) { if (acctDirty.value && !confirm('账号草稿有未保存的修改，放弃并编辑预设？')) return; acctOpen.value = false; acctBaseline.value = null }
  returnFocus = document.activeElement
  const fallback = sources.value.find((p) => p.value === current.value?.provider) || sources.value[0]
  Object.assign(draft, row ? { id: row.id, name: row.name, provider: row.provider, model: row.model, thinking: row.thinking || 'follow' }
    : { id: '', name: '', provider: fallback?.value || '', model: '', thinking: 'follow' })
  presetBaseline.value = snapshotDraft(draft); presetOpen.value = true; presetMsg.value = ''; presetErr.value = ''
  nextTick(() => presetFirstField.value?.focus())
}
function closePreset(force = false) {
  if (!force && presetDirty.value && !confirm('放弃未保存的预设修改？')) return
  presetOpen.value = false; presetBaseline.value = null
  nextTick(() => returnFocus?.isConnected && returnFocus.focus())
}
const activateConfirm = (name) => confirm(`将全局语言模型切换为「${name}」？\n影响之后的规划与提示词生成；不会自动重规划，也不会改写已有提示词。`)
async function activate(row) {
  if (presetBusy.value || props.poolMode) return false
  presetBusy.value = 'activate:' + row.id; presetMsg.value = ''; presetErr.value = ''
  try {
    const result = await requestJSON('/api/language-presets/' + encodeURIComponent(row.id) + '/activate', { method: 'POST' })
    presets.value = result.presets || presets.value; current.value = result.current || current.value
    presetMsg.value = `已切换为「${row.name}」，下一次规划或提示词生成开始使用。`; flash(row.id)
    await refreshKeys(); return true
  } catch (e) { presetErr.value = e.message || '切换失败'; return false } finally { presetBusy.value = '' }
}
async function activateRow(row) { if (activateConfirm(row.name)) await activate(row) }
async function savePreset(andActivate) {
  if (presetBusy.value || presetMissing.value.length) return
  if (andActivate && (props.poolMode || !activateConfirm(draft.name))) return
  presetBusy.value = 'save'; presetMsg.value = ''; presetErr.value = ''
  let saved = null
  try {
    const payload = { ...draft, model: draft.model.trim(), name: draft.name.trim() }
    saved = (await requestJSON('/api/language-presets', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).preset
  } catch (e) { presetErr.value = e.message || '保存失败，草稿已保留。'; presetBusy.value = ''; return }
  presetBusy.value = ''
  closePreset(true); await loadPresets()
  const row = presets.value.find((p) => p.id === saved?.id) || saved
  if (andActivate && row) {
    const ok = await activate(row)
    if (!ok) presetErr.value = `预设「${row.name}」已保存，但未能设为当前：${presetErr.value}`
  } else {
    presetMsg.value = '模型预设已保存；当前语言模型没有变化。'; if (row) flash(row.id)
  }
}
async function removePreset(row) {
  if (presetBusy.value) return
  const inUse = isCurrentPreset(row)
  if (!confirm(`删除模型预设「${row.name}」？\n不会删除 API 密钥。${inUse ? '\n这是当前使用的模型：全局模型保持不变，只是不再出现在预设列表中。' : ''}`)) return
  presetBusy.value = 'delete:' + row.id; presetMsg.value = ''; presetErr.value = ''
  try {
    const result = await requestJSON('/api/language-presets/' + encodeURIComponent(row.id), { method: 'DELETE' })
    presets.value = result.presets || []; current.value = result.current || current.value
    if (presetOpen.value && draft.id === row.id) closePreset(true)
    presetMsg.value = `已删除预设「${row.name}」。`
  } catch (e) { presetErr.value = e.message || '删除失败' } finally { presetBusy.value = '' }
}

let flashTimer
function flash(id) { flashId.value = id; clearTimeout(flashTimer); flashTimer = setTimeout(() => { flashId.value = '' }, 1600) }

watch([currentLevel, acctDirty, presetDirty], () => emit('state', { level: currentLevel.value, dirty: acctDirty.value || presetDirty.value }), { immediate: true })

onMounted(loadPresets)
</script>

<template>
  <section class="svc-panel" aria-labelledby="svc-language-title">
    <h2 id="svc-language-title" class="sr-only-svc">语言模型</h2>
    <div v-if="poolMode" class="svc-banner" role="note"><strong>号池已接管文本模型</strong><span>下方个人配置保留不变，可以查看和整理；「设为当前」暂不可用。关闭号池后按“当前生效”的配置运行。</span></div>


    <div class="svc-workspace">
    <div class="svc-main svc-main--split">
    <!-- 接口账号与密钥 -->
    <section class="svc-section" aria-labelledby="svc-acct-title">
      <div class="svc-section-head"><div><h3 id="svc-acct-title">① 接口账号与密钥</h3><p>先配置能调用的账号。保存账号会同时把它设为当前语言模型（系统按账号保存当前模型）。</p></div></div>
        <ul class="svc-list" aria-label="语言接口账号">
          <li v-for="p in accounts" :key="p.value" class="svc-row" :class="{ 'is-current': p.value === status.provider, 'is-editing': acctOpen && acct.provider === p.value, 'is-flash': flashId === p.value }">
            <div class="svc-row-title"><b>{{ p.label }}</b>
              <span v-if="p.value === status.provider" class="svc-badge ok">✓ 使用中</span>
              <span v-if="p.disabled" class="svc-badge muted">暂不可用</span>
              <span v-else-if="!p.configured" class="svc-badge warn">未配置</span></div>
            <div class="svc-row-meta">{{ p.source === 'custom' ? '自定义兼容接口' : '官方 API' }} ·
              <template v-if="(p.key_hints || []).length">已保存 Key <code v-for="h in p.key_hints" :key="h">{{ h }}</code></template><template v-else>未保存 Key</template></div>
            <div class="svc-row-actions">
              <button type="button" :disabled="saving || p.disabled" :aria-label="`${p.configured ? '编辑' : '配置'}账号 ${p.label}`" @click="openAccount(p.value)">{{ p.configured ? '编辑' : '配置' }}</button>
              <button v-if="(p.key_hints || []).length" type="button" class="svc-danger-text" :disabled="saving || !!acctDeleting" :aria-label="`清除 ${p.label} 在本机保存的 API Key`" @click="deleteAccountKey(p)">{{ acctDeleting === p.value ? '清除中…' : '清除密钥' }}</button>
            </div>
          </li>
          <li v-if="!accounts.length" class="svc-note">{{ loading ? '正在读取账号列表…' : '暂时读取不到账号列表，请点上方「重试」或稍后刷新页面。' }}</li>
        </ul>

      <p v-if="acctMsg" class="svc-status" role="status">{{ acctMsg }}</p>
      <p v-if="acctErr && !acctOpen" class="svc-error" role="alert">{{ acctErr }}</p>
    </section>

    <!-- 模型预设 -->
    <section class="svc-section" aria-labelledby="svc-preset-title">
      <div class="svc-section-head"><div><h3 id="svc-preset-title">② 模型预设</h3><p>在已配置的账号下保存常用模型，之后一键切换。预设不保存密钥。</p></div>
        <button type="button" :disabled="!!presetBusy || !sources.length || (presetOpen && !draft.id)" @click="openPreset()">＋ 新增模型</button></div>
      <p v-if="!loading && !loadError && !sources.length" class="svc-note">还没有可用的接口账号，请先在上方完成配置。</p>
        <div class="svc-section">
          <div v-if="loading" class="svc-skeleton" aria-label="正在读取模型预设…" />
          <ul v-else-if="presets.length" class="svc-list" aria-label="已保存的模型预设">
            <li v-for="row in presets" :key="row.id" class="svc-row" :class="{ 'is-current': isCurrentPreset(row), 'is-editing': presetOpen && draft.id === row.id, 'is-flash': flashId === row.id }">
              <div class="svc-row-title"><b>{{ row.name }}</b>
                <span v-if="isCurrentPreset(row)" class="svc-badge ok">✓ 使用中</span>
                <span v-else-if="current && !sources.some((s) => s.value === row.provider)" class="svc-badge warn">▲ 账号未配置</span></div>
              <div class="svc-row-meta">{{ row.model }} · {{ providerLabel(row.provider) }}</div>
              <div class="svc-row-actions">
                <button type="button" :disabled="!!presetBusy || poolMode || isCurrentPreset(row) || !sources.some((s) => s.value === row.provider)"
                  :title="poolMode ? '号池接管期间不能切换' : isCurrentPreset(row) ? '已经是当前模型' : !sources.some((s) => s.value === row.provider) ? '请先配置该预设使用的接口账号' : ''"
                  :aria-label="`把 ${row.name} 设为当前语言模型`" @click="activateRow(row)">{{ presetBusy === 'activate:' + row.id ? '切换中…' : '设为当前' }}</button>
                <button type="button" :disabled="!!presetBusy" :aria-label="`编辑预设 ${row.name}`" @click="openPreset(row)">编辑</button>
                <button type="button" class="svc-danger-text" :disabled="!!presetBusy" :aria-label="`删除预设 ${row.name}`" @click="removePreset(row)">{{ presetBusy === 'delete:' + row.id ? '删除中…' : '删除' }}</button>
              </div>
            </li>
          </ul>
          <div v-else-if="!loadError" class="svc-empty">
            <div class="svc-empty-title"><div><span>填写示范</span><b>我的规划模型</b></div><em>仅为格式示例</em></div>
            <dl><div><dt>预设名称</dt><dd>我的规划模型</dd></div><div><dt>复用接口账号</dt><dd>上方已配置的账号</dd></div><div><dt>完整模型 ID</dt><dd>以服务商文档为准</dd></div><div><dt>密钥</dt><dd>复用账号，不单独保存</dd></div></dl>
            <p>OCV 不指定或推荐第三方服务商。</p>
            <button type="button" class="primary" :disabled="!sources.length" @click="openPreset()">＋ 按格式新增预设</button>
          </div>
        </div>

      <p v-if="presetMsg" class="svc-status" role="status">{{ presetMsg }}</p>
      <p v-if="presetErr && !presetOpen" class="svc-error" role="alert">{{ presetErr }}</p>
    </section>
    </div>
    <aside class="svc-aside" aria-label="当前配置与编辑区">
      <div class="svc-current" aria-live="polite">
        <div class="svc-current-top"><span class="svc-eyebrow">当前生效</span><span class="svc-scope">全局 · 影响之后的规划与提示词生成</span></div>
        <div v-if="loading" class="svc-skeleton" aria-label="正在读取当前语言模型…" />
        <p v-else-if="loadError" class="svc-error" role="alert">{{ loadError }}<button type="button" @click="retry">重试</button></p>
        <template v-else-if="current">
          <div class="svc-row-title"><span class="svc-current-main">{{ current.provider_label }} · {{ current.model || '未设置模型' }}</span>
            <span v-if="runtimeError" class="svc-badge error">▲ 调用出错</span>
            <span v-else-if="!current.configured" class="svc-badge warn">▲ 账号未配置完整</span>
            <span v-else class="svc-badge ok">● 可用</span></div>
          <div class="svc-current-sub">{{ currentSecondary }}</div>
          <p v-if="runtimeError" class="svc-error">{{ runtimeError }}</p>
        </template>
      </div>
      <form v-if="acctOpen" class="svc-editor" aria-labelledby="svc-acct-editor" @submit.prevent="submitAccount">
        <div class="svc-editor-head"><h4 id="svc-acct-editor">{{ acctProvider.configured ? '编辑账号' : '配置账号' }} · {{ acctProvider.label || '语言接口' }}</h4>
          <span :class="acctDirty ? 'svc-dirty' : 'svc-clean'">{{ acctDirty ? '● 草稿 · 有未保存修改' : '草稿 · 保存前不影响任何任务' }}</span></div>
        <fieldset class="svc-group"><legend>基本信息</legend>
          <label class="svc-field"><span class="svc-label-row"><span class="svc-label">接口来源</span><span class="svc-req">必填</span></span>
            <select ref="acctFirstField" v-model="acct.source" :disabled="saving" @change="changeSource"><option value="official">官方 API</option><option value="custom">自定义兼容接口（高级）</option></select></label>
          <label class="svc-field"><span class="svc-label-row"><span class="svc-label">模型家族</span><span class="svc-req">必填</span></span>
            <select :value="acct.provider" :disabled="saving" @change="changeProvider($event.target.value)"><option v-for="p in acctFamilies" :key="p.value" :value="p.value" :disabled="p.disabled">{{ p.family_label || p.label }}{{ p.disabled ? '（暂不可用）' : '' }}</option></select></label>
          <label v-if="acctModels.length && !acctCustom" class="svc-field svc-wide" :class="{ 'is-missing': acctMissing.includes('Agent 模型') }"><span class="svc-label-row"><span class="svc-label">Agent 模型</span><span class="svc-req">必填</span></span>
            <select v-model="acct.model" :disabled="saving"><option v-for="m in acctModels" :key="m.value" :value="m.value">{{ m.label }}</option></select></label>
          <label v-else class="svc-field svc-wide" :class="{ 'is-missing': acctMissing.includes('完整模型 ID') || acctMissing.includes('Agent 模型') }"><span class="svc-label-row"><span class="svc-label">完整模型 ID</span><span v-if="!(acctCustom && acctProvider.configured)" class="svc-req">必填</span><span v-else class="svc-opt">已保存；不修改可留空</span></span>
            <input v-model.trim="acct.model" name="ocv-language-model-id" autocomplete="off" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore maxlength="256" :disabled="saving" :placeholder="acctProvider.configured ? '已保存；不修改可留空' : '填写接口文档中的完整模型调用名'"></label>
          <label v-if="acctCustom" class="svc-field svc-wide" :class="{ 'is-missing': acctMissing.includes('API Base URL') }"><span class="svc-label-row"><span class="svc-label">API Base URL</span><span v-if="!acctProvider.configured" class="svc-req">必填</span><span v-else class="svc-opt">已保存；更换地址需同时替换 Key</span></span>
            <input v-model.trim="acct.base_url" type="url" inputmode="url" name="ocv-language-api-base-url" autocomplete="off" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore maxlength="2048" :disabled="saving" :placeholder="acctProvider.configured ? '已保存；不修改可留空' : '例如 https://api.example.com/v1'"></label>
        </fieldset>
        <SecretField v-model="acct.api_key" :label="acctCustom ? 'API Key' : `${acctProvider.label || ''} API Key`" :saved="!!(acctProvider.key_hints || []).length" :hints="acctProvider.key_hints || []"
          :required="!acctCustom && !acctProvider.configured" :optional-note="acctCustom ? '本地无鉴权可留空' : ''" :disabled="saving" input-name="ocv-language-api-secret" />
        <details v-if="acctCustom" class="svc-advanced"><summary>高级设置 · 思考模式与等待时间<span v-if="acct.thinking !== 'follow' || !acct.reasoning_compat || Number(acct.reply_timeout) !== 120" class="svc-badge muted">已调整</span></summary>
          <div class="svc-group">
            <label class="svc-field"><span class="svc-label">思考模式</span><select v-model="acct.thinking" :disabled="saving"><option value="follow">跟随接口默认（推荐）</option><option value="disabled">强制关闭（DeepSeek 兼容）</option><option value="enabled">强制开启（DeepSeek 兼容）</option></select>
              <small>强制开关会发送 DeepSeek 格式的 <code>thinking</code> 参数；其他模型请选“跟随接口默认”。</small></label>
            <div class="svc-field"><label class="svc-check"><input v-model="acct.reasoning_compat" type="checkbox" :disabled="saving">思考模型兼容：保留思考，仅将结论正文交给下游</label>
              <label v-if="acct.reasoning_compat" class="svc-field" :class="{ 'is-missing': acctMissing.some((m) => m.startsWith('单次回复')) }"><span class="svc-label">单次回复最长等待（秒）</span><input v-model.number="acct.reply_timeout" type="number" min="10" max="600" step="1" :disabled="saving"></label>
              <small v-if="acct.reasoning_compat">默认 120 秒，包含思考和正文接收。超时不自动重试或切换渠道；本地停止等待不保证上游停止计费。</small></div>
          </div>
        </details>
        <p v-if="acctCustom" class="svc-note">需兼容 <code>/chat/completions</code>、具备足够上下文长度并能稳定输出 JSON。安全提示：API Key 会随请求发送到此网址，请只填写你信任的服务地址。</p>
        <div class="svc-footer">
          <span v-if="acctMissing.length" class="svc-missing" role="status">还缺：{{ acctMissing.join('、') }}</span>
          <button type="button" :disabled="saving" @click="closeAccount()">取消</button>
          <button type="submit" class="primary" :disabled="saving || poolMode || acctMissing.length > 0">{{ saving ? '保存中…' : '保存并设为当前' }}</button>
          <span class="svc-footer-note">{{ poolMode ? '号池接管期间不能切换当前语言模型。' : '会弹出确认；保存后全局语言模型切换为此账号和模型。' }}</span>
        </div>
        <p v-if="acctErr" class="svc-error" role="alert">{{ acctErr }}</p>
      </form>
      <form v-else-if="presetOpen" class="svc-editor" aria-labelledby="svc-preset-editor" @submit.prevent="savePreset(false)">
        <div class="svc-editor-head"><h4 id="svc-preset-editor">{{ draft.id ? '编辑预设' : '新增预设' }}</h4>
          <span :class="presetDirty ? 'svc-dirty' : 'svc-clean'">{{ presetDirty ? '● 草稿 · 有未保存修改' : '草稿 · 保存前不影响任何任务' }}</span></div>
        <fieldset class="svc-group"><legend>基本信息</legend>
          <label class="svc-field" :class="{ 'is-missing': presetMissing.includes('预设名称') }"><span class="svc-label-row"><span class="svc-label">预设名称</span><span class="svc-req">必填</span></span>
            <input ref="presetFirstField" v-model="draft.name" maxlength="100" :disabled="!!presetBusy" placeholder="例如：我的规划模型"></label>
          <label class="svc-field" :class="{ 'is-missing': presetMissing.includes('复用接口账号') }"><span class="svc-label-row"><span class="svc-label">复用接口账号</span><span class="svc-req">必填</span></span>
            <select v-model="draft.provider" :disabled="!!presetBusy"><option v-for="s in draftSources" :key="s.value" :value="s.value">{{ s.source === 'official' ? s.label : s.source === 'unknown' ? s.label : '自定义兼容接口 · ' + (s.family_label || s.label) }}</option></select></label>
          <label class="svc-field svc-wide" :class="{ 'is-missing': presetMissing.includes('完整模型 ID') }"><span class="svc-label-row"><span class="svc-label">完整模型 ID</span><span class="svc-req">必填</span></span>
            <input v-model.trim="draft.model" maxlength="256" :disabled="!!presetBusy" placeholder="填写接口文档中的完整模型调用名" autocomplete="off" spellcheck="false"></label>
          <label v-if="draft.provider === 'custom'" class="svc-field svc-wide"><span class="svc-label">思考模式</span>
            <select v-model="draft.thinking" :disabled="!!presetBusy"><option value="follow">跟随接口默认</option><option value="disabled">强制关闭（需接口支持）</option><option value="enabled">强制开启（需接口支持）</option></select></label>
        </fieldset>
        <div class="svc-footer">
          <span v-if="presetMissing.length" class="svc-missing" role="status">还缺：{{ presetMissing.join('、') }}</span>
          <button type="button" :disabled="!!presetBusy" @click="closePreset()">取消</button>
          <button type="submit" :disabled="!!presetBusy || presetMissing.length > 0">{{ presetBusy === 'save' ? '保存中…' : '仅保存' }}</button>
          <button type="button" class="primary" :disabled="!!presetBusy || poolMode || presetMissing.length > 0" @click="savePreset(true)">保存并设为当前</button>
          <span class="svc-footer-note">「仅保存」不切换当前模型；「保存并设为当前」会再确认一次。</span>
        </div>
        <p v-if="presetErr" class="svc-error" role="alert">{{ presetErr }}</p>
      </form>
      <div v-else class="svc-aside-idle">
        <h4>编辑区</h4>
        <p>点左侧账号或预设的「编辑 / 配置 / 新增」，在这里修改。改动先存为草稿，保存前不会影响任何任务。</p>
        <ul>
          <li>接口账号：保存时会同时把它设为当前语言模型，会先弹出确认。</li>
          <li>模型预设：可以「仅保存」，不会切换当前模型。</li>
          <li>已保存的 Key 默认保留，只有点「替换」才会发送新 Key；密钥只保存在本机，不回显原文。</li>
        </ul>
      </div>
    </aside>
    </div>
  </section>
</template>

<style scoped>
.sr-only-svc{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
</style>
