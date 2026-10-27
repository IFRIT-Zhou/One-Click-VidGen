<script setup>
import {computed,ref,watch,nextTick,onUnmounted} from 'vue'
import {requestJSON} from '../api'
const props=defineProps({project:{type:Object,required:true},disabled:Boolean,hasDrafts:Boolean})
const emit=defineEmits(['update'])
const dialog=ref(null),opening=ref(false),loading=ref(false),submitting=ref(false),error=ref(''),plan=ref(null)
const notes=ref({}),subtitles=ref(true),transparency=ref(20)
const task=computed(()=>props.project.storyboard_preview_task||{})
const running=computed(()=>task.value.status==='running')
const ready=computed(()=>props.project.shots?.length&&props.project.audio&&props.project.shots.every(s=>s.image&&s.image_status==='completed'&&s.image_task?.status!=='running')&&!['planning','stopping','image_generating','image_stopping','exporting'].includes(props.project.status))
const reason=computed(()=>props.hasDrafts?'请先保存镜头修改，再导出当前版本':!ready.value?'请先完成全部核心分镜图和配音':'用分镜图片和最终配音导出带动静标记的预览，不需要生成动态视频')
const fileUrl=computed(()=>'/api/video-studio/'+props.project.id+'/storyboard-preview/file?v='+encodeURIComponent(props.project.storyboard_preview?.task_id||''))
const base=()=>'/api/video-studio/'+props.project.id+'/storyboard-preview'
let timer,polling=false,closed=false,focusBefore
function accept(result,id){
 if(closed||id!==props.project.id)return
 emit('update',{id,revision:result.revision,storyboard_preview_task:result.task,storyboard_preview:result.preview})
}
async function poll(){
 if(polling||submitting.value)return
 polling=true;const id=props.project.id
 try{const result=await requestJSON(base()+'/plan');accept(result,id)}catch(e){if(opening.value)error.value=e.message}finally{polling=false}
}
watch(running,value=>{clearInterval(timer);if(value){poll();timer=setInterval(poll,2500)}},{immediate:true})
async function open(){
 if(props.disabled)return
 if(!opening.value)focusBefore=document.activeElement
 opening.value=true;error.value='';plan.value=null
 await nextTick();if(!dialog.value.open)dialog.value.showModal();loading.value=true
 const id=props.project.id
 try{
  const result=await requestJSON(base()+'/plan')
  if(id!==props.project.id||closed)return
  plan.value=result;notes.value=Object.fromEntries(result.shots.map(s=>[s.id,s.text]));accept(result,id)
 }catch(e){error.value=e.message}finally{loading.value=false}
}
function close(){dialog.value?.close();opening.value=false;focusBefore?.focus?.()}
async function submit(){
 if(!plan.value||running.value||submitting.value||!ready.value||props.hasDrafts||props.disabled)return
 submitting.value=true;error.value='';const id=props.project.id
 try{
  const record=await requestJSON(base(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:plan.value.revision,notes:notes.value,subtitles:subtitles.value,background_transparency:Number(transparency.value)})})
  if(id!==props.project.id||closed)return
  emit('update',record)
 }catch(e){error.value=e.message}finally{submitting.value=false}
}
onUnmounted(()=>{closed=true;clearInterval(timer);dialog.value?.close()})
</script>

<template>
 <button type="button" class="ghost-btn storyboard-preview-entry" :disabled="disabled" :title="reason" @click="open">{{running?'分镜预览合成中…':'分镜预览'}}</button>
 <Teleport to="body">
  <dialog ref="dialog" class="storyboard-preview-dialog" aria-labelledby="storyboard-preview-title" @cancel.prevent="close" @click="e=>{if(e.target===dialog)close()}">
   <header><div><h2 id="storyboard-preview-title">导出分镜预览</h2><p>先看文案与画面节奏，再决定是否生成动态视频。</p></div><button type="button" aria-label="关闭分镜预览" @click="close">关闭</button></header>
   <div class="storyboard-preview-content">
    <p class="preview-explanation"><span class="preview-badge motion">动</span>后续生成动态 <span class="preview-badge still">静</span>保持静态 · 本预览全部使用核心分镜图，配合最终配音与字幕。</p>
    <p v-if="error" class="preview-error" role="alert">{{error}}</p>
    <p v-if="loading" role="status">正在读取当前分镜…</p>
    <p v-if="running" class="preview-status" role="status">正在本地合成，可以关闭窗口继续工作。不调用图片或视频模型，也不会改变正式制作进度。</p>
    <p v-else-if="task.status==='failed'" class="preview-error" role="alert">{{task.message}}</p>
    <section v-if="project.storyboard_preview?.file" class="preview-result">
     <h3>最近完成的预览</h3>
     <video v-if="opening" :key="fileUrl" controls playsinline preload="metadata" :src="fileUrl"/>
     <a :href="fileUrl" download="分镜预览.mp4">下载分镜预览 MP4</a><small>独立预览文件，不替代正式成片；之后修改的内容需重新导出。</small>
    </section>
    <template v-if="plan">
     <div class="preview-options"><label><input type="checkbox" v-model="subtitles" :disabled="running||submitting">显示配音字幕</label><label>说明框背景透明度 <select v-model="transparency" :disabled="running||submitting"><option :value="0">0% · 不透明</option><option :value="20">20%</option><option :value="50">50%</option><option :value="80">80%</option><option :value="100">100% · 全透明</option></select></label></div>
     <details :open="!project.storyboard_preview?.file&&!running">
      <summary>核对动作说明 · {{plan.shots.length}} 镜</summary>
      <p class="preview-help">仅显示动作与结尾，已排除常见模板说明，不使用 H3 扩写。可以在这里修正提取结果，不会修改原提示词。右下角文本框小于画面 1/4，长文本自动分页。</p>
      <article v-for="shot in plan.shots" :key="shot.id" class="preview-shot">
       <div><span :class="['preview-badge',shot.kind==='video'?'motion':'still']">{{shot.kind==='video'?'动':'静'}}</span><strong>第 {{String(shot.index).padStart(2,'0')}} 镜</strong><small>{{Number(shot.start).toFixed(1)}}–{{Number(shot.end).toFixed(1)}} 秒</small><small v-if="!shot.image_ready" class="preview-error">核心图未完成</small></div>
       <label v-if="shot.kind==='video'"><span class="preview-help">右下角动作说明</span><textarea v-model="notes[shot.id]" rows="4" maxlength="6000" :disabled="running||submitting" :aria-label="'第 '+shot.index+' 镜动作说明'" placeholder="没有提取到动作说明，可在此填写；留空则不显示文本框。"/></label>
       <p v-else class="preview-help">静态画面，只显示“静”标记和配音字幕。</p>
      </article>
     </details>
    </template>
   </div>
   <footer><span>{{hasDrafts?'请先保存镜头修改':!ready?'请先完成全部核心分镜图和配音':'仅本地合成 · 保留正式制作进度'}}</span><button v-if="error&&!running" type="button" :disabled="loading||submitting" @click="open">重新读取分镜</button><button type="button" class="primary-btn" :disabled="!plan||loading||running||submitting||disabled||hasDrafts||!ready" @click="submit">{{running||submitting?'正在合成…':'导出分镜预览'}}</button></footer>
  </dialog>
 </Teleport>
</template>

<style scoped>
.storyboard-preview-dialog button{border:1px solid #49605a;border-radius:10px;background:#293532;color:#edf5f4;padding:10px 16px;font:inherit;cursor:pointer}.storyboard-preview-dialog button:hover:not(:disabled){background:#354740}.storyboard-preview-dialog button.primary-btn{background:#81dcc6;color:#102d25;border-color:#81dcc6;box-shadow:none;font-weight:700}.storyboard-preview-dialog button.primary-btn:hover:not(:disabled){background:#a0e9d7}.storyboard-preview-dialog button:focus-visible,.storyboard-preview-dialog textarea:focus-visible{outline:2px solid #81dcc6;outline-offset:3px}.storyboard-preview-dialog .preview-shot textarea{font:inherit;line-height:1.7}
.storyboard-preview-dialog{background:#1d2726;color:#edf5f4;border:1px solid #526c66;border-radius:18px;padding:0;width:min(920px,calc(100vw - 32px));max-height:90vh;box-sizing:border-box;box-shadow:0 24px 80px #0008}.storyboard-preview-dialog::backdrop{background:#000a}.storyboard-preview-dialog[open]{display:flex;flex-direction:column}.storyboard-preview-dialog header,.storyboard-preview-dialog footer{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:20px 24px;flex-shrink:0}.storyboard-preview-dialog header{border-bottom:1px solid #354945}.storyboard-preview-dialog h2{margin:0;font-size:22px}.storyboard-preview-dialog h3{font-size:16px;margin:0 0 12px}.storyboard-preview-dialog header p,.preview-help,.preview-explanation,.storyboard-preview-dialog small,.storyboard-preview-dialog footer span{color:#a8bbb5;font-size:13px;line-height:1.7}.storyboard-preview-dialog header p{margin:6px 0 0}.storyboard-preview-dialog button{white-space:nowrap}.storyboard-preview-content{overflow-y:auto;min-height:0;padding:16px 24px}.preview-explanation{margin:0 0 16px}.preview-badge{display:inline-flex;align-items:center;justify-content:center;border-radius:50%;width:27px;height:27px;font-size:16px;color:white;flex-shrink:0;vertical-align:middle;margin-right:6px}.preview-badge.motion{background:#239957}.preview-badge.still{background:#cf4149;margin-left:12px}.preview-error{color:#ffb0b0!important}.preview-status{padding:14px;background:#243d37;border-radius:10px;font-size:14px;line-height:1.7}.preview-result video{width:100%;max-height:45vh;display:block;background:#101514}.preview-result a{display:inline-block;color:#8edccb;margin:12px 12px 12px 0}.preview-options{display:flex;align-items:center;gap:24px;flex-wrap:wrap;margin:20px 0}.preview-options label{display:flex;align-items:center;gap:10px;font-size:14px}.preview-options input{width:auto}.preview-options select{width:auto}.storyboard-preview-dialog summary{cursor:pointer;padding:12px 0;font-weight:600}.preview-shot{border-top:1px solid #354945;padding:16px 0}.preview-shot>div{display:flex;align-items:center;gap:8px;margin-bottom:10px}.preview-shot .preview-badge.still{margin-left:0}.preview-shot label{display:block}.preview-shot textarea{display:block;box-sizing:border-box;width:100%;resize:vertical;min-height:100px;margin-top:5px;background:#141c1b;color:#edf5f4;border:1px solid #49605a;border-radius:10px;padding:12px;line-height:1.7;font:inherit}.storyboard-preview-dialog footer{border-top:1px solid #354945}.storyboard-preview-dialog button:disabled{opacity:.45;cursor:not-allowed}@media(max-width:600px){.storyboard-preview-dialog header,.storyboard-preview-dialog footer,.storyboard-preview-content{padding:16px}.preview-options{gap:12px}.storyboard-preview-dialog footer{flex-wrap:wrap}.preview-shot>div{flex-wrap:wrap}.preview-options label{flex-wrap:wrap}.storyboard-preview-dialog footer button{width:100%}}
</style>
