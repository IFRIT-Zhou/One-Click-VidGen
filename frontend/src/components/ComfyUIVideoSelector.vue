<script setup>
import {computed,onMounted,ref,watch} from 'vue'
import {requestJSON} from '../api'

const props=defineProps({form:{type:Object,required:true}})
const emit=defineEmits(['open-workbench'])
const profiles=ref([]),loading=ref(false),error=ref('')
const videoProfiles=computed(()=>profiles.value.filter(item=>item.kind==='video'))
const selected=computed(()=>videoProfiles.value.find(item=>item.id===props.form.comfyui_profile_id))
const supportsReferenceAudio=computed(()=>Boolean(selected.value?.mappings?.audio?.node_id&&selected.value?.mappings?.audio?.input_name))
const audioProfiles=computed(()=>videoProfiles.value.filter(item=>item.mappings?.audio?.node_id&&item.mappings?.audio?.input_name))
async function load(){loading.value=true;error.value='';try{const data=await requestJSON('/api/comfyui');profiles.value=data.profiles||[]}catch(e){error.value=e.message}finally{loading.value=false}}
function chooseBackend(value){props.form.video_generation_backend=value;if(value==='comfyui'&&!props.form.comfyui_profile_id&&videoProfiles.value.length)props.form.comfyui_profile_id=videoProfiles.value[0].id}
watch(videoProfiles,(items)=>{if(props.form.video_generation_backend==='comfyui'&&!items.some(item=>item.id===props.form.comfyui_profile_id))props.form.comfyui_profile_id=items[0]?.id||''})
// Keep the user's reference-audio intent while changing profiles. An
// incompatible saved profile is shown as invalid until a compatible profile
// is selected; silently clearing the switch made a later valid selection lose
// the setting before it could be saved.
watch(()=>props.form.video_generation_backend,value=>{if(value!=='comfyui')props.form.comfyui_reference_audio=false})
watch(()=>props.form.comfyui_h3_prompt_agent,enabled=>{if(enabled)props.form.comfyui_reference_audio=false})
function toggleReferenceAudio(event){
 const enabled=Boolean(event.target.checked)
 if(enabled&&!supportsReferenceAudio.value){
  const candidate=audioProfiles.value[0]
  if(!candidate){props.form.comfyui_reference_audio=false;error.value='还没有配置参考音频节点的跑视频预设，请先前往工作台配置。';return}
  props.form.comfyui_profile_id=candidate.id
 }
 props.form.comfyui_reference_audio=enabled
}
onMounted(load)
</script>

<template>
 <section class="comfy-video-selector">
  <div class="selector-copy"><div class="sidebar-label">动态镜头生成</div><strong>选择视频由哪里生成</strong><small>此选择会保存进当前项目；以后修改全局配置不会悄悄切换已创建项目。</small></div>
  <div class="backend-options" role="group" aria-label="动态镜头生成方式"><button type="button" :class="{active:form.video_generation_backend!=='comfyui'}" @click="chooseBackend('api')"><b>视频 API</b><small>使用“接口与服务”中的视频模型</small></button><button type="button" :class="{active:form.video_generation_backend==='comfyui'}" @click="chooseBackend('comfyui')"><b>本地 ComfyUI</b><small>调用已保存的跑视频工作流</small></button></div>
  <div v-if="form.video_generation_backend==='comfyui'" class="local-profile-row"><label><span>跑视频工作流</span><select v-model="form.comfyui_profile_id" :disabled="loading||!videoProfiles.length"><option value="">{{loading?'正在读取…':'请选择工作流预设'}}</option><option v-for="item in videoProfiles" :key="item.id" :value="item.id">{{item.name}}</option></select></label><button type="button" @click="load">刷新</button><button type="button" @click="emit('open-workbench')">管理工作流 →</button></div>
  <label v-if="form.video_generation_backend==='comfyui'&&!form.comfyui_h3_prompt_agent" class="h3-agent-option">
   <input v-model="form.comfyui_h3_prompt_agent" type="checkbox">
   <span><b>启用 H3 提示词转换 Agent</b><small>提交本地工作流前，按官方 H3 Skill 把通用视频提示词转换为 H3 六段式专用提示词。通用稿不会被覆盖。</small></span>
  </label>
  <label v-if="form.video_generation_backend==='comfyui'" class="h3-agent-option">
   <input :checked="form.comfyui_reference_audio" type="checkbox" @change="toggleReferenceAudio">
   <span><b>传入本镜 TTS 参考音频</b><small>按镜头时间范围裁剪已确认配音，注入工作流的参考音频节点，供模型参考节奏与动作。最终合成仍使用原配音。</small><small v-if="!supportsReferenceAudio" class="selector-warning-inline">{{audioProfiles.length?'开启后将自动切换到“'+audioProfiles[0].name+'”。':'当前没有映射参考音频节点的跑视频预设。'}}</small></span>
  </label>
  <div v-if="form.video_generation_backend==='comfyui'&&!loading&&!videoProfiles.length" class="selector-warning">还没有“跑视频”预设。请先到 ComfyUI 工作台保存工作流。</div>
  <small v-else-if="selected" class="selected-note">当前项目将使用：{{selected.name}} · {{selected.nodes?.length||0}} 个节点</small><small v-if="error" class="selector-error">{{error}}</small>
 </section>
</template>

<style scoped>
.comfy-video-selector{display:grid;gap:13px;padding:15px;border:1px solid var(--border,#34403e);border-radius:10px;background:color-mix(in srgb,var(--panel,#1c2221) 84%,#000)}.selector-copy{display:grid;gap:4px}.selector-copy small,.selected-note{color:var(--muted,#9aaba5);line-height:1.6}.backend-options{display:grid;grid-template-columns:1fr 1fr;gap:9px}.backend-options button{display:grid;gap:5px;text-align:left;padding:12px;background:transparent}.backend-options button.active{border-color:var(--accent,#83dec5);background:#25352f}.backend-options small{color:var(--muted,#9aaba5);white-space:normal}.local-profile-row{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:8px;align-items:end}.local-profile-row label{display:grid;gap:6px;font-size:12px}.local-profile-row select{width:100%}.h3-agent-option{display:flex;gap:10px;align-items:flex-start;padding:12px;border:1px solid color-mix(in srgb,var(--accent,#83dec5) 28%,var(--border,#34403e));border-radius:9px;background:#202b28;cursor:pointer}.h3-agent-option input{margin-top:3px}.h3-agent-option span{display:grid;gap:4px}.h3-agent-option small{color:var(--muted,#9aaba5);line-height:1.55}.h3-agent-option.disabled{opacity:.7;cursor:not-allowed}.h3-agent-option .selector-warning-inline{color:#e7bf87}.selector-warning{padding:10px 12px;border-radius:8px;background:#3b3025;color:#e7bf87;font-size:12px}.selector-error{color:#e6a08d}@media(max-width:680px){.backend-options{grid-template-columns:1fr}.local-profile-row{grid-template-columns:1fr}.local-profile-row button{width:100%}}
</style>
