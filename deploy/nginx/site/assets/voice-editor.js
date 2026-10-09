(() => {
  "use strict";

  const STORAGE_KEY = "ocvg.projects.v1";
  const queryId = new URLSearchParams(location.search).get("project");
  const requestedScene = new URLSearchParams(location.search).get("scene");
  const el = (selector, root = document) => root.querySelector(selector);
  const state = { root: null, projects: [], project: null, lines: [], dirty: false, speakingId: null };
  const emotionLabels = { natural: "自然", calm: "平静", happy: "愉快", sad: "悲伤" };
  const cloud = VoiceCloud.createClient();
  const activeJobs = new Set();
  let voices = [], playingAudio = null, playingUrl = "";
  let savedVoiceSignature = null;
  let savedContent = null, saveTimer = null;
  let sceneLocated = false;

  function requestedSceneLine(line) {
    if (!requestedScene) return false;
    if (String(line.sceneId || line.scene_id || "") === requestedScene) return true;
    const cue = (state.project?.subtitles || []).find((item) => String(item.id) === String(line.id));
    return String(cue?.sceneId || cue?.scene_id || "") === requestedScene;
  }

  function uid() {
    return globalThis.crypto?.randomUUID?.() || `voice-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function unpackProjects(raw) {
    if (Array.isArray(raw)) return raw;
    if (Array.isArray(raw?.projects)) return raw.projects;
    if (Array.isArray(raw?.items)) return raw.items;
    if (raw && typeof raw === "object") return Object.values(raw).filter((item) => item && typeof item === "object");
    return [];
  }

  function sourceLines(project) {
    if (Array.isArray(project.voiceLines) && project.voiceLines.length) return project.voiceLines;
    const audio = Array.isArray(project.audio) ? project.audio : [];
    const subtitles = Array.isArray(project.subtitles) ? project.subtitles : Array.isArray(project.captions) ? project.captions : [];
    const scenes = Array.isArray(project.scenes) ? project.scenes : Array.isArray(project.storyboard) ? project.storyboard : [];
    if (subtitles.length) return subtitles.map((cue, index) => {
      const sceneId = cue.sceneId || cue.scene_id || cue.id;
      const clip = audio.find((item) => String(item.sceneId || item.scene_id || item.id) === String(sceneId));
      return {
        ...clip,
        id: cue.id || uid(), sceneId, subtitleText: cue.text || cue.content || "",
        speechText: cue.speechText || cue.ttsText || cue.text || cue.content || "",
        status: clip && (clip.status === "completed" || clip.url || clip.audioUrl) ? "generated" : cue.hidden ? "hidden" : "draft", index
      };
    });
    if (audio.length) return audio.map((item, index) => {
      const sceneId = item.sceneId || item.scene_id || item.id;
      const scene = scenes.find((candidate) => String(candidate.sceneId || candidate.scene_id || candidate.id) === String(sceneId)) || {};
      const text = item.subtitle || item.text || item.narration || scene.subtitle || scene.narration || scene.text || "";
      return {
        ...item,
        id: item.id || uid(), sceneId, subtitleText: text, speechText: item.speechText || item.ttsText || text,
        status: item.status === "completed" || item.url || item.audioUrl ? "generated" : "draft", index
      };
    });
    return scenes.map((scene, index) => ({ id: uid(), sceneId: scene.sceneId || scene.scene_id || scene.id, subtitleText: scene.subtitle || scene.narration || scene.text || "", speechText: scene.ttsText || scene.narration || scene.text || "", index }));
  }

  function normalizeLine(line, index) {
    const subtitleText = String(line.subtitleText ?? line.subtitle ?? line.text ?? line.content ?? "");
    return {
      ...line,
      id: line.id || uid(), sceneId: line.sceneId || line.scene_id || null,
      subtitleText, speechText: String(line.speechText ?? line.ttsText ?? line.readingText ?? subtitleText),
      speed: clamp(Number(line.speed ?? 1), .5, 2), pause: clamp(Number(line.pause ?? line.pauseMs ?? 400), 0, 5000),
      emotion: emotionLabels[line.emotion] ? line.emotion : "natural",
      status: ["ready", "generated"].includes(line.status) ? line.status : "draft", order: index
    };
  }

  function clamp(value, min, max) { return Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : min; }

  function loadProject() {
    try {
      state.root = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      state.projects = unpackProjects(state.root);
      state.project = state.projects.find((item) => String(item.id ?? item.projectId) === String(queryId)) || (!queryId ? state.projects[0] : null);
    } catch { state.root = []; state.projects = []; }
    if (!state.project) {
      el("#project-name").textContent = queryId ? "未找到该项目" : "未选择项目";
      showNotice("无法读取项目。请先从项目工作台进入，或检查浏览器本地项目数据。", true);
      return;
    }
    const name = state.project.name || state.project.title || "未命名项目";
    el("#project-name").textContent = name;
    document.title = `${name} · 逐句配音`;
    el("#back-project").href = `/workspace/?project=${encodeURIComponent(state.project.id ?? state.project.projectId ?? "")}`;
    state.lines = sourceLines(state.project).map(normalizeLine);
    savedVoiceSignature = JSON.stringify(state.project.voiceLines || []);
    savedContent = JSON.stringify(state.lines.map((line, index) => ({ ...line, index: index + 1 })));
  }

  function markDirty(line) {
    if (line && line.status === "generated") line.status = "draft";
    state.dirty = true;
    el("#save-state").textContent = "保存中";
    el("#save-state").classList.add("dirty");
    clearTimeout(saveTimer); saveTimer = setTimeout(() => saveProject(true), 400);
  }

  function showNotice(message, error = false) {
    const node = el("#notice");
    node.textContent = message;
    node.className = `notice show${error ? " error" : ""}`;
    clearTimeout(showNotice.timer);
    showNotice.timer = setTimeout(() => node.classList.remove("show"), 4200);
  }

  function hasCurrentAudio(line) {
    const account = cloud.session()?.user?.id;
    return Boolean(account && line.status === "generated" && line.cloudAudio?.account === account && line.cloudAudio?.url);
  }

  function refreshSummary() {
    const ready = state.lines.filter(hasCurrentAudio).length;
    const chars = state.lines.reduce((total, line) => total + line.speechText.length, 0);
    el("#summary").textContent = `${state.lines.length} 个句段 · ${chars} 个朗读字`;
    el("#progress-text").textContent = `${ready} / ${state.lines.length} 已生成配音`;
    el("#progress-bar").style.width = `${state.lines.length ? ready / state.lines.length * 100 : 0}%`;
  }

  function render() {
    const list = el("#voice-list");
    list.replaceChildren();
    el("#empty-state").classList.toggle("show", state.lines.length === 0);
    state.lines.forEach((line, index) => {
      const row = el("#voice-template").content.firstElementChild.cloneNode(true);
      row.dataset.id = line.id;
      if (requestedSceneLine(line)) { row.classList.add("scene-target"); row.setAttribute("tabindex", "-1"); row.setAttribute("aria-label", "当前镜头的配音句段"); }
      const isReady = hasCurrentAudio(line);
      row.classList.toggle("is-ready", isReady);
      el(".line-number", row).textContent = String(index + 1).padStart(2, "0");
      el(".status-badge", row).textContent = isReady ? "已生成配音" : line.cloudAudio ? "音频待更新或需原账号" : "草稿";
      el(".subtitle-copy", row).textContent = line.subtitleText || "（无字幕文本）";
      const speech = el(".speech-text", row);
      const speed = el(".line-speed", row);
      const pause = el(".line-pause", row);
      const emotion = el(".line-emotion", row);
      speech.value = line.speechText;
      speed.value = String(line.speed);
      pause.value = String(line.pause);
      emotion.value = line.emotion;
      el(".speed-control output", row).textContent = `${line.speed.toFixed(2)}×`;
      el(".ready-line", row).textContent = isReady ? "改为草稿" : "标记就绪";
      el(".ready-line", row).hidden = true;
      el(".merge-line", row).disabled = index === state.lines.length - 1;
      const pending = line.cloudRequest && !["completed", "failed", "cancelled"].includes(line.cloudRequest.status);
      const generate = el(".generate-line", row);
      generate.textContent = activeJobs.has(line.id) ? "生成中…" : pending ? "继续任务" : "云端生成";
      generate.disabled = activeJobs.has(line.id);
      generate.addEventListener("click", () => generateLine(line));
      el(".cloud-play", row).hidden = !line.cloudAudio;
      el(".cloud-download", row).disabled = !line.cloudAudio;
      el(".cloud-play", row).addEventListener("click", () => previewLine(line));
      el(".cloud-download", row).addEventListener("click", () => playCloud(line, true));
      if (pending) el(".status-badge", row).textContent = line.cloudRequest.status === "unknown" ? "等待核对提交" : "云端处理中";
      if (activeJobs.has(line.id)) row.querySelectorAll("textarea,input,select,.split-line,.merge-line,.more-remove,.ready-line").forEach((control) => { control.disabled = true; });
      speech.addEventListener("input", () => { line.speechText = speech.value; markDirty(line); refreshSummary(); updateRowStatus(row, line); });
      speed.addEventListener("input", () => { line.speed = Number(speed.value); el(".speed-control output", row).textContent = `${line.speed.toFixed(2)}×`; markDirty(line); updateRowStatus(row, line); });
      pause.addEventListener("change", () => { line.pause = Number(pause.value); markDirty(); });
      emotion.addEventListener("change", () => { line.emotion = emotion.value; markDirty(line); updateRowStatus(row, line); });
      el(".preview-line", row).textContent = line.cloudAudio ? "试听配音" : "本机试听";
      el(".preview-line", row).addEventListener("click", () => line.cloudAudio ? playCloud(line, false) : previewLine(line));
      el(".ready-line", row).addEventListener("click", () => { line.status = isReady ? "draft" : "ready"; markDirty(); render(); });
      el(".more-remove", row).addEventListener("click", () => { stopPreview(); state.lines.splice(index, 1); markDirty(); render(); });
      el(".split-line", row).addEventListener("click", () => splitLine(index, speech.selectionStart));
      el(".merge-line", row).addEventListener("click", () => mergeLine(index));
      list.append(row);
    });
    refreshSummary();
    const target = el(".voice-row.scene-target");
    if (target && !sceneLocated) { sceneLocated = true; target.scrollIntoView({ behavior: "smooth", block: "center" }); target.focus({ preventScroll: true }); }
  }

  function updateRowStatus(row, line) {
    row.classList.remove("is-ready");
    el(".status-badge", row).textContent = "草稿";
    el(".ready-line", row).textContent = "标记就绪";
    line.status = "draft";
    refreshSummary();
  }

  function previewLine(line) {
    if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) return showNotice("当前浏览器不支持本地语音试听。", true);
    const text = line.speechText.trim();
    if (!text) return showNotice("请先填写朗读文本。", true);
    stopPreview();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "zh-CN";
    utterance.rate = line.speed;
    utterance.pitch = line.emotion === "happy" ? 1.12 : line.emotion === "serious" || line.emotion === "sad" ? .9 : 1;
    utterance.onend = () => { state.speakingId = null; };
    utterance.onerror = () => { state.speakingId = null; showNotice("本地试听未能播放，请检查系统语音设置。", true); };
    state.speakingId = line.id;
    speechSynthesis.speak(utterance);
  }

  function stopPreview() {
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    if (playingAudio) { playingAudio.pause(); playingAudio = null; }
    if (playingUrl) { URL.revokeObjectURL(playingUrl); playingUrl = ""; }
    state.speakingId = null;
  }

  function splitPoint(text, cursor) {
    if (Number.isInteger(cursor) && cursor > 0 && cursor < text.length) return cursor;
    const candidates = [...text.matchAll(/[，。！？；,.!?;]\s*/g)].map((match) => match.index + match[0].length);
    return candidates.sort((a, b) => Math.abs(a - text.length / 2) - Math.abs(b - text.length / 2))[0] || Math.ceil(text.length / 2);
  }

  function splitLine(index, cursor) {
    const line = state.lines[index];
    const text = line.speechText.trim();
    if (text.length < 2) return showNotice("这一句太短，无法拆分。", true);
    const point = splitPoint(text, cursor);
    const first = text.slice(0, point).trim();
    const second = text.slice(point).trim();
    if (!first || !second) return showNotice("请把光标放在要拆分的位置。", true);
    const subtitlePoint = Math.round(line.subtitleText.length * first.length / text.length);
    const secondSubtitle = line.subtitleText.slice(subtitlePoint).trim();
    line.speechText = first;
    line.subtitleText = line.subtitleText.slice(0, subtitlePoint).trim() || line.subtitleText;
    line.status = "draft";
    delete line.cloudRequest; delete line.cloudAudio;
    state.lines.splice(index + 1, 0, { ...line, id: uid(), sceneId: line.sceneId, subtitleText: secondSubtitle, speechText: second, status: "draft" });
    markDirty(); render();
  }

  function mergeLine(index) {
    const line = state.lines[index];
    const next = state.lines[index + 1];
    if (!next) return;
    if (activeJobs.has(line.id) || activeJobs.has(next.id)) return showNotice("请等待这两句的云端任务结束后再合并。", true);
    line.subtitleText = `${line.subtitleText.trim()} ${next.subtitleText.trim()}`.trim();
    line.speechText = `${line.speechText.trim()} ${next.speechText.trim()}`.trim();
    line.pause = next.pause;
    line.status = "draft";
    delete line.cloudRequest; delete line.cloudAudio;
    state.lines.splice(index + 1, 1);
    markDirty(); render();
  }

  function addLine() {
    state.lines.push({ id: uid(), sceneId: null, subtitleText: "", speechText: "", speed: Number(el("#global-speed").value), pause: Number(el("#global-pause").value), emotion: el("#global-emotion").value, status: "draft" });
    markDirty(); render();
    const row = el(`.voice-row[data-id="${CSS.escape(state.lines.at(-1).id)}"]`);
    row?.scrollIntoView({ behavior: "smooth", block: "center" });
    el(".speech-text", row)?.focus();
  }

  function applyGlobal() {
    if (!state.lines.length) return showNotice("当前没有句段可应用。", true);
    if (activeJobs.size) return showNotice("请等待云端配音任务结束后再应用全局设置。", true);
    const speed = Number(el("#global-speed").value);
    const pause = Number(el("#global-pause").value);
    const emotion = el("#global-emotion").value;
    state.lines.forEach((line) => { const affectsAudio = line.speed !== speed || line.emotion !== emotion; line.speed = speed; line.pause = pause; line.emotion = emotion; if (affectsAudio && line.status === "generated") line.status = "draft"; });
    markDirty(); render(); showNotice("全局设置已应用到全部句段。");
  }

  function saveProject(quiet = false) {
    clearTimeout(saveTimer); saveTimer = null;
    if (!state.project) return showNotice("当前没有可保存的项目。", true);
    state.project.voiceLines = state.lines.map((line, index) => ({ ...line, index: index + 1 }));
    const content = JSON.stringify(state.project.voiceLines);
    if (content === savedContent) { state.dirty = false; el("#save-state").textContent = "已保存到本机"; el("#save-state").classList.remove("dirty"); el("#save-project").hidden = true; return true; }
    state.project.updatedAt = new Date().toISOString();
    try {
      const fresh = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      const current = unpackProjects(fresh).find((item) => item.id === state.project.id);
      if (!current) throw new Error("project missing");
      if (savedVoiceSignature !== null && JSON.stringify(current.voiceLines || []) !== savedVoiceSignature) throw new Error("配音已在其他页面修改，请刷新后再保存；未覆盖其他页面的内容。");
      current.voiceLines = state.project.voiceLines; current.updatedAt = state.project.updatedAt;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(fresh));
      savedVoiceSignature = JSON.stringify(current.voiceLines);
      savedContent = content;
      state.dirty = false;
      el("#save-state").textContent = "已保存到本机";
      el("#save-state").classList.remove("dirty");
      el("#save-state").removeAttribute("title"); el("#save-project").hidden = true;
      document.dispatchEvent(new CustomEvent("ocvg:project-saved", { detail: { id: state.project.id } }));
      return true;
    } catch (error) { state.dirty = true; el("#save-state").textContent = "保存失败"; el("#save-state").title = error.message || "请保留当前页面后重试。"; el("#save-project").hidden = false; showNotice(error.message?.startsWith("配音已") ? error.message : "项目已移除或浏览器存储空间不足，保存失败。", true); return false; }
  }

  async function loadVoices() {
    const select = el("#cloud-voice");
    if (!cloud.session()?.user?.id) { el("#cloud-state").textContent = "登录后自动返回当前作品。"; return; }
    try {
      const data = await cloud.request("/cloud/voices?page_size=100");
      voices = data.items || []; select.replaceChildren();
      for (const voice of voices) { const option = document.createElement("option"); option.value = voice.id; option.textContent = voice.display_name; select.append(option); }
      select.value = state.project?.source?.voiceId || data.default_voice_id || voices[0]?.id || "";
      if (!select.value && voices.length) select.value = voices[0].id;
      el("#cloud-state").textContent = "按实际报价扣除积分；仅重新生成所选句段。";
      for (const line of state.lines) if (line.cloudRequest?.jobId && !["completed", "failed", "cancelled"].includes(line.cloudRequest.status)) generateLine(line, true);
    } catch (error) { el("#cloud-state").textContent = error.message; }
  }

  async function generateLine(line, resume = false) {
    if (!state.project || activeJobs.has(line.id)) return;
    activeJobs.add(line.id); render();
    try {
      const account = cloud.session()?.user?.id;
      if (!account) throw new Error("请先在云端工作台登录。");
      let request = line.cloudRequest;
      if (request && !["completed", "failed", "cancelled"].includes(request.status)) {
        if (request.account !== account) throw new Error("该任务属于另一个账号，请切回原账号查看。");
      } else {
        if (resume) return;
        const voice = voices.find((item) => item.id === el("#cloud-voice").value);
        const payload = VoiceCloud.payload(line, voice);
        const quote = await cloud.request("/cloud/quotes", { method: "POST", body: JSON.stringify(payload) });
        if (!window.confirm(`为这一句生成云端配音，预计 ${quote.estimated_credits} 积分。继续？`)) return;
        request = { account, clientId: `line-${uid()}`, payload, fingerprint: VoiceCloud.fingerprint(line, voice), status: "submitting", createdAt: new Date().toISOString() };
        line.cloudRequest = request;
        if (!saveProject(true)) { delete line.cloudRequest; return; }
      }
      if (!request.jobId) {
        const job = await cloud.request("/cloud/jobs", { method: "POST", headers: { "Idempotency-Key": request.clientId }, body: JSON.stringify({ ...request.payload, client_job_id: request.clientId }) });
        request.jobId = job.job_id; request.status = job.status; saveProject(true);
        if (!request.jobId) throw new Error("提交响应未返回任务编号，可安全继续原请求。");
      }
      for (let attempt = 0; attempt < 180; attempt++) {
        if (cloud.session()?.user?.id !== account) throw new Error("账号已切换，停止查询。");
        const job = await cloud.request(`/cloud/jobs/${encodeURIComponent(request.jobId)}`);
        request.status = job.status;
        if (job.status === "completed") {
          const chunk = job.result?.chunks?.find((item) => Number(item.index) === 0);
          if (!chunk) { request.status = "awaiting_result"; throw new Error("任务已完成，但没有返回音频片段。可继续查询原任务。"); }
          line.cloudAudio = { url: `/api/v1/cloud/jobs/${encodeURIComponent(job.job_id)}/chunks/0/audio`, jobId: job.job_id, account, fingerprint: request.fingerprint, createdAt: new Date().toISOString() };
          const voice = request.payload.voice;
          line.status = VoiceCloud.fingerprint(line, voice) === request.fingerprint ? "generated" : "draft";
          saveProject(true); showNotice(line.status === "generated" ? "云端配音已生成，可试听或下载 WAV。" : "配音已生成，但文字或参数已修改；已保留为旧版本，请重新生成当前设置。"); return;
        }
        if (["failed", "cancelled"].includes(job.status)) { saveProject(true); throw new Error(job.error_message || "任务失败或已取消，可重新生成。"); }
        await new Promise((resolve) => setTimeout(resolve, 3000));
      }
      showNotice("任务仍在运行，点击“继续任务”或下次打开查看；不会重复提交。");
    } catch (error) {
      if (line.cloudRequest && !line.cloudRequest.jobId) line.cloudRequest.status = [400, 402, 403, 409, 413, 415, 422].includes(error.status) ? "failed" : "unknown";
      saveProject(true); showNotice(error.message, true);
    } finally { activeJobs.delete(line.id); render(); }
  }

  async function playCloud(line, download) {
    try {
      if (line.cloudAudio.account !== cloud.session()?.user?.id) throw new Error("请登录生成该音频的账号。");
      const blob = await cloud.request(line.cloudAudio.url, {}, true);
      if (download) {
        const url = URL.createObjectURL(blob), anchor = document.createElement("a");
        anchor.href = url; anchor.download = `配音-${state.lines.indexOf(line) + 1}.wav`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      } else {
        stopPreview(); playingUrl = URL.createObjectURL(blob); playingAudio = new Audio(playingUrl); await playingAudio.play();
        if (line.status !== "generated") showNotice("播放的是修改前的云端版本，请重新生成以采用当前设置。");
      }
    } catch (error) { showNotice(error.message, true); }
  }

  el("#save-project").addEventListener("click", () => saveProject());
  el("#reload-voices").addEventListener("click", loadVoices);
  document.addEventListener("ocvg:flush-project", (event) => { if (!saveProject(true)) event.preventDefault(); });
  el("#add-line").addEventListener("click", addLine);
  el("#empty-add").addEventListener("click", addLine);
  el("#apply-global").addEventListener("click", applyGlobal);
  el("#stop-preview").addEventListener("click", stopPreview);
  el("#global-speed").addEventListener("input", (event) => { el("#global-speed-value").textContent = `${Number(event.target.value).toFixed(2)}×`; });
  document.addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); saveProject(); } if (event.key === "Escape") stopPreview(); });
  document.addEventListener("click", (event) => { if (event.target.closest?.("a[href]") && state.dirty && !saveProject(true)) event.preventDefault(); }, true);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden" && state.dirty) saveProject(true); });
  addEventListener("pagehide", () => { if (state.dirty) saveProject(true); });
  addEventListener("beforeunload", (event) => { if (state.dirty && !saveProject(true)) { event.preventDefault(); event.returnValue = ""; } });

  loadProject();
  render();
  loadVoices();
})();
