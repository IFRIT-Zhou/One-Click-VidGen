<script setup>
import IcanChannelHint from './IcanChannelHint.vue'
import CloudImageQuality from './CloudImageQuality.vue'
import { imageChoice } from '../icanImageSizes'
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { taskImagePoolEnabled } from '../taskImagePool'

// Task-side image route selector. Profile management lives in services/ImageServicePanel.vue.
const props = defineProps({ form: { type: Object, required: true }, authenticated: Boolean })
const poolEnabled = computed(() => taskImagePoolEnabled(props.form, props.authenticated))
function toggleTaskPool(event) {
 props.form.task_image_pool = event.target.checked
 if (!poolEnabled.value) syncResolution()
}
const profiles = ref([])
const message = ref('')
const selected = computed(() => profiles.value.find(item => item.id === props.form.image_profile_id) || null)
const cloudSummary = computed(() => props.form.method === 'ican'
 ? `ICAN · GPT Image 2.5 · ${imageChoice(props.form.size).quality}`
 : `稳定版 · ${(props.form.image_resolution || '2k').toUpperCase()}`)
const resolutions = computed(() => selected.value?.resolutions || ['1k','2k','4k'])

async function load(){try{profiles.value=(await api.imageProfiles()).profiles||[];if(!profiles.value.some(x=>x.id===props.form.image_profile_id))props.form.image_profile_id=profiles.value.find(x=>x.configured)?.id||'';syncResolution()}catch(e){message.value=e.message}}
function syncResolution(){if(poolEnabled.value)return;if(!resolutions.value.includes(props.form.image_resolution))props.form.image_resolution=resolutions.value[0]||'1k'}
watch(()=>props.form.image_profile_id,syncResolution)
onMounted(load)
</script>

<template>
 <section class="image-profile-selector">
  <div class="profile-head"><div><strong>本任务图像模型</strong><small>{{poolEnabled?'选择本任务的云端图片渠道':'使用已配置的个人 API；仅影响本任务出图。'}}</small></div><label class="task-pool-toggle" :title="authenticated?'仅切换本任务图像渠道':'请先登录云端账户'"><span>使用集群</span><input type="checkbox" role="switch" :checked="poolEnabled" :disabled="!authenticated" @change="toggleTaskPool" /></label></div>
  <div v-if="poolEnabled" class="cloud-profile-settings"><strong class="cloud-profile-summary">{{cloudSummary}}</strong><label class="stack"><span>图片生成渠道</span><IcanChannelHint :active="form.method === 'ican'"><select v-model="form.method"><option value="running">稳定版</option><option value="ican">ICAN · GPT Image 2.5</option></select></IcanChannelHint></label><CloudImageQuality :form="form" compact /></div>
  <template v-if="!poolEnabled">
   <div class="profile-select-row">
    <label><span>模型配置</span><select v-model="form.image_profile_id" :disabled="!profiles.length"><option value="">{{profiles.length?'请选择':'尚未配置'}}</option><option v-for="p in profiles" :key="p.id" :value="p.id" :disabled="!p.configured">{{p.name}} · {{p.model_id}}{{p.configured?'':'（缺少 Key）'}}</option></select></label>
    <label v-if="selected?.protocol==='openai_sync'"><span>同步接口出图尺寸</span><input :value="selected.size||'auto'" disabled /><small>由模型配置的 size 控制；需修改时编辑该配置。</small></label>
    <label v-else><span>出图分辨率</span><select v-model="form.image_resolution" :disabled="!selected"><option v-for="r in resolutions" :key="r" :value="r">{{r.toUpperCase()}}</option></select></label>
   </div>
  </template>
  <p v-if="message" class="profile-message">{{message}}</p>
 </section>
</template>

<style scoped>
.task-pool-toggle{display:flex!important;align-items:center;gap:8px;white-space:nowrap;font-size:12px}.task-pool-toggle input{appearance:none;width:38px!important;height:22px!important;min-height:0!important;padding:2px!important;border-radius:20px;background:#46525d;cursor:pointer}.task-pool-toggle input:before{content:'';display:block;width:16px;height:16px;border-radius:50%;background:white;transition:transform .15s}.task-pool-toggle input:checked{background:var(--ocv-accent,#72cdb7)}.task-pool-toggle input:checked:before{transform:translateX(15px)}.task-pool-toggle input:disabled{opacity:.45;cursor:not-allowed}
.cloud-profile-settings{display:grid;grid-template-columns:minmax(0,1fr);gap:12px;min-width:0}.cloud-profile-settings>.stack{display:grid;gap:7px;min-width:0}.cloud-profile-settings select{width:100%;min-width:0;box-sizing:border-box}.cloud-profile-summary{font-size:14px;color:var(--ocv-accent,#7ddac5);overflow-wrap:anywhere}
.image-profile-selector{display:grid;gap:14px;padding:18px;border:1px solid var(--ocv-border,#35433e);border-radius:14px;background:color-mix(in srgb,var(--ocv-panel,#202825) 88%,transparent)}.profile-head,.profile-select-row{display:flex;gap:12px;align-items:center}.profile-head{justify-content:space-between}.profile-head>div{display:grid;gap:4px}.profile-head small{display:block;color:var(--ocv-muted,#9aa6a1)}.profile-select-row label{flex:1;display:grid;gap:7px}.profile-message{margin:0;color:var(--ocv-accent,#7ddac5)}.disabled{opacity:.72}@media(max-width:760px){.profile-select-row{display:grid;grid-template-columns:1fr}}
</style>
