<script setup>
import {ref,onMounted,computed} from 'vue'
import {requestJSON} from '../api'
const props=defineProps({manage:Boolean,disabled:Boolean,cloudPool:Boolean})
const expanded=ref(false)
const rows=ref([]),current=ref(null),selected=ref(''),busy=ref(false),message=ref(''),editing=ref(false)
const draft=ref({id:'',name:'',model:'',provider:'custom',thinking:'follow'})
const sources=computed(()=>current.value?.providers?.filter(p=>p.configured&&!p.disabled)||[])
const pendingSwitch=computed(()=>{const row=rows.value.find(p=>p.id===selected.value);return !!row&&(row.provider!==current.value?.provider||row.model!==current.value?.model)})
async function load(){try{const r=await requestJSON('/api/language-presets');rows.value=r.presets;current.value=r.current;selected.value=rows.value.find(p=>p.provider===r.current.provider&&p.model===r.current.model)?.id||''}catch(e){message.value=e.message}}
function edit(row){draft.value=row?{...row}:{id:'',name:current.value?.model||'',model:current.value?.model||'',provider:current.value?.provider||'custom',thinking:'follow'};editing.value=true}
async function save(){busy.value=true;message.value='';try{await requestJSON('/api/language-presets',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(draft.value)});editing.value=false;await load();message.value='模型预设已保存；尚未切换运行模型。'}catch(e){message.value=e.message}finally{busy.value=false}}
async function activate(){if(!selected.value||!confirm('切换全局语言模型，影响之后的语言模型调用。不会自动重规划或改写已有提示词。继续？'))return;busy.value=true;try{await requestJSON('/api/language-presets/'+selected.value+'/activate',{method:'POST'});await load();message.value='已切换，下一次规划或提示词生成使用此模型。'}catch(e){message.value=e.message}finally{busy.value=false}}
async function remove(row){if(!confirm('删除模型预设？不会删除 API 密钥。'))return;busy.value=true;try{await requestJSON('/api/language-presets/'+row.id,{method:'DELETE'});await load()}catch(e){message.value=e.message}finally{busy.value=false}}
onMounted(load)
</script>
<template>
 <section class="language-presets">
  <div class="preset-heading" :class="{'is-collapsed':cloudPool||(!manage&&!expanded)}"><div class="preset-title"><strong>语言模型</strong><small v-if="cloudPool">当前由号池托管，模型由服务端统一分配</small><small v-else>当前全局：{{current?.model||'正在读取…'}}</small></div><button v-if="!manage&&!cloudPool" type="button" :aria-expanded="expanded" @click="expanded=!expanded">{{expanded?'收起设置 ▴':'切换模型 ▾'}}</button><button v-if="!cloudPool&&(manage||expanded)" type="button" :disabled="busy" @click="load">刷新列表</button></div>
  <div v-if="!cloudPool" v-show="manage||expanded" class="preset-switch">
   <select v-model="selected" :disabled="busy||disabled" aria-label="语言模型预设"><option value="">{{rows.length?'选择已保存模型':'请先到接口与服务保存模型预设'}}</option><option v-for="row in rows" :key="row.id" :value="row.id">{{row.name===row.model?row.model:row.name+' · '+row.model}}</option></select>
   <button type="button" :disabled="busy||disabled||!selected" @click="activate">{{busy?'处理中…':'使用此模型'}}</button>
   <button v-if="manage" type="button" :disabled="busy" @click="edit()">＋ 保存新模型</button>
  </div>
  <p v-if="!cloudPool" v-show="manage||expanded" class="muted preset-hint">切换后用于后续规划与提示词生成。号池托管任务仍使用号池模型。</p>
  <div v-if="$slots.actions" class="preset-actions"><slot name="actions" :unavailable="busy||(!cloudPool&&(pendingSwitch||!current))" :pending-switch="!cloudPool&&pendingSwitch" /></div>
  <template v-if="manage&&!cloudPool">
   <div v-for="row in rows" :key="row.id" class="preset-row"><span><b>{{row.name}}</b><small>{{row.model}}</small></span><button :disabled="busy" @click="edit(row)">编辑</button><button :disabled="busy" @click="remove(row)">删除</button></div>
   <div v-if="editing" class="preset-editor"><label>预设名称<input v-model="draft.name" maxlength="100"></label><label>复用接口账号<select v-model="draft.provider"><option v-for="source in sources" :key="source.value" :value="source.value">{{source.source==='official'?source.label:'自定义兼容接口 · '+source.family_label}}</option></select></label><label>完整模型 ID<input v-model.trim="draft.model" placeholder="填写接口文档中的完整模型调用名" maxlength="256"></label><label v-if="draft.provider==='custom'">思考模式<select v-model="draft.thinking"><option value="follow">跟随接口默认</option><option value="disabled">强制关闭（需接口支持）</option><option value="enabled">强制开启（需接口支持）</option></select></label><div><button :disabled="busy||!draft.name||!draft.model" @click="save">保存模型预设</button><button :disabled="busy" @click="editing=false">取消</button></div></div>
  </template>
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
<style scoped>.language-presets{padding:16px;border:1px solid var(--border,#35423f);border-radius:12px;margin:14px 0}.preset-heading,.preset-switch,.preset-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.preset-heading{margin-bottom:12px}.preset-heading small{color:var(--muted)}.preset-heading button{margin-left:auto}.preset-switch select{flex:1;min-width:180px}.language-presets p{font-size:12px;line-height:1.6}.preset-row{padding:10px 0;border-top:1px solid var(--border,#35423f)}.preset-row>span{flex:1;min-width:160px;overflow-wrap:anywhere}.preset-row small{display:block;color:var(--muted)}.preset-editor{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding-top:16px}.preset-editor label{display:grid;gap:6px}.preset-editor input,.preset-editor select{width:100%;min-width:0}.preset-editor>div{grid-column:1/-1;display:flex;gap:10px}@media(max-width:700px){.preset-editor{grid-template-columns:1fr}}</style>
