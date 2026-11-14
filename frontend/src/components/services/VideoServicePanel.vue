<script setup>
// 接口与服务 · 视频接口：当前视频接口 → 编辑草稿。
// Same /api/video-model contract and payload as the previous form. New keys
// on the same site are appended as parallel keys; a new site needs new keys.
import './service-panel.css'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { requestJSON } from '../../api'
import { readResolution, updateResolution, resolutionSize } from '../../rhWorkflowResolution'
import { draftChanged, missingFields, snapshotDraft, urlOrigin, videoRules } from '../../serviceDraft'

const emit = defineEmits(['state'])
const PROTOCOLS = { async_task: '兼容异步视频接口', ark: '火山方舟官方 · Seedance', runninghub_workflow: 'RunningHub 工作流（RH 币）' }
const DEFAULT_WORKFLOW_ID = '2108955980238266370'

const model = ref(null), loading = ref(true), loadError = ref(''), busy = ref(''), msg = ref(''), err = ref('')
const open = ref(false), baseline = ref(null), customResolution = ref(false), firstField = ref(null)
const draft = reactive(emptyDraft())

function emptyWorkflow() { return { workflow_id: DEFAULT_WORKFLOW_ID, image_nodes: [], prompt_node: { node_id: '', field: 'text' }, duration_node: { node_id: '', field: 'value' }, output_nodes: [], preferred_output: '', instance_type: 'default', use_personal_queue: false, overrides: [] } }
function emptyDraft() { return { protocol: 'async_task', model: 'doubao-seedance-2-0-260128', base_url: '', submit_path: '/openapi/v2/model/multimodal-video', query_path: '/openapi/v2/query', upload_path: '/openapi/v2/media/upload/binary', resolution: '720p', concurrency_mode: 'auto', per_key_concurrency: 1, total_concurrency: 3, api_keys: [''], workflow: emptyWorkflow(), image_nodes_text: '', output_nodes_text: '', overrides_text: '[]' } }
function workflowInto(target, value) {
  const workflow = { ...emptyWorkflow(), ...value, prompt_node: value?.prompt_node || { node_id: '', field: 'text' }, duration_node: value?.duration_node || { node_id: '', field: 'value' } }
  if (!workflow.workflow_id) workflow.workflow_id = DEFAULT_WORKFLOW_ID
  Object.assign(target, {
    workflow, image_nodes_text: (value?.image_nodes || []).map((n) => `${n.node_id}.${n.field}`).join(', '),
    output_nodes_text: (value?.output_nodes || []).join(', '), overrides_text: JSON.stringify(value?.overrides || [], null, 2),
  })
}
function fill(value) {
  Object.assign(draft, emptyDraft(), {
    protocol: value.protocol || 'async_task', model: value.model || 'doubao-seedance-2-0-260128', base_url: value.base_url || '',
    submit_path: value.submit_path || '', query_path: value.query_path || '/openapi/v2/query', upload_path: value.upload_path || '/openapi/v2/media/upload/binary',
    resolution: value.resolution || '720p', concurrency_mode: value.concurrency_mode || 'auto', per_key_concurrency: value.per_key_concurrency || 1,
    total_concurrency: value.total_concurrency || 3, api_keys: [''],
  })
  workflowInto(draft, value.workflow)
}
function workflowPayload() {
  return {
    ...draft.workflow,
    image_nodes: draft.image_nodes_text.split(/[,，\n]/).map((v) => v.trim()).filter(Boolean).map((v) => { const pos = v.lastIndexOf('.'); if (pos < 1) throw Error('图片槽位请填写“节点编号.字段名”，多个槽位用逗号分隔'); return { node_id: v.slice(0, pos), field: v.slice(pos + 1) } }),
    output_nodes: draft.output_nodes_text.split(/[,，\n]/).map((v) => v.trim()).filter(Boolean),
    overrides: JSON.parse(draft.overrides_text || '[]'),
  }
}

async function load() {
  loading.value = !model.value; loadError.value = ''
  try { model.value = await requestJSON('/api/video-model') } catch (e) { loadError.value = e.message || '无法读取视频接口配置' } finally { loading.value = false }
}

// ---------- summary ----------
const configured = computed(() => !!model.value?.has_api_key)
const level = computed(() => loading.value ? '' : loadError.value ? 'error' : configured.value ? 'ready' : 'missing')
function capacity(count, mode, perKey, total) {
  const per = Math.max(1, +perKey || 1), cap = count * per
  const effective = mode === 'manual' ? Math.min(cap, Math.max(1, +total || 1)) : cap
  return count ? `${count} 个 Key × 每 Key ${per} 路，预计最多 ${effective} 路并发` : '尚未保存视频 API Key'
}
const summaryMain = computed(() => {
  const m = model.value; if (!m) return ''
  const detail = m.protocol === 'ark' ? m.model : m.protocol === 'runninghub_workflow' ? `工作流 ${m.workflow?.workflow_id || '未设置'}` : (m.model_label || '多模态视频')
  return `${PROTOCOLS[m.protocol] || PROTOCOLS.async_task} · ${detail}`
})
const summarySub = computed(() => {
  const m = model.value; if (!m) return ''
  return [m.base_url || '尚未设置地址', m.protocol !== 'runninghub_workflow' ? `原生 ${m.resolution || '720p'}` : '分辨率由工作流决定',
    capacity(m.key_count || 0, m.concurrency_mode, m.per_key_concurrency, m.total_concurrency)].join(' · ')
})
async function removeKey(index, hint) {
  if (busy.value) return
  if (!confirm(`清除本机保存的视频 API Key ${hint}？\n不会撤销服务商密钥。正在运行或需要续查的原任务可能仍依赖它。`)) return
  busy.value = 'delete:' + index; err.value = ''; msg.value = ''
  try { model.value = await requestJSON('/api/video-model/keys/' + index, { method: 'DELETE' }); msg.value = '视频 API Key 已删除。' }
  catch (e) { err.value = e.message || '删除失败' } finally { busy.value = '' }
}

// ---------- editor ----------
const rh = computed(() => draft.protocol === 'runninghub_workflow')
const savedKeys = computed(() => model.value?.key_count || 0)
const siteChanged = computed(() => savedKeys.value > 0 && !!urlOrigin(draft.base_url) && urlOrigin(draft.base_url) !== urlOrigin(model.value?.base_url))
const missing = computed(() => {
  if (!open.value) return []
  const list = missingFields(draft, videoRules({ savedKeyCount: savedKeys.value, previousBaseUrl: model.value?.base_url }))
  if (rh.value) { try { workflowPayload() } catch (e) { list.push(e instanceof SyntaxError ? '有效的固定节点参数 JSON' : '正确的图片槽位格式（节点编号.字段名）') } }
  return list
})
const missingForReuse = computed(() => open.value ? missingFields(draft, videoRules({ savedKeyCount: savedKeys.value, previousBaseUrl: model.value?.base_url, reuseImageKeys: true })) : [])
const dirty = computed(() => open.value && draftChanged(draft, baseline.value))
const draftPreview = computed(() => capacity(savedKeys.value + (siteChanged.value ? -savedKeys.value : 0) + draft.api_keys.filter((k) => k.trim()).length, draft.concurrency_mode, draft.per_key_concurrency, draft.total_concurrency))
const rhResolution = computed(() => { try { return readResolution(draft.overrides_text) } catch { return null } })
const rhSize = computed(() => rhResolution.value ? resolutionSize(rhResolution.value) : '')
const rhPreset = computed(() => customResolution.value ? 'custom' : rhResolution.value?.megapixels === 1 ? '1' : rhResolution.value?.megapixels === 2 ? '2' : 'custom')
function changeRhResolution(field, value) { try { draft.overrides_text = updateResolution(draft.overrides_text, field, value); err.value = '' } catch (e) { err.value = '请先修正下方固定节点参数 JSON：' + e.message } }
function chooseRhResolution(event) { customResolution.value = event.target.value === 'custom'; if (!customResolution.value) changeRhResolution('megapixels', Number(event.target.value)) }
function useWorkflowPreset(preset) { workflowInto(draft, preset.config); msg.value = '已填入 ' + preset.name + ' 节点映射，点击保存后生效。'; err.value = '' }
function chooseProtocol() {
  draft.per_key_concurrency = draft.protocol === 'ark' ? 3 : 1
  if (draft.protocol === 'ark') draft.total_concurrency = Math.max(3, draft.total_concurrency || 3)
  if (draft.protocol === 'ark') Object.assign(draft, { base_url: 'https://ark.cn-beijing.volces.com', submit_path: '/api/v3/contents/generations/tasks', query_path: '/api/v3/contents/generations/tasks', upload_path: '/api/v3', model: 'doubao-seedance-2-0-260128' })
  else if (draft.protocol === 'runninghub_workflow') Object.assign(draft, { base_url: 'https://www.runninghub.ai', submit_path: '/openapi/v2/run/workflow/' + draft.workflow.workflow_id, query_path: '/openapi/v2/query', upload_path: '/openapi/v2/media/upload/binary' })
  else Object.assign(draft, { submit_path: '/openapi/v2/model/multimodal-video', query_path: '/openapi/v2/query', upload_path: '/openapi/v2/media/upload/binary', resolution: '720p' })
}
function addKey() { if (draft.api_keys.length < 10) draft.api_keys.push('') }
let returnFocus = null
function openEditor() {
  returnFocus = document.activeElement
  fill(model.value || {}); customResolution.value = false
  baseline.value = snapshotDraft(draft); open.value = true; msg.value = ''; err.value = ''
  nextTick(() => firstField.value?.focus())
}
function closeEditor(force = false) {
  if (!force && dirty.value && !confirm('放弃未保存的视频接口修改？')) return
  open.value = false; baseline.value = null
  nextTick(() => returnFocus?.isConnected && returnFocus.focus())
}
function unlock(event) { event?.currentTarget?.removeAttribute('readonly') }
async function save(reuse = false) {
  if (busy.value || (reuse ? missingForReuse.value : missing.value).length) return
  busy.value = 'save'; err.value = ''; msg.value = ''
  try {
    const { workflow, image_nodes_text, output_nodes_text, overrides_text, ...base } = draft
    const payload = { ...base, workflow: rh.value ? workflowPayload() : (model.value?.workflow || {}), api_keys: draft.api_keys.map((v) => v.trim()).filter(Boolean), use_image_credentials: reuse }
    model.value = await requestJSON('/api/video-model', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    closeEditor(true)
    msg.value = reuse ? '已复用兼容的图像 API 账号和全部并行 Key；之后新提交的视频任务开始使用。' : '视频接口配置已保存；之后新提交的视频任务开始使用。'
  } catch (e) { err.value = String(e.message || '保存失败').replace(/[。.]\s*$/, '') + '。草稿已保留。' } finally { busy.value = '' }
}

watch([level, dirty], () => emit('state', { level: level.value, dirty: dirty.value }), { immediate: true })
onMounted(load)
</script>

<template>
  <section class="svc-panel" aria-label="视频接口">
    <div class="svc-workspace">
    <div class="svc-main">
    <div class="svc-current" aria-live="polite">
      <div class="svc-current-top"><span class="svc-eyebrow">当前视频接口</span><span class="svc-scope">之后新提交的视频任务 · 不影响已提交或已生成的视频</span></div>
      <div v-if="loading" class="svc-skeleton" aria-label="正在读取视频接口…" />
      <p v-else-if="loadError" class="svc-error" role="alert">{{ loadError }}<button type="button" @click="load">重试</button></p>
      <template v-else-if="model">
        <div class="svc-row-title"><span class="svc-current-main">{{ summaryMain }}</span>
          <span v-if="configured" class="svc-badge ok">● 已配置</span>
          <span v-else-if="model.source === 'image_compatible'" class="svc-badge warn">▲ 未保存视频 Key · 可复用同站点图像账号</span>
          <span v-else class="svc-badge warn">▲ 未配置</span></div>
        <div class="svc-current-sub">{{ summarySub }}</div>
        <div v-if="model.key_hints?.length" class="svc-row-meta">已保存 Key
          <span v-for="(hint, index) in model.key_hints" :key="hint + index" class="svc-key-chip"><code>{{ hint }}</code>
            <button type="button" class="svc-danger-text" :disabled="!!busy" :aria-label="`删除视频 API Key ${hint}`" @click="removeKey(index, hint)">{{ busy === 'delete:' + index ? '删除中…' : '删除' }}</button></span></div>
        <div class="svc-row-actions"><button type="button" :class="{ primary: !configured }" :disabled="!!busy || open" @click="openEditor">{{ configured ? '编辑配置' : '配置视频接口' }}</button></div>
      </template>
    </div>
    <p v-if="msg && !open" class="svc-status" role="status">{{ msg }}</p>
    <p v-if="err && !open" class="svc-error" role="alert">{{ err }}</p>

    </div>
    <aside class="svc-aside" aria-label="编辑区">
    <form v-if="open" class="svc-editor" aria-labelledby="svc-video-editor" @submit.prevent="save(false)">
      <div class="svc-editor-head"><h4 id="svc-video-editor">编辑视频接口</h4>
        <span :class="dirty ? 'svc-dirty' : 'svc-clean'">{{ dirty ? '● 草稿 · 有未保存修改' : '草稿 · 保存前不影响任何任务' }}</span></div>

      <fieldset class="svc-group"><legend>基本信息</legend>
        <label class="svc-field"><span class="svc-label-row"><span class="svc-label">接入方式</span><span class="svc-req">必填</span></span>
          <select ref="firstField" v-model="draft.protocol" :disabled="!!busy" @change="chooseProtocol"><option value="async_task">兼容异步视频接口</option><option value="ark">火山方舟官方 · Seedance</option><option value="runninghub_workflow">RunningHub 工作流（RH 币）</option></select></label>
        <label v-if="draft.protocol === 'ark'" class="svc-field" :class="{ 'is-missing': missing.includes('模型调用名称') }"><span class="svc-label-row"><span class="svc-label">模型调用名称</span><span class="svc-req">必填</span></span>
          <input v-model.trim="draft.model" :disabled="!!busy" placeholder="doubao-seedance-2-0-260128" autocomplete="off" spellcheck="false"></label>
        <label class="svc-field svc-wide" :class="{ 'is-missing': missing.some((m) => m.includes('Base URL')) }"><span class="svc-label-row"><span class="svc-label">API Base URL</span><span class="svc-req">必填</span><span v-if="savedKeys" class="svc-opt">换站点需要填写新 Key</span></span>
          <input v-model.trim="draft.base_url" type="url" :disabled="!!busy" placeholder="https://api.example.com" autocomplete="off" spellcheck="false"></label>
        <template v-if="!rh">
          <label class="svc-field" :class="{ 'is-missing': missing.includes('视频提交路径') }"><span class="svc-label-row"><span class="svc-label">视频提交路径</span><span class="svc-req">必填</span></span>
            <input v-model.trim="draft.submit_path" :disabled="!!busy" placeholder="/openapi/v2/模型/…/multimodal-video" autocomplete="off" spellcheck="false"></label>
          <label class="svc-field"><span class="svc-label">原生分辨率</span>
            <select v-model="draft.resolution" :disabled="!!busy"><option value="480p">480p</option><option value="720p">720p</option><option v-if="draft.protocol === 'ark'" value="1080p">1080p · 标准版</option></select></label>
        </template>
      </fieldset>
      <details v-if="!rh" class="svc-advanced"><summary>高级 · 状态查询与素材上传路径</summary>
        <div class="svc-group">
          <label class="svc-field"><span class="svc-label">状态查询路径</span><input v-model.trim="draft.query_path" :disabled="!!busy" placeholder="/openapi/v2/query" autocomplete="off" spellcheck="false"></label>
          <label class="svc-field"><span class="svc-label">素材上传路径</span><input v-model.trim="draft.upload_path" :disabled="!!busy" placeholder="/openapi/v2/media/upload/binary" autocomplete="off" spellcheck="false"></label>
        </div>
      </details>

      <fieldset v-if="rh" class="svc-group"><legend>RH 币工作流</legend>
        <p class="svc-note svc-wide">按每镜的核心图、视频提示词和请求秒数运行已发布工作流。分辨率、采样及音频开关由工作流决定；请核对节点后保存。不同工作流的节点编号不同，示例编号不可直接套用。运行费用及会员要求以 RunningHub 账号为准。</p>
        <div v-if="model?.workflow_presets?.length" class="svc-field svc-wide"><span class="svc-label">工作流预设</span>
          <div class="svc-row-actions"><button v-for="preset in model.workflow_presets" :key="preset.id" type="button" :disabled="!!busy" @click="useWorkflowPreset(preset)">使用 {{ preset.name }}</button></div>
          <small v-if="draft.workflow.workflow_id === DEFAULT_WORKFLOW_ID">aiwood 预设：单张核心图、24 帧；时长自动跟随本镜。参考音频未启用。</small></div>
        <template v-if="draft.workflow.workflow_id === DEFAULT_WORKFLOW_ID">
          <label class="svc-field"><span class="svc-label">视频生成清晰度</span><select :value="rhPreset" :disabled="!!busy || !rhResolution" @change="chooseRhResolution"><option value="1">720P 级 · 1MP（默认）</option><option value="2">1080P 级 · 2MP</option><option value="custom">自定义像素规模</option></select></label>
          <label v-if="rhPreset === 'custom'" class="svc-field"><span class="svc-label">像素规模（MP）</span><input type="number" min="0.1" max="16" step="0.1" :value="rhResolution?.megapixels" :disabled="!!busy || !rhResolution" @change="changeRhResolution('megapixels', Number($event.target.value))"></label>
          <div class="svc-field"><span class="svc-label">预计生成尺寸</span><span class="svc-note">{{ rhSize || '请检查高级节点参数' }} 像素</span></div>
          <small class="svc-note svc-wide">尺寸按工作流比例及对齐规则计算。默认横屏 1080P 级为 1920 × 1088，并非精确 1920 × 1080；本选项不裁剪视频。更高分辨率会增加资源消耗。保存后用于新提交任务，不改变已生成视频或已提交任务。</small>
        </template>
        <label class="svc-field" :class="{ 'is-missing': missing.includes('工作流 ID') }"><span class="svc-label-row"><span class="svc-label">工作流 ID</span><span class="svc-req">必填</span></span><input v-model.trim="draft.workflow.workflow_id" :disabled="!!busy" placeholder="2108955980238266370"></label>
        <label class="svc-field"><span class="svc-label">运行规格</span><select v-model="draft.workflow.instance_type" :disabled="!!busy"><option value="default">default · 标准规格</option><option value="plus">plus · 大显存</option><option value="ultra">ultra · 超大显存</option></select></label>
        <label class="svc-field svc-wide" :class="{ 'is-missing': missing.includes('图片槽位') || missing.some((m) => m.includes('图片槽位格式')) }"><span class="svc-label-row"><span class="svc-label">图片槽位（按参考图顺序，第一个为核心图）</span><span class="svc-req">必填</span></span><input v-model="draft.image_nodes_text" :disabled="!!busy" placeholder="例如 51.image, 49.image；以你的工作流为准"></label>
        <label class="svc-field" :class="{ 'is-missing': missing.includes('提示词节点编号') }"><span class="svc-label-row"><span class="svc-label">提示词节点编号</span><span class="svc-req">必填</span></span><input v-model.trim="draft.workflow.prompt_node.node_id" :disabled="!!busy" placeholder="API 面板中的 nodeId"></label>
        <label class="svc-field"><span class="svc-label">提示词字段</span><input v-model.trim="draft.workflow.prompt_node.field" :disabled="!!busy" placeholder="text"></label>
        <label class="svc-field" :class="{ 'is-missing': missing.includes('时长节点编号') }"><span class="svc-label-row"><span class="svc-label">时长节点编号（秒）</span><span class="svc-req">必填</span></span><input v-model.trim="draft.workflow.duration_node.node_id" :disabled="!!busy" placeholder="API 面板中的 nodeId"></label>
        <label class="svc-field"><span class="svc-label">时长字段</span><input v-model.trim="draft.workflow.duration_node.field" :disabled="!!busy" placeholder="value"></label>
        <label class="svc-field" :class="{ 'is-missing': missing.includes('视频保存节点') }"><span class="svc-label-row"><span class="svc-label">视频保存节点（逗号分隔）</span><span class="svc-req">必填</span></span><input v-model="draft.output_nodes_text" :disabled="!!busy" placeholder="自动将这些节点的 save_output 设为 true"></label>
        <label class="svc-field" :class="{ 'is-missing': missing.includes('成品输出节点') }"><span class="svc-label-row"><span class="svc-label">成品输出节点</span><span class="svc-req">必填</span></span><input v-model.trim="draft.workflow.preferred_output" :disabled="!!busy" placeholder="填写最终视频节点，避免取到一采预览"></label>
        <label class="svc-check svc-wide"><input v-model="draft.workflow.use_personal_queue" type="checkbox" :disabled="!!busy">使用个人队列</label>
        <details class="svc-advanced svc-wide"><summary>其他固定节点参数（可选）</summary>
          <label class="svc-field" :class="{ 'is-missing': missing.includes('有效的固定节点参数 JSON') }"><span class="svc-label">固定节点参数 JSON</span><textarea v-model="draft.overrides_text" rows="5" spellcheck="false" :disabled="!!busy" />
            <small>用于分辨率、采样等额外参数。格式：[{"node_id":"节点编号","field":"字段名","value":数值或文字}]。不填写时沿用工作流默认值。未使用的额外素材槽位需要在工作流中关闭。</small></label>
        </details>
      </fieldset>

      <fieldset class="svc-group"><legend>API Key</legend>
        <div class="svc-field svc-wide">
          <div v-if="model?.key_hints?.length" class="svc-secret-saved"><span class="svc-secret-state"><span>已保存 <code v-for="(hint, index) in model.key_hints" :key="hint + index">{{ hint }}</code></span>
            <small>{{ siteChanged ? '地址换到了新站点：已保存的 Key 不会发送到新站点，保存时会被下方填写的新 Key 替换。' : '保留已保存的 Key。下方填写的 Key 会追加为并行 Key。' }}</small></span></div>
          <label v-for="(_, index) in draft.api_keys" :key="index" class="svc-field" :class="{ 'is-missing': index === 0 && missing.some((m) => m.includes('API Key')) }">
            <span class="svc-label-row"><span class="svc-label">{{ savedKeys && !siteChanged ? '追加并行 Key' : 'API Key' }} {{ index + 1 }}</span><span v-if="index === 0 && missing.some((m) => m.includes('API Key'))" class="svc-req">必填</span><span v-else class="svc-opt">可留空</span></span>
            <input v-model="draft.api_keys[index]" type="password" autocomplete="new-password" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore readonly :disabled="!!busy" placeholder="填写服务商提供的 Key" @focus="unlock"></label>
          <button type="button" class="svc-link-btn" style="justify-self:start" :disabled="!!busy || draft.api_keys.length >= 10" @click="addKey">＋ 再添加一个并行 Key</button>
        </div>
      </fieldset>

      <fieldset class="svc-group"><legend>视频并发</legend>
        <label class="svc-field"><span class="svc-label">模式</span><select v-model="draft.concurrency_mode" :disabled="!!busy"><option value="auto">自动</option><option value="manual">手动限制</option></select></label>
        <label class="svc-field" :class="{ 'is-missing': missing.some((m) => m.startsWith('单 Key')) }"><span class="svc-label">单 Key 并发（1–8）</span><input v-model.number="draft.per_key_concurrency" type="number" min="1" max="8" :disabled="!!busy"></label>
        <label v-if="draft.concurrency_mode === 'manual'" class="svc-field" :class="{ 'is-missing': missing.some((m) => m.startsWith('总并发')) }"><span class="svc-label">总并发上限（1–32）</span><input v-model.number="draft.total_concurrency" type="number" min="1" max="32" :disabled="!!busy"></label>
        <small class="svc-note svc-wide">保存后：{{ draftPreview }}</small>
      </fieldset>

      <small class="svc-note">接口按异步任务协议工作；地址和路径以你的服务商文档为准。OCV 不指定或推荐第三方服务商。密钥只保存在本机，不会回显原文。</small>
      <div class="svc-footer">
        <span v-if="missing.length" class="svc-missing" role="status">还缺：{{ missing.join('、') }}</span>
        <button type="button" :disabled="!!busy" @click="closeEditor()">取消</button>
        <button v-if="model?.source === 'image_compatible'" type="button" :disabled="!!busy || missingForReuse.length > 0 || draft.api_keys.some((k) => k.trim())" :title="draft.api_keys.some((k) => k.trim()) ? '已填写新 Key 时不能同时复用图像账号' : ''" @click="save(true)">保存并复用同站点图像账号</button>
        <button type="submit" class="primary" :disabled="!!busy || missing.length > 0">{{ busy === 'save' ? '保存中…' : '保存视频接口配置' }}</button>
        <span class="svc-footer-note">保存后用于之后新提交的视频任务；已提交或已生成的视频不受影响。</span>
      </div>
      <p v-if="msg" class="svc-status" role="status">{{ msg }}</p>
      <p v-if="err" class="svc-error" role="alert">{{ err }}</p>
    </form>
    <div v-else class="svc-aside-idle">
      <h4>编辑区</h4>
      <p>点左侧「编辑配置」，在这里修改。改动先存为草稿，保存前不会影响任何任务。</p>
      <ul>
        <li>同一站点：新填写的 Key 会追加为并行 Key，已保存的 Key 保留。</li>
        <li>更换到新站点：必须填写新 Key，旧 Key 不会发送到新站点，会被替换。</li>
        <li>保存后只影响之后新提交的视频任务，已提交或已生成的视频不变。</li>
      </ul>
    </div>
    </aside>
    </div>
  </section>
</template>
