<script setup>
import { computed } from 'vue'
const props = defineProps({ maxDuration: {type: [Number, String], default: 14}, recommended: {type: Boolean, default: true}, backend: String })
const emit = defineEmits(['update:maxDuration', 'update:recommended'])
const locked = computed(() => props.backend === 'cloud_h3' && props.recommended)
const displayed = computed(() => locked.value ? 14 : props.maxDuration)
function changeDuration(event) {
  const value = event.target.value === '' ? '' : Number(event.target.value)
  emit('update:maxDuration', value)
}
const invalid = computed(() => !locked.value && (!Number.isInteger(props.maxDuration) || props.maxDuration < 4))
</script>
<template>
 <div class="shot-planning-limit">
  <label class="planning-duration-field">
   <span>分镜规划最长时长</span>
   <span class="planning-duration-input"><input aria-label="分镜规划最长时长（秒）" type="number" min="4" step="1" :value="displayed" :disabled="locked" :aria-invalid="invalid" @input="changeDuration"/><span>秒 / 镜</span></span>
  </label>
  <label v-if="backend==='cloud_h3'" class="planning-cluster-recommendation">
   <input type="checkbox" :checked="recommended" @change="emit('update:recommended', $event.target.checked)"/>
   <span>采用集群推荐上限：14 秒</span>
  </label>
  <small v-if="invalid" role="alert">请输入至少 4 秒的整数。</small>
  <p v-else class="planning-limit-hint">上限不是目标时长，按最终配音与字幕规划。<template v-if="backend==='cloud_h3'">取消勾选可规划长镜头，超过 14 秒的镜头需拆分或单独更换生成通道。</template><template v-else>长镜头需所选生成模型支持。</template></p>
 </div>
</template>
<style scoped>
.shot-planning-limit{display:flex;flex-direction:column;gap:10px;min-width:0;grid-column:1 / -1}
.planning-duration-field{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0}
.planning-duration-input{display:flex;align-items:center;gap:8px;flex-shrink:0;color:var(--muted,#a2b8c8);font-size:13px}
.planning-duration-input input{width:92px;min-width:0;margin:0;padding:10px 12px;border:1px solid var(--border,#35423f);border-radius:9px;background:var(--bg,#131d2d);color:var(--text,#e5edf6);font:inherit;font-size:16px}
.planning-duration-input input:disabled{opacity:.65;cursor:not-allowed}
.planning-cluster-recommendation{display:flex;align-items:flex-start;gap:8px;margin:0;cursor:pointer;font-size:14px;line-height:1.5}
.planning-cluster-recommendation input{width:16px;height:16px;min-width:16px;flex:0 0 16px;margin:3px 0 0;accent-color:var(--accent,#80d9bf)}
.planning-limit-hint{margin:0;color:var(--muted,#a2b8c8);font-size:13px;line-height:1.65}
.shot-planning-limit>small{color:var(--danger,#f39898)}
@media(max-width:400px){.planning-duration-field{align-items:flex-start;flex-direction:column}}
</style>
