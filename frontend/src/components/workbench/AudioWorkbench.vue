<script setup>
// 配音与字幕工作台：总配音条（吸顶）+ 全部句子 + 当前句 + 版本/参数/下一步。
// 所有动作调用 useWorkspace 原有函数；不改变接口、payload、确认和计费说明。
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { followNarration } from '../../narrationFollow.js'
import TtsFindReplace from '../TtsFindReplace.vue'
import TtsStructureControls from '../TtsStructureControls.vue'
import TtsRebuildButton from '../TtsRebuildButton.vue'
import TtsEditorFeedback from '../TtsEditorFeedback.vue'
import TtsBoundaryEditor from '../TtsBoundaryEditor.vue'
import GuidedStage from './GuidedStage.vue'
import { fmtTime, trackStickyHeight } from './format.js'

const ws = inject('ocv')
const editor = computed(() => ws.ttsEditor)
const segments = computed(() => editor.value?.segments || [])
const total = computed(() => { const l = segments.value.at(-1); return l ? Number(l.end) + Number(l.pause_after || 0) : 0 })
const task = computed(() => editor.value?.task || { status: 'idle' })
const running = computed(() => task.value.status === 'running')
const locked = computed(() => ws.ttsBoundaryBusy || running.value)
const selected = computed(() => ws.selectedTtsSegmentIndices || [])
const viewing = computed(() => ws.studioSentence)
const current = computed(() => segments.value.find(s => s.index === viewing.value))
const position = computed(() => segments.value.findIndex(s => s.index === viewing.value))
const next = computed(() => segments.value[position.value + 1])
const filter = ref('all')

const drafts = computed(() => ws.ttsReadingDrafts || {})
const draftOf = (s) => drafts.value[s.index] ?? s.tts_text ?? s.text ?? ''
const applied = (s) => s.tts_text ?? s.text ?? ''
const pending = (s) => s.index in drafts.value && String(drafts.value[s.index]).trim() !== String(applied(s)).trim()
const differsFromSubtitle = (s) => String(draftOf(s)).trim() !== String(s.text || '').trim()
const pronounced = (s) => !pending(s) && (s.pronunciation_modified || String(applied(s)).trim() !== String(s.text || '').trim())
const pauseOf = (s) => Number(ws.ttsPauseDrafts?.[s.index] ?? s.pause_after ?? 0)
const pausePending = (s) => s.index in (ws.ttsPauseDrafts || {}) && Number(ws.ttsPauseDrafts[s.index]) !== Number(s.pause_after || 0)
const synced = (s) => (editor.value?.subtitle_sync_indices || []).includes(s.index)
const pendingCount = computed(() => segments.value.filter(s => pending(s) || pausePending(s)).length)
const pausePendingCount = computed(() => segments.value.filter(pausePending).length)
const changed = (s) => pending(s) || pausePending(s) || pronounced(s) || s.subtitle_independent || pauseOf(s) > 0
const rows = computed(() => segments.value.filter(s => filter.value === 'selected' ? selected.value.includes(s.index) : filter.value === 'changed' ? changed(s) : true))

// ---- 总配音：只有播放或主动拖动时跟随 ----
const master = ref(null), list = ref(null), track = ref(null)
const playing = ref(false), now = ref(0), duration = ref(0), scrubbing = ref(false)
const length = computed(() => duration.value || total.value)
function follow(event) {
  now.value = event.currentTarget.currentTime
  ws.narrationPlayingIndex = followNarration(event, segments.value, ws.narrationPlayingIndex, list.value, index => { ws.studioSentence = index })
}
function toggleMaster() {
  const a = master.value
  if (!a) return
  if (a.paused) { if (ws.ttsSegmentIsPlaying) ws.toggleTtsSegmentAudio(segments.value.find(s => s.index === ws.ttsSegmentPlayingIndex) || {}); a.play().catch(() => {}) } else a.pause()
}
function seekTo(t) { if (master.value) master.value.currentTime = Math.max(0, Math.min(t, Math.max(0, length.value - 0.05))) }
const pointerTime = (e) => { const r = track.value.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * length.value }
function onDown(e) { if (e.button || !master.value) return; scrubbing.value = true; track.value.setPointerCapture?.(e.pointerId); seekTo(pointerTime(e)) }
function onMove(e) { if (scrubbing.value) seekTo(pointerTime(e)) }
function onTrackKey(e) {
  const step = e.shiftKey ? 10 : 2
  if (e.key === 'ArrowRight') { e.preventDefault(); seekTo(now.value + step) }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); seekTo(now.value - step) }
  else if (e.key === 'Home') { e.preventDefault(); seekTo(0) }
  else if (e.key === ' ') { e.preventDefault(); toggleMaster() }
}
// 时间线按「开始时间 / 总时长」精确定位，游标与色块严格对齐。
function spanStyle(start, end) { const total = length.value || 1; return { left: `calc(${start / total * 100}% + 1px)`, width: `max(2px, calc(${Math.max(0, end - start) / total * 100}% - 2px))` } }
function playFrom(s) { seekTo(Number(s.start)); if (master.value?.paused) toggleMaster() }
watch(() => ws.studioAudio, () => { playing.value = false; now.value = 0; duration.value = 0 })

// ---- 单句试听：不移动总配音；开始时暂停总配音 ----
const segActive = (s) => ws.ttsSegmentPlayingIndex === s.index
function toggleSegment(s) { if (!master.value?.paused) master.value.pause(); ws.toggleTtsSegmentAudio(s) }

// ---- 导航 ----
function view(index, focus = false) {
  ws.studioSentence = index
  nextTick(() => {
    const row = list.value?.querySelector(`[data-narration-index="${index}"]`)
    row?.scrollIntoView({ block: 'nearest' })
    if (focus) row?.querySelector('.aw-row-main')?.focus()
  })
}
function step(d) { const n = segments.value[position.value + d]; if (n) view(n.index) }
function onListKey(e) {
  if (!['ArrowDown', 'ArrowUp'].includes(e.key) || e.target.tagName === 'INPUT') return
  e.preventDefault()
  const i = rows.value.findIndex(s => s.index === viewing.value)
  const n = rows.value[Math.max(0, Math.min(rows.value.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))]
  if (n) view(n.index, true)
}
const root = ref(null), sticky = ref(null)
let stopTracking = () => {}
onMounted(() => { stopTracking = trackStickyHeight(root.value, sticky.value) })
onBeforeUnmount(() => stopTracking())
onMounted(() => { if (viewing.value != null) nextTick(() => list.value?.querySelector(`[data-narration-index="${viewing.value}"]`)?.scrollIntoView({ block: 'center' })) })

// ---- 勾选（与当前查看句互相独立） ----
function toggleSelected(index, on) {
  const list = ws.selectedTtsSegmentIndices || []
  ws.selectedTtsSegmentIndices = on ? [...new Set([...list, index])] : list.filter(i => i !== index)
}
function selectVisible() { ws.selectedTtsSegmentIndices = [...new Set([...selected.value, ...rows.value.map(s => s.index)])] }
function clearSelection() { ws.selectedTtsSegmentIndices = [] }

function setPause(s, value) { ws.ttsPauseDrafts[s.index] = value === '' ? '' : Number(value) }
const engineLabel = computed(() => ({ indextts25: 'IndexTTS-2.5', indextts2: 'IndexTTS-2', cluster: '集群 GPU', qwen: 'Qwen TTS' })[editor.value?.engine] || ws.ttsRefineEngineLabel || '配音引擎')
const settings = computed(() => editor.value?.settings || {})
const voiceLabel = computed(() => editor.value?.engine === 'qwen' ? settings.value.qwen_voice : editor.value?.engine === 'cluster' ? (settings.value.cluster_voice_id || '预设音色') : (settings.value.tts_voice_id || '项目默认'))
const tail = (t, n = 14) => { const a = Array.from(String(t || '')); return (a.length > n ? '…' : '') + a.slice(-n).join('') }
const head = (t, n = 14) => { const a = Array.from(String(t || '')); return a.slice(0, n).join('') + (a.length > n ? '…' : '') }
const guidedCancel = computed(() => ['video', 'dynamic'].includes(ws.studioKind) && ws.isGuidedWorkflowJob(ws.studioJob) && ws.guidedHasExistingWorkflow && ws.guidedStage !== 'completed' && ws.studioJob?.status !== 'completed')
const taskTitle = computed(() => running.value ? (task.value.cancel_requested ? '正在停止本次配音编辑' : '正在制作候选配音') : task.value.status === 'failed' ? '本次配音编辑没有完成' : task.value.status === 'awaiting_review' ? '新配音需要核对' : '配音编辑')
</script>

<template>
  <div ref="root" class="wb aw" :class="{ 'is-locked': running }">
    <div ref="sticky" class="wb-sticky">
      <section class="wb-transport aw-transport" aria-label="总配音">
        <button class="wb-play" type="button" :disabled="!ws.studioAudio" :aria-label="playing ? '暂停总配音' : '播放总配音'" @click="toggleMaster">
          <svg v-if="!playing" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" /></svg>
          <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" /></svg>
        </button>
        <div class="wb-clock"><b>{{ fmtTime(now) }}</b><span>/ {{ fmtTime(length) }}</span></div>
        <div class="aw-follow" aria-live="polite">
          <template v-if="!ws.studioAudio">{{ ws.studioJob?.message || '音频生成后可在此试听' }}</template>
          <template v-else-if="playing && ws.narrationPlayingIndex"><i class="wb-live" />正在播放第 {{ ws.narrationPlayingIndex }} 句 · 列表跟随</template>
          <template v-else>已暂停 · 可自由浏览，点时间线跳转</template>
        </div>
        <div ref="track" class="wb-track aw-track" role="slider" tabindex="0" aria-label="总配音进度（左右方向键快进快退，空格播放暂停）"
          :aria-valuemin="0" :aria-valuemax="Math.round(length)" :aria-valuenow="Math.round(now)" :aria-valuetext="fmtTime(now)"
          @pointerdown="onDown" @pointermove="onMove" @pointerup="scrubbing = false" @pointercancel="scrubbing = false" @keydown="onTrackKey">
          <span v-for="s in segments" :key="s.index" class="aw-block" :style="spanStyle(Number(s.start), Number(s.end) + Number(s.pause_after || 0))"
            :class="{ 'is-playing': playing && ws.narrationPlayingIndex === s.index, 'is-viewing': viewing === s.index, 'is-selected': selected.includes(s.index), 'is-pending': pending(s) || pausePending(s) }"
            :title="`第 ${s.index} 句 · ${fmtTime(s.start)}`" />
          <i class="wb-playhead" :style="{ left: (length ? now / length * 100 : 0) + '%' }" />
        </div>
        <div class="aw-tools">
          <TtsFindReplace :save-subtitles="ws.saveTtsSubtitleBatch" :project-id="ws.visualEditorProjectId" :editor="editor" :drafts="drafts" :busy="ws.ttsBoundaryBusy"
            @reading="ws.updateTtsReadingDraft" @subtitle-sync="ws.setTtsSubtitleSync" @select="ws.selectedTtsSegmentIndices = $event" />
        </div>
        <audio v-if="ws.studioAudio" ref="master" :key="ws.studioAudio" :src="ws.studioAudio" preload="metadata" class="wb-audio"
          @loadedmetadata="duration = Number.isFinite($event.target.duration) ? $event.target.duration : 0"
          @play="playing = true; follow($event)" @pause="playing = false" @ended="playing = false"
          @timeupdate="follow" @seeking="follow" @seeked="follow" />
      </section>
      <GuidedStage compact class="wb-stage-strip" />
    </div>

    <section v-if="task.status !== 'idle' && (task.message || running)" class="wb-task" :class="'is-' + (task.cancel_requested ? 'cancelling' : task.status)">
      <div class="wb-task-head">
        <b>{{ taskTitle }}</b>
        <span v-if="running && Number.isFinite(Number(task.progress))" class="wb-task-progress"><i :style="{ width: (task.progress || 0) + '%' }" /></span>
      </div>
      <TtsEditorFeedback :project-id="ws.visualEditorProjectId" :editor="editor" @adopted="ws.loadTtsEditor" />
      <small v-if="running">正式版本保留，完成前句子编辑暂时锁定，总配音仍可试听。停止只影响这次配音编辑，不会取消项目或其他任务。</small>
      <small v-else-if="task.status === 'failed'">正式配音、字幕和素材都没有被替换，可以修改后再试。</small>
    </section>
    <TtsEditorFeedback v-else-if="editor.available" :project-id="ws.visualEditorProjectId" :editor="editor" @adopted="ws.loadTtsEditor" />

    <div v-if="ws.ttsEditorLoading" class="wb-empty"><b>正在读取逐句配音…</b></div>
    <div v-else-if="!editor.available || !segments.length" class="wb-empty">
      <b>还没有可编辑的逐句配音</b>
      <p>{{ editor.message || '配音生成后，这里会列出全部句子，可以逐句试听、修正发音和字幕。' }}</p>
      <GuidedStage class="wb-empty-stage" />
    </div>

    <div v-else class="wb-body aw-body">
      <aside class="wb-list aw-list" aria-label="全部句子">
        <header class="aw-list-head">
          <div><b>全部句子</b><span>{{ segments.length }} 句 · {{ fmtTime(total) }}</span></div>
          <TtsStructureControls only="insert" label="＋ 插入句子" class="aw-insert" :project-id="ws.visualEditorProjectId" :editor="editor" :selected-indices="selected"
            :busy="ws.ttsBoundaryBusy" :has-drafts="ws.hasTtsStructureDrafts()" :anchor-index="viewing" @busy="ws.setTtsStructureBusy" @started="ws.onTtsStructureStarted" />
          <div class="wb-filters" role="group" aria-label="筛选句子">
            <button type="button" :class="{ on: filter === 'all' }" :aria-pressed="filter === 'all'" @click="filter = 'all'">全部</button>
            <button type="button" :class="{ on: filter === 'changed' }" :aria-pressed="filter === 'changed'" @click="filter = 'changed'">有改动</button>
            <button type="button" :class="{ on: filter === 'selected' }" :aria-pressed="filter === 'selected'" @click="filter = 'selected'">已勾选 {{ selected.length || '' }}</button>
          </div>
        </header>
        <div class="aw-batch" :class="{ on: selected.length }">
          <template v-if="selected.length">
            <span><b>已勾选 {{ selected.length }} 句</b><small>批量操作只作用于勾选的句子</small></span>
            <button type="button" class="primary-btn" :disabled="editor.structural_edit_available === false || locked" @click="ws.regenerateSelectedTtsSegments">{{ running ? '重配音中…' : '重配勾选句' }}</button>
            <TtsStructureControls only="delete" label="删除" class="aw-del" :project-id="ws.visualEditorProjectId" :editor="editor" :selected-indices="selected"
              :busy="ws.ttsBoundaryBusy" :has-drafts="ws.hasTtsStructureDrafts()" @busy="ws.setTtsStructureBusy" @started="ws.onTtsStructureStarted" />
            <button type="button" class="ghost-btn" :disabled="locked" @click="clearSelection">清除勾选</button>
          </template>
          <template v-else>
            <span><small>勾选句子后可批量重配或删除；修改朗读文本会自动勾选该句</small></span>
            <button type="button" class="ghost-btn" :disabled="locked || !rows.length" @click="selectVisible">勾选当前列表</button>
          </template>
        </div>
        <div ref="list" class="aw-rows" @keydown="onListKey">
          <div v-for="s in rows" :key="s.index" class="aw-row" :data-narration-index="s.index"
            :class="{ 'is-viewing': viewing === s.index, 'is-playing': playing && ws.narrationPlayingIndex === s.index }">
            <input type="checkbox" :checked="selected.includes(s.index)" :disabled="running" :aria-label="'勾选第' + s.index + '句'" @change="toggleSelected(s.index, $event.target.checked)" />
            <button type="button" class="aw-row-main" :aria-current="viewing === s.index ? 'true' : undefined" @click="view(s.index)">
              <span class="aw-row-top"><b>{{ s.index }}</b><i v-if="playing && ws.narrationPlayingIndex === s.index" class="wb-live" /><small>{{ fmtTime(s.start) }} · {{ Number(s.duration).toFixed(1) }}s</small></span>
              <span class="aw-row-text">{{ s.text }}</span>
              <span class="wb-tags">
                <em v-if="pending(s)" class="t-warn">改稿未应用</em>
                <em v-if="pausePending(s)" class="t-warn">停顿未保存</em>
                <em v-if="pronounced(s)">已修音</em>
                <em v-if="s.subtitle_independent">独立字幕</em>
                <em v-if="synced(s)">重配时同步字幕</em>
                <em v-if="pauseOf(s) > 0 && !pausePending(s)">停顿 +{{ pauseOf(s).toFixed(1) }}s</em>
              </span>
            </button>
          </div>
          <p v-if="!rows.length" class="wb-none">没有符合筛选的句子。</p>
        </div>
      </aside>

      <section v-if="current" class="aw-detail" aria-label="当前句">
        <header class="wb-head">
          <div class="wb-nav">
            <button type="button" class="ghost-btn" :disabled="position <= 0" aria-label="上一句" @click="step(-1)">↑</button>
            <button type="button" class="ghost-btn" :disabled="position >= segments.length - 1" aria-label="下一句" @click="step(1)">↓</button>
          </div>
          <div class="wb-where"><b>第 {{ current.index }} 句</b><span>/ {{ segments.length }}</span><small>{{ fmtTime(current.start) }} – {{ fmtTime(current.end) }} · {{ Number(current.duration || 0).toFixed(2) }} 秒</small></div>
          <label class="aw-check"><input type="checkbox" :checked="selected.includes(current.index)" :disabled="running" @change="toggleSelected(current.index, $event.target.checked)" />勾选此句</label>
          <button type="button" class="ghost-btn" :disabled="!ws.studioAudio" title="从这句开始播放总配音" @click="playFrom(current)">从这里播放整段</button>
        </header>

        <p class="aw-subtitle">{{ current.text }}</p>
        <div class="aw-seg-player">
          <button type="button" class="aw-seg-play" :aria-label="segActive(current) && ws.ttsSegmentIsPlaying ? '暂停本句' : '试听本句'" @click="toggleSegment(current)">{{ segActive(current) && ws.ttsSegmentIsPlaying ? '❚❚' : '▶' }}</button>
          <input type="range" min="0" step="0.01" :max="Math.max(0.01, segActive(current) ? ws.ttsSegmentDuration : Number(current.duration || 0))"
            :value="segActive(current) ? ws.ttsSegmentCurrentTime : 0" aria-label="本句试听进度" @input="ws.seekTtsSegmentAudio(current, $event)" />
          <small>{{ fmtTime(segActive(current) ? ws.ttsSegmentCurrentTime : 0) }} / {{ fmtTime(current.duration) }} · 单句试听，不移动总配音</small>
        </div>

        <div v-if="editor.structural_edit_available === false" class="wb-note t-warn-box">{{ editor.structural_edit_message }}</div>

        <div class="aw-cards">
          <section class="wb-card aw-reading" :class="{ 'is-pending': pending(current) }">
            <header><b>朗读文本</b><small>配音按这段文字生成，可用同音字修正发音</small><em v-if="pending(current)" class="t-warn">未应用</em><em v-else-if="pronounced(current)">已修音</em></header>
            <textarea :value="draftOf(current)" rows="4" maxlength="1200" :disabled="locked" placeholder="例如：点击chong2绘按钮" @input="ws.updateTtsReadingDraft(current, $event)" />
            <div class="wb-card-actions">
              <button type="button" class="ghost-btn" :disabled="locked || !(current.index in drafts) && !pronounced(current)" @click="ws.resetTtsReadingDraft(current)">恢复原文</button>
              <span>改完后在左侧「重配勾选句」生成新配音</span>
            </div>
          </section>
          <section class="wb-card aw-sub">
            <header><b>成片字幕</b><small>显示在画面上的文字</small><em v-if="current.subtitle_independent">独立字幕</em></header>
            <p class="aw-sub-preview">{{ synced(current) ? draftOf(current) : current.text }}</p>
            <label class="aw-sync" :class="{ disabled: !differsFromSubtitle(current) }">
              <input type="checkbox" :checked="synced(current)" :disabled="locked || !differsFromSubtitle(current)" @change="ws.setTtsSubtitleSync(current, $event.target.checked)" />
              <span>重配时字幕也改成朗读文本</span>
            </label>
            <small v-if="current.subtitle_independent" class="aw-note">字幕已独立保存，原配音不变；勾选上面一项后，重配时恢复跟随朗读文本。</small>
            <div class="wb-card-actions">
              <button type="button" class="ghost-btn" :disabled="locked || !differsFromSubtitle(current) || !String(draftOf(current)).trim()" title="把左边文字只保存为字幕，不改配音、朗读文本和时间轴" @click="ws.saveTtsSubtitleOnly(current)">仅保存为字幕</button>
              <span>不重配，不改声音</span>
            </div>
          </section>
        </div>

        <section class="wb-card aw-joint">
          <header><b>{{ next ? '与下一句的交界' : '句子结构' }}</b><small>{{ next ? '调整断句位置或在两句之间加停顿' : '最后一句没有下一句交界' }}</small></header>
          <div v-if="next" class="aw-joint-visual" aria-hidden="true">
            <span>{{ tail(current.text) }}</span>
            <i :class="{ 'has-pause': pauseOf(current) > 0 }">{{ pauseOf(current) > 0 ? '停 ' + pauseOf(current).toFixed(1) + 's' : '‖' }}</i>
            <span>{{ head(next.text) }}</span>
          </div>
          <div class="aw-joint-actions">
            <template v-if="next">
              <label class="aw-pause"><span>额外停顿</span><input :value="ws.ttsPauseDrafts[current.index] ?? current.pause_after ?? 0" type="number" min="0" max="30" step="0.1" :disabled="locked" @input="setPause(current, $event.target.value)" /><small>秒</small></label>
              <button type="button" class="ghost-btn" :class="{ 'is-ready': pausePending(current) }" :disabled="locked" @click="ws.saveTtsPause(current)">保存停顿</button>
              <button type="button" class="ghost-btn" :disabled="locked" @click="ws.previewTtsBoundary(current)">试听交界</button>
              <button v-if="editor.structural_edit_available !== false" type="button" class="ghost-btn" :disabled="locked" @click="ws.openTtsBoundaryEditor(current, true)">调整交界…</button>
            </template>
            <button v-if="editor.structural_edit_available !== false" type="button" class="ghost-btn" :disabled="locked || Array.from(String(current.text || '')).length < 2" @click="ws.openTtsBoundaryEditor(current, false)">在本句内拆分…</button>
          </div>
          <div class="aw-rebuild" :class="{ 'is-due': pausePendingCount > 0 }">
            <span v-if="pausePendingCount">有 {{ pausePendingCount }} 处停顿还没保存，可以逐句保存，也可以一次合成整段。</span>
            <span v-else>改了多处停顿时，可一次合成整段；复用已有配音，不重新生成声音。</span>
            <TtsRebuildButton :editor="editor" :busy="ws.ttsBoundaryBusy" @rebuild="ws.rebuildTtsAudio" />
          </div>
        </section>
        <TtsBoundaryEditor v-if="ws.ttsBoundary.open" :state="ws.ttsBoundary" :project-id="ws.visualEditorProjectId" :busy="ws.ttsBoundaryBusy" :over-limit="ws.ttsBoundaryOverLimit()"
          @close="ws.closeTtsBoundaryEditor" @split="ws.updateTtsBoundaryParts" @count="ws.refreshTtsBoundaryTokenCounts" @submit="ws.submitTtsBoundary" />
      </section>

      <aside class="wb-side aw-side" aria-label="配音版本与下一步">
        <GuidedStage class="wb-stage-side">
          <template #note><p v-if="pendingCount" class="wb-warn">还有 {{ pendingCount }} 句改稿或停顿未应用。<button type="button" class="wb-link" @click="filter = 'changed'">查看</button></p></template>
        </GuidedStage>
        <section class="wb-card">
          <header><b>配音版本</b><small>正式版本 v{{ editor.revision ?? 0 }}</small></header>
          <p class="aw-meter"><span>可撤回 {{ editor.history_count || 0 }} / {{ editor.history_limit || 20 }} 步</span><i><u :style="{ width: ((editor.history_count || 0) / (editor.history_limit || 20) * 100) + '%' }" /></i></p>
          <TtsStructureControls only="undo" label="撤回上一步" class="aw-undo" :project-id="ws.visualEditorProjectId" :editor="editor" :selected-indices="selected"
            :busy="ws.ttsBoundaryBusy" :has-drafts="ws.hasTtsStructureDrafts()" show-undo @busy="ws.setTtsStructureBusy" @started="ws.onTtsStructureStarted" @undo="ws.undoLastTtsEdit" />
        </section>
        <section class="wb-card">
          <header><b>重配参数</b><button type="button" class="wb-link" :disabled="running" @click="ws.studioDrawer = '重配参数'">修改</button></header>
          <dl class="wb-dl">
            <dt>引擎</dt><dd>{{ engineLabel }}</dd>
            <dt>音色</dt><dd>{{ voiceLabel }}</dd>
            <template v-if="editor.engine !== 'qwen'"><dt>情绪</dt><dd>{{ settings.tts_emotion ? (ws.emotionLabel ? ws.emotionLabel(settings.tts_emotion) : settings.tts_emotion) : '参考原音频' }}</dd></template>
            <dt>语速</dt><dd>{{ Number(settings.tts_speed || 1).toFixed(2) }}×</dd>
          </dl>
          <small class="aw-note">只影响之后的重配，已生成的句子不变。</small>
        </section>
        <div v-if="guidedCancel" class="wb-corner"><button type="button" class="wb-corner-btn" :disabled="ws.guidedCancelling" @click="ws.cancelGuidedWorkflow">{{ ws.guidedCancelling ? '正在取消…' : '取消本次分步任务' }}</button></div>
      </aside>
    </div>
  </div>
</template>

<style src="./workbench.css"></style>
<style scoped>
.aw-transport{grid-template-columns:auto auto minmax(150px,230px) minmax(0,1fr) auto}
.aw-follow{font-size:12px;line-height:1.5;color:var(--ocv-muted);display:flex;align-items:center;gap:7px;min-width:0}
.aw-track{display:block;height:34px}
.aw-block{position:absolute;top:0;bottom:0;display:block;border-radius:3px;background:color-mix(in srgb,var(--ocv-text) 12%,transparent);transition:background .15s}
.aw-block.is-viewing{background:color-mix(in srgb,var(--ocv-accent) 38%,transparent)}
.aw-block.is-playing{background:var(--ocv-accent)}
.aw-block.is-selected:before{content:"";position:absolute;left:50%;top:-6px;width:4px;height:4px;border-radius:50%;background:var(--ocv-text);transform:translateX(-50%)}
.aw-block.is-pending{box-shadow:inset 0 -3px 0 var(--ocv-warning)}
.aw-tools{display:flex;gap:8px}
.aw-tools :deep(.tts-find-replace){margin-left:0}
.aw-tools :deep(.tts-find-replace>button){white-space:nowrap}
.aw-list-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:14px 14px 10px}.aw-list-head>div:first-child{display:flex;gap:8px;align-items:baseline}.aw-list-head span{font-size:12px;color:var(--ocv-muted)}
.aw-list-head .wb-filters{flex-basis:100%}
.aw-insert{margin:0!important}
.aw-insert :deep(button),.aw-del :deep(button),.aw-undo :deep(button){font-size:12px;padding:6px 11px;min-height:32px}
.aw-del :deep(button){color:var(--ocv-danger)}
.aw-batch{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 10px 8px;padding:9px 10px;border-radius:10px;background:var(--wb-soft);font-size:12px}
.aw-batch.on{background:var(--wb-accent-soft);outline:1px solid color-mix(in srgb,var(--ocv-accent) 35%,transparent)}
.aw-batch>span{display:grid;gap:2px;flex:1 1 100%;min-width:0}.aw-batch small{color:var(--ocv-muted)}
.aw-batch .primary-btn,.aw-batch .ghost-btn{min-height:32px;padding:6px 11px;font-size:12px}
.aw-rows{overflow:auto;max-height:340px;padding:2px 6px 10px}
.aw-row{display:flex;gap:10px;align-items:flex-start;padding:10px 10px 10px 12px;border-radius:10px;position:relative}
.aw-row+.aw-row{margin-top:2px}
.aw-row:hover{background:var(--wb-soft)}
.aw-row.is-viewing{background:var(--wb-accent-soft)}.aw-row.is-viewing:before{content:"";position:absolute;left:0;top:10px;bottom:10px;width:3px;border-radius:3px;background:var(--ocv-accent)}
.aw-row input{margin-top:4px;flex:none}
.aw-row-main{flex:1;min-width:0;display:grid;gap:4px;text-align:left;background:none!important;border:0!important;padding:0!important;color:inherit;cursor:pointer;border-radius:6px;min-height:0;white-space:normal;box-shadow:none!important}
.aw-row-top{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--ocv-muted)}.aw-row-top b{color:var(--ocv-text);font-variant-numeric:tabular-nums}.aw-row-top small{margin-left:auto;font-variant-numeric:tabular-nums}
.aw-row-text{font-size:13px;line-height:1.65;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.aw-row.is-viewing .aw-row-text{-webkit-line-clamp:unset;display:block}
.aw-detail{display:grid;gap:14px;min-width:0}
.aw-check{display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer}
.aw-subtitle{margin:0;font-size:22px;line-height:1.7;letter-spacing:.01em;padding:4px 2px;overflow-wrap:anywhere}
.aw-seg-player{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 12px;border-radius:12px;background:var(--wb-soft)}
.aw-seg-play{width:36px;height:36px;border-radius:50%!important;padding:0!important;flex:none;border:1px solid color-mix(in srgb,var(--ocv-accent) 60%,transparent)!important;background:transparent!important;color:var(--ocv-accent)!important;cursor:pointer;font-size:12px;min-height:0}
.aw-seg-player input{flex:1;min-width:120px;accent-color:var(--ocv-accent);padding:0!important;height:20px;background:none!important;border:0!important}
.aw-seg-player small{color:var(--ocv-muted);font-size:11px;font-variant-numeric:tabular-nums}
.aw-cards{display:grid;grid-template-columns:1fr;gap:14px}
.aw-reading.is-pending{border-color:color-mix(in srgb,var(--ocv-warning) 55%,transparent)}
.aw-sub-preview{margin:0 0 12px;min-height:56px;padding:12px 14px;border-radius:9px;background:color-mix(in srgb,var(--ocv-bg) 55%,var(--ocv-panel));font-size:15px;line-height:1.75;overflow-wrap:anywhere}
.aw-sync{display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer}.aw-sync.disabled{opacity:.55;cursor:default}
.aw-note{display:block;margin-top:8px;font-size:11px;line-height:1.6;color:var(--ocv-muted)}
.aw-joint-visual{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px;font-size:14px;color:var(--ocv-muted)}
.aw-joint-visual span{padding:6px 10px;border-radius:8px;background:var(--wb-soft);overflow-wrap:anywhere}.aw-joint-visual span:first-child{color:var(--ocv-text)}
.aw-joint-visual i{font-style:normal;font-size:12px;padding:4px 8px;border-radius:999px;border:1px dashed var(--wb-line)}.aw-joint-visual i.has-pause{color:var(--ocv-accent);border-color:color-mix(in srgb,var(--ocv-accent) 55%,transparent)}
.aw-joint-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.aw-pause{display:flex;align-items:center;gap:8px;font-size:13px}.aw-pause input{width:84px}.aw-pause small{color:var(--ocv-muted)}
.aw-rebuild{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:14px;padding-top:12px;border-top:1px dashed var(--wb-line);font-size:12px;color:var(--ocv-muted)}
.aw-rebuild.is-due{color:var(--ocv-warning)}
.aw-meter{display:grid;gap:6px;margin:0 0 12px;font-size:12px;color:var(--ocv-muted)}.aw-meter i{display:block;height:4px;border-radius:9px;background:var(--wb-line);overflow:hidden}.aw-meter u{display:block;height:100%;background:var(--ocv-accent)}
.is-locked .aw-detail .wb-card,.is-locked .aw-rows{opacity:.75}
@container (max-width:900px){.aw-transport{grid-template-columns:auto auto minmax(0,1fr)}.aw-track{grid-column:1/-1}.aw-tools{grid-column:1/-1;justify-content:flex-end}}
@container (max-width:560px){.aw-subtitle{font-size:18px}.aw-follow{display:none}}
@container (min-width:900px){.aw-list{position:sticky;top:var(--wb-top);height:calc(100vh - var(--wb-top) - 16px)}.aw-rows{max-height:none;flex:1}}
@container (min-width:1180px){.aw-cards{grid-template-columns:minmax(0,1.15fr) minmax(0,1fr)}}
@container (min-width:1320px) and (max-width:1599px){.aw-cards{grid-template-columns:1fr}}
@container (min-width:1320px){.aw-subtitle{font-size:24px}}
@container (min-width:2000px){.aw-subtitle{font-size:26px}}
</style>
