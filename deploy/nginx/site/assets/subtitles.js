(() => {
  "use strict";

  const STORAGE_KEY = "ocvg.projects.v1";
  const queryId = new URLSearchParams(location.search).get("project");
  const el = (selector, root = document) => root.querySelector(selector);
  let baseline = null;
  let saveTimer = null;
  let savedContent = null;
  const state = { root: null, projects: [], project: null, cues: [], dirty: false, subtitleStyle: { fontSize: 28, textColor: "#FFFFFF", bottomMargin: 32, outline: 2 } };

  function normalizeStyle(raw = {}) {
    const integer = (value, minimum, maximum, fallback) => Number.isInteger(value) && value >= minimum && value <= maximum ? value : fallback;
    return { fontSize: integer(raw?.fontSize, 16, 64, 28), textColor: /^#[0-9a-fA-F]{6}$/.test(raw?.textColor) ? raw.textColor.toUpperCase() : "#FFFFFF", bottomMargin: integer(raw?.bottomMargin, 0, 120, 32), outline: integer(raw?.outline, 0, 4, 2) };
  }

  function renderStyle() {
    const style = state.subtitleStyle, preview = el("#subtitle-style-example");
    preview.style.fontSize = `${style.fontSize}px`; preview.style.color = style.textColor; preview.style.bottom = `${style.bottomMargin}px`;
    preview.style.webkitTextStroke = `${style.outline}px black`; preview.style.paintOrder = "stroke fill";
    el("#subtitle-font-value").textContent = style.fontSize; el("#subtitle-margin-value").textContent = style.bottomMargin;
  }

  function loadStyleControls() {
    state.subtitleStyle = normalizeStyle(state.project?.subtitleStyle);
    el("#subtitle-font-size").value = state.subtitleStyle.fontSize; el("#subtitle-color").value = state.subtitleStyle.textColor;
    el("#subtitle-bottom-margin").value = state.subtitleStyle.bottomMargin; el("#subtitle-outline").value = state.subtitleStyle.outline;
    renderStyle();
  }

  function uid() {
    return globalThis.crypto?.randomUUID?.() || `cue-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function unpackProjects(raw) {
    if (Array.isArray(raw)) return raw;
    if (Array.isArray(raw?.projects)) return raw.projects;
    if (Array.isArray(raw?.items)) return raw.items;
    if (raw && typeof raw === "object") return Object.values(raw).filter((item) => item && typeof item === "object");
    return [];
  }

  function loadProject() {
    try {
      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      state.root = raw;
      state.projects = unpackProjects(raw);
      state.project = state.projects.find((item) => String(item.id ?? item.projectId) === String(queryId)) || (!queryId ? state.projects[0] : null);
    } catch {
      state.root = [];
      state.projects = [];
    }
    if (!state.project) {
      el("#project-name").textContent = queryId ? "未找到该项目" : "未选择项目";
      showNotice("无法读取项目。请先从项目工作台进入，或检查浏览器本地项目数据。", true);
      return;
    }
    baseline = JSON.parse(JSON.stringify(state.project));
    const name = state.project.name || state.project.title || "未命名项目";
    el("#project-name").textContent = name;
    document.title = `${name} · 字幕精修`;
    el("#back-project").href = `/workspace/?project=${encodeURIComponent(state.project.id ?? state.project.projectId ?? "")}`;
    const source = Array.isArray(state.project.subtitles) ? state.project.subtitles
      : Array.isArray(state.project.captions) ? state.project.captions
      : deriveFromScenes(state.project.scenes || state.project.storyboard || []);
    state.cues = source.map(normalizeCue).sort((a, b) => a.start - b.start);
  }

  function deriveFromScenes(scenes) {
    let cursor = 0;
    return scenes.map((scene, index) => {
      const text = scene.subtitle || scene.narration || scene.text || "";
      const start = Number(scene.start ?? scene.startTime ?? cursor);
      const duration = Number(scene.duration ?? Math.max(2, text.length / 5));
      const end = Number(scene.end ?? scene.endTime ?? start + duration);
      cursor = end;
      return { id: scene.id || `scene-${index + 1}`, start, end, text, hidden: false };
    }).filter((cue) => cue.text);
  }

  function normalizeCue(cue, index) {
    const start = Math.max(0, toSeconds(cue.start ?? cue.startTime ?? cue.begin ?? 0));
    const endRaw = toSeconds(cue.end ?? cue.endTime ?? cue.finish ?? start + 2);
    return { id: cue.id || uid(), sceneId: cue.sceneId || cue.scene_id || null, start, end: Math.max(start + .05, endRaw), text: String(cue.text ?? cue.content ?? cue.subtitle ?? ""), hidden: Boolean(cue.hidden), order: index };
  }

  function toSeconds(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    const input = String(value ?? "").trim().replace(",", ".");
    if (/^\d+(?:\.\d+)?$/.test(input)) return Number(input);
    const parts = input.split(":").map(Number);
    if (parts.some(Number.isNaN)) return 0;
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return 0;
  }

  function formatTime(seconds, separator = ",") {
    const msTotal = Math.max(0, Math.round(Number(seconds || 0) * 1000));
    const hours = Math.floor(msTotal / 3600000);
    const minutes = Math.floor(msTotal % 3600000 / 60000);
    const secs = Math.floor(msTotal % 60000 / 1000);
    const ms = msTotal % 1000;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}${separator}${String(ms).padStart(3, "0")}`;
  }

  function markDirty() {
    state.dirty = true;
    el("#save-state").textContent = "保存中";
    el("#save-state").classList.add("dirty");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveProject(), 400);
  }

  function showNotice(message, error = false) {
    const node = el("#notice");
    node.textContent = message;
    node.className = `notice show${error ? " error" : ""}`;
    clearTimeout(showNotice.timer);
    showNotice.timer = setTimeout(() => node.classList.remove("show"), 4200);
  }

  function refreshSummary() {
    const visible = state.cues.filter((cue) => !cue.hidden).length;
    const duration = state.cues.reduce((max, cue) => Math.max(max, cue.end), 0);
    const minutes = Math.floor(duration / 60);
    const seconds = Math.floor(duration % 60);
    el("#summary").textContent = `${state.cues.length} 条字幕 · ${visible} 条参与导出 · 总时长 ${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  function updateCueTime(cue, key, input) {
    const value = toSeconds(input.value);
    if (!Number.isFinite(value) || value < 0) {
      input.setCustomValidity("请输入如 00:00:03,500 的有效时间");
      input.reportValidity();
      return;
    }
    input.setCustomValidity("");
    if (key === "start") cue.start = Math.min(value, Math.max(0, cue.end - .05));
    else cue.end = Math.max(value, cue.start + .05);
    input.value = formatTime(cue[key]);
    markDirty();
    refreshSummary();
  }

  function render() {
    const list = el("#subtitle-list");
    list.replaceChildren();
    el("#empty-state").classList.toggle("show", state.cues.length === 0);
    state.cues.forEach((cue, index) => {
      const row = el("#subtitle-template").content.firstElementChild.cloneNode(true);
      row.dataset.id = cue.id;
      row.classList.toggle("is-hidden", cue.hidden);
      el(".row-number", row).textContent = String(index + 1).padStart(2, "0");
      const start = el(".start-time", row);
      const end = el(".end-time", row);
      const text = el(".subtitle-text", row);
      start.value = formatTime(cue.start);
      end.value = formatTime(cue.end);
      text.value = cue.text;
      el(".char-count", row).textContent = `${cue.text.length} 字`;
      el(".merge", row).disabled = index === state.cues.length - 1;
      el(".toggle", row).textContent = cue.hidden ? "显示" : "隐藏";
      start.addEventListener("change", () => updateCueTime(cue, "start", start));
      end.addEventListener("change", () => updateCueTime(cue, "end", end));
      text.addEventListener("input", () => { cue.text = text.value; el(".char-count", row).textContent = `${cue.text.length} 字`; markDirty(); });
      el(".remove", row).addEventListener("click", () => { state.cues.splice(index, 1); markDirty(); render(); });
      el(".toggle", row).addEventListener("click", () => { cue.hidden = !cue.hidden; markDirty(); render(); });
      el(".merge", row).addEventListener("click", () => mergeCue(index));
      el(".split", row).addEventListener("click", () => splitCue(index, text.selectionStart));
      list.append(row);
    });
    refreshSummary();
  }

  function splitCue(index, cursor) {
    const cue = state.cues[index];
    const text = cue.text.trim();
    if (text.length < 2) return showNotice("这条字幕太短，无法拆分。", true);
    let point = Number.isInteger(cursor) && cursor > 0 && cursor < text.length ? cursor : -1;
    if (point < 0) {
      const candidates = [...text.matchAll(/[，。！？；,.!?;]\s*/g)].map((match) => match.index + match[0].length);
      point = candidates.sort((a, b) => Math.abs(a - text.length / 2) - Math.abs(b - text.length / 2))[0] || Math.ceil(text.length / 2);
    }
    const first = text.slice(0, point).trim();
    const second = text.slice(point).trim();
    if (!first || !second) return showNotice("请把光标放在要拆分的位置，再点击拆分。", true);
    const originalEnd = cue.end;
    const midpoint = cue.start + (originalEnd - cue.start) * (first.length / text.length);
    cue.text = first;
    cue.end = midpoint;
    state.cues.splice(index + 1, 0, { id: uid(), sceneId: cue.sceneId, start: midpoint, end: originalEnd, text: second, hidden: cue.hidden });
    markDirty();
    render();
  }

  function mergeCue(index) {
    const cue = state.cues[index];
    const next = state.cues[index + 1];
    if (!next) return;
    cue.text = `${cue.text.trim()} ${next.text.trim()}`.trim();
    cue.end = Math.max(cue.end, next.end);
    cue.hidden = cue.hidden && next.hidden;
    state.cues.splice(index + 1, 1);
    markDirty();
    render();
  }

  function addCue() {
    const previous = state.cues.at(-1);
    const start = previous ? previous.end : 0;
    state.cues.push({ id: uid(), start, end: start + 2.5, text: "", hidden: false });
    markDirty();
    render();
    const row = el(`.subtitle-row[data-id="${CSS.escape(state.cues.at(-1).id)}"]`);
    row?.scrollIntoView({ behavior: "smooth", block: "center" });
    el(".subtitle-text", row)?.focus();
  }

  function parseSubtitleFile(content, filename) {
    const text = content.replace(/^\uFEFF/, "").replace(/\r/g, "");
    const isVtt = /\.vtt$/i.test(filename) || /^WEBVTT/m.test(text);
    const lines = text.replace(/^WEBVTT[^\n]*\n+/, "").split("\n");
    const cues = [];
    let index = 0;
    while (index < lines.length) {
      while (index < lines.length && !lines[index].trim()) index++;
      if (index >= lines.length) break;
      if (!lines[index].includes("-->")) index++;
      if (index >= lines.length || !lines[index].includes("-->")) { index++; continue; }
      const timing = lines[index++].match(/([^\s]+)\s*-->\s*([^\s]+)/);
      if (!timing) continue;
      const body = [];
      while (index < lines.length && lines[index].trim()) body.push(lines[index++]);
      cues.push({ id: uid(), start: toSeconds(timing[1]), end: toSeconds(timing[2]), text: body.join("\n"), hidden: false });
    }
    if (!cues.length) throw new Error(isVtt ? "没有识别到有效的 VTT 字幕。" : "没有识别到有效的 SRT 字幕。");
    return cues.map(normalizeCue);
  }

  async function importFile(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) return showNotice("字幕文件不能超过 5 MB。", true);
    try {
      state.cues = parseSubtitleFile(await file.text(), file.name);
      markDirty();
      render();
      showNotice(`已导入 ${state.cues.length} 条字幕并准备自动保存，请检查时间与文本。`);
    } catch (error) { showNotice(error.message || "字幕文件解析失败。", true); }
  }

  function saveProject() {
    clearTimeout(saveTimer); saveTimer = null;
    if (!state.project) return showNotice("当前没有可保存的项目。", true);
    state.project.subtitles = state.cues.map(({ id, sceneId, start, end, text, hidden }, index) => ({ id, sceneId, index: index + 1, start, end, text, hidden }));
    state.project.subtitleStyle = { ...state.subtitleStyle };
    const content = JSON.stringify([state.project.subtitles, state.project.subtitleStyle]);
    if (content === savedContent) { state.dirty = false; el("#save-state").textContent = "已保存到本机"; el("#save-state").classList.remove("dirty"); el("#save-project").hidden = true; return true; }
    state.project.updatedAt = new Date().toISOString();
    try {
      state.project = window.OCVGProjectStore.save(localStorage, STORAGE_KEY, baseline, state.project, ["subtitles", "subtitleStyle"]);
      baseline = JSON.parse(JSON.stringify(state.project));
      savedContent = content;
      state.dirty = false;
      el("#save-state").textContent = "已保存到本机";
      el("#save-state").classList.remove("dirty");
      el("#save-state").removeAttribute("title"); el("#save-project").hidden = true;
      document.dispatchEvent(new CustomEvent("ocvg:project-saved", { detail: { id: state.project.id } }));
      return true;
    } catch (error) { state.dirty = true; el("#save-state").textContent = "保存失败"; el("#save-state").title = error.message || "请导出字幕备份后重试。"; el("#save-project").hidden = false; showNotice(error.message || "浏览器存储空间不足，保存失败。请先导出字幕备份。", true); return false; }
  }

  function exportFile(format) {
    const cues = state.cues.filter((cue) => !cue.hidden);
    if (!cues.length) return showNotice("没有可导出的字幕。", true);
    const vtt = format === "vtt";
    const body = cues.map((cue, index) => `${index + 1}\n${formatTime(cue.start, vtt ? "." : ",")} --> ${formatTime(cue.end, vtt ? "." : ",")}\n${cue.text.trim()}\n`).join("\n");
    const blob = new Blob([vtt ? `WEBVTT\n\n${body}` : body], { type: vtt ? "text/vtt;charset=utf-8" : "application/x-subrip;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${(state.project?.name || state.project?.title || "subtitles").replace(/[\\/:*?"<>|]/g, "-")}.${format}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    showNotice("已导出字幕文本与时间。字体、颜色和描边随项目保存，由 MP4 字幕烧录采用。");
  }

  function replaceAll() {
    const search = el("#search-text").value;
    if (!search) return showNotice("请先输入要查找的文字。", true);
    const replacement = el("#replace-text").value;
    let count = 0;
    state.cues.forEach((cue) => { const hits = cue.text.split(search).length - 1; if (hits) { cue.text = cue.text.split(search).join(replacement); count += hits; } });
    if (!count) return showNotice("没有找到匹配文字。", true);
    markDirty(); render(); showNotice(`已替换 ${count} 处文字。`);
  }

  function applyOffset() {
    const seconds = Number(el("#offset-value").value);
    if (!Number.isFinite(seconds) || seconds === 0) return showNotice("请输入非零偏移秒数。", true);
    state.cues.forEach((cue) => { const duration = cue.end - cue.start; cue.start = Math.max(0, cue.start + seconds); cue.end = Math.max(cue.start + duration, cue.end + seconds); });
    markDirty(); render(); showNotice(`全部字幕已${seconds > 0 ? "延后" : "提前"} ${Math.abs(seconds)} 秒。`);
  }

  el("#subtitle-file").addEventListener("change", importFile);
  el("#save-project").addEventListener("click", saveProject);
  el("#add-subtitle").addEventListener("click", addCue);
  el("#empty-add").addEventListener("click", addCue);
  el("#export-srt").addEventListener("click", () => exportFile("srt"));
  el("#export-vtt").addEventListener("click", () => exportFile("vtt"));
  el("#replace-all").addEventListener("click", replaceAll);
  el("#apply-offset").addEventListener("click", applyOffset);
  ["#subtitle-font-size", "#subtitle-color", "#subtitle-bottom-margin", "#subtitle-outline"].forEach((selector) => el(selector).addEventListener("input", () => {
    state.subtitleStyle = normalizeStyle({ fontSize: Number(el("#subtitle-font-size").value), textColor: el("#subtitle-color").value, bottomMargin: Number(el("#subtitle-bottom-margin").value), outline: Number(el("#subtitle-outline").value) });
    renderStyle(); markDirty();
  }));
  const asrClient = VoiceCloud.createClient();
  const asr = { busy: false, cues: [], account: null };
  function asrKey() { return `ocvg.asr.${state.project?.id || queryId || "none"}.${asrClient.session()?.user?.id || "anonymous"}`; }
  function asrRemember(value) { sessionStorage.setItem(asrKey(), JSON.stringify(value)); }
  function asrPending() { try { return JSON.parse(sessionStorage.getItem(asrKey()) || "null"); } catch (_) { return null; } }
  async function runAsr(resumeOnly = false) {
    if (asr.busy || !state.project) return;
    asr.busy = true; el("#asr-action").disabled = true; el("#asr-adopt").disabled = true;
    const owner = asrClient.session()?.user?.id;
    try {
      if (!owner) throw new Error("请先在云端工作室登录，再返回本页识别。");
      let pending = asrPending();
      const file = el("#asr-file").files?.[0];
      const fileSignature = file ? `${file.name}:${file.size}:${file.lastModified}:${el("#asr-language").value}` : "";
      const changedFile = !resumeOnly && file && pending?.fileSignature !== fileSignature;
      if (pending && !pending.id) {
        try { const job = await asrClient.request(`/asr/requests/${encodeURIComponent(pending.clientId)}`); pending.id = job.id; asrRemember(pending); }
        catch (error) { if (error.status !== 404) throw error; if (changedFile) throw new Error("上次提交尚未核对完成，请重新选择原文件继续原请求，避免重复创建任务。"); }
      }
      if (changedFile && pending?.id) {
        const previous = await asrClient.request(`/asr/jobs/${encodeURIComponent(pending.id)}`);
        if (!["completed", "failed"].includes(previous.status)) throw new Error("上一识别任务仍在运行，请先继续查询原任务；完成后再提交新文件。");
        pending = null;
      }
      if (!pending?.id) {
        if (resumeOnly) return;
        if (!file) throw new Error("请选择音视频文件。提交中断后请重新选择同一个文件，以恢复原请求。");
        if (file.size > 50 * 1024 * 1024 || !file.size) throw new Error("音视频文件不能为空，且不能超过 50 MB。");
        const capability = await asrClient.request("/asr/status");
        if (!capability.available) throw new Error("独立语音识别服务尚未就绪，当前字幕已保留；请稍后重试。");
        pending ||= { clientId: `asr-${uid()}`, fileSignature };
        asrRemember(pending);
        const body = new FormData(); body.append("file", file); body.append("language", el("#asr-language").value);
        el("#asr-note").textContent = "正在上传音视频并创建识别任务…";
        const job = await asrClient.request("/asr/jobs", { method: "POST", headers: { "Idempotency-Key": pending.clientId }, body });
        pending.id = job.id; asrRemember(pending);
      }
      asr.cues = []; el("#asr-preview").hidden = true;
      for (let attempt = 0; attempt < 180; attempt++) {
        if (asrClient.session()?.user?.id !== owner) throw new Error("账号已切换，已停止读取原任务。");
        const job = await asrClient.request(`/asr/jobs/${encodeURIComponent(pending.id)}`);
        if (job.status === "failed") { sessionStorage.removeItem(asrKey()); throw new Error(job.error || "语音识别失败，当前字幕已保留。"); }
        if (job.status === "completed") {
          if (!Array.isArray(job.cues) || !job.cues.length) throw new Error("没有识别到字幕，当前字幕已保留。");
          asr.cues = job.cues; asr.account = owner;
          el("#asr-note").textContent = `识别完成：${job.cues.length} 条字幕。请预览并校对，再点击采用。`;
          el("#asr-preview").textContent = job.cues.map((cue) => `${formatTime(cue.start)} → ${formatTime(cue.end)}\n${cue.text}`).join("\n\n");
          el("#asr-preview").hidden = false; el("#asr-adopt").disabled = false;
          return;
        }
        el("#asr-note").textContent = "CPU 正在识别。可离开后返回继续查询；当前字幕保持不变。";
        await new Promise((resolve) => setTimeout(resolve, 5000));
      }
      el("#asr-note").textContent = "任务仍在处理，可点击继续任务查询原结果。";
    } catch (error) { el("#asr-note").textContent = error.message || "识别请求失败，当前字幕已保留。"; }
    finally { asr.busy = false; el("#asr-action").disabled = false; }
  }
  el("#asr-action").addEventListener("click", () => runAsr(false));
  el("#asr-adopt").addEventListener("click", () => {
    if (!asr.cues.length || asr.account !== asrClient.session()?.user?.id) return showNotice("请登录识别任务所属账号后采用结果。", true);
    if (!window.confirm(`采用 ${asr.cues.length} 条识别结果将替换当前字幕。确认采用？`)) return;
    state.cues = asr.cues.map(normalizeCue); markDirty(); render();
    showNotice("已采用识别结果并准备自动保存，请继续校对字幕。");
  });
  document.addEventListener("ocvg:flush-project", (event) => { if (state.dirty && !saveProject()) event.preventDefault(); });
  document.addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); saveProject(); } });
  document.addEventListener("click", (event) => { if (event.target.closest?.("a[href]") && state.dirty && !saveProject()) event.preventDefault(); }, true);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden" && state.dirty) saveProject(); });
  addEventListener("pagehide", () => { if (state.dirty) saveProject(); });
  addEventListener("beforeunload", (event) => { if (state.dirty && !saveProject()) { event.preventDefault(); event.returnValue = ""; } });

  loadProject();
  loadStyleControls();
  savedContent = JSON.stringify([state.cues.map(({ id, sceneId, start, end, text, hidden }, index) => ({ id, sceneId, index: index + 1, start, end, text, hidden })), state.subtitleStyle]);
  render();
  if (asrPending()) runAsr(true);
  else if (asrClient.session()?.user?.id) asrClient.request("/asr/status").then((capability) => { if (!capability.available) el("#asr-note").textContent = "独立语音识别服务尚未就绪，可继续手动编辑或导入字幕。"; }).catch((error) => { el("#asr-note").textContent = error.message; });
})();
