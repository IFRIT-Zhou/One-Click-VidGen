<script setup>
import { computed, nextTick, ref, useId, watch } from 'vue'

const props = defineProps({
  shot: { type: Object, required: true },
  subtitles: { type: Array, default: () => [] },
  statusLabel: String,
  editDisabled: Boolean,
})
defineEmits(['edit'])
const uid = useId(), active = ref('image'), expanded = ref(false)
watch(() => [props.shot.id, props.shot.kind], () => {
  active.value = props.shot.kind === 'video' ? 'video' : 'image'
  expanded.value = false
}, { immediate: true })
watch(active, () => { expanded.value = false })
// These are the editable design fields, never the expanded H3 request payload.
const prompts = computed(() => [
  { key: 'image', label: '画面提示词', text: props.shot.image_prompt || props.shot.visual_description || '' },
  ...(props.shot.kind === 'video' ? [{ key: 'video', label: props.shot.video_prompt ? '动态提示词' : '动态表达', text: props.shot.video_prompt || props.shot.action || '' }] : []),
])
const selected = computed(() => prompts.value.find(item => item.key === active.value) || prompts.value[0])
const longPrompt = computed(() => selected.value.text.length > 280 || selected.value.text.split('\n').length > 6)
async function moveTab(direction) {
  const index=prompts.value.findIndex(item=>item.key===active.value)
  active.value=prompts.value[(index+direction+prompts.value.length)%prompts.value.length].key
  await nextTick()
  document.getElementById(uid+'-'+active.value)?.focus()
}
</script>

<template>
  <div class="shot-result-overview">
    <section class="shot-review-copy" aria-label="本镜文案">
      <h3>本镜文案</h3>
      <div class="shot-review-copy-text"><p v-for="(text,index) in subtitles" :key="index">{{ text }}</p><p v-if="!subtitles.length" class="review-muted">本镜暂无文案</p></div>
    </section>
    <section class="shot-review-effect" aria-label="分镜效果">
      <header><h3>分镜效果</h3><span class="review-muted">{{ statusLabel }}</span></header>
      <slot name="preview" />
      <p v-if="shot.intent" class="shot-review-intent">{{ shot.intent }}</p>
    </section>
    <section class="shot-review-prompts" aria-label="本镜提示词">
      <header>
        <h3>本镜提示词</h3>
        <button type="button" class="shot-edit-design" :disabled="editDisabled" :title="editDisabled ? '当前镜头正在处理，完成后可编辑' : '进入本镜画面与提示词编辑'" @click="$emit('edit', active)"><span aria-hidden="true">✎</span> 编辑本镜画面与提示词</button>
      </header>
      <div class="shot-prompt-tabs" role="tablist" aria-label="提示词类型">
        <button v-for="item in prompts" :id="uid+'-'+item.key" :key="item.key" type="button" role="tab" :aria-selected="active===item.key" :aria-controls="uid+'-prompt'" :tabindex="active===item.key?0:-1" :class="{active:active===item.key}" @click="active=item.key" @keydown.left.prevent="moveTab(-1)" @keydown.right.prevent="moveTab(1)">{{ item.label }}</button>
      </div>
      <div :id="uid+'-prompt'" role="tabpanel" :aria-labelledby="uid+'-'+selected.key" class="shot-readable-prompt" :class="{clamped:longPrompt&&!expanded}" tabindex="0">{{ selected.text || '尚未填写，可点击上方按钮补充本镜设计。' }}</div>
      <button v-if="longPrompt" type="button" class="shot-prompt-expand" :aria-expanded="expanded" @click="expanded=!expanded">{{ expanded ? '收起全文' : '展开全文' }}</button>
      <p v-if="shot.image_prompt_out_of_sync||shot.video_prompt_out_of_sync" class="shot-prompt-note">设计已修改，当前效果可能仍为上一版，请核对后再生成。</p>
    </section>
    <div class="shot-review-auxiliary"><slot name="auxiliary" /></div>
  </div>
</template>

<style scoped>
.shot-result-overview{display:grid;gap:20px;min-width:0;color:var(--text,#eef3f1)}
.shot-result-overview section{min-width:0}.shot-result-overview h3{font-size:15px;margin:0;font-weight:700}.shot-result-overview header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px}.review-muted{color:var(--muted,#aab8b3);font-size:12px}
.shot-review-copy{padding:18px 20px;border:1px solid var(--border,#35423f);border-left:3px solid var(--accent,#81d9bd);border-radius:10px;background:color-mix(in srgb,var(--accent,#81d9bd) 5%,var(--panel,#1e2625))}.shot-review-copy h3{color:var(--muted,#aab8b3);font-size:12px;margin-bottom:10px}.shot-review-copy-text{font-size:17px;line-height:1.9;overflow-wrap:anywhere}.shot-review-copy-text p{margin:4px 0}.shot-review-intent{margin:9px 0 0;font-size:13px;line-height:1.65;color:var(--muted,#aab8b3)}
.shot-review-prompts{padding:16px 18px;border:1px solid var(--border,#35423f);border-radius:10px;background:var(--panel,#1e2625)}
.shot-result-overview .shot-edit-design{display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:10px 14px!important;min-height:40px;border:1px solid #b9a6ff!important;border-radius:9px;background:#b9a6ff!important;color:#251a42!important;font-size:13px;font-weight:700;cursor:pointer;box-shadow:0 2px 8px #0002}.shot-edit-design span{font-size:17px}.shot-result-overview .shot-edit-design:hover:not(:disabled){background:#cebeff!important}.shot-result-overview .shot-edit-design:disabled{opacity:.45;cursor:not-allowed;box-shadow:none}.shot-edit-design:focus-visible,.shot-prompt-tabs button:focus-visible,.shot-prompt-expand:focus-visible{outline:2px solid #b9a6ff;outline-offset:3px}
.shot-prompt-tabs{display:flex;gap:18px;border-bottom:1px solid var(--border,#35423f);margin-bottom:12px}.shot-result-overview .shot-prompt-tabs button{padding:8px 0!important;border:0!important;border-bottom:2px solid transparent!important;border-radius:0!important;background:transparent!important;color:var(--muted,#aab8b3)!important;font-size:13px;cursor:pointer}.shot-result-overview .shot-prompt-tabs button.active{color:var(--text,#eef3f1)!important;border-bottom-color:#b9a6ff!important}.shot-readable-prompt{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.85}.shot-readable-prompt.clamped{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:7;overflow:hidden}.shot-result-overview .shot-prompt-expand{margin-top:8px;padding:4px 0!important;background:none!important;border:0!important;color:var(--muted,#aab8b3)!important;font-size:12px;cursor:pointer}.shot-prompt-note{font-size:12px;color:var(--muted,#aab8b3);margin:12px 0 0}.shot-review-auxiliary{padding-top:18px;border-top:1px solid var(--border,#35423f);min-width:0}
@media(max-width:600px){.shot-result-overview{gap:16px}.shot-review-copy{padding:14px}.shot-review-copy-text{font-size:16px}.shot-review-prompts{padding:14px}.shot-review-prompts header{align-items:stretch}.shot-review-prompts h3{width:100%}.shot-result-overview .shot-edit-design{width:100%}}
</style>
