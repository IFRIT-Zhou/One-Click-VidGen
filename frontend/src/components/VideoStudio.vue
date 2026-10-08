<script setup>
import {computed,ref,watch,nextTick,onMounted,onUnmounted} from 'vue'
import {requestJSON} from '../api'
import { imageSize } from '../icanImageSizes'
import ParameterReview from './ParameterReview.vue'
import VideoShotNavigator from './VideoShotNavigator.vue'
import ShotVideoOptions from './ShotVideoOptions.vue'
import OperationStatus from './OperationStatus.vue'
import LanguageModelPresets from './LanguageModelPresets.vue'
import ResizableShotWorkspace from './ResizableShotWorkspace.vue'
import WorkspacePanels from './WorkspacePanels.vue'
import DynamicTextModeSelector from './DynamicTextModeSelector.vue'
import SubtitleStyleEditor from './SubtitleStyleEditor.vue'
import ShotReferencePreview from './ShotReferencePreview.vue'
import { normalizeDynamicTextMode, dynamicTextModeLabel } from '../dynamicTextMode'
import { shotPromptNotes, shotHasPromptWarning, promptWarningRows } from '../videoPromptWarnings'
const emit=defineEmits(['new-project','duplicate-config','edit-audio','edit-config'])
const items=ref([]), project=ref(null), selected=ref(0), error=ref(''), busy=ref(false), dirty=ref(false)
const view=ref('storyboard')
watch(view,value=>{if(value!=='storyboard'&&selected.value<0)selected.value=0})
const exportPreview=ref('raw')
const workspaceTitles={audio:'配音与字幕',storyboard:'动态分镜',motions:'动态镜头',export:'合成与导出',parameters:'参数回顾'}
const workspaceHints={audio:'试听配音、核对字幕；需要调整时进入配音精修。',storyboard:'逐镜确认画面与提示词，再将核心图制作成动态片段。',motions:'逐镜试看、重新生成或替换本地视频，满意后合成成片。',export:'预览和下载成片；合成选项可随时调整，不必重新生成镜头。',parameters:'查看本项目保存的配置，也可以用相同配置新建作品。'}
function moveShot(delta){selected.value=Math.max(0,Math.min((project.value?.shots.length||1)-1,selected.value+delta));boundary.value=1;closeBoundaryEditor()}
const motionDrafts=ref({})
function rememberMotionDraft(shot){if(project.value?.status==='image_review'){motionDrafts.value[shot.id]={intent:shot.intent||'',kind:shot.kind,action:shot.action||'',video_prompt:shot.video_prompt||'',reference_audio_enabled:shot.reference_audio_enabled!==false,reference_audio_lipsync:shot.reference_audio_lipsync!==false};dirty.value=true}else dirty.value=true}
async function saveMotionDrafts(){for(const [id,fields] of Object.entries(motionDrafts.value)){const updated=await call('/'+project.value.id+'/shots/'+encodeURIComponent(id)+'/motion','POST',{revision:project.value.revision,...fields});delete motionDrafts.value[id];acceptImageUpdate(updated)}dirty.value=false;sessionStorage.removeItem(draftKey(project.value.id))}
const autoMotionBusy=ref(false)
async function autoFillMotion(shot){
 autoMotionBusy.value=true
 videoLogsOpen.value=true
 const shotNumber=project.value.shots.findIndex(row=>row.id===shot.id)+1
 project.value.logs.push(`第 ${String(shotNumber).padStart(2,'0')} 镜：正在按核心图规划动态表达与视频模型最终提示词…`)
 try{acceptImageUpdate(await call('/'+project.value.id+'/shots/'+encodeURIComponent(shot.id)+'/refresh-prompts','POST',{revision:project.value.revision,basis:'image',action:shot.action||'',image_prompt:shot.image_prompt||''}))}
 catch(e){project.value.logs.push(`第 ${String(shotNumber).padStart(2,'0')} 镜：动态表达与视频提示词更新失败：${e.message}`);throw e}
 finally{autoMotionBusy.value=false}
}
async function changeShotKind(shot,event){
 const kind=event.target.value,wasStatic=shot.kind==='static'
 await run(async()=>{
   if(project.value.status==='image_review'){
   if(dirty.value)await save()
   acceptImageUpdate(await call('/'+project.value.id+'/shots/'+encodeURIComponent(shot.id)+'/motion','POST',{revision:project.value.revision,kind,action:shot.action||'',video_prompt:shot.video_prompt||'',reference_audio_enabled:shot.reference_audio_enabled!==false,reference_audio_lipsync:shot.reference_audio_lipsync!==false}))
  }else{shot.kind=kind;dirty.value=true;await save()}
  const current=project.value.shots.find(row=>row.id===shot.id)
  if(wasStatic&&kind==='video'){
   try{await autoFillMotion(current)}
   catch(e){throw new Error('已切换为动态，但动态表达与视频提示词未更新成功：'+e.message+'。请点击“按核心图更新视频提示词”重试。')}
  }
 })
 event.target.value=project.value.shots.find(row=>row.id===shot.id)?.kind||shot.kind
}
const videoStages=['video_generation_ready','video_generating','video_stopping','video_review','exporting','export_failed','completed']
const videoModel=ref(null),videoModelError=ref(''),videoLogsOpen=ref(false)
const comfyProfiles=ref([]),comfyLoading=ref(false),comfyError=ref(''),shotOptionDrafts=ref({})
try{const saved=JSON.parse(sessionStorage.getItem('ocv.shot-options.drafts')||'{}');if(saved&&typeof saved==='object'&&!Array.isArray(saved))shotOptionDrafts.value=saved}catch{}
watch(shotOptionDrafts,value=>{try{sessionStorage.setItem('ocv.shot-options.drafts',JSON.stringify(value))}catch{}},{deep:true})
const apiVideoReady=computed(()=>videoModel.value?.source==='dedicated'&&videoModel.value?.has_api_key)
async function loadComfyProfiles(){comfyLoading.value=true;comfyError.value='';try{const data=await requestJSON('/api/comfyui');comfyProfiles.value=(data.profiles||[]).filter(item=>item.kind==='video')}catch(e){comfyError.value=e.message}finally{comfyLoading.value=false}}
function optionsFor(shot,includeDraft=true){
 const key=project.value.id+':'+shot.id
 if(includeDraft&&shotOptionDrafts.value[key])return shotOptionDrafts.value[key]
 const params=project.value.creation_parameters||{},saved=shot.video_generation_options||{},request=shot.video_request||{}
 return {backend:saved.backend||request.backend||(['api','comfyui'].includes(shot.video_backend)?shot.video_backend:params.video_generation_backend)||'api',profile_id:saved.profile_id??request.profile_id??params.comfyui_profile_id??comfyProfiles.value[0]?.id??'',resolution:saved.resolution??request.resolution??'',width:saved.width??request.width,height:saved.height??request.height,h3_prompt_agent:saved.h3_prompt_agent??(request.prompt_format==='h3_ref2va'||Boolean(params.comfyui_h3_prompt_agent))}
}
const normalizedOptions=value=>({backend:value.backend,profile_id:value.backend==='comfyui'?value.profile_id:'',resolution:value.resolution||'',h3_prompt_agent:value.backend==='comfyui'&&Boolean(value.h3_prompt_agent),...(value.resolution==='custom'?{width:value.width,height:value.height}:{})})
const selectedOptionsDirty=computed(()=>Boolean(selectedShot.value)&&JSON.stringify(normalizedOptions(optionsFor(selectedShot.value)))!==JSON.stringify(normalizedOptions(optionsFor(selectedShot.value,false))))
async function saveShotOptions(){const shot=selectedShot.value;if(!shot)return;await run(async()=>{project.value=await call('/'+project.value.id+'/shots/'+encodeURIComponent(shot.id)+'/generation-options','PUT',{revision:project.value.revision,options:normalizedOptions(optionsFor(shot))});delete shotOptionDrafts.value[project.value.id+':'+shot.id]})}
async function applyAllShotOptions(){const shot=selectedShot.value;if(!shot)return;const shots=dynamicShots.value;if(!confirm('将当前设置应用到全部 '+shots.length+' 个动态镜头？\n'+generationSummary(shot)+'\n会覆盖各镜头的生成配置；现有素材保留，点击不会开始生成。'))return;await run(async()=>{project.value=await call('/'+project.value.id+'/generation-options','PUT',{revision:project.value.revision,options:normalizedOptions(optionsFor(shot))});for(const item of shots)delete shotOptionDrafts.value[project.value.id+':'+item.id]})}
function generationSummary(shot){const options=optionsFor(shot),profile=comfyProfiles.value.find(item=>item.id===options.profile_id);return (options.backend==='comfyui'?(profile?.engine==='managed'?'OCV 内置 ComfyUI · ':'外部 ComfyUI · ')+(profile?.name||'未选择工作流'):'视频 API')+' · '+(options.resolution==='custom'?options.width+' × '+options.height:options.resolution||profile?.resolution_preset||videoModel.value?.resolution||'跟随设置')}
const shotConfigured=shot=>{const options=optionsFor(shot);return options.backend==='comfyui'?comfyProfiles.value.some(item=>item.id===options.profile_id):Boolean(apiVideoReady.value)}
function confirmLongApiShots(shots){
 const longShots=shots.filter(shot=>optionsFor(shot).backend==='api'&&Number(shot.generation_duration||Math.max(4,Math.ceil(Number(shot.duration)||0)))>15)
 if(!longShots.length)return true
 const lines=longShots.map(shot=>'第 '+String(project.value.shots.findIndex(item=>item.id===shot.id)+1).padStart(2,'0')+' 镜：请求 '+Number(shot.generation_duration||Math.max(4,Math.ceil(Number(shot.duration)||0)))+' 秒')
 return confirm('长镜头 API 兼容性提醒\n\n'+lines.join('\n')+'\n\n以上镜头超过建议的 15 秒。不同服务商／模型的时长上限不同，请确认当前视频 API 支持这些请求时长。\nOCV 将按原时长提交，不会自动裁短；不支持时可能失败，费用以服务商规则为准。\n\n确认支持并继续？取消后可拆分镜头或更换模型。')
}
async function submitVideoBatch(shots,{automated=false,regenerate=false}={}){
 if(!shots.length)return
 const unchecked=shots.filter(shot=>shot.design_needs_review)
 if(unchecked.length){
  const numbers=unchecked.map(shot=>String(project.value.shots.findIndex(item=>item.id===shot.id)+1).padStart(2,'0'))
  error.value='第 '+numbers.join('、')+' 镜字幕范围调整后，设计尚未更新确认。请返回分镜编辑，完整重规划本镜或按核心图更新视频提示词后再生成；本次未提交生成任务。'
  selected.value=project.value.shots.findIndex(item=>item.id===unchecked[0].id)
  return
 }
 if(shots.some(shot=>!shotConfigured(shot))){error.value='部分镜头尚未配置工作流或视频 API，请逐镜检查生成设置。';return}
 if(!confirmLongApiShots(shots))return
 const lines=shots.map(shot=>'第 '+String(project.value.shots.findIndex(item=>item.id===shot.id)+1).padStart(2,'0')+' 镜：'+generationSummary(shot))
 const paid=shots.filter(shot=>optionsFor(shot).backend==='api').length
 if(!automated&&!confirm((regenerate?'重新生成全部所选镜头，已有片段会保留到历史记录。':'生成所选未完成镜头。')+'\n按各镜头设置执行；页面内尚未保存的配置也会随本次提交保存。\n\n'+lines.join('\n')+'\n\n'+(paid?paid+' 个镜头使用视频 API，可能产生费用。':'全部使用 ComfyUI，内置／外部引擎按各镜头工作流执行。')+'\n是否开始？'))return
 const shot_options=Object.fromEntries(shots.map(shot=>[shot.id,normalizedOptions(optionsFor(shot))]))
 await run(async()=>{project.value=await call('/'+project.value.id+'/videos/generate','POST',{revision:project.value.revision,shot_ids:shots.map(shot=>shot.id),shot_options,regenerate_completed:regenerate});for(const shot of shots)delete shotOptionDrafts.value[project.value.id+':'+shot.id];view.value='motions';videoLogsOpen.value=true})
}
const selectedOptions=computed({get:()=>selectedShot.value?optionsFor(selectedShot.value):{backend:'api',resolution:'',profile_id:'',h3_prompt_agent:false},set:value=>{if(selectedShot.value)shotOptionDrafts.value[project.value.id+':'+selectedShot.value.id]=value}})
const selectedLocalVideo=computed(()=>selectedOptions.value.backend==='comfyui')
const selectedVideoConfigured=computed(()=>selectedLocalVideo.value?comfyProfiles.value.some(item=>item.id===selectedOptions.value.profile_id):apiVideoReady.value)
const shotOptionsLocked=computed(()=>busy.value||selectedShot.value?.video_status==='running'||Boolean(videoRunning.value&&selectedShot.value?.video_queued)||Boolean(selectedShot.value?.video_request&&!selectedShot.value?.video_terminal&&selectedShot.value?.video_status!=='completed'))
const localQueueRunning=computed(()=>videoRunning.value&&['comfyui','mixed'].includes(project.value?.active_video_backend||project.value?.creation_parameters?.video_generation_backend))
const videoStageReady=computed(()=>videoStages.includes(project.value?.status))
const videoRunning=computed(()=>['video_generating','video_stopping'].includes(project.value?.status))
const storyboardStageLabel=computed(()=>({stopping:'正在停止规划…',planning:'正在规划…',generation_ready:'分镜已确认，等待生成核心图',image_generating:'正在生成核心分镜图…',image_stopping:'正在停止核心图生成…',image_review:'请检查核心分镜图',video_generation_ready:'核心图已确认，等待生成动态镜头',video_generating:'正在生成动态镜头，分镜仅供回顾',video_stopping:'动态镜头正在安全停止',video_review:'动态镜头等待检查，分镜仅供回顾'}[project.value?.status]||'分镜确认与编辑'))
const dynamicShots=computed(()=>(project.value?.shots||[]).filter(shot=>shot.kind==='video'))
const completedVideos=computed(()=>dynamicShots.value.filter(shot=>shot.video_status==='completed').length)
const localVideo=computed(()=>project.value?.creation_parameters?.video_generation_backend==='comfyui')
const projectReferenceAudio=computed(()=>localVideo.value&&project.value?.creation_parameters?.comfyui_reference_audio===true&&!project.value?.creation_parameters?.comfyui_h3_prompt_agent)
const videoConfigured=computed(()=>localVideo.value?Boolean(project.value?.creation_parameters?.comfyui_profile_id):videoModel.value?.source==='dedicated'&&videoModel.value?.has_api_key)
const canProcessVideo=shot=>shot.kind==='video'&&shot.video_status!=='completed'&&!shot.video_terminal&&shot.video_status!=='failed'&&!(shot.video_status==='unknown'&&!shot.video_resume_available)
const pendingVideos=computed(()=>dynamicShots.value.filter(canProcessVideo))
const videoStatusLabel=shot=>shot.kind!=='video'?'静态画面':shot.video_queued&&videoRunning.value?'已加入队列':({pending:'等待生成',running:'处理中',completed:'已完成',failed:'生成失败',unknown:'状态待核实',stopped:'已停止'}[shot.video_status]||'等待生成')
const videoTask=shot=>({status:shot.video_queued&&videoRunning.value?'queued':shot.video_status,message:shot.video_queued&&videoRunning.value?'已加入 OCV 队列，等待处理':shot.video_progress_message||videoStatusLabel(shot),started_at:shot.video_started_at,finished_at:shot.video_finished_at,error:shot.video_error})
const videoBatchSummary=computed(()=>`已完成 ${completedVideos.value}/${dynamicShots.value.length} 镜 · 处理中 ${dynamicShots.value.filter(s=>s.video_status==='running').length} 镜 · OCV 排队 ${dynamicShots.value.filter(s=>s.video_queued).length} 镜`)
const requestedSeconds=shot=>Number(shot.video_request?.duration||shot.generation_duration||Math.max(4,Math.ceil(Number(shot.duration)||0)))
const requestedResolution=shot=>shot.video_request?.width&&shot.video_request?.height?`${shot.video_request.width} × ${shot.video_request.height}`:shot.video_request?.resolution||(shot.video_backend==='comfyui'&&shot.video_request?'旧记录未保存分辨率':shot.video_resolution||'尚未提交')
const videoUrl=shot=>base+'/'+project.value.id+'/videos/'+encodeURIComponent(shot.id)+'?v='+encodeURIComponent(shot.video_version||project.value.revision)
const historyVideoUrl=(shot,index)=>base+'/'+project.value.id+'/videos/'+encodeURIComponent(shot.id)+'/history/'+index+'?v='+encodeURIComponent(project.value.revision)
function defaultProjectView(record){return ['exporting','export_failed','completed'].includes(record?.status)?'export':videoStages.includes(record?.status)?'motions':'storyboard'}
const pendingAudioShots=computed(()=>project.value?.shots?.filter(s=>s.audio_timing_adjustment?.needs_review)||[])
watch(()=>[project.value?.id,project.value?.audio_version],async()=>{await nextTick();const index=project.value?.shots?.findIndex(s=>s.audio_timing_adjustment?.needs_review)??-1;if(index>=0){selected.value=index;view.value=defaultProjectView(project.value)}})
const narrationUrl=computed(()=>project.value?base+'/'+project.value.id+'/audio?v='+encodeURIComponent(project.value.audio_version||project.value.revision):'')
const previewAdaptRate=ref(1)
function nextAudioReview(){const shots=project.value?.shots||[];const indices=shots.map((s,i)=>s.audio_timing_adjustment?.needs_review?i:-1).filter(i=>i>=0);if(indices.length){selected.value=indices.find(i=>i>selected.value)??indices[0];view.value=videoStageReady.value?'motions':'storyboard'}}
async function confirmAudioReview(shot){await run(async()=>{project.value=await call('/'+project.value.id+'/shots/'+encodeURIComponent(shot.id)+'/confirm-audio-timing','POST',{revision:project.value.revision})});if(pendingAudioShots.value.length)nextAudioReview()}
const allVideosReady=computed(()=>Boolean(project.value?.shots.length)&&videoStageReady.value&&project.value.shots.every(shot=>shot.kind==='video'?shot.video_status==='completed':shot.image_status==='completed'))
const exportRunning=computed(()=>project.value?.status==='exporting')
const useVideoAudio=ref(true)
const exportLayoutSettings=ref({video_orientation:'landscape',subtitle_layouts:{}})
const exportLayoutVariant=ref('both')
const exportLayoutDirty=computed(()=>JSON.stringify(exportLayoutSettings.value.subtitle_layouts)!==JSON.stringify(project.value?.creation_parameters?.subtitle_layouts||{}))
watch(()=>[project.value?.id,project.value?.creation_parameters?.subtitle_layouts,project.value?.settings?.ratio],(value,old)=>{
 if(!old||value[0]!==old[0]||JSON.stringify(exportLayoutSettings.value.subtitle_layouts)===JSON.stringify(old[1]||{})){
  exportLayoutSettings.value={video_orientation:value[2]==='9:16'?'portrait':'landscape',subtitle_layouts:JSON.parse(JSON.stringify(value[1]||{}))}
 }
 exportLayoutSettings.value.video_orientation=value[2]==='9:16'?'portrait':'landscape'
},{deep:true})
const exportUrl=variant=>base+'/'+project.value.id+'/export/'+variant+'?v='+encodeURIComponent(project.value.revision)
async function exportVideo(automated=false){
 if(!allVideosReady.value)return
 if(pendingAudioShots.value.length&&!confirm(`还有 ${pendingAudioShots.value.length} 个镜头的配音变化尚未检查。确定仍然合成？取消可返回检查。`)){nextAudioReview();return}
 const audioNote=useVideoAudio.value?'动态片段中的原声音效会降低音量后与 TTS 配音混合。':'动态片段原声将被忽略，成片只使用 TTS 配音。'
 if(!automated&&!confirm('将按字幕时间轴裁切动态片段、补齐静态镜头，并生成字幕版与纯净版成片。\n\n'+audioNote+'\n\n是否开始？'))return
 await run(async()=>{project.value=await call('/'+project.value.id+'/export','POST',{revision:project.value.revision,use_video_audio:useVideoAudio.value,subtitle_layouts:exportLayoutSettings.value.subtitle_layouts});view.value='export'})
}
async function openExportFolder(){await run(async()=>{await call('/'+project.value.id+'/export/open','POST')})}
async function loadVideoModel(){
 videoModelError.value=''
 try{videoModel.value=await requestJSON('/api/video-model')}catch(e){videoModelError.value=e.message}
}
async function generateVideos(shots,retryFailed=false,automated=false,singleShot=false){
 if(!shots.length)return
 if(!singleShot)return submitVideoBatch(shots,{automated})
 const options=singleShot?optionsFor(shots[0]):null
 const useLocal=options?options.backend==='comfyui':localVideo.value
 const optionsPayload=options?{options}:{}
 if(useLocal){
  const queueNote=videoRunning.value?'当前已有镜头在生成，本次选择将追加到队列，前一镜完成后自动继续。':'将按镜头顺序提交到本地生成队列。'
  const profile=comfyProfiles.value.find(item=>item.id===(options?.profile_id||project.value.creation_parameters.comfyui_profile_id))
  let resourceNote=''
  if(!automated&&singleShot&&profile){
   try{const query=new URLSearchParams({profile_id:profile.id,ratio:project.value.settings.ratio||'16:9',resolution:options?.resolution||'',seconds:String(requestedSeconds(shots[0])),...(options?.resolution==='custom'?{width:options.width,height:options.height}:{})});const estimate=await requestJSON('/api/comfyui/resource-estimate?'+query);resourceNote=`\n\n本镜粗估：显存 ${estimate.vram_estimate_gb.join('–')} GB（当前空闲 ${estimate.vram_free_gb??'未知'} GB），内存 ${estimate.ram_estimate_gb.join('–')} GB（当前可用 ${estimate.ram_available_gb??'未知'} GB）。${estimate.risk==='high'?'⚠ 资源溢出风险较高，请优先降低分辨率或关闭占用程序。':estimate.risk==='caution'?'资源余量接近上限。':''}\n估算不保证运行时不会溢出。`}
   catch(e){resourceNote='\n\n暂时无法读取资源预估：'+e.message}
  }
  if(!automated&&!confirm(`将使用 ComfyUI 工作流「${profile?.name||'项目预设'}」生成 ${shots.length} 个镜头，分辨率：${options?.resolution==='custom'?options.width+' × '+options.height:options?.resolution||profile?.resolution_preset||(profile?profile.default_width+' × '+profile.default_height:'工作流默认')}。\n${queueNote}${resourceNote}\n是否开始？`))return
  await run(async()=>{project.value=await call('/'+project.value.id+'/videos/generate','POST',{revision:project.value.revision,shot_ids:shots.map(shot=>shot.id),retry_failed:retryFailed,...optionsPayload});view.value='motions';videoLogsOpen.value=true});return
 }
 if(!apiVideoReady.value){error.value='请先到“接口与服务”保存视频 API 配置，再开始生成。';return}
 if(!confirmLongApiShots(shots))return
 const lines=shots.map(shot=>{const index=project.value.shots.findIndex(item=>item.id===shot.id)+1;return '第 '+String(index).padStart(2,'0')+' 镜：'+requestedSeconds(shot)+' 秒 · '+(options?.resolution||shot.video_request?.resolution||videoModel.value.resolution)+(shot.video_resume_available&&shot.video_task_id&&!retryFailed?' · 查询原任务，不重新提交':shot.video_not_submitted&&!retryFailed?' · 继续首次付费生成（尚未提交）':' · 新的付费生成')})
 const message=(retryFailed?'云端已确认上次任务失败。本操作会提交新的付费请求，服务商可能再次扣费。':'推荐先试生成 1～2 镜，确认效果后再生成其余镜头。')+'\n\n'+lines.join('\n')+'\n\n静音生成，仅上传本镜已选参考图；费用以你的服务商实际计费为准。是否继续？'
 if(!automated&&!confirm(message))return
 await run(async()=>{project.value=await call('/'+project.value.id+'/videos/generate','POST',{revision:project.value.revision,shot_ids:shots.map(shot=>shot.id),retry_failed:retryFailed,...optionsPayload});view.value='motions';videoLogsOpen.value=true})
}
async function regenerateVideo(shot){
 if(!confirmLongApiShots([shot]))return
 const audioNote=optionsFor(shot).backend==='comfyui'&&!optionsFor(shot).h3_prompt_agent&&project.value.creation_parameters?.comfyui_reference_audio&&shot.reference_audio_enabled!==false
  ?'本镜将继续注入对应 TTS 参考音频'+(shot.reference_audio_lipsync!==false?'并要求人物对口型。':'，但不要求人物对口型。')
  :'本镜不会注入参考音频。'
 const options=optionsFor(shot),profile=comfyProfiles.value.find(item=>item.id===options.profile_id)
 const detail=options.backend==='comfyui'?`ComfyUI · ${profile?.name||'所选工作流'} · ${options.resolution==='custom'?options.width+' × '+options.height:options.resolution||profile?.resolution_preset||'工作流默认'}`:`视频 API · ${options.resolution||videoModel.value?.resolution}（可能再次计费）`
 if(!confirm('将按本镜生成设置重新生成：'+detail+'。\n\n'+audioNote+'\n原片段会保留在历史记录中，但新片段生成后将替代它参与合成。是否继续？'))return
 await run(async()=>{project.value=await call('/'+project.value.id+'/videos/generate','POST',{revision:project.value.revision,shot_ids:[shot.id],regenerate_completed:true,options});view.value='motions';videoLogsOpen.value=true})
}
async function regenerateAllVideos(){
 return submitVideoBatch(dynamicShots.value,{regenerate:true})
}
async function stopVideos(){if(!confirm('停止后不再提交后续镜头。已经提交的云端任务可能继续执行及计费，停止不会取消扣费；可稍后继续查询原任务。确定停止？'))return;await run(async()=>{project.value=await call('/'+project.value.id+'/videos/stop','POST');videoLogsOpen.value=true})}
async function reopenShot(shot){
 if(!confirm('返回完整分镜编辑界面？可连续调整所有镜头，未修改的图片和动态片段保留。编辑完成后统一确认返回；此操作不会生成或扣费。'))return
 await run(async()=>{stopMotionPreviewAudio();project.value=await call('/'+project.value.id+'/shots/'+encodeURIComponent(shot.id)+'/reopen','POST',{revision:project.value.revision});view.value='storyboard';await nextTick();document.querySelector('.video-detail')?.scrollIntoView({behavior:'smooth',block:'start'})})
}
const input=ref({name:'动态视频草案',srt:'',style:'生动清晰的简笔画风格',characters:'',world:'',ratio:'16:9',dynamic_text_mode:'visual_first',scene_references_enabled:true})
const boundary=ref(1),boundaryEditor=ref({open:false,mode:'split'}),sourceAudio=ref(null),boundaryAudio=ref(null)
const motionAudio=ref(null),previewNarration=ref(true)
let boundaryStopTimer=null
const redrawReferenceIds=ref([]),useCurrentReference=ref(false),useSceneReference=ref(true),redrawResolution=ref('')
const redrawIcan=computed(()=>Boolean(project.value?.creation_parameters?.use_cloud_image_pool)&&project.value?.creation_parameters?.method==='ican')
watch(redrawIcan,()=>{redrawResolution.value=''})
const imagePromptDrafts=ref({}),submittedImagePrompts=ref({})
const sceneAssets=computed(()=>project.value?.scene_assets||[])
const selectedShot=computed(()=>project.value?.shots?.[selected.value]||null)
const storyboardRows=computed(()=>[...(project.value?.scene_assets||[]).map((shot,index)=>({shot,i:-index-1})),...(project.value?.shots||[]).map((shot,i)=>({shot,i}))])
const selectedStoryboardAsset=computed(()=>storyboardRows.value.find(row=>row.i===selected.value)?.shot)
watch(()=>selectedStoryboardAsset.value?.id,()=>hydrateRedrawSelection(selectedStoryboardAsset.value))
const historyPreviewIndex=ref(null)
const selectedVideoHistory=computed(()=>((selectedShot.value?.video_history)||[])
 .map((entry,index)=>({entry,index}))
 .filter(({entry})=>entry?.video&&(entry.video_status==='completed'||entry.video_version)))
watch(()=>selectedShot.value?.id,()=>{historyPreviewIndex.value=null;hydrateRedrawSelection(selectedShot.value)})
function historyVersionLabel(row,position){
 const attempt=Number(row.entry.video_attempt)||0
 const time=Number(row.entry.invalidated_at)||0
 return `历史版本 ${position+1}${attempt?' · 第 '+attempt+' 次生成':''}${time?' · '+new Date(time*1000).toLocaleString():''}`
}
async function uploadReplacementVideo(shot,event){
 const file=event.target.files?.[0];event.target.value='';if(!file)return
 if(!confirm(`将使用本地文件“${file.name}”替换第 ${String(selected.value+1).padStart(2,'0')} 镜的当前动态片段。\n\n现有片段会进入历史记录，其他镜头、配音和分镜不变；最终成片需要重新合成。是否继续？`))return
 await run(async()=>{const data=new FormData();data.append('revision',String(project.value.revision));data.append('file',file);project.value=await requestJSON(base+'/'+project.value.id+'/videos/'+encodeURIComponent(shot.id)+'/upload',{method:'POST',body:data});historyPreviewIndex.value=null;view.value='motions'})
}
async function adoptHistoryVersion(shot,index){
 if(!confirm('采用这个历史片段作为当前版本？\n\n当前片段会自动进入历史记录，只需重新合成成片，不会重新生成其他镜头。'))return
 await run(async()=>{project.value=await call('/'+project.value.id+'/videos/'+encodeURIComponent(shot.id)+'/history/'+index+'/adopt','POST',{revision:project.value.revision});historyPreviewIndex.value=null})
}
async function deleteHistoryVersion(shot,index){
 if(!confirm('永久删除这个未采用的历史视频及其本地文件？此操作无法撤回。'))return
 await run(async()=>{project.value=await call('/'+project.value.id+'/videos/'+encodeURIComponent(shot.id)+'/history/'+index+'?revision='+encodeURIComponent(project.value.revision),'DELETE');historyPreviewIndex.value=null})
}
const scenesById=computed(()=>Object.fromEntries((project.value?.scenes||[]).map(scene=>[scene.slide_id,scene])))
const boundaryPreview=computed(()=>{
 const shot=selectedShot.value;if(!shot)return null
 let own=(shot.slide_ids||[]).map(id=>scenesById.value[id]).filter(Boolean)
 if(boundaryEditor.value.mode==='boundary')own=own.concat((project.value?.shots?.[selected.value+1]?.slide_ids||[]).map(id=>scenesById.value[id]).filter(Boolean))
 if(boundaryEditor.value.mode==='merge'){
  const following=project.value?.shots?.[selected.value+1]
  if(!following)return null
  const right=(following.slide_ids||[]).map(id=>scenesById.value[id]).filter(Boolean)
  return previewSides(own,right)
 }
 const cut=Math.max(1,Math.min(Number(boundary.value)||1,Math.max(1,own.length-1)))
 return previewSides(own.slice(0,cut),own.slice(cut))
})
const sceneReferencesEnabled=computed(()=>{
 const parameters=project.value?.creation_parameters||{},settings=project.value?.settings||{}
 const dynamic=Object.hasOwn(parameters,'dynamic_text_mode')||Object.hasOwn(settings,'dynamic_text_mode')
 return (dynamic||parameters.director_strategy==='enhanced_beta')&&parameters.scene_references_enabled!==false
})
const sceneReferencesBusy=computed(()=>['image_generating','image_stopping'].includes(project.value?.status)&&['planning','generating'].includes(project.value?.scene_references_status))
const sceneReferenceMessage=computed(()=>project.value?.scene_references_message||project.value?.scene_references_error||project.value?.scene_reference_plan?.message||'')
const sceneReferencesNeedGeneration=computed(()=>project.value?.scene_references_status!=='completed'||sceneAssets.value.some(asset=>asset.image_status!=='completed'))
const sceneReferencesBlockConfirm=computed(()=>sceneReferencesEnabled.value&&sceneReferencesNeedGeneration.value)
const allImageAssets=computed(()=>[...(project.value?.shots||[]),...sceneAssets.value])
const sceneAssetFor=shot=>sceneAssets.value.find(asset=>asset.id===shot.scene_reference_id)
const isSceneAsset=asset=>sceneAssets.value.some(scene=>scene.id===asset.id)
function sceneUsageLabel(shot){const asset=sceneAssetFor(shot);if(!asset)return '尚未匹配场景参考';if(!shot.scene_reference_used_version)return '当前核心图尚未使用此场景';return shot.scene_reference_used_version===asset.image_version?'当前核心图已使用此版场景':'场景已更新；重绘本镜头后才会使用新版'}
function sceneUsedByLabel(asset){return (asset.used_by||[]).map(id=>{const index=project.value.shots.findIndex(shot=>shot.id===id);return index>=0?String(index+1).padStart(2,'0'):null}).filter(Boolean).join('、')||'暂无关联镜头'}
function rememberImagePrompt(asset){imagePromptDrafts.value[asset.id]=asset.image_prompt}
function resetProjectImageState(){imagePromptDrafts.value={};submittedImagePrompts.value={};redrawReferenceIds.value=[];useCurrentReference.value=false;useSceneReference.value=true;redrawResolution.value=''}
function acceptImageUpdate(record,discardIds=[]){
 for(const id of discardIds){delete imagePromptDrafts.value[id];delete submittedImagePrompts.value[id]}
 for(const asset of [...(record.shots||[]),...(record.scene_assets||[])]){
  if(Object.hasOwn(submittedImagePrompts.value,asset.id)){
   if(asset.image_task?.status==='completed'){
    if(imagePromptDrafts.value[asset.id]===submittedImagePrompts.value[asset.id])delete imagePromptDrafts.value[asset.id]
    delete submittedImagePrompts.value[asset.id]
   }else if(asset.image_task?.status==='failed')delete submittedImagePrompts.value[asset.id]
  }
  if(Object.hasOwn(imagePromptDrafts.value,asset.id))asset.image_prompt=imagePromptDrafts.value[asset.id]
 }
 for(const shot of record.shots||[]){if(motionDrafts.value[shot.id])Object.assign(shot,motionDrafts.value[shot.id])}
 project.value=record
}
const promptWarnings=computed(()=>shotPromptNotes(selectedShot.value))
const warnedShotRows=computed(()=>promptWarningRows(project.value?.shots))
const warnedShots=computed(()=>warnedShotRows.value.length)
async function showWarning(index){selected.value=index;boundary.value=1;closeBoundaryEditor();await nextTick();const details=document.querySelector('.current-shot-warning');if(details){details.open=true;details.scrollIntoView({behavior:'smooth',block:'center'})}}
const planningCanResume=computed(()=>Boolean(project.value?.planning_resume_available)&&!dirty.value)
const planningButtonLabel=computed(()=>planningCanResume.value?'继续未完成规划':project.value?.shots?.length?'重新规划':'重试规划')
const replanMode=ref('text_assisted'),replanRegroup=ref(false)
watch(()=>[project.value?.id,project.value?.creation_parameters?.dynamic_text_mode,project.value?.settings?.dynamic_text_mode],()=>{
 replanMode.value=normalizeDynamicTextMode(project.value?.creation_parameters?.dynamic_text_mode??project.value?.settings?.dynamic_text_mode)
 replanRegroup.value=false
})
const sources=ref([]),sourceId=ref(''),importName=ref('')
const newVisual=ref({style:'生动清晰的简笔画风格',characters:'',world:'',ratio:'16:9',dynamic_text_mode:'visual_first',scene_references_enabled:true})
function chooseSource(){const source=sources.value.find(item=>item.id===sourceId.value);importName.value=source?source.name.slice(0,90)+' · 动态版':''}
async function importProject(){await run(async()=>{if(dirty.value)await save();project.value=await call('/from-project','POST',{job_id:sourceId.value,name:importName.value,...newVisual.value});selected.value=0;view.value='storyboard';dirty.value=false;await list()})}
let timer
const draftKey=id=>'ocv-video-draft:'+id
watch(()=>project.value?.id,(id,previous)=>{if(id!==previous){resetProjectImageState();motionDrafts.value={};useVideoAudio.value=project.value?.export_settings?.use_video_audio!==false}},{flush:'sync'})
watch(project,value=>{if(value&&dirty.value){try{sessionStorage.setItem(draftKey(value.id),JSON.stringify(value))}catch{error.value='浏览器无法暂存修改，请使用保存镜头修改按钮。'}}},{deep:true})
const base='/api/video-studio'
async function call(path,method='GET',body){return requestJSON(base+path,{method,...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})})}
async function run(fn){busy.value=true;error.value='';try{await fn()}catch(e){error.value=e.message}finally{busy.value=false;if(project.value?.creation_parameters?.dynamic_auto_advance)setTimeout(advanceAutoPilot,0)}}
async function list(){items.value=(await call('')).items}
async function open(id){await run(async()=>{if(dirty.value)await save();const fresh=await call('/'+id);let cached;try{cached=JSON.parse(sessionStorage.getItem(draftKey(id))||'null')}catch{}dirty.value=false;resetProjectImageState();motionDrafts.value={};project.value=fresh;selected.value=0;view.value=defaultProjectView(fresh);if(cached&&cached.revision===fresh.revision){if(['draft','storyboard_review'].includes(fresh.status)){project.value=cached;dirty.value=true;error.value='已恢复本页暂存的镜头修改，请确认后保存。'}else if(fresh.status==='image_review'){for(const shot of fresh.shots){const draft=cached.shots?.find(row=>row.id===shot.id);if(draft&&(draft.action!==shot.action||draft.video_prompt!==shot.video_prompt)){shot.action=draft.action||'';shot.video_prompt=draft.video_prompt||'';rememberMotionDraft(shot)}}if(dirty.value)error.value='已恢复暂存的动态表达与视频提示词，请确认后保存。'}}})}
async function create(){await run(async()=>{if(dirty.value)await save();project.value=await call('','POST',input.value);selected.value=0;view.value='storyboard';dirty.value=false;await list()})}
async function plan(automated=false){
 const message=planningCanResume.value?'将复用已保存的规划，仅继续未完成的步骤；仍需调用语言 API，是否继续？':(dirty.value?'将先保存当前镜头修改，再重新规划。':'')+(project.value.manual_groups?.length?'将保留已确认的手动字幕分组，重新设计各镜头，会消耗 LLM 额度。是否继续？':'使用当前语言 API 规划分镜，会消耗 LLM 额度。已有镜头将在成功后被替换，是否继续？')
 if(!automated&&!confirm(message))return
 await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/plan','POST');dirty.value=false})
}
async function save(){if(project.value.status==='image_review'){await saveMotionDrafts();return}project.value=await call('/'+project.value.id,'PUT',{revision:project.value.revision,shots:project.value.shots});dirty.value=false;sessionStorage.removeItem(draftKey(project.value.id))}
async function replanWithCurrentModel(){
 const mode=replanMode.value,regroup=replanRegroup.value
 const grouping=regroup?'按全文语义重新划分全部镜头，镜头编号、数量和动静类型可能变化；':'保留当前分镜边界与动静类型；'
 if(!confirm(`使用当前语言服务，以「${dynamicTextModeLabel(mode)}」重新规划？\n保留配音、字幕文字与时间轴。${grouping}重新设计画面与提示词，消耗语言模型额度。\n成功后需确认新方案，再生成图片和视频，不自动调用素材生成。失败则保留原方案。`))return
 await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/plan?fresh=true&revision='+project.value.revision+'&regroup='+regroup+'&expression_mode='+encodeURIComponent(mode),'POST');dirty.value=false;view.value='storyboard'})
}
function previewSides(left,right){
 const side=rows=>({rows,text:rows.map(row=>row.text).join('\n'),start:Number(rows[0]?.start||0),end:Number(rows.at(-1)?.end||0)})
 const a=side(left),b=side(right)
 return {left:a,right:b,boundary:a.end,combinedDuration:Number((b.end-a.start).toFixed(3))}
}
function openBoundaryEditor(mode){stopBoundaryPreview();mode=mode||(selected.value<project.value.shots.length-1?'boundary':'split');boundaryEditor.value={open:true,mode};if(mode==='boundary')boundary.value=selectedShot.value.slide_ids.length;else if(mode==='split')boundary.value=Math.min(Math.max(1,Number(boundary.value)||1),Math.max(1,(selectedShot.value?.slide_ids?.length||1)-1))}
async function repairDesigns(){if(!confirm('保留当前字幕分组，仅更新受影响镜头的设计，会调用语言 API。继续？'))return;closeBoundaryEditor();await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/plan?repair_only=true','POST')})}
async function confirmDesign(){if(!confirm('确认本镜的核心图、动态表达与视频提示词已检查完毕并沿用？此操作不生成、不扣费。'))return;await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/design/confirm','POST',{revision:project.value.revision,shot_id:selectedShot.value.id})})}
async function undoStructure(){if(!confirm('撤回最近一次结构调整，恢复调整前的镜头与提示词；当前未保存修改会被放弃。继续？'))return;await run(async()=>{project.value=await call('/'+project.value.id+'/structure/undo','POST',{revision:project.value.revision});dirty.value=false;sessionStorage.removeItem(draftKey(project.value.id));selected.value=Math.min(selected.value,project.value.shots.length-1);closeBoundaryEditor()})}
function closeBoundaryEditor(){stopBoundaryPreview();boundaryEditor.value.open=false}
function stopBoundaryPreview(){
 if(boundaryStopTimer){clearTimeout(boundaryStopTimer);boundaryStopTimer=null}
 const audio=boundaryAudio.value;if(audio){audio.pause();audio.ontimeupdate=null}
}
function stopMotionPreviewAudio(){const audio=motionAudio.value;if(audio)audio.pause()}
async function syncMotionPreview(event,phase){
 const video=event.currentTarget,audio=motionAudio.value,shot=selectedShot.value
 if(!shot)return
 const targetDuration=Number(shot.duration),sourceDuration=Number(video.duration)
 const rate=Number.isFinite(sourceDuration)&&targetDuration>sourceDuration+.03?sourceDuration/targetDuration:1
 previewAdaptRate.value=rate
 if(Math.abs(video.playbackRate-rate)>.001)video.playbackRate=rate
 const offset=Math.max(0,Number(video.currentTime)||0)/rate
 if(offset>=targetDuration-.025){if(audio)audio.pause();if(!video.paused)video.pause();return}
 if(!audio||!previewNarration.value){if(audio)audio.pause();return}
 if(phase==='pause'||phase==='ended'){audio.pause();return}
 const target=Number(shot.start)+offset
 if(phase==='seeking'||Math.abs(audio.currentTime-target)>.22)audio.currentTime=target
 audio.playbackRate=1
 if(phase==='play'&&audio.paused){try{await audio.play()}catch{error.value='镜头可以播放，但浏览器阻止了配音同步。请再次点击播放，或检查浏览器声音权限。'}}
}
async function playBoundaryRange(start,end){
 const audio=boundaryAudio.value;if(!audio||!project.value?.audio){error.value='当前任务没有可试听的配音。';return}
 if(!Number.isFinite(Number(start))||!Number.isFinite(Number(end))||Number(end)<=Number(start)){error.value='当前字幕试听范围无效，请重新选择切口。';return}
 error.value='';stopBoundaryPreview();sourceAudio.value?.pause();stopMotionPreviewAudio();audio.currentTime=Math.max(0,Number(start));const finish=()=>{if(audio.currentTime>=Number(end)-.03)stopBoundaryPreview()};audio.ontimeupdate=finish
 try{await audio.play();if(audio.ontimeupdate===finish)boundaryStopTimer=setTimeout(stopBoundaryPreview,Math.max(500,(Number(end)-Number(start)+.3)*1000))}catch{stopBoundaryPreview();error.value='配音暂时无法播放，请检查音频文件或浏览器播放权限。'}
}
function playBoundaryPart(part){const preview=boundaryPreview.value;if(!preview)return;const side=preview[part];playBoundaryRange(side.start,side.end)}
function playBoundaryEdge(part){const preview=boundaryPreview.value;if(!preview)return;const point=preview.boundary;if(part==='left')playBoundaryRange(Math.max(preview.left.start,point-5),point);else if(part==='right')playBoundaryRange(point,Math.min(preview.right.end,point+5));else playBoundaryRange(Math.max(preview.left.start,point-3),Math.min(preview.right.end,point+3))}
async function applyBoundaryEdit(){const mode=boundaryEditor.value.mode;if(mode==='merge'&&!confirm('合并后，后一镜头的字幕和时长会并入当前镜头。是否继续？'))return;if(await structure(mode))closeBoundaryEditor()}
async function structure(action){if(action==='delete'&&!confirm('删除此画面并将字幕并入上一镜（第一镜并入下一镜）。配音保留，受影响视频需重生成；超过15秒将暂转静态。继续？'))return false;structureFeedback.value='正在提交分镜调整…';const oldCount=project.value.shots.length;let completed=false;await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/structure','POST',{revision:project.value.revision,action,index:selected.value,boundary:Number(boundary.value)});selected.value=Math.min(action==='delete'?Math.max(0,selected.value-1):selected.value,project.value.shots.length-1);view.value='storyboard';completed=true});structureFeedback.value=completed?`分镜调整已保存：${oldCount} 镜 → ${project.value.shots.length} 镜。配音与字幕保留。`:(error.value||'操作未完成');return completed}
async function review(){await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/review','POST',{revision:project.value.revision});project.value=await call('/'+project.value.id+'/images/generate','POST',{revision:project.value.revision})})}
async function generateImages(){await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/images/generate','POST',{revision:project.value.revision})})}
async function stopImages(){await run(async()=>{project.value=await call('/'+project.value.id+'/images/stop','POST')})}
async function confirmImages(regenerateShotId=''){
 const unchecked=project.value.shots.map((shot,index)=>shot.design_needs_review?String(index+1).padStart(2,'0'):null).filter(Boolean)
 if(unchecked.length&&!confirm('第 '+unchecked.join('、')+' 镜的字幕范围曾调整。\n\n如果你已检查现有核心图、动态表达与视频提示词，确认后将统一沿用这些设计，返回动态镜头页。\n不会重新规划、生成或扣费。若尚未检查，请取消并继续编辑。\n\n确认沿用并返回？'))return
 await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/images/confirm','POST',{revision:project.value.revision,confirm_adjusted_designs:Boolean(unchecked.length),...(regenerateShotId?{regenerate_shot_id:regenerateShotId}:{})});view.value='motions';videoLogsOpen.value=true})
}
async function finishShotReedit(shot){
 let reroll=''
 if(shot.kind==='video'&&shot.video_status==='completed'){
  const message='检测到本镜的核心分镜图、动态表达和视频生成提示词均未发生会使原片失效的变化。\n\n是否仍将当前动态片段标记为待重新生成？\n\n确认后只会返回动态镜头页；仍需再次点击生成并确认费用，当前操作不会调用视频 API。'
  if(!confirm(message))return
  reroll=shot.id
 }
 await confirmImages(reroll)
}
const imageUrl=shot=>base+'/'+project.value.id+'/images/'+encodeURIComponent(shot.id)+'?v='+project.value.revision
const redrawReferences=computed(()=>project.value?.redraw_references||[])
const projectRedrawReferences=computed(()=>(project.value?.references||[]).map((asset,index)=>({
 ...asset,origin:'project',displayName:asset.description||asset.label||asset.name||`任务参考图 ${index+1}`
})))
const uploadedRedrawReferences=computed(()=>redrawReferences.value.map((asset,index)=>({
 ...asset,origin:'uploaded',displayName:asset.name||`新增参考图 ${index+1}`
})))
const sceneRedrawReferences=computed(()=>(project.value?.scene_assets||[]).filter(asset=>asset.image_status==='completed').map(asset=>({...asset,origin:'scene',displayName:asset.name||'场景图'})))
const redrawReferenceGallery=computed(()=>[...projectRedrawReferences.value,...sceneRedrawReferences.value,...uploadedRedrawReferences.value])
const redrawReferenceUrl=asset=>base+'/'+project.value.id+'/redraw-references/'+encodeURIComponent(asset.id)+'?v='+project.value.revision
const imageEditRunning=computed(()=>sceneReferencesBusy.value||allImageAssets.value.some(asset=>asset.image_task?.status==='running'))
const imageAssetRunning=asset=>asset?.image_task?.status==='running'
function sceneReferenceIds(asset){return asset.redraw_selection?.reference_ids??asset.reference_ids??[]}
function toggleSceneMaterial(asset,id){
 const ids=sceneReferenceIds(asset)
 if(!ids.includes(id)&&ids.length>=3){error.value='场景图最多使用 3 张参考素材。';return}
 asset.redraw_selection={...(asset.redraw_selection||{}),reference_ids:ids.includes(id)?ids.filter(value=>value!==id):[...ids,id]}
}
function hydrateRedrawSelection(shot){
 if(!shot)return
 const saved=shot.redraw_selection
 const known=new Set(redrawReferenceGallery.value.map(asset=>String(asset.id)))
 const defaults=Array.isArray(saved?.reference_ids)?saved.reference_ids:(shot.reference_ids||shot.reference_image_ids||[])
 redrawReferenceIds.value=defaults.map(String).filter(id=>known.has(id)).slice(0,3)
 useCurrentReference.value=Boolean(saved?.use_current_image)
 useSceneReference.value=saved?.use_scene_reference!==false
 if(useCurrentReference.value&&redrawReferenceIds.value.length>2)redrawReferenceIds.value=redrawReferenceIds.value.slice(0,2)
}
function redrawReferenceNumber(id){return redrawReferenceIds.value.indexOf(id)+(useCurrentReference.value?2:1)}
function toggleRedrawReference(id){const values=redrawReferenceIds.value;if(values.includes(id))redrawReferenceIds.value=values.filter(value=>value!==id);else if(values.length<3-(useCurrentReference.value?1:0))redrawReferenceIds.value=[...values,id];else error.value='每次重绘最多使用 3 张参考图（包含当前画面）。'}
function toggleCurrentReference(){if(!useCurrentReference.value&&redrawReferenceIds.value.length>=3)redrawReferenceIds.value=redrawReferenceIds.value.slice(0,2);useCurrentReference.value=!useCurrentReference.value}
async function uploadRedrawReferences(event){const files=[...(event.target.files||[])];event.target.value='';for(const file of files.slice(0,Math.max(0,3-(useCurrentReference.value?1:0)-redrawReferenceIds.value.length))){await run(async()=>{const data=new FormData();data.append('file',file);acceptImageUpdate(await requestJSON(base+'/'+project.value.id+'/redraw-references',{method:'POST',body:data}));const latest=project.value.redraw_references?.at(-1);if(latest)redrawReferenceIds.value=[...redrawReferenceIds.value,latest.id]})}}
async function deleteRedrawReference(asset){
 if(asset.origin!=='uploaded'||!confirm(`删除新增参考图“${asset.displayName}”？图片会从本项目素材库移除。`))return
 await run(async()=>{acceptImageUpdate(await requestJSON(base+'/'+project.value.id+'/redraw-references/'+encodeURIComponent(asset.id)+'?revision='+encodeURIComponent(project.value.revision),{method:'DELETE'}));redrawReferenceIds.value=redrawReferenceIds.value.filter(id=>id!==asset.id)})
}
async function clearUploadedRedrawReferences(){
 if(!uploadedRedrawReferences.value.length||!confirm(`清空本项目后加的 ${uploadedRedrawReferences.value.length} 张参考图？创建任务时上传的原始素材不会删除。`))return
 await run(async()=>{acceptImageUpdate(await requestJSON(base+'/'+project.value.id+'/redraw-references?revision='+encodeURIComponent(project.value.revision),{method:'DELETE'}));const originals=new Set(projectRedrawReferences.value.map(asset=>asset.id));redrawReferenceIds.value=redrawReferenceIds.value.filter(id=>originals.has(id))})
}
async function redrawShot(shot){if(!shot.image_prompt?.trim()){error.value='请先填写图片提示词。';return}await run(async()=>{rememberImagePrompt(shot);const sceneAsset=isSceneAsset(shot),submittedPrompt=shot.image_prompt;const updated=await call('/'+project.value.id+'/images/'+encodeURIComponent(shot.id)+'/redraw','POST',{revision:project.value.revision,prompt:submittedPrompt,reference_ids:redrawReferenceIds.value,use_current_image:useCurrentReference.value,use_scene_reference:sceneAsset?false:useSceneReference.value,image_resolution:redrawIcan.value?null:redrawResolution.value||null,size:redrawIcan.value&&redrawResolution.value?imageSize(project.value.settings.ratio||'16:9',redrawResolution.value):null});submittedImagePrompts.value[shot.id]=submittedPrompt;acceptImageUpdate(updated);dirty.value=false;sessionStorage.removeItem(draftKey(project.value.id))})}
async function replaceShotImage(event,shot){const file=event.target.files?.[0];event.target.value='';if(!file)return;await run(async()=>{const data=new FormData();data.append('revision',String(project.value.revision));data.append('prompt',shot.image_prompt||'');data.append('file',file);acceptImageUpdate(await requestJSON(base+'/'+project.value.id+'/images/'+encodeURIComponent(shot.id)+'/upload',{method:'POST',body:data}),[shot.id]);dirty.value=false;sessionStorage.removeItem(draftKey(project.value.id))})}
async function undoShot(shot){await run(async()=>{acceptImageUpdate(await call('/'+project.value.id+'/images/'+encodeURIComponent(shot.id)+'/undo','POST',{revision:project.value.revision}),[shot.id]);dirty.value=false})}
async function resetShotPrompt(shot){await run(async()=>{acceptImageUpdate(await call('/'+project.value.id+'/images/'+encodeURIComponent(shot.id)+'/reset-prompt','POST',{revision:project.value.revision}),[shot.id]);dirty.value=false})}
async function refreshShotPrompts(shot,basis){
 const label=basis==='full'?'根据本镜字幕和项目设定，重新设计动态表达、核心图提示词和视频提示词，覆盖本镜已有文字编辑；保留字幕与时长，不自动重绘图片或生成视频':basis==='action'?'根据当前动态表达，只更新本镜的核心图提示词和视频提示词':'根据当前核心图，只更新本镜的视频提示词'
 if(!confirm(label+'，会调用语言 API，其他镜头不变。是否继续？'))return
 await run(async()=>{if(dirty.value)await save();const updated=await call('/'+project.value.id+'/shots/'+encodeURIComponent(shot.id)+'/refresh-prompts','POST',{revision:project.value.revision,basis,action:shot.action||'',image_prompt:shot.image_prompt||''});delete imagePromptDrafts.value[shot.id];delete submittedImagePrompts.value[shot.id];project.value=updated;dirty.value=false;sessionStorage.removeItem(draftKey(project.value.id))})
}
async function generateSceneReferences(){await run(async()=>{acceptImageUpdate(await call('/'+project.value.id+'/scene-references/generate','POST',{revision:project.value.revision}))})}
async function stopPlanning(){await run(async()=>{project.value=await call('/'+project.value.id+'/stop','POST')})}
async function loadSrt(event){const file=event.target.files[0];if(file)input.value.srt=await file.text()}
function exportPlan(){const blob=new Blob([JSON.stringify(project.value,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=project.value.settings.name+'-分镜草案.json';a.click();URL.revokeObjectURL(url)}
const reviewRequest=computed(()=>{
 const record=project.value||{}, settings=record.settings||{}, saved=record.creation_parameters||{}
 return {...saved,dynamic_video:true,step_mode:true,
  dynamic_text_mode:normalizeDynamicTextMode(saved.dynamic_text_mode??settings.dynamic_text_mode),
  scene_references_enabled:sceneReferencesEnabled.value,
  project_name:settings.name||saved.project_name||'动态视频任务',
  visual_style_prompt:settings.style||saved.visual_style_prompt||'',
  global_character_prompt:settings.characters||saved.global_character_prompt||'',
  story_environment_prompt:settings.world||saved.story_environment_prompt||'',
  video_orientation:settings.ratio==='9:16'?'portrait':(saved.video_orientation||'landscape')}
})
const originalScript=computed(()=>String(reviewRequest.value.script||'').trim())
const finalTranscript=computed(()=>project.value?.scenes?.map(row=>row.text).filter(Boolean).join('\n')||'')
function openClassicEditor(){
 const form=JSON.parse(JSON.stringify(reviewRequest.value))
 emit('edit-config',{id:'rerun-'+crypto.randomUUID(),name:form.project_name,kind:'dynamic',form,
  subtitle:{project_name:form.project_name,source_audio_id:form.source_audio_id||'',reference_text:form.script||'',use_correction:true},
  engine:form.tts_engine==='indextts2'?'indextts25':form.tts_engine||'indextts25',rerun:true,
  source_project_id:project.value.id,source_project_revision:project.value.revision,
  rerun_base:JSON.parse(JSON.stringify(form)),rerun_stages:JSON.parse(JSON.stringify(stageSteps.value)),source_view:view.value})
}
function openStage(stage){if(stage.key==='script'){openClassicEditor();return}view.value=stage.key}
const stageSteps=computed(()=>[
 {key:'script',label:'文案',state:'done'},
 {key:'audio',label:'配音与字幕',state:'done'},
 {key:'storyboard',label:'动态分镜',state:videoStageReady.value||project.value?.status==='completed'?'done':'current'},
 {key:'motions',label:'动态镜头',state:project.value?.status==='completed'||(project.value?.status==='video_review'&&completedVideos.value===dynamicShots.value.length)?'done':videoStageReady.value?'current':'upcoming'},
 {key:'export',label:'导出',state:project.value?.status==='completed'?'done':(allVideosReady.value||['exporting','export_failed'].includes(project.value?.status))?'current':'upcoming'},
 {key:'parameters',label:'参数回顾',state:'review'},
])
const storyboardLocked=computed(()=>['generation_ready','image_generating','image_stopping','image_review',...videoStages,'completed'].includes(project.value?.status))
const canEditStructure=computed(()=>project.value&&!busy.value&&!imageEditRunning.value&&!['planning','stopping','image_generating','image_stopping','video_generating','video_stopping','exporting'].includes(project.value.status))
const structureFeedback=ref('')
function duplicateFromSnapshot(){
 const form=JSON.parse(JSON.stringify(reviewRequest.value))
 form.project_name=String(form.project_name||'动态视频任务').slice(0,65)+' · 副本'
 form.dynamic_video=true;form.step_mode=true
 const subtitle={project_name:form.project_name,source_audio_id:form.source_audio_id||'',reference_text:form.script||'',use_correction:true}
 emit('duplicate-config',{id:'draft-'+crypto.randomUUID(),form,subtitle,engine:form.tts_engine==='indextts2'?'indextts25':form.tts_engine||'indextts25'})
}
function resetFromSnapshot(){
 if(!confirm('重置后会保留当前任务参数，创建一份同名、尚未运行的新草稿。原项目及其成片不会删除。是否继续？'))return
 const form=JSON.parse(JSON.stringify(reviewRequest.value))
 form.project_name=String(form.project_name||'动态视频任务').slice(0,65)
 form.dynamic_video=true;form.step_mode=true
 const subtitle={project_name:form.project_name,source_audio_id:form.source_audio_id||'',reference_text:form.script||'',use_correction:true}
 emit('duplicate-config',{id:'draft-'+crypto.randomUUID(),form,subtitle,engine:form.tts_engine==='indextts2'?'indextts25':form.tts_engine||'indextts25',reset:true})
}
const autoAdvanceBusy=ref(false)
let autoAdvanceKey=''
const autoAdvanceEnabled=computed(()=>project.value?.creation_parameters?.dynamic_auto_advance===true)
async function advanceAutoPilot(){
 const record=project.value
 if(!record||!autoAdvanceEnabled.value||busy.value||autoAdvanceBusy.value||record.error)return
 const status=String(record.status||'')
 if(['planning','stopping','image_generating','image_stopping','video_generating','video_stopping','exporting','completed'].includes(status))return
 if(status==='image_review'){
  if(record.shots.some(shot=>shot.image_status==='failed')){error.value='一键制作已暂停：存在生成失败的核心分镜图，请重试或替换后继续。';view.value='storyboard';return}
  if(record.shots.some(shot=>shot.image_status!=='completed')||sceneReferencesBlockConfirm.value||imageEditRunning.value)return
 }
 if(status==='video_generation_ready'&&dynamicShots.value.some(shot=>!shotConfigured(shot))){error.value='一键制作已暂停：请检查各镜头的工作流或视频 API 配置。';view.value='motions';return}
 if(status==='video_review'&&dynamicShots.value.some(shot=>shot.video_status!=='completed')){error.value='一键制作已暂停：部分动态镜头尚未完成，请在动态镜头页检查后继续。';view.value='motions';return}
 const key=`${record.id}:${record.revision}:${status}`
 if(autoAdvanceKey===key)return
 autoAdvanceKey=key;autoAdvanceBusy.value=true
 try{
  if(status==='draft'&&!record.shots.length){view.value='storyboard';await plan(true)}
  else if(status==='storyboard_review'&&record.shots.length){view.value='storyboard';await review()}
  else if(status==='generation_ready'){view.value='storyboard';await generateImages()}
  else if(status==='image_review'){view.value='storyboard';await confirmImages()}
  else if(status==='video_generation_ready'){if(allVideosReady.value){view.value='export';await exportVideo(true)}else{view.value='motions';await generateVideos(dynamicShots.value,false,true)}}
  else if(status==='video_review'&&allVideosReady.value){view.value='export';await exportVideo(true)}
 }finally{autoAdvanceBusy.value=false}
}
watch(()=>[project.value?.status,project.value?.revision,videoConfigured.value,comfyProfiles.value.length],()=>setTimeout(advanceAutoPilot,0))
onMounted(()=>{
 run(async()=>{
  const id=sessionStorage.getItem('ocv-video-open')
  if(id){
   project.value=await call('/'+id)
   const requestedView=sessionStorage.getItem('ocv-video-view')
   view.value=requestedView&&stageSteps.value.some(stage=>stage.key===requestedView)?requestedView:defaultProjectView(project.value)
   sessionStorage.removeItem('ocv-video-view')
   sessionStorage.removeItem('ocv-video-open')
   const autoPlan=sessionStorage.getItem('ocv-video-autoplan')===id
   if(autoPlan)sessionStorage.removeItem('ocv-video-autoplan')
   // Consume the navigation intent before submission. Reopening or refreshing
   // a project must never trigger another paid planning request.
   if(autoPlan&&project.value.status==='draft'&&!project.value.shots.length){
    project.value=await call('/'+id+'/plan','POST')
   }
  }
  await list()
  if(!project.value)sources.value=(await call('/sources')).items
 })
 loadVideoModel().then(()=>advanceAutoPilot())
 loadComfyProfiles()
 timer=setInterval(async()=>{if((['planning','stopping','image_generating','image_stopping','video_generating','video_stopping','exporting'].includes(project.value?.status)||imageEditRunning.value)&&!busy.value){const id=project.value.id;try{const latest=await call('/'+id);if(project.value?.id===id)acceptImageUpdate(latest)}catch(e){error.value=e.message}}},2500)
})
watch([selected,view],()=>stopMotionPreviewAudio())
watch(previewNarration,enabled=>{if(!enabled)stopMotionPreviewAudio()})
onUnmounted(()=>{clearInterval(timer);stopBoundaryPreview();stopMotionPreviewAudio()})
</script>
<template>
<section class="content video-workspace">
 <div v-if="project" class="workspace-project-heading"><div><p class="eyebrow">动态视频工作台</p><h1>{{project.settings.name}}</h1></div><div class="workspace-project-actions"><span v-if="dirty" class="workspace-draft" role="status">有未保存修改</span><button class="ghost-btn" @click="exportPlan">导出分镜草案</button><button class="ghost-btn" @click="emit('new-project')">＋ 新建项目</button></div></div>
 <nav v-if="project" class="video-stage-nav" aria-label="动态视频任务阶段">
  <button v-for="(stage,index) in stageSteps" :key="stage.key" type="button" :aria-current="view===stage.key?'step':undefined" :class="[stage.state,{active:view===stage.key}]" @click="openStage(stage)">
   <span v-if="stage.state==='done'">✓</span><span v-else-if="stage.state==='upcoming'">{{index+1}}</span><span v-else>•</span>
   <b>{{stage.label}}</b><small>{{stage.state==='done'?'已完成':stage.state==='current'?'当前阶段':stage.state==='upcoming'?'尚未开始':'只读查看'}}</small>
  </button>
 </nav>
 <div class="page-heading workspace-stage-heading"><div><h2>{{project?workspaceTitles[view]||'动态视频':'动态视频'}}</h2><p class="muted">{{project?workspaceHints[view]:'从文案开始制作动态视频，或继续已有任务。'}}</p></div><span v-if="project?.shots.length" class="readonly-badge">{{project.shots.length}} 镜 · {{project.settings.ratio||'16:9'}}</span></div>
 <div v-if="project&&autoAdvanceEnabled&&project.status!=='completed'" class="auto-pilot-status" role="status"><span class="auto-pilot-dot"></span><div><strong>{{autoAdvanceBusy?'一键制作正在进入下一阶段':'一键制作已开启'}}</strong><p>成功的确认步骤会自动继续；失败或需要人工处理时会停留在对应阶段。</p></div></div>
 <p v-if="error" class="studio-notice error">{{error}}</p>
 <section v-if="project&&pendingAudioShots.length" class="single-shot-refresh" role="status">
  <div><strong>配音已更新 · {{pendingAudioShots.length}} 镜待检查</strong><p>时间轴已同步，原画面保留。请试看裁切或降速后的效果；不满意时只重新生成对应镜头。</p></div>
  <button @click="nextAudioReview">下一个待检查</button>
 </section>
 <section v-if="selectedShot?.audio_timing_adjustment?.needs_review&&['storyboard','motions'].includes(view)" class="single-shot-refresh">
  <div><strong>第 {{String(selected+1).padStart(2,'0')}} 镜配音发生变化，请检查</strong><p>{{selectedShot.audio_timing_adjustment.previous_duration}} 秒 → {{selectedShot.duration}} 秒。检查动作与叙事是否完整，再决定沿用或重新生成。</p><p v-if="selectedShot.audio_timing_adjustment.text_changed">字幕文字也已修改，请同时核对画面含义。</p></div>
  <button :disabled="!videoStageReady" @click="view='motions'">试看本镜</button>
  <button :disabled="busy" @click="confirmAudioReview(selectedShot)">确认沿用</button>
 </section>
 <LanguageModelPresets v-if="project&&view==='storyboard'" :disabled="busy||!canEditStructure" :cloud-pool="Boolean(project.creation_parameters?.use_cloud_image_pool)">
  <template #actions="{unavailable,pendingSwitch}">
   <div class="replan-description"><strong>重新规划画面</strong><small class="muted">保留配音与字幕时间轴。确认新方案后再生成素材，不自动重做。</small><small v-if="pendingSwitch" class="muted">请先点击“使用此模型”，再重新规划。</small>
    <details class="replan-options"><summary>规划选项 · {{ dynamicTextModeLabel(replanMode) }} · {{ replanRegroup?'重新划分镜头':'保留镜头边界' }}</summary>
     <fieldset :disabled="busy||!canEditStructure||unavailable"><DynamicTextModeSelector v-model="replanMode" />
      <label class="replan-regroup"><input type="checkbox" v-model="replanRegroup">同时重新划分镜头</label>
      <small class="muted">仅重规划时生效。勾选后不保留手动分组，按完整语义重新选择边界和动静类型；配音、字幕不变。未勾选时，如需把动态循环改为静态，可单独修改镜头类型。</small>
     </fieldset>
    </details>
   </div>
   <button type="button" :disabled="busy||!canEditStructure||unavailable" @click="replanWithCurrentModel">重新规划</button>
  </template>
 </LanguageModelPresets>
 <OperationStatus v-if="project&&(busy||(view!=='motions'&&videoRunning)||['planning','stopping','image_generating','image_stopping','exporting'].includes(project.status))" title="项目运行状态" :pending="busy" :task="{status:'running',message:project.status==='exporting'?'正在合成成片':storyboardStageLabel}" :summary="videoRunning?videoBatchSummary:''"/>
 <template v-if="!project&&!busy">
 <div class="video-start">
  <section class="video-card"><h2>新建视频任务</h2><p class="muted">可借用已有作品的配音、字幕和时间戳。画面从头设计，不导入旧图、参考图、画风、人物或旧分镜。</p><label>配音与字幕来源<select v-model="sourceId" @change="chooseSource"><option value="">请选择已完成的项目</option><option v-for="source in sources" :key="source.id" :value="source.id">{{source.name}}</option></select></label><p v-if="!sources.length" class="muted">暂无可导入项目，需要项目已完成，且配音与最终字幕文件完整。</p><label>新项目名称<input v-model="importName" maxlength="100" placeholder="自动填写，可修改"></label><label>新视频比例<select v-model="newVisual.ratio"><option value="16:9">横屏 16:9</option><option value="9:16">竖屏 9:16</option></select></label>
   <DynamicTextModeSelector v-model="newVisual.dynamic_text_mode" />
   <label class="dynamic-scene-reference-toggle"><input type="checkbox" v-model="newVisual.scene_references_enabled">启用场景参考</label><p class="muted">所有表达方式均可使用；为重复场景额外生成参考图并计费，关闭后不生成。</p>
   <details open><summary>新任务画面设定（不继承来源项目）</summary><label>统一画风<textarea v-model="newVisual.style" rows="2"/></label><label>人物设定<textarea v-model="newVisual.characters" rows="2" placeholder="例如：主讲人是红色身体、戴红围巾的火柴人"/></label><label>世界与场景<textarea v-model="newVisual.world" rows="2"/></label></details><button class="primary-btn" :disabled="busy||!sourceId" @click="importProject">新建任务并导入配音字幕</button><p class="muted">独立保存，不影响原作品。直接从文案／音频开始的完整入口将在后续接入。</p></section>
  <section class="video-card"><h2>继续编辑</h2><p class="muted" v-if="!items.length">还没有视频分镜草案。</p><button class="video-record" v-for="item in items" :key="item.id" @click="open(item.id)"><strong>{{item.settings.name}}</strong><span>{{item.scenes.length}} 条字幕 · {{item.shots.length}} 个镜头</span></button></section>
 </div>
 <details class="video-srt-import"><summary>单独导入 SRT（测试入口，不包含配音）</summary>
 <div class="video-start">
  <section class="video-card"><h2>新建分镜草案</h2><label>项目名称<input v-model="input.name" maxlength="100"></label><label>视频比例<select v-model="input.ratio"><option value="16:9">横屏 16:9</option><option value="9:16">竖屏 9:16</option></select></label>
   <DynamicTextModeSelector v-model="input.dynamic_text_mode" />
   <label class="dynamic-scene-reference-toggle"><input type="checkbox" v-model="input.scene_references_enabled">启用场景参考</label><p class="muted">所有表达方式均可使用；为重复场景额外生成参考图并计费，关闭后不生成。</p>
   <label>导入已校对字幕<input type="file" accept=".srt" @change="loadSrt"></label><textarea v-model="input.srt" rows="4" placeholder="粘贴 SRT（保留时间戳），或选择字幕文件"/><details><summary>画风与人物设定</summary><label>统一画风<textarea v-model="input.style" rows="2"/></label><label>人物设定<textarea v-model="input.characters" rows="2"/></label><label>世界与场景<textarea v-model="input.world" rows="2"/></label></details><button class="primary-btn" :disabled="busy||!input.srt" @click="create">创建草案</button></section>
 </div>
 </details>
 </template>
 <section v-if="project&&view==='audio'" class="video-card video-history-panel">
  <button v-if="project.source_project?.id" :disabled="busy||videoRunning||exportRunning" @click="emit('edit-audio',project)">编辑配音与字幕</button>
  <div class="history-heading"><div><p class="eyebrow">阶段 2 · 已完成</p><h2>配音与字幕</h2><p class="muted">这里播放的是本动态任务采用的配音快照；参数均来自创建任务时保存的记录。</p></div><span class="readonly-badge">只读</span></div>
  <audio v-if="project.audio" :key="narrationUrl" controls preload="metadata" :src="narrationUrl"/>
  <p v-else class="studio-notice">这个测试草案没有保存配音文件。</p>
  <div class="readonly-summary-grid"><label>配音执行方<input readonly :value="reviewRequest.tts_engine||'该任务未记录'"/></label><label>音色<input readonly :value="reviewRequest.cluster_voice_id||reviewRequest.qwen_tts_voice||reviewRequest.tts_voice_id||'该任务未记录'"/></label><label>情绪<input readonly :value="reviewRequest.tts_emotion||'参考原音频'"/></label><label>语速<input readonly :value="reviewRequest.tts_speed??'该任务未记录'"/></label></div>
  <details open><summary>最终字幕与时间戳 · 只读</summary><div class="subtitle-review-list"><p v-for="scene in project.scenes" :key="scene.slide_id"><time>{{scene.start.toFixed(1)}}～{{scene.end.toFixed(1)}}s</time><span>{{scene.text}}</span></p></div></details>
 </section>
 <section v-if="project&&view==='motions'" class="video-card motion-workspace">
  <header class="motion-heading"><div><p class="eyebrow">阶段 4 · 动态镜头</p><h2>{{project.settings.name}}</h2><p class="muted">已完成 {{completedVideos}} / {{dynamicShots.length}} 个动态镜头 · {{project.shots.length-dynamicShots.length}} 个静态画面</p></div><button v-if="videoStageReady" type="button" class="ghost-btn" @click="view='storyboard'">查看已确认分镜</button></header>
  <p v-if="!videoStageReady" class="studio-notice">请先在“动态分镜”阶段完成并确认核心分镜图。此处不会自动提交视频生成。</p>
  <template v-else>
   <audio v-if="project.audio" ref="motionAudio" :key="narrationUrl" preload="auto" :src="narrationUrl" class="motion-narration-audio"/>
   <div v-if="dynamicShots.length" class="motion-api-summary"><strong>项目默认：{{localVideo?(project.creation_parameters.comfyui_profile_id?.startsWith('ocv-h3-')?'OCV 内置 ComfyUI':'外部 ComfyUI'):'视频 API'}}</strong><span>各镜头可单独设置，批量生成尊重各镜头配置。</span><button type="button" :disabled="busy||videoRunning" @click="openClassicEditor">修改项目默认配置</button></div>
   <p v-if="videoModelError&&dynamicShots.some(shot=>optionsFor(shot).backend==='api')" class="studio-notice error">{{videoModelError}}</p>
   <div v-if="dynamicShots.length" class="motion-generation-bar">
    <div><strong>{{videoRunning?(project.status==='video_stopping'?'正在安全停止…':'正在生成动态镜头…'):completedVideos===dynamicShots.length?'动态片段已准备完成':'先试一镜，再决定是否批量生成'}}</strong><p class="muted">{{videoRunning?'按照“接口与服务”中的并发设置处理，实时进度见日志。停止不会撤销云端已提交或已计费的任务。':'只有点击生成并确认后才调用视频 API；已完成片段不会重复提交，失败镜头不会自动重新扣费。'}}</p></div>
    <button v-if="videoRunning" type="button" class="primary-btn" :disabled="busy||project.status==='video_stopping'" @click="stopVideos">{{project.status==='video_stopping'?'正在停止…':'停止生成'}}</button>
   <div v-else class="motion-batch-actions"><button type="button" :disabled="busy||videoRunning||!selectedShot" @click="reopenShot(selectedShot)">重新编辑分镜</button><button type="button" class="primary-btn" :disabled="busy||videoRunning||!dynamicShots.length" @click="regenerateAllVideos">全部重新生成（{{dynamicShots.length}}）</button><button type="button" class="primary-btn" :disabled="busy||!pendingVideos.length" @click="generateVideos(pendingVideos)">生成全部未完成（{{pendingVideos.length}}）</button></div>
   </div>
   <div v-if="allVideosReady&&!videoRunning" class="video-next-stage"><div><strong>{{dynamicShots.length?'全部动态片段已完成':'全部静态画面已就绪'}}</strong><p>{{dynamicShots.length?'镜头素材已就绪，可以进入最终合成。':'本项目不需要生成动态片段，可直接用图片与配音合成。'}}</p></div><button type="button" class="primary-btn" @click="view='export'">下一步：合成与导出</button></div>
   <p v-if="dynamicShots.some(shot=>shot.video_status!=='completed'&&(shot.video_terminal||shot.video_status==='failed'||(shot.video_status==='unknown'&&!shot.video_resume_available)))" class="duration-repair-note needs-review">批量处理会跳过失败或状态待核实的镜头，请在左侧选择相应镜头查看原因。确认失败的任务需单独点击“重新付费生成”。</p>
   <OperationStatus v-if="videoRunning||busy" title="动态镜头运行状态" :pending="busy" :task="{status:videoRunning?'running':'',message:project.status==='video_stopping'?'正在停止后续处理；已提交的服务端任务可能继续运行':'当前批次正在处理，可切换镜头查看各自状态'}" :summary="videoBatchSummary"/>
   <p v-if="project.error" class="studio-notice error">{{project.error}}</p>
   <ResizableShotWorkspace v-if="project.shots.length" scope="dynamic-motions" class="video-editor motion-editor">
    <template #sidebar><VideoShotNavigator :shots="project.shots" :selected="selected" :image-url="imageUrl" motion :status-label="videoStatusLabel" @select="selected=$event" /></template>
    <article v-if="selectedShot" :key="selectedShot.id" class="video-detail motion-detail">
     <div class="shot-local-navigation"><strong>镜头 {{String(selected+1).padStart(2,'0')}} <span>/ {{project.shots.length}}</span></strong><div><button :disabled="selected===0" @click="moveShot(-1)" aria-label="上一个镜头">← 上一镜</button><button :disabled="selected===project.shots.length-1" @click="moveShot(1)" aria-label="下一个镜头">下一镜 →</button></div></div>
     <details open class="shot-subtitles"><summary>对应字幕（只读）</summary><p v-for="id in selectedShot.slide_ids" :key="id">{{scenesById[id]?.text}}</p></details>
     <div class="motion-shot-heading"><div><h3>第 {{String(selected+1).padStart(2,'0')}} 镜 · {{videoStatusLabel(selectedShot)}}</h3><p class="muted">{{selectedShot.intent}}</p></div><div class="motion-heading-actions"><span v-if="selectedShot.kind==='video'" class="readonly-badge">使用 {{selectedShot.duration}} 秒 · 请求 {{requestedSeconds(selectedShot)}} 秒 · {{requestedResolution(selectedShot)}}</span><label v-if="selectedShot.kind==='video'" class="motion-upload-button" :class="{disabled:busy||videoRunning}">上传本地视频替换<input type="file" accept="video/mp4,.mp4" :disabled="busy||videoRunning" @change="uploadReplacementVideo(selectedShot,$event)"></label><button type="button" :disabled="busy||videoRunning" @click="reopenShot(selectedShot)">重新编辑本镜</button></div></div>
     <div class="structure-toolbar"><strong>局部返修</strong><button type="button" :disabled="!canEditStructure||selected===0" @click="selected-=1;openBoundaryEditor('boundary')">与上一镜调整字幕</button><button type="button" :disabled="!canEditStructure||selected===project.shots.length-1" @click="openBoundaryEditor('boundary')">与下一镜调整字幕</button><button type="button" :disabled="!canEditStructure||selectedShot.slide_ids.length<2" @click="openBoundaryEditor('split')">拆分 / 新增分镜</button><button v-if="project.structure_history?.length" type="button" :disabled="!canEditStructure" @click="undoStructure">撤回结构调整</button><button v-if="project.status==='image_review'&&selectedShot.design_needs_review" type="button" :disabled="busy||imageEditRunning" @click="confirmDesign">确认本镜修改</button><span v-else-if="selectedShot.design_review_confirmed" class="muted">修改已确认</span></div>
     <ShotVideoOptions v-if="selectedShot.kind==='video'" v-model="selectedOptions" :profiles="comfyProfiles" :loading="comfyLoading" :disabled="shotOptionsLocked" :dirty="selectedOptionsDirty" :saved="Boolean(selectedShot.video_generation_options)" :save-disabled="busy||videoRunning" :api-ready="Boolean(apiVideoReady)" :api-resolution="videoModel?.resolution||'720p'" :ratio="project.settings.ratio||'16:9'" :seconds="requestedSeconds(selectedShot)" :image-src="imageUrl(selectedShot)" @refresh="loadComfyProfiles" @save="saveShotOptions" @apply-all="applyAllShotOptions"/>
     <p v-if="selectedShot.video_request" class="muted">当前片段实际使用：{{selectedShot.video_request.backend==='comfyui'?'ComfyUI · '+(comfyProfiles.find(item=>item.id===selectedShot.video_request.profile_id)?.name||selectedShot.video_request.profile_id):'视频 API'}} · {{requestedResolution(selectedShot)}}。上方修改在下次生成时生效。</p>
     <p v-if="selectedLocalVideo&&comfyError" class="studio-notice error">工作流读取失败：{{comfyError}}</p>
     <div v-if="selectedShot.kind==='video'&&(!videoRunning||(localQueueRunning&&selectedLocalVideo&&!['running','unknown'].includes(selectedShot.video_status)))" class="motion-shot-actions">
      <button v-if="selectedShot.video_status==='completed'" type="button" class="primary-btn" :disabled="busy||videoRunning||!selectedVideoConfigured" @click="regenerateVideo(selectedShot)">按此配置重新生成本镜</button>
      <button v-else-if="selectedShot.video_terminal" type="button" class="primary-btn" :disabled="busy||!selectedVideoConfigured" @click="generateVideos([selectedShot],true,false,true)">{{selectedLocalVideo?'重新生成本镜':'重新付费生成本镜'}}</button>
      <button v-else-if="selectedShot.video_resume_available" type="button" class="primary-btn" :disabled="busy||!selectedVideoConfigured" @click="generateVideos([selectedShot],false,false,true)">{{selectedShot.video_not_submitted?'继续生成（尚未提交）':'继续查询原任务'}}</button>
      <button v-else-if="canProcessVideo(selectedShot)" type="button" class="primary-btn" :disabled="busy||!selectedVideoConfigured||Boolean(videoRunning&&selectedShot.video_queued)" @click="generateVideos([selectedShot],false,false,true)">{{videoRunning&&selectedShot.video_queued?'已加入生成队列':selectedLocalVideo&&videoRunning?'加入本地生成队列':'试生成当前镜头'}}</button>
     </div>
     <WorkspacePanels scope="dynamic-motions" :log-count="project.logs?.length||0">
      <template #preview>
     <div v-if="selectedShot.kind==='video'&&selectedShot.video_status==='completed'" class="motion-preview">
      <video :key="selectedShot.id+':'+selectedShot.video_version+':'+project.audio_version" controls muted playsinline preload="metadata" :src="videoUrl(selectedShot)" :poster="imageUrl(selectedShot)" @loadedmetadata="syncMotionPreview($event,'metadata')" @play="syncMotionPreview($event,'play')" @pause="syncMotionPreview($event,'pause')" @ended="syncMotionPreview($event,'ended')" @seeking="syncMotionPreview($event,'seeking')" @timeupdate="syncMotionPreview($event,'timeupdate')" @ratechange="syncMotionPreview($event,'seeking')"></video>
      <p v-if="selectedShot.audio_timing_adjustment" class="motion-preview-note">当前试听与导出均使用新版配音：{{previewAdaptRate<1?'画面降速至 '+previewAdaptRate.toFixed(2)+' 倍':'画面按新时长裁切'}}，目标 {{selectedShot.duration}} 秒。播放器进度显示原片时间。<strong v-if="previewAdaptRate<0.8">降速较明显，建议重点检查，必要时重新生成本镜。</strong></p>
      <label v-if="project.audio" class="motion-audio-toggle"><input type="checkbox" v-model="previewNarration">同步播放本镜配音</label>
      <p v-else class="muted">该任务没有配音快照，只能预览画面。</p>
      <a :href="videoUrl(selectedShot)" :download="'镜头'+String(selected+1).padStart(2,'0')+'.mp4'" class="motion-download">↓ 下载本镜原片</a>
     </div>
     <OperationStatus v-if="selectedShot.kind==='video'" title="本镜运行状态" :task="videoTask(selectedShot)"/>
     <p v-if="selectedShot.video_error" class="studio-notice error">{{selectedShot.video_error}}</p>
     <p v-if="selectedShot.video_task_id" class="motion-task-id muted">云端任务编号：<code>{{selectedShot.video_task_id}}</code><span v-if="selectedShot.video_attempt"> · 第 {{selectedShot.video_attempt}} 次请求</span></p>
     <p v-if="selectedShot.kind==='video'&&selectedShot.video_status==='unknown'&&!selectedShot.video_resume_available" class="duration-repair-note needs-review">本次提交结果未知，且无法安全查询原任务。请先向服务商核实，不要重新提交，避免重复扣费。</p>
     <p v-if="selectedShot.kind==='video'" class="motion-preview-note muted">原始视频音轨保持静音；试看时可同步本镜对应的项目配音。为覆盖字幕时长，请求秒数可能向上取整，试看和最终合成都只使用本镜字幕时长。</p>
     <p v-else class="studio-notice">这是静态镜头，保留已确认的图片，不提交视频 API，也不产生视频生成费用。</p>
     <details v-if="selectedShot.kind==='video'&&selectedVideoHistory.length" class="motion-history"><summary>历史生成结果 · {{selectedVideoHistory.length}} 个</summary><p class="muted">重生成和本地替换都不会覆盖旧片段。可先预览，再决定采用或删除。</p><div class="motion-history-list"><div v-for="(row,position) in selectedVideoHistory" :key="row.index+':'+(row.entry.video_version||row.entry.video)" class="motion-history-row"><span><b>{{historyVersionLabel(row,position)}}</b><small>{{row.entry.invalidated_reason||'此前生成的动态片段'}}</small></span><div class="actions"><button type="button" @click="historyPreviewIndex=historyPreviewIndex===row.index?null:row.index">{{historyPreviewIndex===row.index?'收起':'预览'}}</button><button type="button" class="primary-btn" :disabled="busy||videoRunning" @click="adoptHistoryVersion(selectedShot,row.index)">采用</button><button type="button" :disabled="busy||videoRunning" @click="deleteHistoryVersion(selectedShot,row.index)">删除</button></div><video v-if="historyPreviewIndex===row.index" controls muted playsinline preload="metadata" :src="historyVideoUrl(selectedShot,row.index)"></video></div></div></details>
     <details class="motion-core-reference" :open="selectedShot.video_status!=='completed'"><summary>已确认的核心分镜图 · 图1</summary><div class="storyboard-result"><img :src="imageUrl(selectedShot)" :alt="'第 '+(selected+1)+' 镜核心分镜图'"></div></details>
      </template>
      <template #prompts><p class="muted">{{selectedShot.video_request?'本次实际提交内容（只读）':'将提交的视频提示词（只读）'}}</p><p class="motion-final-prompt">{{selectedShot.video_request?.prompt||selectedShot.video_prompt||'本镜为静态画面，无视频提示词。'}}</p></template>
      <template #logs><div class="video-logs" role="log" aria-live="polite"><div v-for="(line,index) in project.logs" :key="index">{{line}}</div><p v-if="!project.logs?.length">暂无任务日志</p></div></template>
     </WorkspacePanels>
    </article>
   </ResizableShotWorkspace>
  </template>
 </section>
 <section v-if="project&&view==='export'" class="video-card video-history-panel">
  <div class="history-heading"><div><p class="eyebrow">阶段 5</p><h2>合成与导出</h2><p class="muted">动态片段按字幕时长裁切，静态镜头自动补帧；旁白和字幕始终沿用本任务确认后的时间轴。</p></div><span class="readonly-badge">{{project.status==='completed'?'已完成':exportRunning?'正在合成':project.status==='export_failed'?'需要重试':'等待合成'}}</span></div>
  <p v-if="project.error" class="studio-notice error">{{project.error}}</p>
  <details class="export-layout-panel" v-if="project.shots.length"><summary>调整成片布局与字幕 <span class="muted">{{exportLayoutDirty?'有修改 · 合成时保存':'拖动画面、缩放、调整字幕位置'}}</span></summary>
   <div class="export-layout-content"><div class="export-layout-context"><label>选择预览分镜<select v-model="selected"><option v-for="(shot,index) in project.shots" :key="shot.id" :value="index">第 {{String(index+1).padStart(2,'0')}} 镜 · {{shot.summary||shot.title||shot.id}}</option></select></label><p class="muted">仅切换预览素材，布局统一用于全部镜头。调整后点击“按当前选项重新合成”或“开始合成”保存并生效，不会重新出图或生成视频。</p><label class="export-audio-option"><input type="checkbox" :checked="exportLayoutVariant!=='raw'" @change="exportLayoutVariant=$event.target.checked?'both':'raw'"><span>预览显示字幕（不改变输出版本）</span></label></div>
   <SubtitleStyleEditor :settings="exportLayoutSettings" :variant="exportLayoutVariant" dynamic hide-editions :readonly="exportRunning||busy" :preview-image="selectedShot?.image?imageUrl(selectedShot):''" :preview-shot-id="selectedShot?.id" :preview-url="base+'/'+project.id+'/layout-preview'" />
   </div>
  </details>
  <template v-if="project.status==='completed'&&project.export">
   <div class="export-preview-switch" role="group" aria-label="选择成片预览版本"><button :class="{active:exportPreview==='raw'}" :aria-pressed="exportPreview==='raw'" @click="exportPreview='raw'">纯净版 · 无字幕</button><button :class="{active:exportPreview==='subtitles'}" :aria-pressed="exportPreview==='subtitles'" @click="exportPreview='subtitles'">字幕版</button><span class="muted">仅切换预览，不会重新合成</span></div>
   <video :key="exportPreview" controls playsinline preload="metadata" :src="exportUrl(exportPreview)"></video>
   <div class="export-actions"><a class="primary-btn" :href="exportUrl('subtitles')" download>下载字幕版</a><a class="ghost-btn" :href="exportUrl('raw')" download>下载纯净版</a><button type="button" class="ghost-btn" @click="openExportFolder">打开输出目录</button></div>
   <label class="export-audio-option"><input type="checkbox" v-model="useVideoAudio"><span><strong>使用动态片段声音</strong><small>保留视频模型生成的环境音效，与 TTS 配音混合；关闭后仅使用 TTS 配音。</small></span></label>
   <button type="button" class="ghost-btn" :disabled="busy" @click="exportVideo">按当前选项重新合成</button>
   <p class="muted">输出目录同时包含最终字幕 SRT、配音快照和动态分镜方案。</p>
  </template>
  <template v-else-if="exportRunning"><p class="studio-notice">正在本地合成，请保持 OCV 运行。完成后本页会自动显示预览，无需刷新整个页面。</p><details v-if="project.logs?.length" open class="planning-log"><summary>合成日志</summary><div class="video-logs"><div v-for="(line,index) in project.logs" :key="index">{{line}}</div></div></details></template>
  <template v-else-if="allVideosReady"><label class="export-audio-option"><input type="checkbox" v-model="useVideoAudio"><span><strong>使用动态片段声音</strong><small>保留视频模型生成的环境音效，与 TTS 配音混合；关闭后仅使用 TTS 配音。</small></span></label><button type="button" class="primary-btn" :disabled="busy" @click="exportVideo">{{project.status==='export_failed'||project.status==='completed'?'重新合成':'开始合成字幕版与纯净版'}}</button><p class="muted">该步骤只在本地运行，不调用图像或视频 API，不产生接口费用；可以修改选项后重新合成。</p></template>
  <template v-else><p class="studio-notice">请先完成全部动态镜头。</p><button v-if="videoStageReady" type="button" class="primary-btn" @click="view='motions'">返回动态镜头</button></template>
 </section>
 <ParameterReview v-if="project&&view==='parameters'" :request="reviewRequest" :reference-assets="[]" :status="project.status" :busy="busy" @duplicate="duplicateFromSnapshot" @reset="resetFromSnapshot" />
  <section v-if="project&&view==='storyboard'" class="video-card video-project">
  <p v-if="project.status==='planning'" class="studio-notice" role="status" aria-live="polite">正在根据配音和字幕规划动静分镜，下方会显示进度；完成后自动展开镜头列表。</p>
  <p v-else-if="!project.shots.length&&(project.error||error)" class="studio-notice">{{planningCanResume?'已有规划进度已保存，点击“继续未完成规划”即可接着处理（会调用 API）。':'分镜规划尚未完成，可以点击“重试规划”重新运行（会调用 API）。'}}</p>
  <p v-if="project.planning_recovery" class="studio-notice" role="status">{{project.planning_recovery}}</p>
  <div v-if="project.source_project" class="video-source-summary"><p class="muted">配音字幕来源：{{project.source_project.name}} · 已保存的当前配音与时间轴</p><audio v-if="project.audio" ref="sourceAudio" :key="narrationUrl" controls preload="metadata" :src="narrationUrl"/></div>
  <header><div><h2>{{project.settings.name}}</h2><p class="muted">{{storyboardStageLabel}} · 所有切分都遵循字幕边界</p></div><div class="video-header-actions"><button v-if="['draft','storyboard_review'].includes(project.status)" class="primary-btn" :disabled="busy||!project.shots.length" @click="review">确认分镜方案并生成核心图</button><button v-if="['planning','stopping'].includes(project.status)" class="primary-btn" :disabled="busy||project.status==='stopping'" @click="stopPlanning">{{project.status==='stopping'?'正在停止…':'停止规划'}}</button><button v-else-if="project.status==='generation_ready'" class="primary-btn" :disabled="busy" @click="generateImages">开始生成核心分镜图</button><button v-else-if="['image_generating','image_stopping'].includes(project.status)" class="primary-btn" :disabled="busy||project.status==='image_stopping'" @click="stopImages">{{project.status==='image_stopping'?'正在停止…':'停止生成'}}</button><button v-else-if="project.status==='image_review'" :disabled="busy||project.shots.every(s=>s.image_status==='completed')" @click="generateImages">重试未完成图片</button><button v-else-if="!storyboardLocked" :disabled="busy" @click="plan">{{planningButtonLabel}}</button></div></header>
  <div v-if="['image_generating','image_stopping','image_review',...videoStages].includes(project.status)" class="video-next-stage" role="status">
   <div><strong>{{project.status==='image_review'?'核心分镜图等待确认':videoStageReady?'核心分镜图已确认':project.status==='image_stopping'?'正在安全停止':'正在生成核心分镜图'}}</strong><p>已完成 {{project.shots.filter(s=>s.image_status==='completed').length}} / {{project.shots.length}} 张<span v-if="project.shots.some(s=>s.image_status==='failed')">，失败 {{project.shots.filter(s=>s.image_status==='failed').length}} 张</span>。图片确认后，只为动态镜头生成视频。</p></div>
   <button v-if="project.status==='image_review'" class="primary-btn" :disabled="busy||imageEditRunning||sceneReferencesBlockConfirm||project.shots.some(s=>s.image_status!=='completed')" @click="confirmImages()">确认核心分镜图，进入动态视频生成</button>
   <button v-else-if="videoStageReady" type="button" class="primary-btn" @click="view='motions'">{{completedVideos?'查看动态镜头':'进入动态镜头生成'}}</button>
   <span v-else>请在下方查看实时日志</span>
  </div>
  <p v-if="project.error" class="studio-notice error">{{project.error}}</p>
  <section v-if="warnedShots" class="prompt-warning-index" aria-label="建议核对的镜头"><div><strong>{{warnedShots}} 个镜头存在需要留意的提示词差异</strong><p class="muted">可能涉及遗漏的短文字、阶段内容混用或要求冲突，不代表生成失败。点击镜头查看详情；可检查后继续确认。</p></div><button v-for="row in warnedShotRows" :key="row.shot.id" type="button" :class="{active:selected===row.index}" @click="showWarning(row.index)"><b>第 {{String(row.index+1).padStart(2,'0')}} 镜</b><span>{{row.notes.map(note=>note.label).join('；')}}</span></button></section>
  <details v-if="promptWarnings.length" :key="selected" class="shot-subtitles current-shot-warning"><summary>第 {{String(selected+1).padStart(2,'0')}} 镜{{shotHasPromptWarning(selectedShot)?'核对详情':'措辞比对记录'}} · {{promptWarnings.length}} 条</summary><p class="muted">以下来自字面比对，不是语义判定。称呼、说法或气泡名称不同，不一定影响画面；请结合实际提示词判断。</p><p v-for="(note,i) in promptWarnings" :key="i" :class="{'prompt-note-warning':note.severity==='warning'}"><span class="prompt-note-level">{{note.severity==='warning'?'建议核对':'措辞记录'}}</span>{{note.label}}</p></details>
  <details v-if="project.planning_recovery&&project.shots[selected]?.visual_description&&!project.shots[selected]?.image_prompt" open class="shot-subtitles"><summary>当前镜头已保存的核心画面草案（尚未定稿）</summary><p>{{project.shots[selected].visual_description}}</p><p class="muted">可参考此草案，在下方补齐核心分镜图提示词和视频提示词；保存并确认前不会生成图片。</p></details>
  <details v-if="project.logs.length&&!project.shots.length" class="planning-log" open><summary>任务日志 <span class="muted">{{project.logs.length}} 条</span></summary><div class="video-logs" role="log" aria-live="polite"><div v-for="(line,i) in project.logs" :key="i">{{line}}</div></div></details>
  <p v-if="sceneReferencesBusy||project.scene_references_status==='failed'" class="studio-notice">{{sceneReferenceMessage||'正在准备场景参考…'}}</p>
  <button v-if="sceneReferencesEnabled&&project.status==='image_review'&&sceneReferencesNeedGeneration" :disabled="busy||imageEditRunning" @click="generateSceneReferences">补充 / 重试场景参考（调用 API）</button>
  <ResizableShotWorkspace class="video-editor" v-if="project.shots.length" scope="dynamic-storyboard"><template #sidebar><VideoShotNavigator :shots="project.shots" :selected="selected" :image-url="imageUrl" :has-warning="shotHasPromptWarning" :scene-assets="sceneAssets" @select="selected=$event;boundary=1;closeBoundaryEditor()" /></template>
   <template v-if="selected>=0&&selectedShot"><div class="structure-toolbar"><strong>局部返修</strong><button type="button" :disabled="!canEditStructure||selected===0" @click="selected-=1;openBoundaryEditor('boundary')">与上一镜调整字幕</button><button type="button" :disabled="!canEditStructure||selected===project.shots.length-1" @click="openBoundaryEditor('boundary')">与下一镜调整字幕</button><button type="button" :disabled="!canEditStructure||selectedShot.slide_ids.length<2" @click="openBoundaryEditor('split')">拆分 / 新增分镜</button><button v-if="project.structure_history?.length" type="button" :disabled="!canEditStructure" @click="undoStructure">撤回结构调整</button><button v-if="project.status==='image_review'&&selectedShot.design_needs_review" type="button" :disabled="busy||imageEditRunning" @click="confirmDesign">确认本镜修改</button><span v-else-if="selectedShot.design_review_confirmed" class="muted">修改已确认</span></div></template>
   <div v-for="{shot,i} in storyboardRows" :key="shot.id" v-show="i===selected" class="video-detail">
    <div class="shot-local-navigation"><strong>{{i<0?'场景 '+(-i):'镜头 '+String(i+1).padStart(2,'0')}} <span v-if="i>=0">/ {{project.shots.length}}</span></strong><div v-if="i>=0"><button :disabled="selected===0" @click="moveShot(-1)" aria-label="上一个镜头">← 上一镜</button><button :disabled="selected===project.shots.length-1" @click="moveShot(1)" aria-label="下一个镜头">下一镜 →</button></div></div>
    <p v-if="i>=0&&storyboardLocked" class="readonly-stage-note">{{project.status==='image_review'?'字幕分组与时长已经确认；你仍可切换动静态、修改本镜提示词、重绘或替换图片。切换类型自动保存，不会重新生成图片。':'本阶段已经确认，以下内容仅供回顾，不会提供修改入口。'}}</p>
    <p v-if="i<0" class="readonly-stage-note">场景参考 · 用于镜头 {{sceneUsedByLabel(shot)}}。不占用视频时长；相关镜头下次重绘才采用新版场景。</p>
    <div @input="i>=0&&!storyboardLocked&&(dirty=true)">
     <WorkspacePanels scope="dynamic-storyboard" :log-count="project.logs.length">
      <template #preview><fieldset :disabled="busy||['planning','stopping'].includes(project.status)">
     <details v-if="i>=0" open class="shot-subtitles"><summary>对应字幕（只读）</summary><p v-for="id in shot.slide_ids" :key="id">{{project.scenes.find(s=>s.slide_id===id)?.text}}</p></details>
     <ShotReferencePreview :shot="shot" :references="project.references||[]" :scene="sceneAssetFor(shot)" :scene-enabled="sceneReferencesEnabled" :reference-url="redrawReferenceUrl" :image-url="imageUrl" />
     <div v-if="shot.image_status" class="storyboard-result" :class="shot.image_status"><img v-if="shot.image_status==='completed'" :src="imageUrl(shot)" :alt="'核心分镜图 '+(i+1)"><div v-else><strong>{{shot.image_status==='running'?'正在生成核心分镜图':shot.image_status==='failed'?'生成失败':'等待生成'}}</strong><p v-if="shot.image_error">{{shot.image_error}}</p></div></div>
     <div v-if="sceneAssetFor(shot)" class="shot-scene-reference">
      <img v-if="sceneAssetFor(shot).image_status==='completed'" :src="imageUrl(sceneAssetFor(shot))" :alt="sceneAssetFor(shot).name||'场景参考'">
      <div><strong>场景参考 · {{sceneAssetFor(shot).name||sceneAssetFor(shot).id}}</strong><p class="muted">{{sceneUsageLabel(shot)}}</p><label v-if="project.status==='image_review'" class="scene-reference-toggle"><input type="checkbox" v-model="useSceneReference" :disabled="imageAssetRunning(shot)||sceneAssetFor(shot).image_status!=='completed'">重绘时使用场景参考</label><p v-if="sceneAssetFor(shot).image_status!=='completed'" class="muted">场景图尚未完成，可在上方场景参考资产中查看或重试。</p></div>
     </div>
      </fieldset></template>
      <template #prompts><fieldset :disabled="busy||['planning','stopping'].includes(project.status)">
     <label v-if="i>=0">这一镜头想表达什么<input v-model="shot.intent" :disabled="busy||imageEditRunning||(storyboardLocked&&project.status!=='image_review')" @input="rememberMotionDraft(shot)"></label>
     <div v-if="i>=0" class="video-row"><label>画面类型<select :value="shot.kind" @change="changeShotKind(shot,$event)" :disabled="imageAssetRunning(shot)||(storyboardLocked&&project.status!=='image_review')"><option value="static">静态画面</option><option value="video">动态视频</option></select></label><div v-if="shot.kind==='video'&&projectReferenceAudio" class="shot-audio-settings"><span>参考音频</span><label><input type="checkbox" :checked="shot.reference_audio_enabled!==false" :disabled="imageAssetRunning(shot)||(storyboardLocked&&project.status!=='image_review')" @change="shot.reference_audio_enabled=$event.target.checked;rememberMotionDraft(shot)">本镜启用</label><label :class="{disabled:shot.reference_audio_enabled===false}"><input type="checkbox" :checked="shot.reference_audio_lipsync!==false" :disabled="shot.reference_audio_enabled===false||imageAssetRunning(shot)||(storyboardLocked&&project.status!=='image_review')" @change="shot.reference_audio_lipsync=$event.target.checked;rememberMotionDraft(shot)">人物对口型</label></div><p class="muted">使用 {{shot.duration}} 秒<span v-if="shot.kind==='video'"> · 请求 {{Math.max(4,Math.ceil(shot.duration))}} 秒</span></p></div>
     <p v-if="shot.audio_timing_adjustment&&!shot.design_needs_review" class="image-edit-status completed">新配音时长已同步：已有动态片段继续复用；导出时会自动裁切较长片段，或匀速放慢较短片段以贴合本镜配音。</p>
     <p v-if="shot.duration_repair?.note" class="duration-repair-note" :class="{'needs-review':['boundary_fallback','single_subtitle_static'].includes(shot.duration_repair.method)}">{{shot.duration_repair.note}}</p>
     <p v-if="shot.warning" class="studio-notice">{{shot.warning}}</p>
     <label v-if="shot.kind==='video'" class="motion-field">动态表达<textarea v-model="shot.action" rows="3" @input="rememberMotionDraft(shot)" :disabled="imageAssetRunning(shot)||(storyboardLocked&&project.status!=='image_review')"/></label>
     <label class="image-prompt-field">{{i<0?'场景图提示词':'核心分镜图提示词'}} <small v-if="project.status==='image_review'" class="muted">修改后点击“按当前提示词重绘”提交，当前图片不会自动变化。</small><textarea v-model="shot.image_prompt" rows="4" :disabled="imageAssetRunning(shot)||(storyboardLocked&&project.status!=='image_review')" @input="project.status==='image_review'&&rememberImagePrompt(shot)"/></label>
     <label v-if="shot.kind==='video'" class="video-prompt-field">视频模型最终提示词<textarea v-model="shot.video_prompt" rows="5" @input="rememberMotionDraft(shot)" :disabled="imageAssetRunning(shot)||(storyboardLocked&&project.status!=='image_review')"/></label>
     <p v-if="autoMotionBusy" role="status" class="muted">正在按核心图规划动态表达与视频模型最终提示词…不会生成图片或视频。</p>
     <button v-if="project.status==='image_review'&&shot.kind==='video'&&!shot.action?.trim()" type="button" :disabled="busy||imageEditRunning||autoMotionBusy" @click="run(()=>autoFillMotion(shot))">自动补写动态表达与视频提示词</button>
     <button v-if="project.status==='image_review'&&Object.keys(motionDrafts).length" type="button" :disabled="busy||imageEditRunning" @click="run(save)">保存动态表达与视频提示词</button>
     <p v-if="project.status==='image_review'&&shot.kind==='video'&&!shot.video_prompt?.trim()" class="muted">本镜尚未填写视频提示词。可直接按核心图生成，或先填写动态表达，再更新两个提示词；不会自动重绘图片。</p>
     <p v-if="shot.prompt_refresh_note" class="image-edit-status completed">{{shot.prompt_refresh_note}}</p>
     <p v-if="shot.image_prompt_out_of_sync" class="duration-repair-note needs-review">核心图提示词已经更新，但当前图片还是上一版。请按新提示词重绘，或改用“按当前核心图更新视频提示词”。</p>
      </fieldset></template>
      <template #logs><div v-if="i===selected" class="video-logs" role="log" aria-live="polite"><div v-for="(line,index) in project.logs" :key="index">{{line}}</div><p v-if="!project.logs.length">暂无任务日志</p></div></template>
     </WorkspacePanels>
    </div>
    <section v-if="shot.kind==='video'&&['storyboard_review','image_review'].includes(project.status)" class="single-shot-refresh">
     <div><strong>重新规划本镜</strong><p class="muted">完整重规划从字幕与项目设定重新设计；局部更新沿用当前编辑内容。均不改变字幕、时长或其他镜头。</p></div>
     <button type="button" :disabled="busy||imageEditRunning" @click="refreshShotPrompts(shot,'full')">完整重规划本镜</button>
     <button type="button" :disabled="busy||imageEditRunning||!shot.action?.trim()" @click="refreshShotPrompts(shot,'action')">按动态表达更新两个提示词</button>
     <button type="button" :disabled="busy||imageEditRunning||!shot.image_prompt?.trim()" @click="refreshShotPrompts(shot,'image')">按核心图更新视频提示词</button>
     <p v-if="shot.image_origin==='reference_redraw'||shot.image_origin==='upload'" class="muted">当前图片来自{{shot.image_origin==='upload'?'本地替换':'参考模式重绘'}}；第二个按钮会先识别最终图片，再更新视频提示词。</p>
    </section>
    <section v-if="project.status==='image_review'" class="storyboard-edit-tools">
     <div class="storyboard-edit-heading"><div><strong>{{i<0?'修正这张场景图':'修正这张核心分镜图'}}</strong><p class="muted">提示词修改只在点击重绘时提交；参考图最多 3 张，编号按下方选中顺序传给图像模型。</p><p v-if="!useCurrentReference&&!redrawReferenceIds.length" class="muted">当前没有选择参考图，本次只按提示词重绘。</p><p v-if="sceneAssetFor(shot)&&useSceneReference" class="muted">场景图自动附在这些参考素材之后，只约束空间布局。</p></div><select v-model="redrawResolution" aria-label="重绘分辨率"><option value="">跟随任务清晰度</option><template v-if="redrawIcan"><option value="2K">2K</option><option value="2.5K">2.5K</option></template><template v-else><option value="1k">1K</option><option value="2k">2K</option><option value="4k">4K</option></template></select></div>
     <div class="storyboard-reference-library">
      <div class="reference-library-heading"><div><strong>本次重绘参考图</strong><p class="muted">点击图片选择或取消；绿色卡片会按图号顺序传给图像模型。</p></div><div><label class="file-action">＋ 上传新参考图<input type="file" accept="image/jpeg,image/png,image/webp" multiple :disabled="busy||imageEditRunning" @change="uploadRedrawReferences"></label><button v-if="uploadedRedrawReferences.length" type="button" :disabled="busy||imageEditRunning" @click="clearUploadedRedrawReferences">清空新增图</button></div></div>
      <div class="storyboard-reference-grid">
       <article v-if="shot.image_status==='completed'" class="reference-image-card" :class="{active:useCurrentReference}">
        <button type="button" :disabled="busy||imageAssetRunning(shot)" @click="toggleCurrentReference"><img :src="imageUrl(shot)" alt="当前核心分镜图"><span><b>当前核心图</b><small>重绘前的本图</small></span><em>{{useCurrentReference?'图1 · 正在使用':'未选择'}}</em></button>
       </article>
       <article v-for="asset in redrawReferenceGallery" :key="asset.origin+':'+asset.id" class="reference-image-card" :class="{active:redrawReferenceIds.includes(asset.id)}">
        <button type="button" :disabled="busy||imageAssetRunning(shot)" @click="toggleRedrawReference(asset.id)"><img :src="redrawReferenceUrl(asset)" :alt="asset.displayName"><span><b>{{asset.displayName}}</b><small>{{asset.origin==='project'?'创建任务时上传':asset.origin==='scene'?'项目场景资产':'本页后来上传'}}</small></span><em>{{redrawReferenceIds.includes(asset.id)?'图'+redrawReferenceNumber(asset.id)+' · 正在使用':'未选择'}}</em></button>
        <button v-if="asset.origin==='uploaded'" type="button" class="reference-card-delete" :disabled="busy||imageEditRunning" title="删除这张新增参考图" @click.stop="deleteRedrawReference(asset)">删除</button>
       </article>
       <p v-if="!redrawReferenceGallery.length" class="reference-library-empty">创建任务时没有上传参考图；可在右上角补充新图片。</p>
      </div>
     </div>
     <OperationStatus title="本镜图片状态" :task="shot.image_task||{status:shot.image_status,message:shot.image_status==='running'?'正在生成核心图':''}"/>
     <div class="storyboard-edit-actions">
      <button type="button" class="primary-btn" :disabled="busy||imageAssetRunning(shot)||!shot.image_prompt?.trim()" @click="redrawShot(shot)">▶ 按当前提示词重绘</button>
      <label class="file-action">↕ 替换本地图片<input type="file" accept="image/jpeg,image/png,image/webp" :disabled="busy||imageAssetRunning(shot)" @change="replaceShotImage($event,shot)"></label>
      <button type="button" :disabled="busy||imageAssetRunning(shot)||!shot.image_history?.length" @click="undoShot(shot)">↶ 撤回上一版</button>
      <button type="button" :disabled="busy||imageAssetRunning(shot)||!shot.baseline_image_prompt" @click="resetShotPrompt(shot)">恢复初始提示词</button>
     </div>
    </section>
    <section v-if="!storyboardLocked&&!['planning','stopping'].includes(project.status)&&(project.manual_groups?.length||shot.design_needs_review||shot.previous_designs?.length)" class="video-actions">
     <span v-if="project.manual_groups?.length" class="muted">手动字幕分组已锁定，重新规划也会保留。</span>
     <template v-if="shot.design_needs_review"><p>字幕范围已调整，原提示词保留为草稿，请按新字幕更新设计，或检查修改后确认沿用。</p><button :disabled="busy" @click="repairDesigns">只更新受影响镜头设计</button><button :disabled="busy" @click="confirmDesign">确认沿用当前设计</button></template>
     <details v-if="shot.previous_designs?.length"><summary>查看调整前的设计</summary><article v-for="(old,index) in shot.previous_designs" :key="index"><b>{{old.intent}}</b><p>{{old.action}}</p><p>{{old.image_prompt}}</p><p>{{old.video_prompt}}</p></article></details>
    </section>
    <div v-if="i>=0&&canEditStructure" class="video-actions"><button v-if="!storyboardLocked" class="primary-btn" :disabled="busy||!dirty||['planning','stopping'].includes(project.status)" @click="run(save)">保存镜头修改</button><button :disabled="busy||(shot.slide_ids.length<2&&i===project.shots.length-1)||['planning','stopping'].includes(project.status)" @click="openBoundaryEditor()">调整分镜…</button><button v-if="!storyboardLocked&&project.structure_history?.length" :disabled="busy||['planning','stopping'].includes(project.status)" @click="undoStructure">撤回结构调整</button><button :disabled="busy||shot.slide_ids.length<2||['planning','stopping'].includes(project.status)" @click="openBoundaryEditor('split')">新增分镜（选择字幕切口）</button><button :disabled="busy||project.shots.length<2||['planning','stopping'].includes(project.status)" @click="structure('delete')">删除画面并合并字幕</button></div>
    <p v-if="structureFeedback" class="studio-notice" :class="{error:!!error}" role="status">{{structureFeedback}}</p>
   </div>
  </ResizableShotWorkspace>
 </section>
</section>
 <Teleport to="body">
  <div v-if="boundaryEditor.open&&project&&selectedShot" class="structure-overlay" @click.self="!busy&&closeBoundaryEditor()">
   <audio v-if="project.audio" ref="boundaryAudio" :key="narrationUrl" :src="narrationUrl" preload="metadata" />
    <section v-if="selected>=0&&boundaryEditor.open&&boundaryPreview" class="video-boundary-editor structure-dialog" role="dialog" aria-modal="true" aria-label="调整字幕与分镜">
     <header><div><strong>调整字幕与分镜 · 第 {{String(selected+1).padStart(2,'0')}} 镜</strong><p class="muted">按现有字幕切口调整。配音保持连续；受影响镜头需检查设计与素材，其他镜头保留。</p></div><button type="button" @click="closeBoundaryEditor">关闭</button></header>
     <div class="boundary-modes" role="group" aria-label="选择分镜调整方式">
      <button type="button" :aria-pressed="boundaryEditor.mode==='boundary'" :class="{active:boundaryEditor.mode==='boundary'}" :disabled="busy||selected===project.shots.length-1" @click="openBoundaryEditor('boundary')">调整字幕归属</button>
      <button type="button" :aria-pressed="boundaryEditor.mode==='split'" :class="{active:boundaryEditor.mode==='split'}" :disabled="busy||selectedShot.slide_ids.length<2" @click="openBoundaryEditor('split')">拆分 / 新增分镜</button>
      <button type="button" :aria-pressed="boundaryEditor.mode==='merge'" :class="{active:boundaryEditor.mode==='merge'}" :disabled="busy||selected===project.shots.length-1" @click="openBoundaryEditor('merge')">合为一镜</button>
     </div>
     <p class="boundary-mode-help muted">{{boundaryEditor.mode==='boundary'?'在当前镜头与后一个镜头之间移动字幕，镜头数量不变。':boundaryEditor.mode==='split'?'在当前镜头内选择字幕切口，一个镜头拆成两个。':'将当前镜头与后一个镜头合并，以下显示合并前的两段内容。'}}</p>
     <label v-if="boundaryEditor.mode!=='merge'" class="video-boundary-slider"><span>{{boundaryEditor.mode==='boundary'?'调整相邻镜头边界':'选择切口'}}：第 {{boundary}} 条字幕之后 · {{boundaryPreview.boundary.toFixed(2)}} 秒</span><input v-model.number="boundary" type="range" min="1" :max="selectedShot.slide_ids.length+(boundaryEditor.mode==='boundary'?(project.shots[selected+1]?.slide_ids.length||0):0)-1" step="1"></label>
     <div class="video-boundary-grid">
      <article><div><b>{{boundaryEditor.mode==='split'?'拆分后的前镜头':'当前镜头'}}</b><span>{{(boundaryPreview.left.end-boundaryPreview.left.start).toFixed(2)}} 秒</span></div><p v-for="row in boundaryPreview.left.rows" :key="row.slide_id">{{row.text}}</p><button type="button" @click="playBoundaryPart('left')">▶ 试听整段</button></article>
      <article><div><b>{{boundaryEditor.mode==='split'?'拆分后的后镜头':'后一镜头'}}</b><span>{{(boundaryPreview.right.end-boundaryPreview.right.start).toFixed(2)}} 秒</span></div><p v-for="row in boundaryPreview.right.rows" :key="row.slide_id">{{row.text}}</p><button type="button" @click="playBoundaryPart('right')">▶ 试听整段</button></article>
     </div>
     <div class="video-boundary-listen"><button type="button" @click="playBoundaryEdge('left')">▶ 试听前段末尾</button><button type="button" @click="playBoundaryEdge('right')">▶ 试听后段开头</button><button type="button" @click="playBoundaryEdge('continuous')">▶ 连续试听交界</button><span>共同边界 {{boundaryPreview.boundary.toFixed(2)}} 秒</span></div>
     <p v-if="boundaryEditor.mode==='merge'&&boundaryPreview.combinedDuration>15" class="duration-repair-note needs-review">合并后共 {{boundaryPreview.combinedDuration.toFixed(2)}} 秒，超过建议的 15 秒；建议拆分，也可保留并按工作流能力尝试。</p>
     <p v-else-if="boundaryEditor.mode!=='merge'&&(boundaryPreview.left.end-boundaryPreview.left.start>15||boundaryPreview.right.end-boundaryPreview.right.start>15)" class="duration-repair-note needs-review">调整后仍有镜头超过建议的 15 秒，可能增加资源占用；你可以继续保留，也可保存后拆分。</p>
     <p class="muted">保存后返回动态分镜检查：受影响的现有图片保留，新镜头需要生成图片；受影响视频存入历史，需按新字幕范围重新生成。此操作不调用生成 API。</p>
     <p v-if="error" class="studio-notice error" role="alert">{{error}}</p><footer><button type="button" @click="closeBoundaryEditor">取消</button><button class="primary-btn" type="button" :disabled="busy||!canEditStructure" @click="applyBoundaryEdit">{{boundaryEditor.mode==='boundary'?'确认调整边界':boundaryEditor.mode==='split'?'确认拆成两个镜头':'确认合并镜头'}}</button></footer>
    </section>
  </div>
 </Teleport>
</template>
<style scoped>
.structure-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:12px;margin:12px 0;border:1px solid var(--border,#35423f);border-radius:10px}.structure-toolbar strong{margin-right:8px}.structure-overlay{position:fixed;inset:0;z-index:2000;background:#000a;display:flex;align-items:center;justify-content:center;padding:20px}.structure-dialog{width:min(940px,100%);max-height:90vh;overflow:auto;box-sizing:border-box;background:var(--panel,#1e2625);color:var(--text,#eef3f1);padding:24px;border:1px solid var(--border,#35423f);border-radius:16px}.structure-dialog button{cursor:pointer}.structure-dialog button:disabled{opacity:.45;cursor:default}.structure-dialog footer{display:flex;gap:10px;justify-content:flex-end;margin-top:20px}
.motion-batch-actions button:first-child:not(.primary-btn){color:var(--accent);border:1px solid var(--accent);background:color-mix(in srgb,var(--accent) 18%,var(--panel));font-weight:650}
.motion-batch-actions button:first-child:not(.primary-btn):hover:not(:disabled){background:color-mix(in srgb,var(--accent) 30%,var(--panel));box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 15%,transparent)}
.motion-batch-actions button:first-child:not(.primary-btn):focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.replan-description{flex:1}
.replan-options{margin-top:8px;max-width:780px}
.replan-options summary{cursor:pointer;font-size:13px;line-height:1.7}
.replan-options fieldset{border:0;padding:14px 0 0;margin:0;min-width:0}
.replan-options fieldset:disabled{opacity:.6}
.replan-options .replan-regroup{display:flex;align-items:center;gap:8px;margin:16px 0 6px;font-size:13px}
.replan-regroup input{width:16px;height:16px;margin:0;accent-color:var(--accent,#81d9bd)}
.export-layout-panel{margin:18px 0;padding:16px 20px;border:1px solid var(--line,#394440);border-radius:14px;background:#19211f}.export-layout-panel>summary{cursor:pointer;font-weight:600;display:flex;gap:12px;flex-wrap:wrap;align-items:center}.export-layout-panel>summary span{font-size:12px;font-weight:400}.export-layout-content{max-width:760px;margin:24px auto 8px;display:grid;gap:20px}.export-layout-context{display:grid;gap:10px;border-bottom:1px solid #394440;padding-bottom:16px}.export-layout-context label{display:grid;gap:8px}.export-layout-context select{width:100%;min-width:0}.export-layout-context p{font-size:13px;line-height:1.7}
.prompt-note-level{display:inline-block;margin-right:8px;color:var(--muted,#aab8b3);font-size:12px}.prompt-note-warning .prompt-note-level{color:#e6ba62}.current-shot-warning p{overflow-wrap:anywhere}
.video-card label.dynamic-scene-reference-toggle{display:flex;align-items:center;gap:8px;margin-top:14px}.video-card .dynamic-scene-reference-toggle input{width:16px;height:16px;margin:0;accent-color:var(--accent,#81d9bd)}.video-card :deep(.dynamic-text-mode-row){grid-template-columns:1fr}.video-card :deep(.dynamic-text-mode-row .director-strategy-options){justify-self:start}
.motion-heading,.motion-generation-bar,.motion-shot-heading{display:flex;align-items:center;justify-content:space-between;gap:18px}.motion-heading h2,.motion-shot-heading h3{margin:3px 0 7px}.motion-heading p,.motion-shot-heading p{margin:0;line-height:1.55}.motion-generation-bar{padding:15px 17px;border:1px solid color-mix(in srgb,var(--accent,#81d9bd) 45%,var(--border,#35423f));border-radius:12px;background:color-mix(in srgb,var(--accent,#81d9bd) 7%,var(--panel,#1e2625));margin-bottom:15px}.motion-generation-bar p{margin:5px 0 0;line-height:1.6}.motion-generation-bar>button{flex-shrink:0}.motion-batch-actions{display:flex;justify-content:flex-end;gap:10px;flex-shrink:0;flex-wrap:wrap}.motion-shot-heading{align-items:flex-start;margin-bottom:14px}.motion-shot-heading .readonly-badge{white-space:normal;line-height:1.5}.motion-preview{display:grid;gap:12px;margin:16px 0}.motion-preview video{display:block;width:100%;max-height:560px;background:#070b0a;border:1px solid var(--border,#35423f);border-radius:12px}.motion-download{justify-self:start;display:inline-flex;padding:9px 13px;border:1px solid var(--border,#35423f);border-radius:8px;color:var(--accent,#81d9bd);text-decoration:none}.motion-progress{padding:25px;text-align:center;border:1px solid var(--border,#35423f);border-radius:12px;background:var(--bg,#141918);color:var(--muted,#aab8b3)}.motion-progress p{margin-bottom:0}.motion-task-id{font-size:12px;overflow-wrap:anywhere}.motion-shot-actions{display:flex;gap:10px;flex-wrap:wrap;margin:15px 0}.motion-preview-note{font-size:13px;line-height:1.65}.motion-core-reference,.motion-request-review{padding:14px;border:1px solid var(--border,#35423f);border-radius:11px}.motion-core-reference .storyboard-result{margin-bottom:0}.motion-request-review p{line-height:1.65;overflow-wrap:anywhere}.motion-final-prompt{white-space:pre-wrap;color:var(--text,#eef3f1);font-size:14px}.motion-editor aside button.motion-failed{border-left:3px solid #d8aa4b}.motion-detail{min-width:0}
@media(max-width:900px){.motion-heading,.motion-generation-bar,.motion-shot-heading{align-items:flex-start;flex-direction:column}.motion-generation-bar>button,.motion-batch-actions,.motion-batch-actions button{width:100%}}
.single-shot-refresh{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin:14px 0;padding:14px 16px;border:1px solid var(--border,#35423f);border-radius:11px;background:color-mix(in srgb,var(--accent,#81d9bd) 5%,var(--panel,#1e2625))}.single-shot-refresh>div{flex:1 1 320px}.single-shot-refresh>div p{margin:5px 0 0}.single-shot-refresh>p{flex-basis:100%;margin:2px 0 0}
.prompt-warning-index{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:14px 0;padding:14px 16px;border:1px solid color-mix(in srgb,#d8aa4b 62%,var(--border,#35423f));border-radius:11px;background:color-mix(in srgb,#d8aa4b 7%,var(--panel,#1e2625))}.prompt-warning-index>div{flex:1 1 360px}.prompt-warning-index p{margin:5px 0 0;line-height:1.6}.prompt-warning-index button{display:grid;gap:3px;text-align:left;max-width:320px}.prompt-warning-index button span{font-size:12px;color:var(--muted,#aab8b3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.prompt-warning-index button.active{border-color:#d8aa4b}.video-editor aside button.warning b{display:flex;align-items:center;justify-content:space-between;gap:6px}.video-editor aside button.warning em{font-size:11px;font-style:normal;color:#e6ba62;border:1px solid color-mix(in srgb,#d8aa4b 55%,transparent);border-radius:999px;padding:2px 6px}
.boundary-modes{display:flex;gap:4px;padding:4px;margin-top:16px;border:1px solid var(--border,#35423f);border-radius:10px;background:var(--bg,#141918);max-width:480px}.boundary-modes button{flex:1;min-width:0;padding:10px 8px;border:1px solid transparent;border-radius:7px;background:transparent;color:inherit}.boundary-modes button.active{background:color-mix(in srgb,var(--accent,#81d9bd) 16%,var(--panel,#1e2625));border-color:var(--accent,#81d9bd);color:var(--accent,#81d9bd)}.boundary-mode-help{margin:10px 0 14px;line-height:1.6;font-size:13px}.boundary-modes button:focus-visible{outline:2px solid var(--accent,#81d9bd);outline-offset:2px}.export-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:18px;align-items:center}.export-actions a{text-decoration:none;display:inline-flex;align-items:center;justify-content:center}.video-history-panel>video{display:block;width:100%;max-height:68vh;margin-top:18px;background:#080b0c;border-radius:10px}
.video-workspace{max-width:1500px}.video-start{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px}.video-card{border:1px solid var(--border,#35423f);background:var(--panel,#1e2625);border-radius:18px;padding:24px;min-width:0}.video-card h2{margin:0 0 16px}.video-card label{display:grid;gap:8px;margin-bottom:14px}.video-card input,.video-card textarea,.video-card select{width:100%;box-sizing:border-box;color:inherit;background:var(--bg,#141918);border:1px solid var(--border,#35423f);border-radius:9px;padding:10px}.video-card textarea{resize:vertical}.video-card details{margin:14px 0}.video-card summary{cursor:pointer;margin-bottom:12px}.video-record{display:grid;text-align:left;gap:6px;width:100%;padding:14px;margin-bottom:8px}.video-project header,.video-row{display:flex;align-items:center;justify-content:space-between;gap:20px}.video-editor{display:grid;grid-template-columns:220px minmax(0,1fr);gap:24px;margin-top:20px}.video-editor aside{max-height:780px;overflow:auto}.video-editor aside button{display:grid;gap:8px;text-align:left;width:100%;padding:14px;margin:0 0 8px}.video-editor aside button.active{border-color:var(--accent,#81d9bd);background:#30443c}.video-detail fieldset{border:0;padding:0;margin:0;min-width:0}.video-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px;align-items:center}.video-actions select{width:auto}.video-logs{background:#101514;border-radius:10px;padding:14px;line-height:1.8;margin-top:15px;color:#9fbbb1}.video-card button{cursor:pointer}.video-card button:disabled{opacity:.4;cursor:default}@media(max-width:900px){.video-start,.video-editor{grid-template-columns:1fr}.video-editor aside{max-height:220px}.video-project header{align-items:start;flex-wrap:wrap}}
.video-stage-nav{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin:18px 0 22px;padding:7px;border:1px solid var(--border,#35423f);border-radius:14px;background:var(--panel,#1e2625)}
.video-stage-nav button{display:grid;grid-template-columns:auto 1fr;grid-template-rows:auto auto;column-gap:9px;align-items:center;text-align:left;min-width:0;padding:11px 13px;border:1px solid transparent;border-radius:10px;background:transparent;color:var(--muted,#aab8b3);cursor:pointer}
.video-stage-nav button>span{grid-row:1/3;display:grid;place-items:center;width:25px;height:25px;border-radius:50%;background:var(--bg,#141918);color:inherit}.video-stage-nav b{color:inherit}.video-stage-nav small{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.video-stage-nav button.done{color:var(--accent,#81d9bd)}.video-stage-nav button.current{color:var(--text,#eef3f1)}.video-stage-nav button.upcoming{opacity:.62}.video-stage-nav button.active{border-color:color-mix(in srgb,var(--accent,#81d9bd) 58%,var(--border,#35423f));background:color-mix(in srgb,var(--accent,#81d9bd) 10%,transparent);color:var(--text,#eef3f1)}
.video-history-panel{margin-top:0}.history-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:20px}.history-heading h2{margin:3px 0 6px}.history-heading p{margin:0}.readonly-badge{flex:0 0 auto;padding:6px 10px;border:1px solid var(--border,#35423f);border-radius:999px;color:var(--muted,#aab8b3);font-size:12px}.video-history-panel textarea{line-height:1.65;resize:vertical}.video-history-panel audio{width:min(560px,100%);margin-bottom:18px}.readonly-summary-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.subtitle-review-list{display:grid;gap:8px;max-height:440px;overflow:auto}.subtitle-review-list p{display:grid;grid-template-columns:110px minmax(0,1fr);gap:12px;margin:0;padding:10px 12px;border:1px solid var(--border,#35423f);border-radius:9px}.subtitle-review-list time{color:var(--muted,#aab8b3);font-variant-numeric:tabular-nums}
.readonly-stage-note{margin:0 0 14px;padding:10px 12px;border:1px solid var(--border,#35423f);border-radius:9px;color:var(--muted,#aab8b3);background:var(--bg,#141918)}
@media(max-width:900px){.video-stage-nav{grid-template-columns:1fr 1fr}.readonly-summary-grid{grid-template-columns:1fr 1fr}.subtitle-review-list p{grid-template-columns:1fr}}
.video-header-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px}
@media(max-width:900px){.video-header-actions{width:100%;justify-content:flex-start;flex-wrap:wrap}}
.video-workspace .video-detail{min-width:0}
.video-workspace .video-card textarea{min-height:0;height:auto;line-height:1.6}
.video-workspace .video-detail textarea{height:136px;min-height:80px;max-height:380px;overflow-y:auto}
.video-workspace .video-detail .motion-field textarea{height:88px}
.video-workspace .video-detail .video-prompt-field textarea{height:152px}
.video-workspace .video-editor{align-items:start;gap:20px}
.video-workspace .video-editor aside{min-width:0;overflow-x:hidden;overflow-y:auto;max-height:620px}
.video-workspace .video-editor aside button{box-sizing:border-box;min-width:0;max-width:100%;white-space:normal;gap:5px}
.video-workspace .video-editor aside button span{min-width:0;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.5}
.video-workspace .video-row label{margin-bottom:10px}
.shot-audio-settings{display:flex;align-items:center;gap:10px;padding:8px 11px;border:1px solid color-mix(in srgb,var(--accent,#81d9bd) 32%,var(--border,#35423f));border-radius:9px;background:#202c28}.shot-audio-settings>span{font-size:11px;color:var(--muted,#9fb0aa)}.video-card .shot-audio-settings label{display:flex;align-items:center;gap:6px;margin:0;white-space:nowrap;font-size:12px}.video-card .shot-audio-settings input{width:15px;height:15px;margin:0;padding:0}.shot-audio-settings label.disabled{opacity:.45}
.duration-repair-note{margin:0 0 14px;padding:9px 12px;border-left:3px solid var(--accent,#81d9bd);border-radius:0 7px 7px 0;background:color-mix(in srgb,var(--accent,#81d9bd) 6%,transparent);color:var(--muted,#aab8b3);font-size:13px;line-height:1.6}.duration-repair-note.needs-review{border-left-color:#d8b86d;background:color-mix(in srgb,#d8b86d 7%,transparent)}
.video-workspace .planning-log{margin:12px 0 0;border-top:1px solid var(--border,#35423f);padding-top:12px}
.video-workspace .planning-log summary{display:list-item;font-size:13px;margin:0}
.video-workspace .planning-log .video-logs{max-height:160px;overflow:auto;margin-top:10px;font-size:13px}
.motion-api-summary{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:11px 13px;margin-bottom:12px;border:1px solid var(--border,#35423f);border-radius:10px;background:var(--panel,#1e2625)}.motion-api-summary span{color:var(--muted,#aab8b3);margin-right:auto}.motion-api-summary button{padding:6px 10px}
.video-workspace .video-source-summary audio{height:36px;max-width:100%}
.video-workspace .video-actions{border-top:1px solid var(--border,#35423f);padding-top:14px;margin-top:12px}
.video-boundary-editor{margin:18px 0 6px;padding:16px;border:1px solid color-mix(in srgb,var(--accent,#81d9bd) 55%,var(--border,#35423f));border-radius:12px;background:color-mix(in srgb,var(--accent,#81d9bd) 6%,var(--bg,#141918))}.video-boundary-editor>header,.video-boundary-editor>footer,.video-boundary-listen,.video-boundary-grid article>div{display:flex;align-items:center;justify-content:space-between;gap:12px}.video-boundary-editor>header p{margin:5px 0 0}.video-boundary-editor>footer{justify-content:flex-end;margin-top:14px;padding-top:13px;border-top:1px solid var(--border,#35423f)}.video-boundary-slider{margin:16px 0!important;padding:12px;border:1px solid var(--border,#35423f);border-radius:9px;background:var(--panel,#1e2625)}.video-boundary-slider input{padding:0!important;accent-color:var(--accent,#81d9bd)}.video-boundary-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.video-boundary-grid article{min-width:0;padding:14px;border:1px solid var(--border,#35423f);border-radius:10px;background:var(--panel,#1e2625)}.video-boundary-grid article span{color:var(--muted,#aab8b3);font-variant-numeric:tabular-nums}.video-boundary-grid article p{margin:9px 0;line-height:1.65;overflow-wrap:anywhere}.video-boundary-grid article button{margin-top:7px}.video-boundary-listen{justify-content:flex-start;flex-wrap:wrap;margin-top:12px}.video-boundary-listen span{margin-left:auto;color:var(--muted,#aab8b3);font-size:13px;font-variant-numeric:tabular-nums}
.video-workspace .shot-subtitles{margin:0 0 18px;padding:14px 16px;border:1px solid var(--border,#35423f);border-radius:10px;background:color-mix(in srgb,var(--panel,#1e2625) 72%,var(--bg,#141918))}
.video-workspace .shot-subtitles summary{margin:0;font-weight:700}.video-workspace .shot-subtitles[open] summary{margin-bottom:9px}.video-workspace .shot-subtitles p{margin:5px 0;line-height:1.65}
.video-workspace .video-next-stage{display:flex;align-items:center;justify-content:space-between;gap:18px;margin:16px 0 4px;padding:14px 16px;border:1px solid color-mix(in srgb,var(--accent,#81d9bd) 52%,var(--border,#35423f));border-radius:12px;background:color-mix(in srgb,var(--accent,#81d9bd) 9%,var(--panel,#1e2625))}.video-workspace .video-next-stage p{margin:5px 0 0;color:var(--muted,#aab8b3);line-height:1.55}.video-workspace .video-next-stage span{flex:0 0 auto;padding:8px 11px;border-radius:8px;background:var(--bg,#141918);color:var(--muted,#aab8b3);font-size:13px}
.video-workspace .storyboard-result{display:grid;place-items:center;min-height:180px;margin:0 0 18px;border:1px solid var(--border,#35423f);border-radius:12px;overflow:hidden;background:#080d0c;text-align:center}.video-workspace .storyboard-result img{display:block;width:100%;max-height:520px;object-fit:contain}.video-workspace .storyboard-result.failed{padding:18px;border-color:#85504f;color:#ffaaa7}.video-workspace .storyboard-result.running{padding:18px;color:var(--accent,#81d9bd)}
.storyboard-edit-tools{margin-top:14px;padding:16px;border:1px solid color-mix(in srgb,var(--accent,#81d9bd) 38%,var(--border,#35423f));border-radius:12px;background:color-mix(in srgb,var(--accent,#81d9bd) 6%,var(--bg,#141918))}.storyboard-edit-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.storyboard-edit-heading p{margin:5px 0 0}.storyboard-edit-heading select{width:auto;min-width:150px;padding:8px 10px;color:inherit;background:var(--bg,#141918);border:1px solid var(--border,#35423f);border-radius:8px}.storyboard-reference-list,.storyboard-edit-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:13px}.storyboard-reference-list button.active{border-color:var(--accent,#81d9bd);background:color-mix(in srgb,var(--accent,#81d9bd) 18%,var(--panel,#1e2625));color:var(--text,#eef3f1)}.file-action{display:inline-flex!important;align-items:center;justify-content:center;width:auto!important;margin:0!important;padding:8px 12px;border:1px solid var(--border,#35423f);border-radius:8px;background:var(--panel,#1e2625);cursor:pointer}.file-action input{display:none}.image-edit-status{margin:12px 0 0;padding:9px 11px;border-radius:8px;background:var(--panel,#1e2625);color:var(--muted,#aab8b3)}.image-edit-status.running{color:var(--accent,#81d9bd)}.image-edit-status.failed{color:#ffaaa7}
.storyboard-reference-library{margin-top:14px;padding:13px;border:1px solid var(--border,#35423f);border-radius:11px;background:var(--bg,#141918)}.reference-library-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.reference-library-heading p{margin:4px 0 0}.reference-library-heading>div:last-child{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.storyboard-reference-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:10px;margin-top:12px}.reference-image-card{position:relative;min-width:0;border:1px solid var(--border,#35423f);border-radius:10px;overflow:hidden;background:var(--panel,#1e2625)}.reference-image-card.active{border-color:var(--accent,#81d9bd);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent,#81d9bd) 22%,transparent)}.reference-image-card>button:first-child{display:grid;width:100%;height:100%;padding:0;border:0;border-radius:0;background:transparent;text-align:left;overflow:hidden}.reference-image-card img{display:block;width:100%;height:112px;object-fit:cover;background:#080d0c}.reference-image-card span{display:grid;gap:3px;padding:9px 10px 5px;min-width:0}.reference-image-card b,.reference-image-card small{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.reference-image-card small{color:var(--muted,#aab8b3);font-size:11px}.reference-image-card em{margin:0 10px 10px;padding:4px 7px;border-radius:6px;background:var(--bg,#141918);color:var(--muted,#aab8b3);font-size:11px;font-style:normal;justify-self:start}.reference-image-card.active em{background:color-mix(in srgb,var(--accent,#81d9bd) 18%,var(--bg,#141918));color:var(--accent,#81d9bd)}.reference-card-delete{position:absolute;top:7px;right:7px;min-height:26px!important;padding:4px 7px!important;border-color:#7b4646!important;background:#241817dd!important;color:#ffb3af!important;font-size:11px!important}.reference-library-empty{grid-column:1/-1;margin:0;padding:18px;text-align:center;color:var(--muted,#aab8b3);border:1px dashed var(--border,#35423f);border-radius:8px}
.video-workspace .scene-reference-assets{margin:18px 0 0;padding:16px;border:1px solid var(--border,#35423f);border-radius:12px;background:color-mix(in srgb,var(--accent,#81d9bd) 4%,var(--bg,#141918))}.scene-reference-assets>summary{margin-bottom:0}.scene-reference-assets>summary>.muted{display:inline-block;margin-left:12px;font-size:13px}.scene-reference-assets[open]>summary{margin-bottom:14px}.scene-reference-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.scene-reference-heading p{margin:0 0 8px;line-height:1.6}.scene-reference-heading>button{flex:0 0 auto}.scene-cost-note{font-size:13px;margin:5px 0 12px}.scene-assets-grid{display:grid;gap:12px}.video-card .scene-asset-card{margin:0;padding:12px;border:1px solid var(--border,#35423f);border-radius:10px;background:var(--panel,#1e2625)}.scene-asset-card>summary{display:flex;align-items:center;gap:12px;margin:0}.scene-asset-card[open]>summary{margin-bottom:14px}.scene-asset-card>summary>img,.scene-thumbnail-placeholder{display:block;flex:0 0 76px;width:76px;height:50px;object-fit:cover;border:1px solid var(--border,#35423f);border-radius:6px;background:#080d0c}.scene-thumbnail-placeholder{display:grid;place-items:center;color:var(--muted,#aab8b3);font-size:12px}.scene-asset-card summary small{display:block;margin-top:5px}.scene-asset-state{margin-left:auto;color:var(--muted,#aab8b3);font-size:12px;white-space:nowrap}.scene-asset-card summary::after{content:'⌄';color:var(--muted,#aab8b3);font-size:20px}.scene-asset-card[open] summary::after{transform:rotate(180deg)}.scene-asset-card .storyboard-result img{max-height:360px}.scene-asset-card textarea{min-height:100px;max-height:360px}.shot-scene-reference{display:flex;align-items:center;gap:14px;padding:12px;margin:0 0 16px;border:1px solid var(--border,#35423f);border-radius:10px;background:color-mix(in srgb,var(--accent,#81d9bd) 5%,var(--bg,#141918))}.shot-scene-reference>img{display:block;width:112px;height:72px;object-fit:cover;border-radius:7px;flex:0 0 auto}.shot-scene-reference p{margin:5px 0;line-height:1.5;font-size:13px}.video-card label.scene-reference-toggle{display:flex;align-items:center;gap:8px;margin:8px 0 0;font-size:13px}.video-card .scene-reference-toggle input{width:15px;height:15px;margin:0;accent-color:var(--accent,#81d9bd)}
@media(max-width:900px){.video-workspace .video-editor aside{max-height:240px}}
@media(max-width:900px){.video-workspace .video-next-stage{align-items:flex-start;flex-direction:column}.storyboard-edit-heading{flex-direction:column}.storyboard-edit-heading select{width:100%}.scene-reference-heading{flex-direction:column;gap:8px}.scene-reference-assets>summary>.muted{display:block;margin:7px 0 0}.shot-scene-reference{align-items:flex-start}.shot-scene-reference>img{width:88px;height:60px}.scene-asset-state{display:none}.video-boundary-grid{grid-template-columns:1fr}.video-boundary-listen span{width:100%;margin-left:0}.video-boundary-editor>header{align-items:flex-start}}
.motion-narration-audio{display:none}.motion-audio-toggle{justify-self:start;display:flex;align-items:center;gap:8px}.motion-audio-toggle input{width:auto;margin:0}
.export-audio-option{display:flex;align-items:flex-start;gap:10px;margin:16px 0;padding:12px 14px;border:1px solid var(--border,#35423f);border-radius:10px;background:#1d2825}.export-audio-option input{width:17px;height:17px;margin:2px 0 0}.export-audio-option span{display:grid;gap:3px}.export-audio-option small{color:var(--muted,#aab8b3);line-height:1.5}
.motion-heading-actions{display:flex;align-items:center;justify-content:flex-end;gap:10px;flex-wrap:wrap}
.motion-upload-button{display:inline-flex;align-items:center;padding:9px 13px;border:1px solid var(--border,#35423f);border-radius:8px;cursor:pointer;font-size:13px;color:var(--text,#eef3f1);background:var(--panel,#1e2625)}.motion-upload-button:hover{border-color:var(--accent,#81d9bd)}.motion-upload-button.disabled{opacity:.5;cursor:not-allowed}.motion-upload-button input{display:none}.motion-history{padding:14px;border:1px solid var(--border,#35423f);border-radius:11px;margin:14px 0}.motion-history>summary{cursor:pointer;font-weight:700}.motion-history-list{display:grid;gap:10px;margin-top:12px}.motion-history-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;padding:12px;border:1px solid var(--border,#35423f);border-radius:9px;background:var(--bg,#141918)}.motion-history-row>span{display:grid;gap:4px}.motion-history-row small{color:var(--muted,#aab8b3)}.motion-history-row video{grid-column:1/-1;width:100%;max-height:480px;background:#070b0a;border-radius:8px}.motion-history-row .actions{display:flex;gap:8px;flex-wrap:wrap}
.reference-image-card img{height:128px;object-fit:contain}
</style>
<style scoped>
/* Dynamic workspace: persistent navigation, bounded previews and a single reading column. */
.video-workspace{max-width:1600px;margin-inline:auto;padding-bottom:40px;--workspace-gap:24px}
.workspace-project-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:18px}.workspace-project-heading h1{margin:4px 0;font-size:25px;line-height:1.35;overflow-wrap:anywhere}.workspace-project-heading .eyebrow{margin:0;font-size:11px;letter-spacing:.12em;color:var(--muted)}.workspace-project-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap;flex-shrink:0}.workspace-draft{font-size:12px;color:#e6ba62}
.video-stage-nav{position:sticky;top:12px;z-index:15;margin:0 0 24px;box-shadow:0 8px 24px #0002;background:var(--panel,#1e2625)}.video-stage-nav button{padding:10px;transition:background .15s,border-color .15s}.video-stage-nav button:hover{background:color-mix(in srgb,var(--accent,#81d9bd) 7%,transparent)}.video-stage-nav b{font-size:13px}.video-stage-nav small{font-size:10px;margin-top:3px}
.workspace-stage-heading{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:18px}.workspace-stage-heading h2{margin:0 0 6px;font-size:20px}.workspace-stage-heading p{margin:0;font-size:13px;line-height:1.65}.video-card{padding:22px;border-radius:14px}.video-card .muted{line-height:1.65}.video-workspace button:focus-visible,.video-workspace summary:focus-visible{outline:2px solid var(--accent,#81d9bd);outline-offset:3px}.video-workspace button{min-height:36px}.video-workspace .video-editor{grid-template-columns:248px minmax(0,1fr);gap:var(--workspace-gap);padding-top:20px;border-top:1px solid var(--border,#35423f)}
.video-workspace .video-editor>.shot-navigator{max-height:calc(100vh - 132px);overflow:hidden}.video-detail{background:color-mix(in srgb,var(--bg,#141918) 25%,transparent);padding:20px;border:1px solid var(--border,#35423f);border-radius:12px}.shot-local-navigation{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px}.shot-local-navigation strong{font-size:14px}.shot-local-navigation strong span{font-weight:400;color:var(--muted);font-size:12px}.shot-local-navigation>div{display:flex;gap:6px}.shot-local-navigation button{font-size:12px;padding:6px 10px;min-height:30px}
.video-workspace .shot-subtitles{padding:12px 14px;margin-bottom:16px;font-size:13px;background:var(--panel,#1e2625)}.shot-subtitles[open]{max-height:230px;overflow:auto}.video-workspace .shot-subtitles p{line-height:1.8}.video-workspace .storyboard-result{min-height:120px;background:#0c100f}.video-workspace .storyboard-result img{max-height:min(56vh,560px);width:100%;object-fit:contain}.motion-preview video{max-height:58vh;min-height:180px}.video-workspace .video-detail label{font-size:13px;line-height:1.5}.video-workspace .video-detail textarea{font-size:13px;line-height:1.8;padding:12px;min-height:96px;height:148px}.video-workspace .video-detail .motion-field textarea{height:112px}.video-workspace .video-detail .video-prompt-field textarea{height:160px}.video-workspace .video-detail input{font-size:13px}.video-row{flex-wrap:wrap}.video-row>p{margin-left:auto;font-size:12px}
.motion-heading{padding-bottom:15px;margin-bottom:14px;border-bottom:1px solid var(--border,#35423f)}.motion-heading .eyebrow{display:none}.motion-heading h2{font-size:16px}.motion-heading p{font-size:12px}.motion-generation-bar{padding:14px;gap:16px}.motion-generation-bar strong{font-size:14px}.motion-generation-bar p{font-size:12px;max-width:580px}.motion-batch-actions button{font-size:13px;white-space:nowrap}.motion-shot-heading{flex-direction:column;gap:12px}.motion-shot-heading h3{font-size:16px}.motion-shot-heading p{font-size:13px}.motion-heading-actions{width:100%;justify-content:flex-start;gap:8px}.motion-heading-actions .readonly-badge{margin-right:auto}.motion-heading-actions button,.motion-upload-button{font-size:12px}.motion-preview{grid-template-columns:minmax(0,1fr) auto;align-items:center}.motion-preview video{grid-column:1/-1}.video-card label.motion-audio-toggle{display:flex;align-items:center;margin:0;gap:8px}.video-card .motion-audio-toggle input{width:16px;height:16px;padding:0;accent-color:var(--accent,#81d9bd)}.motion-download{font-size:12px;padding:7px 10px}.motion-task-id{font-size:11px}.motion-preview-note{font-size:12px;line-height:1.7}.motion-core-reference,.motion-request-review,.motion-history{font-size:13px;background:var(--panel,#1e2625)}
.video-workspace .planning-log{padding:10px 12px;border:1px solid var(--border,#35423f);border-radius:8px;margin:12px 0;background:var(--bg,#141918)}.video-workspace .planning-log .video-logs{max-height:120px;line-height:1.7;font-size:12px}.video-workspace .video-source-summary{display:flex;align-items:center;justify-content:space-between;gap:16px;padding-bottom:14px;margin-bottom:16px;border-bottom:1px solid var(--border,#35423f)}.video-source-summary p{font-size:12px;margin:0}.video-source-summary audio{width:290px;flex-shrink:0}.video-project>header h2{font-size:16px;margin:0 0 6px}.video-project>header p{font-size:12px;margin:0}.video-header-actions{flex-wrap:wrap}.video-workspace .video-next-stage{font-size:13px}.video-next-stage button{font-size:13px;flex-shrink:0}.video-workspace .scene-reference-assets{padding:12px;font-size:13px}.readonly-stage-note{font-size:12px;line-height:1.6}.storyboard-edit-tools,.single-shot-refresh{font-size:13px}.single-shot-refresh button,.storyboard-edit-actions button{font-size:12px}.prompt-warning-index{font-size:13px}.prompt-warning-index>button{max-width:200px}.export-preview-switch{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:14px}.export-preview-switch button.active{background:color-mix(in srgb,var(--accent,#81d9bd) 12%,var(--panel,#1e2625));border-color:var(--accent,#81d9bd);color:var(--accent,#81d9bd)}.export-preview-switch span{font-size:12px;margin-left:auto}.video-history-panel>video{margin-top:0;max-height:62vh}.video-card label.export-audio-option{display:flex;gap:10px}.video-card .export-audio-option input{width:17px;flex-shrink:0}
@media(min-width:1500px){.video-workspace .video-editor{grid-template-columns:270px minmax(0,1fr)}.video-detail{padding:24px}}
@media(max-width:1100px){.workspace-project-heading{align-items:flex-start}.workspace-project-actions{max-width:280px}.motion-generation-bar{align-items:flex-start;flex-direction:column}.motion-batch-actions{width:100%}.video-workspace .video-editor{grid-template-columns:210px minmax(0,1fr);gap:16px}.video-detail{padding:14px}}
@media(max-width:900px){.video-stage-nav{top:0;display:flex;overflow-x:auto;gap:4px;padding:5px;border-radius:10px}.video-stage-nav button{flex:0 0 125px}.video-workspace .video-editor{grid-template-columns:1fr}.video-workspace .video-editor>.shot-navigator{max-height:none}.workspace-project-heading{flex-direction:column;gap:10px}.workspace-project-actions{max-width:none;justify-content:flex-start}.video-card{padding:16px}.video-workspace .video-source-summary{align-items:flex-start;flex-direction:column;gap:8px}.motion-preview{grid-template-columns:1fr}.motion-download{justify-self:start}.motion-batch-actions button{width:auto;flex:1}.video-next-stage button{width:100%}.workspace-stage-heading .readonly-badge{display:none}.video-workspace .shot-subtitles{max-height:260px}.motion-history-row{grid-template-columns:1fr}.export-preview-switch span{width:100%;margin:4px 0}.video-row>p{margin-left:0}}
@media(prefers-reduced-motion:reduce){.video-workspace *{transition:none!important;scroll-behavior:auto!important}}
.auto-pilot-status{display:flex;align-items:center;gap:12px;margin:-4px 0 18px;padding:11px 14px;border:1px solid color-mix(in srgb,var(--accent,#81d9bd) 42%,var(--border,#35423f));border-radius:10px;background:color-mix(in srgb,var(--accent,#81d9bd) 7%,var(--panel,#1e2625))}.auto-pilot-dot{width:9px;height:9px;flex:0 0 auto;border-radius:50%;background:var(--accent,#81d9bd);box-shadow:0 0 0 5px color-mix(in srgb,var(--accent,#81d9bd) 14%,transparent)}.auto-pilot-status strong{font-size:13px}.auto-pilot-status p{margin:3px 0 0;color:var(--muted,#aab8b3);font-size:11px}
</style>
