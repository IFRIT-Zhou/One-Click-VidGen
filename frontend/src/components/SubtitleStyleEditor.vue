<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { requestJSON } from '../api'
const props = defineProps({ settings: { type: Object, required: true }, variant: { type: String, default: 'both' }, readonly: Boolean, orientationControl: Boolean, dynamic: Boolean, previewImage: String, previewUrl: String, previewShotId: String, hideEditions: Boolean })
const emit = defineEmits(['update:variant'])
const portrait = computed(() => props.settings.video_orientation === 'portrait')
const defaults = computed(() => ({ font: 'Microsoft YaHei', size: portrait.value ? 56 : 36, position: portrait.value ? 75 : 95, width_percent: portrait.value ? 78 : 92, max_chars: portrait.value ? 14 : 44, color: '#ffffff', outline_color: '#000000', outline: 2, background: true, opacity: 0.75 }))
const layout = computed(() => ({ ...defaults.value, x_position:50, frame_scale:1, frame_x:50, frame_y:50, ...(props.settings.subtitle_layouts?.[portrait.value ? 'portrait' : 'landscape'] || {}) }))
const disabled = computed(() => props.readonly || props.variant === 'raw')
const fonts = ref(['Microsoft YaHei', 'SimHei', 'SimSun', 'Arial'])
const sample = ref('从一段文字开始，让每个想法被看见。')
const actual = ref(null), busy = ref(false), error = ref(''), guides = ref(true)
function set(key, value) {
  if (props.readonly || (disabled.value && !key.startsWith('frame_'))) return
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
const target = ref(props.dynamic ? 'frame' : 'subtitle'), sourceRatio = ref(16/9)
const clamp = (n,min,max) => Math.max(min,Math.min(max,n))
watch(() => props.previewImage, url => { sourceRatio.value=width.value/height.value; if(url){const img=new Image();img.onload=()=>{if(props.previewImage===url)sourceRatio.value=img.naturalWidth/img.naturalHeight};img.src=url} }, {immediate:true})
const frame = computed(() => {
 const ratio=props.previewImage?sourceRatio.value:width.value/height.value
 const w=Math.min(width.value,height.value*ratio)*layout.value.frame_scale, h=w/ratio
 return {x:width.value*layout.value.frame_x/100-w/2,y:height.value*layout.value.frame_y/100-h/2,w,h}
})
const selection = computed(() => target.value==='frame'&&props.dynamic ? frame.value : {x:width.value*layout.value.x_position/100-boxWidth.value/2,y:height.value*layout.value.position/100-boxHeight.value/2,w:boxWidth.value,h:boxHeight.value})
const dragState = ref(null)
function startDrag(e,resize=false){
 if(props.readonly || (target.value==='subtitle'&&disabled.value))return
 const svg=e.currentTarget.closest('svg'), r=svg.getBoundingClientRect()
 dragState.value={id:e.pointerId,x:e.clientX,y:e.clientY,r,resize,target:target.value,scale:layout.value.frame_scale,size:layout.value.size,px:layout.value.frame_x,py:layout.value.frame_y,sx:layout.value.x_position,sy:layout.value.position}
 svg.setPointerCapture(e.pointerId);e.preventDefault()
}
function moveDrag(e){
 const drag=dragState.value
 if(!drag||drag.id!==e.pointerId)return
 const dx=(e.clientX-drag.x)/drag.r.width,dy=(e.clientY-drag.y)/drag.r.height
 if(drag.resize){if(drag.target==='frame')set('frame_scale',Math.round(clamp(drag.scale+dx*2,.25,2)*100)/100);else set('size',Math.round(clamp(drag.size+dx*200,20,100)))}
 else if(drag.target==='frame'){set('frame_x',Math.round(clamp(drag.px+dx*100,0,100)*10)/10);set('frame_y',Math.round(clamp(drag.py+dy*100,0,100)*10)/10)}
 else{set('x_position',Math.round(clamp(drag.sx+dx*100,5,95)*10)/10);set('position',Math.round(clamp(drag.sy+dy*100,10,96)*10)/10)}
}
function resetPlacement(){if(target.value==='frame'){set('frame_scale',1);set('frame_x',50);set('frame_y',50)}else{set('x_position',50);set('position',defaults.value.position);set('size',defaults.value.size)}}
const lines = computed(() => {
  const limit = Math.max(6, Math.min(layout.value.max_chars, Math.floor(width.value * layout.value.width_percent / 100 / layout.value.size)))
  return Array.from(sample.value.replace(/\s+/g, ' ').trim()).reduce((rows, char, i) => { if (i % limit === 0) rows.push(''); rows[rows.length - 1] += char; return rows }, [])
})
const boxHeight = computed(() => lines.value.length * layout.value.size * 1.2 + 10)
const boxWidth = computed(() => Math.min(width.value * layout.value.width_percent / 100, Math.max(1, ...lines.value.map(s => s.length)) * layout.value.size + 24))
watch(() => [props.settings.video_orientation, props.settings.subtitle_layouts, props.variant, sample.value,props.previewImage], () => { actual.value = null }, { deep: true })
watch(() => props.variant, value=>{if(value==='raw'&&props.dynamic)target.value='frame'})
onMounted(async () => { try { fonts.value = (await requestJSON('/api/subtitle-fonts')).fonts } catch {} })
async function preview() {
  busy.value = true; error.value = ''
  const snapshot = JSON.stringify([props.settings.video_orientation, props.settings.subtitle_layouts, props.variant, sample.value,props.previewShotId])
  try {
    const result = await requestJSON(props.previewUrl || '/api/subtitle-style-preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ video_orientation: props.settings.video_orientation || 'landscape', subtitle_layouts: props.settings.subtitle_layouts || {}, video_render_variant: props.variant, text: sample.value,shot_id:props.previewShotId }) })
    if (snapshot === JSON.stringify([props.settings.video_orientation, props.settings.subtitle_layouts, props.variant, sample.value,props.previewShotId])) actual.value = result
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>

<template>
 <section class="subtitle-design">
  <header><div><small>最终渲染 · {{width}} × {{height}}</small><h3>{{dynamic ? '画面布局与字幕' : '成片版本与字幕'}}</h3></div><span>{{portrait ? '竖屏 9:16' : '横屏 16:9'}}</span></header>
  <div v-if="!hideEditions" class="edition-options">
   <label><input type="checkbox" :checked="variant !== 'raw'" :disabled="readonly || variant === 'subtitles'" @change="variantChange('subtitles', $event.target.checked)">字幕版</label>
   <label><input type="checkbox" :checked="variant !== 'subtitles'" :disabled="readonly || variant === 'raw'" @change="variantChange('raw', $event.target.checked)">无字幕版</label>
  </div>
  <p v-if="!hideEditions" class="hint">至少保留一个版本。无论选择哪一项，SRT 字幕文件都会正常输出。</p>
  <label v-if="orientationControl" class="orientation-field">渲染比例<select v-model="settings.video_orientation" :disabled="readonly"><option value="landscape">横屏 16:9</option><option value="portrait">竖屏 9:16</option></select><small>切换比例不会重新生成现有图片；比例不符时完整保留画面并留边。</small></label>
  <p v-if="variant==='raw'" class="locked-note">{{hideEditions?'当前预览隐藏字幕，样式已保留；打开“预览显示字幕”即可调整。':'当前仅输出无字幕版，字幕样式已保留，重新勾选字幕版即可调整。'}}</p>
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
  </fieldset>
   <div class="preview-heading"><b>{{dynamic?'成片布局预览':'字幕预览'}}</b><label class="check"><input v-model="guides" type="checkbox">安全区参考线</label></div>
   <div class="layout-tools">
    <div class="layout-target" aria-label="调整对象"><button v-if="dynamic" type="button" :class="{active:target==='frame'}" :disabled="readonly" @click="target='frame';actual=null">调整画面</button><button type="button" :class="{active:target==='subtitle'}" :disabled="disabled" @click="target='subtitle';actual=null">调整字幕</button></div>
    <button type="button" :disabled="readonly || (target==='subtitle'&&disabled)" @click="resetPlacement">{{target==='frame'?'画面复位':'字幕复位'}}</button>
   </div>
   <div v-if="dynamic && target==='frame'" class="layout-scale"><label>画面缩放</label><input aria-label="画面缩放" type="range" min="25" max="200" step="1" :disabled="readonly" :value="Math.round(layout.frame_scale*100)" @input="set('frame_scale',Number($event.target.value)/100)"><span>{{Math.round(layout.frame_scale*100)}}%</span></div>
   <div v-else class="layout-scale"><label>字幕字号</label><input aria-label="字幕字号" type="range" min="20" max="100" step="1" :disabled="disabled" :value="layout.size" @input="set('size',Number($event.target.value))"><span>{{layout.size}} px</span></div>
   <div class="subtitle-preview" :class="{portrait}">
    <img v-if="actual" :src="actual.image" alt="真实渲染字幕预览帧">
    <svg v-else :viewBox="`0 0 ${width} ${height}`" role="img" aria-label="可拖动的成片布局预览" :class="{draggable:!readonly}" @pointerdown="startDrag($event)" @pointermove="moveDrag" @pointerup="dragState=null" @pointercancel="dragState=null" @lostpointercapture="dragState=null">
     <rect width="100%" height="100%" fill="#000000"/>
     <image v-if="dynamic&&previewImage" :href="previewImage" :x="frame.x" :y="frame.y" :width="frame.w" :height="frame.h" preserveAspectRatio="xMidYMid meet"/>
     <g v-else-if="dynamic"><rect :x="frame.x" :y="frame.y" :width="frame.w" :height="frame.h" fill="#263732"/><text :x="frame.x+frame.w/2" :y="frame.y+frame.h/2" text-anchor="middle" fill="#b1c6be" font-size="40">画面占位 · 导出页可用实际分镜预览</text></g>
     <rect v-if="guides" :x="width*.08" :y="height*.1" :width="width*.78" :height="height*.7" fill="none" stroke="#718c88" stroke-width="3" stroke-dasharray="12 10"/>
     <g v-if="variant!=='raw'"><rect v-if="layout.background" :x="width*layout.x_position/100-boxWidth/2" :y="height*layout.position/100-boxHeight/2" :width="boxWidth" :height="boxHeight" rx="8" fill="#071834" :opacity="layout.opacity"/>
     <text v-for="(line,i) in lines" :key="i" :x="width*layout.x_position/100" :y="height*layout.position/100+(i-(lines.length-1)/2)*layout.size*1.2" dominant-baseline="central" text-anchor="middle" :font-family="layout.font" :font-size="layout.size" font-weight="600" :fill="layout.color" :stroke="layout.outline_color" :stroke-width="layout.outline*2" paint-order="stroke fill">{{line}}</text></g>
     <g v-if="!readonly && !(target==='subtitle'&&disabled)" class="selection"><rect :x="selection.x+2" :y="selection.y+2" :width="Math.max(0,selection.w-4)" :height="Math.max(0,selection.h-4)" fill="none" stroke="#85ddc9" stroke-width="2" vector-effect="non-scaling-stroke"/><rect :x="selection.x+selection.w-38" :y="selection.y+selection.h-38" width="36" height="36" fill="#85ddc9" class="resize-handle" @pointerdown.stop="startDrag($event,true)"/></g>
    </svg>
   </div>
   <p class="hint">{{dynamic?'选中调整对象后，在预览中拖动位置；拖动右下角或使用滑杆缩放。画布外的部分会被裁切，留白区域为黑色。此布局统一应用于全部镜头，不修改源素材。':'在预览中拖动字幕位置，拖动右下角或使用滑杆调整字号。'}}</p>
   <label class="sample-input">预览文字（不改变文案）<input v-model="sample" maxlength="160"></label>
   <div class="preview-actions"><button type="button" :disabled="busy || !sample.trim()" @click="preview">{{busy?'正在生成…':'生成真实预览帧'}}</button><span v-if="actual">{{actual.engine}}</span><button v-if="actual" type="button" @click="actual=null">返回示意预览</button></div>
   <p class="hint">示意预览便于即时调整；真实预览使用本机渲染器，无需出图或扣费。参考线不进入成片，实际平台按钮遮挡范围可能不同。</p>
   <p v-if="dynamic&&!previewImage&&!previewUrl" class="hint">创建阶段使用占位画面，真实预览帧仅校验字幕。已有分镜后，可到“合成与导出”选择实际分镜检查完整布局。</p>
   <p v-if="error" role="alert">{{error}}</p>
 </section>
</template>

<style scoped>
.subtitle-design{display:grid;gap:16px;min-width:0}.subtitle-design header,.preview-heading,.preview-actions{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.subtitle-design h3{margin:4px 0}.hint,.subtitle-design small{font-size:12px;color:var(--muted,#a1aaa6);line-height:1.6}.edition-options{display:flex;gap:24px}.edition-options label,.check{display:flex!important;align-items:center;gap:9px}.subtitle-design input[type=checkbox]{appearance:auto!important;width:17px!important;height:17px!important;min-height:0!important;flex:none}.subtitle-design fieldset{border:0;padding:0;margin:0;min-width:0;display:grid;gap:18px}.locked{opacity:.45}.style-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.style-fields label,.sample-input,.orientation-field{display:grid;gap:7px;font-size:13px;min-width:0}.wide{grid-column:1/-1}.subtitle-design select,.subtitle-design input:not([type=checkbox]){width:100%;min-width:0;box-sizing:border-box}.subtitle-design input[type=color]{height:40px;padding:4px}.subtitle-design summary{cursor:pointer;padding:8px 0}.subtitle-preview{max-width:100%;width:100%;margin:auto;overflow:hidden;border:1px solid #394440;border-radius:10px;line-height:0}.subtitle-preview.portrait{max-width:260px}.subtitle-preview svg,.subtitle-preview img{display:block;width:100%;height:auto}.preview-heading .check{font-size:12px}.locked-note{font-size:13px;color:var(--muted,#a1aaa6)}.preview-actions button{padding:10px 14px}.preview-actions span{font-size:12px}.subtitle-design .style-fields{margin-top:8px}@media(max-width:480px){.style-fields{grid-template-columns:1fr}}
.layout-tools,.layout-target,.layout-scale{display:flex;align-items:center;gap:10px;min-width:0}.layout-tools{justify-content:space-between;flex-wrap:wrap}.layout-tools button{font-size:12px;padding:8px 12px}.layout-target button.active{border-color:#85ddc9;color:#85ddc9;background:#263d36}.layout-scale{font-size:12px}.layout-scale label,.layout-scale span{white-space:nowrap;flex:none}.layout-scale input{flex:1}.layout-scale span{width:55px;text-align:right}.subtitle-preview svg.draggable{touch-action:none;cursor:move;user-select:none}.resize-handle{cursor:nwse-resize}.selection{pointer-events:none}.resize-handle{pointer-events:auto}
</style>
