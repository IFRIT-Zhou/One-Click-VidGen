<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps({ form: { type: Object, required: true }, manage: Boolean })
const profiles = ref([])
const message = ref('')
const saving = ref(false)
const editing = ref(false)
const draft = reactive({ id:'', name:'', protocol:'async_task', base_url:'', model_id:'', text_endpoint:'/openapi/v2/{model}/text-to-image', reference_endpoint:'/openapi/v2/{model}/image-to-image', query_endpoint:'/openapi/v2/query', resolutions:['1k','2k','4k'], reference_images:true, api_key:'' })
const selected = computed(() => profiles.value.find(item => item.id === props.form.image_profile_id) || null)
const resolutions = computed(() => selected.value?.resolutions || ['1k','2k','4k'])

function reset(profile=null){Object.assign(draft,profile?{...profile,api_key:'',resolutions:[...(profile.resolutions||['1k','2k','4k'])]}:{id:'',name:'',protocol:'async_task',base_url:'',model_id:'',text_endpoint:'/openapi/v2/{model}/text-to-image',reference_endpoint:'/openapi/v2/{model}/image-to-image',query_endpoint:'/openapi/v2/query',resolutions:['1k','2k','4k'],reference_images:true,api_key:''});editing.value=true}
async function load(){try{profiles.value=(await api.imageProfiles()).profiles||[];if(!profiles.value.some(x=>x.id===props.form.image_profile_id))props.form.image_profile_id=profiles.value.find(x=>x.configured)?.id||'';syncResolution()}catch(e){message.value=e.message}}
function syncResolution(){if(!resolutions.value.includes(props.form.image_resolution))props.form.image_resolution=resolutions.value[0]||'1k'}
async function save(){saving.value=true;message.value='';try{const payload={...draft,resolutions:[...draft.resolutions]};if(!payload.api_key.trim())delete payload.api_key;const result=await api.saveImageProfile(payload);message.value=result.message;await load();props.form.image_profile_id=result.profile.id;editing.value=false}catch(e){message.value=e.message}finally{saving.value=false}}
async function remove(profile){if(!confirm(`删除“${profile.name}”？`))return;try{await api.deleteImageProfile(profile.id);await load()}catch(e){message.value=e.message}}
function toggleResolution(value){const list=draft.resolutions;const index=list.indexOf(value);if(index>=0&&list.length>1)list.splice(index,1);else if(index<0)list.push(value)}
watch(()=>props.form.image_profile_id,syncResolution)
onMounted(load)
</script>

<template>
 <section class="image-profile-selector" :class="{manager:manage,disabled:form.use_cloud_image_pool}">
  <div class="profile-head"><div><strong>{{manage?'图像模型配置':'本任务图像模型'}}</strong><small>{{form.use_cloud_image_pool?'号池模型由云端统一管理':manage?'每个模型、接口和 Key 独立保存，不预设任何中转商。':'仅影响本任务；不会改动接口与服务中的配置。'}}</small></div><button v-if="manage&&!editing" type="button" @click="reset()">＋ 新增模型</button></div>
  <template v-if="!form.use_cloud_image_pool">
   <div v-if="!manage||!editing" class="profile-select-row">
    <label><span>模型配置</span><select v-model="form.image_profile_id" :disabled="!profiles.length"><option value="">{{profiles.length?'请选择':'尚未配置'}}</option><option v-for="p in profiles" :key="p.id" :value="p.id" :disabled="!p.configured">{{p.name}} · {{p.model_id}}{{p.configured?'':'（缺少 Key）'}}</option></select></label>
    <label><span>出图分辨率</span><select v-model="form.image_resolution" :disabled="!selected"><option v-for="r in resolutions" :key="r" :value="r">{{r.toUpperCase()}}</option></select></label>
   </div>
   <div v-if="manage&&!editing" class="profile-list"><article v-for="p in profiles" :key="p.id"><div><b>{{p.name}}</b><small>{{p.model_id}} · {{p.protocol==='async_task'?'异步任务接口':'兼容接口'}} · {{p.key_count}} 个 Key</small></div><button type="button" @click="reset(p)">编辑</button><button v-if="!p.legacy" type="button" @click="remove(p)">删除</button></article><div v-if="!profiles.length" class="profile-empty"><div class="profile-empty-title"><div><span>填写示范</span><b>Image 2.5 · 我的接口</b></div><em>仅为格式示例</em></div><dl><div><dt>API Base URL</dt><dd>https://api.example.com</dd></div><div><dt>模型 ID</dt><dd>image-2.5</dd></div><div><dt>API Key</dt><dd>填写服务商提供的密钥</dd></div><div><dt>支持分辨率</dt><dd>1K / 2K / 4K</dd></div></dl><p>实际内容请以你的接口服务商文档为准。OCV 不指定或推荐第三方服务商。</p><button class="primary" type="button" @click="reset()">＋ 按格式新增配置</button></div></div>
   <div v-if="manage&&editing" class="profile-editor">
    <label><span>配置名称</span><input v-model="draft.name" placeholder="例如：Image 2.5 · 我的中转接口" /></label>
    <label><span>接口协议</span><select v-model="draft.protocol"><option value="async_task">通用异步任务接口</option></select></label>
    <label class="wide"><span>API Base URL</span><input v-model="draft.base_url" placeholder="https://api.example.com" /></label>
    <label><span>模型 ID</span><input v-model="draft.model_id" placeholder="填写服务商提供的模型 ID" /></label>
    <label><span>API Key</span><input v-model="draft.api_key" type="password" :placeholder="draft.id?'已保存；不修改可留空':'填写 API Key；多个可用逗号分隔'" /></label>
    <details class="wide"><summary>接口路径与能力（高级）</summary><div class="endpoint-grid"><label><span>文生图路径</span><input v-model="draft.text_endpoint" /></label><label><span>参考生图路径</span><input v-model="draft.reference_endpoint" /></label><label><span>查询路径</span><input v-model="draft.query_endpoint" /></label></div></details>
    <div class="wide profile-capabilities"><span>支持分辨率</span><label v-for="r in ['1k','2k','4k']" :key="r"><input type="checkbox" :checked="draft.resolutions.includes(r)" @change="toggleResolution(r)" /> {{r.toUpperCase()}}</label><label><input v-model="draft.reference_images" type="checkbox" /> 支持参考图</label></div>
    <div class="wide profile-actions"><button type="button" @click="editing=false">取消</button><button class="primary" type="button" :disabled="saving||!draft.name||!draft.base_url||!draft.model_id" @click="save">{{saving?'保存中…':'保存模型配置'}}</button></div>
   </div>
  </template>
  <p v-if="message" class="profile-message">{{message}}</p>
 </section>
</template>

<style scoped>
.image-profile-selector{display:grid;gap:14px;padding:18px;border:1px solid var(--ocv-border,#35433e);border-radius:14px;background:color-mix(in srgb,var(--ocv-panel,#202825) 88%,transparent)}.profile-head,.profile-select-row,.profile-list article,.profile-actions{display:flex;gap:12px;align-items:center}.profile-head{justify-content:space-between}.profile-head>div,.profile-list article>div{display:grid;gap:4px}.profile-head small,.profile-list small{display:block;color:var(--ocv-muted,#9aa6a1)}.profile-select-row label{flex:1;display:grid;gap:7px}.profile-list{display:grid;gap:8px}.profile-list article{padding:10px 12px;border:1px solid var(--ocv-border,#35433e);border-radius:10px}.profile-list article>div{flex:1}.profile-editor{display:grid;grid-template-columns:1fr 1fr;gap:12px}.profile-editor label{display:grid;gap:6px}.wide{grid-column:1/-1}.endpoint-grid{display:grid;gap:10px;margin-top:12px}.profile-capabilities{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.profile-capabilities label{display:flex;grid-auto-flow:column;align-items:center}.profile-actions{justify-content:flex-end}.profile-message{margin:0;color:var(--ocv-accent,#7ddac5)}.disabled{opacity:.72}@media(max-width:760px){.profile-select-row,.profile-editor{display:grid;grid-template-columns:1fr}.wide{grid-column:auto}}
.profile-empty{display:grid;gap:14px;padding:16px;border:1px dashed color-mix(in srgb,var(--ocv-accent,#7ddac5) 55%,var(--ocv-border,#35433e));border-radius:12px;background:color-mix(in srgb,var(--ocv-accent,#7ddac5) 5%,transparent)}.profile-empty-title{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.profile-empty-title>div{display:grid;gap:4px}.profile-empty-title span{font-size:.8rem;color:var(--ocv-accent,#7ddac5)}.profile-empty-title em{padding:4px 8px;border-radius:999px;background:color-mix(in srgb,var(--ocv-accent,#7ddac5) 12%,transparent);color:var(--ocv-muted,#9aa6a1);font-size:.75rem;font-style:normal}.profile-empty dl{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0}.profile-empty dl>div{display:grid;gap:3px;padding:9px 10px;border-radius:8px;background:color-mix(in srgb,var(--ocv-panel,#202825) 80%,transparent)}.profile-empty dt{font-size:.76rem;color:var(--ocv-muted,#9aa6a1)}.profile-empty dd{margin:0;overflow-wrap:anywhere}.profile-empty p{margin:0;color:var(--ocv-muted,#9aa6a1);font-size:.82rem}.profile-empty button{justify-self:start}
</style>
