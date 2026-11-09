<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { requestJSON } from '../api'
import { animatedTemplateIds, MAX_TEMPLATE_CHOICES, templateLimits, templateMediaUrl, templateSelectionIssue, templateTags, toggleTemplate } from '../shotcraftTemplates'

const props = defineProps({ modelValue: { type: Array, default: () => [] }, disabled: Boolean, duration: Number })
const emit = defineEmits(['update:modelValue', 'selection-meta'])
const items = ref([]), vocabulary = ref({}), loading = ref(false), loaded = ref(false), error = ref(''), notice = ref('')
const playing = ref(true), pageVisible = ref(true), visibleIds = ref(new Set()), hovered = ref(''), focused = ref(''), dismissed = ref('')
const failedMedia = ref(new Set())
const active = computed(() => hovered.value || focused.value)
const animated = computed(() => animatedTemplateIds(items.value, visibleIds.value, active.value, playing.value && pageVisible.value))
const issue = computed(() => templateSelectionIssue(props.modelValue, items.value, props.duration, loaded.value))
const selectedItems = computed(() => props.modelValue.map(id => items.value.find(item => item.id === id)).filter(Boolean))
let observer = null, mediaQuery = null, alive = true, requestVersion = 0
const elements = new Map()
const idPrefix = 'shotcraft-tooltip-' + Math.random().toString(36).slice(2, 10)

watch([selectedItems, issue, items], () => emit('selection-meta', {
  items: selectedItems.value, catalog: items.value, valid: !issue.value, issue: issue.value,
}), { immediate: true })
watch(() => props.modelValue, () => { notice.value = '' })
function cardRef(element, id) {
  const previous = elements.get(id)
  if (previous === element) return
  if (previous) observer?.unobserve(previous)
  if (!element) { elements.delete(id); return }
  elements.set(id, element)
  if (observer) observer.observe(element)
  else visibleIds.value = new Set([...visibleIds.value, id])
}
function setMotionPreference(event) { playing.value = !event.matches }
function visibilityChanged() { pageVisible.value = document.visibilityState !== 'hidden' }
onMounted(() => {
  if (typeof IntersectionObserver !== 'undefined') {
    observer = new IntersectionObserver(entries => {
      const next = new Set(visibleIds.value)
      for (const entry of entries) {
        const id = entry.target.dataset.templateId
        if (entry.isIntersecting) next.add(id); else next.delete(id)
      }
      visibleIds.value = next
    }, { threshold: 0.15 })
    for (const element of elements.values()) observer.observe(element)
  }
  mediaQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)')
  if (mediaQuery) { playing.value = !mediaQuery.matches; mediaQuery.addEventListener?.('change', setMotionPreference) }
  visibilityChanged(); document.addEventListener('visibilitychange', visibilityChanged)
  load()
})
onUnmounted(() => { alive = false; observer?.disconnect(); mediaQuery?.removeEventListener?.('change', setMotionPreference); document.removeEventListener('visibilitychange', visibilityChanged) })
async function load() {
  const version = ++requestVersion
  loading.value = true; error.value = ''; notice.value = ''
  try {
    const result = await requestJSON('/api/vibe-templates')
    if (!alive || version !== requestVersion) return
    if (!Array.isArray(result.items) || !result.items.length) throw new Error('没有可展示的动效模板')
    items.value = result.items.filter(item => item && typeof item.id === 'string' && typeof item.name === 'string')
    if (!items.value.length) throw new Error('模板目录格式不完整')
    vocabulary.value = result.tag_labels || {}; loaded.value = true; failedMedia.value = new Set()
    await nextTick()
    for (const element of elements.values()) observer?.observe(element)
  } catch (failure) {
    if (alive && version === requestVersion) { error.value = '暂时无法读取动效库：' + failure.message; loaded.value = false }
  } finally { if (alive && version === requestVersion) loading.value = false }
}
function choose(item) {
  if (props.disabled) return
  if (!item.available) { notice.value = item.reason || '这款模板尚未安装就绪，可先观看示意。'; return }
  if (!props.modelValue.includes(item.id) && props.modelValue.length >= MAX_TEMPLATE_CHOICES) {
    notice.value = '最多选择 3 款，请先取消一款。'; return
  }
  emit('update:modelValue', toggleTemplate(props.modelValue, item.id))
}
function autoSelect() { if (!props.disabled) emit('update:modelValue', []) }
function enter(id) { hovered.value = id; dismissed.value = '' }
function focus(id) { hovered.value = ''; focused.value = id; dismissed.value = '' }
function imageUrl(item) {
  const url = animated.value.has(item.id) ? templateMediaUrl(item.preview_url) : templateMediaUrl(item.poster_url)
  if (url && !failedMedia.value.has(url)) return url
  const poster = templateMediaUrl(item.poster_url)
  return poster && !failedMedia.value.has(poster) ? poster : ''
}
function mediaFailed(event) { failedMedia.value = new Set([...failedMedia.value, event.currentTarget.getAttribute('src')]) }
defineExpose({ refresh: load })
</script>

<template>
  <section class="shotcraft-picker" aria-label="选择镜头动效">
    <div class="shotcraft-picker-heading">
      <div><strong>选择动效</strong><span class="shotcraft-selection-count">{{ modelValue.length ? `已选 ${modelValue.length} 款` : 'AI 自动选择' }}</span></div>
      <div class="shotcraft-picker-tools">
        <button type="button" class="shotcraft-auto" :class="{ active: !modelValue.length }" :disabled="disabled" :aria-pressed="!modelValue.length" @click="autoSelect">让 AI 自动选择</button>
        <button type="button" class="shotcraft-motion-toggle" :aria-pressed="playing" :aria-label="playing ? '暂停动效示意' : '播放动效示意'" @click="playing = !playing">{{ playing ? '暂停示意' : '播放示意' }}</button>
      </div>
    </div>
    <p class="shotcraft-picker-help">选 1–3 款，由 AI 按文案编排；留空自动选择。悬停查看适用场景。<span>示意配色与素材不用于替换本镜。</span></p>
    <p v-if="loading" class="shotcraft-picker-message" role="status">正在读取动效库…</p>
    <div v-if="error" class="shotcraft-picker-error" role="alert"><span>{{ error }}，仍可使用自动选择。</span><button type="button" :disabled="loading" @click="load">重试读取</button></div>
    <div v-if="items.length" class="shotcraft-template-grid">
      <button v-for="item in items" :key="item.id" :ref="element => cardRef(element, item.id)" type="button"
        class="shotcraft-template-card" :class="{ selected: modelValue.includes(item.id), unavailable: !item.available }"
        :data-template-id="item.id" :aria-pressed="modelValue.includes(item.id)" :aria-disabled="disabled || !item.available"
        :aria-label="item.name + (item.available ? '' : '，当前不可用')" :aria-describedby="active === item.id && dismissed !== item.id ? idPrefix + item.id : undefined"
        @click="choose(item)" @mouseenter="enter(item.id)" @mouseleave="hovered = ''" @focus="focus(item.id)" @blur="focused = ''" @keydown.esc="dismissed = item.id">
        <span class="shotcraft-template-image"><img v-if="imageUrl(item)" :src="imageUrl(item)" alt="" loading="lazy" decoding="async" @error="mediaFailed"><span v-else class="shotcraft-media-placeholder">动效示意待就绪</span><span v-if="modelValue.includes(item.id)" class="shotcraft-template-check" aria-hidden="true">✓</span><span v-if="!item.available" class="shotcraft-template-unavailable">尚未就绪</span></span>
        <span class="shotcraft-template-name">{{ item.name }}</span>
        <span v-if="active === item.id && dismissed !== item.id" :id="idPrefix + item.id" role="tooltip" class="shotcraft-template-tooltip">
          <strong>{{ item.capabilities?.recommended_use || item.name }}</strong>
          <span class="shotcraft-tooltip-tags"><span v-for="tag in templateTags(item, vocabulary)" :key="tag">{{ tag }}</span></span>
          <span class="shotcraft-tooltip-limits">{{ templateLimits(item) }}</span>
          <span v-if="item.capabilities?.avoid_for?.length" class="shotcraft-tooltip-caution">不推荐：{{ item.capabilities.avoid_for.join('、') }}</span>
          <span v-if="!item.available" class="shotcraft-tooltip-caution">{{ item.reason || '运行组件尚未就绪' }}</span>
        </span>
      </button>
    </div>
    <p v-if="issue" class="shotcraft-picker-warning" role="alert">{{ issue }}</p>
    <p v-else-if="notice" class="shotcraft-picker-warning" role="status">{{ notice }}</p>
  </section>
</template>

<style scoped>
.shotcraft-picker{--template-accent:#c5bcff;--template-border:#877cbd;margin:14px 0;padding:16px;border:1px solid var(--border,#35423f);border-radius:12px;background:var(--panel,#1c2523);min-width:0}
.shotcraft-picker-heading,.shotcraft-picker-tools{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.shotcraft-picker-heading{justify-content:space-between}.shotcraft-picker-heading strong{font-size:15px}.shotcraft-selection-count{margin-left:10px;color:var(--muted,#aab8b3);font-size:12px}.shotcraft-picker-tools button{font-size:12px;padding:7px 10px;border-radius:7px;min-height:32px;background:transparent;color:var(--muted,#aab8b3);border:1px solid var(--border,#35423f)}.shotcraft-picker-tools .active{color:var(--template-accent);border-color:var(--template-border);background:#b7aaff12}.shotcraft-picker-tools button:disabled{opacity:.5;cursor:not-allowed}
.shotcraft-picker-help{margin:10px 0 14px;color:var(--muted,#aab8b3);font-size:12px;line-height:1.6}.shotcraft-picker-help span{display:block;font-size:11px;opacity:.85}.shotcraft-template-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.shotcraft-template-card{position:relative;display:flex;flex-direction:column;min-width:0;padding:0;overflow:visible;cursor:pointer;border:1px solid var(--border,#35423f);border-radius:9px;background:var(--surface,#202b29);color:var(--text,#e5ebe8);text-align:left;transition:border-color .15s,box-shadow .15s}.shotcraft-template-card:hover,.shotcraft-template-card:focus-visible{border-color:var(--template-accent);outline:none;box-shadow:0 0 0 2px #c5bcff22;z-index:3}.shotcraft-template-card.selected{border-color:var(--template-accent);box-shadow:0 0 0 1px var(--template-border);background:#c5bcff12}.shotcraft-template-card[aria-disabled=true]{cursor:not-allowed}.shotcraft-template-card.unavailable .shotcraft-template-name{color:var(--muted,#aab8b3)}
.shotcraft-template-image{position:relative;display:flex;align-items:center;justify-content:center;width:100%;aspect-ratio:16/9;overflow:hidden;border-radius:8px 8px 0 0;background:#101816}.shotcraft-template-image img{display:block;width:100%;height:100%;object-fit:contain}.shotcraft-template-name{display:block;width:100%;padding:10px 12px;font-size:13px;font-weight:600;line-height:1.4;white-space:nowrap;text-overflow:ellipsis;overflow:hidden}.shotcraft-template-check{position:absolute;right:8px;top:8px;display:grid;place-items:center;width:23px;height:23px;background:var(--template-accent);color:#211936;border-radius:50%;font-size:15px;box-shadow:0 1px 6px #0007}.shotcraft-media-placeholder{font-size:12px;color:var(--muted,#aab8b3)}.shotcraft-template-unavailable{position:absolute;bottom:6px;right:6px;padding:2px 6px;border-radius:4px;background:#101816dc;color:#bcc7c1;font-size:10px}
.shotcraft-template-tooltip{position:absolute;left:0;bottom:calc(100% + 9px);z-index:8;display:grid;gap:8px;width:max(100%,270px);max-width:min(330px,75vw);padding:13px 14px;border:1px solid #756b9d;border-radius:9px;background:#24272f;color:#eef0f5;box-shadow:0 7px 24px #0007;font-size:12px;line-height:1.55;pointer-events:none;text-align:left;font-weight:400;white-space:normal}.shotcraft-template-card:nth-child(4n) .shotcraft-template-tooltip{left:auto;right:0}.shotcraft-template-tooltip strong{font-size:12px;font-weight:500}.shotcraft-tooltip-tags{display:flex;gap:5px;flex-wrap:wrap}.shotcraft-tooltip-tags span{padding:2px 6px;background:#c5bcff18;border:1px solid #c5bcff35;border-radius:4px;color:#ded8ff;font-size:11px}.shotcraft-tooltip-limits{color:#c6d0cb;font-variant-numeric:tabular-nums}.shotcraft-tooltip-caution{color:#c8c7cd;font-size:11px}.shotcraft-picker-message,.shotcraft-picker-warning,.shotcraft-picker-error{font-size:12px;line-height:1.6;margin:10px 0 0}.shotcraft-picker-message{color:var(--muted,#aab8b3)}.shotcraft-picker-warning{color:var(--warning,#e7c37b)}.shotcraft-picker-error{display:flex;justify-content:space-between;align-items:center;gap:12px;color:var(--warning,#e7c37b);margin-bottom:12px}.shotcraft-picker-error button{white-space:nowrap;font-size:12px;padding:5px 9px}
@media(max-width:1150px){.shotcraft-template-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.shotcraft-template-card:nth-child(4n) .shotcraft-template-tooltip{left:0;right:auto}.shotcraft-template-card:nth-child(3n) .shotcraft-template-tooltip{left:auto;right:0}}
@media(max-width:700px){.shotcraft-picker{padding:12px}.shotcraft-template-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.shotcraft-picker-heading{align-items:flex-start}.shotcraft-picker-tools{gap:6px}.shotcraft-template-name{padding:9px;font-size:12px}.shotcraft-template-card:nth-child(3n) .shotcraft-template-tooltip{left:0;right:auto}.shotcraft-template-card:nth-child(2n) .shotcraft-template-tooltip{left:auto;right:0}.shotcraft-picker-help span{margin-top:3px}}
@media(prefers-reduced-motion:reduce){.shotcraft-template-card{transition:none}}
</style>
