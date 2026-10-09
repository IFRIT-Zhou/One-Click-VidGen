(function (root) {
  "use strict";
  const storeKey = "ocvg.projects.v1";
  const arrays = (value) => Array.isArray(value) ? value : Object.entries(value || {}).flatMap(([sceneId, list]) => (Array.isArray(list) ? list : list?.versions || []).map(item => ({ ...item, sceneId: item.sceneId || sceneId })));
  function scenesFor(project) {
    const images = arrays(project.imageVersions), clips = arrays(project.videoClips);
    return (project.scenes || []).map((scene, index) => {
      const image = images.find(item => String(item.id) === String(scene.currentImageVersionId) && String(item.sceneId) === String(scene.id));
      const clip = clips.find(item => String(item.id) === String(scene.currentVideoClipId) && String(item.sceneId) === String(scene.id));
      const audio = (project.voiceLines?.length ? project.voiceLines : project.audio || []).filter(item => item.sceneId === scene.id);
      return { scene, index, image, clip, audio };
    });
  }
  function subtitleText(cues) {
    const stamp = seconds => { const total = Math.round(seconds * 1000); return `${String(Math.floor(total / 3600000)).padStart(2,"0")}:${String(Math.floor(total / 60000) % 60).padStart(2,"0")}:${String(Math.floor(total / 1000) % 60).padStart(2,"0")},${String(total % 1000).padStart(3,"0")}`; };
    return cues.filter(cue => !cue.hidden && cue.text && cue.end > cue.start).map((cue,i)=>`${i+1}\n${stamp(cue.start)} --> ${stamp(cue.end)}\n${cue.text}`).join("\n\n");
  }
  function projectFingerprint(value) {
    const clean = input => Array.isArray(input) ? input.map(clean) : input && typeof input === "object" ? Object.fromEntries(Object.entries(input).filter(([key]) => !["exports", "updatedAt"].includes(key)).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, clean(item)])) : input;
    return JSON.stringify(clean(Object.fromEntries(["script", "scriptScenesOutOfSync", "scenes", "voiceLines", "audio", "imageVersions", "videoClips", "subtitles", "subtitleStyle", "aspectRatio"].map(key => [key, value?.[key]]))));
  }
  function sceneReadiness(project, files = new Map(), account = null) {
    const issues = [], entries = scenesFor(project || {});
    if (!entries.length) return [{ text: "还没有分镜，请先完善分镜，再准备画面和配音。", page: "workspace" }];
    if (project.scriptScenesOutOfSync) return [{ text: "脚本与镜头旁白尚未同步，请先确认使用哪一版内容，再导出。", page: "workspace" }];
    if (entries.length > 16) issues.push({ text: "当前超过 16 个镜头，请先调整分镜数量。", page: "workspace" });
    for (const item of entries) {
      const prefix = `镜头 ${item.index + 1}「${item.scene.title || "未命名"}」`, file = files.get(item.scene.id);
      const add = (text, page) => issues.push({ text: `${prefix}：${text}`, page, sceneId: item.scene.id });
      const selectedVideo = Boolean(item.scene.currentVideoClipId), isVideo = file ? file.type.startsWith("video/") : selectedVideo;
      if (file && !/^(video\/(mp4|webm|quicktime)|image\/(jpeg|png|webp))$/.test(file.type)) add("请选择 JPG、PNG、WebP 图片或 MP4、WebM、MOV 视频。", "visual-editor");
      if (!file) {
        if (selectedVideo && item.scene.videoStale) add("画面改动后，原视频片段已失效，请重新选择素材。", "visual-editor");
        else if (selectedVideo && !item.clip) add("采用的视频版本已不存在，请重新选择素材。", "visual-editor");
        else if (selectedVideo && (item.clip.localOnly || !(item.clip.url || item.clip.videoUrl))) add("本地视频需要重新选择，可在本镜头下方补齐。", "local");
        else if (!selectedVideo && (!item.image?.url || item.image.status !== "ready")) add("尚未采用可用图片，请在画面页选择版本。", "visual-editor");
        const visual = selectedVideo ? item.clip : item.image;
        if (visual?.account && account && visual.account !== account) add("当前画面属于其他账号，请切回原账号或重新选择素材。", "visual-editor");
      }
      if (!isVideo && !item.audio.length && String(item.scene.narration || "").trim()) add("旁白还没有配音，请先生成本镜头的配音。", "voice-editor");
      else if (!isVideo && item.audio.some(line => !line.cloudAudio?.url || line.status !== "generated")) add("有未生成或修改后未重配的句段，请先完成配音。", "voice-editor");
      else if (!isVideo && item.audio.some(line => line.cloudAudio?.account && account && line.cloudAudio.account !== account)) add("配音属于其他账号，请切回原账号或重新生成。", "voice-editor");
    }
    return issues;
  }
  root.OCVGTimeline = { scenesFor, subtitleText, sceneReadiness };
  if (!root.document) return;
  const $ = selector => document.querySelector(selector), client = VoiceCloud.createClient();
  const projectId = new URLSearchParams(location.search).get("project");
  let selectedFiles = new Map(), durations = new Map(), durationBaselines = new Map(), musicSettings = new Map(), busy = false, pendingChecked = false;
  function project() { return OCVGProjectStore.unpack(JSON.parse(localStorage.getItem(storeKey) || "[]")).find(item => item.id === projectId); }
  function say(text) { $("#timeline-message").textContent = text; }
  function pendingSubmission() {
    const owner = client.session()?.user?.id;
    if (!owner) return null;
    try { return JSON.parse(localStorage.getItem(`ocvg.timeline-pending:${owner}:${projectId}`) || "null"); } catch (_) { return null; }
  }
  function refreshSubmissionState() {
    const blocked = sceneReadiness(project(), selectedFiles, client.session()?.user?.id).length > 0;
    $("#timeline-export").disabled = busy || blocked;
    $("#timeline-clean").disabled = busy || blocked;
    const pending = Boolean(pendingSubmission());
    $("#timeline-pending")?.classList?.toggle("hidden", !pending);
    $("#timeline-clear-pending")?.classList?.toggle("hidden", !pending || !pendingChecked);
    if (typeof CustomEvent === "function") document.dispatchEvent(new CustomEvent("ocvg:timeline-ready"));
  }
  function renderReadiness(current) {
    const panel = $("#timeline-readiness"); panel.replaceChildren();
    const issues = sceneReadiness(current, selectedFiles, client.session()?.user?.id);
    panel.classList.toggle("needs-materials", issues.length > 0);
    const heading = document.createElement("strong"); heading.textContent = issues.length ? "先补齐这些内容" : `已准备 ${scenesFor(current).length} 个镜头，可以导出`; panel.append(heading);
    for (const issue of issues) {
      const row = document.createElement("div"), text = document.createElement("span"), link = document.createElement("a");
      text.textContent = issue.text; link.className = "readiness-link";
      link.href = issue.page === "local" ? `#timeline-scene-${encodeURIComponent(issue.sceneId)}` : `/${issue.page}/?project=${encodeURIComponent(projectId)}${issue.sceneId ? `&scene=${encodeURIComponent(issue.sceneId)}` : ""}`;
      link.textContent = issue.page === "voice-editor" ? "去补配音" : issue.page === "workspace" ? "去完善分镜" : issue.page === "local" ? "选择文件" : "去补画面";
      row.append(text, link); panel.append(row);
    }
    if (!current?.scenes?.length) {
      const alternative = document.createElement("button"); alternative.className = "ghost-button"; alternative.textContent = "也可以剪辑已有视频";
      alternative.addEventListener("click", () => document.dispatchEvent(new CustomEvent("ocvg:edit-existing"))); panel.append(alternative);
    }
    refreshSubmissionState();
  }
  function musicKey(file, index) { return `${index}:${file.name}:${file.size}:${file.lastModified}`; }
  function settingsForMusic(file, index) {
    const key = musicKey(file, index);
    if (!musicSettings.has(key)) musicSettings.set(key, { volume: Number($("#timeline-bgm-volume").value), delay: 0, loop: true, fade: 1 });
    return musicSettings.get(key);
  }
  function renderMusic() {
    const list = $("#timeline-music-list"); if (!list) return;
    list.replaceChildren();
    const files = [...$("#timeline-bgm").files];
    if (files.length > 3) say("最多选择 3 条配乐，请重新选择；当前文件不会提交。");
    files.slice(0, 3).forEach((file, index) => {
      const row = document.createElement("article"); row.className = "timeline-music-track";
      const name = document.createElement("strong"); name.textContent = `${index + 1} · ${file.name}`; row.append(name);
      const settings = settingsForMusic(file, index);
      for (const [key, text, maximum] of [["volume", "音量", 2], ["delay", "延迟（秒）", 600], ["fade", "淡入淡出（秒）", 30], ["loop", "循环", 0]]) {
        const label = document.createElement("label"); label.textContent = text;
        const input = document.createElement("input"); input.type = key === "loop" ? "checkbox" : "number";
        input.setAttribute("aria-label", `配乐 ${index + 1} ${text}`);
        if (key === "loop") input.checked = settings.loop;
        else { input.value = settings[key]; input.min = "0"; input.max = String(maximum); input.step = "0.1"; }
        input.addEventListener("input", () => { settings[key] = key === "loop" ? input.checked : Number(input.value); });
        label.append(input); row.append(label);
      }
      list.append(row);
    });
  }
  function saveDuration(sceneId, value) {
    const duration = Number(value);
    if (!Number.isFinite(duration) || duration < .1 || duration > 600) { say("镜头时长须为 0.1–600 秒，尚未保存。"); return false; }
    try {
      const base = { id: projectId, scenes: [{ id: sceneId, duration: durationBaselines.get(sceneId) }] };
      const edited = { id: projectId, scenes: [{ id: sceneId, duration }] };
      OCVGProjectStore.save(localStorage, storeKey, base, edited, ["scenes"]);
      durationBaselines.set(sceneId, duration); durations.delete(sceneId);
      say("镜头时长已保存到本机；保存到云端时会包含该修改。");
      return true;
    } catch (error) { say(`镜头时长未保存：${error.message} 当前输入仍保留，请核对其它页面修改。`); return false; }
  }
  function flushDurations() {
    for (const [sceneId, value] of [...durations]) if (!saveDuration(sceneId, value)) return false;
    return true;
  }
  function render() {
    const current = project(), list = $("#timeline-scenes"); list.replaceChildren();
    if (!current) { renderReadiness({ scenes: [] }); return say("请从项目工作台选择一个项目。"); }
    for (const item of scenesFor(current)) {
      if (!durationBaselines.has(item.scene.id) || !durations.has(item.scene.id)) durationBaselines.set(item.scene.id, item.scene.duration);
      const row = document.createElement("article"); row.className = "timeline-scene"; row.id = `timeline-scene-${encodeURIComponent(item.scene.id)}`;
      const title = document.createElement("strong"); title.textContent = `${String(item.index + 1).padStart(2,"0")} · ${item.scene.title || "未命名镜头"}`;
      const detail = document.createElement("small"); detail.textContent = selectedFiles.has(item.scene.id) ? `本次使用：${selectedFiles.get(item.scene.id).name}` : item.clip ? `视频片段：${item.clip.name || "已采用版本"}${item.clip.localOnly ? "（请重新选择文件）" : ""}` : `${item.image?.url ? "已选图片" : "缺少图片"} · ${item.audio.filter(line => line.cloudAudio).length} 句云端配音`;
      const label = document.createElement("label"); label.textContent = "镜头秒数 "; const duration = document.createElement("input"); duration.type="number";duration.min="0.1";duration.max="600";duration.step="0.1";duration.value=durations.get(item.scene.id)??(Number(item.scene.duration)||5);duration.dataset.duration=item.scene.id;duration.setAttribute("aria-label",`${item.scene.title}镜头秒数`);duration.addEventListener("input",()=>durations.set(item.scene.id,duration.value));duration.addEventListener("change",()=>saveDuration(item.scene.id,duration.value));label.append(duration);
      const upload = document.createElement("label"); upload.className="ghost-button";upload.textContent="替换本次素材";const input=document.createElement("input");input.type="file";input.accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime";input.setAttribute("aria-label",`${item.scene.title}替换本次素材`);input.addEventListener("change",()=>{if(input.files[0])selectedFiles.set(item.scene.id,input.files[0]);render();});upload.append(input);
      row.append(title,detail,label,upload);list.append(row);
    }
    renderReadiness(current);
  }
  async function media(url, account, kind) {
    if (!url) throw new Error(`缺少${kind}素材，请先完成生成或选择本地文件。`);
    if (account && account !== client.session()?.user?.id) throw new Error(`${kind}素材属于其他账号，请切回原账号。`);
    if (!account && (/^\/api\//i.test(url) || /^https?:\/\//i.test(url))) throw new Error(`${kind}云端素材缺少所属账号，请重新生成或下载后手动选择。`);
    if (/^data:image\/(png|jpeg|webp);base64,/i.test(url)) { const response = await fetch(url); return response.blob(); }
    if (/^(blob:|file:)/i.test(url)) throw new Error("本地素材已失效，请重新选择文件。");
    return client.request(url, {}, true);
  }
  async function mediaDuration(blob) {
    return new Promise((resolve,reject)=>{const audio=document.createElement("audio"),url=URL.createObjectURL(blob);const cleanup=()=>{clearTimeout(timer);URL.revokeObjectURL(url);audio.removeAttribute("src");audio.load();};const timer=setTimeout(()=>{cleanup();reject(new Error("无法读取配音时长，请重新生成音频。"));},10000);audio.onloadedmetadata=()=>{const duration=audio.duration;cleanup();Number.isFinite(duration)&&duration>0?resolve(duration):reject(new Error("配音时长无效"));};audio.onerror=()=>{cleanup();reject(new Error("配音文件无法解码。"));};audio.src=url;});
  }
  async function submit(clean) {
    if(busy)return;busy=true;$("#timeline-export").disabled=true;$("#timeline-clean").disabled=true;
    try {
      const owner=client.session()?.user?.id;if(!owner)throw new Error("请先在云端工作台登录。");
      if(!flushDurations())throw new Error("镜头时长尚未保存，已停止合成，请先处理时长保存提示。");
      const current=project();if(!current)throw new Error("项目已移除。");
      const inputFingerprint = projectFingerprint(current);
      const entries=scenesFor(current);if(!entries.length||entries.length>16)throw new Error("一次合成需要 1–16 个镜头。");
      const issues=sceneReadiness(current,selectedFiles,owner);if(issues.length)throw new Error(issues[0].text);
      const form=new FormData(),manifest={orientation:current.aspectRatio==="9:16"?"portrait":"landscape",scenes:[]},cues=[];let assetIndex=0,cursor=0,bytes=0;
      const music=[...$("#timeline-bgm").files];if(music.length>3)throw new Error("一次最多选择 3 条配乐。");
      for(const file of music)if(file.size>30*1024*1024)throw new Error("每条配乐不能超过 30 MiB。");
      const add=(blob,name)=>{bytes+=blob.size;if(bytes>230*1024*1024)throw new Error("素材总量超过 230 MiB，请压缩素材或减少镜头。");if(assetIndex>=48)throw new Error("一次最多合成 48 个素材，请减少镜头或句段。");form.append("assets",blob,name);return assetIndex++;};
      for(const entry of entries){
        say(`正在准备镜头 ${entry.index+1}/${entries.length} 的图片和配音…`);
        const file=selectedFiles.get(entry.scene.id),isVideo=file?file.type.startsWith("video/"):Boolean(entry.clip);
        if(!file&&entry.scene.currentVideoClipId&&entry.scene.videoStale)throw new Error(`镜头 ${entry.index+1} 的视频片段已因画面修改失效，请重新选择本次素材。`);
        if(!file&&entry.scene.currentVideoClipId&&!entry.clip)throw new Error(`镜头 ${entry.index+1} 采用的视频版本已不存在，请重新选择素材。`);
        if(!file&&!isVideo&&(!entry.image||entry.image.status!=="ready"))throw new Error(`镜头 ${entry.index+1} 没有可用的已采用图片，请先在视觉页选择版本。`);
        if(file&&!/^(video\/(mp4|webm|quicktime)|image\/(jpeg|png|webp))$/.test(file.type))throw new Error("请选择 JPG、PNG、WebP 图片或 MP4、WebM、MOV 视频。");
        const visual=file||await media(isVideo?(entry.clip.url||entry.clip.videoUrl):entry.image?.url,isVideo?entry.clip.account:entry.image?.account,isVideo?"视频":"图片");
        const extension=isVideo?(file?.name.split(".").pop()||"mp4"):visual.type.includes("png")?"png":visual.type.includes("webp")?"webp":"jpg";
        const scene={duration:Number(document.querySelector(`[data-duration="${CSS.escape(entry.scene.id)}"]`)?.value??entry.scene.duration??5),pause:0};
        if(!Number.isFinite(scene.duration)||scene.duration<0.1||scene.duration>600)throw new Error(`镜头 ${entry.index+1} 时长须为 0.1–600 秒。`);
        scene[isVideo?"video_index":"image_index"]=add(visual,`scene-${entry.index}.${extension}`);
        const lines=[];
        if(!isVideo&&entry.audio.length){scene.audio_indices=[];scene.audio_pauses=[];let voiced=0;
          for(const line of entry.audio){
            if(!line.cloudAudio||line.status!=="generated")throw new Error(`镜头 ${entry.index+1} 有未生成或修改后未重配的句段，请先在配音页生成。`);
            const blob=await media(line.cloudAudio.url,line.cloudAudio.account,"配音"),duration=await mediaDuration(blob),pause=Math.max(0,Math.min(5,Number(line.pause??400)/1000));
            scene.audio_indices.push(add(blob,`voice-${assetIndex}.wav`));scene.audio_pauses.push(pause);lines.push({text:line.subtitleText||line.speechText,start:cursor+voiced,end:cursor+voiced+duration});voiced+=duration+pause;
          }
          scene.duration=Math.max(scene.duration,Math.ceil(voiced*100)/100);
        }
        manifest.scenes.push(scene);
        if($("#timeline-retime").checked){if(lines.length)cues.push(...lines);else if(entry.scene.narration)cues.push({text:entry.scene.narration,start:cursor,end:cursor+scene.duration});}
        cursor+=scene.duration;if(cursor>600)throw new Error("时间线超过 10 分钟，请缩短镜头。");
      }
      if(!$("#timeline-retime").checked)cues.push(...(current.subtitles||[]));
      const captions=clean?"":subtitleText(cues);
      manifest.music=music.map((file,index)=>{
        const settings=settingsForMusic(file,index);
        if(!Number.isFinite(settings.volume)||settings.volume<0||settings.volume>2||!Number.isFinite(settings.delay)||settings.delay<0||settings.delay>600||!Number.isFinite(settings.fade)||settings.fade<0||settings.fade>30)throw new Error(`配乐 ${index+1} 的音量、延迟或淡入淡出参数无效。`);
        return {audio_index:add(file,`music-${index}.${file.name.split(".").pop()}`),...settings};
      });
      form.append("manifest",JSON.stringify(manifest));form.append("burn_subtitles",String(Boolean(captions)));
      form.append("subtitle_style",JSON.stringify(current.subtitleStyle||{}));
      if(captions)form.append("subtitles",new File([captions],"timeline.srt",{type:"application/x-subrip"}));
      for(const [key,value]of Object.entries({source_volume:1,bgm_volume:Number($("#timeline-bgm-volume").value),bgm_delay:0,bgm_loop:true,bgm_fade:1}))form.append(key,String(value));
      const signature=JSON.stringify({manifest,captions,subtitleStyle:current.subtitleStyle||{},music:music.map(file=>[file.name,file.size,file.lastModified]),sceneAssets:entries.map(entry=>[entry.image?.id,entry.image?.url,entry.clip?.id,entry.clip?.url||entry.clip?.videoUrl,selectedFiles.get(entry.scene.id)?.name,selectedFiles.get(entry.scene.id)?.size,selectedFiles.get(entry.scene.id)?.lastModified,entry.audio.map(line=>[line.id,line.cloudAudio?.url,line.cloudAudio?.jobId,line.cloudAudio?.account,line.pause,line.speechText])]),volume:$("#timeline-bgm-volume").value});
      if (projectFingerprint(project()) !== inputFingerprint) throw new Error("项目在准备期间已修改，旧提交已停止；请重新检查素材后再导出。");
      const key=`ocvg.timeline-pending:${owner}:${projectId}`;let pending=JSON.parse(localStorage.getItem(key)||"null");
      if(pending&&(pending.signature!==signature||pending.draftFingerprint!==inputFingerprint))throw new Error("上次合成提交尚待核对。请先查看上次任务，确认没有重复导出后再修改设置提交。");
      if(!pending){pending={id:`timeline-${crypto.randomUUID()}`,signature,draftFingerprint:inputFingerprint};localStorage.setItem(key,JSON.stringify(pending));pendingChecked=false;}
      if(client.session()?.user?.id!==owner)throw new Error("账号已切换，停止提交。");
      say("正在上传并合成时间线…");
      const job=await client.request("/editor/timeline",{method:"POST",headers:{"Idempotency-Key":pending.id},body:form});
      if(client.session()?.user?.id!==owner)throw new Error("账号已切换，提交结果请切回原账号核对。");
      const raw=JSON.parse(localStorage.getItem(storeKey)||"[]"),saved=OCVGProjectStore.unpack(raw).find(item=>item.id===projectId), changedAfterSubmit = saved && projectFingerprint(saved) !== inputFingerprint;
      if(saved){saved.exports=[{...job,account:owner,kind:"timeline",url:job.download_url,stale:Boolean(changedAfterSubmit)},...(saved.exports||[]).filter(record=>record.id!==job.id)];saved.updatedAt=new Date().toISOString();localStorage.setItem(storeKey,JSON.stringify(raw));}
      localStorage.removeItem(key);
      say(changedAfterSubmit ? "时间线已提交，但提交后项目有新修改；该任务已标为修改前版本。请按当前内容重新导出。" : `时间线已提交 · ${Math.round(cursor*10)/10} 秒。请在云端导出记录查看和下载 MP4。`);$("#reload-exports").click();
    }catch(error){say(error.message);}finally{busy=false;refreshSubmissionState();}
  }
  document.addEventListener("ocvg:flush-project",event=>{if(!flushDurations())event.preventDefault();});
  $("#timeline-bgm").addEventListener("change",renderMusic);
  $("#timeline-bgm-volume").addEventListener("input",()=>{for(const settings of musicSettings.values())settings.volume=Number($("#timeline-bgm-volume").value);renderMusic();});
  $("#timeline-refresh").addEventListener("click",render);
  $("#timeline-export").addEventListener("click",()=>submit($("#timeline-output").value==="clean"));$("#timeline-clean").addEventListener("click",()=>submit(true));
  $("#timeline-check-pending").addEventListener("click",()=>{$("#reload-exports").click();$("#export-history").scrollIntoView({behavior:"smooth"});});
  document.addEventListener("ocvg:exports-loaded",event=>{
    const pending=pendingSubmission();if(!pending||event.detail.account!==client.session()?.user?.id)return;
    if(event.detail.jobs.some(job=>job.client_id===pending.id)){
      localStorage.removeItem(`ocvg.timeline-pending:${event.detail.account}:${projectId}`);pendingChecked=false;say("已找到上次导出任务，请在导出记录中查看结果。");
    }else{pendingChecked=true;say("当前导出记录中没有找到上次任务。请核对后再决定是否重新提交。");}
    refreshSubmissionState();
  });
  $("#timeline-clear-pending").addEventListener("click",()=>{const owner=client.session()?.user?.id;if(!owner||!pendingChecked)return;if(confirm("已查询导出记录且未找到上次任务，确定允许重新提交？新提交会创建一个导出任务。")){localStorage.removeItem(`ocvg.timeline-pending:${owner}:${projectId}`);pendingChecked=false;refreshSubmissionState();say("可以重新提交，已完成的导出仍可在记录中下载。");}});
  render();
})(globalThis);
