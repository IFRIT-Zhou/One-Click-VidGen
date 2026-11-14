<script setup>
import {ref,onMounted,computed} from 'vue'
import {requestJSON} from '../api'
// Task-side model switcher. Full management lives in services/LanguageServicePanel.vue.
const props=defineProps({disabled:Boolean,cloudPool:Boolean})
const expanded=ref(false)
const rows=ref([]),current=ref(null),selected=ref(''),busy=ref(false),message=ref('')
const pendingSwitch=computed(()=>{const row=rows.value.find(p=>p.id===selected.value);return !!row&&(row.provider!==current.value?.provider||row.model!==current.value?.model)})
async function load(){try{const r=await requestJSON('/api/language-presets');rows.value=r.presets;current.value=r.current;selected.value=rows.value.find(p=>p.provider===r.current.provider&&p.model===r.current.model)?.id||''}catch(e){message.value=e.message}}
async function activate(){if(!selected.value||!confirm('切换全局语言模型，影响之后的语言模型调用。不会自动重规划或改写已有提示词。继续？'))return;busy.value=true;try{await requestJSON('/api/language-presets/'+selected.value+'/activate',{method:'POST'});await load();message.value='已切换，下一次规划或提示词生成使用此模型。'}catch(e){message.value=e.message}finally{busy.value=false}}
onMounted(load)
</script>
<template>
 <section class="language-presets">
  <div class="preset-heading" :class="{'is-collapsed':cloudPool||!expanded}"><div class="preset-title"><strong>语言模型</strong><small v-if="cloudPool">当前由号池托管，模型由服务端统一分配</small><small v-else>当前全局：{{current?.model||'正在读取…'}}</small></div><button v-if="!cloudPool" type="button" :aria-expanded="expanded" @click="expanded=!expanded">{{expanded?'收起设置 ▴':'切换模型 ▾'}}</button><button v-if="!cloudPool&&expanded" type="button" :disabled="busy" @click="load">刷新列表</button></div>
  <div v-if="!cloudPool" v-show="expanded" class="preset-switch">
   <select v-model="selected" :disabled="busy||disabled" aria-label="语言模型预设"><option value="">{{rows.length?'选择已保存模型':'请先到接口与服务保存模型预设'}}</option><option v-for="row in rows" :key="row.id" :value="row.id">{{row.name===row.model?row.model:row.name+' · '+row.model}}</option></select>
   <button type="button" :disabled="busy||disabled||!selected" @click="activate">{{busy?'处理中…':'使用此模型'}}</button>
  </div>
  <p v-if="!cloudPool" v-show="expanded" class="muted preset-hint">切换后用于后续规划与提示词生成。号池托管任务仍使用号池模型。</p>
  <div v-if="$slots.actions" class="preset-actions"><slot name="actions" :unavailable="busy||(!cloudPool&&(pendingSwitch||!current))" :pending-switch="!cloudPool&&pendingSwitch" /></div>
  <p v-if="message" role="status">{{message}}</p>
 </section>
</template>
<style scoped>
.preset-title{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;min-width:0}
.preset-title small{overflow-wrap:anywhere}
.preset-heading.is-collapsed{margin-bottom:0}
.preset-hint{margin:8px 0 0}
.preset-actions{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:14px;padding-top:14px;border-top:1px solid var(--border,#35423f)}
.preset-actions :deep(.replan-description){display:grid;gap:5px;min-width:0}
.preset-actions :deep(.replan-description strong){font-size:13px}
.preset-actions :deep(.replan-description small){font-size:12px;line-height:1.6}
.preset-actions :deep(button){flex-shrink:0;white-space:nowrap}
@media(max-width:700px){.preset-title{display:grid;gap:4px}.preset-actions{align-items:stretch;flex-direction:column}.preset-switch select{flex-basis:100%}}
</style>
<style scoped>.language-presets{padding:16px;border:1px solid var(--border,#35423f);border-radius:12px;margin:14px 0}.preset-heading,.preset-switch{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.preset-heading{margin-bottom:12px}.preset-heading small{color:var(--muted)}.preset-heading button{margin-left:auto}.preset-switch select{flex:1;min-width:180px}.language-presets p{font-size:12px;line-height:1.6}</style>
