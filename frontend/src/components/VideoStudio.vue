<script setup>
import {computed,ref,watch,nextTick,onMounted,onUnmounted} from 'vue'
import {requestJSON} from '../api'
import ParameterReview from './ParameterReview.vue'
import VideoShotNavigator from './VideoShotNavigator.vue'
import DynamicTextModeSelector from './DynamicTextModeSelector.vue'
import { normalizeDynamicTextMode } from '../dynamicTextMode'
import { shotPromptNotes, shotHasPromptWarning, promptWarningRows } from '../videoPromptWarnings'
const emit=defineEmits(['new-project','duplicate-config','edit-audio','edit-config'])
const items=ref([]), project=ref(null), selected=ref(0), error=ref(''), busy=ref(false), dirty=ref(false)
const view=ref('storyboard')
const exportPreview=ref('raw')
const workspaceTitles={audio:'配音与字幕',storyboard:'动态分镜',motions:'动态镜头',export:'合成与导出',parameters:'参数回顾'}
const workspaceHints={audio:'试听配音、核对字幕；需要调整时进入配音精修。',storyboard:'逐镜确认画面与提示词，再将核心图制作成动态片段。',motions:'逐镜试看、重新生成或替换本地视频，满意后合成成片。',export:'预览和下载成片；合成选项可随时调整，不必重新生成镜头。',parameters:'查看本项目保存的配置，也可以用相同配置新建作品。'}
function moveShot(delta){selected.value=Math.max(0,Math.min((project.value?.shots.length||1)-1,selected.value+delta));boundary.value=1;closeBoundaryEditor()}
const motionDrafts=ref({})
function rememberMotionDraft(shot){if(project.value?.status==='image_review'){motionDrafts.value[shot.id]={kind:shot.kind,action:shot.action||'',video_prompt:shot.video_prompt||'',reference_audio_enabled:shot.reference_audio_enabled!==false,reference_audio_lipsync:shot.reference_audio_lipsync!==false};dirty.value=true}else dirty.value=true}
async function saveMotionDrafts(){for(const [id,fields] of Object.entries(motionDrafts.value)){const updated=await call('/'+project.value.id+'/shots/'+encodeURIComponent(id)+'/motion','POST',{revision:project.value.revision,...fields});delete motionDrafts.value[id];acceptImageUpdate(updated)}dirty.value=false;sessionStorage.removeItem(draftKey(project.value.id))}
const autoMotionBusy=ref(false)
async function autoFillMotion(shot){
 autoMotionBusy.value=true
 videoLogsOpen.value=true
 const shotNumber=project.value.shots.findIndex(row=>row.id===shot.id)+1
 project.value.logs.push(`第 ${String(shotNumber).padStart(2,'0')} 镜：正在调用 Agent 自动补写动态表达…`)
 try{acceptImageUpdate(await call('/'+project.value.id+'/shots/'+encodeURIComponent(shot.id)+'/refresh-prompts','POST',{revision:project.value.revision,basis:'image',action_only:true,action:'',image_prompt:shot.image_prompt||''}))}
 catch(e){project.value.logs.push(`第 ${String(shotNumber).padStart(2,'0')} 镜：自动补写动态表达失败：${e.message}`);throw e}
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
  if(wasStatic&&kind==='video'&&!current.action?.trim()){
   try{await autoFillMotion(current)}
   catch(e){throw new Error('已切换为动态，但自动补写动态表达失败：'+e.message+'。可手动填写，或点击“自动补写动态表达”重试。')}
  }
 })
 event.target.value=project.value.shots.find(row=>row.id===shot.id)?.kind||shot.kind
}
const videoStages=['video_generation_ready','video_generating','video_stopping','video_review','exporting','export_failed','completed']
const videoModel=ref(null),videoModelError=ref(''),videoLogsOpen=ref(false)
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
const videoStatusLabel=shot=>shot.kind!=='video'?'静态画面':({pending:'等待生成',running:'处理中',completed:'已完成',failed:'生成失败',unknown:'状态待核实',stopped:'已停止'}[shot.video_status]||'等待生成')
const requestedSeconds=shot=>Number(shot.video_request?.duration||shot.generation_duration||Math.max(4,Math.ceil(Number(shot.duration)||0)))
const requestedResolution=shot=>shot.video_request?.resolution||shot.video_resolution||videoModel.value?.resolution||'720p'
const videoUrl=shot=>base+'/'+project.value.id+'/videos/'+encodeURIComponent(shot.id)+'?v='+encodeURIComponent(shot.video_version||project.value.revision)
const historyVideoUrl=(shot,index)=>base+'/'+project.value.id+'/videos/'+encodeURIComponent(shot.id)+'/history/'+index+'?v='+encodeURIComponent(project.value.revision)
function defaultProjectView(record){return ['exporting','export_failed','completed'].includes(record?.status)?'export':videoStages.includes(record?.status)?'motions':'storyboard'}
const allVideosReady=computed(()=>dynamicShots.value.length>0&&completedVideos.value===dynamicShots.value.length)
const exportRunning=computed(()=>project.value?.status==='exporting')
const useVideoAudio=ref(true)
const exportUrl=variant=>base+'/'+project.value.id+'/export/'+variant+'?v='+encodeURIComponent(project.value.revision)
async function exportVideo(automated=false){
 if(!allVideosReady.value)return
 const audioNote=useVideoAudio.value?'动态片段中的原声音效会降低音量后与 TTS 配音混合。':'动态片段原声将被忽略，成片只使用 TTS 配音。'
 if(!automated&&!confirm('将按字幕时间轴裁切动态片段、补齐静态镜头，并生成字幕版与纯净版成片。\n\n'+audioNote+'\n\n是否开始？'))return
 await run(async()=>{project.value=await call('/'+project.value.id+'/export','POST',{revision:project.value.revision,use_video_audio:useVideoAudio.value});view.value='export'})
}
async function openExportFolder(){await run(async()=>{await call('/'+project.value.id+'/export/open','POST')})}
async function loadVideoModel(){
 videoModelError.value=''
 try{videoModel.value=await requestJSON('/api/video-model')}catch(e){videoModelError.value=e.message}
}
async function generateVideos(shots,retryFailed=false,automated=false){
 if(!shots.length)return
 if(localVideo.value){
  const queueNote=videoRunning.value?'当前已有镜头在生成，本次选择将追加到队列，前一镜完成后自动继续。':'将按镜头顺序提交到本地生成队列。'
  if(!automated&&!confirm(`将使用本项目的 ComfyUI 工作流生成 ${shots.length} 个镜头。\n${queueNote}${project.value.creation_parameters.comfyui_reference_audio?'\n将逐镜注入对应 TTS 参考音频。':''}\n是否开始？`))return
  await run(async()=>{project.value=await call('/'+project.value.id+'/videos/generate','POST',{revision:project.value.revision,shot_ids:shots.map(shot=>shot.id),retry_failed:retryFailed});view.value='motions';videoLogsOpen.value=true});return
 }
 if(!videoConfigured.value){error.value='请先到“接口与服务”保存视频 API 配置，再开始生成。';return}
 const lines=shots.map(shot=>{const index=project.value.shots.findIndex(item=>item.id===shot.id)+1;return '第 '+String(index).padStart(2,'0')+' 镜：'+requestedSeconds(shot)+' 秒 · '+(retryFailed?videoModel.value.resolution:requestedResolution(shot))+(shot.video_resume_available&&shot.video_task_id&&!retryFailed?' · 查询原任务，不重新提交':shot.video_not_submitted&&!retryFailed?' · 继续首次付费生成（尚未提交）':' · 新的付费生成')})
 const message=(retryFailed?'云端已确认上次任务失败。本操作会提交新的付费请求，服务商可能再次扣费。':'推荐先试生成 1～2 镜，确认效果后再生成其余镜头。')+'\n\n'+lines.join('\n')+'\n\n静音生成，仅上传本镜已选参考图；费用以你的服务商实际计费为准。是否继续？'
 if(!automated&&!confirm(message))return
 await run(async()=>{project.value=await call('/'+project.value.id+'/videos/generate','POST',{revision:project.value.revision,shot_ids:shots.map(shot=>shot.id),retry_failed:retryFailed});view.value='motions';videoLogsOpen.value=true})
}
async function regenerateVideo(shot){
 const audioNote=projectReferenceAudio.value&&shot.reference_audio_enabled!==false
  ?'本镜将继续注入对应 TTS 参考音频'+(shot.reference_audio_lipsync!==false?'并要求人物对口型。':'，但不要求人物对口型。')
  :'本镜不会注入参考音频。'
 if(!confirm('将保留当前核心分镜图、动态表达和视频提示词，使用新随机种子重新生成本镜。\n\n'+audioNote+'\n原片段会保留在历史记录中，但新片段生成后将替代它参与合成。是否继续？'))return
 await run(async()=>{project.value=await call('/'+project.value.id+'/videos/generate','POST',{revision:project.value.revision,shot_ids:[shot.id],regenerate_completed:true});view.value='motions';videoLogsOpen.value=true})
}
async function regenerateAllVideos(){
 const shots=dynamicShots.value
 if(!shots.length)return
 const completed=shots.filter(shot=>shot.video_status==='completed').length
 const remaining=shots.length-completed
 const detail=[completed?`已完成的 ${completed} 镜将归档旧片段并换新随机种子重跑。`:'',remaining?`其余 ${remaining} 镜将一并生成。`:''].filter(Boolean).join('\n')
 if(!confirm(`将重新生成全部 ${shots.length} 个动态镜头。\n\n${detail}\n静态镜头不受影响；本地工作流将按镜头依次运行，可能耗时较长。是否继续？`))return
 await run(async()=>{project.value=await call('/'+project.value.id+'/videos/generate','POST',{revision:project.value.revision,shot_ids:shots.map(shot=>shot.id),regenerate_completed:true});view.value='motions';videoLogsOpen.value=true})
}
async function stopVideos(){if(!confirm('停止后不再提交后续镜头。已经提交的云端任务可能继续执行及计费，停止不会取消扣费；可稍后继续查询原任务。确定停止？'))return;await run(async()=>{project.value=await call('/'+project.value.id+'/videos/stop','POST');videoLogsOpen.value=true})}
async function reopenShot(shot){
 if(!confirm('返回当前镜头的分镜编辑界面？现有图片和已付费动态片段会先保留；只有实际修改本镜内容后，才需要重新生成这一镜。'))return
 await run(async()=>{stopMotionPreviewAudio();project.value=await call('/'+project.value.id+'/shots/'+encodeURIComponent(shot.id)+'/reopen','POST',{revision:project.value.revision});view.value='storyboard';await nextTick();document.querySelector('.video-detail')?.scrollIntoView({behavior:'smooth',block:'start'})})
}
const input=ref({name:'动态视频草案',srt:'',style:'生动清晰的简笔画风格',characters:'',world:'',ratio:'16:9',dynamic_text_mode:'visual_first',scene_references_enabled:true})
const boundary=ref(1),boundaryEditor=ref({open:false,mode:'split'}),sourceAudio=ref(null)
const motionAudio=ref(null),previewNarration=ref(true)
let boundaryStopTimer=null
const redrawReferenceIds=ref([]),useCurrentReference=ref(false),useSceneReference=ref(true),redrawResolution=ref('')
const imagePromptDrafts=ref({}),submittedImagePrompts=ref({})
const sceneAssets=computed(()=>project.value?.scene_assets||[])
const selectedShot=computed(()=>project.value?.shots?.[selected.value]||null)
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
function previewSides(left,right){
 const side=rows=>({rows,text:rows.map(row=>row.text).join('\n'),start:Number(rows[0]?.start||0),end:Number(rows.at(-1)?.end||0)})
 const a=side(left),b=side(right)
 return {left:a,right:b,boundary:a.end,combinedDuration:Number((b.end-a.start).toFixed(3))}
}
function openBoundaryEditor(mode){stopBoundaryPreview();mode=mode||(selected.value<project.value.shots.length-1?'boundary':'split');boundaryEditor.value={open:true,mode};if(mode==='boundary')boundary.value=selectedShot.value.slide_ids.length;else if(mode==='split')boundary.value=Math.min(Math.max(1,Number(boundary.value)||1),Math.max(1,(selectedShot.value?.slide_ids?.length||1)-1))}
async function repairDesigns(){if(!confirm('保留当前字幕分组，仅更新受影响镜头的设计，会调用语言 API。继续？'))return;closeBoundaryEditor();await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/plan?repair_only=true','POST')})}
async function confirmDesign(){await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/design/confirm','POST',{revision:project.value.revision,shot_id:selectedShot.value.id})})}
async function undoStructure(){if(!confirm('撤回最近一次结构调整，恢复调整前的镜头与提示词；当前未保存修改会被放弃。继续？'))return;await run(async()=>{project.value=await call('/'+project.value.id+'/structure/undo','POST',{revision:project.value.revision});dirty.value=false;sessionStorage.removeItem(draftKey(project.value.id));selected.value=Math.min(selected.value,project.value.shots.length-1);closeBoundaryEditor()})}
function closeBoundaryEditor(){stopBoundaryPreview();boundaryEditor.value.open=false}
function stopBoundaryPreview(){
 if(boundaryStopTimer){clearTimeout(boundaryStopTimer);boundaryStopTimer=null}
 const audio=sourceAudio.value;if(audio){audio.pause();audio.ontimeupdate=null}
}
function stopMotionPreviewAudio(){const audio=motionAudio.value;if(audio)audio.pause()}
async function syncMotionPreview(event,phase){
 const video=event.currentTarget,audio=motionAudio.value,shot=selectedShot.value
 if(!audio||!shot||!previewNarration.value){if(audio)audio.pause();return}
 if(phase==='pause'||phase==='ended'){audio.pause();return}
 const offset=Math.max(0,Number(video.currentTime)||0)
 if(offset>=Number(shot.duration)-.025){audio.pause();if(!video.paused)video.pause();return}
 const target=Number(shot.start)+offset
 if(phase==='seeking'||Math.abs(audio.currentTime-target)>.22)audio.currentTime=target
 audio.playbackRate=video.playbackRate||1
 if(phase==='play'&&audio.paused){try{await audio.play()}catch{error.value='镜头可以播放，但浏览器阻止了配音同步。请再次点击播放，或检查浏览器声音权限。'}}
}
async function playBoundaryRange(start,end){
 const audio=sourceAudio.value;if(!audio||!Number.isFinite(Number(start))||!Number.isFinite(Number(end))||Number(end)<=Number(start)){error.value='当前任务没有可试听的配音。';return}
 stopBoundaryPreview();audio.currentTime=Math.max(0,Number(start));const finish=()=>{if(audio.currentTime>=Number(end)-.03)stopBoundaryPreview()};audio.ontimeupdate=finish
 boundaryStopTimer=setTimeout(stopBoundaryPreview,Math.max(500,(Number(end)-Number(start)+.3)*1000))
 try{await audio.play()}catch{stopBoundaryPreview();error.value='配音暂时无法播放，请检查音频文件或浏览器播放权限。'}
}
function playBoundaryPart(part){const preview=boundaryPreview.value;if(!preview)return;const side=preview[part];playBoundaryRange(side.start,side.end)}
function playBoundaryEdge(part){const preview=boundaryPreview.value;if(!preview)return;const point=preview.boundary;if(part==='left')playBoundaryRange(Math.max(preview.left.start,point-5),point);else if(part==='right')playBoundaryRange(point,Math.min(preview.right.end,point+5));else playBoundaryRange(Math.max(preview.left.start,point-3),Math.min(preview.right.end,point+3))}
async function applyBoundaryEdit(){const mode=boundaryEditor.value.mode;if(mode==='merge'&&!confirm('合并后，后一镜头的字幕和时长会并入当前镜头。是否继续？'))return;if(await structure(mode))closeBoundaryEditor()}
async function structure(action){if(action==='delete'&&!confirm('删除本镜头并把字幕分给相邻镜头？配音与字幕内容保留。'))return false;let completed=false;await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/structure','POST',{revision:project.value.revision,action,index:selected.value,boundary:Number(boundary.value)});selected.value=Math.min(selected.value,project.value.shots.length-1);completed=true});return completed}
async function review(){await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/review','POST',{revision:project.value.revision});project.value=await call('/'+project.value.id+'/images/generate','POST',{revision:project.value.revision})})}
async function generateImages(){await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/images/generate','POST',{revision:project.value.revision})})}
async function stopImages(){await run(async()=>{project.value=await call('/'+project.value.id+'/images/stop','POST')})}
async function confirmImages(regenerateShotId=''){await run(async()=>{if(dirty.value)await save();project.value=await call('/'+project.value.id+'/images/confirm','POST',{revision:project.value.revision,...(regenerateShotId?{regenerate_shot_id:regenerateShotId}:{})});view.value='motions';videoLogsOpen.value=true})}
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
const redrawReferenceGallery=computed(()=>[...projectRedrawReferences.value,...uploadedRedrawReferences.value])
const redrawReferenceUrl=asset=>base+'/'+project.value.id+'/redraw-references/'+encodeURIComponent(asset.id)+'?v='+project.value.revision
const imageEditRunning=computed(()=>sceneReferencesBusy.value||allImageAssets.value.some(asset=>asset.image_task?.status==='running'))
const imageAssetRunning=asset=>asset?.image_task?.status==='running'
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
async function redrawShot(shot){if(!shot.image_prompt?.trim()){error.value='请先填写图片提示词。';return}await run(async()=>{rememberImagePrompt(shot);const sceneAsset=isSceneAsset(shot),submittedPrompt=shot.image_prompt;const updated=await call('/'+project.value.id+'/images/'+encodeURIComponent(shot.id)+'/redraw','POST',{revision:project.value.revision,prompt:submittedPrompt,reference_ids:sceneAsset?[]:redrawReferenceIds.value,use_current_image:sceneAsset?false:useCurrentReference.value,use_scene_reference:sceneAsset?false:useSceneReference.value,image_resolution:redrawResolution.value||null});submittedImagePrompts.value[shot.id]=submittedPrompt;acceptImageUpdate(updated);dirty.value=false;sessionStorage.removeItem(draftKey(project.value.id))})}
async function replaceShotImage(event,shot){const file=event.target.files?.[0];event.target.value='';if(!file)return;await run(async()=>{const data=new FormData();data.append('revision',String(project.value.revision));data.append('prompt',shot.image_prompt||'');data.append('file',file);acceptImageUpdate(await requestJSON(base+'/'+project.value.id+'/images/'+encodeURIComponent(shot.id)+'/upload',{method:'POST',body:data}),[shot.id]);dirty.value=false;sessionStorage.removeItem(draftKey(project.value.id))})}
async function undoShot(shot){await run(async()=>{acceptImageUpdate(await call('/'+project.value.id+'/images/'+encodeURIComponent(shot.id)+'/undo','POST',{revision:project.value.revision}),[shot.id]);dirty.value=false})}
async function resetShotPrompt(shot){await run(async()=>{acceptImageUpdate(await call('/'+project.value.id+'/images/'+encodeURIComponent(shot.id)+'/reset-prompt','POST',{revision:project.value.revision}),[shot.id]);dirty.value=false})}
async function refreshShotPrompts(shot,basis){
 const label=basis==='action'?'根据当前动态表达，只更新本镜的核心图提示词和视频提示词':'根据当前核心图，只更新本镜的视频提示词'
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
 if(status==='video_generation_ready'&&!videoConfigured.value){error.value=localVideo.value?'一键制作已暂停：当前项目没有可用的 ComfyUI 工作流预设。':'一键制作已暂停：请先配置视频 API。';view.value='motions';return}
 if(status==='video_review'&&dynamicShots.value.some(shot=>shot.video_status!=='completed')){error.value='一键制作已暂停：部分动态镜头尚未完成，请在动态镜头页检查后继续。';view.value='motions';return}
 const key=`${record.id}:${record.revision}:${status}`
 if(autoAdvanceKey===key)return
 autoAdvanceKey=key;autoAdvanceBusy.value=true
 try{
  if(status==='draft'&&!record.shots.length){view.value='storyboard';await plan(true)}
  else if(status==='storyboard_review'&&record.shots.length){view.value='storyboard';await review()}
  else if(status==='generation_ready'){view.value='storyboard';await generateImages()}
  else if(status==='image_review'){view.value='storyboard';await confirmImages()}
  else if(status==='video_generation_ready'){view.value='motions';await generateVideos(dynamicShots.value,false,true)}
  else if(status==='video_review'&&allVideosReady.value){view.value='export';await exportVideo(true)}
 }finally{autoAdvanceBusy.value=false}
}
watch(()=>[project.value?.status,project.value?.revision,videoConfigured.value],()=>setTimeout(advanceAutoPilot,0))
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
 <template v-if="!project&&!busy">
 <div class="video-start">
  <section class="video-card"><h2>新建视频任务</h2><p class="muted">可借用已有作品的配音、字幕和时间戳。画面从头设计，不导入旧图、参考图、画风、人物或旧分镜。</p><label>配音与字幕来源<select v-model="sourceId" @change="chooseSource"><option value="">请选择已完成的项目</option><option v-for="source in sources" :key="source.id" :value="source.id">{{source.name}}</option></select></label><p v-if="!sources.length" class="muted">暂无可导入项目，需要项目已完成，且配音与最终字幕文件完整。</p><label>新项目名称<input v-model="importName" maxlength="100" placeholder="自动填写，可修改"></label><label>新视频比例<select v-model="newVisual.ratio"><option value="16:9">横屏 16:9</option><option value="9:16">竖屏 9:16</option></select></label>
   <DynamicTextModeSelector v-model="newVisual.dynamic_text_mode" />
   <label class="dynamic-scene-reference-toggle"><input type="checkbox" v-model="newVisual.scene_references_enabled">启用场景参考</label><p class="muted">两种表达方式均可使用；为重复场景额外生成参考图并计费，关闭后不生成。</p>
   <details open><summary>新任务画面设定（不继承来源项目）</summary><label>统一画风<textarea v-model="newVisual.style" rows="2"/></label><label>人物设定<textarea v-model="newVisual.characters" rows="2" placeholder="例如：主讲人是红色身体、戴红围巾的火柴人"/></label><label>世界与场景<textarea v-model="newVisual.world" rows="2"/></label></details><button class="primary-btn" :disabled="busy||!sourceId" @click="importProject">新建任务并导入配音字幕</button><p class="muted">独立保存，不影响原作品。直接从文案／音频开始的完整入口将在后续接入。</p></section>
  <section class="video-card"><h2>继续编辑</h2><p class="muted" v-if="!items.length">还没有视频分镜草案。</p><button class="video-record" v-for="item in items" :key="item.id" @click="open(item.id)"><strong>{{item.settings.name}}</strong><span>{{item.scenes.length}} 条字幕 · {{item.shots.length}} 个镜头</span></button></section>
 </div>
 <details class="video-srt-import"><summary>单独导入 SRT（测试入口，不包含配音）</summary>
 <div class="video-start">
  <section class="video-card"><h2>新建分镜草案</h2><label>项目名称<input v-model="input.name" maxlength="100"></label><label>视频比例<select v-model="input.ratio"><option value="16:9">横屏 16:9</option><option value="9:16">竖屏 9:16</option></select></label>
   <DynamicTextModeSelector v-model="input.dynamic_text_mode" />
   <label class="dynamic-scene-reference-toggle"><input type="checkbox" v-model="input.scene_references_enabled">启用场景参考</label><p class="muted">两种表达方式均可使用；为重复场景额外生成参考图并计费，关闭后不生成。</p>
   <label>导入已校对字幕<input type="file" accept=".srt" @change="loadSrt"></label><textarea v-model="input.srt" rows="4" placeholder="粘贴 SRT（保留时间戳），或选择字幕文件"/><details><summary>画风与人物设定</summary><label>统一画风<textarea v-model="input.style" rows="2"/></label><label>人物设定<textarea v-model="input.characters" rows="2"/></label><label>世界与场景<textarea v-model="input.world" rows="2"/></label></details><button class="primary-btn" :disabled="busy||!input.srt" @click="create">创建草案</button></section>
 </div>
 </details>
 </template>
 <section v-if="project&&view==='audio'" class="video-card video-history-panel">
  <button v-if="project.source_project?.id" :disabled="busy||videoRunning||exportRunning" @click="emit('edit-audio',project)">编辑配音与字幕</button>
  <div class="history-heading"><div><p class="eyebrow">阶段 2 · 已完成</p><h2>配音与字幕</h2><p class="muted">这里播放的是本动态任务采用的配音快照；参数均来自创建任务时保存的记录。</p></div><span class="readonly-badge">只读</span></div>
  <audio v-if="project.audio" :key="project.id" controls preload="metadata" :src="base+'/'+project.id+'/audio'"/>
  <p v-else class="studio-notice">这个测试草案没有保存配音文件。</p>
  <div class="readonly-summary-grid"><label>配音执行方<input readonly :value="reviewRequest.tts_engine||'该任务未记录'"/></label><label>音色<input readonly :value="reviewRequest.cluster_voice_id||reviewRequest.qwen_tts_voice||reviewRequest.tts_voice_id||'该任务未记录'"/></label><label>情绪<input readonly :value="reviewRequest.tts_emotion||'参考原音频'"/></label><label>语速<input readonly :value="reviewRequest.tts_speed??'该任务未记录'"/></label></div>
  <details open><summary>最终字幕与时间戳 · 只读</summary><div class="subtitle-review-list"><p v-for="scene in project.scenes" :key="scene.slide_id"><time>{{scene.start.toFixed(1)}}～{{scene.end.toFixed(1)}}s</time><span>{{scene.text}}</span></p></div></details>
 </section>
 <section v-if="project&&view==='motions'" class="video-card motion-workspace">
  <header class="motion-heading"><div><p class="eyebrow">阶段 4 · 动态镜头</p><h2>{{project.settings.name}}</h2><p class="muted">已完成 {{completedVideos}} / {{dynamicShots.length}} 个动态镜头 · {{project.shots.length-dynamicShots.length}} 个静态画面</p></div><button v-if="videoStageReady" type="button" class="ghost-btn" @click="view='storyboard'">查看已确认分镜</button></header>
  <p v-if="!videoStageReady" class="studio-notice">请先在“动态分镜”阶段完成并确认核心分镜图。此处不会自动提交视频生成。</p>
  <template v-else>
   <audio v-if="project.audio" ref="motionAudio" :key="project.id+':motion-audio'" preload="auto" :src="base+'/'+project.id+'/audio'" class="motion-narration-audio"/>
   <div class="motion-api-summary"><strong>{{localVideo?'本地 ComfyUI':'视频接口'}}</strong><span v-if="localVideo">{{project.creation_parameters.comfyui_reference_audio?'已开启本镜 TTS 参考音频':'未开启参考音频'}}</span><span v-else-if="videoConfigured">已配置 · {{videoModel.key_count||1}} 个 Key · {{videoModel.effective_concurrency||1}} 路并发 · {{videoModel.resolution}}</span><span v-else>未配置，请前往“接口与服务”填写</span><button type="button" :disabled="busy||videoRunning" @click="openClassicEditor">修改项目配置</button></div>
   <p v-if="videoModelError" class="studio-notice error">{{videoModelError}}</p>
   <div class="motion-generation-bar">
    <div><strong>{{videoRunning?(project.status==='video_stopping'?'正在安全停止…':'正在生成动态镜头…'):completedVideos===dynamicShots.length?'动态片段已准备完成':'先试一镜，再决定是否批量生成'}}</strong><p class="muted">{{videoRunning?'按照“接口与服务”中的并发设置处理，实时进度见日志。停止不会撤销云端已提交或已计费的任务。':'只有点击生成并确认后才调用视频 API；已完成片段不会重复提交，失败镜头不会自动重新扣费。'}}</p></div>
    <button v-if="videoRunning" type="button" class="primary-btn" :disabled="busy||project.status==='video_stopping'" @click="stopVideos">{{project.status==='video_stopping'?'正在停止…':'停止生成'}}</button>
   <div v-else class="motion-batch-actions"><button type="button" class="primary-btn" :disabled="busy||videoRunning||!videoConfigured||!dynamicShots.length" @click="regenerateAllVideos">全部重新生成（{{dynamicShots.length}}）</button><button type="button" class="primary-btn" :disabled="busy||!videoConfigured||!pendingVideos.length" @click="generateVideos(pendingVideos)">生成全部未完成（{{pendingVideos.length}}）</button></div>
   </div>
   <div v-if="allVideosReady&&!videoRunning" class="video-next-stage"><div><strong>全部动态片段已完成</strong><p>静态镜头和配音字幕也已就绪，可以进入最终合成。</p></div><button type="button" class="primary-btn" @click="view='export'">下一步：合成与导出</button></div>
   <p v-if="dynamicShots.some(shot=>shot.video_status!=='completed'&&(shot.video_terminal||shot.video_status==='failed'||(shot.video_status==='unknown'&&!shot.video_resume_available)))" class="duration-repair-note needs-review">批量处理会跳过失败或状态待核实的镜头，请在左侧选择相应镜头查看原因。确认失败的任务需单独点击“重新付费生成”。</p>
   <p v-if="project.error" class="studio-notice error">{{project.error}}</p>
   <details v-if="project.logs?.length" class="planning-log" :open="videoLogsOpen||videoRunning" @toggle="videoLogsOpen=$event.target.open"><summary>任务日志 <span class="muted">{{project.logs.length}} 条</span></summary><div class="video-logs" role="log" aria-live="polite"><div v-for="(line,index) in project.logs" :key="index">{{line}}</div></div></details>
   <div v-if="project.shots.length" class="video-editor motion-editor">
    <VideoShotNavigator :shots="project.shots" :selected="selected" :image-url="imageUrl" motion :status-label="videoStatusLabel" @select="selected=$event" />
    <article v-if="selectedShot" :key="selectedShot.id" class="video-detail motion-detail">
     <div class="shot-local-navigation"><strong>镜头 {{String(selected+1).padStart(2,'0')}} <span>/ {{project.shots.length}}</span></strong><div><button :disabled="selected===0" @click="moveShot(-1)" aria-label="上一个镜头">← 上一镜</button><button :disabled="selected===project.shots.length-1" @click="moveShot(1)" aria-label="下一个镜头">下一镜 →</button></div></div>
     <details open class="shot-subtitles"><summary>对应字幕（只读）</summary><p v-for="id in selectedShot.slide_ids" :key="id">{{scenesById[id]?.text}}</p></details>
     <div class="motion-shot-heading"><div><h3>第 {{String(selected+1).padStart(2,'0')}} 镜 · {{videoStatusLabel(selectedShot)}}</h3><p class="muted">{{selectedShot.intent}}</p></div><div class="motion-heading-actions"><span v-if="selectedShot.kind==='video'" class="readonly-badge">使用 {{selectedShot.duration}} 秒 · 请求 {{requestedSeconds(selectedShot)}} 秒 · {{requestedResolution(selectedShot)}}</span><label v-if="selectedShot.kind==='video'" class="motion-upload-button" :class="{disabled:busy||videoRunning}">上传本地视频替换<input type="file" accept="video/mp4,.mp4" :disabled="busy||videoRunning" @change="uploadReplacementVideo(selectedShot,$event)"></label><button v-if="selectedShot.kind==='video'&&selectedShot.video_status==='completed'" type="button" :disabled="busy||videoRunning||!videoConfigured" @click="regenerateVideo(selectedShot)">重新生成本镜</button><button type="button" :disabled="busy||videoRunning" @click="reopenShot(selectedShot)">重新编辑本镜</button></div></div>
     <div v-if="selectedShot.kind==='video'&&selectedShot.video_status==='completed'" class="motion-preview">
      <video :key="selectedShot.id+':'+selectedShot.video_version" controls muted playsinline preload="metadata" :src="videoUrl(selectedShot)" :poster="imageUrl(selectedShot)" @play="syncMotionPreview($event,'play')" @pause="syncMotionPreview($event,'pause')" @ended="syncMotionPreview($event,'ended')" @seeking="syncMotionPreview($event,'seeking')" @timeupdate="syncMotionPreview($event,'timeupdate')" @ratechange="syncMotionPreview($event,'seeking')"></video>
      <label v-if="project.audio" class="motion-audio-toggle"><input type="checkbox" v-model="previewNarration">同步播放本镜配音</label>
      <p v-else class="muted">该任务没有配音快照，只能预览画面。</p>
      <a :href="videoUrl(selectedShot)" :download="'镜头'+String(selected+1).padStart(2,'0')+'.mp4'" class="motion-download">↓ 下载本镜原片</a>
     </div>
     <div v-else-if="selectedShot.kind==='video'&&['running','unknown','stopped'].includes(selectedShot.video_status)" class="motion-progress" role="status"><strong>{{videoStatusLabel(selectedShot)}}</strong><p>{{selectedShot.video_status==='running'?'正在提交、查询或下载云端片段，进度见任务日志。':'已保留本地记录，请根据下方提示继续处理。'}}</p></div>
     <p v-if="selectedShot.video_error" class="studio-notice error">{{selectedShot.video_error}}</p>
     <p v-if="selectedShot.video_task_id" class="motion-task-id muted">云端任务编号：<code>{{selectedShot.video_task_id}}</code><span v-if="selectedShot.video_attempt"> · 第 {{selectedShot.video_attempt}} 次请求</span></p>
     <p v-if="selectedShot.kind==='video'&&selectedShot.video_status==='unknown'&&!selectedShot.video_resume_available" class="duration-repair-note needs-review">本次提交结果未知，且无法安全查询原任务。请先向服务商核实，不要重新提交，避免重复扣费。</p>
     <div v-if="selectedShot.kind==='video'&&(!videoRunning||(localVideo&&project.status==='video_generating'&&![ 'running','unknown'].includes(selectedShot.video_status)))&&selectedShot.video_status!=='completed'" class="motion-shot-actions">
      <button v-if="selectedShot.video_terminal" type="button" :disabled="busy||!videoConfigured" @click="generateVideos([selectedShot],true)">重新付费生成本镜</button>
      <button v-else-if="selectedShot.video_resume_available" type="button" class="primary-btn" :disabled="busy||!videoConfigured" @click="generateVideos([selectedShot])">{{selectedShot.video_not_submitted?'继续生成（尚未提交）':'继续查询原任务'}}</button>
      <button v-else-if="canProcessVideo(selectedShot)" type="button" class="primary-btn" :disabled="busy||!videoConfigured" @click="generateVideos([selectedShot])">{{localVideo&&videoRunning?'加入本地生成队列':'试生成当前镜头'}}</button>
     </div>
     <p v-if="selectedShot.kind==='video'" class="motion-preview-note muted">原始视频音轨保持静音；试看时可同步本镜对应的项目配音。为覆盖字幕时长，请求秒数可能向上取整，试看和最终合成都只使用本镜字幕时长。</p>
     <p v-else class="studio-notice">这是静态镜头，保留已确认的图片，不提交视频 API，也不产生视频生成费用。</p>
     <details v-if="selectedShot.kind==='video'&&selectedVideoHistory.length" class="motion-history"><summary>历史生成结果 · {{selectedVideoHistory.length}} 个</summary><p class="muted">重生成和本地替换都不会覆盖旧片段。可先预览，再决定采用或删除。</p><div class="motion-history-list"><div v-for="(row,position) in selectedVideoHistory" :key="row.index+':'+(row.entry.video_version||row.entry.video)" class="motion-history-row"><span><b>{{historyVersionLabel(row,position)}}</b><small>{{row.entry.invalidated_reason||'此前生成的动态片段'}}</small></span><div class="actions"><button type="button" @click="historyPreviewIndex=historyPreviewIndex===row.index?null:row.index">{{historyPreviewIndex===row.index?'收起':'预览'}}</button><button type="button" class="primary-btn" :disabled="busy||videoRunning" @click="adoptHistoryVersion(selectedShot,row.index)">采用</button><button type="button" :disabled="busy||videoRunning" @click="deleteHistoryVersion(selectedShot,row.index)">删除</button></div><video v-if="historyPreviewIndex===row.index" controls muted playsinline preload="metadata" :src="historyVideoUrl(selectedShot,row.index)"></video></div></div></details>
     <details class="motion-core-reference" :open="selectedShot.video_status!=='completed'"><summary>已确认的核心分镜图 · 图1</summary><div class="storyboard-result"><img :src="imageUrl(selectedShot)" :alt="'第 '+(selected+1)+' 镜核心分镜图'"></div></details>
     <details v-if="selectedShot.kind==='video'" class="motion-request-review"><summary>{{selectedShot.video_request?'本次实际提交内容（只读）':'将提交的视频提示词（只读）'}}</summary><p class="motion-final-prompt">{{selectedShot.video_request?.prompt||selectedShot.video_prompt}}</p><p class="muted">核心图为图1，其余参考图只使用本镜已选素材，不会把全局素材全部传入。视频生成不上传旁白音频。</p><p v-if="selectedShot.video_request?.reference_files?.length" class="muted">本次参考图：{{selectedShot.video_request.reference_files.length}} 张</p></details>
    </article>
   </div>
  </template>
 </section>
 <section v-if="project&&view==='export'" class="video-card video-history-panel">
  <div class="history-heading"><div><p class="eyebrow">阶段 5</p><h2>合成与导出</h2><p class="muted">动态片段按字幕时长裁切，静态镜头自动补帧；旁白和字幕始终沿用本任务确认后的时间轴。</p></div><span class="readonly-badge">{{project.status==='completed'?'已完成':exportRunning?'正在合成':project.status==='export_failed'?'需要重试':'等待合成'}}</span></div>
  <p v-if="project.error" class="studio-notice error">{{project.error}}</p>
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
  <div v-if="project.source_project" class="video-source-summary"><p class="muted">配音字幕来源：{{project.source_project.name}} · {{project.import_scope==='audio_subtitles'?'仅导入配音、字幕和时间戳，画面重新规划':'旧版导入草案（保留原设置）；如需全新画面设计，请重新创建任务'}}</p><audio v-if="project.audio" ref="sourceAudio" :key="project.id" controls preload="metadata" :src="base+'/'+project.id+'/audio'"/></div>
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
  <details v-if="project.logs.length" class="planning-log" :open="autoMotionBusy||['planning','stopping','image_generating','image_stopping'].includes(project.status)"><summary>任务日志 <span class="muted">{{project.logs.length}} 条</span></summary><div class="video-logs" role="log" aria-live="polite"><div v-for="(line,i) in project.logs" :key="i">{{line}}</div></div></details>
  <details v-if="sceneReferencesEnabled||sceneAssets.length" class="scene-reference-assets" :open="sceneReferencesBusy||project.scene_references_status==='failed'||(project.status==='image_review'&&sceneReferencesBlockConfirm)">
   <summary><strong>场景参考资产</strong><span class="muted">{{sceneAssets.length}} 张 · {{sceneReferencesBusy?'正在准备场景参考':project.scene_references_status==='failed'?'需要重试':sceneReferencesBlockConfirm?'场景参考尚未准备完成':'仅用于相关镜头，不占用视频时间轴'}}</span></summary>
   <div class="scene-reference-heading">
    <div><p class="muted">先固定场景中的空间布局、家具和道具，再将场景图提供给相关分镜。修改场景后，已有核心图需逐张重绘才会采用新版。</p><p v-if="sceneReferencesBusy" class="image-edit-status running" role="status">{{project.scene_references_status==='planning'?'正在规划需要共用的场景…':'正在生成场景参考图…'}}</p><p v-else-if="sceneReferenceMessage" class="image-edit-status" :class="project.scene_references_status==='failed'?'failed':''">{{sceneReferenceMessage}}</p><p v-else-if="!sceneAssets.length" class="muted">{{project.scene_references_status==='completed'?'本次规划没有需要共用的场景。':project.status==='image_review'?'这个任务尚未生成场景参考。可手动补充，完成后选择相关核心图重绘。':'已启用场景参考，生成核心分镜图时会先规划和生成场景。'}}</p></div>
    <button v-if="sceneReferencesEnabled&&project.status==='image_review'&&sceneReferencesNeedGeneration" type="button" :disabled="busy||imageEditRunning" @click="generateSceneReferences">{{project.scene_references_status==='failed'||sceneAssets.length?'重试场景参考（调用 API）':'补充场景参考（调用 API）'}}</button>
   </div>
   <p v-if="project.status==='image_review'&&sceneReferencesBlockConfirm" class="studio-notice">本任务已启用场景参考，场景准备尚未完成，暂不能确认进入下一阶段。请先补充或重试场景参考；完成后可按需重绘关联核心图。</p>
   <p v-if="sceneReferencesEnabled&&project.status==='image_review'&&sceneReferencesNeedGeneration" class="muted scene-cost-note">将调用语言与图像 API；已有核心分镜图保留，完成后可按需重绘相关镜头。</p>
   <div class="scene-assets-grid">
    <details v-for="asset in sceneAssets" :key="asset.id" class="scene-asset-card">
     <summary><img v-if="asset.image_status==='completed'" :src="imageUrl(asset)" :alt="asset.name||'场景参考'"><span v-else class="scene-thumbnail-placeholder">{{asset.image_status==='failed'?'未完成':'场景'}}</span><span><strong>{{asset.name||asset.id}}</strong><small class="muted">用于镜头 {{sceneUsedByLabel(asset)}}</small></span><span class="scene-asset-state">{{asset.image_task?.status==='running'?'正在重绘':asset.image_status==='completed'?'已生成':asset.image_status==='failed'?'生成失败':'等待生成'}}</span></summary>
     <div v-if="asset.image_status==='completed'" class="storyboard-result"><img :src="imageUrl(asset)" :alt="asset.name||'场景参考'"></div>
     <p v-if="asset.image_error" class="image-edit-status failed">{{asset.image_error}}</p>
     <label>场景参考图提示词<textarea v-model="asset.image_prompt" rows="4" :disabled="busy||imageEditRunning||project.status!=='image_review'" @input="rememberImagePrompt(asset)"/></label>
     <p v-if="asset.image_task?.message" class="image-edit-status" :class="asset.image_task.status">{{asset.image_task.message}}</p>
     <div v-if="project.status==='image_review'&&asset.image_status==='completed'" class="storyboard-edit-actions">
      <button class="primary-btn" type="button" :disabled="busy||imageEditRunning||!asset.image_prompt?.trim()" @click="redrawShot(asset)">重绘场景图</button>
      <label class="file-action">替换本地图片<input type="file" accept="image/jpeg,image/png,image/webp" :disabled="busy||imageEditRunning" @change="replaceShotImage($event,asset)"></label>
      <button type="button" :disabled="busy||imageEditRunning||!asset.image_history?.length" @click="undoShot(asset)">撤回上一版</button>
      <button type="button" :disabled="busy||imageEditRunning||!asset.baseline_image_prompt" @click="resetShotPrompt(asset)">恢复初始提示词</button>
     </div>
    </details>
   </div>
  </details>
  <div class="video-editor" v-if="project.shots.length"><VideoShotNavigator :shots="project.shots" :selected="selected" :image-url="imageUrl" :has-warning="shotHasPromptWarning" @select="selected=$event;boundary=1;closeBoundaryEditor()" />
   <div v-for="(shot,i) in project.shots" :key="shot.id" v-show="i===selected" class="video-detail">
    <div class="shot-local-navigation"><strong>镜头 {{String(i+1).padStart(2,'0')}} <span>/ {{project.shots.length}}</span></strong><div><button :disabled="selected===0" @click="moveShot(-1)" aria-label="上一个镜头">← 上一镜</button><button :disabled="selected===project.shots.length-1" @click="moveShot(1)" aria-label="下一个镜头">下一镜 →</button></div></div>
    <p v-if="storyboardLocked" class="readonly-stage-note">{{project.status==='image_review'?'字幕分组与时长已经确认；你仍可切换动静态、修改本镜提示词、重绘或替换图片。切换类型自动保存，不会重新生成图片。':'本阶段已经确认，以下内容仅供回顾，不会提供修改入口。'}}</p>
    <fieldset :disabled="busy||['planning','stopping'].includes(project.status)" @input="!storyboardLocked&&(dirty=true)">
     <details open class="shot-subtitles"><summary>对应字幕（只读）</summary><p v-for="id in shot.slide_ids" :key="id">{{project.scenes.find(s=>s.slide_id===id)?.text}}</p></details>
     <div v-if="shot.image_status" class="storyboard-result" :class="shot.image_status"><img v-if="shot.image_status==='completed'" :src="imageUrl(shot)" :alt="'核心分镜图 '+(i+1)"><div v-else><strong>{{shot.image_status==='running'?'正在生成核心分镜图':shot.image_status==='failed'?'生成失败':'等待生成'}}</strong><p v-if="shot.image_error">{{shot.image_error}}</p></div></div>
     <div v-if="sceneAssetFor(shot)" class="shot-scene-reference">
      <img v-if="sceneAssetFor(shot).image_status==='completed'" :src="imageUrl(sceneAssetFor(shot))" :alt="sceneAssetFor(shot).name||'场景参考'">
      <div><strong>场景参考 · {{sceneAssetFor(shot).name||sceneAssetFor(shot).id}}</strong><p class="muted">{{sceneUsageLabel(shot)}}</p><label v-if="project.status==='image_review'" class="scene-reference-toggle"><input type="checkbox" v-model="useSceneReference" :disabled="imageAssetRunning(shot)||sceneAssetFor(shot).image_status!=='completed'">重绘时使用场景参考</label><p v-if="sceneAssetFor(shot).image_status!=='completed'" class="muted">场景图尚未完成，可在上方场景参考资产中查看或重试。</p></div>
     </div>
     <label>这一镜头想表达什么<input v-model="shot.intent" :disabled="storyboardLocked"></label>
     <div class="video-row"><label>画面类型<select :value="shot.kind" @change="changeShotKind(shot,$event)" :disabled="imageAssetRunning(shot)||(storyboardLocked&&project.status!=='image_review')"><option value="static">静态画面</option><option value="video" :disabled="shot.duration>15">动态视频</option></select></label><div v-if="shot.kind==='video'&&projectReferenceAudio" class="shot-audio-settings"><span>参考音频</span><label><input type="checkbox" :checked="shot.reference_audio_enabled!==false" :disabled="imageAssetRunning(shot)||(storyboardLocked&&project.status!=='image_review')" @change="shot.reference_audio_enabled=$event.target.checked;rememberMotionDraft(shot)">本镜启用</label><label :class="{disabled:shot.reference_audio_enabled===false}"><input type="checkbox" :checked="shot.reference_audio_lipsync!==false" :disabled="shot.reference_audio_enabled===false||imageAssetRunning(shot)||(storyboardLocked&&project.status!=='image_review')" @change="shot.reference_audio_lipsync=$event.target.checked;rememberMotionDraft(shot)">人物对口型</label></div><p class="muted">使用 {{shot.duration}} 秒<span v-if="shot.kind==='video'"> · 请求 {{Math.max(4,Math.ceil(shot.duration))}} 秒</span></p></div>
     <p v-if="shot.duration_repair?.note" class="duration-repair-note" :class="{'needs-review':['boundary_fallback','single_subtitle_static'].includes(shot.duration_repair.method)}">{{shot.duration_repair.note}}</p>
     <p v-if="shot.warning" class="studio-notice">{{shot.warning}}</p>
     <label v-if="shot.kind==='video'" class="motion-field">动态表达<textarea v-model="shot.action" rows="3" @input="rememberMotionDraft(shot)" :disabled="imageAssetRunning(shot)||(storyboardLocked&&project.status!=='image_review')"/></label>
     <label class="image-prompt-field">核心分镜图提示词<textarea v-model="shot.image_prompt" rows="4" :disabled="imageAssetRunning(shot)||(storyboardLocked&&project.status!=='image_review')" @input="project.status==='image_review'&&rememberImagePrompt(shot)"/></label>
     <label v-if="shot.kind==='video'" class="video-prompt-field">视频模型最终提示词<textarea v-model="shot.video_prompt" rows="5" @input="rememberMotionDraft(shot)" :disabled="imageAssetRunning(shot)||(storyboardLocked&&project.status!=='image_review')"/></label>
     <p v-if="autoMotionBusy" role="status" class="muted">正在调用语言模型，为本镜自动补写动态表达…不会生成图片或视频。</p>
     <button v-if="project.status==='image_review'&&shot.kind==='video'&&!shot.action?.trim()" type="button" :disabled="busy||imageEditRunning||autoMotionBusy" @click="run(()=>autoFillMotion(shot))">自动补写动态表达</button>
     <button v-if="project.status==='image_review'&&Object.keys(motionDrafts).length" type="button" :disabled="busy||imageEditRunning" @click="run(save)">保存动态表达与视频提示词</button>
     <p v-if="project.status==='image_review'&&shot.kind==='video'&&!shot.video_prompt?.trim()" class="muted">本镜尚未填写视频提示词。可直接按核心图生成，或先填写动态表达，再更新两个提示词；不会自动重绘图片。</p>
     <p v-if="shot.prompt_refresh_note" class="image-edit-status completed">{{shot.prompt_refresh_note}}</p>
     <p v-if="shot.image_prompt_out_of_sync" class="duration-repair-note needs-review">核心图提示词已经更新，但当前图片还是上一版。请按新提示词重绘，或改用“按当前核心图更新视频提示词”。</p>
    </fieldset>
    <section v-if="project.status==='image_review'&&project.reedit_shot_id===shot.id" class="shot-reedit-next video-next-stage" role="status">
     <div><strong>正在返修第 {{String(i+1).padStart(2,'0')}} 镜</strong><p>修改和重绘完成后，从这里返回同一镜头的动态生成页。若本镜内容发生变化，只会要求重新生成这一镜；点击这里不会自动调用视频 API 或扣费。</p></div>
     <button type="button" class="primary-btn" :disabled="busy||imageEditRunning||sceneReferencesBlockConfirm||project.shots.some(s=>s.image_status!=='completed')||(shot.kind==='video'&&!shot.video_prompt?.trim())" @click="finishShotReedit(shot)">{{shot.kind==='video'?'完成本镜返修，进入动态重生成':'完成本镜返修，返回动态镜头页'}}</button>
    </section>
    <section v-if="shot.kind==='video'&&['storyboard_review','image_review'].includes(project.status)" class="single-shot-refresh">
     <div><strong>仅重新规划本镜提示词</strong><p class="muted">以你当前编辑的内容为准，不改变字幕分组、时长或其他镜头。</p></div>
     <button type="button" :disabled="busy||imageEditRunning||!shot.action?.trim()" @click="refreshShotPrompts(shot,'action')">按动态表达更新两个提示词</button>
     <button type="button" :disabled="busy||imageEditRunning||!shot.image_prompt?.trim()" @click="refreshShotPrompts(shot,'image')">按核心图更新视频提示词</button>
     <p v-if="shot.image_origin==='reference_redraw'||shot.image_origin==='upload'" class="muted">当前图片来自{{shot.image_origin==='upload'?'本地替换':'参考模式重绘'}}；第二个按钮会先识别最终图片，再更新视频提示词。</p>
    </section>
    <section v-if="project.status==='image_review'&&shot.image_status==='completed'" class="storyboard-edit-tools">
     <div class="storyboard-edit-heading"><div><strong>修正这张核心分镜图</strong><p class="muted">提示词修改只在点击重绘时提交；参考图最多 3 张，编号按下方选中顺序传给图像模型。</p><p v-if="!useCurrentReference&&!redrawReferenceIds.length" class="muted">当前没有选择参考图，本次只按提示词重绘。</p><p v-if="sceneAssetFor(shot)&&useSceneReference" class="muted">场景图自动附在这些参考素材之后，只约束空间布局。</p></div><select v-model="redrawResolution" aria-label="重绘分辨率"><option value="">跟随任务分辨率</option><option value="1k">1K</option><option value="2k">2K</option><option value="4k">4K</option></select></div>
     <div class="storyboard-reference-library">
      <div class="reference-library-heading"><div><strong>本次重绘参考图</strong><p class="muted">点击图片选择或取消；绿色卡片会按图号顺序传给图像模型。</p></div><div><label class="file-action">＋ 上传新参考图<input type="file" accept="image/jpeg,image/png,image/webp" multiple :disabled="busy||imageEditRunning" @change="uploadRedrawReferences"></label><button v-if="uploadedRedrawReferences.length" type="button" :disabled="busy||imageEditRunning" @click="clearUploadedRedrawReferences">清空新增图</button></div></div>
      <div class="storyboard-reference-grid">
       <article class="reference-image-card" :class="{active:useCurrentReference}">
        <button type="button" :disabled="busy||imageAssetRunning(shot)" @click="toggleCurrentReference"><img :src="imageUrl(shot)" alt="当前核心分镜图"><span><b>当前核心图</b><small>重绘前的本图</small></span><em>{{useCurrentReference?'图1 · 正在使用':'未选择'}}</em></button>
       </article>
       <article v-for="asset in redrawReferenceGallery" :key="asset.origin+':'+asset.id" class="reference-image-card" :class="{active:redrawReferenceIds.includes(asset.id)}">
        <button type="button" :disabled="busy||imageAssetRunning(shot)" @click="toggleRedrawReference(asset.id)"><img :src="redrawReferenceUrl(asset)" :alt="asset.displayName"><span><b>{{asset.displayName}}</b><small>{{asset.origin==='project'?'创建任务时上传':'本页后来上传'}}</small></span><em>{{redrawReferenceIds.includes(asset.id)?'图'+redrawReferenceNumber(asset.id)+' · 正在使用':'未选择'}}</em></button>
        <button v-if="asset.origin==='uploaded'" type="button" class="reference-card-delete" :disabled="busy||imageEditRunning" title="删除这张新增参考图" @click.stop="deleteRedrawReference(asset)">删除</button>
       </article>
       <p v-if="!redrawReferenceGallery.length" class="reference-library-empty">创建任务时没有上传参考图；可在右上角补充新图片。</p>
      </div>
     </div>
     <p v-if="shot.image_task?.message" class="image-edit-status" :class="shot.image_task.status">{{shot.image_task.message}}</p>
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
    <section v-if="!storyboardLocked&&!['planning','stopping'].includes(project.status)&&boundaryEditor.open&&boundaryPreview" class="video-boundary-editor">
     <header><div><strong>调整分镜</strong><p class="muted">只调整画面覆盖范围，配音不变。原设计保留，支持撤回。</p></div><button type="button" @click="closeBoundaryEditor">关闭</button></header>
     <div class="boundary-modes" role="group" aria-label="选择分镜调整方式">
      <button type="button" :aria-pressed="boundaryEditor.mode==='boundary'" :class="{active:boundaryEditor.mode==='boundary'}" :disabled="busy||i===project.shots.length-1" @click="openBoundaryEditor('boundary')">移动边界</button>
      <button type="button" :aria-pressed="boundaryEditor.mode==='split'" :class="{active:boundaryEditor.mode==='split'}" :disabled="busy||shot.slide_ids.length<2" @click="openBoundaryEditor('split')">拆成两镜</button>
      <button type="button" :aria-pressed="boundaryEditor.mode==='merge'" :class="{active:boundaryEditor.mode==='merge'}" :disabled="busy||i===project.shots.length-1" @click="openBoundaryEditor('merge')">合为一镜</button>
     </div>
     <p class="boundary-mode-help muted">{{boundaryEditor.mode==='boundary'?'在当前镜头与后一个镜头之间移动字幕，镜头数量不变。':boundaryEditor.mode==='split'?'在当前镜头内选择字幕切口，一个镜头拆成两个。':'将当前镜头与后一个镜头合并，以下显示合并前的两段内容。'}}</p>
     <label v-if="boundaryEditor.mode!=='merge'" class="video-boundary-slider"><span>{{boundaryEditor.mode==='boundary'?'调整相邻镜头边界':'选择切口'}}：第 {{boundary}} 条字幕之后 · {{boundaryPreview.boundary.toFixed(2)}} 秒</span><input v-model.number="boundary" type="range" min="1" :max="shot.slide_ids.length+(boundaryEditor.mode==='boundary'?(project.shots[i+1]?.slide_ids.length||0):0)-1" step="1"></label>
     <div class="video-boundary-grid">
      <article><div><b>{{boundaryEditor.mode==='split'?'拆分后的前镜头':'当前镜头'}}</b><span>{{(boundaryPreview.left.end-boundaryPreview.left.start).toFixed(2)}} 秒</span></div><p v-for="row in boundaryPreview.left.rows" :key="row.slide_id">{{row.text}}</p><button type="button" @click="playBoundaryPart('left')">▶ 试听整段</button></article>
      <article><div><b>{{boundaryEditor.mode==='split'?'拆分后的后镜头':'后一镜头'}}</b><span>{{(boundaryPreview.right.end-boundaryPreview.right.start).toFixed(2)}} 秒</span></div><p v-for="row in boundaryPreview.right.rows" :key="row.slide_id">{{row.text}}</p><button type="button" @click="playBoundaryPart('right')">▶ 试听整段</button></article>
     </div>
     <div class="video-boundary-listen"><button type="button" @click="playBoundaryEdge('left')">▶ 试听前段末尾</button><button type="button" @click="playBoundaryEdge('right')">▶ 试听后段开头</button><button type="button" @click="playBoundaryEdge('continuous')">▶ 连续试听交界</button><span>共同边界 {{boundaryPreview.boundary.toFixed(2)}} 秒</span></div>
     <p v-if="boundaryEditor.mode==='merge'&&boundaryPreview.combinedDuration>15" class="duration-repair-note needs-review">合并后共 {{boundaryPreview.combinedDuration.toFixed(2)}} 秒；若设为动态视频会超过 15 秒，请确认是否确实需要合并。</p>
     <footer><button type="button" @click="closeBoundaryEditor">取消</button><button class="primary-btn" type="button" :disabled="busy" @click="applyBoundaryEdit">{{boundaryEditor.mode==='boundary'?'确认调整边界':boundaryEditor.mode==='split'?'确认拆成两个镜头':'确认合并镜头'}}</button></footer>
    </section>
    <div v-if="!storyboardLocked&&!['image_generating','image_stopping'].includes(project.status)" class="video-actions"><button class="primary-btn" :disabled="busy||!dirty||['planning','stopping'].includes(project.status)" @click="run(save)">保存镜头修改</button><button :disabled="busy||(shot.slide_ids.length<2&&i===project.shots.length-1)||['planning','stopping'].includes(project.status)" @click="openBoundaryEditor()">调整分镜…</button><button v-if="project.structure_history?.length" :disabled="busy||['planning','stopping'].includes(project.status)" @click="undoStructure">撤回结构调整</button><button :disabled="busy||shot.slide_ids.length<2||['planning','stopping'].includes(project.status)" @click="structure('insert')">新增空白镜头</button><button :disabled="busy||project.shots.length<2||['planning','stopping'].includes(project.status)" @click="structure('delete')">删除镜头</button></div>
   </div>
  </div>
 </section>
</section>
</template>
<style scoped>
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
