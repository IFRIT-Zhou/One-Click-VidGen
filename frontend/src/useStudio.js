import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { api } from './api'

// Navigation never cancels a task. A single useWorkspace instance keeps polling.
export function useStudio(w) {
  const creationDefaults = JSON.parse(JSON.stringify(w.form))
  const subtitleDefaults = JSON.parse(JSON.stringify(w.subtitleForm))
  function requestReferenceImageIds(request) {
    const ids = Array.isArray(request.reference_image_ids)
      ? request.reference_image_ids.map((value) => String(value || '').trim()).filter(Boolean)
      : []
    const legacyId = String(request.protagonist_reference_image_id || '').trim()
    if (!ids.length && legacyId) ids.push(legacyId)
    return [...new Set(ids)].slice(0, 3)
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
    return `${url}${url.includes('?') ? '&' : '?'}v=${revision}`
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
  function typeOf(j){return j.request?.subtitle_only?'subtitle':j.request?.module1_only?'audio':'video'}
  function typeLabel(j){return ({video:'图文视频',audio:'仅配音',subtitle:'仅字幕识别'})[typeof j==='string'?j:typeOf(j)]}
  function saveDraft(){
    clearTimeout(draftTimer)
    if(draftLoading||studioPage.value!=='new'||!studioDraftId.value)return
    const hasContent=studioKind.value==='subtitle'
      ? Boolean(w.subtitleForm.source_audio_id||String(w.subtitleForm.reference_text||'').trim())
      : Boolean(w.form.source_audio_id||String(w.form.script||'').trim())
    if(!hasContent&&!studioDrafts.value.some(d=>d.id===studioDraftId.value)){studioSaveState.value='填写文案或上传素材后自动保存草稿';return}
    try{
      // Store creation inputs only. Account forms and API credentials are excluded.
      const form=Object.fromEntries(Object.entries(w.form).filter(([k])=>!/(key|password|token|secret)/i.test(k)))
      const record={id:studioDraftId.value,name:studioTitle.value,kind:studioKind.value,form,subtitle:{...w.subtitleForm},engine:w.ttsEngine.value,updated:new Date().toISOString()}
      const next=[record,...studioDrafts.value.filter(d=>d.id!==record.id)].slice(0,50)
      localStorage.setItem(storageKey(),JSON.stringify(next));studioDrafts.value=next;studioSaveState.value='草稿已保存到本机'
    }catch{studioSaveState.value='草稿保存失败，请检查浏览器存储空间'}
  }
  watch(()=>[w.form,w.subtitleForm,w.ttsEngine.value],()=>{if(studioPage.value==='new'&&!draftLoading){studioSaveState.value='保存中…';clearTimeout(draftTimer);draftTimer=setTimeout(saveDraft,650)}},{deep:true})
  function goHome(){saveDraft();studioPage.value='home';studioDrawer.value=''}
  function newProject(kind='video',draft=null){
    // Leaving an editor releases its UI locks, not its backend task.
    ++openSerial
    studioBusy.value=false
    w.stopTtsEditorPolling()
    w.stopVisualEditorTaskPolling()
    w.resetTtsSegmentAudio()
    w.closeTtsBoundaryEditor()
    w.clearGuidedEditingState()
    w.guidedCreatingNew.value=true
    saveDraft();draftLoading=true;studioKind.value=kind;studioDraftId.value=draft?.id||`draft-${Date.now()}`;studioPage.value='new';studioProjectId.value='';studioError.value='';studioDrawer.value='';studioTab.value=kind==='subtitle'?'素材':'文案';w.activePage.value=kind==='audio'?'module1':kind==='subtitle'?'subtitle':'workspace';w.followLiveJob.value=false
    if(draft){Object.assign(w.form,draft.form);Object.assign(w.subtitleForm,draft.subtitle);w.ttsEngine.value=draft.engine||w.ttsEngine.value}
    else {w.form.project_name=w.randomProjectName();w.form.script='';w.form.source_audio_id='';w.form.skip_tts=false;w.form.skip_text_correction=false;w.form.reference_image_ids=[];w.form.protagonist_reference_image_id='';w.referenceImageNames.value=[];w.protagonistReferenceImageError.value='';w.subtitleForm.project_name='字幕_'+new Date().toLocaleDateString();w.subtitleForm.source_audio_id='';w.subtitleForm.reference_text='';w.sourceAudioName.value='';w.scriptUploadName.value='';w.subtitleAudioName.value=''}
    draftLoading=false;saveDraft()
  }
  async function openProject(job){
    saveDraft();const serial=++openSerial;studioBusy.value=true;studioError.value='';studioDrawer.value='';studioProjectId.value=job.id
    try{
      const data=await api.job(job.id);if(serial!==openSerial)return
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
    }catch(e){studioError.value=e.message||'读取项目失败'}finally{if(serial===openSerial)studioBusy.value=false}
  }
  async function launch(){
    if(studioBusy.value||studioHasRunning.value)return
    saveDraft();studioBusy.value=true;studioError.value=''
    try{
      const oldId=studioJob.value?.id
      if(studioKind.value==='audio')await w.submitModule1()
      else if(studioKind.value==='subtitle')await w.submitSubtitleJob()
      else {w.guidedCreatingNew.value=true;await w.submit()}
      const job=studioJob.value
      if(job?.id&&job.id!==oldId){studioDrafts.value=studioDrafts.value.filter(d=>d.id!==studioDraftId.value);localStorage.setItem(storageKey(),JSON.stringify(studioDrafts.value));studioPage.value='editor';studioProjectId.value=job.id;studioTab.value=studioKind.value==='subtitle'?'字幕':'配音';w.followLiveJob.value=false;w.visualEditorProjectId.value=job.id;w.visualEditor.value={items:[],task:{status:'idle'},version:0};w.ttsEditor.value={available:false,segments:[],task:{status:'idle'},message:'生成完成后可逐句编辑。'};w.selectedTtsSegmentIndices.value=[];studioSubtitles.value=[]}
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
  })
  watch(()=>w.ttsEditor.value.segments,segments=>{if(!segments.some(s=>s.index===studioSentence.value))studioSentence.value=segments[0]?.index??null},{immediate:true})
  async function chooseTab(t){studioTab.value=t;if(t==='配音'&&!w.ttsEditor.value.available)await refreshEditorData();if(t==='画面与字幕'&&!w.visualEditor.value.items.length)await refreshEditorData()}
  function keydown(e){if(e.key==='Escape')studioDrawer.value=''}
  window.addEventListener('keydown',keydown)
  function beforeUnload(){saveDraft()}
  window.addEventListener('beforeunload',beforeUnload)
  onBeforeUnmount(()=>{saveDraft();clearTimeout(draftTimer);window.removeEventListener('keydown',keydown);window.removeEventListener('beforeunload',beforeUnload)})

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
return {duplicateStudioProject,resetStudioProject,studioDraftsExpanded,visibleStudioDrafts,deleteStudioDraft,clearStudioDrafts,studioPage,studioTab,studioDrawer,studioKind,studioError,studioBusy,studioSearch,studioFilter,studioSentence,studioLogsOpen,studioSaveState,studioDrafts,studioJobs,studioJob,studioLiveJobs,studioTabs,studioSelectedImage,studioAudio,studioVideo,studioTaskLogs,studioReferenceAssets,studioTitle,studioHasRunning,typeOf,typeLabel,goHome,newProject,openProject,launch,changeStudioPage,openLogs,refreshEditorData,reconnectStudio,chooseTab,studioSubtitles,studioSubtitleMessage,exportStudioSubtitles}
}
