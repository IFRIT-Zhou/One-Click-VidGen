<script setup>
import { dynamicTextModeDescriptions, normalizeDynamicTextMode } from '../dynamicTextMode'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { requestJSON } from '../api'

const props = defineProps({ modelValue: { type: String, default: 'visual_first' } })
defineEmits(['update:modelValue'])
const extensions = ref([])
async function refreshExtensions() {
  try {
    const result = await requestJSON('/api/director-profiles')
    extensions.value = (result.profiles || []).filter(item => item.id === 'medical_paper')
  } catch { extensions.value = [] }
}
const missingExtension = computed(() => props.modelValue === 'medical_paper' && !extensions.value.length)
const hint = computed(() => missingExtension.value
  ? '此任务使用的可选导演插件未启用。已有素材保留；继续规划前请启用插件，或明确选择其他方式。'
  : extensions.value.find(item => item.id === props.modelValue)?.description || dynamicTextModeDescriptions[normalizeDynamicTextMode(props.modelValue)])
onMounted(() => { refreshExtensions(); window.addEventListener('focus', refreshExtensions) })
onUnmounted(() => window.removeEventListener('focus', refreshExtensions))
</script>

<template>
  <div class="director-strategy-row dynamic-text-mode-row">
    <div class="director-strategy-copy">
      <span>动态画面表达</span>
      <small>选择画面规划方式；不改动配音与成片字幕。</small>
    </div>
    <div class="director-strategy-options" role="group" aria-label="动态画面表达">
      <button type="button" :class="{ active: modelValue === 'text_assisted' }" :aria-pressed="modelValue === 'text_assisted'" @click="$emit('update:modelValue', 'text_assisted')">文字辅助</button>
      <button type="button" :class="{ active: modelValue === 'visual_first' }" :aria-pressed="modelValue === 'visual_first'" @click="$emit('update:modelValue', 'visual_first')">画面优先</button>
      <button v-for="item in extensions" :key="item.id" type="button" :class="{ active: modelValue === item.id }" :aria-pressed="modelValue === item.id" @click="$emit('update:modelValue', item.id)">{{ item.label }}</button>
    </div>
    <small class="director-strategy-hint">{{ hint }}</small>
  </div>
</template>

<style scoped>
div.dynamic-text-mode-row.director-strategy-row{display:flex;flex-direction:column;align-items:stretch;gap:12px;min-width:0}
.dynamic-text-mode-row .director-strategy-copy{display:grid;gap:5px}
.dynamic-text-mode-row .director-strategy-copy>span{display:block;font-size:14px}
.dynamic-text-mode-row .director-strategy-copy>small{display:block;font-size:12px;line-height:1.6}
.dynamic-text-mode-row .director-strategy-options{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);width:100%;box-sizing:border-box;margin:0}
.dynamic-text-mode-row .director-strategy-options button{min-width:0;font-size:13px}
.dynamic-text-mode-row .director-strategy-hint{display:block;width:100%;font-size:12px;line-height:1.7}
</style>
