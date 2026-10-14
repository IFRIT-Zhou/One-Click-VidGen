<script setup>
import {ref,watch} from 'vue'
import {requestJSON} from '../api'
const props=defineProps({projectId:String,taskStatus:String})
const emit=defineEmits(['adopted'])
const pending=ref(null),busy=ref(false),error=ref('')
watch(()=>[props.projectId,props.taskStatus],async()=>{
 const id=props.projectId;pending.value=null
 if(!id||props.taskStatus==='running')return
 try{const data=await requestJSON(`/api/jobs/${id}/tts-editor/status`);if(props.projectId===id)pending.value=data.pending_alignment||null}catch{}
},{immediate:true})
async function adopt(){
 if(!confirm('采用已生成的新配音，并按原字幕边界比例估算新时间？不会重新调用 TTS。字幕可能不同步，请试听检查；旧版本可撤回。'))return
 busy.value=true;error.value=''
 try{await requestJSON(`/api/jobs/${props.projectId}/tts-editor/adopt-estimated`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:pending.value.token,confirmed:true})});pending.value=null;emit('adopted')}
 catch(e){error.value=e.message}finally{busy.value=false}
}
</script>
<template>
 <section v-if="pending" class="pending-recovery">
  <strong>新配音已保留，等待你确认</strong>
  <p>可先试听以下新配音。采用后字幕时间为估算值，请检查同步；不会再次调用 TTS，旧版本可撤回。</p>
  <div v-for="index in pending.indices" :key="index"><span>第 {{index}} 段 · 新配音</span><audio controls preload="none" :src="`/api/jobs/${projectId}/tts-editor/pending-audio/${index}?token=${encodeURIComponent(pending.token)}`" /></div>
  <button :disabled="busy" @click="adopt">{{busy?'正在采用…':'采用新配音，使用估算字幕时间'}}</button>
  <p v-if="error" role="alert">{{error}}</p>
 </section>
</template>
<style scoped>
.pending-recovery{padding:16px;margin:12px 0;border:1px solid var(--border);border-radius:12px;background:var(--panel);color:var(--text)}p{font-size:13px;color:var(--muted);line-height:1.6}.pending-recovery div{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin:10px 0}audio{height:36px;max-width:100%}button{margin-top:8px}
</style>
