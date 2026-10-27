<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api.js'

const props = defineProps({
  projectId: String, editor: { type: Object, required: true },
  selectedIndices: { type: Array, default: () => [] }, anchorIndex: Number,
  busy: Boolean, hasDrafts: Boolean, showUndo: Boolean,
  selectionOnly: Boolean,
})
const emit = defineEmits(['started', 'undo', 'busy'])
const mode = ref(''), working = ref(false), error = ref(''), details = ref(null)
const anchor = ref(0), position = ref('after'), text = ref(''), dialog = ref(null)
let revision = 0, selected = [], project = '', previousFocus = null
const locked = computed(() => props.busy || working.value || props.editor.task?.status === 'running')
const available = computed(() => props.editor.available && props.editor.structural_edit_available !== false && props.editor.segments?.length)
const dirtyHint = '请先保存、重配或恢复当前文字、字幕和停顿草稿，再增删片段。'

async function open(action) {
  if (locked.value || !available.value || (action === 'delete' && !props.selectedIndices.length)) return
  error.value = ''
  if (props.hasDrafts) { error.value = dirtyHint; return }
  mode.value = action
  project = props.projectId
  revision = Number(props.editor.revision || 0)
  selected = [...props.selectedIndices]
  anchor.value = props.anchorIndex || selected[0] || props.editor.segments[0].index
  position.value = 'after'
  text.value = ''
  details.value = null
  previousFocus = document.activeElement
  await nextTick()
  dialog.value?.focus()
  if (action === 'delete') {
    working.value = true
    try { details.value = await api.previewTtsStructure(project, payload()) }
    catch (e) { error.value = e.message || '无法核对删除范围' }
    finally { working.value = false }
  }
}
function payload() {
  return mode.value === 'delete'
    ? { action: 'delete', indices: selected, expected_revision: revision }
    : { action: 'insert', anchor_index: Number(anchor.value), position: position.value, text: text.value.trim(), expected_revision: revision }
}
function close() {
  if (working.value) return
  mode.value = ''
  previousFocus?.focus?.()
}
async function submit() {
  if (locked.value || props.hasDrafts) { error.value = dirtyHint; return }
  if (project !== props.projectId || revision !== Number(props.editor.revision || 0)) {
    error.value = '配音已变化，请关闭窗口后重新选择。'; return
  }
  working.value = true
  error.value = ''
  emit('busy', true)
  try {
    await api.editTtsStructure(project, payload())
    mode.value = ''
    if (project === props.projectId) emit('started')
    previousFocus?.focus?.()
  } catch (e) { error.value = e.message || '配音增删未提交，原内容保留' }
  finally {
    working.value = false
    emit('busy', false)
    await nextTick()
    if (mode.value) dialog.value?.focus()
  }
}
function keyboard(event) {
  if (event.key === 'Escape') { event.preventDefault(); close(); return }
  if (event.key !== 'Tab') return
  const elements = [...dialog.value.querySelectorAll('button:not(:disabled), input, textarea, select')]
  if (!elements.length) { event.preventDefault(); return }
  const first = elements[0], last = elements.at(-1)
  if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.value)) {
    event.preventDefault(); last.focus()
  } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog.value)) {
    event.preventDefault(); first.focus()
  }
}
watch(() => props.projectId, () => { mode.value = ''; error.value = '' })
onBeforeUnmount(() => { previousFocus = null })
</script>

<template>
  <div class="tts-structure-controls" :class="{'selection-only':selectionOnly}">
    <button v-if="!selectionOnly" type="button" class="secondary small" :disabled="locked || !available" @click="open('insert')">＋ 插入配音</button>
    <button v-if="selectionOnly" type="button" class="secondary small remove" :disabled="locked || !available || !selectedIndices.length" :title="selectedIndices.length ? '删除选中的完整配音段' : '请先勾选左侧配音段'" @click="open('delete')">删除选中<span v-if="selectedIndices.length">（{{ selectedIndices.length }}）</span></button>
    <button v-if="!selectionOnly" type="button" class="secondary small" :disabled="locked || hasDrafts || !editor.history_count" title="撤回最近一次配音编辑，包括重配、交界调整或增删；不按勾选范围撤回" @click="$emit('undo')">撤回配音</button>
    <span v-if="error && !mode" class="error" role="alert">{{ error }}</span>
  </div>
  <Teleport to="body">
    <div v-if="mode" class="tts-structure-backdrop" @click.self="close">
      <section ref="dialog" class="tts-structure-dialog" role="dialog" aria-modal="true" aria-labelledby="tts-structure-title" tabindex="-1" @keydown="keyboard">
        <header><h3 id="tts-structure-title">{{ mode === 'delete' ? '删除配音片段' : '插入新配音' }}</h3><button type="button" class="secondary small" :disabled="working" @click="close">关闭</button></header>
        <template v-if="mode === 'delete'">
          <p>将删除选中的完整配音段及对应文案、字幕，后续时间轴自动前移。不会重新配音；保留最近 20 次编辑供撤销。</p>
          <div v-if="working && !details" role="status">正在核对删除范围…</div>
          <template v-if="details">
            <p class="summary">{{ details.segments.length }} 段 · 缩短约 {{ details.removed_seconds.toFixed(2) }} 秒</p>
            <ol class="segment-summary"><li v-for="item in details.segments" :key="item.index"><b>第 {{ item.index }} 段</b><span>{{ item.text }}</span></li></ol>
            <p class="hint">删空的分镜退出时间轴；部分内容变化的画面保留并标记待核对。原素材保留。</p>
          </template>
        </template>
        <template v-else>
          <div class="insert-position">
            <label>插入位置<select v-model="anchor" :disabled="working"><option v-for="item in editor.segments" :key="item.index" :value="item.index">第 {{ item.index }} 段 · {{ String(item.text).slice(0, 32) }}</option></select></label>
            <label>前后<select v-model="position" :disabled="working"><option value="after">之后</option><option value="before">之前</option></select></label>
          </div>
          <label>新增文案<textarea v-model="text" rows="5" maxlength="1200" :disabled="working" placeholder="输入需要新增的配音内容" /></label>
          <p class="hint">沿用项目已保存的配音引擎和音色，仅生成新增内容；云端配音可能产生费用。新增画面先显示待规划占位，原画面不自动重做。</p>
        </template>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <footer><button type="button" class="secondary" :disabled="working" @click="close">取消</button><button type="button" :class="{ 'delete-confirm': mode === 'delete' }" :disabled="locked || hasDrafts || (mode === 'delete' ? !details : !text.trim())" @click="submit">{{ working ? '正在处理…' : mode === 'delete' ? '确认删除' : '生成并插入' }}</button></footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.tts-structure-controls{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:10px 0}
.tts-structure-controls.selection-only{margin:0;display:inline-flex}button.remove:disabled{color:var(--muted,#aab8b3)}
button{font:inherit;font-weight:600;border:1px solid #53666566;border-radius:9px;background:#263132;color:#e4eeee;padding:10px 14px;cursor:pointer}button:hover:not(:disabled){border-color:#82cfc0}button:focus-visible{outline:2px solid #82cfc0;outline-offset:3px}button.small{font-size:13px;padding:8px 11px}
.remove,.error{color:#f3a4a4}.error{font-size:14px}.tts-structure-backdrop{position:fixed;inset:0;z-index:15000;background:#0009;display:flex;align-items:center;justify-content:center;padding:20px}
.tts-structure-dialog{width:min(600px,100%);max-height:85vh;overflow:auto;background:var(--panel-bg,#1d272b);color:var(--text-primary,#eef4f4);border:1px solid #607977;border-radius:16px;padding:24px;box-shadow:0 20px 70px #0006;outline:none}
header,footer{display:flex;align-items:center;justify-content:space-between;gap:12px}h3{margin:0;font-size:20px}p{line-height:1.65}footer{justify-content:flex-end;margin-top:22px}footer button:last-child{background:#82d9c3;color:#102d32}footer button.delete-confirm{background:#c3585f;color:#fff}.hint{color:#aebebf;font-size:14px}.summary{font-weight:600}.segment-summary{max-height:230px;overflow:auto;padding:0;list-style:none}.segment-summary li{display:grid;gap:6px;padding:12px 0;border-bottom:1px solid #53626255}.insert-position{display:grid;grid-template-columns:minmax(0,1fr) 105px;gap:12px;margin:20px 0}label{display:grid;gap:8px;min-width:0}select,textarea{width:100%;box-sizing:border-box}textarea{resize:vertical;min-height:130px}button:disabled{opacity:.45;cursor:not-allowed}
@media(max-width:480px){.tts-structure-dialog{padding:16px}.insert-position{grid-template-columns:1fr}.tts-structure-backdrop{padding:12px}}
</style>
