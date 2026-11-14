<script setup>
// 接口与服务 · 图像接口：当前出图配置 → 已保存配置 → 编辑草稿。
// Uses the existing /api/image-profiles contract unchanged. Selecting a
// profile only changes form.image_profile_id, exactly as before.
import './service-panel.css'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../../api'
import IcanChannelHint from '../IcanChannelHint.vue'
import CloudImageQuality from '../CloudImageQuality.vue'
import { imageChoice } from '../../icanImageSizes'
import SecretField from './SecretField.vue'
import { draftChanged, imageConcurrencyRules, imageProfileRules, missingFields, parseExtraParameters, snapshotDraft } from '../../serviceDraft'

const props = defineProps({
  form: { type: Object, required: true },
  // 出图并发 is global (RUNNINGHUB_* settings) and applies to whichever personal profile is used.
  concurrency: { type: Object, default: () => ({}) },
  saveConcurrency: { type: Function, default: null },
})
const emit = defineEmits(['saved', 'state'])

const ASYNC_PATHS = { text_endpoint: '/openapi/v2/{model}/text-to-image', reference_endpoint: '/openapi/v2/{model}/image-to-image', query_endpoint: '/openapi/v2/query' }
const SYNC_PATHS = { text_endpoint: '/v1/images/generations', reference_endpoint: '/v1/images/edits' }
const protocolLabel = (p) => p === 'openai_sync' ? 'OpenAI 兼容 · 同步返回' : '异步任务接口'

const profiles = ref([]), loading = ref(true), loadError = ref(''), busy = ref(''), msg = ref(''), err = ref(''), flashId = ref('')
const pool = computed(() => !!props.form.use_cloud_image_pool)
const selected = computed(() => profiles.value.find((p) => p.id === props.form.image_profile_id) || null)
const resolutions = computed(() => selected.value?.resolutions || ['1k', '2k', '4k'])
const cloudSummary = computed(() => props.form.method === 'ican'
  ? `ICAN · GPT Image 2.5 · ${imageChoice(props.form.size).quality}`
  : `稳定版 · ${(props.form.image_resolution || '2k').toUpperCase()}`)
const level = computed(() => loading.value ? '' : loadError.value ? 'error' : pool.value ? 'ready' : !selected.value ? 'missing' : selected.value.configured ? 'ready' : 'missing')

function syncResolution() { if (pool.value) return; if (!resolutions.value.includes(props.form.image_resolution)) props.form.image_resolution = resolutions.value[0] || '1k' }
async function load() {
  loading.value = !profiles.value.length; loadError.value = ''
  try {
    profiles.value = (await api.imageProfiles()).profiles || []
    if (!profiles.value.some((x) => x.id === props.form.image_profile_id)) props.form.image_profile_id = profiles.value.find((x) => x.configured)?.id || ''
    syncResolution()
  } catch (e) { loadError.value = e.message || '无法读取图像模型配置' } finally { loading.value = false }
}
function useProfile(p) {
  props.form.image_profile_id = p.id; syncResolution(); flash(p.id)
  err.value = ''; msg.value = `已选「${p.name}」作为当前出图配置。从视频项目进入时，返回后应用到该项目。`
}

// ---------- editor ----------
const open = ref(false), baseline = ref(null), original = ref(null), extraParameters = ref('{}'), firstField = ref(null)
const draft = reactive({})
const isNew = computed(() => !draft.id)
const extra = computed(() => draft.protocol === 'openai_sync' ? parseExtraParameters(extraParameters.value) : { ok: true, value: {} })
const missing = computed(() => {
  if (!open.value) return []
  const list = missingFields(draft, imageProfileRules({ isNew: isNew.value, previousBaseUrl: original.value?.base_url, hadKeys: (original.value?.key_count || 0) > 0 }))
  if (!extra.value.ok) list.push('有效的额外参数 JSON')
  return list
})
const dirty = computed(() => open.value && draftChanged({ ...draft, extra: extraParameters.value }, baseline.value))
let returnFocus = null
function fresh() { return { id: '', name: '', protocol: 'async_task', base_url: '', model_id: '', ...ASYNC_PATHS, resolutions: ['1k', '2k', '4k'], reference_images: true, api_key: '', size: 'auto', quality: '', response_format: '', request_parameters: {}, timeout_seconds: 600 } }
function openEditor(profile = null) {
  if (open.value && dirty.value && !confirm('当前配置草稿有未保存的修改，放弃并继续？')) return
  // One editor at a time in the right-hand column.
  if (conOpen.value) { if (conDirty.value && !confirm('出图并发草稿有未保存的修改，放弃并继续？')) return; conOpen.value = false; conBaseline.value = null }
  returnFocus = document.activeElement
  for (const key of Object.keys(draft)) delete draft[key]
  if (profile) {
    const { configured, key_count, key_hints, legacy, ...rest } = profile
    Object.assign(draft, fresh(), rest, { api_key: '', resolutions: [...(profile.resolutions || ['1k', '2k', '4k'])] })
  } else Object.assign(draft, fresh())
  draft.size ||= 'auto'; draft.timeout_seconds ||= 600
  extraParameters.value = JSON.stringify(draft.request_parameters || {}, null, 2)
  original.value = profile; baseline.value = snapshotDraft({ ...draft, extra: extraParameters.value }); open.value = true; msg.value = ''; err.value = ''
  nextTick(() => firstField.value?.focus())
}
function closeEditor(force = false) {
  if (!force && dirty.value && !confirm('放弃未保存的配置修改？')) return
  open.value = false; baseline.value = null; original.value = null
  nextTick(() => returnFocus?.isConnected && returnFocus.focus())
}
function chooseProtocol() { Object.assign(draft, draft.protocol === 'openai_sync' ? { ...SYNC_PATHS, size: draft.size || 'auto', timeout_seconds: draft.timeout_seconds || 600 } : { ...ASYNC_PATHS }) }
function toggleResolution(value) { const list = draft.resolutions; const index = list.indexOf(value); if (index >= 0 && list.length > 1) list.splice(index, 1); else if (index < 0) list.push(value) }
async function save(andUse) {
  if (busy.value || missing.value.length) return
  busy.value = 'save'; msg.value = ''; err.value = ''
  try {
    const payload = { ...draft, request_parameters: extra.value.value, resolutions: [...draft.resolutions] }
    if (!String(payload.api_key || '').trim()) delete payload.api_key
    const result = await api.saveImageProfile(payload)
    await load()
    if (andUse) { props.form.image_profile_id = result.profile.id; syncResolution() }
    emit('saved', result.profile.id)
    closeEditor(true); flash(result.profile.id)
    msg.value = String(result.message || '图像模型配置已保存').replace(/[。.]\s*$/, '') + (andUse ? '，并已选为当前出图配置。' : '；当前出图配置没有变化。')
  } catch (e) { err.value = e.message || '保存失败，草稿已保留。' } finally { busy.value = '' }
}
async function remove(p) {
  if (busy.value) return
  const inUse = props.form.image_profile_id === p.id
  if (!confirm(`删除“${p.name}”及其本机保存的全部密钥？\n不会撤销服务商密钥，也不会删除已生成素材。依赖此接口的项目后续调用可能失败。${inUse ? '\n这是当前出图配置：删除后不会自动换成其他配置，需要你重新选择。' : ''}`)) return
  busy.value = 'delete:' + p.id; msg.value = ''; err.value = ''
  try {
    const result = await api.deleteImageProfile(p.id)
    profiles.value = (await api.imageProfiles()).profiles || []
    if (inUse) props.form.image_profile_id = ''
    if (open.value && draft.id === p.id) closeEditor(true)
    msg.value = result.message || '图像接口已删除；请按需重新选择配置。'
  } catch (e) { err.value = e.message || '删除失败' } finally { busy.value = '' }
}
// ---------- 出图并发 ----------
const conOpen = ref(false), conBaseline = ref(null), conBusy = ref(false), conMsg = ref(''), conErr = ref(''), conFirst = ref(null)
const con = reactive({ mode: 'auto', per_key: 1, total: 3 })
const conSaved = computed(() => ({ mode: props.concurrency?.mode === 'manual' ? 'manual' : 'auto', per_key: Number(props.concurrency?.per_key || 1), total: Number(props.concurrency?.total_limit || 3) }))
const conMissing = computed(() => conOpen.value ? missingFields(con, imageConcurrencyRules()) : [])
const conDirty = computed(() => conOpen.value && draftChanged(con, conBaseline.value))
const conSummary = computed(() => `${conSaved.value.mode === 'manual' ? '手动限制' : '自动（推荐）'} · 单 Key 并发 ${conSaved.value.per_key}${conSaved.value.mode === 'manual' ? ' · 总并发上限 ' + conSaved.value.total : ''}`)
function conPreview(v) {
  const p = selected.value
  if (!p) return '选择出图配置后，按它的 Key 数计算可同时生成的张数。'
  const keys = p.key_count || 0, per = Math.max(1, +v.per_key || 1), cap = keys * per
  const effective = v.mode === 'manual' ? Math.min(cap, Math.max(1, +v.total || 1)) : cap
  return keys ? `当前出图配置「${p.name}」有 ${keys} 个 Key × 每 Key ${per} 路，最多同时生成 ${effective} 张。` : `当前出图配置「${p.name}」还没有 Key。`
}
function openConcurrency() {
  if (open.value) { if (dirty.value && !confirm('图像配置草稿有未保存的修改，放弃并编辑出图并发？')) return; open.value = false; baseline.value = null; original.value = null }
  returnFocus = document.activeElement
  Object.assign(con, conSaved.value); conBaseline.value = snapshotDraft(con); conOpen.value = true; conMsg.value = ''; conErr.value = ''
  nextTick(() => conFirst.value?.focus())
}
function closeConcurrency(force = false) {
  if (!force && conDirty.value && !confirm('放弃未保存的并发修改？')) return
  conOpen.value = false; conBaseline.value = null
  nextTick(() => returnFocus?.isConnected && returnFocus.focus())
}
async function saveConcurrencyDraft() {
  if (conBusy.value || pool.value || conMissing.value.length || !props.saveConcurrency) return
  conBusy.value = true; conMsg.value = ''; conErr.value = ''
  const result = await props.saveConcurrency({ mode: con.mode, per_key: Number(con.per_key), total: Number(con.total) })
  conBusy.value = false
  if (result?.ok) { closeConcurrency(true); conMsg.value = '出图并发已保存。' + conPreview(conSaved.value) }
  else conErr.value = String(result?.message || '保存出图并发设置失败').replace(/[。.]\s*$/, '') + '。草稿已保留。'
}

let flashTimer
function flash(id) { flashId.value = id; clearTimeout(flashTimer); flashTimer = setTimeout(() => { flashId.value = '' }, 1600) }

watch(() => props.form.image_profile_id, syncResolution)
watch([level, dirty, conDirty], () => emit('state', { level: level.value, dirty: dirty.value || conDirty.value }), { immediate: true })
onMounted(load)
</script>

<template>
  <section class="svc-panel" aria-label="图像接口">

    <div class="svc-workspace">
    <div class="svc-main svc-main--split">
    <!-- 列表 + 编辑 -->
    <section class="svc-section" aria-labelledby="svc-image-list-title">
      <div class="svc-section-head"><div><h3 id="svc-image-list-title">已保存的图像接口</h3><p>每个模型、接口地址和 Key 独立保存，不预设任何中转商。</p></div>
        <button type="button" :disabled="!!busy || (open && isNew)" @click="openEditor()">＋ 新增配置</button></div>
        <div class="svc-section">
          <div v-if="loading" class="svc-skeleton" aria-label="正在读取图像配置…" />
          <ul v-else-if="profiles.length" class="svc-list" aria-label="图像接口配置">
            <li v-for="p in profiles" :key="p.id" class="svc-row" :class="{ 'is-current': !pool && form.image_profile_id === p.id, 'is-editing': open && draft.id === p.id, 'is-flash': flashId === p.id }">
              <div class="svc-row-title"><b>{{ p.name }}</b>
                <span v-if="!pool && form.image_profile_id === p.id" class="svc-badge ok">✓ 使用中</span>
                <span v-if="!p.configured" class="svc-badge warn">▲ 缺少 Key</span>
                <span v-if="p.legacy" class="svc-badge muted">旧版接口</span></div>
              <div class="svc-row-meta">{{ p.model_id }} · {{ protocolLabel(p.protocol) }} · {{ p.key_count }} 个 Key</div>
              <div class="svc-row-meta">{{ p.base_url }}</div>
              <div v-if="p.key_hints?.length" class="svc-row-meta">已保存密钥 <code v-for="h in p.key_hints" :key="h">{{ h }}</code></div>
              <div class="svc-row-actions">
                <button type="button" :disabled="!!busy || pool || form.image_profile_id === p.id || !p.configured"
                  :title="pool ? '号池开启中，出图走号池渠道' : !p.configured ? '缺少 Key，请先编辑补充' : form.image_profile_id === p.id ? '已经在使用' : ''"
                  :aria-label="`在本项目使用 ${p.name}`" @click="useProfile(p)">在本项目使用</button>
                <button type="button" :disabled="!!busy" :aria-label="`编辑 ${p.name}`" @click="openEditor(p)">编辑</button>
                <button type="button" class="svc-danger-text" :disabled="!!busy" :aria-label="`${p.legacy ? '清除旧接口' : '删除接口及密钥'} ${p.name}`" @click="remove(p)">{{ busy === 'delete:' + p.id ? '删除中…' : (p.legacy ? '清除旧接口' : '删除') }}</button>
              </div>
            </li>
          </ul>
          <div v-else-if="!loadError" class="svc-empty">
            <div class="svc-empty-title"><div><span>填写示范</span><b>Image 2.5 · 我的接口</b></div><em>仅为格式示例</em></div>
            <dl><div><dt>API Base URL</dt><dd>https://api.example.com</dd></div><div><dt>模型 ID</dt><dd>image-2.5</dd></div><div><dt>API Key</dt><dd>填写服务商提供的密钥</dd></div><div><dt>支持分辨率</dt><dd>1K / 2K / 4K</dd></div></dl>
            <p>实际内容请以你的接口服务商文档为准。OCV 不指定或推荐第三方服务商。</p>
            <button class="primary" type="button" :disabled="open" @click="openEditor()">＋ 按格式新增配置</button>
          </div>
        </div>

      <p v-if="msg" class="svc-status" role="status">{{ msg }}</p>
      <p v-if="err && !open" class="svc-error" role="alert">{{ err }}</p>
    </section>

    <!-- 出图并发（原独立分页，合并到图像接口） -->
    <section id="service-concurrency" class="svc-section" aria-labelledby="svc-concurrency-title">
      <div class="svc-section-head"><div><h3 id="svc-concurrency-title">出图并发</h3><p>对所有个人图像配置生效：同时生成的张数 = 所用配置的 Key 数 × 单 Key 并发（手动模式再受总上限限制）。</p></div></div>
      <div class="svc-row" :class="{ 'is-editing': conOpen }">
        <template v-if="pool">
          <div class="svc-row-title"><b>由云端号池服务器动态调度</b><span class="svc-badge muted">号池模式</span></div>
          <div class="svc-row-meta">号池开启期间不使用这里的设置；关闭号池后按已保存的设置运行：{{ conSummary }}</div>
        </template>
        <template v-else>
          <div class="svc-row-title"><b>{{ conSummary }}</b></div>
          <div class="svc-row-meta">{{ conPreview(conSaved) }}</div>
        </template>
        <div class="svc-row-actions"><button type="button" :disabled="pool || conOpen || conBusy || !saveConcurrency" :title="pool ? '号池模式下由服务器调度' : ''" aria-label="编辑出图并发" @click="openConcurrency">编辑</button></div>
      </div>
      <p v-if="conMsg" class="svc-status" role="status">{{ conMsg }}</p>
      <p v-if="conErr && !conOpen" class="svc-error" role="alert">{{ conErr }}</p>
    </section>
    </div>
    <aside class="svc-aside" aria-label="当前配置与编辑区">
      <div class="svc-current" aria-live="polite">
        <div class="svc-current-top"><span class="svc-eyebrow">当前出图配置</span><span class="svc-scope">当前工作的出图 · 从视频项目进入时，返回后应用到该项目</span></div>
        <template v-if="pool">
          <div class="svc-row-title"><span class="svc-current-main">号池出图 · {{ cloudSummary }}</span><span class="svc-badge ok">● 号池托管</span></div>
          <div class="svc-current-sub">下方个人配置保留不变，可以继续整理；关闭号池后按所选个人配置出图。</div>
          <div class="svc-current-fields">
            <label class="svc-field"><span class="svc-label">图片生成渠道</span><IcanChannelHint :active="form.method === 'ican'"><select v-model="form.method"><option value="running">稳定版</option><option value="ican">ICAN · GPT Image 2.5</option></select></IcanChannelHint></label>
            <CloudImageQuality :form="form" compact />
          </div>
        </template>
        <div v-else-if="loading" class="svc-skeleton" aria-label="正在读取图像配置…" />
        <p v-else-if="loadError" class="svc-error" role="alert">{{ loadError }}<button type="button" @click="load">重试</button></p>
        <template v-else-if="selected">
          <div class="svc-row-title"><span class="svc-current-main">{{ selected.name }} · {{ selected.model_id }}</span>
            <span v-if="selected.configured" class="svc-badge ok">● 可用</span><span v-else class="svc-badge warn">▲ 缺少 Key</span></div>
          <div class="svc-current-sub">{{ protocolLabel(selected.protocol) }} · {{ selected.key_count }} 个 Key · {{ selected.base_url }}</div>
          <div class="svc-current-fields">
            <label v-if="selected.protocol === 'openai_sync'" class="svc-field"><span class="svc-label">同步接口出图尺寸</span><input :value="selected.size || 'auto'" disabled><small>由配置里的 size 控制；需修改时编辑该配置。</small></label>
            <label v-else class="svc-field"><span class="svc-label">出图分辨率</span><select v-model="form.image_resolution"><option v-for="r in resolutions" :key="r" :value="r">{{ r.toUpperCase() }}</option></select></label>
          </div>
        </template>
        <template v-else>
          <div class="svc-row-title"><span class="svc-current-main">尚未选择出图配置</span><span class="svc-badge warn">▲ 未选择</span></div>
          <div class="svc-current-sub">{{ profiles.length ? '在下方列表中点「在本项目使用」。缺少 Key 的配置不能使用。' : '先新增一个图像接口配置。' }}</div>
        </template>
      </div>
      <form v-if="open" class="svc-editor" aria-labelledby="svc-image-editor" @submit.prevent="save(false)">
        <div class="svc-editor-head"><h4 id="svc-image-editor">{{ isNew ? '新增图像接口' : '编辑 · ' + (original?.name || '') }}</h4>
          <span :class="dirty ? 'svc-dirty' : 'svc-clean'">{{ dirty ? '● 草稿 · 有未保存修改' : '草稿 · 保存前不影响任何任务' }}</span></div>
        <fieldset class="svc-group"><legend>基本信息</legend>
          <label class="svc-field" :class="{ 'is-missing': missing.includes('配置名称') }"><span class="svc-label-row"><span class="svc-label">配置名称</span><span class="svc-req">必填</span></span>
            <input ref="firstField" v-model="draft.name" maxlength="60" :disabled="!!busy" placeholder="例如：Image 2.5 · 我的中转接口"></label>
          <label class="svc-field"><span class="svc-label-row"><span class="svc-label">接口协议</span><span class="svc-req">必填</span></span>
            <select v-model="draft.protocol" :disabled="!!busy" @change="chooseProtocol"><option value="async_task">异步任务接口 · 提交后查询</option><option value="openai_sync">OpenAI 兼容图像接口 · 同步返回</option></select></label>
          <label class="svc-field svc-wide" :class="{ 'is-missing': missing.includes('API Base URL') }"><span class="svc-label-row"><span class="svc-label">API Base URL</span><span class="svc-req">必填</span><span v-if="!isNew" class="svc-opt">更换地址需同时替换 Key</span></span>
            <input v-model.trim="draft.base_url" type="url" inputmode="url" maxlength="2048" autocomplete="off" spellcheck="false" :disabled="!!busy" placeholder="https://api.example.com"></label>
          <label class="svc-field svc-wide" :class="{ 'is-missing': missing.includes('模型 ID') }"><span class="svc-label-row"><span class="svc-label">模型 ID</span><span class="svc-req">必填</span></span>
            <input v-model.trim="draft.model_id" maxlength="256" autocomplete="off" spellcheck="false" :disabled="!!busy" placeholder="填写服务商提供的模型 ID"></label>
        </fieldset>
        <SecretField v-model="draft.api_key" label="API Key" :saved="!isNew && (original?.key_count || 0) > 0" :hints="original?.key_hints || []" :required="isNew"
          placeholder="填写 API Key；多个可用逗号分隔" replace-note="填写后替换此配置下全部已保存 Key（多个用逗号分隔）；留空仍保留原 Key。" :disabled="!!busy" input-name="ocv-image-api-secret" />
        <fieldset v-if="draft.protocol === 'openai_sync'" class="svc-group"><legend>同步接口参数</legend>
          <p class="svc-note svc-wide">适用于 images/generations 与 images/edits；支持 Base64 或图片 URL 返回。不会调用 RunningHub 上传/查询接口。超时后不会自动重新出图，避免重复收费。</p>
          <label class="svc-field" :class="{ 'is-missing': missing.some((m) => m.startsWith('出图尺寸')) }"><span class="svc-label">实际出图尺寸</span><input v-model.trim="draft.size" :disabled="!!busy" placeholder="auto 或 1536x1024"></label>
          <label class="svc-field"><span class="svc-label-row"><span class="svc-label">质量参数</span><span class="svc-opt">可留空</span></span><input v-model="draft.quality" :disabled="!!busy" placeholder="以服务商文档为准，例如 high"></label>
          <label class="svc-field"><span class="svc-label">返回格式</span><select v-model="draft.response_format" :disabled="!!busy"><option value="">不传参数 · 服务商默认</option><option value="b64_json">b64_json</option><option value="url">url</option></select></label>
          <label class="svc-field" :class="{ 'is-missing': missing.some((m) => m.startsWith('等待超时')) }"><span class="svc-label">等待超时（秒）</span><input v-model.number="draft.timeout_seconds" type="number" min="30" max="1800" :disabled="!!busy"></label>
          <small class="svc-note svc-wide">同步接口使用以上 size，不将 1K/2K/4K 擅自转成不受支持的尺寸；模型、提示词、参考图与数量由 OCV 管理。</small>
        </fieldset>
        <fieldset class="svc-group"><legend>能力</legend>
          <div class="svc-field svc-wide" :class="{ 'is-missing': missing.includes('至少一种支持分辨率') }"><span class="svc-label">支持分辨率（至少一种）</span>
            <div class="svc-checks"><label v-for="r in ['1k', '2k', '4k']" :key="r" class="svc-check"><input type="checkbox" :checked="draft.resolutions.includes(r)" :disabled="!!busy" @change="toggleResolution(r)"> {{ r.toUpperCase() }}</label>
              <label class="svc-check"><input v-model="draft.reference_images" type="checkbox" :disabled="!!busy"> 支持参考图（图生图）</label></div></div>
        </fieldset>
        <details class="svc-advanced"><summary>高级 · 接口路径{{ draft.protocol === 'openai_sync' ? '与额外请求参数' : '' }}</summary>
          <div class="svc-group">
            <label class="svc-field svc-wide"><span class="svc-label">文生图路径</span><input v-model.trim="draft.text_endpoint" :disabled="!!busy" spellcheck="false"></label>
            <label class="svc-field svc-wide"><span class="svc-label">参考生图路径</span><input v-model.trim="draft.reference_endpoint" :disabled="!!busy" spellcheck="false"></label>
            <label v-if="draft.protocol !== 'openai_sync'" class="svc-field svc-wide"><span class="svc-label">查询路径</span><input v-model.trim="draft.query_endpoint" :disabled="!!busy" spellcheck="false"></label>
            <label v-if="draft.protocol === 'openai_sync'" class="svc-field svc-wide" :class="{ 'is-missing': !extra.ok }"><span class="svc-label-row"><span class="svc-label">额外请求参数（JSON）</span><span class="svc-opt">可选</span></span>
              <textarea v-model="extraParameters" rows="3" spellcheck="false" :disabled="!!busy" placeholder='{"output_format":"png"}' :aria-invalid="!extra.ok" />
              <small v-if="!extra.ok" class="svc-error">{{ extra.error }}</small><small v-else>以服务商文档为准；不能覆盖模型、提示词、图片、数量、密钥或流式设置。</small></label>
            <small class="svc-note svc-wide">路径必须与 Base URL 同源，避免把密钥发送到其他地址。</small>
          </div>
        </details>
        <div class="svc-footer">
          <span v-if="missing.length" class="svc-missing" role="status">还缺：{{ missing.join('、') }}</span>
          <button type="button" :disabled="!!busy" @click="closeEditor()">取消</button>
          <button type="submit" :disabled="!!busy || missing.length > 0">{{ busy === 'save' ? '保存中…' : '仅保存' }}</button>
          <button type="button" class="primary" :disabled="!!busy || pool || missing.length > 0" @click="save(true)">保存并在本项目使用</button>
          <span class="svc-footer-note">{{ pool ? '号池开启中，只能保存配置，出图仍走号池。' : '「仅保存」不改变当前出图配置。' }}</span>
        </div>
        <p v-if="err" class="svc-error" role="alert">{{ err }}</p>
      </form>
      <form v-else-if="conOpen" class="svc-editor" aria-labelledby="svc-concurrency-editor" @submit.prevent="saveConcurrencyDraft">
        <div class="svc-editor-head"><h4 id="svc-concurrency-editor">编辑出图并发</h4>
          <span :class="conDirty ? 'svc-dirty' : 'svc-clean'">{{ conDirty ? '● 草稿 · 有未保存修改' : '草稿 · 保存前不影响任何任务' }}</span></div>
        <div class="svc-group">
          <label class="svc-field"><span class="svc-label">模式</span><select ref="conFirst" v-model="con.mode" :disabled="conBusy"><option value="auto">自动（推荐）</option><option value="manual">手动限制</option></select></label>
          <label class="svc-field" :class="{ 'is-missing': conMissing.some((m) => m.startsWith('单 Key')) }"><span class="svc-label">单 Key 并发（1–16）</span><input v-model.number="con.per_key" type="number" min="1" max="16" :disabled="conBusy"></label>
          <label v-if="con.mode === 'manual'" class="svc-field" :class="{ 'is-missing': conMissing.some((m) => m.startsWith('总并发')) }"><span class="svc-label">总并发上限（1–64）</span><input v-model.number="con.total" type="number" min="1" max="64" :disabled="conBusy"></label>
          <small class="svc-note svc-wide">保存后：{{ conPreview(con) }} 三方接口通常保持单 Key 并发 1；官方或高并发接口可按服务商额度提高。</small>
        </div>
        <div class="svc-footer">
          <span v-if="conMissing.length" class="svc-missing" role="status">还缺：{{ conMissing.join('、') }}</span>
          <button type="button" :disabled="conBusy" @click="closeConcurrency()">取消</button>
          <button type="submit" class="primary" :disabled="conBusy || !conDirty || conMissing.length > 0">{{ conBusy ? '保存中…' : '保存并发设置' }}</button>
        </div>
        <p v-if="conErr" class="svc-error" role="alert">{{ conErr }}</p>
      </form>
      <div v-else class="svc-aside-idle">
        <h4>编辑区</h4>
        <p>点左侧配置的「编辑」或「＋ 新增配置」，或出图并发的「编辑」，在这里修改。改动先存为草稿，保存前不会影响任何任务。</p>
        <ul>
          <li>「仅保存」不会改变当前出图配置；「保存并在本项目使用」才会切换。</li>
          <li>已保存的 Key 默认保留；点「替换」后填写的 Key 会替换这个配置下的全部 Key。更换接口地址时必须重新填写 Key。</li>
          <li>出图并发对所有个人图像配置生效，按所用配置的 Key 数计算。</li>
        </ul>
      </div>
    </aside>
    </div>
  </section>
</template>
