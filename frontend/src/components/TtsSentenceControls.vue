<script setup>
import { computed } from 'vue'
const props = defineProps({
  item: { type: Object, required: true },
  editor: { type: Object, required: true },
  busy: Boolean, opened: Boolean, subtitleSync: Boolean,
  reading: String, pause: [Number, String],
})
const emit = defineEmits(['toggle', 'reading', 'reset', 'subtitle-sync', 'save-subtitle', 'boundary', 'pause', 'save-pause', 'preview'])
const locked = computed(() => props.busy || props.editor.task?.status === 'running')
const draft = computed(() => props.reading ?? props.item.tts_text ?? props.item.text ?? '')
const modified = computed(() => String(draft.value).trim() !== String(props.item.text || '').trim())
const hasNext = computed(() => Number(props.item.index) < (props.editor.segments || []).length)
const canResegment = computed(() => props.editor.structural_edit_available !== false)
</script>

<template>
  <div class="tts-sentence-controls">
    <div class="tts-pronunciation-tools">
      <button class="tts-pronunciation-toggle tts-pronunciation-main-toggle" type="button" :disabled="locked" :aria-expanded="opened" @click="emit('toggle')">
        <span>发音与文案修正</span><i aria-hidden="true">{{ opened ? '⌃' : '⌄' }}</i>
      </button>
      <template v-if="opened">
        <div class="tts-pronunciation-editor">
          <label>
            <span>朗读文本 <small>仅修正发音时无需同步字幕</small></span>
            <textarea :value="draft" rows="2" maxlength="1200" :disabled="locked"
              placeholder="例如：点击chong2绘按钮；只改显示文字可点右侧“仅保存字幕”"
              @input="emit('reading', $event.target.value)"></textarea>
          </label>
          <div class="tts-subtitle-save-actions">
            <button class="ghost-btn compact-btn" type="button" :disabled="locked || !modified || !String(draft).trim()"
              title="将框内文字仅保存为字幕，不修改配音、朗读文本或时间轴" @click="emit('save-subtitle')">仅保存字幕</button>
            <button class="ghost-btn compact-btn" type="button" :disabled="locked || !modified" @click="emit('reset')">恢复原文</button>
          </div>
        </div>
        <label class="tts-subtitle-sync-toggle" :class="{ disabled: !modified }">
          <input type="checkbox" :checked="subtitleSync" :disabled="locked || !modified" @change="emit('subtitle-sync', $event.target.checked)" />
          <span>同时修改字幕</span><small>重配时同步；已有配音也可点“重新合成完整配音”补同步</small>
        </label>
        <small class="tts-subtitle-preview">{{ subtitleSync ? '字幕将同步为：' + draft : '成片字幕保持：' + item.text }}</small>
        <small v-if="item.subtitle_independent" class="tts-independent-note">字幕已独立保存，原配音不变；仅在勾选“同时修改字幕”时恢复跟随朗读文本。</small>
      </template>
    </div>
    <div class="tts-boundary-row">
      <button v-if="hasNext && canResegment" class="ghost-btn compact-btn" type="button" :disabled="locked" @click="emit('boundary', true)">调整交界</button>
      <template v-if="hasNext">
        <label><span>额外停顿</span><input :value="pause ?? item.pause_after ?? 0" :disabled="locked" type="number" min="0" max="30" step="0.1" @input="emit('pause', $event.target.value === '' ? '' : Number($event.target.value))" /><small>秒</small></label>
        <button class="ghost-btn compact-btn" type="button" :disabled="locked" @click="emit('save-pause')">保存停顿</button>
        <button class="ghost-btn compact-btn" type="button" :disabled="locked" @click="emit('preview')">试听交界</button>
      </template>
      <button v-if="canResegment" class="ghost-btn compact-btn boundary-add" type="button" :disabled="locked || Array.from(String(item.text || '')).length < 2" @click="emit('boundary', false)">本句内新增交界点</button>
    </div>
  </div>
</template>

<style scoped>
.tts-sentence-controls{min-width:0;grid-column:1/-1}.tts-subtitle-save-actions{display:grid;gap:8px}.tts-independent-note{display:block;color:var(--muted);line-height:1.6;margin-top:6px}.tts-boundary-row .boundary-add{margin-left:auto}
.tts-subtitle-sync-toggle{display:flex;align-items:center;flex-wrap:wrap;gap:6px;font-size:12px}.tts-subtitle-sync-toggle input{width:16px;height:16px;min-height:0;margin:0;flex:none}.tts-subtitle-sync-toggle small{color:var(--muted);overflow-wrap:anywhere}.tts-subtitle-sync-toggle.disabled{opacity:.6}
@media(max-width:600px){.tts-boundary-row .boundary-add{margin-left:0}.tts-subtitle-save-actions{grid-template-columns:1fr 1fr}}
</style>
