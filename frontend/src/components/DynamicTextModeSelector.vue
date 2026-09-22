<script setup>
import { dynamicTextModeDescriptions, normalizeDynamicTextMode } from '../dynamicTextMode'

defineProps({ modelValue: { type: String, default: 'visual_first' } })
defineEmits(['update:modelValue'])
</script>

<template>
  <div class="director-strategy-row dynamic-text-mode-row">
    <div class="director-strategy-copy">
      <span>动态画面表达</span>
      <small>控制画面是否借助文字说明，与作品风格、配音和成片字幕独立。</small>
    </div>
    <div class="director-strategy-options" role="group" aria-label="动态画面表达">
      <button type="button" :class="{ active: modelValue === 'text_assisted' }" :aria-pressed="modelValue === 'text_assisted'" @click="$emit('update:modelValue', 'text_assisted')">文字辅助</button>
      <button type="button" :class="{ active: modelValue === 'visual_first' }" :aria-pressed="modelValue === 'visual_first'" @click="$emit('update:modelValue', 'visual_first')">画面优先</button>
    </div>
    <small class="director-strategy-hint">{{ dynamicTextModeDescriptions[normalizeDynamicTextMode(modelValue)] }}</small>
  </div>
</template>

<style scoped>
div.dynamic-text-mode-row.director-strategy-row{display:flex;flex-direction:column;align-items:stretch;gap:12px;min-width:0}
.dynamic-text-mode-row .director-strategy-copy{display:grid;gap:5px}
.dynamic-text-mode-row .director-strategy-copy>span{display:block;font-size:14px}
.dynamic-text-mode-row .director-strategy-copy>small{display:block;font-size:12px;line-height:1.6}
.dynamic-text-mode-row .director-strategy-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));width:100%;box-sizing:border-box;margin:0}
.dynamic-text-mode-row .director-strategy-options button{min-width:0;font-size:13px}
.dynamic-text-mode-row .director-strategy-hint{display:block;width:100%;font-size:12px;line-height:1.7}
</style>
