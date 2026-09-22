import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { api, requestJSON } from './api'
import { DEFAULT_DYNAMIC_TEXT_MODE, normalizeDynamicTextMode } from './dynamicTextMode'

// Navigation never cancels a task. A single useWorkspace instance keeps polling.
export function useStudio(w) {
  const rerunTtsKeys=['script','skip_tts','source_audio_id','tts_engine','tts_voice_id','cluster_voice_id','cluster_voice_type','qwen_tts_voice','qwen_tts_instructions','tts_emotion','tts_emotion_weight','tts_speed','tts_volume','tts_pitch','tts_pronunciation']
  const rerunTtsSnapshot=()=>Object.fromEntries([...rerunTtsKeys.map(key=>[key,w.form[key]??null]),['_engine',w.ttsEngine.value||'']])
  const creationDefaults = JSON.parse(JSON.stringify(w.form))
  const subtitleDefaults = JSON.parse(JSON.stringify(w.subtitleForm))
  const creationPreferenceKey = 'ocv.studio.creation_preferences.v1'
  const restoreDefaultsKey = 'ocv.studio.restore_defaults_on_new_project.v1'
  const restoreDefaultsOnNewProject = ref(true)
  try { restoreDefaultsOnNewProject.value = localStorage.getItem(restoreDefaultsKey) !== '0' } catch { /* optional browser storage */ }
  function savedCreationPreferences() {
    try { return JSON.parse(localStorage.getItem(creationPreferenceKey) || 'null') } catch { return null }
  }
  function rememberCreationPreferences() {
    try {
      localStorage.setItem(creationPreferenceKey, JSON.stringify({
        step_mode: Boolean(w.form.step_mode),
        visual_pacing_preset: String(w.form.visual_pacing_preset || 'standard'),
        tts_emotion: String(w.form.tts_emotion || ''),
        dynamic_text_mode: normalizeDynamicTextMode(w.form.dynamic_video ? w.form.dynamic_text_mode : savedCreationPreferences()?.dynamic_text_mode, DEFAULT_DYNAMIC_TEXT_MODE),
      }))
    } catch { /* optional browser storage */ }
  }
  watch(restoreDefaultsOnNewProject, (enabled) => {
    try { localStorage.setItem(restoreDefaultsKey, enabled ? '1' : '0') } catch { /* optional browser storage */ }
    if (!enabled) rememberCreationPreferences()
  })
  function requestReferenceImageIds(request) {
    const ids = Array.isArray(request.reference_image_ids)
      ? request.reference_image_ids.map((value) => String(value || '').trim()).filter(Boolean)
      : []
    const legacyId = String(request.protagonist_reference_image_id || '').trim()
    if (!ids.length && legacyId) ids.push(legacyId)
    return [...new Set(ids)].slice(0, 6)
  }
  async function createStudioProjectFromRequest({ reset = false } = {}) {
    const job = studioJob.value
    if (!job?.request || studioBusy.value) return
    const request = JSON.parse(JSON.stringify(job.request))
    const kind = typeOf(job)
    // Only creation fields, never task identity, checkpoints or credentials.
    const form = JSON.parse(JSON.stringify(creationDefaults))
    const missing = []
    for (const key of Object.keys(form)) {
      if (Object.hasOwn(request, key)) form[key] = request[key]
      else missing.push(key)
    }
    if (request.dynamic_video) {
      form.dynamic_text_mode = normalizeDynamicTextMode(request.dynamic_text_mode)
      if (!Object.hasOwn(request, 'dynamic_text_mode')) form.scene_references_enabled = request.director_strategy === 'enhanced_beta' && request.scene_references_enabled !== false
    }
    // Defaults may contain assets from an unrelated draft. Reference images
    // are opt-in, so they must only come from the saved source request.
    form.reference_image_ids = requestReferenceImageIds(request)
    form.protagonist_reference_image_id = form.reference_image_ids[0] || ''
    form.source_audio_id = String(request.source_audio_id || '')
    form.bgm_tracks = Array.isArray(request.bgm_tracks) ? JSON.parse(JSON.stringify(request.bgm_tracks)) : []
    form.project_name = reset
      ? String(request.project_name || '项目').slice(0, 65)
      : String(request.project_name || '项目').slice(0, 65) + ' · 副本'
    const subtitle = {...subtitleDefaults, project_name:form.project_name, source_audio_id:request.source_audio_id||'', reference_text:request.reference_text||request.script||'', use_correction:request.subtitle_use_correction??true}
    const referenced = [...(form.reference_image_ids||[]),form.protagonist_reference_image_id,request.source_audio_id,...(form.bgm_tracks||[]).map(t=>t.asset_id)].filter(Boolean)
    if (referenced.length) {
      studioBusy.value = true
      try {
        const result = await api.editorUploads()
        const ids = new Set((result.assets||[]).map(a=>a.id))
        if (referenced.some(id=>!ids.has(id))) {
          studioError.value = '原项目的部分音频、参考图或 BGM 素材缺失或无法确认，暂未创建副本。请先恢复素材后重试。'
          return
        }
      } catch {
        studioError.value = '无法检查原项目素材，请确认本地服务正常后重试。暂未创建副本。'
        return
      } finally { studioBusy.value = false }
    }
    newProject(kind,{id:'draft-'+crypto.randomUUID(),form,subtitle,engine:request.tts_engine==='indextts2'?'indextts25':request.tts_engine||'indextts25'})
    studioError.value = (reset
      ? '已重置为未运行状态：保留原项目参数，进度与执行记录不会带入新草稿。'
      : '已创建独立草稿，尚未开始生成。请核对音色和素材；API 与账户使用当前配置。')+(missing.length?'部分历史参数未记录，已用初始值补齐，请核对。':'')
    w.sourceAudioName.value = request.source_audio_id||''
    w.subtitleAudioName.value = request.source_audio_id||''
    w.referenceImageNames.value = (form.reference_image_ids || []).map((id) => {
      const asset = w.editorAssets.value.find((item) => item.id === id)
      return asset?.name || id
    })
    saveDraft()
  }
  function duplicateStudioProject() { return createStudioProjectFromRequest() }
  function resetStudioProject() {
    if (!['failed', 'cancelled', 'completed'].includes(studioJob.value?.status)) return
    if (!window.confirm('重置后将以当前项目参数创建一份未运行草稿；进度、已生成内容和断点不会带入。原项目会保留，可随时返回查看。是否继续？')) return
    return createStudioProjectFromRequest({ reset: true })
  }
  const studioPage = ref('home'), studioTab = ref('文案'), studioDrawer = ref('')
  const studioKind = ref('video'), studioError = ref(''), studioBusy = ref(false)
  const dynamicAudioProjectId = ref(''), dynamicAudioBaselineRevision = ref(0)
  const editingDynamicAudio = computed(()=>Boolean(dynamicAudioProjectId.value))
  function clearDynamicAudioContext(){dynamicAudioProjectId.value='';dynamicAudioBaselineRevision.value=0;sessionStorage.removeItem('ocv-video-audio-edit')}
  const studioSearch = ref(''), studioFilter = ref('all'), studioSentence = ref(null)
  const studioLogsOpen = ref(false), studioSaveState = ref(''), studioDraftId = ref('')
  const studioDrafts = ref([]), studioProjectId = ref('')
  const studioDraftsExpanded = ref(false)
  const visibleStudioDrafts = computed(()=>studioDraftsExpanded.value?studioDrafts.value:studioDrafts.value.slice(0,3))
  function deleteStudioDraft(id){
    try{const next=studioDrafts.value.filter(d=>d.id!==id);localStorage.setItem(storageKey(),JSON.stringify(next));studioDrafts.value=next}catch{studioError.value='删除草稿失败，请检查浏览器存储。'}
  }
  function clearStudioDrafts(){
    if(!window.confirm('清空所有本机草稿？此操作不会删除已生成的项目和素材，草稿文案清空后无法恢复。'))return
    try{localStorage.removeItem(storageKey());studioDrafts.value=[];studioDraftsExpanded.value=false}catch{studioError.value='清空草稿失败，请检查浏览器存储。'}
  }
  const studioJob = computed(()=>studioKind.value==='audio'?w.module1Job.value:studioKind.value==='subtitle'?w.subtitleJob.value:w.activeJob.value)
  const studioLiveJobs = computed(()=>w.jobs.value.filter(j=>['queued','running'].includes(j.status)))
  const studioJobs = computed(()=>w.jobs.value.filter(j=>(studioFilter.value==='all'||typeOf(j)===studioFilter.value)&&String(j.request?.project_name||j.id).toLowerCase().includes(studioSearch.value.toLowerCase())))
  const studioTabs = computed(()=>studioKind.value==='audio'?['文案','配音','导出','参数回顾']:studioKind.value==='subtitle'?['素材','字幕','导出','参数回顾']:['文案','配音','画面与字幕','导出','参数回顾'])
  const studioSelectedImage = computed(()=>w.visualEditor.value.items.find(i=>i.id===w.visualTimingSelectedId.value)||w.visualEditor.value.items[0])
  const studioSelectedImageIndex = computed(()=>w.visualEditor.value.items.findIndex(i=>i.id===studioSelectedImage.value?.id))
  const canSelectPreviousImage = computed(()=>studioSelectedImageIndex.value>0)
  const canSelectNextImage = computed(()=>studioSelectedImageIndex.value>=0&&studioSelectedImageIndex.value<w.visualEditor.value.items.length-1)
  function selectAdjacentVisualImage(offset){
    const index=studioSelectedImageIndex.value+Number(offset||0),item=w.visualEditor.value.items[index]
    if(item)w.visualTimingSelectedId.value=item.id
  }
  const studioAudio = computed(()=>studioJob.value?.artifacts?.audio||'')
  const studioVideo = computed(() => {
    const url = studioJob.value?.artifacts?.video_with_subtitles
      || studioJob.value?.artifacts?.video_raw
      || studioJob.value?.artifacts?.video
      || ''
    // A finished re-render can replace a file at the same URL. Changing only
    // this query marker reloads the video element without reloading the page.
    const revision = Number(w.visualEditor.value?.preview_version || 0)
    if (!url || !revision) return url
    return `${url}${url.includes('?') ? '&' : '?'}preview=${revision}`
  })
  const studioTaskLogs = computed(()=>studioJob.value?.logs||[])
  const studioReferenceAssets = computed(() => {
    const request = studioJob.value?.request || {}
    return requestReferenceImageIds(request).map((id, index) => {
      const asset = w.editorAssets.value.find((item) => item.id === id)
      return asset || { id, name: `参考图 ${index + 1}`, url: '' }
    })
  })
  const studioTitle = computed(()=>studioPage.value==='new'?(studioKind.value==='subtitle'?w.subtitleForm.project_name:w.form.project_name):studioJob.value?.request?.project_name||'项目工作区')
  const studioHasRunning = computed(()=>studioLiveJobs.value.length>0||['queued','running'].includes(studioJob.value?.status))
  let draftTimer, draftLoading=false, openSerial=0
  const storageKey = ()=>`ocv.studio.drafts.v1:${w.session.value.user?.id||'local'}`
  function readDrafts(){try{studioDrafts.value=JSON.parse(localStorage.getItem(storageKey())||'[]')}catch{studioDrafts.value=[]}}
  watch(()=>w.session.value.user?.id,readDrafts,{immediate:true})
  function typeOf(j){return j.request?.subtitle_only?'subtitle':j.request?.module1_only?'audio':j.request?.dynamic_video?'dynamic':'video'}
  function typeLabel(j){return ({video:'图文视频',dynamic:'动态视频',audio:'仅配音',subtitle:'仅字幕识别'})[typeof j==='string'?j:typeOf(j)]}
  function saveDraft(){
    clearTimeout(draftTimer)
    if(draftLoading||studioPage.value!=='new'||!studioDraftId.value)return
    const hasContent=studioKind.value==='subtitle'
      ? Boolean(w.subtitleForm.source_audio_id||String(w.subtitleForm.reference_text||'').trim())
      : Boolean(w.form.source_audio_id||String(w.form.script||'').trim())
    if(!hasContent&&!studioDrafts.value.some(d=>d.id===studioDraftId.value)){studioSaveState.value='填写文案或上传素材后自动保存草稿';return}
    try{
      // Store creation inputs only. Account forms and API credentials are excluded.
      const form=Object.fromEntries(Object.entries(w.form).filter(([k])=>k!=='auto_analyze_reference_images'&&!/(key|password|token|secret)/i.test(k)))
      const record={id:studioDraftId.value,name:studioTitle.value,kind:studioKind.value,form,subtitle:{...w.subtitleForm},engine:w.ttsEngine.value,updated:new Date().toISOString(),rerun:Boolean(w.form._rerun_source_project),source_project_id:w.form._rerun_source_project||'',source_project_revision:Number(w.form._rerun_source_revision||0),rerun_base:w.form._rerun_base||null,rerun_stages:w.form._rerun_stages||[],source_view:w.form._rerun_return_view||'storyboard'}
      const next=[record,...studioDrafts.value.filter(d=>d.id!==record.id)].slice(0,50)
      localStorage.setItem(storageKey(),JSON.stringify(next));studioDrafts.value=next;studioSaveState.value='草稿已保存到本机'
    }catch{studioSaveState.value='草稿保存失败，请检查浏览器存储空间'}
  }
  watch(()=>[w.form,w.subtitleForm,w.ttsEngine.value],()=>{if(studioPage.value==='new'&&!draftLoading){studioSaveState.value='保存中…';clearTimeout(draftTimer);draftTimer=setTimeout(saveDraft,650)}},{deep:true})
  function goHome(){saveDraft();clearDynamicAudioContext();studioPage.value='home';studioDrawer.value=''}
  function newProject(kind='video',draft=null){
    const dynamic = kind === 'dynamic' || Boolean(draft?.form?.dynamic_video)
    if(kind==='dynamic')kind='video'
    // Leaving an editor releases its UI locks, not its backend task.
    ++openSerial
    clearDynamicAudioContext()
    studioBusy.value=false
    w.stopTtsEditorPolling()
    w.stopVisualEditorTaskPolling()
    w.resetTtsSegmentAudio()
    w.closeTtsBoundaryEditor()
    w.clearGuidedEditingState()
    w.guidedCreatingNew.value=true
    saveDraft();draftLoading=true;studioKind.value=kind;studioDraftId.value=draft?.id||`draft-${Date.now()}`;studioPage.value='new';studioProjectId.value='';studioError.value='';studioDrawer.value='';studioTab.value=kind==='subtitle'?'素材':'文案';w.activePage.value=kind==='audio'?'module1':kind==='subtitle'?'subtitle':'workspace';w.followLiveJob.value=false
    for(const key of ['_rerun_source_project','_rerun_source_revision','_rerun_base','_rerun_base_engine','_rerun_tts_baseline','_rerun_stages','_rerun_return_view','_return_dynamic_stage'])delete w.form[key]
    w.form.reference_image_ids=[];w.form.protagonist_reference_image_id=''
    w.form.reference_image_notes={};w.form.reference_image_labels={};w.form.reference_image_kinds={}
    w.referenceImageNames.value=[];w.protagonistReferenceImageError.value=''
    if(draft){
      const autoAnalyze=w.form.auto_analyze_reference_images;Object.assign(w.form,draft.form)
      w.form._rerun_source_project=draft.rerun?String(draft.source_project_id||''):''
      w.form._rerun_source_revision=Number(draft.source_project_revision||0)
      w.form._rerun_base=draft.rerun_base||null
      w.form._rerun_base_engine=draft.engine||''
      w.form._rerun_stages=draft.rerun_stages||[]
      w.form._rerun_return_view=draft.source_view||'storyboard'
      // Leaving the classic editor through the progress bar must commit the
      // pending configuration first. Previously this jumped straight back to
      // the persisted project and discarded the newly selected ComfyUI preset.
      w.form._return_dynamic_stage=(stage)=>{void launch(stage)}
      w.form.auto_analyze_reference_images=autoAnalyze;Object.assign(w.subtitleForm,draft.subtitle);w.ttsEngine.value=draft.engine||w.ttsEngine.value
    }
    else {
      const modePreference=restoreDefaultsOnNewProject.value?undefined:savedCreationPreferences()?.dynamic_text_mode
      w.form.dynamic_text_mode=normalizeDynamicTextMode(modePreference,DEFAULT_DYNAMIC_TEXT_MODE)
      if(restoreDefaultsOnNewProject.value){w.form.step_mode=false;w.form.visual_pacing_preset='standard';w.form.tts_emotion=''}
      else {const saved=savedCreationPreferences();if(saved){w.form.step_mode=Boolean(saved.step_mode);w.form.visual_pacing_preset=['auto','slow','standard','fast','custom'].includes(saved.visual_pacing_preset)?saved.visual_pacing_preset:w.form.visual_pacing_preset;w.form.tts_emotion=String(saved.tts_emotion||'')}}
      w.form.project_name=w.randomProjectName();w.form.script='';w.form.source_audio_id='';w.form.skip_tts=false;w.form.skip_text_correction=false;w.form.reference_image_ids=[];w.form.protagonist_reference_image_id='';w.referenceImageNames.value=[];w.protagonistReferenceImageError.value='';w.subtitleForm.project_name='字幕_'+new Date().toLocaleDateString();w.subtitleForm.source_audio_id='';w.subtitleForm.reference_text='';w.sourceAudioName.value='';w.scriptUploadName.value='';w.subtitleAudioName.value=''
    }
    if(draft){
      w.form.dynamic_text_mode=normalizeDynamicTextMode(draft.form?.dynamic_text_mode)
      if(dynamic&&!Object.hasOwn(draft.form||{},'dynamic_text_mode'))w.form.scene_references_enabled=draft.form?.director_strategy==='enhanced_beta'&&draft.form?.scene_references_enabled!==false
      w.form.reference_image_ids=requestReferenceImageIds(draft.form||{})
      w.form.protagonist_reference_image_id=w.form.reference_image_ids[0]||''
      for(const field of ['reference_image_notes','reference_image_labels','reference_image_kinds'])w.form[field]={...(draft.form?.[field]||{})}
      w.referenceImageNames.value=w.form.reference_image_ids.map(id=>w.editorAssets.value.find(asset=>asset.id===id)?.name||id)
    }
    w.form.dynamic_video=dynamic
    if(dynamic)w.form.step_mode=true
    if(draft?.rerun&&!w.form._rerun_tts_baseline)w.form._rerun_tts_baseline=rerunTtsSnapshot()
    draftLoading=false;saveDraft()
  }
  async function enterDynamicStoryboard(){
    if(studioBusy.value)return
    studioBusy.value=true;studioError.value=''
    try{
      const existing=await requestJSON('/api/video-studio/by-audio-task/'+encodeURIComponent(studioJob.value.id))
      if(existing.project){
        if(sessionStorage.getItem('ocv-video-audio-edit')===existing.project.id){
          const audioChanged=Number(w.ttsEditor.value?.revision||0)!==Number(dynamicAudioBaselineRevision.value||0)
          if(audioChanged)await requestJSON('/api/video-studio/'+existing.project.id+'/sync-audio',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:existing.project.revision})})
          sessionStorage.removeItem('ocv-video-audio-edit')
        }
        sessionStorage.setItem('ocv-video-open',existing.project.id)
        sessionStorage.removeItem('ocv-video-autoplan')
        dynamicAudioProjectId.value='';dynamicAudioBaselineRevision.value=0
        studioPage.value='videos'
        return
      }
      const result=await requestJSON('/api/video-studio/from-project',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:studioJob.value.id,from_audio_task:true})})
      sessionStorage.setItem('ocv-video-open',result.id)
      sessionStorage.setItem('ocv-video-autoplan',result.id)
      studioPage.value='videos'
    }catch(e){studioError.value=e.message}finally{studioBusy.value=false}
  }
  async function openDynamicAudio(project){
    dynamicAudioProjectId.value=project.id
    sessionStorage.setItem('ocv-video-audio-edit',project.id)
    await openProject({id:project.source_project.id},true)
    dynamicAudioBaselineRevision.value=Number(w.ttsEditor.value?.revision||0)
    studioTab.value='配音'
  }
  async function openProject(job, editDynamicAudio=false){
    if(!editDynamicAudio)clearDynamicAudioContext()
    saveDraft();const serial=++openSerial;studioBusy.value=true;studioError.value='';studioDrawer.value='';studioProjectId.value=job.id
    try{
      const data=await api.job(job.id);if(serial!==openSerial)return
      if(data.request?.dynamic_video&&!editDynamicAudio){
        const existing=await requestJSON('/api/video-studio/by-audio-task/'+encodeURIComponent(data.id))
        if(serial!==openSerial)return
        if(existing.project){
          sessionStorage.setItem('ocv-video-open',existing.project.id)
          sessionStorage.removeItem('ocv-video-autoplan')
          studioPage.value='videos'
          return
        }
      }
      studioKind.value=typeOf(data);w.followLiveJob.value=false;w.activeJob.value=data
      w.activePage.value=studioKind.value==='audio'?'module1':studioKind.value==='subtitle'?'subtitle':'workspace'
      if(studioKind.value==='audio')w.module1Job.value=data
      if(studioKind.value==='subtitle')w.subtitleJob.value=data
      w.visualEditorProjectId.value=data.id;w.visualEditorOpen.value=true
      w.visualEditor.value={items:[],task:{status:'idle'},version:0}
      w.ttsEditor.value={available:false,segments:[],task:{status:'idle'},message:'正在读取…'}
      w.selectedTtsSegmentIndices.value=[];studioSentence.value=null
      if(w.isGuidedWorkflowJob(data)&&data.status!=='completed'){w.hydrateGuidedForm(data);w.guidedCreatingNew.value=false}
      studioPage.value='editor';studioTab.value=studioKind.value==='subtitle'?'字幕':data.status==='completed'?'导出':'配音'
      if(studioKind.value!=='subtitle')await w.loadTtsEditor()
      if(serial!==openSerial)return
      if(studioKind.value==='video')await w.loadVisualEditor({hydrateBgm:true})
      if(serial!==openSerial)return
      if(studioKind.value==='subtitle')await loadStudioSubtitles()
      if(serial!==openSerial)return
      if(w.isGuidedWorkflowJob(data)){
        const stage=data.request?._step_mode_stage
        if(stage==='visual_setup')studioDrawer.value='作品风格'
        if(stage==='visual_review')studioTab.value='画面与字幕'
        if(stage==='render_setup')studioTab.value='导出'
      }
    }catch(e){studioError.value=e.message||'读取项目失败'}finally{
      if(serial===openSerial){
        studioBusy.value=false
        if(studioJob.value?.request?.dynamic_video&&studioJob.value?.request?.dynamic_auto_advance&&studioJob.value?.request?._step_mode_stage==='audio_review')setTimeout(enterDynamicStoryboard,0)
      }
    }
  }
  async function launch(returnStage=''){
    if(typeof returnStage!=='string')returnStage=''
    if(studioBusy.value||studioHasRunning.value)return
    saveDraft();rememberCreationPreferences();studioBusy.value=true;studioError.value=''
    try{
      if(studioKind.value==='video'&&w.form.dynamic_video&&w.form._rerun_source_project){
        const sourceId=String(w.form._rerun_source_project),base=w.form._rerun_base||{}
        const changed=key=>JSON.stringify(w.form[key]??null)!==JSON.stringify(base[key]??null)
        const ttsChanged=JSON.stringify(rerunTtsSnapshot())!==JSON.stringify(w.form._rerun_tts_baseline||rerunTtsSnapshot())
        if(!ttsChanged){
          const visualKeys=['visual_pacing_preset','visual_min_duration','visual_target_duration','visual_max_duration','visual_max_slides','image_profile_id','image_resolution','visual_backend','video_orientation','video_render_variant','subtitle_font','subtitle_size','subtitle_color','subtitle_outline_color','subtitle_outline_width','subtitle_position','subtitle_margin_bottom']
          const parameters={};for(const key of visualKeys)if(Object.hasOwn(w.form,key)&&changed(key))parameters[key]=w.form[key]
          const payload={revision:Number(w.form._rerun_source_revision||0),name:w.form.project_name||'',style:w.form.visual_style_prompt||'',characters:w.form.global_character_prompt||'',world:w.form.story_environment_prompt||'',dynamic_text_mode:w.form.dynamic_text_mode||'visual_first',scene_references_enabled:w.form.scene_references_enabled!==false,video_generation_backend:w.form.video_generation_backend||'api',comfyui_profile_id:w.form.comfyui_profile_id||'',comfyui_h3_prompt_agent:Boolean(w.form.comfyui_h3_prompt_agent),comfyui_reference_audio:Boolean(w.form.comfyui_reference_audio),dynamic_auto_advance:Boolean(w.form.dynamic_auto_advance),parameters}
          const updated=await requestJSON('/api/video-studio/'+encodeURIComponent(sourceId)+'/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
          if(updated.status==='draft'&&!updated.shots?.length)sessionStorage.setItem('ocv-video-autoplan',sourceId)
          studioDrafts.value=studioDrafts.value.filter(d=>d.id!==studioDraftId.value);localStorage.setItem(storageKey(),JSON.stringify(studioDrafts.value))
          sessionStorage.setItem('ocv-video-open',sourceId);sessionStorage.setItem('ocv-video-view',updated.status==='draft'?'storyboard':(returnStage||w.form._rerun_return_view||'storyboard'));studioPage.value='videos';return
        }
      }
      const oldId=studioJob.value?.id
      if(studioKind.value==='audio')await w.submitModule1()
      else if(studioKind.value==='subtitle')await w.submitSubtitleJob()
      else {if(w.form.dynamic_video)w.form.step_mode=true;w.guidedCreatingNew.value=true;await w.submit()}
      const job=studioJob.value
      if(job?.id&&job.id!==oldId){studioDrafts.value=studioDrafts.value.filter(d=>d.id!==studioDraftId.value);localStorage.setItem(storageKey(),JSON.stringify(studioDrafts.value));delete w.form._rerun_source_project;studioPage.value='editor';studioProjectId.value=job.id;studioTab.value=studioKind.value==='subtitle'?'字幕':'配音';w.followLiveJob.value=false;w.visualEditorProjectId.value=job.id;w.visualEditor.value={items:[],task:{status:'idle'},version:0};w.ttsEditor.value={available:false,segments:[],task:{status:'idle'},message:'生成完成后可逐句编辑。'};w.selectedTtsSegmentIndices.value=[];studioSubtitles.value=[]}
      else studioError.value=w.generationSubmitMessage.value||'任务尚未启动，请检查配置和输入。'
    }catch(e){studioError.value=e.message}finally{studioBusy.value=false}
  }
  async function changeStudioPage(n){try{const result=await api.jobs(n,w.JOB_PAGE_SIZE);w.jobs.value=result.jobs||[];w.jobPage.value=result.page;w.jobTotal.value=result.total;w.jobTotalPages.value=result.total_pages}catch(e){studioError.value=e.message}}
  async function reconnectStudio(){studioBusy.value=true;studioError.value='';try{await w.loadSettings();await w.refresh()}catch(e){studioError.value=e.message||'连接失败，请确认 Launcher 已启动服务。'}finally{studioBusy.value=false}}
  async function openLogs(job){if(job)await openProject(job);else saveDraft();studioPage.value='logs'}
  async function refreshEditorData(){if(!studioProjectId.value)return;w.visualEditorProjectId.value=studioProjectId.value;if(studioKind.value==='video')await w.loadVisualEditor({hydrateBgm:true});if(studioKind.value!=='subtitle')await w.loadTtsEditor();else await loadStudioSubtitles()}
  watch(()=>studioJob.value?.status,async(status,previous)=>{if(studioPage.value==='editor'&&studioJob.value?.id===studioProjectId.value&&status!==previous&&['waiting_confirmation','completed','failed'].includes(status)){await refreshEditorData()}})
  watch(()=>studioJob.value?.request?._step_mode_stage,stage=>{
    if(studioPage.value!=='editor'||studioJob.value?.id!==studioProjectId.value)return
    if(stage==='audio_review')studioTab.value='配音'
    if(stage==='visual_setup'){studioTab.value='文案';studioDrawer.value='作品风格'}
    if(stage==='visual_review')studioTab.value='画面与字幕'
    if(stage==='render_setup'){studioTab.value='导出';studioDrawer.value='背景音乐'}
    if(stage==='audio_review'&&studioJob.value?.request?.dynamic_video&&studioJob.value?.request?.dynamic_auto_advance&&!studioBusy.value)enterDynamicStoryboard()
  })
  watch(()=>w.ttsEditor.value.segments,segments=>{if(!segments.some(s=>s.index===studioSentence.value))studioSentence.value=segments[0]?.index??null},{immediate:true})
  async function chooseTab(t){
    studioTab.value=t
    if(t==='导出'&&studioKind.value==='video'&&studioProjectId.value){
      const projectId=studioProjectId.value
      try{
        const latest=await api.job(projectId)
        if(studioProjectId.value===projectId&&w.activeJob.value?.id===projectId)w.activeJob.value=latest
      }catch(error){studioError.value=error.message||'读取最新成片失败，请稍后重试'}
    }
    // A boundary edit may finish while another page is open. Reload the
    // persisted manifest instead of trusting rows already held in memory.
    if(t==='配音')await refreshEditorData()
    if(t==='画面与字幕'&&!w.visualEditor.value.items.length)await refreshEditorData()
  }
  function keydown(e){if(e.key==='Escape')studioDrawer.value=''}
  window.addEventListener('keydown',keydown)
  function beforeUnload(){saveDraft()}
  window.addEventListener('beforeunload',beforeUnload)
  onBeforeUnmount(()=>{saveDraft();clearTimeout(draftTimer);stopVisualSubtitleSplitAudio();window.removeEventListener('keydown',keydown);window.removeEventListener('beforeunload',beforeUnload)})

  // Subtitle-only editing uses a local draft and exports an edited SRT. Original
  // recognition output remains available for the existing subtitle renderer.
  const studioSubtitles=ref([]),studioSubtitleMessage=ref('')
  function parseTime(t){const p=t.replace(',','.').split(':').map(Number);return p[0]*3600+p[1]*60+p[2]}
  function srtTime(t){const ms=Math.round(Math.max(0,Number(t))*1000);return `${String(Math.floor(ms/3600000)).padStart(2,'0')}:${String(Math.floor(ms/60000)%60).padStart(2,'0')}:${String(Math.floor(ms/1000)%60).padStart(2,'0')},${String(ms%1000).padStart(3,'0')}`}
  async function loadStudioSubtitles(){
    studioSubtitles.value=[];studioSubtitleMessage.value='';const job=w.subtitleJob.value,url=job?.artifacts?.subtitle;if(!url)return
    try{const response=await fetch(url,{credentials:'include'});if(!response.ok)throw Error('读取字幕失败');const text=await response.text();studioSubtitles.value=text.replace(/\r/g,'').trim().split(/\n\s*\n/).map(block=>{const lines=block.split('\n'),idx=lines.findIndex(l=>l.includes('-->'));if(idx<0)return null;const match=lines[idx].match(/(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)/);if(!match)return null;return {start:parseTime(match[1]),end:parseTime(match[2]),text:lines.slice(idx+1).join('\n')}}).filter(Boolean)}catch(e){studioSubtitleMessage.value=e.message}
  }
  function exportStudioSubtitles(){
    const rows=studioSubtitles.value;if(rows.some((r,i)=>!Number.isFinite(Number(r.start))||!Number.isFinite(Number(r.end))||r.start<0||r.end<=r.start||(i&&r.start<rows[i-1].end))){studioSubtitleMessage.value='时间需按顺序排列，结束晚于开始，相邻字幕不能重叠。';return}
    const text=rows.map((r,i)=>`${i+1}\n${srtTime(r.start)} --> ${srtTime(r.end)}\n${r.text}\n`).join('\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type:'application/x-subrip;charset=utf-8'}));a.download=`${studioTitle.value}_校对.srt`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);studioSubtitleMessage.value='已导出校对后的字幕。'
  }
  const visualSubtitleSplitDialog=ref({open:false,sentence:null,left_text:'',right_text:'',boundary:0,original_boundary:0})
  const visualPictureInsertBusy=ref(false)
  let subtitleSplitAudio=null,subtitleSplitAudioEnd=0
  function stopVisualSubtitleSplitAudio(){if(!subtitleSplitAudio)return;subtitleSplitAudio.pause();subtitleSplitAudio.removeAttribute('src');subtitleSplitAudio.load();subtitleSplitAudio=null;subtitleSplitAudioEnd=0}
  function closeVisualSubtitleSplit(){stopVisualSubtitleSplitAudio();visualSubtitleSplitDialog.value={open:false,sentence:null,left_text:'',right_text:'',boundary:0,original_boundary:0}}
  watch(()=>w.visualEditorProjectId.value,closeVisualSubtitleSplit)
  function openVisualSubtitleSplit(sentence){
    if(!sentence?.slide_id||w.visualSubtitleSaving.value||w.ttsEditor.value.task?.status==='running')return
    if(w.visualSubtitleDirtyCount.value){w.visualEditor.value.task={status:'failed',action:'subtitle_split',message:'请先保存当前字幕文字修改，再拆分字幕。'};return}
    const text=String(sentence.text||'').trim();if(text.length<2){w.visualEditor.value.task={status:'failed',action:'subtitle_split',message:'这条字幕太短，无法拆成两条。'};return}
    const mid=text.length/2,candidates=[];for(let i=1;i<text.length;i+=1)if('，。！？；：、,.!?;:'.includes(text[i-1]))candidates.push(i)
    const at=candidates.length?candidates.reduce((best,item)=>Math.abs(item-mid)<Math.abs(best-mid)?item:best):Math.max(1,Math.min(text.length-1,Math.round(mid)))
    const start=Number(sentence.start||0),end=Number(sentence.end||0),ratio=Math.max(.15,Math.min(.85,at/text.length))
    const boundary=Number((start+(end-start)*ratio).toFixed(2))
    w.closeVisualSubtitleRemove();w.closeVisualBoundaryAlign();visualSubtitleSplitDialog.value={open:true,sentence,left_text:text.slice(0,at).trim(),right_text:text.slice(at).trim(),boundary,original_boundary:boundary}
  }
  async function playVisualSubtitleSplitRange(start,end){
    if(!w.visualEditorProjectId.value||Number(end)<=Number(start))return
    if(subtitleSplitAudio)subtitleSplitAudio.pause()
    const audio=subtitleSplitAudio||new Audio();subtitleSplitAudio=audio;subtitleSplitAudioEnd=Number(end)
    if(!audio.dataset.splitListener){audio.addEventListener('timeupdate',()=>{if(audio.currentTime>=subtitleSplitAudioEnd-.015)audio.pause()});audio.dataset.splitListener='1'}
    const url=`/api/jobs/${encodeURIComponent(w.visualEditorProjectId.value)}/visual-editor/audio`
    if(audio.dataset.source!==url){audio.src=url;audio.dataset.source=url;audio.load()}
    const begin=Math.max(0,Number(start)||0),play=async()=>{audio.currentTime=begin;try{await audio.play()}catch(e){w.visualEditor.value.task={status:'failed',action:'subtitle_split',message:e?.message||'浏览器暂时无法播放项目配音'}}}
    if(audio.readyState>=1)await play();else audio.addEventListener('loadedmetadata',play,{once:true})
  }
  async function applyVisualSubtitleSplit(){
    const f=visualSubtitleSplitDialog.value,s=f.sentence;if(!w.visualEditorProjectId.value||!s?.slide_id||w.visualSubtitleSaving.value)return
    const left=String(f.left_text||'').trim(),right=String(f.right_text||'').trim();if(!left||!right){w.visualEditor.value.task={status:'failed',action:'subtitle_split',message:'拆分后的两条字幕都需要填写文字。'};return}
    w.visualSubtitleSaving.value=true
    try{w.visualEditor.value=await api.splitVisualSubtitle(w.visualEditorProjectId.value,{slide_id:s.slide_id,left_text:left,right_text:right,boundary:Number(f.boundary)});w.hydrateVisualSubtitleDrafts({preserveDirty:false});closeVisualSubtitleSplit();w.visualEditor.value.task={status:'completed',action:'subtitle_split',message:'已拆成两条字幕；配音保持不变，重新渲染后生效。'}}
    catch(e){w.visualEditor.value.task={status:'failed',action:'subtitle_split',message:e.message||'拆分字幕失败'}}finally{w.visualSubtitleSaving.value=false}
  }
  async function insertVisualPicture(sentence){
    const item=w.selectedVisualTimingItem.value;if(!w.visualEditorProjectId.value||!item||visualPictureInsertBusy.value)return
    if((item.timing?.sentences?.length||0)<2){w.visualEditor.value.task={status:'failed',action:'timing_insert',message:'当前画面只有一条字幕，请先把字幕拆成两条再添加画面。'};return}
    visualPictureInsertBusy.value=true
    try{const result=await api.insertVisualTimingPicture(w.visualEditorProjectId.value,item.id,sentence.slide_id);w.visualEditor.value=result;w.hydrateVisualSubtitleDrafts({preserveDirty:false});const added=result.items.find(row=>row.id.startsWith('poster_added_')&&row.slides?.includes(sentence.slide_id));w.visualTimingSelectedId.value=added?.id||item.id;w.visualEditor.value.task={status:'completed',action:'timing_insert',message:'黑色占位画面已加入并选中；现在可以填写提示词重绘、上传参考图或替换本地图片。'}}
    catch(e){w.visualEditor.value.task={status:'failed',action:'timing_insert',message:e.message||'添加画面失败'}}finally{visualPictureInsertBusy.value=false}
  }
return {openDynamicAudio,enterDynamicStoryboard,editingDynamicAudio,restoreDefaultsOnNewProject,duplicateStudioProject,resetStudioProject,studioDraftsExpanded,visibleStudioDrafts,deleteStudioDraft,clearStudioDrafts,studioPage,studioTab,studioDrawer,studioKind,studioError,studioBusy,studioSearch,studioFilter,studioSentence,studioLogsOpen,studioSaveState,studioDrafts,studioJobs,studioJob,studioLiveJobs,studioTabs,studioSelectedImage,studioSelectedImageIndex,canSelectPreviousImage,canSelectNextImage,selectAdjacentVisualImage,studioAudio,studioVideo,studioTaskLogs,studioReferenceAssets,studioTitle,studioHasRunning,typeOf,typeLabel,goHome,newProject,openProject,launch,changeStudioPage,openLogs,refreshEditorData,reconnectStudio,chooseTab,studioSubtitles,studioSubtitleMessage,exportStudioSubtitles,visualSubtitleSplitDialog,visualPictureInsertBusy,openVisualSubtitleSplit,closeVisualSubtitleSplit,playVisualSubtitleSplitRange,applyVisualSubtitleSplit,insertVisualPicture}
}
