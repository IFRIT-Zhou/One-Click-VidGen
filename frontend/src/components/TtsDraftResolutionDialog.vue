<template>
  <Teleport to="body">
    <div class="tts-draft-backdrop" @click.self="$emit('close')">
      <section ref="dialog" class="tts-draft-dialog" role="dialog" aria-modal="true" aria-labelledby="tts-draft-title" aria-describedby="tts-draft-description" tabindex="-1" @keydown="onKeydown">
        <header><h2 id="tts-draft-title">有 {{ items.length }} 句文字尚未应用</h2><button type="button" aria-label="关闭，返回编辑" @click="$emit('close')">×</button></header>
        <p id="tts-draft-description">这些文字还没有保存为字幕，也没有生成到配音中。两种选择都复用原配音，不会调用 TTS。</p>
        <div class="tts-draft-list">
          <article v-for="item in items" :key="item.index">
            <strong>第 {{ item.index }} 句</strong>
            <p><span>已有配音</span>{{ item.saved }}</p>
            <p><span>修改后的文字</span>{{ item.draft || '（空白，请返回编辑）' }}</p>
          </article>
        </div>
        <footer>
          <button type="button" class="ghost-btn" @click="$emit('resolve', 'audio')">只合成现有配音</button>
          <button type="button" class="primary-btn" :disabled="items.some(item => !item.draft.trim())" @click="$emit('resolve', 'subtitles')">仅保存字幕并合成</button>
        </footer>
        <small>只合成：保留当前字幕和文字草稿。保存字幕：应用上方文字，声音和口播文案不变。</small>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
defineProps({ items: { type: Array, default: () => [] } })
const emit = defineEmits(['close', 'resolve'])
const dialog = ref(null)
let previousFocus, previousOverflow
onMounted(() => {
  previousFocus = document.activeElement
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  dialog.value?.focus()
})
onBeforeUnmount(() => {
  document.body.style.overflow = previousOverflow
  if (previousFocus?.isConnected) previousFocus.focus()
})
function onKeydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); emit('close'); return }
  if (event.key !== 'Tab') return
  const buttons = [...dialog.value.querySelectorAll('button:not(:disabled)')]
  const first = buttons[0], last = buttons.at(-1)
  if (event.shiftKey && [dialog.value, first].includes(document.activeElement)) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
</script>

<style scoped>
.tts-draft-backdrop{position:fixed;inset:0;z-index:12000;display:grid;place-items:center;padding:20px;background:#0009}
.tts-draft-dialog{box-sizing:border-box;width:min(620px,100%);max-height:90dvh;overflow:auto;padding:24px;border:1px solid #45615b;border-radius:18px;background:#1c2726;color:#e5efee;box-shadow:0 20px 80px #0008;outline:none}
header{display:flex;align-items:center;justify-content:space-between;gap:12px}h2{margin:0;font-size:20px}header button{font-size:26px;padding:0 8px;background:transparent;border:0;color:inherit}
p{line-height:1.65;color:#b8c9c6}.tts-draft-list{max-height:40dvh;overflow:auto;margin:18px 0}.tts-draft-list article{padding:14px;border:1px solid #3b514d;border-radius:10px;margin-bottom:10px}.tts-draft-list p{margin:8px 0;white-space:pre-wrap;overflow-wrap:anywhere}.tts-draft-list span{display:block;font-size:12px;color:#80cfbc}
footer{display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap}footer button{min-height:42px}small{display:block;color:#9fb4af;line-height:1.6;margin-top:12px}
button:focus-visible{outline:2px solid #80dcc8;outline-offset:3px}@media(max-width:480px){.tts-draft-dialog{padding:18px}footer{flex-direction:column}footer button{width:100%}}
</style>
