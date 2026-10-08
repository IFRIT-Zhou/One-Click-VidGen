<script setup>
import { computed, watch } from 'vue'
import { ICAN_SIZES, imageChoice, imageSize } from '../icanImageSizes'
const props = defineProps({ form: { type: Object, required: true }, standalone: Boolean })
const isIcan = computed(() => props.form.method === 'ican')
const ratio = computed({
  get: () => props.standalone ? (isIcan.value ? imageChoice(props.form.size).ratio : props.form.ratio || '16:9') : props.form.video_orientation === 'portrait' ? '9:16' : '16:9',
  set: value => {
    if (!props.standalone) props.form.video_orientation = value === '9:16' ? 'portrait' : 'landscape'
    else if (!isIcan.value) props.form.ratio = value
    if (isIcan.value) props.form.size = imageSize(value, quality.value)
  },
})
const quality = computed({
  get: () => isIcan.value ? imageChoice(props.form.size).quality : (props.form.image_resolution || '1k').toUpperCase(),
  set: value => {
    if (isIcan.value) props.form.size = imageSize(ratio.value, value)
    else props.form.image_resolution = value.toLowerCase()
  },
})
const qualities = computed(() => isIcan.value ? Object.keys(ICAN_SIZES[ratio.value]) : ['1K', '2K', '4K'])
watch([() => props.form.video_orientation, isIcan], () => {
  if (isIcan.value && !props.standalone) props.form.size = imageSize(ratio.value, quality.value)
  if (!isIcan.value && !['1k', '2k', '4k'].includes(props.form.image_resolution)) props.form.image_resolution = '1k'
  if (!isIcan.value && props.standalone && !['16:9', '9:16', '1:1', '3:2', '2:1', '3:4', '21:9'].includes(props.form.ratio)) props.form.ratio = '16:9'
}, { immediate: true })
</script>
<template>
 <div class="ican-quality-grid">
  <label><span><strong>{{standalone?'图片画幅':'视频画幅'}}</strong><small>{{standalone?'选择图片比例':'画面和导出视频一致'}}</small></span>
   <select v-model="ratio"><option value="16:9">横屏 16:9</option><option value="9:16">竖屏 9:16</option><template v-if="standalone"><option value="1:1">方形 1:1</option><option value="3:2">横图 3:2</option><option v-if="isIcan" value="2:3">竖图 2:3</option><template v-else><option value="2:1">横图 2:1</option><option value="3:4">竖图 3:4</option><option value="21:9">宽屏 21:9</option></template></template></select>
  </label>
  <label><span><strong>图片清晰度</strong><small>由云端图片号池生成</small></span>
   <select v-model="quality"><option v-for="name in qualities" :key="name" :value="name">{{name}}</option></select>
  </label>
 </div>
</template>
<style scoped>
.ican-quality-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;width:100%}.ican-quality-grid label{display:grid;gap:14px;padding:20px;border:1px solid var(--ocv-border,#d9e5e7);border-radius:16px;background:var(--ocv-panel,#fbfdfd)}.ican-quality-grid span{display:flex;justify-content:space-between;gap:12px;align-items:center}.ican-quality-grid small{color:var(--ocv-muted,#607780);font-size:12px}.ican-quality-grid select{width:100%;min-height:48px;border-radius:10px}@media(max-width:640px){.ican-quality-grid{grid-template-columns:1fr}}
</style>
