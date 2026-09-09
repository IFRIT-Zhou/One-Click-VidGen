<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { requestJSON } from '../api'
const props = defineProps({ settings: { type: Object, required: true }, variant: { type: String, default: 'both' }, readonly: Boolean, orientationControl: Boolean })
const emit = defineEmits(['update:variant'])
const portrait = computed(() => props.settings.video_orientation === 'portrait')
const defaults = computed(() => ({ font: 'Microsoft YaHei', size: portrait.value ? 56 : 36, position: portrait.value ? 75 : 95, width_percent: portrait.value ? 78 : 92, max_chars: portrait.value ? 14 : 44, color: '#ffffff', outline_color: '#000000', outline: 2, background: true, opacity: 0.75 }))
const layout = computed(() => ({ ...defaults.value, ...(props.settings.subtitle_layouts?.[portrait.value ? 'portrait' : 'landscape'] || {}) }))
const disabled = computed(() => props.readonly || props.variant === 'raw')
const fonts = ref(['Microsoft YaHei', 'SimHei', 'SimSun', 'Arial'])
const sample = ref('从一段文字开始，让每个想法被看见。')
const actual = ref(null), busy = ref(false), error = ref(''), guides = ref(true)
function set(key, value) {
  if (disabled.value) return
  const orientation = portrait.value ? 'portrait' : 'landscape'
  props.settings.subtitle_layouts = { ...(props.settings.subtitle_layouts || {}), [orientation]: { ...(props.settings.subtitle_layouts?.[orientation] || {}), [key]: value } }
}
function variantChange(kind, checked) {
  const sub = kind === 'subtitles' ? checked : props.variant !== 'raw'
  const raw = kind === 'raw' ? checked : props.variant !== 'subtitles'
  if (!sub && !raw) return
  emit('update:variant', sub && raw ? 'both' : sub ? 'subtitles' : 'raw')
}
const width = computed(() => portrait.value ? 1080 : 1920)
const height = computed(() => portrait.value ? 1920 : 1080)
const lines = computed(() => {
  const limit = Math.max(6, Math.min(layout.value.max_chars, Math.floor(width.value * layout.value.width_percent / 100 / layout.value.size)))
  return Array.from(sample.value.replace(/\s+/g, ' ').trim()).reduce((rows, char, i) => { if (i % limit === 0) rows.push(''); rows[rows.length - 1] += char; return rows }, [])
})
const boxHeight = computed(() => lines.value.length * layout.value.size * 1.2 + 10)
const boxWidth = computed(() => Math.min(width.value * layout.value.width_percent / 100, Math.max(1, ...lines.value.map(s => s.length)) * layout.value.size + 24))
watch(() => [props.settings.video_orientation, props.settings.subtitle_layouts, props.variant, sample.value], () => { actual.value = null }, { deep: true })
onMounted(async () => { try { fonts.value = (await requestJSON('/api/subtitle-fonts')).fonts } catch {} })
async function preview() {
  busy.value = true; error.value = ''
  const snapshot = JSON.stringify([props.settings.video_orientation, props.settings.subtitle_layouts, props.variant, sample.value])
  try {
    const result = await requestJSON('/api/subtitle-style-preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ video_orientation: props.settings.video_orientation || 'landscape', subtitle_layouts: props.settings.subtitle_layouts || {}, video_render_variant: props.variant, text: sample.value }) })
    if (snapshot === JSON.stringify([props.settings.video_orientation, props.settings.subtitle_layouts, props.variant, sample.value])) actual.value = result
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>

<template>
 <section class="subtitle-design">
  <header><div><small>最终渲染</small><h3>成片版本与字幕</h3></div><span>{{portrait ? '竖屏 9:16' : '横屏 16:9'}}</span></header>
  <div class="edition-options">
   <label><input type="checkbox" :checked="variant !== 'raw'" :disabled="readonly || variant === 'subtitles'" @change="variantChange('subtitles', $event.target.checked)">字幕版</label>
   <label><input type="checkbox" :checked="variant !== 'subtitles'" :disabled="readonly || variant === 'raw'" @change="variantChange('raw', $event.target.checked)">无字幕版</label>
  </div>
  <p class="hint">至少保留一个版本。无论选择哪一项，SRT 字幕文件都会正常输出。</p>
  <label v-if="orientationControl" class="orientation-field">渲染比例<select v-model="settings.video_orientation" :disabled="readonly"><option value="landscape">横屏 16:9</option><option value="portrait">竖屏 9:16</option></select><small>切换比例不会重新生成现有图片；比例不符时完整保留画面并留边。</small></label>
  <p v-if="variant==='raw'" class="locked-note">当前仅输出无字幕版，字幕样式已保留，重新勾选字幕版即可调整。</p>
  <fieldset :disabled="disabled" :class="{locked:disabled}">
   <div class="style-fields">
    <label class="wide">字体<select :value="layout.font" @change="set('font',$event.target.value)"><option v-if="!fonts.includes(layout.font)" :value="layout.font">{{layout.font}}（本机未找到，将回退）</option><option v-for="font in fonts" :key="font">{{font}}</option></select></label>
    <label>字号（像素）<input type="number" min="20" max="100" :value="layout.size" @change="set('size',Math.max(20,Math.min(100,Number($event.target.value)||defaults.size)))"></label>
    <label>距顶部（%）<input type="number" min="10" max="96" :value="layout.position" @change="set('position',Math.max(10,Math.min(96,Number($event.target.value)||defaults.position)))"></label>
    <label>文字颜色<input type="color" :value="layout.color" @input="set('color',$event.target.value)"></label>
    <label>描边颜色<input type="color" :value="layout.outline_color" @input="set('outline_color',$event.target.value)"></label>
    <label>描边粗细（像素）<input type="number" min="0" max="8" step="0.5" :value="layout.outline" @change="set('outline',Math.max(0,Math.min(8,Number($event.target.value)||0)))"></label>
   </div>
   <details><summary>更多样式</summary><div class="style-fields">
    <label>字幕最大宽度（%）<input type="number" min="35" max="95" :value="layout.width_percent" @change="set('width_percent',Math.max(35,Math.min(95,Number($event.target.value)||78)))"></label>
    <label>每行最多字数<input type="number" min="6" max="60" :value="layout.max_chars" @change="set('max_chars',Math.max(6,Math.min(60,Number($event.target.value)||14)))"></label>
    <label class="check"><input type="checkbox" :checked="layout.background" @change="set('background',$event.target.checked)">字幕底色</label>
    <label>底色不透明度<input type="range" min="0" max="1" step="0.05" :disabled="!layout.background" :value="layout.opacity" @input="set('opacity',Number($event.target.value))"></label>
   </div></details>
   <div class="preview-heading"><b>字幕预览</b><label class="check"><input v-model="guides" type="checkbox">安全区参考线</label></div>
   <div class="subtitle-preview" :class="{portrait}">
    <img v-if="actual" :src="actual.image" alt="真实渲染字幕预览帧">
    <svg v-else :viewBox="`0 0 ${width} ${height}`" role="img" aria-label="字幕样式示意预览">
     <rect width="100%" height="100%" fill="#050a12"/>
     <rect v-if="guides" :x="width*.08" :y="height*.1" :width="width*.78" :height="height*.7" fill="none" stroke="#718c88" stroke-width="3" stroke-dasharray="12 10"/>
     <rect v-if="layout.background" :x="(width-boxWidth)/2" :y="height*layout.position/100-boxHeight/2" :width="boxWidth" :height="boxHeight" rx="8" fill="#071834" :opacity="layout.opacity"/>
     <text v-for="(line,i) in lines" :key="i" :x="width/2" :y="height*layout.position/100+(i-(lines.length-1)/2)*layout.size*1.2" dominant-baseline="central" text-anchor="middle" :font-family="layout.font" :font-size="layout.size" font-weight="600" :fill="layout.color" :stroke="layout.outline_color" :stroke-width="layout.outline*2" paint-order="stroke fill">{{line}}</text>
    </svg>
   </div>
   <label class="sample-input">预览文字（不改变文案）<input v-model="sample" maxlength="160"></label>
   <div class="preview-actions"><button type="button" :disabled="busy || !sample.trim()" @click="preview">{{busy?'正在生成…':'生成真实预览帧'}}</button><span v-if="actual">{{actual.engine}}</span><button v-if="actual" type="button" @click="actual=null">返回示意预览</button></div>
   <p class="hint">示意预览便于即时调整；真实预览使用本机渲染器，无需出图或扣费。参考线不进入成片，实际平台按钮遮挡范围可能不同。</p>
   <p v-if="error" role="alert">{{error}}</p>
  </fieldset>
 </section>
</template>

<style scoped>
.subtitle-design{display:grid;gap:16px;min-width:0}.subtitle-design header,.preview-heading,.preview-actions{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.subtitle-design h3{margin:4px 0}.hint,.subtitle-design small{font-size:12px;color:var(--muted,#a1aaa6);line-height:1.6}.edition-options{display:flex;gap:24px}.edition-options label,.check{display:flex!important;align-items:center;gap:9px}.subtitle-design input[type=checkbox]{appearance:auto!important;width:17px!important;height:17px!important;min-height:0!important;flex:none}.subtitle-design fieldset{border:0;padding:0;margin:0;min-width:0;display:grid;gap:18px}.locked{opacity:.45}.style-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.style-fields label,.sample-input,.orientation-field{display:grid;gap:7px;font-size:13px;min-width:0}.wide{grid-column:1/-1}.subtitle-design select,.subtitle-design input:not([type=checkbox]){width:100%;min-width:0;box-sizing:border-box}.subtitle-design input[type=color]{height:40px;padding:4px}.subtitle-design summary{cursor:pointer;padding:8px 0}.subtitle-preview{max-width:100%;width:100%;margin:auto;overflow:hidden;border:1px solid #394440;border-radius:10px;line-height:0}.subtitle-preview.portrait{max-width:260px}.subtitle-preview svg,.subtitle-preview img{display:block;width:100%;height:auto}.preview-heading .check{font-size:12px}.locked-note{font-size:13px;color:var(--muted,#a1aaa6)}.preview-actions button{padding:10px 14px}.preview-actions span{font-size:12px}.subtitle-design .style-fields{margin-top:8px}@media(max-width:480px){.style-fields{grid-template-columns:1fr}}
</style>
