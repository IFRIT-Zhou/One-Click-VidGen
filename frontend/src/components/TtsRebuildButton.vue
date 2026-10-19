<script setup>
defineProps({ editor: { type: Object, required: true }, busy: Boolean })
defineEmits(['rebuild'])
</script>
<template>
  <button type="button" class="ghost-btn compact-btn"
    title="复用已有配音合成整段，并应用额外停顿；勾选“同时修改字幕”的句子会同步已生成的朗读文字。未处理的文字修改会先提示选择，不重新调用 TTS。"
    :disabled="busy || editor.task?.status === 'running' || !editor.available || !(editor.segments || []).length"
    @click="$emit('rebuild')">{{ busy ? '正在处理配音…' : '重新合成完整配音' }}</button>
</template>
