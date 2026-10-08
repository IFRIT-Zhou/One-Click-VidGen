(() => {
  "use strict";

  const STORAGE_KEY = "ocvg.projects.v1";
  const $ = (selector, root = document) => root.querySelector(selector);
  const uid = (prefix) => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const state = { store: null, project: null, projectId: "", scenes: [], clips: {}, edit: null, bgm: {}, bgmTracks: [], sourceObjectUrl: "", bgmObjectUrl: "", subtitleObjectUrl: "", clipUrls: new Map(), generatedTrack: null, previewingSelection: false };
  const cloud = VoiceCloud.createClient();
  const uploadFiles = { video: null, bgm: null, subtitles: null };
  const clipFiles = new Map(), changedClipScenes = new Set();
  let exportBusy = false, exportTimer = null;
  let editorMode = "project";

  function setEditorMode(mode) {
    editorMode = mode === "existing" ? "existing" : "project";
    document.querySelectorAll("[data-editor-mode]").forEach(node => node.classList.toggle("hidden", node.dataset.editorMode !== editorMode));
    for (const value of ["project", "existing"]) {
      const tab = $(`#mode-${value}`), active = value === editorMode;
      tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1;
    }
    const primary = $("#primary-export");
    primary.textContent = editorMode === "project" ? "导出项目视频" : "导出已有视频";
    primary.disabled = editorMode === "project" && $("#timeline-export").disabled;
    if (editorMode === "project") { $("#video-preview").pause(); $("#bgm-preview").pause(); }
  }

  function bindEditorModes() {
    for (const mode of ["project", "existing"]) {
      const tab = $(`#mode-${mode}`);
      tab.addEventListener("click", () => setEditorMode(mode));
      tab.addEventListener("keydown", event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault(); const next = event.key === "Home" ? "project" : event.key === "End" ? "existing" : mode === "project" ? "existing" : "project";
        setEditorMode(next); $(`#mode-${next}`).focus();
      });
    }
    $("#primary-export").addEventListener("click", () => {
      const button = editorMode === "project" ? $("#timeline-export") : $("#export-video");
      if (!button.disabled) button.click();
    });
    document.addEventListener("ocvg:timeline-ready", () => setEditorMode(editorMode));
    document.addEventListener("ocvg:edit-existing", () => setEditorMode("existing"));
    setEditorMode("project");
  }

  function parseStore() { try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch (_) { return []; } }
  function projectIdOf(project) { return String(project?.id ?? project?.projectId ?? project?.project_id ?? ""); }
  function locateProject(store, wantedId) {
    const match = (item) => item && typeof item === "object" && projectIdOf(item) === wantedId;
    if (Array.isArray(store)) return store.find(match) || (!wantedId && store[0]) || null;
    if (!store || typeof store !== "object") return null;
    if (Array.isArray(store.projects)) return store.projects.find(match) || (!wantedId && store.projects[0]) || null;
    if (match(store)) return store;
    if (wantedId && store[wantedId] && typeof store[wantedId] === "object") return store[wantedId];
    const values = Object.values(store).filter((value) => value && typeof value === "object" && !Array.isArray(value));
    return values.find(match) || (!wantedId && values[0]) || null;
  }
  function sceneId(scene, index) { return String(scene?.id ?? scene?.sceneId ?? scene?.scene_id ?? scene?.shotId ?? scene?.index ?? index); }
  function sceneTitle(scene, index) { return String(scene?.title || scene?.name || `镜头 ${String(index + 1).padStart(2, "0")}`); }

  function normalizeClip(raw, sceneIdValue) {
    if (Array.isArray(raw)) return { currentVersionId: String(raw[0]?.id || ""), versions: raw };
    if (raw && typeof raw === "object") return { ...raw, currentVersionId: String(raw.currentVersionId || raw.current_version_id || raw.versions?.[0]?.id || ""), versions: Array.isArray(raw.versions) ? raw.versions : [] };
    return { currentVersionId: "", versions: [], sceneId: sceneIdValue };
  }

  function loadProject() {
    state.store = parseStore(); state.projectId = new URLSearchParams(location.search).get("project") || ""; state.project = locateProject(state.store, state.projectId);
    if (!state.project) { showPageNotice(state.projectId ? `找不到项目“${state.projectId}”。请从项目工作台重新进入。` : "链接中缺少 project 参数，请从项目工作台选择项目。", true); state.project = { id: state.projectId || "unbound", title: "未绑定项目", scenes: [] }; }
    state.base = JSON.parse(JSON.stringify(state.project));
    state.projectId = projectIdOf(state.project) || state.projectId;
    const rawScenes = Array.isArray(state.project.scenes) ? state.project.scenes : Array.isArray(state.project.shots) ? state.project.shots : [];
    state.scenes = rawScenes.map((raw, index) => ({ id: sceneId(raw, index), title: sceneTitle(raw, index), index, raw }));
    const sourceClips = state.project.videoClips && typeof state.project.videoClips === "object" ? state.project.videoClips : {};
    state.scenes.forEach((scene) => { const raw = Array.isArray(sourceClips) ? sourceClips.filter((item) => String(item.sceneId ?? item.scene_id) === scene.id) : sourceClips[scene.id]; state.clips[scene.id] = normalizeClip(raw, scene.id); const selected = String(scene.raw.currentVideoClipId || scene.raw.current_video_clip_id || ""); if (state.clips[scene.id].versions.some((item) => String(item.id) === selected)) state.clips[scene.id].currentVersionId = selected; });
    const rawEdit = state.project.videoEdit && typeof state.project.videoEdit === "object" ? state.project.videoEdit : state.project.video_edit && typeof state.project.video_edit === "object" ? state.project.video_edit : {};
    state.edit = { ...rawEdit, trimStart: finite(rawEdit.trimStart ?? rawEdit.trim_start, 0), trimEnd: finite(rawEdit.trimEnd ?? rawEdit.trim_end, 0), sourceVolume: clamp(finite(rawEdit.sourceVolume ?? rawEdit.source_volume, 1), 0, 1), bgmVolume: clamp(finite(rawEdit.bgmVolume ?? rawEdit.bgm_volume, .35), 0, 1), bgmDelay: clamp(finite(rawEdit.bgmDelay ?? rawEdit.bgm_delay, 0), 0, 30), bgmLoop: rawEdit.bgmLoop ?? true, bgmFade: clamp(finite(rawEdit.bgmFade, 1), 0, 10), subtitlesEnabled: rawEdit.subtitlesEnabled ?? rawEdit.subtitles_enabled ?? true, sourceFile: rawEdit.sourceFile || rawEdit.source_file || null, subtitleFile: rawEdit.subtitleFile || rawEdit.subtitle_file || null };
    state.bgmTracks = Array.isArray(state.project.bgm) ? state.project.bgm.map((item) => ({ ...item })) : state.project.bgm && typeof state.project.bgm === "object" ? [{ ...state.project.bgm }] : [];
    state.bgm = state.bgmTracks[0] ? { ...state.bgmTracks[0] } : {};
  }

  function finite(value, fallback) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : fallback; }
  function clamp(value, min, max) { return Math.min(max, Math.max(min, value)); }
  function escapeHtml(value) { const div = document.createElement("div"); div.textContent = String(value ?? ""); return div.innerHTML; }
  function escapeAttr(value) { return escapeHtml(value).replace(/"/g, "&quot;"); }
  function formatTime(seconds) { const value = Math.max(0, finite(seconds, 0)); const minutes = Math.floor(value / 60); const rest = value - minutes * 60; return `${String(minutes).padStart(2, "0")}:${rest.toFixed(3).padStart(6, "0")}`; }
  function fileMetadata(file) { return { name: file.name, size: file.size, type: file.type || "application/octet-stream", lastModified: file.lastModified, localOnly: true }; }
  function showPageNotice(text, visible = true) { const node = $("#page-notice"); node.textContent = text; node.classList.toggle("hidden", !visible); }
  function setExportMessage(text) { $("#export-message").textContent = text; }

  function persist() {
    if (!locateProject(state.store, state.projectId)) return false;
    state.project.videoEdit = { ...state.edit };
    state.project.bgm = Object.keys(state.bgm).length ? [{ ...state.bgm }, ...state.bgmTracks.slice(1)] : [];
    state.project.videoClips = Object.entries(state.clips).flatMap(([sceneIdValue, clip]) => clip.versions.map(({ objectUrl, ...version }) => ({ ...version, sceneId: sceneIdValue })));
    state.scenes.forEach((scene) => { scene.raw.currentVideoClipId = state.clips[scene.id].currentVersionId || null; });
    state.project.updatedAt = new Date().toISOString();
    const indicator = $("#save-state"); indicator.className = "save-state saving"; indicator.innerHTML = "<i></i>保存中";
    try {
      const fresh = parseStore(), current = locateProject(fresh, state.projectId);
      if (!current) throw new Error("项目已移除");
      const fields = ["videoEdit", "bgm", "videoClips"];
      const merged = OCVGProjectStore.mergeFields(state.base, state.project, current, fields);
      for (const scene of current.scenes || []) {
        const local = state.scenes.find((item) => item.id === String(scene.id));
        if (local && changedClipScenes.has(local.id)) {
          const baseScene = (state.base.scenes || []).find((item) => String(item.id) === local.id) || {};
          const selection = OCVGProjectStore.mergeFields(baseScene, local.raw, scene, ["currentVideoClipId"]);
          scene.currentVideoClipId = selection.currentVideoClipId;
        }
      }
      Object.assign(current, merged, { updatedAt: state.project.updatedAt });
      localStorage.setItem(STORAGE_KEY, JSON.stringify(fresh)); state.base = JSON.parse(JSON.stringify(current)); changedClipScenes.clear(); indicator.className = "save-state"; indicator.innerHTML = "<i></i>已保存到本机"; return true;
    }
    catch (error) { indicator.className = "save-state error"; indicator.innerHTML = "<i></i>保存失败"; showPageNotice(`编辑参数未能保存：${error.message}。请下载编辑清单备份后再刷新核对。`, true); return false; }
  }

  function sourceUrlFromProject() {
    const completed = (Array.isArray(state.project.exports) ? state.project.exports : []).find((item) => !item.stale && item.status === "completed" && item.url && item.account === cloud.session()?.user?.id);
    const candidates = [state.project.videoUrl, state.project.video_url, state.project.outputUrl, state.project.output_url, state.project.result?.videoUrl, state.project.result?.video_url, completed?.url];
    return String(candidates.find(Boolean) || "");
  }

  async function loadInitialMedia() {
    const sourceUrl = sourceUrlFromProject();
    if (sourceUrl) {
      try {
        const blob = await cloud.request(sourceUrl, {}, true);
        state.sourceObjectUrl = URL.createObjectURL(blob); setVideoSource(state.sourceObjectUrl, "项目成片", false);
      } catch (error) { showPageNotice(`项目成片暂时无法读取：${error.message}`, true); }
    }
    if (state.edit.sourceFile?.name) $("#video-file-name").textContent = `${state.edit.sourceFile.name}（需重新选择本地文件）`;
    if (state.bgm.file?.name) $("#bgm-file-name").textContent = `${state.bgm.file.name}（需重新选择本地文件）`;
    if (state.edit.subtitleFile?.name) $("#subtitle-file-name").textContent = `${state.edit.subtitleFile.name}（需重新选择本地文件）`;
  }

  function setVideoSource(url, label, local = true) {
    const video = $("#video-preview"); video.pause(); $("#bgm-preview").pause(); video.src = url; video.load(); $("#video-empty").classList.add("hidden"); $("#source-pill").textContent = label; $("#video-file-name").textContent = local ? label : `${label} · 项目素材`; state.previewingSelection = false;
  }

  function applyStoredControls() {
    $("#source-volume").value = state.edit.sourceVolume; $("#bgm-volume").value = state.edit.bgmVolume; $("#bgm-delay").value = state.edit.bgmDelay; $("#bgm-loop").checked = state.edit.bgmLoop; $("#bgm-fade").value = state.edit.bgmFade; $("#subtitle-enabled").checked = Boolean(state.edit.subtitlesEnabled); updateControlOutputs(); applyPlaybackSettings();
  }

  function updateControlOutputs() {
    $("#trim-start-output").textContent = formatTime(state.edit.trimStart); $("#trim-end-output").textContent = formatTime(state.edit.trimEnd); $("#source-volume-output").textContent = `${Math.round(state.edit.sourceVolume * 100)}%`; $("#bgm-volume-output").textContent = `${Math.round(state.edit.bgmVolume * 100)}%`; $("#bgm-delay-output").textContent = `${state.edit.bgmDelay.toFixed(1)} 秒`; $("#selection-duration").textContent = `选区 ${formatTime(Math.max(0, state.edit.trimEnd - state.edit.trimStart))}`;
  }

  function applyPlaybackSettings() {
    const video = $("#video-preview"); const bgm = $("#bgm-preview");
    video.volume = state.edit.sourceVolume; bgm.volume = state.edit.bgmVolume; bgm.loop = state.edit.bgmLoop;
    if (state.generatedTrack?.track) state.generatedTrack.track.mode = state.edit.subtitlesEnabled ? "showing" : "disabled";
  }

  function onVideoMetadata() {
    const video = $("#video-preview"); const duration = finite(video.duration, 0); if (!duration) return;
    const hasSavedEnd = state.edit.trimEnd > 0; state.edit.trimStart = clamp(state.edit.trimStart, 0, Math.max(0, duration - .01)); state.edit.trimEnd = hasSavedEnd ? clamp(state.edit.trimEnd, state.edit.trimStart + .01, duration) : duration;
    [$("#trim-start"), $("#trim-end")].forEach((input) => { input.max = duration; }); $("#trim-start").value = state.edit.trimStart; $("#trim-end").value = state.edit.trimEnd; video.currentTime = state.edit.trimStart; updateControlOutputs(); updateTimecode(); persist();
  }

  function updateTimecode() { const video = $("#video-preview"); $("#timecode").textContent = `${formatTime(video.currentTime)} / ${formatTime(video.duration)}`; }
  function syncBgm(playIfNeeded = false) {
    const video = $("#video-preview"); const bgm = $("#bgm-preview"); if (!bgm.src) return;
    let target = video.currentTime - state.edit.trimStart - state.edit.bgmDelay;
    if (target < 0) { bgm.pause(); if (bgm.readyState) bgm.currentTime = 0; return; }
    if (state.edit.bgmLoop && Number.isFinite(bgm.duration) && bgm.duration > 0) target %= bgm.duration;
    if (Math.abs(bgm.currentTime - target) > .35 && bgm.readyState) bgm.currentTime = clamp(target, 0, finite(bgm.duration, target));
    if (playIfNeeded && !video.paused) bgm.play().catch(() => setExportMessage("浏览器暂未允许背景音乐自动播放，请再次点击预览选区。"));
  }

  function onVideoTimeUpdate() {
    const video = $("#video-preview"); updateTimecode();
    if (state.previewingSelection && video.currentTime >= state.edit.trimEnd - .02) { video.pause(); video.currentTime = state.edit.trimEnd; $("#bgm-preview").pause(); state.previewingSelection = false; }
    else if (!video.paused && $("#bgm-preview").src && video.currentTime >= state.edit.trimStart + state.edit.bgmDelay && $("#bgm-preview").paused) syncBgm(true);
  }

  function handleVideoFile(file) {
    if (!file) return; if (!file.type.startsWith("video/")) { showPageNotice("请选择浏览器支持的视频文件。", true); return; }
    if (file.size > 200 * 1024 * 1024) return showPageNotice("主视频不能超过 200 MiB。", true);
    uploadFiles.video = file; state.edit.trimStart = 0; state.edit.trimEnd = 0;
    if (state.sourceObjectUrl) URL.revokeObjectURL(state.sourceObjectUrl); state.sourceObjectUrl = URL.createObjectURL(file); state.edit.sourceFile = fileMetadata(file); setVideoSource(state.sourceObjectUrl, file.name); persist(); showPageNotice("", false);
  }

  function handleBgmFile(file) {
    if (!file) return; if (!file.type.startsWith("audio/")) { showPageNotice("请选择音频文件。", true); return; }
    if (file.size > 30 * 1024 * 1024) return showPageNotice("背景音乐不能超过 30 MiB。", true);
    uploadFiles.bgm = file;
    if (state.bgmObjectUrl) URL.revokeObjectURL(state.bgmObjectUrl); state.bgmObjectUrl = URL.createObjectURL(file); const audio = $("#bgm-preview"); audio.src = state.bgmObjectUrl; audio.load(); state.bgm.file = fileMetadata(file); $("#bgm-file-name").textContent = file.name; persist(); showPageNotice("", false);
  }

  function srtToVtt(text) {
    const normalized = text.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
    if (/^WEBVTT(?:\s|$)/.test(normalized)) return normalized;
    return `WEBVTT\n\n${normalized.replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, "$1.$2")}`;
  }

  async function handleSubtitleFile(file) {
    if (!file) return; if (file.size > 1024 * 1024) return showPageNotice("字幕文件不能超过 1 MiB。", true);
    uploadFiles.subtitles = file; const extension = file.name.split(".").pop().toLowerCase(); state.edit.subtitleFile = fileMetadata(file); $("#subtitle-file-name").textContent = file.name;
    if (state.generatedTrack) { state.generatedTrack.remove(); state.generatedTrack = null; } if (state.subtitleObjectUrl) URL.revokeObjectURL(state.subtitleObjectUrl);
    if (extension === "ass") { setExportMessage("ASS 文件已写入编辑清单；浏览器不原生预览 ASS，服务端编码时可烧录。 "); persist(); return; }
    try { const vtt = srtToVtt(await file.text()); state.subtitleObjectUrl = URL.createObjectURL(new Blob([vtt], { type: "text/vtt" })); const track = document.createElement("track"); track.kind = "subtitles"; track.label = "项目字幕"; track.srclang = "zh"; track.src = state.subtitleObjectUrl; track.default = true; $("#video-preview").appendChild(track); state.generatedTrack = track; track.addEventListener("load", applyPlaybackSettings, { once: true }); setExportMessage("字幕已载入预览。 "); }
    catch (_) { setExportMessage("字幕文件无法解析，但文件信息仍已写入编辑清单。 "); }
    persist();
  }

  function updateEditFromControls() {
    const video = $("#video-preview"); const duration = finite(video.duration, Math.max(1, Number($("#trim-end").max)));
    let start = clamp(Number($("#trim-start").value), 0, duration); let end = clamp(Number($("#trim-end").value), 0, duration);
    if (start >= end) { if (document.activeElement === $("#trim-start")) start = Math.max(0, end - .01); else end = Math.min(duration, start + .01); }
    state.edit.trimStart = start; state.edit.trimEnd = end; state.edit.sourceVolume = Number($("#source-volume").value); state.edit.bgmVolume = Number($("#bgm-volume").value); state.edit.bgmDelay = Number($("#bgm-delay").value); state.edit.bgmLoop = $("#bgm-loop").checked; state.edit.bgmFade = clamp(Number($("#bgm-fade").value), 0, 10); state.edit.subtitlesEnabled = $("#subtitle-enabled").checked; $("#trim-start").value = start; $("#trim-end").value = end; updateControlOutputs(); applyPlaybackSettings(); syncBgm(); persist();
  }

  function resetSettings() {
    const duration = finite($("#video-preview").duration, 1); state.edit.trimStart = 0; state.edit.trimEnd = duration; state.edit.sourceVolume = 1; state.edit.bgmVolume = .35; state.edit.bgmDelay = 0; state.edit.subtitlesEnabled = true; $("#trim-start").value = 0; $("#trim-end").value = duration; applyStoredControls(); persist();
  }

  function playSelection() {
    const video = $("#video-preview"); if (!video.src) { showPageNotice("请先选择主视频或镜头片段。", true); return; }
    if (video.currentTime < state.edit.trimStart || video.currentTime >= state.edit.trimEnd) video.currentTime = state.edit.trimStart; state.previewingSelection = true; syncBgm(); video.play().then(() => syncBgm(true)).catch(() => showPageNotice("浏览器无法播放此视频编码，请更换文件格式。", true));
  }

  function renderClips() {
    $("#clip-count").textContent = `${state.scenes.length} 镜`; const node = $("#clip-grid");
    if (!state.scenes.length) { node.innerHTML = '<div class="empty-clips">项目还没有分镜。创建分镜后可在这里管理逐镜视频片段。</div>'; return; }
    node.innerHTML = state.scenes.map((scene) => { const clip = state.clips[scene.id]; const current = clip.versions.find((version) => String(version.id) === clip.currentVersionId); return `<article class="clip-card"><div class="clip-head"><span><strong>${escapeHtml(scene.title)}</strong><small>镜头 ${String(scene.index + 1).padStart(2, "0")}</small></span><span>${clip.versions.length} 个版本</span></div><div class="clip-current"><i>▶</i><span><b>${escapeHtml(current?.name || "尚未添加片段")}</b><small>${current ? `${formatBytes(current.size)} · ${current.localOnly ? "本地文件" : "项目素材"}` : "上传视频创建首个版本"}</small></span></div><div class="clip-actions"><label class="clip-upload"><input type="file" accept="video/*" data-clip-upload="${escapeAttr(scene.id)}">上传新版本</label>${current ? `<button type="button" data-delete-clip="${escapeAttr(scene.id)}">删除当前</button>` : ""}</div><div class="version-dots">${clip.versions.map((version, index) => `<button type="button" class="${String(version.id) === clip.currentVersionId ? "current" : ""}" data-select-clip="${escapeAttr(scene.id)}" data-version-id="${escapeAttr(version.id)}" title="版本 ${index + 1}"></button>`).join("")}</div></article>`; }).join("");
    node.querySelectorAll("[data-clip-upload]").forEach((input) => input.addEventListener("change", (event) => { handleClipUpload(input.dataset.clipUpload, event.target.files[0]); input.value = ""; }));
    node.querySelectorAll("[data-select-clip]").forEach((button) => button.addEventListener("click", () => selectClipVersion(button.dataset.selectClip, button.dataset.versionId)));
    node.querySelectorAll("[data-delete-clip]").forEach((button) => button.addEventListener("click", () => deleteCurrentClip(button.dataset.deleteClip)));
  }

  function formatBytes(bytes) { const value = finite(bytes, 0); if (!value) return "大小未知"; return value >= 1048576 ? `${(value / 1048576).toFixed(1)} MB` : `${Math.round(value / 1024)} KB`; }
  function handleClipUpload(sceneIdValue, file) {
    if (!file || !file.type.startsWith("video/")) return; if (file.size > 200 * 1024 * 1024) return showPageNotice("片段不能超过 200 MiB。", true);
    const id = uid("clip"); const url = URL.createObjectURL(file); state.clipUrls.set(id, url); clipFiles.set(id, file); const clip = state.clips[sceneIdValue]; clip.versions.push({ id, name: file.name, ...fileMetadata(file), createdAt: new Date().toISOString() }); clip.currentVersionId = id; changedClipScenes.add(sceneIdValue); persist(); renderClips(); handleVideoFile(file); setExportMessage("片段已加入当前镜头，并作为当前主视频供预览和导出。刷新页面后需重新选择本地文件。");
  }
  async function selectClipVersion(sceneIdValue, versionId) {
    const clip = state.clips[sceneIdValue]; clip.currentVersionId = versionId; changedClipScenes.add(sceneIdValue); persist(); renderClips();
    const version = clip.versions.find((item) => String(item.id) === versionId);
    try {
      let file = clipFiles.get(versionId);
      if (!file && (version?.url || version?.videoUrl)) file = new File([await cloud.request(version.url || version.videoUrl, {}, true)], "clip.mp4", { type: "video/mp4" });
      if (file) { handleVideoFile(file); setExportMessage("当前选用片段已载入为主视频，导出将使用此片段。"); }
      else setExportMessage("版本选择已保存。该本地片段需重新上传才能作为主视频，当前预览和导出源保持不变。");
    } catch (error) { setExportMessage(error.message); }
  }
  function deleteCurrentClip(sceneIdValue) { const clip = state.clips[sceneIdValue]; const index = clip.versions.findIndex((item) => String(item.id) === clip.currentVersionId); if (index < 0) return; const [removed] = clip.versions.splice(index, 1); const url = state.clipUrls.get(String(removed.id)); if (url) URL.revokeObjectURL(url); state.clipUrls.delete(String(removed.id)); clipFiles.delete(String(removed.id)); clip.currentVersionId = String(clip.versions[Math.max(0, index - 1)]?.id || clip.versions[0]?.id || ""); changedClipScenes.add(sceneIdValue); persist(); renderClips(); }

  function manifest() {
    return { format: "oneclick-vidgen-edit-manifest", version: 1, exportedAt: new Date().toISOString(), project: { id: state.projectId, title: state.project.title || state.project.name || "未命名项目" }, source: state.edit.sourceFile, edit: { trimStart: state.edit.trimStart, trimEnd: state.edit.trimEnd, sourceVolume: state.edit.sourceVolume, subtitlesEnabled: state.edit.subtitlesEnabled }, bgm: { file: state.bgm.file || null, volume: state.edit.bgmVolume, delay: state.edit.bgmDelay }, subtitles: state.edit.subtitleFile, clips: state.scenes.map((scene) => ({ sceneId: scene.id, title: scene.title, ...state.clips[scene.id], versions: state.clips[scene.id].versions.map(({ objectUrl, ...version }) => version) })), encodingRequired: true, encodingNote: "应用裁剪、混音与字幕烧录需要服务端 FFmpeg 编码。" };
  }
  function downloadManifest() { const content = JSON.stringify(manifest(), null, 2); const url = URL.createObjectURL(new Blob([content], { type: "application/json;charset=utf-8" })); const anchor = document.createElement("a"); anchor.href = url; anchor.download = `OneClickVidGen_${safeName(state.project.title || state.project.name || state.projectId || "project")}_edit.json`; document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); setExportMessage("编辑备份已下载。视频文件请使用“导出已有视频”。"); }
  function safeName(value) { return String(value).replace(/[\\/:*?"<>|]+/g, "-").slice(0, 60); }

  function exportRecord(job) {
    const fresh = parseStore(), project = locateProject(fresh, state.projectId);
    if (!project) return;
    const records = Array.isArray(project.exports) ? project.exports : [];
    const record = { ...job, kind: "video-edit", account: cloud.session()?.user?.id };
    project.exports = [record, ...records.filter((item) => item.id !== job.id)];
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(fresh)); } catch { setExportMessage("导出任务已提交，本机记录保存失败；可点“刷新导出记录”找回。"); }
  }

  function subtitleFromProject() {
    const project = locateProject(parseStore(), state.projectId);
    const cues = (project?.subtitles || []).filter((cue) => !cue.hidden && Number(cue.end) > Number(cue.start));
    if (!cues.length) return null;
    const stamp = (seconds) => { const ms = Math.round(Number(seconds) * 1000); return `${String(Math.floor(ms / 3600000)).padStart(2, "0")}:${String(Math.floor(ms / 60000) % 60).padStart(2, "0")}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")},${String(ms % 1000).padStart(3, "0")}`; };
    const text = cues.map((cue, i) => `${i + 1}\n${stamp(cue.start)} --> ${stamp(cue.end)}\n${String(cue.text || "").replace(/\r?\n\s*\r?\n/g, "\n")}`).join("\n\n");
    return new File([text], "project.srt", { type: "application/x-subrip" });
  }

  async function createExport(clean = false) {
    if (exportBusy) return;
    exportBusy = true; $("#start-export").disabled = true; $("#start-clean-export").disabled = true;
    try {
      const account = cloud.session()?.user?.id;
      if (!account) throw new Error("请先在云端工作台登录，再返回导出。");
      let video = uploadFiles.video;
      if (!video) {
        const source = sourceUrlFromProject();
        if (!source) throw new Error("请重新选择主视频文件，文件内容不会保存在浏览器项目记录中。");
        setExportMessage("正在读取项目成片…");
        video = new File([await cloud.request(source, {}, true)], "project.mp4", { type: "video/mp4" });
      }
      if (state.edit.trimEnd <= state.edit.trimStart || state.edit.trimEnd - state.edit.trimStart > 600) throw new Error("请选择 0–10 分钟以内的有效裁剪范围。");
      if (state.bgm.file && !uploadFiles.bgm) throw new Error("请重新选择背景音乐文件，或先移除背景音乐。");
      if (!clean && state.edit.subtitlesEnabled && state.edit.subtitleFile && !uploadFiles.subtitles) throw new Error("请重新选择字幕文件，或移除它以使用项目字幕。");
      const subtitles = !clean && state.edit.subtitlesEnabled ? uploadFiles.subtitles || subtitleFromProject() : null;
      const settings = { trim_start: state.edit.trimStart, trim_end: state.edit.trimEnd, source_volume: state.edit.sourceVolume, bgm_volume: state.edit.bgmVolume, bgm_delay: state.edit.bgmDelay, bgm_loop: state.edit.bgmLoop, bgm_fade: state.edit.bgmFade, burn_subtitles: Boolean(subtitles) };
      const subtitleStyle = locateProject(parseStore(), state.projectId)?.subtitleStyle || {};
      const fingerprint = JSON.stringify({ settings, subtitleStyle, video: uploadFiles.video ? fileMetadata(video) : { url: sourceUrlFromProject() }, bgm: uploadFiles.bgm && fileMetadata(uploadFiles.bgm), subtitles: subtitles && { name: subtitles.name, text: await subtitles.text() } });
      let pending = state.edit.pendingExport;
      if (pending && pending.account !== account) throw new Error("尚有另一账号的导出提交待核对，请切回原账号。");
      if (pending && pending.fingerprint !== fingerprint) throw new Error("上次提交的结果尚未确认。请先刷新导出记录核对，暂不能更换素材、参数或字幕版本重试。");
      if (!pending) { pending = { clientId: `edit-${uid("export")}`, account, fingerprint }; state.edit.pendingExport = pending; if (!persist()) { delete state.edit.pendingExport; throw new Error("本机保存失败，未上传素材。请先保留编辑清单并处理保存错误。"); } }
      const form = new FormData(); form.append("video", video);
      form.append("subtitle_style", JSON.stringify(subtitleStyle));
      if (uploadFiles.bgm) form.append("bgm", uploadFiles.bgm);
      if (subtitles) form.append("subtitles", subtitles);
      Object.entries(settings).forEach(([key, value]) => form.append(key, String(value)));
      setExportMessage("正在上传素材并提交编码任务，请保留此页面直到收到任务编号…");
      const job = await cloud.request("/editor/exports", { method: "POST", headers: { "Idempotency-Key": pending.clientId }, body: form });
      delete state.edit.pendingExport; persist(); exportRecord(job);
      $("#export-dialog").close(); setExportMessage("导出任务已提交。关闭页面后仍会继续编码，可从导出记录找回。"); await loadExports();
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && state.edit.pendingExport) { delete state.edit.pendingExport; persist(); }
      setExportMessage(error.message); $("#export-dialog-message").textContent = error.message;
    }
    finally { exportBusy = false; $("#start-export").disabled = false; $("#start-clean-export").disabled = false; }
  }

  async function loadExports() {
    clearTimeout(exportTimer);
    try {
      const data = await cloud.request("/editor/exports");
      const jobs = Array.isArray(data) ? data : data.items || [];
      if (typeof CustomEvent === "function") document.dispatchEvent(new CustomEvent("ocvg:exports-loaded", { detail: { jobs, account: cloud.session()?.user?.id } }));
      const account = cloud.session()?.user?.id;
      const fresh = parseStore(), saved = locateProject(fresh, state.projectId);
      if (saved && Array.isArray(saved.exports)) {
        let changed = false;
        saved.exports = saved.exports.map((record) => {
          const job = record.account === account && jobs.find((item) => item.id === record.id);
          if (!job) return record;
          const updated = { ...record, ...job, url: job.download_url || null };
          if (JSON.stringify(updated) !== JSON.stringify(record)) changed = true;
          return updated;
        });
        if (changed) {
          saved.updatedAt = new Date().toISOString();
          try { localStorage.setItem(STORAGE_KEY, JSON.stringify(fresh)); state.project.exports = saved.exports; }
          catch (_) { setExportMessage("云端状态已读取，本机记录更新失败，请下载成片并保留备份。"); }
        }
      }
      const list = $("#export-jobs"); list.replaceChildren();
      if (!jobs.length) { list.textContent = "当前账号还没有导出记录。"; return; }
      const labels = { processing: "编码中", completed: "已完成", failed: "失败", queued: "排队中" };
      for (const job of jobs) {
        const row = document.createElement("article"); row.className = "export-job";
        const stale = saved?.exports?.find(record => record.id === job.id && record.account === account)?.stale;
        const reused = Number.isInteger(job.cached_scenes) && job.cached_scenes > 0 ? ` · 复用 ${job.cached_scenes} 个镜头` : "";
        const text = document.createElement("span"); text.textContent = `${labels[job.status] || job.status}${stale ? " · 修改前的版本" : ""} · ${job.id.slice(-8)}${reused}${job.error ? ` · ${job.error}` : ""}`; row.append(text);
        if (job.status === "completed") { const download = document.createElement("button"); download.className = "ghost-button"; download.textContent = "下载 MP4"; download.addEventListener("click", () => downloadExport(job, download)); row.append(download); }
        list.append(row);
        if (job.client_id === state.edit.pendingExport?.clientId) { delete state.edit.pendingExport; persist(); exportRecord(job); }
      }
      if (jobs.some((job) => ["queued", "processing"].includes(job.status))) exportTimer = setTimeout(loadExports, 4000);
    } catch (error) { $("#export-jobs").textContent = error.message; }
  }

  async function downloadExport(job, button) {
    button.disabled = true;
    try {
      const blob = await cloud.request(`/editor/exports/${encodeURIComponent(job.id)}/download`, {}, true);
      const url = URL.createObjectURL(blob), anchor = document.createElement("a"); anchor.href = url; anchor.download = `OneClickVidGen-${job.id}.mp4`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { setExportMessage(error.message); }
    finally { button.disabled = false; }
  }

  function removeMedia(kind) {
    uploadFiles[kind] = null;
    if (kind === "bgm") { $("#bgm-preview").pause(); $("#bgm-preview").removeAttribute("src"); state.bgm = {}; state.bgmTracks = []; $("#bgm-file-name").textContent = "未使用背景音乐"; }
    else { state.edit.subtitleFile = null; state.generatedTrack?.remove(); state.generatedTrack = null; $("#subtitle-file-name").textContent = "未选择文件，导出时使用项目字幕"; }
    persist();
  }

  function bindEvents() {
    const video = $("#video-preview"); const bgm = $("#bgm-preview");
    $("#video-input").addEventListener("change", (event) => { handleVideoFile(event.target.files[0]); event.target.value = ""; }); $("#bgm-input").addEventListener("change", (event) => { handleBgmFile(event.target.files[0]); event.target.value = ""; }); $("#subtitle-input").addEventListener("change", async (event) => { await handleSubtitleFile(event.target.files[0]); event.target.value = ""; });
    ["#trim-start", "#trim-end", "#source-volume", "#bgm-volume", "#bgm-delay", "#bgm-loop", "#bgm-fade", "#subtitle-enabled"].forEach((selector) => $(selector).addEventListener("input", updateEditFromControls));
    video.addEventListener("loadedmetadata", onVideoMetadata); video.addEventListener("timeupdate", onVideoTimeUpdate); video.addEventListener("play", () => syncBgm(true)); video.addEventListener("pause", () => bgm.pause()); video.addEventListener("seeking", () => syncBgm()); video.addEventListener("ended", () => bgm.pause());
    $("#play-selection").addEventListener("click", playSelection); $("#set-start").addEventListener("click", () => { if (!video.src) return; $("#trim-start").value = Math.min(video.currentTime, state.edit.trimEnd - .01); updateEditFromControls(); }); $("#set-end").addEventListener("click", () => { if (!video.src) return; $("#trim-end").value = Math.max(video.currentTime, state.edit.trimStart + .01); updateEditFromControls(); }); $("#reset-settings").addEventListener("click", resetSettings);
    [$("#export-manifest"), $("#export-manifest-secondary")].forEach((button) => button.addEventListener("click", downloadManifest)); $("#export-video").addEventListener("click", () => { $("#export-dialog-message").textContent = ""; $("#export-dialog").showModal(); }); $("#close-export-dialog").addEventListener("click", () => $("#export-dialog").close());
    $("#start-export").addEventListener("click", () => createExport(false)); $("#start-clean-export").addEventListener("click", () => createExport(true)); $("#reload-exports").addEventListener("click", loadExports);
    $("#remove-bgm").addEventListener("click", () => removeMedia("bgm")); $("#remove-subtitles").addEventListener("click", () => removeMedia("subtitles"));
    document.addEventListener("ocvg:flush-project", (event) => { if (!persist()) event.preventDefault(); });
    window.addEventListener("beforeunload", () => { [state.sourceObjectUrl, state.bgmObjectUrl, state.subtitleObjectUrl, ...state.clipUrls.values()].filter(Boolean).forEach((url) => URL.revokeObjectURL(url)); });
  }

  loadProject(); const title = state.project.title || state.project.name || "未命名项目"; $("#project-title").textContent = title; document.title = `${title} · 视频编辑器｜One-Click VidGen`; const query = state.projectId ? `?project=${encodeURIComponent(state.projectId)}` : ""; $("#back-project").href = `/workspace/${query}`;
  applyStoredControls(); bindEvents(); bindEditorModes(); renderClips(); loadInitialMedia(); updateTimecode();
  if (cloud.session()?.access_token) loadExports();
})();
