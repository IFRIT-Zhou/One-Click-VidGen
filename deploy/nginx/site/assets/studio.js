(function () {
  "use strict";

  const estimateCard = document.querySelector("#generation-estimate");
  const estimateAnchor = document.querySelector("#one-click-panel");
  const estimateSidebar = document.querySelector(".workspace-summary");
  const estimateDesktop = window.matchMedia("(min-width: 1181px)");
  function placeEstimateCard() {
    if (!estimateCard || !estimateAnchor || !estimateSidebar) return;
    if (estimateDesktop.matches) estimateSidebar.append(estimateCard);
    else estimateAnchor.before(estimateCard);
  }
  placeEstimateCard();
  estimateDesktop.addEventListener("change", placeEstimateCard);

  const API_BASE = "/api/v1";
  const auth = window.OCVGSessionRefresh.session;
  const TERMINAL = new Set(["completed", "failed", "cancelled"]);
  const IMAGE_TERMINAL = new Set(["SUCCESS", "FAILED", "ERROR", "CANCELLED"]);
  const state = {
    session: readSession(), account: null, voices: [], selectedVoice: null,
    quote: null, cloudReady: false, modelReady: false, storyboard: [], storyboardPlanning: false, videoSubmitting: false, videoSubmission: null,
    currentJob: null, pollTimer: null, audioBlobs: new Map(),
    imageJobs: [], imageBlobs: new Map(), objectUrls: [], composing: false,
    videoJob: null, videoJobPolling: false, referenceImages: [],
    videoPreviewJobId: null, videoPreviewUrl: null, videoPreviewLoading: false,
  };
  let audioEpoch = 0, imageEpoch = 0, videoEpoch = 0, videoRestoreRequest = 0;
  const element = (selector) => document.querySelector(selector);
  const sleep = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  const protectedTargets = new Set(["script-panel", "advanced-creation", "one-click-panel", "storyboard-panel", "voice-panel", "settings-panel", "task-panel", "compose-panel"]);
  const advancedCreationTargets = new Set(["style-panel", "voice-panel", "settings-panel"]);
  let pendingProtectedTarget = protectedTargets.has(window.location.hash.slice(1)) ? window.location.hash.slice(1) : null;
  let navigationFocusTarget = null;
  let navigationFocusTimer = 0;

  function readSession() { try { return JSON.parse(sessionStorage.getItem("ocvg-cloud-session")) || null; } catch (_) { return null; } }
  function saveSession(session) { state.session = session; if (session) sessionStorage.setItem("ocvg-cloud-session", JSON.stringify(session)); else sessionStorage.removeItem("ocvg-cloud-session"); document.dispatchEvent(new CustomEvent("ocvg:account-changed")); }
  function message(node, text, type = "error") { node.textContent = text; node.className = `inline-message show ${type}`; }
  function clearMessage(node) { node.textContent = ""; node.className = "inline-message"; }
  function unwrap(value) { return value && typeof value === "object" && value.data && value.code !== undefined ? value.data : value; }
  function errorText(error) {
    const value = String(error && error.message || error || "请求失败");
    const mappings = [[/401|credentials|token|登录/i, "登录状态已失效，请重新登录。"], [/insufficient|积分|balance/i, "可用积分不足，请先充值。"], [/Ray|timed out|timeout|503|unavailable/i, "云端服务暂时不可用，请稍后重试。"], [/quota|queue|并发|429/i, "当前任务额度已用完，请稍后再试。"], [/413|too large/i, "内容或文件超出允许大小。"], [/415|format|WAV|MP3|FLAC/i, "文件格式不受支持。"]];
    return (mappings.find(([pattern]) => pattern.test(value)) || [null, value])[1];
  }

  async function refreshToken(initial = auth.read()) {
    if (!initial?.refresh_token) return false;
    state.session = await auth.refresh(initial); return true;
  }

  async function api(path, options = {}, retry = true, initial = auth.read()) {
    const sentSession = initial ? auth.current(initial) : null;
    if (sentSession) state.session = sentSession;
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (sentSession?.access_token) headers.Authorization = `Bearer ${sentSession.access_token}`;
    if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
    if (response.status === 401 && retry && initial && await refreshToken(sentSession)) return api(path, options, false, initial);
    if (response.status === 204) { if (initial) state.session = auth.current(initial); return null; }
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("json") ? await response.json() : await response.text();
    if (initial) state.session = auth.current(initial);
    if (!response.ok || (data && typeof data === "object" && data.code && data.code !== 0 && data.code !== "0")) {
      const detail = typeof data === "object" ? (data.message || data.detail || data.code) : data;
      throw new Error(typeof detail === "string" ? detail : `HTTP ${response.status}`);
    }
    return data;
  }

  async function authenticatedBlob(pathOrUrl) {
    const target = new URL(String(pathOrUrl), window.location.origin);
    const isSameOriginApi = target.origin === window.location.origin && target.pathname.startsWith("/api/");
    const initial = auth.read(), sentSession = initial ? auth.current(initial) : null;
    if (sentSession) state.session = sentSession;
    const headers = isSameOriginApi && sentSession ? { Authorization: `Bearer ${sentSession.access_token}` } : {};
    let response = await fetch(target.href, { headers });
    if (response.status === 401 && isSameOriginApi && initial && await refreshToken(sentSession)) response = await fetch(target.href, { headers: { Authorization: `Bearer ${state.session.access_token}` } });
    if (!response.ok) throw new Error(`下载失败（HTTP ${response.status}）`);
    const blob = await response.blob();
    if (initial) state.session = auth.current(initial);
    return blob;
  }

  function splitText(text) {
    const clean = text.trim(); if (!clean) return [];
    const chunks = []; let remaining = clean;
    while (remaining.length) {
      if (remaining.length <= 900) { chunks.push(remaining); break; }
      let cut = Math.max(remaining.lastIndexOf("。", 900), remaining.lastIndexOf("！", 900), remaining.lastIndexOf("？", 900), remaining.lastIndexOf("\n", 900), remaining.lastIndexOf("；", 900));
      if (cut < 300) cut = 900; else cut += 1;
      chunks.push(remaining.slice(0, cut).trim()); remaining = remaining.slice(cut).trim();
    }
    return chunks.filter(Boolean);
  }

  function ttsChunks() {
    const texts = state.storyboard.length ? state.storyboard.map((scene) => scene.narration.trim()).filter(Boolean) : splitText(element("#script-input").value);
    return texts.map((text, index) => ({ index, text }));
  }

  function ttsPayload() {
    const emotion = element("#emotion").value;
    return {
      chunks: ttsChunks(),
      voice: { type: state.selectedVoice.type === "preset" ? "preset" : "uploaded", id: state.selectedVoice.id },
      emotion: { name: emotion || null, weight: Number(element("#emotion-weight").value) },
      audio: { speed: Number(element("#speed").value), volume: 1, pitch: Number(element("#pitch").value), sample_rate: 24000, channels: 1 },
      gpu_acceleration: true,
    };
  }

  function poolChat(system, user, planning) {
    if (!planning || !Number.isInteger(planning.stage)) throw new Error("分镜规划阶段无效");
    return api("/storyboard/plan-stage", { method: "POST", body: JSON.stringify({ ...planning, input: user }) });
  }

  function parseModelJson(raw) {
    const response = unwrap(raw);
    const content = response && response.choices && response.choices[0] && response.choices[0].message && response.choices[0].message.content;
    if (!content) throw new Error("文本模型没有返回有效内容");
    const cleaned = String(content).replace(/^\s*```(?:json)?/i, "").replace(/```\s*$/, "").trim();
    try { return JSON.parse(cleaned); } catch (_) {
      const start = cleaned.indexOf("{"); const end = cleaned.lastIndexOf("}");
      if (start >= 0 && end > start) return JSON.parse(cleaned.slice(start, end + 1));
      throw new Error("文本模型返回的分镜格式无效，请重新生成");
    }
  }

  async function checkHealth() {
    const node = element("#cloud-state");
    try {
      const health = await api("/health", {}, false); state.cloudReady = Boolean(health.ok && health.control_api && !health.ray_error);
      node.className = `cloud-state ${state.cloudReady ? "online" : "offline"}`;
      node.querySelector("strong").textContent = state.cloudReady ? "服务运行正常" : "配音集群维护中";
    } catch (_) { state.cloudReady = false; node.className = "cloud-state offline"; node.querySelector("strong").textContent = "无法连接服务"; }
    updateSubmit();
  }

  async function checkModelPool() {
    const node = element("#model-pool-state"); if (!state.session) return;
    node.textContent = "正在检测"; node.className = "pool-state checking";
    try { const result = unwrap(await api("/model-pool/status", { method: "POST", body: "{}" })); state.modelReady = Boolean(result && result.available); node.textContent = state.modelReady ? "模型号池可用" : "模型号池维护中"; node.className = `pool-state ${state.modelReady ? "online" : "offline"}`; }
    catch (_) { state.modelReady = false; node.textContent = "模型号池不可用"; node.className = "pool-state offline"; }
    updateStoryboardButton();
  }

  function renderAuth() {
    const loggedIn = Boolean(state.session && state.session.access_token);
    element("#auth-wall").classList.toggle("hidden", loggedIn); element("#workspace-form").classList.remove("is-locked");
    if (!element("#header-account").hasAttribute?.("data-account-trigger")) element("#header-account").textContent = loggedIn ? (state.session.user && state.session.user.email || "账户中心") : "登录账户";
    element("#studio-email").textContent = loggedIn ? (state.session.user && state.session.user.email || "已登录") : "尚未登录";
    updateSubmit(); updateStoryboardButton(); updateVideoJobButton();
  }

  async function loadAccount() {
    if (!state.session) return;
    try { state.account = await api("/account/summary"); const credits = state.account.credits || {}; const quota = state.account.quota || {}; element("#studio-credits").textContent = Number(credits.available || 0).toLocaleString("zh-CN"); element("#studio-daily").textContent = `${Number(quota.daily_characters_used || 0).toLocaleString("zh-CN")} / ${Number(quota.daily_characters_limit || 0).toLocaleString("zh-CN")}`; element("#studio-concurrency").textContent = `${quota.running_jobs || 0} / ${quota.max_concurrent_jobs || 0}`; }
    catch (_) { logout(false); }
  }

  async function loadVoices() {
    if (!state.session) return; element("#voice-loading").style.display = "block"; clearMessage(element("#voice-message"));
    try { const data = await api("/cloud/voices?page_size=100"); state.voices = data.items || []; if (!state.selectedVoice || !state.voices.some((voice) => voice.id === state.selectedVoice.id)) state.selectedVoice = state.voices.find((voice) => voice.id === data.default_voice_id) || state.voices[0] || null; renderVoices(); scheduleQuote(); }
    catch (error) { message(element("#voice-message"), errorText(error)); }
    finally { element("#voice-loading").style.display = "none"; }
  }

  function renderVoices() {
    const grid = element("#voice-grid"); grid.innerHTML = "";
    state.voices.forEach((voice, index) => {
      const card = document.createElement("article"); card.className = `voice-card ${state.selectedVoice && state.selectedVoice.id === voice.id ? "selected" : ""}`;
      card.innerHTML = `<button class="voice-select" type="button"><span class="voice-avatar">${String(index + 1).padStart(2, "0")}</span><span><strong>${escapeHtml(voice.display_name || voice.id)}</strong><small>${voice.type === "preset" ? "平台默认音色" : "我的音色"}</small></span><i>✓</i></button><div class="voice-actions">${voice.type === "preset" ? "<button data-preview type=\"button\">▶ 试听</button>" : "<button data-delete type=\"button\">删除音色</button>"}</div>`;
      card.querySelector(".voice-select").addEventListener("click", () => { if (state.selectedVoice?.id !== voice.id) invalidateStudioResults("audio"); state.selectedVoice = voice; renderVoices(); scheduleQuote(); scheduleStudioDraft(); });
      const preview = card.querySelector("[data-preview]"); if (preview) preview.addEventListener("click", (event) => previewVoice(voice, event.currentTarget));
      const remove = card.querySelector("[data-delete]"); if (remove) remove.addEventListener("click", () => deleteVoice(voice)); grid.appendChild(card);
    });
  }

  async function previewVoice(voice, button) {
    const oldText = button.textContent; button.disabled = true; button.textContent = "加载中…";
    try { const blob = await authenticatedBlob(`/api/v1/cloud/voices/${encodeURIComponent(voice.id)}/audio`); const url = rememberUrl(blob); const audio = new Audio(url); button.textContent = "■ 停止"; const reset = () => { button.textContent = oldText; button.disabled = false; }; audio.addEventListener("ended", reset, { once: true }); await audio.play(); button.onclick = () => { audio.pause(); reset(); }; }
    catch (error) { button.textContent = oldText; button.disabled = false; message(element("#voice-message"), errorText(error)); }
  }

  async function deleteVoice(voice) { if (!window.confirm(`确认删除音色“${voice.display_name}”吗？`)) return; try { await api(`/cloud/voices/${encodeURIComponent(voice.id)}`, { method: "DELETE" }); state.selectedVoice = null; await loadVoices(); message(element("#voice-message"), "音色已删除。", "success"); } catch (error) { message(element("#voice-message"), errorText(error)); } }
  function setDocumentStatus(text, type = "") {
    const node = element("#document-upload-status");
    node.textContent = text;
    node.className = `document-upload-status ${type}`.trim();
  }

  async function parseDocument() {
    const input = element("#document-file");
    const file = input.files[0];
    if (!file) return;
    const extension = file.name.split(".").pop().toLowerCase();
    if (!new Set(["txt", "docx"]).has(extension)) {
      setDocumentStatus("仅支持 TXT 或 DOCX 文档。", "error");
      input.value = "";
      return;
    }
    input.disabled = true;
    setDocumentStatus(`正在解析 ${file.name}…`, "loading");
    try {
      const form = new FormData();
      form.append("file", file);
      const result = unwrap(await api("/documents/parse", { method: "POST", body: form }));
      if (!result || typeof result.text !== "string") throw new Error("文档解析结果无效");
      const script = element("#script-input");
      if (script.maxLength > 0 && result.text.length > script.maxLength) {
        throw new Error(`文档正文 ${result.text.length.toLocaleString("zh-CN")} 字，超过 ${script.maxLength.toLocaleString("zh-CN")} 字上限，请拆分后上传。`);
      }
      script.value = result.text;
      script.dispatchEvent(new Event("input", { bubbles: true }));
      const parsedName = result.filename || file.name;
      setDocumentStatus(`已导入 ${parsedName}（${result.text.length.toLocaleString("zh-CN")} 字）`, "success");
      scheduleStudioDraft();
      script.focus();
    } catch (error) {
      setDocumentStatus(errorText(error), "error");
    } finally {
      input.disabled = false;
      input.value = "";
    }
  }

  async function uploadVoice() {
    const file = element("#voice-file").files[0]; const name = element("#voice-name").value.trim(); if (!file || !name) { message(element("#voice-message"), "请选择音频文件并填写音色名称。"); return; }
    const button = element("#upload-voice"); button.disabled = true; button.textContent = "正在上传…";
    try { const form = new FormData(); form.append("file", file); form.append("display_name", name); const result = await api("/cloud/voices", { method: "POST", headers: { "Idempotency-Key": `voice-web-${Date.now()}-${file.size}` }, body: form }); state.selectedVoice = result.voice; element("#voice-file").value = ""; element("#voice-name").value = ""; element("#upload-form").classList.remove("show"); await loadVoices(); message(element("#voice-message"), result.deduplicated ? "该音频已存在，已选中原音色。" : "个人音色上传成功。", "success"); }
    catch (error) { message(element("#voice-message"), errorText(error)); }
    finally { button.disabled = false; button.textContent = "上传音色"; }
  }

  function renderReferenceImages() {
    const node = element("#reference-list");
    if (!node) return;
    node.innerHTML = state.referenceImages.map((item, index) => `<span class="reference-chip"><span>${escapeHtml(item.name || `参考图 ${index + 1}`)}</span><button type="button" data-reference-remove="${index}" aria-label="移除参考图">×</button></span>`).join("");
    node.querySelectorAll("[data-reference-remove]").forEach((button) => button.addEventListener("click", () => {
      state.referenceImages.splice(Number(button.dataset.referenceRemove), 1);
      renderReferenceImages(); invalidateStudioResults("image");
    }));
  }

  async function uploadReferenceImages(event) {
    const input = event.currentTarget;
    const selected = Array.from(input.files || []);
    const uploadMethod = element("#image-method").value;
    const remaining = Math.max(0, 3 - state.referenceImages.length);
    const files = selected.slice(0, remaining);
    if (selected.length > remaining) {
      message(element("#job-message"), `最多上传 3 张角色参考图，本次仅处理前 ${remaining} 张。`);
    }
    if (!files.length) { input.value = ""; return; }
    input.disabled = true;
    try {
      for (const file of files) {
        const form = new FormData(); form.append("file", file);
        const result = unwrap(await api(`/image-pool/media/upload?provider=${uploadMethod === "ican" ? "ican" : "runninghub"}`, { method: "POST", body: form }));
        const url = result && (result.download_url || result.url);
        if (!url) throw new Error("参考图上传接口未返回地址");
        if (element("#image-method").value !== uploadMethod) throw new Error("上传期间渠道已切换，请重新上传参考图");
        state.referenceImages.push({ name: file.name, mediaId: result.media_id, downloadUrl: url });
        renderReferenceImages(); invalidateStudioResults("image");
      }
    } catch (error) {
      message(element("#job-message"), `参考图上传失败：${errorText(error)}`);
    } finally { input.disabled = false; input.value = ""; }
  }

  function updateStoryboardButton() {
    const button = element("#generate-storyboard"); if (!button) return;
    button.disabled = state.storyboardPlanning || state.videoSubmitting || !element("#script-input").value.trim() || Boolean(state.session && !state.modelReady);
    if (!state.session) button.textContent = "登录后生成分镜"; else if (!state.modelReady) button.textContent = "模型号池暂不可用"; else if (!element("#script-input").value.trim()) button.textContent = "请先输入文案"; else button.textContent = state.storyboard.length ? "重新生成分镜" : "用 AI 生成分镜";
  }

  function autoSceneCount(text) {
    const speed = Number(element("#speed").value) || 1;
    return Math.max(2, Math.min(16, Math.ceil(String(text || "").trim().length / (36 * speed))));
  }

  async function generateStoryboard(options = {}) {
    if (state.storyboardPlanning || (state.videoSubmitting && !options.forVideo)) return null;
    if (!requireGenerationLogin("storyboard-panel")) return;
    const exactCount = state.storyboard.length > 0;
    const text = element("#script-input").value.trim(); const count = exactCount ? Number(element("#storyboard-count").value) : autoSceneCount(text); const style = element("#visual-style").value;
    if (!Number.isInteger(count) || count < 1 || count > 16) { message(element("#storyboard-message"), "分镜数量请输入 1–16 的整数。"); return null; }
    if (!options.confirmed && exactCount && !window.confirm(`将按 ${count} 个镜头重新规划，替换当前分镜及其编辑内容。本次规划免费，确认重新生成？`)) return;
    const button = element("#generate-storyboard"); const progress = element("#agent-progress"); clearMessage(element("#storyboard-message")); button.disabled = true;
    const account = state.session?.user?.id, epoch = videoEpoch;
    const originalScenes = JSON.stringify(state.storyboard);
    const assertCurrent = () => {
      if (state.session?.user?.id !== account || videoEpoch !== epoch || element("#script-input").value.trim() !== text || element("#visual-style").value !== style || JSON.stringify(state.storyboard) !== originalScenes) throw new Error("账户、文案或设置已改变，已停止生成，请确认当前内容后重试。");
    };
    state.storyboardPlanning = true; updateVideoJobButton(); element("#storyboard-count").disabled = true;
    try {
      const descriptions = ["正在通读全文并提取人物、地点和叙事主线…", "正在划分语义镜头并校验完整旁白…", "正在生成统一风格的画面提示词…"];
      const nextStoryboard = await window.OCVGStoryboardPlan.generate({ script: text, style, count, exactCount, poolChat: async (...args) => { assertCurrent(); const reply = await poolChat(...args); assertCurrent(); return reply; }, parseModelJson, splitText, onProgress: (index, repairing) => {
        progress.innerHTML = `<b>Agent ${index}</b><span>${repairing ? "结果格式或内容未通过校验，正在免费修复一次。" : descriptions[index]}</span>`;
        options.onProgress?.(repairing ? "分镜校验未通过，正在免费修复。" : descriptions[index]);
      } });
      assertCurrent();
      invalidateStudioResults(); state.storyboard = nextStoryboard; state.storyboardUndo = null; scheduleStudioDraft();
      progress.innerHTML = `<b>规划完成</b><span>${state.storyboard.length} 个镜头，可在提交前直接修改。</span>`; renderStoryboard(); scheduleQuote(); message(element("#storyboard-message"), "分镜已生成，本次规划免费，未扣除积分。", "success");
      return nextStoryboard;
    } catch (error) { progress.innerHTML = ""; message(element("#storyboard-message"), errorText(error)); if (options.forVideo) throw error; return null; }
    finally { state.storyboardPlanning = false; element("#storyboard-count").disabled = false; updateStoryboardButton(); updateVideoJobButton(); loadAccount().catch(() => {}); }
  }

  function renderStoryboard() {
    const hasScenes = state.storyboard.length > 0;
    element("#storyboard-count-field").hidden = !hasScenes;
    element("#storyboard-manual").hidden = !hasScenes;
    element("#storyboard-count").value = hasScenes ? state.storyboard.length : "";
    element("#add-storyboard-scene").disabled = state.storyboard.length >= 16;
    if (!hasScenes) state.storyboardUndo = null;
    element("#undo-storyboard-delete").hidden = !state.storyboardUndo;
    const grid = element("#storyboard-grid"); grid.innerHTML = state.storyboard.map((scene, index) => `<article class="story-card"><div class="story-number">${String(index + 1).padStart(2, "0")}</div><div><input data-scene-title="${index}" value="${escapeAttribute(scene.title)}" aria-label="镜头标题" /><label>旁白<textarea data-scene-narration="${index}" maxlength="900">${escapeHtml(scene.narration)}</textarea></label><label>生图提示词<textarea data-scene-prompt="${index}" maxlength="4000">${escapeHtml(scene.prompt)}</textarea></label><div class="story-scene-actions">${index > 0 ? `<button class="quiet-button" type="button" data-scene-merge="${index}">合并到上一镜</button>` : ""}<button class="quiet-button scene-delete" type="button" data-scene-delete="${index}" ${state.storyboard.length === 1 ? "disabled" : ""}>删除此镜头</button></div></div></article>`).join("");
    grid.querySelectorAll("[data-scene-title]").forEach((input) => input.addEventListener("input", () => { state.storyboard[Number(input.dataset.sceneTitle)].title = input.value; clearStoryboardUndo(); scheduleStudioDraft(); }));
    grid.querySelectorAll("[data-scene-narration]").forEach((input) => input.addEventListener("input", () => { state.storyboard[Number(input.dataset.sceneNarration)].narration = input.value; clearStoryboardUndo(); syncStoryboardScript(); invalidateStudioResults("audio"); scheduleQuote(); }));
    grid.querySelectorAll("[data-scene-prompt]").forEach((input) => input.addEventListener("input", () => { state.storyboard[Number(input.dataset.scenePrompt)].prompt = input.value; clearStoryboardUndo(); invalidateStudioResults("image"); }));
    grid.querySelectorAll("[data-scene-merge]").forEach(button => button.addEventListener("click", () => editStoryboardScenes("merge", Number(button.dataset.sceneMerge))));
    grid.querySelectorAll("[data-scene-delete]").forEach(button => button.addEventListener("click", () => editStoryboardScenes("delete", Number(button.dataset.sceneDelete))));
  }

  function clearStoryboardUndo() { state.storyboardUndo = null; element("#undo-storyboard-delete").hidden = true; }
  function syncStoryboardScript() { element("#script-input").value = state.storyboard.map(scene => scene.narration.trim()).filter(Boolean).join("\n"); }
  function editStoryboardScenes(action, index = 0) {
    if (state.storyboardPlanning || state.videoSubmitting || state.composing || (state.videoJob && !TERMINAL.has(state.videoJob.status)) || (state.currentJob && !TERMINAL.has(state.currentJob.status)) || state.imageJobs.some(job => !IMAGE_TERMINAL.has(job.status))) {
      message(element("#storyboard-message"), "任务正在进行，请等待完成或取消后再增减镜头。"); return;
    }
    const original = state.storyboard.map(scene => ({ ...scene }));
    if (action === "add") {
      if (original.length >= 16) return;
      state.storyboard.push({ index: original.length, title: `镜头 ${original.length + 1}`, narration: "", description: "", prompt: "", imageStatus: "pending" });
      clearStoryboardUndo();
    } else if (action === "undo") {
      if (!state.storyboardUndo || state.storyboardUndo.account !== state.session?.user?.id) return;
      state.storyboard = state.storyboardUndo.scenes; element("#script-input").value = state.storyboardUndo.script; clearStoryboardUndo();
    } else {
      if (!Number.isInteger(index) || !original[index] || original.length <= 1) return;
      if (action === "merge") {
        if (index === 0) return;
        const previous = original[index - 1], current = original[index];
        const narration = [previous.narration.trim(), current.narration.trim()].filter(Boolean).join("\n");
        const prompt = [previous.prompt.trim(), current.prompt.trim()].filter(Boolean).join("\n");
        if (narration.length > 900 || prompt.length > 4000) { message(element("#storyboard-message"), "合并后内容过长，请先缩短旁白或提示词；也可指定较少镜头重新规划。"); return; }
        state.storyboard[index - 1] = { ...previous, narration, prompt };
        clearStoryboardUndo();
      } else if (action === "delete") {
        if (original[index].narration.trim() && !window.confirm(`删除第 ${index + 1} 镜及其旁白？第 1 步文案也会同步移除这段文字。若要保留旁白，请使用“合并到上一镜”。`)) return;
        state.storyboardUndo = { scenes: original, script: element("#script-input").value, account: state.session?.user?.id };
      } else return;
      state.storyboard.splice(index, 1); syncStoryboardScript();
    }
    state.storyboard = state.storyboard.map((scene, index) => ({ ...scene, index, imageStatus: "pending" }));
    invalidateStudioResults(); renderStoryboard(); scheduleQuote(); scheduleStudioDraft();
    element("#agent-progress").innerHTML = `<b>已更新</b><span>${state.storyboard.length} 个镜头</span>`;
    message(element("#storyboard-message"), action === "add" ? "已添加空白镜头，请填写旁白和生图提示词后再生成素材。" : action === "merge" ? "已合并，旁白和提示词均已保留，请检查合并后的画面描述。" : action === "undo" ? "已恢复删除的镜头及文案。" : "已删除，可点击“撤销删除”恢复。", "success");
    if (action === "add") element("#storyboard-grid").querySelector(`[data-scene-title="${state.storyboard.length - 1}"]`)?.focus();
  }

  let quoteTimer;
  function scheduleQuote() { window.clearTimeout(quoteTimer); updateTextSummary(); updateStoryboardButton(); quoteTimer = window.setTimeout(loadQuote, 450); }
  async function loadQuote() {
    state.quote = null; const chunks = ttsChunks(); if (!state.session || !state.selectedVoice || !chunks.length) { element("#summary-cost").textContent = "—"; updateSubmit(); return; }
    try { state.quote = await api("/cloud/quotes", { method: "POST", body: JSON.stringify(ttsPayload()) }); element("#summary-cost").textContent = `${state.quote.estimated_credits} 积分`; } catch (_) { element("#summary-cost").textContent = "报价失败"; }
    updateSubmit();
  }
  function updateTextSummary() { const text = element("#script-input").value; element("#text-counter").textContent = `${text.length.toLocaleString("zh-CN")} / 5,000`; element("#summary-characters").textContent = text.length.toLocaleString("zh-CN"); element("#summary-chunks").textContent = `${ttsChunks().length} / ${state.storyboard.length}`; }
  function updateVideoJobButton() {
    const button = element("#create-video-job"); if (!button) return;
    const active = state.videoJob && !TERMINAL.has(state.videoJob.status);
    const text = element("#script-input").value.trim();
    button.disabled = state.videoSubmitting || state.storyboardPlanning || !text || active || Boolean(state.session && !state.selectedVoice);
    if (state.videoSubmitting || state.storyboardPlanning) button.textContent = "正在规划与提交…";
    else if (!state.session) button.textContent = "登录后开始生成";
    else if (!text) button.textContent = "请先输入文案";
    else if (!state.selectedVoice) button.textContent = "请选择配音音色";
    else if (active) button.textContent = "完整视频正在生成";
    else button.textContent = "一键生成完整 MP4";
  }

  const icanVideoSizes = {"16:9": {"2k": "2048x1152", "2.5k": "2560x1440"}, "9:16": {"2k": "1152x2048", "2.5k": "1440x2560"}, "1:1": {"1k": "1024x1024"}};
  function updateImageChoices(preferred) {
    const ican = element("#image-method").value === "ican";
    const ratio = element("#aspect-ratio");
    for (const option of ratio.options) option.disabled = ican && !icanVideoSizes[option.value];
    if (ican && !icanVideoSizes[ratio.value]) ratio.value = "16:9";
    const quality = element("#image-resolution");
    const previous = preferred || quality.value;
    const choices = ican ? Object.keys(icanVideoSizes[ratio.value]) : ["1k", "2k", "4k"];
    quality.replaceChildren(...choices.map(value => new Option(value.toUpperCase(), value)));
    quality.value = choices.includes(previous) ? previous : (ican && choices.includes("2.5k") ? "2.5k" : choices[0]);
    element("#image-channel-hint").textContent = ican ? "ICAN 使用 GPT Image 2.5，0.04 元/张（0.04 积分）；横屏和竖屏默认 2.5K，方形支持 1K。" : "平价gpt image2.5 支持 1K、2K、4K 图片清晰度。";
  }
  function imageChannelPayload() {
    const method = element("#image-method").value;
    const ratio = element("#aspect-ratio").value;
    const resolution = element("#image-resolution").value;
    return method === "ican" ? {method, provider: "ican", model: "gpt-image-2.5", size: icanVideoSizes[ratio][resolution]} : {method, aspectRatio: ratio, resolution};
  }
  element("#image-method").addEventListener("change", () => {
    updateImageChoices(element("#image-method").value === "ican" ? "2.5k" : "1k");
    if (state.referenceImages.length) {
      state.referenceImages = []; renderReferenceImages();
      message(element("#job-message"), "已切换画面渠道，请为新渠道重新上传角色参考图。");
      element("#image-channel-hint").textContent += " 已清除旧渠道参考图，请重新上传。";
    }
    invalidateStudioResults("image"); scheduleStudioDraft();
  });
  element("#aspect-ratio").addEventListener("change", () => updateImageChoices());
  updateImageChoices();

  function videoJobPayload() {
    const emotion = element("#emotion").value;
    return {
      client_job_id: `web_video_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      script: element("#script-input").value.trim(),
      voice: { type: state.selectedVoice.type === "preset" ? "preset" : "user", id: state.selectedVoice.id },
      video: {
        method: element("#image-method").value,
        ...(element("#image-method").value === "ican" ? {size: imageChannelPayload().size} : {}),
        aspect_ratio: element("#aspect-ratio").value,
        resolution: element("#image-resolution").value,
        scene_count: state.storyboard.length || autoSceneCount(element("#script-input").value),
        visual_style: element("#visual-style").value,
      },
      audio: {
        speed: Number(element("#speed").value), pitch: Number(element("#pitch").value),
        emotion: emotion || null, emotion_weight: Number(element("#emotion-weight").value),
      },
      reference_image_urls: state.referenceImages.map((reference) => reference.downloadUrl),
      storyboard: state.storyboard.map((scene, index) => ({ index, title: scene.title, narration: scene.narration, description: scene.description || "", prompt: scene.prompt })),
    };
  }

  function videoStepLabel(key) { return ({ storyboard: "智能分镜", tts: "生成配音", images: "生成画面", compose: "合成视频", publish: "发布结果" })[key] || key; }
  function videoStepState(step) { return typeof step === "string" ? step : String(step && step.status || "pending"); }
  function videoStepStatusLabel(status) { return ({ pending: "等待处理", running: "正在处理", completed: "已完成", failed: "处理失败", cancelled: "已取消" })[status] || "状态未知"; }
  function videoStageLabel(stage) { return ({ storyboard: "智能分镜", tts: "生成配音", images: "生成画面", compose: "合成视频", publish: "发布结果" })[stage] || "等待调度"; }
  function videoJobMessage(job) {
    if (job.message) return job.message;
    return ({
      queued: "任务已进入云端队列", storyboarding: "正在规划视频分镜",
      generating_assets: "正在生成配音和画面", composing: "正在合成 MP4",
      publishing: "正在整理视频结果", completed: "完整视频已生成",
      failed: "完整视频生成失败", cancel_requested: "正在取消任务",
      cancelled: "任务已取消",
    })[job.status] || "云端正在处理完整视频";
  }
  function clearVideoPreview() {
    const video = element("#online-video");
    if (video) { video.pause(); video.removeAttribute("src"); video.load(); }
    if (state.videoPreviewUrl) {
      URL.revokeObjectURL(state.videoPreviewUrl);
      state.objectUrls = state.objectUrls.filter((url) => url !== state.videoPreviewUrl);
    }
    state.videoPreviewJobId = null;
    state.videoPreviewUrl = null;
    state.videoPreviewLoading = false;
  }
  function renderVideoExport() {
    const video = element("#online-video"); const empty = element("#online-video-empty");
    const button = element("#preview-video"); const status = element("#online-video-status");
    if (!video || !empty || !button || !status) return;
    const job = state.videoJob; const completed = Boolean(job && job.status === "completed");
    const loaded = completed && state.videoPreviewJobId === job.job_id && Boolean(state.videoPreviewUrl);
    video.classList.toggle("hidden", !loaded); empty.classList.toggle("hidden", loaded);
    button.disabled = !completed || state.videoPreviewLoading;
    button.textContent = state.videoPreviewLoading ? "正在加载在线视频…" : (loaded ? "重新播放" : (completed ? "在线查看 MP4" : "等待完整视频生成"));
    status.textContent = loaded ? `正在查看任务 ${job.job_id}` : (completed ? "视频已生成，可在线播放或下载" : (job ? videoJobMessage(job) : "尚无可查看的视频"));
  }
  async function previewVideoResult() {
    const job = state.videoJob;
    if (!job || job.status !== "completed") return;
    revealStudioTarget("compose-panel", "instant");
    if (state.videoPreviewJobId === job.job_id && state.videoPreviewUrl) {
      element("#online-video").play().catch(() => {});
      return;
    }
    clearVideoPreview(); state.videoPreviewLoading = true; renderVideoExport();
    try {
      const blob = await authenticatedBlob(`/api/v1/video-jobs/${encodeURIComponent(job.job_id)}/result`);
      if (!state.videoJob || state.videoJob.job_id !== job.job_id) return;
      state.videoPreviewJobId = job.job_id; state.videoPreviewUrl = rememberUrl(blob);
      element("#online-video").src = state.videoPreviewUrl; renderVideoExport();
      element("#online-video").play().catch(() => {});
    } catch (error) { message(element("#compose-message"), `在线视频加载失败：${errorText(error)}`); }
    finally { state.videoPreviewLoading = false; renderVideoExport(); }
  }
  function renderVideoJob() {
    const node = element("#video-job"); const job = state.videoJob;
    if (!job) { node.className = "video-job empty"; node.innerHTML = "<strong>尚未开始</strong><p>输入文案并选择音色后即可一次生成完整视频。</p>"; updateVideoJobButton(); renderVideoExport(); return; }
    const progress = Math.max(0, Math.min(100, Number(job.progress || 0))); const steps = job.steps || {};
    const stepMarkup = ["storyboard", "tts", "images", "compose", "publish"].map((key) => { const value = steps[key]; const status = videoStepState(value); const detail = value && typeof value === "object" ? (value.message || (value.progress !== undefined ? `${value.progress}%` : videoStepStatusLabel(status))) : videoStepStatusLabel(status); return `<li class="${escapeAttribute(status)}"><i></i><span><strong>${videoStepLabel(key)}</strong><small>${escapeHtml(detail)}</small></span></li>`; }).join("");
    const error = job.error && (job.error.message || job.error.detail || job.error); const credits = job.credits && (job.credits.used || job.credits.charged || job.credits.reserved);
    node.className = `video-job ${escapeAttribute(job.status || "queued")}`;
    node.innerHTML = `<div class="video-job-head"><div><span>${statusLabel(job.status)} · ${videoStageLabel(job.stage)}</span><strong>${escapeHtml(videoJobMessage(job))}</strong></div><b>${progress}%</b></div><div class="video-job-bar"><i style="width:${progress}%"></i></div><ol class="video-job-steps">${stepMarkup}</ol><div class="video-job-meta"><small>任务编号 ${escapeHtml(job.job_id)}</small>${credits !== undefined ? `<small>积分 ${escapeHtml(credits)}</small>` : ""}</div>${error ? `<p class="video-job-error">${escapeHtml(error)}</p>` : ""}<div class="video-job-actions">${!TERMINAL.has(job.status) ? '<button class="quiet-button" data-video-cancel type="button">取消任务</button>' : ""}${job.status === "completed" ? '<button class="quiet-button" data-video-preview type="button">在线查看</button><button class="button primary" data-video-download type="button">下载 MP4</button>' : ""}</div>`;
    const cancel = node.querySelector("[data-video-cancel]"); if (cancel) cancel.addEventListener("click", cancelVideoJob);
    const preview = node.querySelector("[data-video-preview]"); if (preview) preview.addEventListener("click", previewVideoResult);
    const download = node.querySelector("[data-video-download]"); if (download) download.addEventListener("click", downloadVideoResult);
    updateVideoJobButton(); renderVideoExport();
  }

  async function createVideoJob() {
    if (state.videoSubmitting || state.storyboardPlanning || (state.videoJob && !TERMINAL.has(state.videoJob.status))) return;
    if (!requireGenerationLogin("one-click-panel")) return;
    if (!window.confirm(state.storyboard.length ? "将使用当前已编辑的分镜生成视频，不再重新规划。配音和图片按实际用量计费，确认开始？" : "将先免费完成全文分析、分镜规划和画面提示词，再生成视频。配音和图片按实际用量计费，确认开始？")) return;
    const button = element("#create-video-job"); clearMessage(element("#video-job-message")); button.disabled = true; button.textContent = "正在创建完整任务…";
    const planningProgress = element("#video-planning-progress");
    const account = state.session?.user?.id;
    state.videoSubmitting = true; updateVideoJobButton(); updateStoryboardButton();
    try {
      if (!state.storyboard.length) {
        const planned = await generateStoryboard({ confirmed: true, forVideo: true, onProgress: text => { planningProgress.textContent = text; } });
        if (!planned) throw new Error("分镜规划未完成，未提交视频任务。");
      }
      if (state.session?.user?.id !== account) throw new Error("账户已改变，未提交视频任务。");
      const payload = videoJobPayload();
      window.OCVGStoryboardPlan.plan({ scenes: payload.storyboard.map(scene => ({ ...scene, description: scene.description || scene.prompt })) }, payload.script, splitText);
      window.OCVGStoryboardPlan.prompts({ scenes: payload.storyboard }, payload.storyboard);
      if (payload.storyboard.some(scene => scene.title.length > 160 || scene.narration.length > 1500 || scene.prompt.length > 4000 || scene.description.length > 2000)) throw new Error("分镜字段超过长度限制，请缩短后重试。");
      planningProgress.textContent = `已采用 ${payload.storyboard.length} 个完整分镜，正在提交视频任务…`;
      const epoch = videoEpoch;
      const { client_job_id, ...requestContent } = payload;
      const fingerprint = JSON.stringify({ account, request: requestContent });
      if (state.videoSubmission?.fingerprint !== fingerprint) state.videoSubmission = { fingerprint, clientJobId: client_job_id };
      payload.client_job_id = state.videoSubmission.clientJobId;
      const job = unwrap(await api("/video-jobs", { method: "POST", headers: { "Idempotency-Key": payload.client_job_id }, body: JSON.stringify(payload) }));
      clearVideoPreview();
      if (state.session?.user?.id !== account || epoch !== videoEpoch) throw new Error("账户或创作内容已改变，旧任务可从历史记录查看");
      state.videoSubmission = null;
      planningProgress.textContent = `已提交 ${payload.storyboard.length} 个镜头，后续按此分镜生成配音和画面。`;
      state.videoJob = { ...job, account }; localStorage.setItem("ocvg-recent-video-job", job.job_id); renderVideoJob(); pollVideoJob(job.job_id);
    } catch (error) { planningProgress.textContent = ""; message(element("#video-job-message"), errorText(error)); }
    finally { state.videoSubmitting = false; updateVideoJobButton(); updateStoryboardButton(); }
  }

  async function getVideoJob(jobId) { const account = state.session?.user?.id; const job = unwrap(await api(`/video-jobs/${encodeURIComponent(jobId)}`)); if (state.session?.user?.id !== account) throw new Error("账户已切换"); return { ...job, account }; }
  async function pollVideoJob(jobId) {
    if (state.videoJobPolling) return; state.videoJobPolling = true;
    try {
      while (state.session && state.videoJob && state.videoJob.job_id === jobId && !TERMINAL.has(state.videoJob.status)) {
        await sleep(2400); const job = await getVideoJob(jobId); if (state.videoJob?.job_id !== jobId) return; state.videoJob = job; scheduleStudioDraft(); renderVideoJob();
      }
      if (state.videoJob && state.videoJob.status === "completed") message(element("#video-job-message"), "完整视频已生成，可以在线查看或下载 MP4。", "success");
      await loadAccount();
    } catch (error) { message(element("#video-job-message"), errorText(error)); }
    finally { state.videoJobPolling = false; updateVideoJobButton(); }
  }

  async function cancelVideoJob() {
    if (!state.videoJob) return;
    const epoch = videoEpoch, jobId = state.videoJob.job_id;
    try {
      const job = unwrap(await api(`/video-jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }));
      if (epoch !== videoEpoch || state.videoJob?.job_id !== jobId) return;
      state.videoJob = { ...state.videoJob, ...job }; scheduleStudioDraft(); renderVideoJob(); await loadVideoJobs();
    }
    catch (error) { message(element("#video-job-message"), errorText(error)); }
  }

  async function downloadVideoResult() {
    if (!state.videoJob) return; const button = element("#video-job").querySelector("[data-video-download]"); if (button) { button.disabled = true; button.textContent = "正在下载…"; }
    try { const blob = await authenticatedBlob(`/api/v1/video-jobs/${encodeURIComponent(state.videoJob.job_id)}/result`); downloadBlob(blob, `OneClickVidGen_${state.videoJob.job_id}.mp4`); }
    catch (error) { message(element("#video-job-message"), errorText(error)); }
    finally { if (button) { button.disabled = false; button.textContent = "下载 MP4"; } }
  }

  async function restoreVideoJob(jobId) {
    const epoch = videoEpoch, request = ++videoRestoreRequest;
    try {
      const job = await getVideoJob(jobId);
      if (epoch !== videoEpoch || request !== videoRestoreRequest) return;
      if (state.videoPreviewJobId !== jobId) clearVideoPreview();
      state.videoJob = job; localStorage.setItem("ocvg-recent-video-job", jobId); renderVideoJob();
      if (!TERMINAL.has(state.videoJob.status)) pollVideoJob(jobId);
    }
    catch (error) { message(element("#video-job-message"), errorText(error)); }
  }

  async function loadVideoJobs() {
    if (!state.session) return; const history = element("#video-job-history");
    try {
      const data = unwrap(await api("/video-jobs?page=1&page_size=8")); const items = data.items || [];
      history.innerHTML = items.length ? `<div class="history-head"><span>最近完整视频</span><span>状态 / 进度</span></div>${items.map((job) => `<button type="button" data-video-job-id="${escapeAttribute(job.job_id)}"><span><strong>${escapeHtml(job.job_id)}</strong><small>${new Date(job.created_at).toLocaleString("zh-CN")}</small></span><span><i class="job-status ${escapeAttribute(job.status)}">${statusLabel(job.status)}</i><small>${Number(job.progress || 0)}%</small></span></button>`).join("")}` : "";
      history.querySelectorAll("[data-video-job-id]").forEach((item) => item.addEventListener("click", () => restoreVideoJob(item.dataset.videoJobId)));
      const storedId = localStorage.getItem("ocvg-recent-video-job"); const recentId = items.some((job) => job.job_id === storedId) ? storedId : (items[0] && items[0].job_id);
      if (recentId && !element("#script-input").value.trim() && !studioBinding && (!state.videoJob || state.videoJob.job_id !== recentId)) await restoreVideoJob(recentId);
    } catch (error) { history.innerHTML = ""; message(element("#video-job-message"), errorText(error)); }
  }

  function updateSubmit() {
    const button = element("#submit-job"); const ready = Boolean(state.session && state.selectedVoice && state.storyboard.length && state.quote && state.cloudReady && !state.composing);
    button.disabled = state.session ? !ready : !element("#script-input").value.trim(); updateVideoJobButton();
    if (!state.session) button.textContent = "登录后开始创作"; else if (!state.storyboard.length) button.textContent = "请先生成 AI 分镜"; else if (!state.cloudReady) button.textContent = "配音集群维护中"; else if (!state.selectedVoice) button.textContent = "请选择音色"; else if (!state.quote) button.textContent = "正在计算配音报价…"; else button.textContent = `并行生成全部素材 · 配音 ${state.quote.estimated_credits} 积分`;
  }

  async function submitJob() {
    if (!requireGenerationLogin("task-panel")) return;
    try {
      window.OCVGStoryboardPlan.plan({ scenes: state.storyboard.map(scene => ({ ...scene, description: scene.description || scene.prompt })) }, element("#script-input").value, splitText);
      window.OCVGStoryboardPlan.prompts({ scenes: state.storyboard }, state.storyboard);
    } catch (error) { message(element("#job-message"), errorText(error)); return; }
    clearMessage(element("#job-message")); const estimated = Number(state.quote && state.quote.estimated_credits || 0); const available = Number(state.account && state.account.credits && state.account.credits.available || 0);
    if (available < estimated) { message(element("#job-message"), "当前积分不足以预扣配音费用，图片还会按实际用量另行结算。"); return; }
    if (!window.confirm(`配音预计 ${estimated} 积分，图片按实际用量另行结算，分镜规划免费。确认生成全部素材？`)) return;
    resetAssets(); const button = element("#submit-job"); button.disabled = true; button.textContent = "正在提交云端任务…";
    const epoch = audioEpoch; const jobAccount = state.session?.user?.id;
    const clientJobId = `web_video_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    try {
      const result = await api("/cloud/jobs", { method: "POST", headers: { "Idempotency-Key": clientJobId }, body: JSON.stringify({ ...ttsPayload(), client_job_id: clientJobId }) });
      if (state.session?.user?.id !== jobAccount || epoch !== audioEpoch) throw new Error("账户或配音内容已改变，停止加载旧素材");
      state.currentJob = { ...result, account: jobAccount, total_chunks: ttsChunks().length, progress: 0 }; renderCurrentJob();
      const imageRequest = generateAllImages(clientJobId);
      const ttsDone = pollTtsJob(result.job_id); await Promise.allSettled([ttsDone, imageRequest]); await loadAccount(); updateAssetProgress(); maybeEnableCompose();
    } catch (error) { message(element("#job-message"), errorText(error)); }
    finally { updateSubmit(); }
  }

  function invalidateStudioResults(kind = "all") {
    videoEpoch++; state.videoJob = null; clearVideoPreview(); renderVideoJob();
    if (kind !== "image") { audioEpoch++; state.currentJob = null; state.audioBlobs.clear(); renderCurrentJob(); }
    if (kind !== "audio") { imageEpoch++; state.imageJobs = []; state.imageBlobs.clear(); }
    renderAssets(); updateAssetProgress(); maybeEnableCompose(); scheduleStudioDraft();
  }
  async function resumeBoundTasks() {
    const jobs = [];
    if (state.currentJob?.job_id) jobs.push(pollTtsJob(state.currentJob.job_id));
    if (state.videoJob?.job_id && !TERMINAL.has(state.videoJob.status)) jobs.push(pollVideoJob(state.videoJob.job_id));
    for (const item of state.imageJobs) if (item.taskId && !IMAGE_TERMINAL.has(item.status)) jobs.push(generateOneImage(item, "resume"));
    await Promise.allSettled(jobs);
  }
  function resetAssets() { state.audioBlobs.clear(); state.imageBlobs.clear(); state.imageJobs = []; stableUrls.forEach((url) => URL.revokeObjectURL(url)); stableUrls.clear(); element("#asset-results").innerHTML = ""; element("#compose-progress").textContent = ""; clearMessage(element("#compose-message")); updateAssetProgress(); maybeEnableCompose(); }
  async function generateAllImages(batchId) {
    const imageSettings = imageChannelPayload();
    const references = state.referenceImages.map(reference => reference.downloadUrl);
    const queue = state.storyboard.map((scene) => ({ scene, imageSettings, references, account: state.session?.user?.id, status: "submitting", taskId: null, error: null })); state.imageJobs = queue; renderAssets(); updateAssetProgress();
    let cursor = 0; const worker = async () => { while (cursor < queue.length) { const item = queue[cursor++]; await generateOneImage(item, batchId); } };
    await Promise.all(Array.from({ length: Math.min(3, queue.length) }, worker)); renderAssets(); updateAssetProgress(); maybeEnableCompose();
  }
  async function generateOneImage(item, batchId) {
    const epoch = imageEpoch;
    try {
      if (state.session?.user?.id !== item.account || epoch !== imageEpoch || !state.imageJobs.includes(item)) return;
      const clientJobId = `${batchId}-image-${item.scene.index}`;
      if (!item.taskId) {
      const created = unwrap(await api("/image-pool/generate", { method: "POST", headers: { "Idempotency-Key": clientJobId }, body: JSON.stringify({ clientJobId, prompt: item.scene.prompt, ...(item.imageSettings || imageChannelPayload()), imageUrls: item.references || state.referenceImages.map((reference) => reference.downloadUrl) }) }));
      item.taskId = created.taskId || created.task_id; if (!item.taskId) throw new Error("图片服务没有返回任务编号"); item.status = "QUEUED"; scheduleStudioDraft(); renderAssets();
      }
      const deadline = Date.now() + 15 * 60 * 1000;
      while (Date.now() < deadline) {
        if (state.session?.user?.id !== item.account || epoch !== imageEpoch || !state.imageJobs.includes(item)) return;
        const result = unwrap(await api("/image-pool/query", { method: "POST", body: JSON.stringify({ taskId: item.taskId }) })); if (epoch !== imageEpoch || !state.imageJobs.includes(item)) return; item.status = String(result.status || "RUNNING").toUpperCase(); item.imageUrl = result.imageUrl || result.image_url || result.download_url; if (item.status === "SUCCESS") scheduleStudioDraft(); renderAssets(); updateAssetProgress();
        if (IMAGE_TERMINAL.has(item.status)) {
          if (item.status !== "SUCCESS" || !item.imageUrl) throw new Error(result.message || "图片生成失败");
          try { const blob = await authenticatedBlob(item.imageUrl); if (epoch !== imageEpoch || !state.imageJobs.includes(item)) return; state.imageBlobs.set(item.scene.index, blob); } catch (error) { throw new Error(`图片已生成但浏览器无法下载：${errorText(error)}`); }
          renderAssets(); updateAssetProgress(); maybeEnableCompose(); return;
        }
        await sleep(2200);
      }
      throw new Error("图片任务等待超时");
    } catch (error) { item.status = "FAILED"; item.error = errorText(error); renderAssets(); updateAssetProgress(); }
  }

  function statusLabel(status) { return ({ queued: "排队中", running: "生成中", finalizing: "整理结果", storyboarding: "生成分镜中", generating_assets: "生成素材中", composing: "合成视频中", publishing: "发布结果中", completed: "已完成", failed: "失败", cancelled: "已取消", cancel_requested: "正在取消" })[status] || "状态未知"; }
  function renderCurrentJob() {
    const node = element("#current-task"); const job = state.currentJob;
    if (!job) { node.className = "current-task empty"; node.innerHTML = '<div class="empty-illustration">◎</div><div><strong>还没有正在处理的任务</strong><p>完成分镜和配音设置后，可并行生成全部素材。</p></div>'; return; }
    const progress = Number(job.progress || 0); node.className = `current-task ${job.status || "queued"}`;
    node.innerHTML = `<div class="task-progress-ring" style="background:conic-gradient(var(--teal) ${Math.min(100, progress)}%,#deebe8 0)"><strong>${progress}%</strong></div><div class="task-progress-copy"><span>${statusLabel(job.status)}</span><strong>${escapeHtml(job.message || "云端配音处理中")}</strong><div class="task-bar"><i style="width:${Math.min(100, progress)}%"></i></div><small>任务编号 ${escapeHtml(job.job_id || "")}</small></div>${["queued", "running", "finalizing"].includes(job.status) ? '<button class="quiet-button" data-cancel type="button">取消任务</button>' : ""}`;
    const cancel = node.querySelector("[data-cancel]"); if (cancel) cancel.addEventListener("click", () => cancelJob(job.job_id));
  }

  async function fetchReadyAudio(job) {
    const chunks = job && job.result && Array.isArray(job.result.chunks) ? job.result.chunks : [];
    await Promise.all(chunks.map(async (chunk) => { const index = Number(chunk.index); if (state.audioBlobs.has(index)) return; const path = chunk.audio_url || `/api/v1/cloud/jobs/${encodeURIComponent(job.job_id)}/chunks/${index}/audio`; const blob = await authenticatedBlob(path); if (state.currentJob?.job_id !== job.job_id) return; state.audioBlobs.set(index, blob); renderAssets(); updateAssetProgress(); maybeEnableCompose(); }));
  }
  async function pollTtsJob(jobId) {
    const epoch = audioEpoch;
    window.clearInterval(state.pollTimer);
    for (;;) {
      const account = state.session?.user?.id; const job = await api(`/cloud/jobs/${encodeURIComponent(jobId)}`); if (state.session?.user?.id !== account || epoch !== audioEpoch || state.currentJob?.job_id !== jobId) return; state.currentJob = { ...job, account }; scheduleStudioDraft(); await fetchReadyAudio(job); renderCurrentJob(); updateAssetProgress();
      if (TERMINAL.has(job.status)) { if (job.status !== "completed") message(element("#job-message"), job.message || `配音任务${statusLabel(job.status)}`); await loadJobs(); return job; }
      await sleep(2400);
    }
  }
  async function cancelJob(jobId) {
    const epoch = audioEpoch;
    try {
      const job = await api(`/cloud/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
      if (epoch !== audioEpoch || state.currentJob?.job_id !== jobId) return;
      state.currentJob = { ...state.currentJob, ...job }; scheduleStudioDraft(); renderCurrentJob(); await loadAccount(); await loadJobs();
    } catch (error) { message(element("#job-message"), errorText(error)); }
  }
  async function loadJobs() {
    if (!state.session) return; const list = element("#job-list");
    try { const data = await api("/cloud/jobs?page=1&page_size=8"); const items = data.items || []; list.innerHTML = items.length ? `<div class="history-head"><span>最近配音任务</span><span>状态 / 进度</span></div>${items.map((job) => `<article><div><strong>${escapeHtml(job.job_id)}</strong><small>${new Date(job.created_at).toLocaleString("zh-CN")}</small></div><div><span class="job-status ${job.status}">${statusLabel(job.status)}</span><small>${job.progress || 0}%</small></div></article>`).join("")}` : ""; }
    catch (_) { list.innerHTML = ""; }
  }

  function renderAssets() {
    const node = element("#asset-results"); if (!state.imageJobs.length && !state.audioBlobs.size) { node.innerHTML = ""; return; }
    node.innerHTML = state.storyboard.map((scene, index) => { const imageItem = state.imageJobs[index]; const imageBlob = state.imageBlobs.get(index); const audioBlob = state.audioBlobs.get(index); const imageSource = imageBlob ? rememberStableUrl(imageBlob, `image-${index}`) : ""; const audioSource = audioBlob ? rememberStableUrl(audioBlob, `audio-${index}`) : ""; return `<article class="asset-card"><div class="asset-preview">${imageSource ? `<img src="${imageSource}" alt="${escapeAttribute(scene.title)}" />` : `<span>${imageItem ? imageStatusLabel(imageItem.status) : "等待图片"}</span>`}</div><div><strong>${escapeHtml(scene.title)}</strong><small>${escapeHtml(scene.narration)}</small>${audioSource ? `<audio class="asset-audio" controls preload="none" src="${audioSource}"></audio>` : ""}<div class="asset-tags"><span class="${audioBlob ? "ready" : ""}">${audioBlob ? "音频已下载" : "等待音频"}</span><span class="${imageBlob ? "ready" : imageItem && imageItem.status === "FAILED" ? "failed" : ""}">${imageBlob ? "图片已下载" : imageItem && imageItem.error ? escapeHtml(imageItem.error) : "等待图片"}</span></div></div></article>`; }).join("");
  }
  function imageStatusLabel(status) { return ({ submitting: "提交中", QUEUED: "排队中", RUNNING: "生成中", SUCCESS: "下载中", FAILED: "生成失败" })[status] || "等待图片"; }
  function updateAssetProgress() {
    const total = Math.max(1, state.storyboard.length); const audio = state.audioBlobs.size; const images = state.imageBlobs.size;
    element("#audio-progress-label").textContent = state.currentJob ? `${audio} / ${total} · ${statusLabel(state.currentJob.status)}` : "未开始"; element("#audio-progress-bar").style.width = `${audio / total * 100}%`;
    element("#image-progress-label").textContent = state.imageJobs.length ? `${images} / ${total}` : "未开始"; element("#image-progress-bar").style.width = `${images / total * 100}%`;
  }
  function maybeEnableCompose() { const ready = state.storyboard.length > 0 && state.audioBlobs.size === state.storyboard.length && state.imageBlobs.size === state.storyboard.length && !state.composing; const button = element("#compose-video"); button.disabled = !ready; button.textContent = ready ? "预览并导出 WebM" : "等待素材完成"; if (ready) { element("#video-placeholder").classList.add("hidden"); drawPosterFrame(); } }

  function canvasSize() { const ratio = element("#aspect-ratio").value; return ({ "9:16": [720, 1280], "1:1": [960, 960], "2:1": [1280, 640] })[ratio] || [1280, 720]; }
  async function loadImageBlob(blob) { return new Promise((resolve, reject) => { const image = new Image(); const url = rememberUrl(blob); image.onload = () => resolve(image); image.onerror = reject; image.src = url; }); }
  function drawScene(context, canvas, image, scene) {
    context.fillStyle = "#071f25"; context.fillRect(0, 0, canvas.width, canvas.height); const scale = Math.max(canvas.width / image.naturalWidth, canvas.height / image.naturalHeight); const width = image.naturalWidth * scale; const height = image.naturalHeight * scale; context.drawImage(image, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
    const gradient = context.createLinearGradient(0, canvas.height * .58, 0, canvas.height); gradient.addColorStop(0, "rgba(4,20,24,0)"); gradient.addColorStop(1, "rgba(4,20,24,.86)"); context.fillStyle = gradient; context.fillRect(0, canvas.height * .5, canvas.width, canvas.height * .5); context.fillStyle = "white"; context.textAlign = "center"; context.font = `600 ${Math.max(24, Math.round(canvas.width / 38))}px system-ui, sans-serif`; drawWrappedText(context, scene.narration, canvas.width / 2, canvas.height - Math.max(55, canvas.height * .08), canvas.width * .82, Math.max(35, canvas.width / 28), 3);
  }
  function drawWrappedText(context, text, x, bottom, maxWidth, lineHeight, maxLines) { const chars = [...String(text)]; const lines = []; let line = ""; chars.forEach((char) => { if (context.measureText(line + char).width > maxWidth && line) { lines.push(line); line = char; } else line += char; }); if (line) lines.push(line); const shown = lines.slice(0, maxLines); if (lines.length > maxLines) shown[maxLines - 1] = `${shown[maxLines - 1].slice(0, -1)}…`; shown.forEach((value, index) => context.fillText(value, x, bottom - (shown.length - 1 - index) * lineHeight)); }
  async function drawPosterFrame() { try { const canvas = element("#video-canvas"); [canvas.width, canvas.height] = canvasSize(); const image = await loadImageBlob(state.imageBlobs.get(0)); drawScene(canvas.getContext("2d"), canvas, image, state.storyboard[0]); } catch (_) {} }

  async function composeVideo() {
    const button = element("#compose-video"); clearMessage(element("#compose-message")); state.composing = true; button.disabled = true; button.textContent = "正在本地合成…"; updateSubmit();
    let audioContext;
    try {
      if (!window.MediaRecorder || !HTMLCanvasElement.prototype.captureStream) throw new Error("当前浏览器不支持视频导出，请使用最新版 Chrome 或 Edge");
      const canvas = element("#video-canvas"); [canvas.width, canvas.height] = canvasSize(); const context = canvas.getContext("2d"); const images = await Promise.all(state.storyboard.map((_, index) => loadImageBlob(state.imageBlobs.get(index))));
      audioContext = new (window.AudioContext || window.webkitAudioContext)(); const buffers = [];
      for (let index = 0; index < state.storyboard.length; index += 1) buffers.push(await audioContext.decodeAudioData(await state.audioBlobs.get(index).arrayBuffer()));
      const totalFrames = buffers.reduce((sum, buffer) => sum + buffer.length, 0); const channels = Math.max(...buffers.map((buffer) => buffer.numberOfChannels)); const combined = audioContext.createBuffer(channels, totalFrames, audioContext.sampleRate); let offset = 0; const boundaries = [0];
      buffers.forEach((buffer) => { for (let channel = 0; channel < channels; channel += 1) combined.getChannelData(channel).set(buffer.getChannelData(Math.min(channel, buffer.numberOfChannels - 1)), offset); offset += buffer.length; boundaries.push(offset / audioContext.sampleRate); });
      const destination = audioContext.createMediaStreamDestination(); const source = audioContext.createBufferSource(); source.buffer = combined; source.connect(destination); const canvasStream = canvas.captureStream(24); const stream = new MediaStream([...canvasStream.getVideoTracks(), ...destination.stream.getAudioTracks()]);
      const mimeType = ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"].find((type) => MediaRecorder.isTypeSupported(type)) || ""; const recorder = new MediaRecorder(stream, mimeType ? { mimeType, videoBitsPerSecond: 5_000_000 } : undefined); const parts = []; recorder.ondataavailable = (event) => { if (event.data.size) parts.push(event.data); };
      const done = new Promise((resolve, reject) => { recorder.onstop = resolve; recorder.onerror = () => reject(recorder.error || new Error("视频编码失败")); }); let current = -1; const started = audioContext.currentTime;
      function paint() { const elapsed = audioContext.currentTime - started; const index = Math.max(0, Math.min(images.length - 1, boundaries.findIndex((boundary, boundaryIndex) => boundaryIndex > 0 && elapsed < boundary) - 1)); if (index !== current) { current = index; drawScene(context, canvas, images[index], state.storyboard[index]); } element("#compose-progress").textContent = `本地编码 ${Math.min(100, Math.round(elapsed / combined.duration * 100))}%`; if (elapsed < combined.duration) window.requestAnimationFrame(paint); }
      drawScene(context, canvas, images[0], state.storyboard[0]); recorder.start(1000); source.start(); paint(); source.onended = () => window.setTimeout(() => recorder.state !== "inactive" && recorder.stop(), 250); await done; stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(parts, { type: mimeType || "video/webm" }); downloadBlob(blob, `OneClickVidGen_${new Date().toISOString().slice(0, 10)}.webm`); element("#compose-progress").textContent = `导出完成 · ${(blob.size / 1024 / 1024).toFixed(1)} MB`; message(element("#compose-message"), "视频已在当前设备完成合成并开始下载，云端没有执行视频渲染。", "success");
    } catch (error) { message(element("#compose-message"), errorText(error)); }
    finally { if (audioContext) audioContext.close(); state.composing = false; maybeEnableCompose(); updateSubmit(); }
  }

  function downloadProject() {
    const project = {
      format: "oneclick-vidgen-project", version: 1, created_at: new Date().toISOString(),
      script: element("#script-input").value,
      method: element("#image-method").value,
      aspect_ratio: element("#aspect-ratio").value,
      resolution: element("#image-resolution").value,
      visual_style: element("#visual-style").value,
      scene_count: state.storyboard.length || autoSceneCount(element("#script-input").value),
      voice: state.selectedVoice ? { id: state.selectedVoice.id, display_name: state.selectedVoice.display_name, type: state.selectedVoice.type } : null,
      audio: { speed: Number(element("#speed").value), pitch: Number(element("#pitch").value), emotion: element("#emotion").value, emotion_weight: Number(element("#emotion-weight").value) },
      scenes: state.storyboard.map(({ index, title, narration, description, prompt }) => ({ index, title, narration, description, prompt })),
    };
    downloadBlob(new Blob([JSON.stringify(project, null, 2)], { type: "application/json" }), "OneClickVidGen_project.json");
  }

  const loginReturn = window.OCVGStudioProject.safeReturn(new URLSearchParams(window.location.search).get("return"));
  function returnAfterLogin() { if (loginReturn && state.session?.access_token) { window.location.replace(loginReturn); return true; } return false; }
  let studioBinding = null;
  let studioDraftTimer = 0;
  let studioDirty = false;
  function scheduleStudioDraft() {
    studioDirty = true;
    window.clearTimeout(studioDraftTimer);
    studioDraftTimer = window.setTimeout(() => { studioDraftTimer = 0; saveToProjectWorkspace({ navigate: false }); }, 650);
  }
  const studioBindingKey = () => `ocvg.studio-current.v1:${encodeURIComponent(state.session?.user?.id || "local")}`;
  function requireGenerationLogin(target) {
    if (state.session) return true;
    if (studioDirty && !saveToProjectWorkspace({ navigate: false })) return false;
    pendingProtectedTarget = target; openAuth();
    return false;
  }
  function continueAnonymousCreation(binding) {
    const account = state.session?.user?.id;
    if (!account || !binding?.snapshot || binding.account || binding.snapshot.source?.account) return false;
    studioBinding = { ...binding, account };
    if (!saveToProjectWorkspace({ navigate: false, claim: true })) throw new Error("已登录，但当前草稿接续失败。文案仍在页面，请先处理保存提示。");
    sessionStorage.removeItem("ocvg.studio-current.v1:local");
    return true;
  }
  function restoreCurrentCreation() {
    try {
      const binding = JSON.parse(sessionStorage.getItem(studioBindingKey()) || "null");
      if (!binding?.snapshot || !binding?.draft) return;
      if ((binding.account || "") !== (state.session?.user?.id || "")) return;
      const saved = window.OCVGProjectStore.unpack(JSON.parse(localStorage.getItem("ocvg.projects.v1") || "[]")).find(project => project.id === binding.snapshot.id);
      if (!saved || (saved.source?.account || "") !== (binding.account || "")) return;
      studioBinding = binding;
      const draft = binding.draft;
      for (const [id, value] of Object.entries(draft.controls || {})) if (element(`#${id}`)) element(`#${id}`).value = value;
      updateImageChoices(draft.controls?.["image-resolution"]);
      state.storyboard = draft.storyboard || []; state.currentJob = draft.currentJob || null; state.imageJobs = draft.imageJobs || []; state.videoJob = draft.videoJob || null; state.referenceImages = draft.referenceImages || [];
      state.selectedVoice = draft.selectedVoice || null;
      renderStoryboard(); renderReferenceImages();
      element("#continue-existing").href = `/workspace/?project=${encodeURIComponent(binding.snapshot.id)}`;
      element("#continue-existing").hidden = true;
      element("#studio-creation-title").textContent = "继续当前草稿";
      element("#studio-draft-state").textContent = "已恢复本机草稿";
      resumeBoundTasks();
    } catch (_) { studioBinding = null; }
  }
  function startNewCreation() {
    const changedAccount = studioBinding && studioBinding.account !== state.session?.user?.id;
    if (studioDirty && !changedAccount && !saveToProjectWorkspace({ navigate: false })) return;
    if ((element("#script-input").value.trim() || studioBinding) && !window.confirm("开始新的创作？当前作品会保留在“我的作品”。")) return;
    window.clearTimeout(studioDraftTimer); studioDraftTimer = 0;
    audioEpoch++; imageEpoch++; videoEpoch++;
    sessionStorage.removeItem(studioBindingKey()); studioBinding = null;
    studioDirty = false;
    element("#script-input").value = ""; state.storyboard = []; state.currentJob = null; state.videoJob = null; state.referenceImages = []; resetAssets(); clearVideoPreview();
    renderStoryboard(); renderReferenceImages(); renderVideoJob(); updateTextSummary(); updateVideoJobButton();
    element("#continue-existing").hidden = true; element("#script-input").focus();
    element("#studio-creation-title").textContent = "开始一部新作品";
    element("#studio-draft-state").textContent = "输入文案后自动保留草稿";
    element("#script-panel").scrollIntoView({ behavior: "smooth" });
  }
  function saveToProjectWorkspace(options = {}) {
    const navigate = options.navigate !== false;
    window.clearTimeout(studioDraftTimer); studioDraftTimer = 0;
    const script = element("#script-input").value.trim();
    if (!script && (!studioBinding || navigate)) { if (navigate) message(element("#compose-message"), "先写下文案，再继续编辑。"); if (!studioBinding) studioDirty = false; return !studioBinding; }
    try {
      const account = state.session?.user?.id;
      if (studioBinding && studioBinding.account !== account) throw new Error("账户已变化，当前稿仍属于原账户。请切回原账户继续，或开始新的创作。");
      const previous = studioBinding?.snapshot;
      const now = previous?.createdAt || new Date().toISOString();
      const id = previous?.id || (window.crypto && crypto.randomUUID ? crypto.randomUUID() : `project-${Date.now()}-${Math.random().toString(16).slice(2)}`);
      const candidate = window.OCVGStudioProject.build({ id, now, script, account, scenes: state.storyboard.length ? state.storyboard : splitText(script).map((narration, index) => ({ index, title: `镜头 ${index + 1}`, narration, prompt: "" })), audioJob: state.currentJob, images: state.imageJobs, videoJob: state.videoJob, voiceId: state.selectedVoice?.id, referenceImages: state.referenceImages, settings: { aspectRatio: element("#aspect-ratio").value, resolution: element("#image-resolution").value, visualStyle: element("#visual-style").value, speed: Number(element("#speed").value), pitch: Number(element("#pitch").value), emotion: element("#emotion").value } });
      if (previous?.exports?.length && !candidate.exports.length) candidate.exports = previous.exports.map(record => ({ ...record, stale: true }));
      const before = localStorage.getItem("ocvg.projects.v1");
      window.OCVGStudioProject.saveContinuing(localStorage, window.OCVGProjectStore, candidate, previous);
      const controls = Object.fromEntries(["script-input", "image-method", "aspect-ratio", "image-resolution", "visual-style", "speed", "pitch", "emotion", "emotion-weight"].map(key => [key, element(`#${key}`).value]));
      studioBinding = { account, snapshot: candidate, draft: { controls, selectedVoice: state.selectedVoice, storyboard: state.storyboard, currentJob: state.currentJob, imageJobs: state.imageJobs, videoJob: state.videoJob, referenceImages: state.referenceImages } };
      try { sessionStorage.setItem(studioBindingKey(), JSON.stringify(studioBinding)); } catch (_) { /* The saved work remains in localStorage. */ }
      if (options.claim || (previous && !previous.source?.account && account)) document.dispatchEvent(new CustomEvent("ocvg:studio-claimed", { detail: { id, account } }));
      if (before !== localStorage.getItem("ocvg.projects.v1")) document.dispatchEvent(new CustomEvent("ocvg:project-saved", { detail: { id } }));
      element("#continue-existing").href = `/workspace/?project=${encodeURIComponent(id)}`; element("#continue-existing").hidden = true;
      element("#studio-creation-title").textContent = "继续当前草稿";
      if (navigate) { message(element("#compose-message"), "作品已保存，正在打开编辑。", "success"); window.location.href = `/workspace/?project=${encodeURIComponent(id)}`; }
      else element("#studio-draft-state").textContent = "草稿已自动保留";
      studioDirty = false;
      return true;
    } catch (error) {
      message(element("#compose-message"), `未覆盖已保存的作品。${error.message || "请检查本机存储空间。"} 可点击“打开已保存的作品”继续，或开始新的创作。`);
      if (studioBinding) { element("#continue-existing").href = `/workspace/?project=${encodeURIComponent(studioBinding.snapshot.id)}`; element("#continue-existing").hidden = false; }
      element("#studio-draft-state").textContent = "草稿未保存，请查看提示";
      studioDirty = true;
      return false;
    }
  }

  function boundedNumber(value, minimum, maximum, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.min(maximum, Math.max(minimum, parsed)) : fallback;
  }

  function validSelectValue(id, value, fallback) {
    const select = element(`#${id}`);
    return Array.from(select.options).some((option) => option.value === value) ? value : fallback;
  }

  function normalizeProject(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("项目文件内容无效。");
    if (raw.format && raw.format !== "oneclick-vidgen-project") throw new Error("这不是 One-Click VidGen 项目文件。");
    if (Number(raw.version) !== 1) throw new Error("暂不支持该项目文件版本。");
    const script = typeof raw.script === "string" ? raw.script : "";
    if (script.length > element("#script-input").maxLength) throw new Error("项目文案超过 5,000 字上限。");
    const audio = raw.audio && typeof raw.audio === "object" && !Array.isArray(raw.audio) ? raw.audio : {};
    const scenes = raw.scenes === undefined ? [] : raw.scenes;
    if (!Array.isArray(scenes) || scenes.length > 16) throw new Error("项目分镜必须是最多 16 项的数组。");
    const normalizedScenes = scenes.map((scene, index) => {
      if (!scene || typeof scene !== "object" || Array.isArray(scene)) throw new Error(`第 ${index + 1} 个分镜格式无效。`);
      const narration = String(scene.narration || "").trim();
      const prompt = String(scene.prompt || "").trim();
      if (!narration || !prompt) throw new Error(`第 ${index + 1} 个分镜缺少旁白或生图提示词。`);
      return { index, title: String(scene.title || `镜头 ${index + 1}`).slice(0, 200), narration: narration.slice(0, 1200), description: String(scene.description || "").slice(0, 2000), prompt: prompt.slice(0, 3000), imageStatus: "pending" };
    });
    return {
      script,
      method: raw.method === "ican" ? "ican" : "running",
      aspectRatio: validSelectValue("aspect-ratio", String(raw.aspect_ratio || ""), "16:9"),
      resolution: String(raw.resolution || (raw.method === "ican" ? "2.5k" : "1k")),
      visualStyle: validSelectValue("visual-style", String(raw.visual_style || ""), "电影感写实摄影"),
      sceneCount: normalizedScenes.length || autoSceneCount(script),
      emotion: validSelectValue("emotion", String(audio.emotion || ""), ""),
      speed: boundedNumber(audio.speed, .5, 2, 1),
      pitch: Math.round(boundedNumber(audio.pitch, -12, 12, 0)),
      emotionWeight: boundedNumber(audio.emotion_weight, 0, 1, .65),
      voiceId: raw.voice && typeof raw.voice.id === "string" ? raw.voice.id : null,
      scenes: normalizedScenes,
    };
  }

  async function importProject(event) {
    const input = event.currentTarget;
    const file = input.files[0];
    if (!file) return;
    const hasActiveJob = [state.currentJob, state.videoJob].some((job) => job && !TERMINAL.has(String(job.status || "")));
    if (hasActiveJob) { message(element("#compose-message"), "当前云端任务仍在处理，请等待完成或取消后再导入项目。"); input.value = ""; return; }
    if (file.size > 1024 * 1024) { message(element("#compose-message"), "项目文件不能超过 1 MB。"); input.value = ""; return; }
    input.disabled = true;
    try {
      const project = normalizeProject(JSON.parse(await file.text()));
      element("#script-input").value = project.script;
      element("#aspect-ratio").value = project.aspectRatio;
      element("#image-method").value = project.method;
      updateImageChoices(project.resolution);
      element("#visual-style").value = project.visualStyle;
      element("#emotion").value = project.emotion;
      element("#speed").value = project.speed;
      element("#pitch").value = project.pitch;
      element("#emotion-weight").value = project.emotionWeight;
      element("#speed-value").textContent = `${project.speed.toFixed(2)}×`;
      element("#pitch-value").textContent = project.pitch > 0 ? `+${project.pitch}` : String(project.pitch);
      element("#emotion-weight-value").textContent = `${Math.round(project.emotionWeight * 100)}%`;
      state.storyboard = project.scenes;
      state.referenceImages = [];
      resetAssets(); renderReferenceImages();
      const importedVoice = project.voiceId && state.voices.find((voice) => voice.id === project.voiceId);
      if (importedVoice) state.selectedVoice = importedVoice;
      renderVoices(); renderStoryboard(); renderAssets(); updateAssetProgress(); maybeEnableCompose(); scheduleQuote();
      element("#advanced-creation").open = true;
      const voiceNotice = project.voiceId && !importedVoice ? "；原音色当前不可用，已保留现有选择" : "";
      message(element("#compose-message"), `已导入 ${project.scenes.length} 个分镜和生成参数${voiceNotice}。`, "success");
      studioBinding = null; sessionStorage.removeItem(studioBindingKey()); scheduleStudioDraft();
    } catch (error) {
      message(element("#compose-message"), error instanceof SyntaxError ? "项目文件不是有效的 JSON。" : errorText(error));
    } finally { input.disabled = false; input.value = ""; }
  }
  function downloadBlob(blob, filename) { const url = rememberUrl(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove(); }
  function rememberUrl(blob) { const url = URL.createObjectURL(blob); state.objectUrls.push(url); return url; }
  const stableUrls = new Map(); function rememberStableUrl(blob, key) { if (!stableUrls.has(key)) stableUrls.set(key, rememberUrl(blob)); return stableUrls.get(key); }
  function escapeHtml(value) { const div = document.createElement("div"); div.textContent = String(value || ""); return div.innerHTML; }
  function escapeAttribute(value) { return escapeHtml(value).replace(/"/g, "&quot;"); }
  function openAuth() { element("#auth-message").className = "message"; if (!element("#auth-dialog").open) element("#auth-dialog").showModal(); }
  function protectedTargetFromHash() { const target = window.location.hash.slice(1); return protectedTargets.has(target) ? target : null; }
  function keepStudioLinkVisible(link) {
    const navigation = link.closest(".workspace-steps");
    if (!navigation || navigation.scrollWidth <= navigation.clientWidth) return;
    const navigationRect = navigation.getBoundingClientRect();
    const linkRect = link.getBoundingClientRect();
    if (linkRect.left < navigationRect.left) navigation.scrollLeft -= navigationRect.left - linkRect.left;
    else if (linkRect.right > navigationRect.right) navigation.scrollLeft += linkRect.right - navigationRect.right;
  }
  function setActiveStudioTarget(target, ensureVisible = false) {
    document.querySelectorAll(".workspace-steps a").forEach((link) => {
      const active = link.getAttribute("href") === `#${target}`;
      link.classList.toggle("active", active);
      link.classList.toggle("group-active", link.getAttribute("href") === "#advanced-creation" && advancedCreationTargets.has(target));
      if (active && ensureVisible) keepStudioLinkVisible(link);
    });
  }
  function holdNavigationFocus(target, behavior) {
    navigationFocusTarget = target;
    window.clearTimeout(navigationFocusTimer);
    navigationFocusTimer = window.setTimeout(() => {
      if (navigationFocusTarget !== target) return;
      navigationFocusTarget = null;
      updateActiveStudioTargetFromScroll();
    }, behavior === "smooth" ? 700 : 180);
  }
  function revealStudioTarget(target, behavior = "smooth") {
    if (target === "storyboard-panel") element("#storyboard-panel").open = true;
    if (target === "advanced-creation" || advancedCreationTargets.has(target)) element("#advanced-creation").open = true;
    if (target === "task-panel") { element("#advanced-creation").open = true; element("#advanced-results").open = true; }
    holdNavigationFocus(target, behavior);
    setActiveStudioTarget(target, true);
    // Opening <details> changes layout. Wait for that layout before locating
    // the target, otherwise advanced steps can land on the collapsed summary.
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      element(`#${target}`)?.scrollIntoView({ behavior, block: "start" });
      window.requestAnimationFrame(() => setActiveStudioTarget(target, true));
    }));
  }
  function guardProtectedTarget() {
    const target = protectedTargetFromHash();
    if (!target) return;
    pendingProtectedTarget = null;
    revealStudioTarget(target, "instant");
  }
  function enterPendingProtectedTarget() {
    if (!pendingProtectedTarget) return;
    const target = pendingProtectedTarget;
    pendingProtectedTarget = null;
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${target}`);
    revealStudioTarget(target, "instant");
  }
  function logout(showNotice = true) { if (studioDirty && !saveToProjectWorkspace({ navigate: false })) return; window.clearTimeout(studioDraftTimer); studioDraftTimer = 0; element("#script-input").value = ""; state.storyboard = []; renderStoryboard(); const refresh = state.session && state.session.refresh_token; if (refresh) fetch(`${API_BASE}/auth/logout`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: refresh }) }).catch(() => {}); saveSession(null); studioBinding = null; element("#continue-existing").hidden = true; state.referenceImages = []; renderReferenceImages(); state.currentJob = null; window.clearInterval(state.pollTimer); resetAssets(); state.account = null; state.voices = []; state.selectedVoice = null; state.modelReady = false; state.videoJob = null; clearVideoPreview(); element("#studio-credits").textContent = "—"; element("#studio-daily").textContent = "—"; element("#studio-concurrency").textContent = "—"; element("#voice-grid").innerHTML = ""; element("#model-pool-state").textContent = "尚未检测"; element("#video-job-history").innerHTML = ""; renderVideoJob(); renderAuth(); if (showNotice) openAuth(); }

  let authMode = "login";
  element("#studio-login").addEventListener("click", openAuth); window.OCVGPageAccount = { login: openAuth, logout: () => logout(false) }; element("#close-dialog").addEventListener("click", () => element("#auth-dialog").close()); element("#auth-dialog").addEventListener("click", (event) => { if (event.target === element("#auth-dialog")) event.currentTarget.close(); });
  document.querySelectorAll("[data-auth-mode]").forEach((tab) => tab.addEventListener("click", () => { authMode = tab.dataset.authMode; document.querySelectorAll("[data-auth-mode]").forEach((item) => item.classList.toggle("active", item === tab)); element("#auth-title").textContent = authMode === "login" ? "登录云端账户" : "注册云端账户"; element("#auth-submit").textContent = authMode === "login" ? "登录" : "注册并继续"; element("#password").autocomplete = authMode === "login" ? "current-password" : "new-password"; element("#password").minLength = authMode === "login" ? 1 : 10; element("#password").placeholder = authMode === "login" ? "请输入密码" : "至少 10 位字符"; element("#auth-message").className = "message"; }));
  element("#auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (studioDirty && !saveToProjectWorkspace({ navigate: false })) return;
    const anonymous = !state.session && !studioBinding?.account ? studioBinding : null;
    const submit = element("#auth-submit"); submit.disabled = true;
    const credentials = { email: event.currentTarget.email.value.trim(), password: event.currentTarget.password.value };
    try {
      if (authMode === "register") await api("/auth/register", { method: "POST", body: JSON.stringify(credentials) });
      const login = await api("/auth/login", { method: "POST", body: JSON.stringify(credentials) });
      saveSession(login);
      const continued = continueAnonymousCreation(anonymous);
      element("#auth-dialog").close();
      if (returnAfterLogin()) return;
      if (!continued) restoreCurrentCreation();
      renderAuth(); enterPendingProtectedTarget();
      await Promise.all([loadAccount(), loadVoices(), loadJobs(), loadVideoJobs(), checkModelPool()]); scheduleQuote();
    } catch (error) { const node = element("#auth-message"); node.textContent = errorText(error); node.className = "message show error"; }
    finally { submit.disabled = false; submit.textContent = authMode === "login" ? "登录" : "注册并继续"; }
  });

  element("#script-input").addEventListener("input", () => { invalidateStudioResults(); state.storyboard = []; renderStoryboard(); scheduleQuote(); }); element("#document-file").addEventListener("change", parseDocument); element("#clear-script").addEventListener("click", () => { invalidateStudioResults(); element("#script-input").value = ""; state.storyboard = []; renderStoryboard(); scheduleQuote(); setDocumentStatus(""); scheduleStudioDraft(); }); element("#generate-storyboard").addEventListener("click", generateStoryboard);
  element("#speed").addEventListener("input", (event) => { element("#speed-value").textContent = `${Number(event.target.value).toFixed(2)}×`; scheduleQuote(); }); element("#pitch").addEventListener("input", (event) => { const value = Number(event.target.value); element("#pitch-value").textContent = value > 0 ? `+${value}` : String(value); scheduleQuote(); }); element("#emotion-weight").addEventListener("input", (event) => { element("#emotion-weight-value").textContent = `${Math.round(Number(event.target.value) * 100)}%`; scheduleQuote(); }); element("#emotion").addEventListener("change", scheduleQuote);
  element("#reset-settings").addEventListener("click", () => { element("#emotion").value = ""; element("#speed").value = 1; element("#pitch").value = 0; element("#emotion-weight").value = .65; element("#aspect-ratio").value = "16:9"; updateImageChoices(element("#image-method").value === "ican" ? "2.5k" : "1k"); element("#speed-value").textContent = "1.00×"; element("#pitch-value").textContent = "0"; element("#emotion-weight-value").textContent = "65%"; scheduleQuote(); });
  element("#refresh-voices").addEventListener("click", loadVoices); element("#voice-file").addEventListener("change", (event) => { element("#upload-form").classList.toggle("show", Boolean(event.target.files[0])); if (event.target.files[0] && !element("#voice-name").value) element("#voice-name").value = event.target.files[0].name.replace(/\.[^.]+$/, ""); }); element("#upload-voice").addEventListener("click", uploadVoice); element("#reference-files").addEventListener("change", uploadReferenceImages); element("#create-video-job").addEventListener("click", createVideoJob); element("#refresh-video-jobs").addEventListener("click", loadVideoJobs); element("#preview-video").addEventListener("click", previewVideoResult); element("#submit-job").addEventListener("click", submitJob); element("#refresh-jobs").addEventListener("click", loadJobs); element("#compose-video").addEventListener("click", composeVideo); element("#save-to-project").addEventListener("click", saveToProjectWorkspace); element("#download-project").addEventListener("click", downloadProject); element("#import-project").addEventListener("change", importProject);

  document.querySelectorAll(".workspace-steps a").forEach((link) => link.addEventListener("click", (event) => {
    const target = String(link.getAttribute("href") || "").replace(/^#/, "");
    if (!protectedTargets.has(target)) return;
    event.preventDefault();
    window.history.pushState(null, "", `${window.location.pathname}${window.location.search}#${target}`);
    revealStudioTarget(target, "instant");
  }));

  function updateActiveStudioTargetFromScroll() {
    if (navigationFocusTarget) {
      setActiveStudioTarget(navigationFocusTarget);
      return;
    }
    const cards = Array.from(document.querySelectorAll(".workspace-card"));
    const offset = 110;
    const visibleCards = cards.filter((card) => {
      const rect = card.getBoundingClientRect();
      return rect.height > 0 && rect.bottom > offset;
    });
    if (!visibleCards.length) return;
    const passed = visibleCards.filter((card) => card.getBoundingClientRect().top <= offset + 12);
    const active = (passed.length ? passed[passed.length - 1] : visibleCards[0]);
    setActiveStudioTarget(active.closest("#storyboard-panel")?.id || active.id);
  }
  let activeTargetFrame = 0;
  window.addEventListener("scroll", () => {
    if (activeTargetFrame) return;
    activeTargetFrame = window.requestAnimationFrame(() => {
      activeTargetFrame = 0;
      updateActiveStudioTargetFromScroll();
    });
  }, { passive: true });
  window.addEventListener("beforeunload", () => state.objectUrls.forEach((url) => URL.revokeObjectURL(url))); window.requestAnimationFrame(() => { document.body.classList.add("studio-ready"); updateActiveStudioTargetFromScroll(); });
  window.addEventListener("hashchange", guardProtectedTarget); window.addEventListener("popstate", guardProtectedTarget);
  document.addEventListener("input", (event) => { if (["speed", "pitch", "emotion", "emotion-weight"].includes(event.target.id)) invalidateStudioResults("audio"); if (["image-method", "aspect-ratio", "image-resolution", "visual-style"].includes(event.target.id)) invalidateStudioResults("image"); if (event.target.closest("#workspace-form") && !event.target.closest("#auth-form")) scheduleStudioDraft(); });
  document.addEventListener("change", (event) => { if (event.target.closest("#workspace-form") && !event.target.closest("#auth-form")) scheduleStudioDraft(); });
  document.addEventListener("ocvg:flush-project", (event) => { if (studioDirty && !saveToProjectWorkspace({ navigate: false })) event.preventDefault(); });
  document.addEventListener("click", (event) => { if (event.target.closest?.("a[href]") && studioDirty && !saveToProjectWorkspace({ navigate: false })) event.preventDefault(); }, true);
  window.addEventListener("beforeunload", (event) => { if (studioDirty && !saveToProjectWorkspace({ navigate: false })) { event.preventDefault(); event.returnValue = ""; } });
  element("#start-new-creation").addEventListener("click", startNewCreation);
  element("#add-storyboard-scene").addEventListener("click", () => editStoryboardScenes("add"));
  element("#undo-storyboard-delete").addEventListener("click", () => editStoryboardScenes("undo"));
  if (returnAfterLogin()) return;
  restoreCurrentCreation();
  if (loginReturn && !state.session?.access_token) openAuth();
  renderAuth(); renderVideoJob(); renderVideoExport(); updateTextSummary(); updateAssetProgress(); checkHealth(); window.setInterval(checkHealth, 30000); guardProtectedTarget(); if (state.session) Promise.all([loadAccount(), loadVoices(), loadJobs(), loadVideoJobs(), checkModelPool()]).then(scheduleQuote);
})();
