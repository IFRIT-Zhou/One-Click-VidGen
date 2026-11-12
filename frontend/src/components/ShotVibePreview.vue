<script setup>
import { computed, ref, nextTick, onUnmounted, watch } from 'vue'
import { requestJSON } from '../api'
import ShotcraftTemplatePicker from './ShotcraftTemplatePicker.vue'
import { MAX_PLAN_BYTES, parsePreviewPlan, previewFileUrl, previewRoute, vibeBrief, layeredRequestOptions, layeredStrategyDescription, layeredTaskStrategy, freshCloudStartNotice, stepContinuationBudget, stepContinuationNotice, vibeCloudSubmissionError } from '../vibePreview'

const props = defineProps({projectId:String, shotId:String, imageSrc:String, number:Number, revision:Number, duration:Number, active:{type:Boolean,default:true}, unsaved:Boolean, editLocked:Boolean, designLocked:Boolean, adopted:String, recommendation:Object,ready:{type:Boolean,default:true},reserved:Boolean,reserveLocked:Boolean})
const emit=defineEmits(['update'])
const panel=ref(null), engine=ref('local'), pace=ref('normal'), instruction=ref(''), design=ref(null)
const transition=ref('layered'), transitionSeconds=ref(.8)
watch(()=>props.shotId,()=>{transition.value='layered';transitionSeconds.value=.8})
const receipt=ref(null), stale=ref(false), busy=ref(''), error=ref(''), hint=ref(''), manualCopy=ref(false)
const file=ref(null), plan=ref(null), planName=ref(''), opened=ref(false)
const cloudTask=ref(null),cloudConfig=ref(null),cloudReadError=ref('')
const templateIds=ref([]),templateMeta=ref({catalog:[],items:[],valid:true,issue:''})
let selectionTouched=false,engineTouched=false
const templateSelectionInvalid=computed(()=>templateIds.value.length>0&&!templateMeta.value.valid)
function selectTemplates(ids){templateIds.value=[...ids];selectionTouched=true}
const modelCallBudget=ref(30),imageRequestBudget=ref(2)
const continuationCalls=ref(10)
let continuationRequest=null
const continueStepsImages=ref(false),stepsContinuationRequest=ref(null)
const stepsBudget=computed(()=>stepContinuationBudget(cloudTask.value))
const stepsAdditionalCalls=ref(0)
const stepsContinuationInvalid=computed(()=>!stepsBudget.value.valid||!Number.isInteger(stepsAdditionalCalls.value)||stepsAdditionalCalls.value<0||stepsBudget.value.modelLimit+stepsAdditionalCalls.value>50||stepsBudget.value.modelRemaining+stepsAdditionalCalls.value<1)
watch(()=>cloudTask.value?.task_id,()=>{continueStepsImages.value=false;if(stepsContinuationRequest.value?.taskId!==cloudTask.value?.task_id)stepsContinuationRequest.value=null})
watch(()=>stepsBudget.value.imagesAllowed,value=>{if(!value)continueStepsImages.value=false})
const continuationMaximum=computed(()=>Math.max(0,Number(cloudTask.value?.continuation_additional_limit??(50-(cloudTask.value?.max_model_calls??30)))))
const continuationRemaining=computed(()=>Math.max(0,(cloudTask.value?.max_model_calls??0)-(cloudTask.value?.usage?.model_calls?.length??0))+continuationCalls.value)
const continuationInvalid=computed(()=>!Number.isInteger(continuationCalls.value)||continuationCalls.value<0||continuationCalls.value>continuationMaximum.value||continuationRemaining.value<1)
watch(continuationMaximum,value=>{continuationCalls.value=Math.min(continuationCalls.value,value)})
const advancedSubjectSeparation=ref(false)
const separationDescription=computed(()=>layeredStrategyDescription(advancedSubjectSeparation.value))
const separationDescriptionId=computed(()=>`vibe-separation-${props.projectId}-${props.shotId}`)
const taskStrategy=computed(()=>layeredTaskStrategy(cloudTask.value))
const layeredBudgetInvalid=computed(()=>!Number.isInteger(modelCallBudget.value)||modelCallBudget.value<6||modelCallBudget.value>50||!Number.isInteger(imageRequestBudget.value)||imageRequestBudget.value<0||imageRequestBudget.value>4)
function selectEngine(value){engine.value=value;engineTouched=true}
function templateNames(ids){return (ids||[]).map(id=>templateMeta.value.catalog?.find(item=>item.id===id)?.name||id).join(' / ')}
watch(()=>props.active,async value=>{
  await nextTick()
  if(value&&panel.value)panel.value.open=true
  else panel.value?.querySelectorAll('video').forEach(video=>video.pause())
},{immediate:true})
const cloudRunning=computed(()=>['queued','running','stopping'].includes(cloudTask.value?.status))
const retainedPreview=computed(()=>!!receipt.value&&['queued','running','stopping','failed','stopped'].includes(cloudTask.value?.status)&&receipt.value.cloud_task_id!==cloudTask.value.task_id&&!isNewerLocalPreview(receipt.value,cloudTask.value))
const stageLabels={queued:'等待执行',preparing:'整理本镜素材',planning:'设计动画方案',designing:'设计动画方案',design:'设计动画方案',materials:'准备图层素材',assembly:'复原完整海报',poster_render:'渲染海报对照图',poster_review:'对照原图检查海报',visual_repair:'视觉返工（最多 1 次）',poster_recheck:'返工后复查海报',motion:'按配音编排动效',motion_review:'核查动效关键帧',render:'渲染预览',rendering:'渲染预览',review:'检查关键帧',reviewing:'检查关键帧',repairing:'修正动效',agent_resume:'复核已有预览',agent_plan:'自主设计与工具选择',agent_review:'对照预览验收与返工',agent_tool:'执行制作工具',completed:'预览已完成',failed:'未完成',stopping:'正在停止',stopped:'已停止',needs_input:'需要补充资料',needs_budget:'素材预算已用完'}
Object.assign(stageLabels,{
  agent_resume_steps:'续接未完成步骤',
  source_measure:'本地定位文字与框线',
  agent_restore:'恢复已有素材与代码',render_wait:'等待本地渲染',layout_measure:'测量文字排版',
  layout_preview:'快速排版校样',motion_check:'完整动效检查',video_render:'输出预览视频',audio_mux:'合入原配音',
})
const agentToolLabels={inspect_image:'查看素材与局部细节',set_inventory:'整理内容与取材计划',prepare_subject:'准备主体素材',edit_background:'编辑原图底板',select_background:'选择已有底板',measure_text:'测量文字排版',preview_scene:'编写并渲染动效预览',finish:'检查并交付预览',report_blocker:'说明制作阻碍'}
agentToolLabels.locate_elements='本地定位文字与框线'
function qualityStatus(value){
  const statuses=[value?.quality_status,value?.metrics?.quality_status]
  if(statuses.includes('needs_review'))return 'needs_review'
  if(value?.workflow_version&&(!statuses.includes('passed')||statuses.some(status=>status!=null&&status!=='passed')))return 'unverified'
  return statuses.includes('passed')?'passed':''
}
function qualityIssues(value){
  const issues=value?.quality_issues??value?.metrics?.quality_issues
  return (Array.isArray(issues)?issues:[]).slice(0,12).map(issue=>typeof issue==='string'?issue:[issue?.explanation,issue?.correction].filter(text=>typeof text==='string'&&text.trim()).join('；建议：')).filter(Boolean)
}
const previewQuality=computed(()=>qualityStatus(receipt.value))
const previewNeedsReview=computed(()=>['needs_review','unverified'].includes(previewQuality.value))
const previewQualityIssues=computed(()=>qualityIssues(receipt.value))
const taskNeedsReview=computed(()=>qualityStatus(cloudTask.value)==='needs_review'||['needs_review','unverified'].includes(qualityStatus(cloudTask.value?.preview||cloudTask.value)))
const cloudStatusLabel=computed(()=>cloudTask.value?.status==='completed'&&taskNeedsReview.value?'预览待检查 · 未通过验收':!cloudRunning.value||cloudTask.value?.status==='stopping'?stageLabels[cloudTask.value?.status]:cloudTask.value?.stage==='agent_tool'?(agentToolLabels[cloudTask.value.agent_tool]||stageLabels.agent_tool):stageLabels[cloudTask.value?.stage]||'云端制作')
const resumeRequiresModel=computed(()=>cloudTask.value?.resume_requires_model===true||cloudTask.value?.metrics?.resume_requires_model===true)
const visualRepairs=computed(()=>cloudTask.value?.visual_repairs??cloudTask.value?.metrics?.visual_repairs??cloudTask.value?.preview?.visual_repairs??cloudTask.value?.preview?.metrics?.visual_repairs)
const isToolAgentTask=computed(()=>cloudTask.value?.workflow_version==='tool-agent-layered-v4')
const cloudProgress=computed(()=>cloudTask.value?.message||stageLabels[cloudTask.value?.stage]||stageLabels[cloudTask.value?.status]||'正在处理…')
const base=computed(()=>previewRoute(props.projectId,props.shotId))
const brief=computed(()=>vibeBrief(props.projectId,props.shotId,props.number))
const movie=computed(()=>previewFileUrl(props.projectId,receipt.value,'preview'))
const silent=computed(()=>previewFileUrl(props.projectId,receipt.value,'silent'))
const proof=computed(()=>receipt.value?.proof_url?previewFileUrl(props.projectId,receipt.value,'proof'):'')
const rendererLabel=computed(()=>String(receipt.value?.renderer||'').includes('remotion')?'Remotion 渲染':receipt.value?.engine==='cloud'&&receipt.value?.renderer?'旧版基础渲染':'')
const previewLabel=computed(()=>receipt.value?.template_mode==='layered'?'原图分层':receipt.value?.template_mode==='shotcraft'?'云端模板':receipt.value?.engine==='cloud'?'云端设计':'本地程序生成')
let gone=false,pollTimer=null,scopeVersion=0
const scope=()=>({base:base.value,version:scopeVersion})
const current=s=>!gone&&s.version===scopeVersion&&s.base===base.value
function clearPoll(){if(pollTimer)clearTimeout(pollTimer);pollTimer=null}
onUnmounted(()=>{gone=true;clearPoll()})
watch(base,()=>{scopeVersion++;clearPoll();cloudTask.value=null;receipt.value=null;design.value=null;plan.value=null;planName.value='';busy.value='';error.value='';hint.value='';cloudReadError.value='';templateIds.value=[];selectionTouched=false;engineTouched=false;modelCallBudget.value=30;imageRequestBudget.value=2;continuationCalls.value=10;continuationRequest=null;continueStepsImages.value=false;stepsContinuationRequest.value=null;advancedSubjectSeparation.value=false;if(opened.value)refresh()})
async function loadCloudConfig(){
  const s=scope()
  try{
    const result=await requestJSON('/api/vibe-model')
    if(!current(s))return null
    cloudConfig.value=result;return result
  }catch(e){if(current(s))error.value='无法读取 Vibe 专用配置：'+e.message;return null}
}
watch(engine,value=>{if(value!=='local'&&!cloudConfig.value)loadCloudConfig()})
function isNewerLocalPreview(preview,task){
  if(!preview?.preview_id||preview.engine==='cloud'||!task?.task_id)return false
  const selectedAt=Number(preview.selected_at),taskAt=Number(task.updated_at??task.created_at)
  if(Number.isFinite(selectedAt)&&selectedAt>0&&Number.isFinite(taskAt)&&taskAt>0)return selectedAt>taskAt
  // Older responses have no selection timestamp. A completed cloud task
  // publishes its own pointer, so a different local pointer is newer.
  return task.status==='completed'&&!!task.preview?.preview_id&&preview.preview_id!==task.preview.preview_id
}
function restoreEngine(preview,task){
  if(engineTouched)return
  if(task?.task_id){
    // The preview pointer and latest task describe different things. A failed
    // cloud attempt can leave an old local preview; that is not a local retry.
    engine.value=isNewerLocalPreview(preview,task)?'local':task.template_mode==='layered'?'layered':'cloud'
  }else if(preview?.preview_id)engine.value=preview.template_mode==='layered'?'layered':preview.engine==='cloud'?'cloud':'local'
}
function applyCloud(result,s,replacePreview=true){
  if(!current(s)||!result?.task_id)return
  const registeredContinuation=continuationRequest?.taskId===result.task_id&&(result.review_continuations||[]).some(item=>item.request_id===continuationRequest.body.request_id)
  if(registeredContinuation){continuationRequest=null;hint.value='上次继续请求已登记，未重复追加预算或启动任务。'}
  const pendingSteps=stepsContinuationRequest.value
  if(pendingSteps?.taskId===result.task_id&&(result.steps_continuations||[]).some(item=>item.request_id===pendingSteps.body.request_id)){
    stepsContinuationRequest.value=null;continueStepsImages.value=false;hint.value='上次续接步骤请求已登记，未重复授权或启动任务。'
  }else if(pendingSteps?.taskId===result.task_id){
    const budget=stepContinuationBudget(result),expected=pendingSteps.body
    if(budget.valid&&(budget.modelUsed!==expected.expected_model_calls||budget.modelLimit!==expected.expected_model_limit||budget.imageUsed!==expected.expected_image_requests||budget.imageLimit!==expected.expected_image_limit)){
      stepsContinuationRequest.value=null;continueStepsImages.value=false;hint.value='任务预算记录已变化，请核对剩余次数后重新确认续接；未自动提交。'
    }
  }
  cloudTask.value=result;cloudReadError.value=''
  if(!selectionTouched&&Array.isArray(result.requested_template_ids))templateIds.value=[...result.requested_template_ids]
  if(['queued','running','stopping'].includes(result.status)){busy.value='cloud';schedulePoll(s);return}
  clearPoll();busy.value=''
  if(result.status==='completed'&&result.preview&&replacePreview){receipt.value=result.preview;stale.value=false;hint.value=previewNeedsReview.value?'已保留待检查预览，尚不能采用。请查看问题并调整要求后重试；正式镜头未改变。':'云端动效预览已就绪；正式镜头未改变，满意后再采用。';error.value=''}
  else if(result.status==='failed'){error.value=result.message||'云端制作失败，原素材和正式镜头未改变。可调整设置后重试。'}
  else if(result.status==='stopped'){hint.value=result.message||'任务已停止，正式镜头未改变。'}
}
function schedulePoll(s){clearPoll();pollTimer=setTimeout(()=>pollCloud(s),1500)}
async function pollCloud(s=scope()){
  if(!current(s))return
  try{const result=await requestJSON(s.base+'/vibe-cloud-job/latest');if(current(s))applyCloud(result,s)}
  catch(e){if(current(s)){clearPoll();cloudReadError.value='任务状态暂时无法读取：'+e.message+'。任务可能仍在运行，可刷新状态或停止。'}}
}

async function refresh(){
  if((busy.value&&busy.value!=='cloud')||!props.ready)return
  const s=scope();clearPoll()
  busy.value='read';error.value='';hint.value=''
  try{
    const [previewResult,taskResult]=await Promise.allSettled([requestJSON(s.base+'/vibe-preview/latest'),requestJSON(s.base+'/vibe-cloud-job/latest')])
    if(!current(s))return
    if(previewResult.status==='fulfilled'){const result=previewResult.value;receipt.value=result.available?result:null;stale.value=!result.available&&result.stale===true}
    else error.value=previewResult.reason.message
    if(taskResult.status==='fulfilled'){
      restoreEngine(receipt.value,taskResult.value)
      applyCloud(taskResult.value,s,!receipt.value||receipt.value.preview_id===taskResult.value?.preview?.preview_id)
    }
    else if(taskResult.reason.status!==404)cloudReadError.value='云端任务状态读取失败，请刷新后再启动：'+taskResult.reason.message
  }catch(e){if(current(s))error.value=e.message}
  finally{if(current(s))busy.value=cloudRunning.value?'cloud':''}
}
function toggle(e){ opened.value=e.currentTarget.open;if(opened.value)refresh();else panel.value?.querySelectorAll('video').forEach(video=>video.pause()) }
async function show(){if(panel.value){panel.value.open=true;await nextTick();panel.value?.scrollIntoView({block:'center',behavior:'smooth'})}}
defineExpose({show})
const routeLabels={vibe:'建议用图层动效',static:'建议保留静态',video:'建议用视频模型'}
const post=(suffix,body)=>requestJSON(base.value+'/'+suffix,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
async function autoDesign(){
  if(busy.value||props.unsaved||props.designLocked||!props.ready)return
  if(engine.value!=='local'){await startCloud();return}
  const s=scope()
  error.value='';hint.value='';busy.value='design'
  const body={engine:'local',confirm_model:false,pace:pace.value,instruction:instruction.value.trim(),transition:transition.value,transition_seconds:Number(transitionSeconds.value)}
  if(transition.value==='layered'&&(!receipt.value?.plan?.entrance||receipt.value.plan.entrance.kind==='layered')&&receipt.value?.preview_id&&(body.instruction||pace.value!=='normal'))body.previous_preview_id=receipt.value.preview_id
  try{
    const result=await post('vibe-design',body)
    if(!current(s))return
    const data=parsePreviewPlan(JSON.stringify(result.preview_request))
    design.value=result;plan.value=data;planName.value='本镜自动设计方案'
    busy.value='render'
    const output=await post('vibe-previews',data)
    if(current(s)){receipt.value=output;stale.value=false;hint.value=output.cached?'已复用已有预览。':'预览已就绪；满意后再采用。'}
  }catch(e){if(current(s))error.value=(busy.value==='render'?'设计已保留，可展开「已有动画方案？」仅重试本地合成。':'')+e.message}
  finally{if(current(s))busy.value=''}
}
async function startCloud(){
  if(busy.value||cloudRunning.value||props.unsaved||props.designLocked||!props.ready)return
  const layered=engine.value==='layered'
  const separationOptions=layeredRequestOptions(engine.value,advancedSubjectSeparation.value)
  if(layered&&layeredBudgetInvalid.value){error.value='请设置有效预算：模型调用 6–50 次、图像请求 0–4 次，均为整数。';return}
  if(!layered&&templateSelectionInvalid.value){error.value=templateMeta.value.issue||'当前模板组合不可用，请调整后再制作。';return}
  const selectedTemplates=layered?[]:[...templateIds.value]
  const s=scope();error.value='';hint.value='';busy.value='config'
  const config=await loadCloudConfig()
  if(!current(s))return
  busy.value=''
  if(!config)return
  if(!config.configured){error.value='请先在「接口与服务 → Vibe 动效」保存专用 API Key。不会改用项目号池或本地模式。';return}
  let imageRoute=null
  if(!layered||imageRequestBudget.value>0){
    busy.value='config'
    try{imageRoute=await requestJSON(s.base+'/vibe-image-route')}
    catch(e){if(current(s))error.value=e.message;return}
    finally{if(current(s))busy.value=''}
    if(!current(s))return
  }
  const pendingImage=layered&&!!(imageRoute?.pending_image_task||cloudTask.value?.can_resume_materials)
  const freshNotice=freshCloudStartNotice(cloudTask.value,receipt.value)
  const pendingNotice=pendingImage?'\n注意：旧渠道任务可能仍在运行或计费；重新开始不会转移、查询或取消旧任务。若希望取得原背景结果，请取消并使用“继续原素材任务”。'+(imageRequestBudget.value>0?'确认后本次按当前预算独立执行，可能新增图片费用。':'本次不授权新增出图，但旧图任务仍可能计费。'):''
  const selectionNote=selectedTemplates.length?`手选模板：${templateNames(selectedTemplates)}。仅使用这些模板，由 AI 按文案安排顺序；不会擅自换款。`:'模板：让 AI 自动选择，参考内容、时长与相邻镜头。'
  if(!layered&&!window.confirm(`模板背景将先通过本项目图像模型（${imageRoute.label}）提取一次，可能产生图片费用。保留背景纹理和装饰，移除文字、文本框和复杂主体；不自动重试、不切换渠道。继续吗？`))return
  if(!window.confirm(layered?`${freshNotice}\n\n把本镜核心图、已绑定参考图和最终字幕时间戳交给 ${config.model}，由专用模型（如 DS）自主使用工具取材、写代码、测量排版、查看预览截图并返工，复原海报与编排动效。\n${layeredStrategyDescription(separationOptions.advanced_subject_separation)}\n本次最多 ${modelCallBudget.value} 次模型调用，可能计费；预算上限不代表必定用满。连接失败不自动重试。需要分离主体时在本机按需运行 RMBG，Remotion 在本机渲染，无需启动 ComfyUI。\n${imageRequestBudget.value>0?`授权模型按需发起最多 ${imageRequestBudget.value} 次图像生成或编辑请求，可能计费；使用本项目图像模型：${imageRoute.label}，不会切换渠道。`:'本次图像请求预算为 0：不授权新增出图，仅使用原图、绑定参考素材及本地工具；确需出图时会提示。'}${pendingNotice}\n只生成预览，不覆盖正式镜头；核查仍有问题时仅供检查，调整后重试，通过后才可采用。确认开始吗？`:`${freshNotice}\n\n将当前分镜图、生成本图时实际使用的参考图和最终字幕时间戳发送给 Vibe 专用接口（${config.model}）。\n${selectionNote}\n专用 API 选择已验证镜头模板，继承原图配色与背景风格编排，本机渲染。可从原图保真裁取最多 4 个矩形素材；不自动抠图；先使用本项目图像模型提取一次干净装饰背景，图像请求可能计费。\n最多修正 2 轮，专用模型调用可能计费。只生成预览，通过后仍需确认采用。确认开始吗？`))return
  busy.value='cloud-submit'
  try{
    const result=await requestJSON(s.base+'/vibe-cloud-jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm_cloud:true,start_mode:'fresh',template_mode:layered?'layered':'shotcraft',...separationOptions,...(selectedTemplates.length?{template_ids:selectedTemplates}:{}),...(imageRoute?{image_route_key:imageRoute.key}:{}),...(pendingImage?{confirm_new_image_task:true}:{}),instruction:instruction.value.trim(),pace:pace.value,max_image_requests:layered?imageRequestBudget.value:1,...(layered?{max_model_calls:modelCallBudget.value}:{})})})
    if(!current(s))return
    if(!result?.task_id)throw new Error('服务未返回任务编号，请刷新任务状态后重试。')
    applyCloud(result,s)
  }catch(e){if(current(s)){busy.value='';error.value=vibeCloudSubmissionError(e)}}
}
async function resumeMaterials(){
  if(busy.value||!cloudTask.value?.can_resume_materials||props.unsaved||props.designLocked||!props.ready)return
  const resumeNote=resumeRequiresModel.value?'继续获取原背景图片任务的结果，不重新提交出图。素材到齐后，将继续调用 Vibe 专用模型复原海报、核查与编排动效（可能多次调用并计费）；不是仅做本地渲染。':'继续使用上次已保存的分层方案和原背景图片任务。只查询原任务结果并继续本地渲染，不再调用 DS、不重新提交出图；'
  if(!window.confirm(resumeNote+'\n原任务策略：'+(taskStrategy.value||'沿用原任务记录')+'。当前新填写的动效要求、预算和进阶主体分离开关不用于这次恢复，不改变原任务策略。若仍在排队，等待超时后可再次继续。'))return
  const s=scope(),id=cloudTask.value.task_id;busy.value='cloud-submit';error.value='';cloudReadError.value=''
  try{
    const result=await requestJSON(s.base+'/vibe-cloud-jobs/'+encodeURIComponent(id)+'/resume-materials',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm_model:resumeRequiresModel.value})})
    if(current(s))applyCloud(result,s)
  }catch(e){if(current(s)){busy.value='';error.value=e.message}}
}
async function continueReview(){
  if(busy.value||!cloudTask.value?.can_continue_review||props.unsaved||props.designLocked||!props.ready||continuationInvalid.value)return
  const task=cloudTask.value,used=task.usage?.model_calls?.length??0,added=continuationCalls.value
  if(!window.confirm(`从这轮已有素材、代码和预览继续，让 Vibe 专用模型检查并按需返工，不从头制作。\n已用 ${used} 次，原授权 ${task.max_model_calls} 次；本次追加 ${added} 次，继续期间最多还可调用 ${continuationRemaining.value} 次，可能计费（含失败请求与交付核查）。\n不新增出图，不覆盖正式镜头。沿用原任务的要求和主体策略，上方新任务设置不用于这次继续。`))return
  const s=scope(),id=task.task_id
  if(!continuationRequest||continuationRequest.taskId!==id||continuationRequest.body.expected_model_calls!==used||continuationRequest.body.expected_model_limit!==task.max_model_calls||continuationRequest.body.additional_model_calls!==added){
    continuationRequest={taskId:id,body:{confirm_model:true,additional_model_calls:added,expected_model_calls:used,expected_model_limit:task.max_model_calls,
      request_id:globalThis.crypto?.randomUUID?.()||`continue-${Date.now()}-${Math.random().toString(36).slice(2)}`}}
  }
  const pending=continuationRequest;busy.value='cloud-submit';error.value='';cloudReadError.value=''
  try{
    const result=await requestJSON(s.base+'/vibe-cloud-jobs/'+encodeURIComponent(id)+'/continue-review',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending.body)})
    if(current(s)){continuationRequest=null;applyCloud(result,s)}
  }catch(e){if(current(s)){busy.value='';error.value=e.message+'。已保留本次请求编号；若响应中断，请先刷新核对状态，再用同一请求继续，避免重复授权。'}}
}
async function continueSteps(){
  if(busy.value||!cloudTask.value?.can_continue_steps||props.unsaved||props.designLocked||!props.ready||stepsContinuationInvalid.value)return
  const task=cloudTask.value,budget=stepsBudget.value,id=task.task_id
  const allowImages=stepsContinuationRequest.value?.taskId===id
    ? stepsContinuationRequest.value.body.confirm_remaining_images : continueStepsImages.value&&budget.imagesAllowed
  if(!window.confirm(stepContinuationNotice(task,allowImages)+`\n本次追加 ${stepsContinuationRequest.value?.body.additional_model_calls??stepsAdditionalCalls.value} 次模型调用授权，可能计费；累计上限不超过50次，不重置已用次数。`))return
  const s=scope()
  if(!stepsContinuationRequest.value||stepsContinuationRequest.value.taskId!==id){
    stepsContinuationRequest.value={taskId:id,body:{confirm_model:true,additional_model_calls:stepsAdditionalCalls.value,
      expected_model_calls:budget.modelUsed,expected_model_limit:budget.modelLimit,
      confirm_remaining_images:allowImages,expected_image_requests:budget.imageUsed,expected_image_limit:budget.imageLimit,
      request_id:globalThis.crypto?.randomUUID?.()||`steps-${Date.now()}-${Math.random().toString(36).slice(2)}`}}
  }
  const pending=stepsContinuationRequest.value;busy.value='cloud-submit';error.value='';cloudReadError.value=''
  try{
    const result=await requestJSON(s.base+'/vibe-cloud-jobs/'+encodeURIComponent(id)+'/continue-steps',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending.body)})
    if(!result?.task_id||result.task_id!==id)throw new Error('续接回执与本次任务不一致，请刷新任务状态核对')
    if(current(s)){stepsContinuationRequest.value=null;continueStepsImages.value=false;applyCloud(result,s)
      if(result.steps_continuation_replayed)hint.value='该续接步骤请求已登记，未重复授权或启动任务。'}
  }catch(e){if(current(s)){busy.value='';error.value=e.message+'。已保留本次续接请求编号，未自动重试；请先刷新核对任务状态，再使用同一请求继续，避免重复授权。'}}
}
async function stopCloud(){
  if(!cloudRunning.value||busy.value==='stop'||cloudTask.value?.status==='stopping')return
  const s=scope(),id=cloudTask.value.task_id;busy.value='stop';cloudReadError.value=''
  try{
    const result=await requestJSON(s.base+'/vibe-cloud-jobs/'+encodeURIComponent(id)+'/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
    if(current(s)){applyCloud(result,s);if(cloudRunning.value){hint.value='已请求停止，正在等待当前步骤安全退出。';schedulePoll(s)}}
  }catch(e){if(current(s)){busy.value='cloud';cloudReadError.value='停止请求失败：'+e.message;schedulePoll(s)}}
}
async function adopt(undo=false){
  const id=undo?props.adopted:receipt.value?.preview_id
  if(!id||props.unsaved||props.editLocked||busy.value||!Number.isInteger(props.revision))return
  if(!undo&&previewNeedsReview.value){error.value='此预览尚未通过视觉核查，请调整动效要求后重新设计；不能采用到正式镜头。';return}
  if(!window.confirm(undo?'撤回本镜动效，恢复采用前的镜头？':'采用这个预览作为本镜正式画面？下次导出使用这个无声片段和项目原配音；可以撤回。'))return
  const s=scope()
  busy.value='save';error.value='';hint.value=''
  try{
    const record=await post(undo?'vibe-undo':'vibe-adopt',{revision:props.revision,preview_id:id})
    if(current(s)){emit('update',record);hint.value=undo?'已恢复采用前的镜头。':'已采用。下次导出使用此动效，不会重新配音。'}
  }catch(e){if(current(s))error.value=e.message}
  finally{if(current(s))busy.value=''}
}
async function reserve(){
  if(props.adopted||props.unsaved||props.reserveLocked||busy.value||!Number.isInteger(props.revision))return
  const enabled=!props.reserved
  if(!window.confirm(enabled?'预留本镜给 Vibe？核心图仍按原流程生成；本镜不会提交给视频模型。正式导出前需采用动效或取消预留。':'取消预留，恢复本镜原来的制作方式？不会删除已生成的图片或预览。'))return
  const s=scope()
  busy.value='intent';error.value='';hint.value=''
  try{
    const record=await post('vibe-intent',{revision:props.revision,enabled})
    if(current(s)){emit('update',record);hint.value=enabled?'已预留。本镜不会提交给视频模型。':'已取消预留，恢复原来的制作方式。'}
  }catch(e){if(current(s))error.value=e.message}
  finally{if(current(s))busy.value=''}
}
async function choose(e){
  const selected=e.target.files?.[0];e.target.value='';if(!selected)return
  const s=scope()
  plan.value=null;planName.value='';error.value='';hint.value='';busy.value='file'
  try{
    if(selected.size>MAX_PLAN_BYTES)throw new Error('动画方案超过 64 KB，请检查是否误选了素材文件。')
    const data=parsePreviewPlan(await selected.text())
    if(current(s)){plan.value=data;planName.value=selected.name}
  }catch(e){if(current(s))error.value=e.message}
  finally{if(current(s))busy.value=''}
}
async function render(){
  if(!plan.value||busy.value||props.unsaved||props.designLocked||!props.ready)return
  const s=scope()
  busy.value='render';error.value='';hint.value=''
  try{
    const result=await requestJSON(base.value+'/vibe-previews',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(plan.value)})
    if(current(s)){receipt.value=result;stale.value=false;hint.value=result.cached?'已复用相同方案的预览，没有重新渲染。':'预览已就绪；正式镜头没有改变。'}
  }catch(e){if(current(s))error.value=e.message}
  finally{if(current(s))busy.value=''}
}
async function copyBrief(){
  manualCopy.value=false;hint.value=''
  try{await navigator.clipboard.writeText(brief.value);if(!gone)hint.value='已复制，可粘贴给连接了 OCV 的 AI。'}
  catch{if(!gone)manualCopy.value=true}
}
</script>
<template>
  <details ref="panel" class="vibe-preview" @toggle="toggle">
    <summary>本镜动效预览 <small>{{adopted?'已采用 Vibe':reserved?'已预留 · 待采用':routeLabels[recommendation?.route]||'Vibe · 先预览，再采用'}}</small></summary>
    <template v-if="opened">
      <div v-if="movie" class="vibe-preview-source"><strong>当前预览：{{previewLabel}}</strong><small v-if="rendererLabel" class="vibe-renderer">{{rendererLabel}}</small><small v-if="receipt?.template_ids?.length" class="vibe-template-ids">模板 {{templateNames(receipt.template_ids)}}</small><span v-if="retainedPreview" class="vibe-retained-preview">保留的上次预览 · 不是本轮云端制作的新结果</span></div>
      <video v-if="movie" :key="movie" :src="movie" :poster="imageSrc" controls playsinline preload="metadata" aria-label="本镜动效与配音预览"></video>
      <div v-else-if="imageSrc" class="vibe-still-preview"><img :src="imageSrc" :alt="'第 '+number+' 镜静态分镜'"><small>静态分镜 · Vibe 预览完成后在此展示</small></div>
      <slot name="narration" />
      <p v-if="!ready">本镜还没有完成的核心图。可以先预留，生成核心图后再设计动效。</p>
      <div v-if="ready" class="vibe-designer">
        <div class="vibe-actions"><label>制作方式 <select :value="engine" :disabled="!!busy" @change="selectEngine($event.target.value)"><option value="local">本地 · 程序生成基础动效</option><option value="cloud">云端 · 模板动效</option><option value="layered">云端 · 原图分层动效（试用）</option></select></label>
          <label v-if="engine!=='local'||transition==='layered'">入场节奏 <select v-model="pace" :disabled="!!busy"><option value="normal">标准</option><option value="slow">舒缓</option><option value="brisk">轻快</option></select></label>
          <template v-if="engine==='local'"><label>入场转场 <select v-model="transition" :disabled="!!busy"><option value="layered">分层渐入 · 字幕辅助</option><option value="brush">画笔显现 · 模拟手绘</option><option value="dissolve">叠化</option><option value="black">闪黑</option><option value="white">闪白</option><option value="left">从左移入</option><option value="right">从右移入</option><option value="up">从上移入</option><option value="down">从下移入</option></select></label><label>转场时长 <input v-model.number="transitionSeconds" type="number" min="0.1" max="3" step="0.1" :disabled="!!busy" style="width:85px"> 秒</label></template></div>
        <p v-if="engine==='local'" class="muted">仅作用于本镜开头，上一镜已采用画面的末帧作为下层；首镜使用中性底色。不加出场动画、不改变配音时间轴。{{transition==='layered'?'至少 60% 的区块优先在前 1 秒内就位，其余可跟随字幕。':'整张原图完成入场后保持，不使用音频卡点。'}}</p>
        <ShotcraftTemplatePicker v-if="engine==='cloud'" :model-value="templateIds" :disabled="!!busy||designLocked" :duration="duration" @update:model-value="selectTemplates" @selection-meta="templateMeta=$event" />
        <label>动效要求 <small>选填</small><input v-model="instruction" :disabled="!!busy" maxlength="500" placeholder="例如：左边慢一点，最后多留半秒"></label>
        <p v-if="engine==='local'">纯本地处理，不调用 AI 模型、无模型费用。分层渐入适合简单版式，其余转场直接保留完整原图。<button type="button" class="vibe-gallery-link" :disabled="!!busy" @click="selectEngine('cloud')">浏览 12 款云端动效 →</button></p>
        <template v-else-if="engine==='layered'">
          <p>参考本镜海报构图，按最终配音编排可控图层；照片和原始数据图沿用原素材。默认优先保留复杂主体与背景的完整关系，不要求每个物件都单独抠图。</p>
          <p>专用模型（如 DS）自主使用取材、抠图、测量与预览工具，编写 Remotion 动效代码，并对照原图检查、返工。六步制作建议用于提高效率，不限制工具使用顺序；只生成预览，不覆盖正式镜头。</p>
          <div class="vibe-separation-policy">
            <label><input type="checkbox" v-model="advancedSubjectSeparation" :disabled="!!busy||designLocked" aria-label="进阶主体分离（试用）" :aria-describedby="separationDescriptionId"><span>进阶主体分离（试用）</span><small>默认关闭</small></label>
            <p :id="separationDescriptionId">{{separationDescription}}</p>
            <small>仅影响下一次新任务；人物按需分离时使用本地 RMBG，无需启动 ComfyUI。“继续原素材任务”仍沿用原任务策略。</small>
          </div>
          <fieldset class="vibe-budget" :disabled="!!busy||designLocked">
            <legend>本次自主制作预算</legend>
            <div class="vibe-actions">
              <label>模型调用上限 <input aria-label="模型调用上限" type="number" v-model.number="modelCallBudget" min="6" max="50" step="1"> 次</label>
              <label>图像请求上限 <select aria-label="图像请求上限" v-model.number="imageRequestBudget"><option :value="0">0 次 · 不新增出图</option><option v-for="count in 4" :key="count" :value="count">{{count}} 次</option></select></label>
            </div>
            <p>预算上限不代表必定用满。模型调用和图像生成/编辑可能计费；图像请求沿用「本项目图像模型」已保存的渠道与清晰度，不自动切换。已有素材可复用，本地 RMBG 与渲染不计入模型调用次数。</p>
            <p v-if="layeredBudgetInvalid" class="vibe-error" role="alert">模型调用上限须为 6–50 的整数，图像请求上限须为 0–4 的整数。</p>
          </fieldset>
        </template>
        <template v-else>
          <p title="使用设置中的专用 API，无需 GPT 会员。">通过 Vibe 专用 API 调用配置的模型（如 DeepSeek）设计方案，Remotion 在本机渲染。{{templateIds.length?'按手选模板编排，AI 决定顺序。':'AI 按语义选型，参考相邻镜头减少重复。'}} 沿用本镜配色与素材，预览通过后仍需采用。</p>
          <p>{{cloudConfig?.configured?'当前模型：'+cloudConfig.model:'请在「接口与服务 → Vibe 动效」配置专用 API。'}} 专用模型和背景提取可能计费，最多修正 2 轮；支持原图矩形保真裁切，不自动抠图；先使用本项目图像模型提取一次干净装饰背景，图像请求可能计费。</p>
        </template>
        <div class="vibe-actions">
          <button class="primary-btn" type="button" :disabled="!!busy||cloudRunning||unsaved||designLocked||(engine==='cloud'&&templateSelectionInvalid)||(engine==='layered'&&layeredBudgetInvalid)" @click="autoDesign">{{cloudRunning?'本镜制作中…':busy==='cloud-submit'?'正在提交任务…':receipt?'重新设计并预览':'自动设计并预览'}}</button>
          <button v-if="cloudRunning" type="button" :disabled="busy==='stop'||cloudTask.status==='stopping'" @click="stopCloud">{{busy==='stop'||cloudTask.status==='stopping'?'正在停止…':'停止本镜制作'}}</button>
        </div>
        <p v-if="cloudRunning" role="status">停止后保留已有素材和上一版预览，不影响正式镜头及其他镜头任务。已提交的上游请求可能仍在执行或计费。</p>
        <p v-else-if="busy==='cloud-submit'" role="status">正在等待任务编号，收到后可停止本镜制作；请勿重复提交。</p>
        <p v-if="editLocked&&!designLocked">当前阶段只可预览，待核心图确认并且任务空闲后可采用。</p>
        <p v-if="design" role="note">{{design.summary}} {{(design.warnings||[]).join(' ')}}</p>
      </div>

      <p v-if="!receipt&&ready&&!busy&&!error">{{stale?'本镜的画面、字幕或配音已有变化，旧预览已收起。请用最新素材重新预览。':'选择制作方式与动效后生成预览，不会修改正式镜头。'}}</p>
      <section v-if="receipt&&previewNeedsReview" class="vibe-quality-warning" role="note" aria-label="预览待修正问题">
        <strong>{{previewQuality==='needs_review'?'视觉核查未通过 · 仅供检查':'缺少完整的视觉核查结果 · 仅供检查'}}</strong>
        <p>本次预览可以播放检查，但不能采用到正式镜头。请参考下面的问题调整动效要求后重新设计；原正式镜头保留。</p>
        <ul v-if="previewQualityIssues.length"><li v-for="(issue,index) in previewQualityIssues" :key="index">{{issue}}</li></ul>
      </section>
      <div v-if="receipt||adopted" class="vibe-actions">
        <button v-if="receipt&&receipt.preview_id!==adopted" :disabled="!!busy||unsaved||editLocked||previewNeedsReview" :title="previewNeedsReview?'视觉核查未通过，请调整要求后重新设计':''" type="button" @click="adopt(false)">采用到正式镜头</button>
        <button v-if="adopted" :disabled="!!busy||unsaved||editLocked" type="button" @click="adopt(true)">撤回采用</button>
      </div>
      <section v-if="cloudTask" class="vibe-cloud-status" aria-label="云端制作状态">
        <p class="vibe-task-source">最近云端任务 · 专用 AI 模型设计，本机渲染</p>
        <strong>{{cloudStatusLabel}}</strong>
        <p role="status">{{cloudProgress}}</p>
        <p v-if="taskStrategy" class="vibe-task-strategy">本任务策略：{{taskStrategy}}</p>
        <small v-if="cloudTask.template_ids?.length" class="vibe-task-template-ids">模板 {{templateNames(cloudTask.template_ids)}}</small>
        <small v-if="cloudTask.usage">{{cloudTask.usage.model_calls?.length??0}} 次模型调用 · {{cloudTask.usage.image_requests??cloudTask.usage.image_calls??0}} 次图像请求 · {{cloudTask.usage.repair_rounds??cloudTask.usage.repairs??cloudTask.attempt??0}} 轮修正</small>
        <small v-if="isToolAgentTask&&cloudTask.max_model_calls!=null">本任务授权上限：模型 {{cloudTask.max_model_calls}} 次 · 图像 {{cloudTask.max_image_requests??0}} 次</small>
        <small v-if="cloudTask.new_images_allowed===false">继续核查期间：禁止新增出图，仅复用已有素材。</small>
        <small v-if="visualRepairs!=null&&!isToolAgentTask">海报视觉返工 {{visualRepairs}} / 1 次</small>
        <p v-if="cloudTask.image_route?.label">本次背景图像渠道：{{ cloudTask.image_route.label }}</p>
        <div v-if="cloudTask.can_resume_materials&&!cloudRunning" class="vibe-actions"><button type="button" :disabled="!!busy||unsaved||designLocked||!ready" @click="resumeMaterials">继续原素材任务</button><small>{{resumeRequiresModel?'按原任务渠道获取素材，不重复出图；随后继续模型复原、核查和动效编排，可能计费。':'按原任务渠道查询，不跟随当前设置切换；不重新调用 DS、不重复出图。'}}</small></div>
        <fieldset v-if="cloudTask.can_continue_steps&&!cloudRunning" class="vibe-budget vibe-continue-steps" :disabled="!!busy||unsaved||designLocked||!ready">
          <label>追加模型次数 <input v-model.number="stepsAdditionalCalls" :disabled="!!stepsContinuationRequest" type="number" min="0" :max="Math.max(0,50-stepsBudget.modelLimit)" step="1" aria-label="续接步骤追加模型次数"></label>
          <legend>从保存步骤续接</legend>
          <div class="vibe-actions"><button type="button" :disabled="stepsContinuationInvalid" @click="continueSteps">继续未完成步骤</button><span>模型已用 {{stepsBudget.modelUsed}} / {{stepsBudget.modelLimit}} 次 · 剩余 {{stepsBudget.modelRemaining}} 次</span></div>
          <label v-if="stepsBudget.imagesAllowed" class="vibe-steps-image-choice"><input v-model="continueStepsImages" :disabled="!!stepsContinuationRequest" type="checkbox" aria-label="续接允许使用原授权剩余图片请求"><span>允许使用原授权剩余 {{stepsBudget.imageRemaining}} 次图片请求（可能计费）</span></label>
          <small>仅按上方输入追加授权，不从头制作，不自动重放旧工具；即使尚无预览，也可接着完成。{{continueStepsImages&&stepsBudget.imagesAllowed?'仅沿用原图片额度和渠道。':'默认不新增出图，仅复用已有素材；确认后本轮后续续接也不恢复图片授权。'}}不覆盖正式镜头，不自动重试。</small>
          <small v-if="stepsContinuationInvalid" class="vibe-error">{{stepsBudget.valid?'原模型预算已用完；请使用下方可用的追加预算入口，或确认后新建任务。':'续接预算信息不完整，请先刷新状态。'}}</small>
        </fieldset>
        <small v-else-if="isToolAgentTask&&!cloudRunning&&cloudTask.continue_steps_unavailable_reason" class="muted">暂不能续接未完成步骤：{{cloudTask.continue_steps_unavailable_reason}}</small>
        <fieldset v-if="cloudTask.can_continue_review&&!cloudRunning" class="vibe-budget" :disabled="!!busy||unsaved||designLocked||!ready">
          <legend>继续已有成果，不从头制作</legend>
          <div class="vibe-actions"><label>追加模型次数 <input v-model.number="continuationCalls" aria-label="继续核查追加模型次数" type="number" min="0" :max="continuationMaximum" step="1"></label><button type="button" :disabled="continuationInvalid" @click="continueReview">继续检查与返工</button></div>
          <small>复用本轮素材、代码与预览，不新增出图，不覆盖正式镜头。还可调用 {{continuationRemaining}} 次（含原剩余预算与独立核查），可能计费；累计授权最多 50 次。</small>
        </fieldset>
        <small v-else-if="isToolAgentTask&&['failed','stopped'].includes(cloudTask.status)&&!cloudTask.can_continue_steps&&!cloudTask.can_resume_materials&&cloudTask.continue_review_unavailable_reason" class="muted">暂不能继续已有预览：{{cloudTask.continue_review_unavailable_reason}}</small>
      </section>
      <p v-if="cloudReadError" class="vibe-error" role="alert">{{cloudReadError}}</p>
      <div v-if="!adopted&&(!reserveLocked||reserved)" class="vibe-actions"><button type="button" :disabled="!!busy||unsaved||reserveLocked" @click="reserve">{{reserved?'取消预留':'预留给 Vibe'}}</button><small>{{reserved?'本镜不送视频模型。请在导出前采用动效或取消预留。':'可选：预留后不送视频模型；只试预览无需预留。'}}</small></div>
      <div v-if="ready" class="vibe-actions">
        <a v-if="silent" :href="silent" :download="'镜头'+number+'-无声动效.mp4'">下载无声版</a>
        <a v-if="proof" :href="proof" target="_blank" rel="noopener">查看关键帧</a>
        <button type="button" @click="copyBrief">复制给 AI 的任务</button>
        <button type="button" :disabled="!!busy&&busy!=='cloud'" @click="refresh">刷新预览</button>
      </div>
      <textarea v-if="manualCopy" readonly :value="brief" aria-label="复制给 AI 的任务" @focus="$event.target.select()"></textarea>
      <details v-if="ready" class="vibe-import"><summary>已有动画方案？</summary>
        <input ref="file" hidden type="file" accept=".json,application/json" @change="choose">
        <div class="vibe-actions"><button type="button" :disabled="!!busy" @click="file.click()">选择方案文件</button>
          <span v-if="planName">{{planName}}</span>
          <button type="button" :disabled="!plan||!!busy||unsaved||designLocked" @click="render">生成本镜预览</button>
        </div>
      </details>
      <p v-if="unsaved">预览使用已保存的画面与字幕；请先保存镜头修改再生成。</p>
      <p v-if="(busy&&busy!=='cloud')||hint" role="status">{{({design:'正在本机识别版式与字幕…',render:'正在本机渲染…',save:'正在保存正式镜头…',intent:'正在保存本镜制作方式…',read:'正在读取…',file:'正在读取…',config:'正在读取专用接口…','cloud-submit':'正在创建云端制作任务…',stop:'正在请求停止…'})[busy]||hint}}</p>
      <p v-if="error" class="vibe-error" role="alert">{{error}}</p>
    </template>
  </details>
</template>
<style scoped>
.vibe-still-preview{margin:15px 0}.vibe-still-preview img{display:block;width:100%;max-height:60vh;object-fit:contain;background:#101212;border-radius:8px}.vibe-still-preview small{display:block;margin:8px 0}.vibe-preview video{max-height:60vh!important;object-fit:contain}

.vibe-designer{display:grid;gap:5px;margin:12px 0;padding:9px 14px;border-left:2px solid var(--accent,#82daca)}.vibe-designer select{width:auto}.vibe-designer label{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.vibe-designer input{flex:1;min-width:12em;max-width:44em}
.vibe-preview{margin-top:12px;padding:12px 16px;border:1px solid var(--border,#35423f);border-radius:10px}.vibe-preview summary{cursor:pointer}.vibe-preview small,.vibe-preview p,.vibe-import{font-size:13px;color:var(--muted,#aab8b3)}.vibe-preview small{margin-left:8px}.vibe-preview video{display:block;width:100%;max-height:440px;margin:15px auto;background:#101212;border-radius:8px}.vibe-actions{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:12px 0}.vibe-actions a{color:var(--accent,#82daca)}.vibe-preview textarea{width:100%;min-height:4em}.vibe-preview .vibe-error{color:var(--danger,#fa9797)}
.vibe-budget{min-width:0;margin:6px 0;padding:10px 12px;border:1px solid var(--border,#35423f);border-radius:8px}.vibe-budget legend{padding:0 6px;font-size:13px;color:var(--muted,#aab8b3)}.vibe-budget .vibe-actions{margin:2px 0;gap:12px 22px}.vibe-budget label{margin:4px 0}.vibe-budget input[type=number]{min-width:70px;max-width:90px}.vibe-budget:disabled{opacity:.65}.vibe-cloud-status{padding:12px 14px;margin:12px 0;background:var(--surface,#202b29);border:1px solid var(--border,#35423f);border-radius:9px}
.vibe-continue-steps{border-color:#658eaf;background:#81b9e508}.vibe-continue-steps legend{color:#b6d9f5}.vibe-continue-steps button{background:#1d405a;border:1px solid #658eaf;color:#e5f3ff}.vibe-continue-steps .vibe-actions>span{font-size:13px}.vibe-steps-image-choice{display:flex;align-items:center;gap:8px;font-size:13px}.vibe-steps-image-choice input{width:16px;height:16px;flex:none;accent-color:#91c8f2}.vibe-continue-steps>small{display:block;margin:8px 0 0;line-height:1.6}
.vibe-separation-policy{margin:6px 0;padding:10px 12px;border:1px solid var(--border,#35423f);border-radius:8px;background:var(--surface,#202b29)}.vibe-separation-policy input[type=checkbox]{flex:none;width:16px;height:16px;min-width:0;max-width:none;accent-color:var(--accent,#82daca)}.vibe-separation-policy label span{font-size:14px;font-weight:600}.vibe-separation-policy p{margin:8px 0;line-height:1.6}.vibe-separation-policy>small{display:block;margin-left:0;line-height:1.6}.vibe-separation-policy input:disabled+span{opacity:.65}
.vibe-preview-source{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:14px;font-size:13px}.vibe-preview-source strong{color:var(--accent,#82daca)}.vibe-retained-preview{color:var(--warning,#e7c37b)}
.vibe-quality-warning{margin:12px 0;padding:12px 14px;border:1px solid var(--warning,#e7c37b);border-radius:9px;background:#e7c37b0b}.vibe-quality-warning strong{color:var(--warning,#e7c37b)}.vibe-quality-warning ul{margin:8px 0 0;padding-left:22px;font-size:13px;line-height:1.6;overflow-wrap:anywhere}.vibe-quality-warning li+li{margin-top:6px}
.vibe-preview .vibe-gallery-link{margin-left:10px;padding:3px 0!important;background:transparent!important;border:0!important;color:#c5bcff!important;font-size:13px;text-decoration:underline;text-underline-offset:3px;cursor:pointer}.vibe-preview .vibe-gallery-link:disabled{opacity:.5;cursor:not-allowed}
</style>
