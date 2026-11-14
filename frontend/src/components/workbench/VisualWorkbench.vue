<script setup>
// 画面与字幕工作台：总配音 + 画面时间线（吸顶）/ 画面列表 / 当前画面与操作 / 本画面字幕与覆盖范围。
// 所有动作调用 useWorkspace 原有函数（重绘、替换、撤回、设为原图、字幕草稿与保存、拆分、分界、隐藏、插入画面、时序调整）。
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import OperationStatus from '../OperationStatus.vue'
import TaskConsole from '../TaskConsole.vue'
import GuidedStage from './GuidedStage.vue'
import { fmtTime, trackStickyHeight } from './format.js'

const ws = inject('ocv')
const items = computed(() => ws.visualEditor?.items || [])
const scenes = computed(() => ws.studioSceneAssets || [])
const current = computed(() => ws.studioSelectedImage)
const position = computed(() => items.value.findIndex(i => i.id === current.value?.id))
const total = computed(() => Number(items.value.at(-1)?.timing?.end || 0))
const ttsRunning = computed(() => ws.ttsEditor?.task?.status === 'running')
const busy = (item) => item?.task?.status === 'running'
const imageTasks = computed(() => items.value.filter(busy).length)
const label = (item) => item?.display_name || item?.id || ''
const number = (item) => items.value.indexOf(item) + 1
const imgSrc = (item) => item?.image_url || ''
const timing = computed(() => current.value?.timing || null)
const sentences = computed(() => timing.value?.sentences || [])
const prevItem = computed(() => items.value[position.value - 1])
const nextItem = computed(() => items.value[position.value + 1])
const share = (item) => { const t = item?.timing; return t && total.value ? (Number(t.end) - Number(t.start)) / total.value * 100 : 0 }
const subtitleDirty = (item) => (item?.timing?.sentences || []).some(s => ws.isVisualSubtitleModified(s))
const activeSentence = ref(null)
watch(() => current.value?.id, () => { activeSentence.value = sentences.value[0]?.slide_id || null }, { immediate: true })

// ---- narration + follow ----
const master = ref(null), track = ref(null), list = ref(null)
const playing = ref(false), now = ref(0), duration = ref(0), scrubbing = ref(false), playingId = ref(null), playingSentence = ref(null)
const length = computed(() => duration.value || total.value)
let stopAt = null
function locate(t) {
  for (const item of items.value) {
    const tm = item.timing
    if (tm && t >= Number(tm.start) && t < Number(tm.end)) return { item: item.id, sentence: (tm.sentences || []).find(s => t >= Number(s.start) && t < Number(s.end))?.slide_id || null }
  }
  return null
}
function follow(e) {
  const a = e.currentTarget
  now.value = a.currentTime
  if (stopAt != null && a.currentTime >= stopAt) { stopAt = null; a.pause() }
  const seek = e.type === 'seeking' || e.type === 'seeked'
  if (!seek && (a.paused || a.ended)) return
  const hit = locate(a.currentTime)
  if (!hit) return
  if (hit.item !== playingId.value || seek || e.type === 'play') {
    ws.visualTimingSelectedId = hit.item
    nextTick(() => list.value?.querySelector(`[data-picture="${hit.item}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' }))
  }
  playingId.value = hit.item
  playingSentence.value = hit.sentence
}
function toggleMaster() { const a = master.value; if (!a) return; stopAt = null; a.paused ? a.play().catch(() => {}) : a.pause() }
function seekTo(t) { if (master.value) master.value.currentTime = Math.max(0, Math.min(t, Math.max(0, length.value - 0.05))) }
const pointerTime = (e) => { const r = track.value.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * length.value }
function onDown(e) { if (e.button || !master.value) return; scrubbing.value = true; stopAt = null; track.value.setPointerCapture?.(e.pointerId); seekTo(pointerTime(e)) }
function onKey(e) {
  const step = e.shiftKey ? 10 : 2
  if (e.key === 'ArrowRight') { e.preventDefault(); seekTo(now.value + step) }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); seekTo(now.value - step) }
  else if (e.key === ' ') { e.preventDefault(); toggleMaster() }
}
// 时间线按「开始时间 / 总时长」精确定位，游标与画面块严格对齐。
function spanStyle(start, end) { const total = length.value || 1; return { left: `calc(${start / total * 100}% + 1.5px)`, width: `max(3px, calc(${Math.max(0, end - start) / total * 100}% - 3px))` } }
function playRange(start, end) {
  const a = master.value
  if (!a) return
  seekTo(Number(start) + 0.01)
  stopAt = Number(end)
  if (a.paused) a.play().catch(() => {})
}

// ---- selection ----
function select(id, focus = false) {
  ws.visualTimingSelectedId = id
  nextTick(() => {
    const row = list.value?.querySelector(`[data-picture="${id}"]`)
    row?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    if (focus) row?.querySelector('button')?.focus()
  })
}
function onListKey(e) {
  if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight'].includes(e.key)) return
  e.preventDefault()
  const all = [...scenes.value, ...items.value]
  const i = all.findIndex(x => x.id === current.value?.id)
  const n = all[i + (['ArrowDown', 'ArrowRight'].includes(e.key) ? 1 : -1)]
  if (n) select(n.id, true)
}

const root = ref(null), sticky = ref(null)
let stopTracking = () => {}
onMounted(() => { stopTracking = trackStickyHeight(root.value, sticky.value) })
onBeforeUnmount(() => stopTracking())

const referenceChips = computed(() => ws.visualReferenceOwnerMacroId === current.value?.id ? ws.visualReferenceUploads || [] : [])
const selfRef = computed(() => ws.visualSelfReferenceMacroId === current.value?.id)
const guidedCancel = computed(() => ['video', 'dynamic'].includes(ws.studioKind) && ws.isGuidedWorkflowJob(ws.studioJob) && ws.guidedHasExistingWorkflow && ws.guidedStage !== 'completed' && ws.studioJob?.status !== 'completed')
const timingLocked = computed(() => ws.visualTimingAdjusting || ttsRunning.value)
const subLocked = computed(() => ws.visualSubtitleSaving || ttsRunning.value)
const transitions = [['none', '无（默认）'], ['brush', '画笔蒙版显现'], ['layered', '分块渐入'], ['dissolve', '叠化'], ['black', '闪黑'], ['white', '闪白'], ['left', '从左移入'], ['right', '从右移入'], ['up', '从上移入'], ['down', '从下移入']]
const logsOpen = ref(false)
// Dialogs opened for a sentence outside the current picture (e.g. after a timing change) still need a place to show.
const inCurrent = (id) => sentences.value.some(s => s.slide_id === id)
const orphanPanel = computed(() => (ws.visualSubtitleRemoveDialog.open && !inCurrent(ws.visualSubtitleRemoveDialog.sentence?.slide_id)) || (ws.visualSubtitleSplitDialog.open && !inCurrent(ws.visualSubtitleSplitDialog.sentence?.slide_id)) || (ws.visualBoundaryAlign.open && !inCurrent(ws.visualBoundaryAlign.left_slide_id)))
</script>

<template>
  <div ref="root" class="wb vw" :class="{ 'is-locked': ttsRunning }">
    <div ref="sticky" class="wb-sticky">
      <section class="wb-transport vw-transport" aria-label="总配音与画面时间线">
        <button class="wb-play" type="button" :disabled="!ws.studioAudio" :aria-label="playing ? '暂停总配音' : '播放总配音'" @click="toggleMaster">
          <svg v-if="!playing" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" /></svg>
          <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" /></svg>
        </button>
        <div class="wb-clock"><b>{{ fmtTime(now) }}</b><span>/ {{ fmtTime(length) }}</span></div>
        <div ref="track" class="wb-track vw-track" role="slider" tabindex="0" aria-label="总配音进度，每块是一张画面（左右方向键快进快退，空格播放暂停）"
          :aria-valuemin="0" :aria-valuemax="Math.round(length)" :aria-valuenow="Math.round(now)" :aria-valuetext="fmtTime(now)"
          @pointerdown="onDown" @pointermove="scrubbing && seekTo(pointerTime($event))" @pointerup="scrubbing = false" @pointercancel="scrubbing = false" @keydown="onKey">
          <span v-for="item in items" :key="item.id" class="vw-span" :style="spanStyle(Number(item.timing?.start || 0), Number(item.timing?.end || 0))"
            :class="{ 'is-current': current?.id === item.id, 'is-playing': playing && playingId === item.id, 'is-busy': busy(item), 'is-review': item.audio_timing_adjustment?.needs_review }">
            <img :src="imgSrc(item)" alt="" draggable="false" loading="lazy" />
            <i v-for="s in (item.timing?.sentences || []).slice(1)" :key="s.slide_id" class="vw-tick" :style="{ left: ((s.start - item.timing.start) / Math.max(0.01, item.timing.end - item.timing.start) * 100) + '%' }" />
          </span>
          <i class="wb-playhead" :style="{ left: (length ? now / length * 100 : 0) + '%' }" />
        </div>
        <label class="vw-entrance" title="仅图片入场，默认 0.8 秒；下次合成时生效，不重新出图">
          <span>入场转场</span>
          <select v-model="ws.visualPresentation.image_transition" :disabled="ws.visualEditor.task?.status === 'running'" aria-label="图文视频入场转场">
            <option v-for="t in transitions" :key="t[0]" :value="t[0]">{{ t[1] }}</option>
          </select>
        </label>
        <audio v-if="ws.studioAudio" ref="master" :key="ws.studioAudio" :src="ws.studioAudio" preload="metadata" class="wb-audio"
          @loadedmetadata="duration = Number.isFinite($event.target.duration) ? $event.target.duration : 0"
          @play="playing = true; follow($event)" @pause="playing = false" @ended="playing = false" @timeupdate="follow" @seeking="follow" @seeked="follow" />
      </section>
      <GuidedStage compact class="wb-stage-strip">
        <template #note><span v-if="ws.visualSubtitleDirtyCount" class="wb-warn">还有 {{ ws.visualSubtitleDirtyCount }} 处字幕未保存</span></template>
      </GuidedStage>
    </div>

    <section v-if="ttsRunning" class="wb-task is-running" role="status"><div class="wb-task-head"><b>配音精修正在进行</b></div><small>句子时长会变化，画面时序与字幕编辑暂时锁定，完成后自动刷新。图片和提示词仍可查看。</small></section>
    <section v-else-if="ws.visualEditor.has_active_image_tasks || ws.visualEditor.task?.status === 'running' || ws.visualEditor.task?.status === 'failed'" class="wb-task" :class="ws.visualEditor.task?.status === 'failed' ? 'is-failed' : 'is-running'">
      <div class="wb-task-head"><b>{{ ws.visualEditor.has_active_image_tasks ? `${imageTasks} 张图片正在后台处理` : ws.visualEditor.task?.status === 'failed' ? '画面处理没有完成' : '画面处理中' }}</b><button type="button" class="wb-link" @click="ws.openLogs()">查看详细日志</button></div>
      <small>{{ ws.visualEditor.has_active_image_tasks ? '可以切换画面继续编辑其他图片。' : ws.visualEditor.task?.message }}</small>
    </section>

    <div v-if="!items.length" class="wb-empty">
      <b>还没有画面</b>
      <p>{{ ws.visualEditor.task?.message || '画面生成后，将在这里显示可编辑内容。' }}</p>
      <GuidedStage />
    </div>

    <div v-else class="wb-body vw-body">
      <aside class="wb-list vw-list" aria-label="画面列表">
        <header class="vw-list-head"><b>画面</b><span>{{ items.length }} 张 · {{ fmtTime(total) }}</span></header>
        <div ref="list" class="vw-rows" @keydown="onListKey">
          <div v-for="scene in scenes" :key="scene.id" class="vw-row is-scene" :data-picture="scene.id" :class="{ 'is-current': current?.id === scene.id }">
            <button type="button" :aria-current="current?.id === scene.id ? 'true' : undefined" @click="select(scene.id)">
              <span class="vw-thumb"><img :src="scene.image_url" alt="" loading="lazy" /></span>
              <span class="vw-row-meta"><b>{{ scene.display_name }}</b><small>参考资产 · 不占时长</small></span>
            </button>
          </div>
          <div v-for="item in items" :key="item.id" class="vw-row" :data-picture="item.id" :class="{ 'is-current': current?.id === item.id, 'is-playing': playing && playingId === item.id }">
            <button type="button" :aria-current="current?.id === item.id ? 'true' : undefined" @click="select(item.id)">
              <span class="vw-thumb"><img :src="imgSrc(item)" alt="" loading="lazy" /><em v-if="busy(item)">处理中</em><em v-else-if="item.placeholder" class="t-warn">待生成</em><em v-else-if="item.task?.status === 'failed'" class="t-bad">失败</em></span>
              <span class="vw-row-meta">
                <b>画面 {{ number(item) }}<i v-if="playing && playingId === item.id" class="wb-live" /></b>
                <small>{{ ws.formatTimingRange(item.timing) }} · {{ item.timing?.sentences?.length || 0 }} 句</small>
                <em v-if="item.audio_timing_adjustment?.needs_review" class="t-warn">配音已更新 · 待核对</em>
                <em v-else-if="subtitleDirty(item)" class="t-warn">字幕未保存</em>
              </span>
            </button>
          </div>
        </div>
      </aside>

      <section v-if="current" class="vw-main" aria-label="当前画面">
        <header class="wb-head">
          <div class="wb-nav">
            <button type="button" class="ghost-btn" :disabled="!ws.canSelectPreviousImage" aria-label="上一张画面" @click="ws.selectAdjacentVisualImage(-1)">←</button>
            <button type="button" class="ghost-btn" :disabled="!ws.canSelectNextImage" aria-label="下一张画面" @click="ws.selectAdjacentVisualImage(1)">→</button>
          </div>
          <div class="wb-where">
            <template v-if="current.is_scene || position < 0"><b>{{ current.display_name || label(current) }}</b><small>参考资产 · 不占时长</small></template>
            <template v-else><b>画面 {{ position + 1 }}</b><span>/ {{ items.length }}</span><small>{{ current.id }} · {{ ws.formatTimingRange(current.timing) }} · {{ Number(current.timing?.duration || 0).toFixed(1) }} 秒</small></template>
          </div>
          <button v-if="current.timing" type="button" class="ghost-btn" :disabled="!ws.studioAudio" @click="playRange(current.timing.start, current.timing.end)">▶ 播放这张画面</button>
        </header>

        <p v-if="current.audio_timing_adjustment?.needs_review" class="wb-note t-warn-box">配音已更新：{{ Number(current.audio_timing_adjustment.previous_duration).toFixed(2) }} 秒 → {{ Number(current.audio_timing_adjustment.target_duration).toFixed(2) }} 秒。画面已按最终配音时间轴同步，请核对{{ current.audio_timing_adjustment.text_changed ? '内容与画面含义' : '停留时长' }}；原图保留。</p>
        <OperationStatus v-if="current.task && current.task.status && current.task.status !== 'idle'" title="本图运行状态" :task="current.task" />

        <div class="vw-stage-img">
          <button type="button" class="vw-img" title="点击放大" @click="ws.visualPreviewItem = current">
            <img :src="imgSrc(current)" :alt="label(current)" />
            <em v-if="busy(current)" class="vw-img-state">{{ current.task?.action === 'upload' ? '替换中…' : '重绘中…' }}</em>
            <em v-else-if="current.placeholder" class="vw-img-state">待生成 · 填写提示词后重绘，或替换为本地图片</em>
          </button>
          <button type="button" class="vw-arrow is-prev" :disabled="!ws.canSelectPreviousImage" aria-label="上一张画面" @click="ws.selectAdjacentVisualImage(-1)"><svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7" /></svg></button>
          <button type="button" class="vw-arrow is-next" :disabled="!ws.canSelectNextImage" aria-label="下一张画面" @click="ws.selectAdjacentVisualImage(1)"><svg viewBox="0 0 24 24"><path d="M9 5l7 7-7 7" /></svg></button>
        </div>

        <div class="vw-actions">
          <div class="vw-group">
            <span class="vw-group-label">重绘</span>
            <label class="vw-res" title="仅用于重绘本图，不改变接口服务中的全局分辨率"><span>分辨率</span>
              <select :value="current.redraw_resolution ?? ''" :disabled="busy(current)" :aria-label="current.id + ' 重绘分辨率'" @change="current.redraw_resolution = $event.target.value">
                <option value="">跟随全局</option><option value="1k">1K</option><option value="2k">2K</option><option value="4k">4K</option>
              </select>
            </label>
            <button type="button" class="primary-btn" :disabled="busy(current)" @click="ws.redrawVisualImage(current, current.redraw_resolution || null)">{{ busy(current) ? '重绘中…' : '按提示词重绘' }}</button>
          </div>
          <div class="vw-group">
            <span class="vw-group-label">图片</span>
            <label class="ghost-btn vw-file">替换为本地图片<input type="file" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" :disabled="busy(current)" @change="ws.uploadVisualImage($event, current)" /></label>
            <button type="button" class="ghost-btn" :disabled="busy(current)" @click="ws.undoVisualImage(current)">撤回上一张</button>
            <button v-if="!current.is_scene" type="button" class="ghost-btn" :disabled="busy(current)" title="将当前图片和提示词确认为新的原图" @click="ws.commitVisualBaseline(current)">设为原图</button>
          </div>
        </div>

        <div class="vw-cards">
          <section class="wb-card vw-prompt">
            <header><b>{{ current.is_scene ? '场景提示词' : '提示词' }}</b><small>重绘时使用；修改后点「按提示词重绘」</small><button v-if="!current.is_scene" type="button" class="wb-link" :disabled="busy(current)" @click="ws.resetVisualImagePrompt(current)">重置提示词</button></header>
            <textarea v-model="current.prompt" rows="4" maxlength="12000" :disabled="busy(current)" />
            <p v-if="current.is_scene" class="wb-muted">场景说明：{{ current.text }}</p>
          </section>
          <section class="wb-card vw-refs">
            <header><b>重绘参考</b><small>最多 3 张本地图，可再加当前画面作为图 1</small></header>
            <div class="vw-ref-actions">
              <button type="button" class="ghost-btn" :class="{ 'is-on': selfRef }" :aria-pressed="selfRef" :disabled="busy(current)" @click="ws.toggleVisualSelfReferenceImage(current.id)">{{ selfRef ? '✓ 当前画面已作为图 1' : '用当前画面作为图 1' }}</button>
              <label class="ghost-btn vw-file">上传参考图<input type="file" multiple accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" @change="ws.uploadVisualReferenceImages($event, current.id)" /></label>
            </div>
            <div v-if="ws.visualReferenceOwnerMacroId === current.id && (ws.visualSelfReferenceMacroId || referenceChips.length)" class="vw-chips">
              <span v-if="selfRef" class="vw-chip">图 1 · 当前画面<button type="button" aria-label="取消当前画面参考" @click="ws.toggleVisualSelfReferenceImage(current.id)">×</button></span>
              <span v-for="(asset, n) in referenceChips" :key="asset.id" class="vw-chip" :title="asset.name">图 {{ n + (selfRef ? 2 : 1) }} · {{ asset.name }}<button type="button" :aria-label="'移除 ' + asset.name" @click="ws.clearVisualReferenceImages(n)">×</button></span>
              <button type="button" class="wb-link" @click="ws.clearVisualReferenceImages()">清空全部</button>
            </div>
            <template v-if="current.scene_reference_url">
              <label class="vw-check"><input v-model="current.use_scene_reference" type="checkbox" />使用场景参考 · {{ current.scene_reference_name }}</label>
              <p class="wb-muted">取消勾选仅影响本次重绘；上传新的重绘参考图可替代原参考组合，不影响其他镜头。<button type="button" class="wb-link" @click="ws.visualPreviewItem = { ...current, image_url: current.scene_reference_url }">查看场景图</button></p>
            </template>
            <div v-if="current.reference_materials?.length" class="vw-materials">
              <small>本图使用的参考素材 · 按提交顺序</small>
              <button v-for="(reference, index) in current.reference_materials" :key="reference.label" type="button" :title="reference.description" @click="ws.visualPreviewItem = { id: reference.label, image_url: reference.image_url }"><img :src="reference.image_url" :alt="reference.label" /><span>图{{ index + 1 }} · {{ reference.description || reference.label }}</span></button>
            </div>
            <p v-if="!current.reference_materials?.length && !current.scene_reference_url" class="wb-muted">本图没有记录参考素材，按提示词生成；旧项目可能未保存参考记录。</p>
          </section>
        </div>
        <details class="vw-logs" :open="logsOpen" @toggle="logsOpen = $event.target.open">
          <summary>任务日志 · {{ ws.studioTaskLogs.length }} 条</summary>
          <TaskConsole v-if="logsOpen" :lines="ws.studioTaskLogs" compact :diagnostic-available="!!ws.studioJob" :diagnostic-exporting="ws.diagnosticExporting" @export-diagnostic="ws.exportDiagnosticPackage(ws.studioJob)" />
          <p v-if="ws.diagnosticMessage" class="wb-muted" role="status">{{ ws.diagnosticMessage }}</p>
        </details>
      </section>

      <aside v-if="current" class="wb-side vw-side" aria-label="字幕与画面时序">
        <GuidedStage class="wb-stage-side">
          <template #note><p v-if="ws.visualSubtitleDirtyCount" class="wb-warn">还有 {{ ws.visualSubtitleDirtyCount }} 处字幕未保存。</p></template>
        </GuidedStage>

        <section v-if="!current.is_scene && timing" class="wb-card vw-subs" :class="{ 'is-locked': ttsRunning }">
          <header>
            <b>这张画面的字幕</b><small>{{ sentences.length }} 句 · 点选一句显示操作</small>
            <span class="vw-save">
              <em v-if="ws.visualSubtitleDirtyCount" class="t-warn">{{ ws.visualSubtitleDirtyCount }} 处未保存</em>
              <button type="button" class="primary-btn" :disabled="!ws.visualSubtitleDirtyCount || subLocked" @click="ws.saveVisualSubtitles">{{ ws.visualSubtitleSaving ? '保存中…' : '保存字幕修改' }}</button>
            </span>
          </header>
          <p v-if="orphanPanel" class="wb-note t-warn-box">有一个字幕编辑窗口属于其他画面，请在左侧切到对应画面继续，或<button type="button" class="wb-link" @click="ws.closeVisualSubtitleRemove(); ws.closeVisualSubtitleSplit(); ws.closeVisualBoundaryAlign()">关闭它</button>。</p>
          <p v-if="!ws.visualEditor.timing_available" class="wb-muted">{{ ws.visualEditor.timing_message || '该历史项目缺少可用的字幕时间线。' }}</p>
          <ol v-else class="vw-sentences">
            <li v-for="s in sentences" :key="s.slide_id" :tabindex="activeSentence === s.slide_id ? -1 : 0" :aria-current="activeSentence === s.slide_id ? 'true' : undefined"
              :class="{ 'is-active': activeSentence === s.slide_id, 'is-playing': playing && playingSentence === s.slide_id, 'is-modified': ws.isVisualSubtitleModified(s), 'is-hidden': s.subtitle_hidden }"
              @click="activeSentence = s.slide_id" @focusin="activeSentence = s.slide_id">
              <div class="vw-s-meta"><button type="button" class="vw-s-play" :disabled="!ws.studioAudio" :aria-label="'试听 ' + s.slide_id" title="试听这句" @click.stop="playRange(s.start, s.end)">▶</button><small>{{ ws.formatSubtitleTiming(s) }}</small><em v-if="ws.isVisualSubtitleModified(s)" class="t-warn">已修改</em></div>
              <template v-if="s.subtitle_hidden">
                <p class="vw-s-hidden">已隐藏字幕 · {{ ws.visualSubtitleHiddenModeLabel(s.subtitle_hidden_mode) }}<small>配音、图片与画面时序仍保留</small></p>
                <div v-if="activeSentence === s.slide_id" class="vw-s-actions"><button type="button" class="wb-link" :disabled="subLocked" @click="ws.restoreHiddenVisualSubtitle(s)">恢复字幕</button></div>
              </template>
              <template v-else>
                <textarea v-if="ws.visualSubtitleEditingId === s.slide_id" v-model="ws.visualSubtitleDrafts[s.slide_id]" rows="2" maxlength="1200" :disabled="subLocked" @keydown.ctrl.enter.prevent="ws.finishVisualSubtitleEdit(s)" />
                <p v-else class="vw-s-text" @dblclick="!subLocked && ws.toggleVisualSubtitleEdit(s)">{{ ws.visualSubtitleDrafts[s.slide_id] ?? s.text }}</p>
                <small v-if="ws.visualSubtitlePaceWarning(s)" class="vw-pace">{{ ws.visualSubtitlePaceWarning(s) }}</small>
                <div v-if="activeSentence === s.slide_id || ws.visualSubtitleEditingId === s.slide_id" class="vw-s-actions">
                  <button type="button" class="wb-link" :disabled="subLocked" @click="ws.toggleVisualSubtitleEdit(s)">{{ ws.visualSubtitleEditingId === s.slide_id ? '完成' : '改字' }}</button>
                  <button v-if="ws.isVisualSubtitleModified(s)" type="button" class="wb-link" :disabled="subLocked" @click="ws.resetVisualSubtitleDraft(s)">撤销修改</button>
                  <button type="button" class="wb-link" :disabled="subLocked" title="把本句显示字幕拆成两条，配音保持不变" @click="ws.openVisualSubtitleSplit(s)">拆成两条</button>
                  <button v-if="ws.hasNextVisualSubtitle(s)" type="button" class="wb-link" :disabled="ws.visualBoundaryAlign.status === 'loading' || subLocked" title="只移动本句结束与下一句开始的共同边界" @click="ws.previewVisualSubtitleBoundary(s)">调整分界</button>
                  <button type="button" class="wb-link" :disabled="ws.visualPictureInsertBusy || timingLocked || sentences.length < 2" :title="sentences.length < 2 ? '当前画面只有一句，请先拆分字幕' : '从本句开始插入一张可编辑的黑色占位画面'" @click="ws.insertVisualPicture(s)">{{ ws.visualPictureInsertBusy ? '添加中…' : '从这句起新画面' }}</button>
                  <button type="button" class="wb-link vw-quiet-danger" :disabled="subLocked" title="从成片与 SRT 中隐藏这条字幕" @click="ws.openVisualSubtitleRemove(s)">隐藏</button>
                </div>
              </template>
              <div v-if="ws.visualSubtitleRemoveDialog.open && ws.visualSubtitleRemoveDialog.sentence?.slide_id === s.slide_id" class="vw-panel" @click.stop>
                <p>只移除成片和 SRT 中的显示文字，不删除配音、图片或画面时间线。这段时间：</p>
                <div class="vw-choice">
                  <button type="button" class="ghost-btn" :disabled="ws.visualSubtitleSaving" @click="ws.hideVisualSubtitle('blank')"><b>留空</b><small>这段时间不显示字幕</small></button>
                  <button type="button" class="ghost-btn" :disabled="ws.visualSubtitleSaving || !ws.canMergeHiddenSubtitle(s, 'previous')" @click="ws.hideVisualSubtitle('merge_previous')"><b>并入前句</b><small>前一条可见字幕延长到这里</small></button>
                  <button type="button" class="ghost-btn" :disabled="ws.visualSubtitleSaving || !ws.canMergeHiddenSubtitle(s, 'next')" @click="ws.hideVisualSubtitle('merge_next')"><b>并入后句</b><small>后一条可见字幕提前到这里</small></button>
                </div>
                <button type="button" class="wb-link" @click="ws.closeVisualSubtitleRemove">取消</button>
              </div>
              <div v-if="ws.visualSubtitleSplitDialog.open && ws.visualSubtitleSplitDialog.sentence?.slide_id === s.slide_id" class="vw-panel" @click.stop>
                <p><b>拆分显示字幕</b> · 只拆字幕与时间显示，原配音文件保持不变</p>
                <label><span>前一条字幕</span><textarea v-model="ws.visualSubtitleSplitDialog.left_text" rows="2" maxlength="1200" /></label>
                <label><span>后一条字幕</span><textarea v-model="ws.visualSubtitleSplitDialog.right_text" rows="2" maxlength="1200" /></label>
                <label class="vw-slider"><span>共同分界：{{ ws.formatTimingRange({ start: ws.visualSubtitleSplitDialog.sentence?.start, end: ws.visualSubtitleSplitDialog.boundary }) }}</span>
                  <input v-model.number="ws.visualSubtitleSplitDialog.boundary" type="range" :min="Number(ws.visualSubtitleSplitDialog.sentence?.start || 0) + 0.05" :max="Number(ws.visualSubtitleSplitDialog.sentence?.end || 0) - 0.05" step="0.01" /></label>
                <div class="vw-panel-actions">
                  <button type="button" class="ghost-btn" @click="ws.playVisualSubtitleSplitRange(ws.visualSubtitleSplitDialog.sentence?.start, ws.visualSubtitleSplitDialog.boundary)">▶ 前句</button>
                  <button type="button" class="ghost-btn" @click="ws.playVisualSubtitleSplitRange(ws.visualSubtitleSplitDialog.boundary, ws.visualSubtitleSplitDialog.sentence?.end)">▶ 后句</button>
                  <button type="button" class="ghost-btn" @click="ws.playVisualSubtitleSplitRange(ws.visualSubtitleSplitDialog.sentence?.start, ws.visualSubtitleSplitDialog.sentence?.end)">▶ 连续</button>
                  <button type="button" class="ghost-btn" :disabled="Math.abs(Number(ws.visualSubtitleSplitDialog.boundary) - Number(ws.visualSubtitleSplitDialog.original_boundary)) < 0.005" @click="ws.visualSubtitleSplitDialog.boundary = Number(ws.visualSubtitleSplitDialog.original_boundary)">恢复边界</button>
                </div>
                <div class="vw-panel-actions"><button type="button" class="wb-link" @click="ws.closeVisualSubtitleSplit">取消</button><button type="button" class="primary-btn" :disabled="ws.visualSubtitleSaving" @click="ws.applyVisualSubtitleSplit">{{ ws.visualSubtitleSaving ? '保存中…' : '确认拆分' }}</button></div>
              </div>
              <div v-if="ws.visualBoundaryAlign.open && ws.visualBoundaryAlign.left_slide_id === s.slide_id" class="vw-panel" @click.stop>
                <p><b>与下一句的时间分界</b> · {{ ws.visualBoundaryAlign.left_slide_id }} → {{ ws.visualBoundaryAlign.right_slide_id }}</p>
                <p v-if="ws.visualBoundaryAlign.status === 'loading'" class="wb-muted">正在读取相邻两句的音频边界…</p>
                <p v-else-if="ws.visualBoundaryAlign.status === 'failed'" class="t-bad-box wb-note">{{ ws.visualBoundaryAlign.message || '读取字幕边界失败' }}</p>
                <template v-else-if="ws.visualBoundaryAlign.status === 'ready'">
                  <div class="vw-joint"><span>{{ ws.visualBoundaryAlign.left_text }}</span><i>｜</i><span>{{ ws.visualBoundaryAlign.right_text }}</span></div>
                  <label class="vw-slider"><span>共同边界：{{ ws.formatBoundaryOffset(ws.visualBoundaryAlign.boundary, ws.visualBoundaryAlign.pair_start) }} · {{ ws.formatBoundaryDelta(ws.visualBoundaryAlign.boundary, ws.visualBoundaryAlign.current_boundary) }}</span>
                    <input v-model.number="ws.visualBoundaryAlign.boundary" type="range" :min="ws.visualBoundaryAlign.minimum_boundary" :max="ws.visualBoundaryAlign.maximum_boundary" step="0.01" /></label>
                  <small class="wb-muted">只会移动这两句的共享分界：前句结束与后句开始同步变化；后句结束、下一张画面和更后面的内容保持不变。</small>
                  <div class="vw-panel-actions">
                    <button type="button" class="ghost-btn" @click="ws.playVisualBoundaryRange(ws.visualBoundaryAlign.pair_start, ws.visualBoundaryAlign.boundary)">▶ 前句</button>
                    <button type="button" class="ghost-btn" @click="ws.playVisualBoundaryRange(ws.visualBoundaryAlign.boundary, ws.visualBoundaryAlign.pair_end)">▶ 后句</button>
                    <button type="button" class="ghost-btn" @click="ws.playVisualBoundaryRange(ws.visualBoundaryAlign.clip_start, ws.visualBoundaryAlign.clip_end)">▶ 连续</button>
                    <button type="button" class="ghost-btn" :disabled="Math.abs(Number(ws.visualBoundaryAlign.boundary) - Number(ws.visualBoundaryAlign.current_boundary)) < 0.005" @click="ws.visualBoundaryAlign.boundary = Number(ws.visualBoundaryAlign.current_boundary)">恢复原边界</button>
                  </div>
                </template>
                <div class="vw-panel-actions"><button type="button" class="wb-link" @click="ws.closeVisualBoundaryAlign">关闭</button><button v-if="ws.visualBoundaryAlign.status === 'ready'" type="button" class="primary-btn" :disabled="ws.visualBoundaryApplying" @click="ws.applyVisualSubtitleBoundary">{{ ws.visualBoundaryApplying ? '应用中…' : '确认应用边界' }}</button></div>
              </div>
            </li>
          </ol>
        </section>

        <section v-if="!current.is_scene && timing && ws.visualEditor.timing_available" class="wb-card vw-range" :class="{ 'is-locked': ttsRunning }">
          <header><b>画面覆盖范围</b><small>把一句字幕移到相邻画面，立即生效，可「恢复初始时序」</small></header>
          <div class="vw-range-map" aria-hidden="true">
            <span v-if="prevItem" :style="{ flexGrow: share(prevItem) }">画面 {{ position }}</span>
            <span class="is-this" :style="{ flexGrow: share(current) }">画面 {{ position + 1 }} · {{ sentences.length }} 句</span>
            <span v-if="nextItem" :style="{ flexGrow: share(nextItem) }">画面 {{ position + 2 }}</span>
          </div>
          <div class="vw-range-grid">
            <div><span>开头</span>
              <button type="button" class="ghost-btn" :disabled="!timing.can_extend_prev || timingLocked" title="把上一张画面的最后一句并入这张" @click="ws.adjustEditedTiming('extend_prev')">提前一句</button>
              <button type="button" class="ghost-btn" :disabled="!timing.can_shrink_prev || timingLocked" title="把这张的第一句交给上一张" @click="ws.adjustEditedTiming('shrink_prev')">推后一句</button>
            </div>
            <div><span>结尾</span>
              <button type="button" class="ghost-btn" :disabled="!timing.can_shrink_next || timingLocked" title="把这张的最后一句交给下一张" @click="ws.adjustEditedTiming('shrink_next')">提前一句</button>
              <button type="button" class="ghost-btn" :disabled="!timing.can_extend_next || timingLocked" title="把下一张画面的第一句并入这张" @click="ws.adjustEditedTiming('extend_next')">推后一句</button>
            </div>
          </div>
          <footer class="vw-history">
            <button type="button" class="ghost-btn" :disabled="!ws.visualEditor.timing_available || timingLocked" @click="ws.resetEditedTiming">恢复初始时序</button>
            <button type="button" class="ghost-btn" :disabled="!ws.visualEditor.timing_available || timingLocked" title="把现在的分配保存为新的初始时序，旧基准归档保留" @click="ws.commitEditedTiming">设为新的初始时序</button>
            <select v-model="ws.selectedVisualTimingHistory" :disabled="timingLocked || !ws.visualEditor.timing_history?.length" aria-label="历史时序" @change="ws.restoreSelectedVisualTimingHistory">
              <option value="">历史时序</option><option v-for="entry in ws.visualEditor.timing_history || []" :key="entry.id" :value="entry.id">{{ entry.label }}</option>
            </select>
            <select v-model="ws.selectedVisualSubtitleHistory" :disabled="subLocked || !ws.visualEditor.subtitle_history?.length" aria-label="字幕历史" @change="ws.restoreSelectedVisualSubtitleHistory">
              <option value="">字幕历史</option><option v-for="entry in ws.visualEditor.subtitle_history || []" :key="entry.id" :value="entry.id">{{ entry.label }}</option>
            </select>
            <button type="button" class="ghost-btn" :disabled="!ws.ttsEditor.history_count || ws.ttsBoundaryBusy" @click="ws.undoLastTtsEdit">撤销上一步 · {{ ws.ttsEditor.history_count || 0 }}/{{ ws.ttsEditor.history_limit || 20 }}</button>
          </footer>
        </section>
        <div class="wb-corner">
          <button v-if="!current.is_scene && timing" type="button" class="wb-corner-btn" :disabled="items.length <= 1 || timingLocked" @click="ws.removeEditedTimingPicture">移除这张画面</button>
          <button v-if="guidedCancel" type="button" class="wb-corner-btn" :disabled="ws.guidedCancelling" @click="ws.cancelGuidedWorkflow">{{ ws.guidedCancelling ? '正在取消…' : '取消本次分步任务' }}</button>
        </div>
      </aside>
    </div>
  </div>
</template>

<style src="./workbench.css"></style>
<style scoped>
.vw-transport{grid-template-columns:auto auto minmax(0,1fr) auto}
.vw-track{display:block;height:46px}
.vw-span{position:absolute;top:0;bottom:0;border-radius:6px;overflow:hidden;opacity:.55;outline:2px solid transparent;outline-offset:-2px;transition:opacity .15s}
.vw-span img{width:100%;height:100%;object-fit:cover;display:block;pointer-events:none}
.vw-span.is-current{opacity:1;outline-color:var(--ocv-accent)}.vw-span.is-playing{opacity:1}
.vw-span.is-review{box-shadow:inset 0 -4px 0 var(--ocv-warning)}.vw-span.is-busy{animation:vw-blink 1.4s infinite}
@keyframes vw-blink{50%{opacity:.3}}
.vw-tick{position:absolute;top:0;bottom:0;width:1px;background:#fff8;pointer-events:none}
.vw-entrance{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--ocv-muted);white-space:nowrap}.vw-entrance select{padding:7px 9px!important;font-size:12px!important;max-width:150px}
.vw-list-head{display:flex;gap:8px;align-items:baseline;padding:14px 14px 8px}.vw-list-head span{font-size:12px;color:var(--ocv-muted)}
.vw-rows{display:flex;gap:8px;overflow:auto;padding:4px 10px 12px}
.vw-row{flex:none;width:170px}
.vw-row>button{display:grid;gap:8px;width:100%;padding:8px!important;border-radius:10px!important;border:1px solid transparent!important;background:none!important;color:inherit;text-align:left;cursor:pointer;min-height:0;white-space:normal;box-shadow:none!important}
.vw-row>button:hover{background:var(--wb-soft)!important}
.vw-row.is-current>button{background:var(--wb-accent-soft)!important;border-color:color-mix(in srgb,var(--ocv-accent) 55%,transparent)!important}
.vw-thumb{position:relative;display:block;aspect-ratio:16/9;border-radius:7px;overflow:hidden;background:#0006}.vw-thumb img{width:100%;height:100%;object-fit:cover;display:block}
.vw-thumb em{position:absolute;left:6px;top:6px;background:#000a!important;color:#fff!important}
.vw-row-meta{display:grid;gap:3px;min-width:0}.vw-row-meta b{font-size:13px;display:flex;align-items:center;gap:6px}.vw-row-meta small{font-size:11px;color:var(--ocv-muted);font-variant-numeric:tabular-nums}.vw-row-meta em{justify-self:start}
.vw-main{display:grid;gap:14px;min-width:0}
.vw-stage-img{position:relative;width:min(100%,calc((100vh - 640px) * 16 / 9));min-width:min(100%,520px);margin:0 auto}
.vw-img{display:block;width:100%;padding:0!important;border:0!important;border-radius:14px!important;overflow:hidden;background:#0008!important;cursor:zoom-in;aspect-ratio:16/9;position:relative}
.vw-img img{width:100%;height:100%;object-fit:contain;display:block}
.vw-img-state{position:absolute;left:14px;bottom:14px;background:#000b!important;color:#fff!important;font-size:13px!important;padding:6px 12px!important}
.vw-arrow{position:absolute;top:50%;transform:translateY(-50%);width:42px;height:42px;border-radius:50%!important;padding:0!important;display:grid;place-items:center;border:0!important;background:#0008!important;color:#fff;cursor:pointer;opacity:0;transition:opacity .15s;min-height:0}
.vw-arrow svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
.vw-arrow.is-prev{left:12px}.vw-arrow.is-next{right:12px}.vw-arrow:disabled{display:none}
.vw-stage-img:hover .vw-arrow,.vw-arrow:focus-visible{opacity:1}
.vw-actions{display:flex;gap:10px 24px;flex-wrap:wrap;align-items:center;padding:10px 12px;border-radius:12px;background:var(--wb-soft)}
.vw-group{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.vw-group-label{font-size:11px;letter-spacing:.08em;color:var(--ocv-muted);white-space:nowrap}
.vw-res{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--ocv-muted);white-space:nowrap}.vw-res select{padding:7px 9px!important;font-size:12px!important}
.vw-actions .primary-btn,.vw-actions .ghost-btn{min-height:34px;padding:7px 12px;font-size:12px}
.vw-file{position:relative;display:inline-flex;font-weight:400!important;align-items:center;cursor:pointer;border-radius:8px;padding:7px 12px;font-size:12px;min-height:34px;box-sizing:border-box}
.vw-file input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%}
.vw-cards{display:grid;grid-template-columns:1fr;gap:14px}
.vw-prompt textarea{font-size:14px}
.vw-ref-actions{display:flex;gap:8px;flex-wrap:wrap}.vw-ref-actions .ghost-btn{font-size:12px;min-height:32px;padding:6px 11px}
.vw-chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:10px}
.vw-chip{display:inline-flex;align-items:center;gap:6px;padding:4px 6px 4px 10px;border-radius:999px;font-size:12px;background:var(--wb-accent-soft);border:1px solid color-mix(in srgb,var(--ocv-accent) 35%,transparent);max-width:100%;overflow:hidden;text-overflow:ellipsis}
.vw-chip button{border:0!important;background:none!important;color:var(--ocv-muted);padding:0 4px!important;min-height:0;cursor:pointer}
.vw-check{display:flex;gap:8px;align-items:center;font-size:13px;margin-top:10px}
.vw-materials{display:grid;gap:8px;margin-top:10px}.vw-materials small{color:var(--ocv-muted);font-size:11px}
.vw-materials button{display:flex;gap:10px;align-items:center;text-align:left;white-space:normal;padding:6px!important;border-radius:8px!important;font-size:12px}
.vw-materials img{width:84px;height:56px;object-fit:contain;border-radius:6px;background:#0006;flex:none}
.vw-logs{border:1px solid var(--wb-line);border-radius:12px;padding:10px 14px;background:var(--ocv-panel)}.vw-logs summary{cursor:pointer;font-size:13px;color:var(--ocv-muted)}
.vw-logs[open] summary{margin-bottom:10px}
.vw-save{margin-left:auto;display:flex;align-items:center;gap:8px}.vw-save .primary-btn{min-height:32px;padding:6px 12px;font-size:12px}
.vw-sentences{list-style:none;margin:0;padding:0;display:grid;gap:4px}
.vw-sentences li{padding:9px 12px;border-radius:10px;border:1px solid transparent;cursor:pointer;outline-offset:2px}
.vw-sentences li:hover{background:var(--wb-soft)}
.vw-sentences li:focus{outline:none}.vw-sentences li:focus-visible{outline:2px solid var(--ocv-accent)}
.vw-sentences li.is-active{background:var(--wb-soft);border-color:var(--wb-line);cursor:default}
.vw-sentences li.is-playing{border-color:color-mix(in srgb,var(--ocv-accent) 60%,transparent)}
.vw-sentences li.is-modified{border-color:color-mix(in srgb,var(--ocv-warning) 50%,transparent)}
.vw-s-meta{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--ocv-muted);font-variant-numeric:tabular-nums}
.vw-s-play{width:22px;height:22px;min-height:0!important;padding:0!important;border-radius:50%!important;border:1px solid var(--wb-line)!important;background:none!important;color:var(--ocv-muted);font-size:9px!important;cursor:pointer;display:grid;place-items:center}
.vw-s-play:hover:not(:disabled){color:var(--ocv-accent);border-color:var(--ocv-accent)!important}
.vw-sentences li:not(.is-active) .vw-s-text{color:color-mix(in srgb,var(--ocv-text) 82%,transparent)}
.vw-s-text{margin:6px 0 4px;font-size:15px;line-height:1.7;overflow-wrap:anywhere;cursor:text}
.vw-sentences textarea{width:100%;box-sizing:border-box;margin:6px 0 4px;font-size:15px;line-height:1.7;resize:vertical;min-height:64px}
.vw-s-hidden{margin:6px 0 4px;color:var(--ocv-muted);font-size:13px;display:grid;gap:2px}.vw-s-hidden small{font-size:11px}
.vw-pace{display:block;color:var(--ocv-warning);font-size:11px;margin-bottom:4px}
.vw-s-actions{display:flex;gap:4px 14px;flex-wrap:wrap;padding-top:6px;margin-top:2px;border-top:1px solid var(--wb-line)}
.vw-quiet-danger{color:var(--ocv-muted)!important}.vw-quiet-danger:hover:not(:disabled){color:var(--ocv-danger)!important}
.vw-panel{margin-top:10px;padding:12px;border-radius:10px;border:1px solid var(--wb-line);background:var(--ocv-panel);display:grid;gap:10px;cursor:default}
.vw-panel p{margin:0;font-size:12px;color:var(--ocv-muted);line-height:1.6}.vw-panel p b{color:var(--ocv-text)}
.vw-panel label{display:grid;gap:6px;font-size:12px;color:var(--ocv-muted)}.vw-panel textarea{min-height:56px;font-size:14px}
.vw-choice{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px}.vw-choice .ghost-btn{display:grid;gap:2px;text-align:left;white-space:normal;padding:8px 10px}.vw-choice small{font-size:11px;color:var(--ocv-muted)}
.vw-slider input{padding:0!important;accent-color:var(--ocv-accent);background:none!important;border:0!important}
.vw-panel-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:space-between}.vw-panel-actions .ghost-btn{font-size:12px;padding:6px 10px;min-height:30px}.vw-panel-actions .primary-btn{font-size:12px;min-height:32px;padding:6px 14px}
.vw-joint{display:flex;gap:8px;align-items:center;font-size:13px;flex-wrap:wrap}.vw-joint span{padding:4px 8px;border-radius:7px;background:var(--wb-soft)}.vw-joint i{font-style:normal;color:var(--ocv-muted)}
.vw-range-map{display:flex;gap:3px;margin-bottom:12px;font-size:11px}
.vw-range-map span{padding:8px;border-radius:7px;background:var(--wb-soft);color:var(--ocv-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.vw-range-map span.is-this{background:var(--wb-accent-soft);color:var(--ocv-text);outline:1px solid color-mix(in srgb,var(--ocv-accent) 55%,transparent)}
.vw-range-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.vw-range-grid>div{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.vw-range-grid span{font-size:12px;color:var(--ocv-muted);width:30px}
.vw-range-grid .ghost-btn{font-size:12px;min-height:32px;padding:6px 10px}
.vw-history{display:flex;gap:6px;flex-wrap:wrap;margin-top:14px;padding-top:12px;border-top:1px dashed var(--wb-line)}
.vw-history .ghost-btn,.vw-history select{font-size:12px!important;min-height:32px;padding:6px 10px!important}
.is-locked.vw-subs,.is-locked.vw-range{opacity:.75}
@container (max-width:900px){.vw-transport{grid-template-columns:auto auto minmax(0,1fr)}.vw-track{grid-column:1/-1;order:5}.vw-entrance{justify-self:end}}
@container (min-width:900px){
  .vw-body{grid-template-columns:200px minmax(0,1fr)}
  .vw-list{position:sticky;top:var(--wb-top);height:calc(100vh - var(--wb-top) - 16px)}
  .vw-rows{flex-direction:column;overflow-y:auto;overflow-x:hidden}.vw-row{width:auto}
}
@container (min-width:1180px){.vw-cards{grid-template-columns:minmax(0,1.3fr) minmax(0,1fr)}}
@container (min-width:1320px){
  .vw-body{grid-template-columns:190px minmax(0,1fr) minmax(360px,420px)}
  .vw-side{max-height:calc(100vh - var(--wb-top) - 16px);overflow:auto;scrollbar-gutter:stable}
}
@container (min-width:1320px) and (max-width:1699px){.vw-cards{grid-template-columns:1fr}}
@container (min-width:1900px){.vw-body{grid-template-columns:220px minmax(0,1fr) 460px;gap:20px}}
@media (prefers-reduced-motion:reduce){.vw-span.is-busy{animation:none}.vw-arrow{transition:none}}
</style>
