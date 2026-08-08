(function () {
  "use strict";

  const API_BASE = "/api/v1";
  const TERMINAL = new Set(["completed", "failed", "cancelled"]);
  const IMAGE_TERMINAL = new Set(["SUCCESS", "FAILED", "ERROR", "CANCELLED"]);
  const state = {
    session: readSession(), account: null, voices: [], selectedVoice: null,
    quote: null, cloudReady: false, modelReady: false, storyboard: [],
    currentJob: null, pollTimer: null, audioBlobs: new Map(),
    imageJobs: [], imageBlobs: new Map(), objectUrls: [], composing: false,
  };
  const element = (selector) => document.querySelector(selector);
  const sleep = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  const protectedTargets = new Set(["script-panel", "voice-panel", "task-panel"]);
  let pendingProtectedTarget = protectedTargets.has(window.location.hash.slice(1)) ? window.location.hash.slice(1) : null;

  function readSession() { try { return JSON.parse(sessionStorage.getItem("ocvg-cloud-session")) || null; } catch (_) { return null; } }
  function saveSession(session) { state.session = session; if (session) sessionStorage.setItem("ocvg-cloud-session", JSON.stringify(session)); else sessionStorage.removeItem("ocvg-cloud-session"); }
  function message(node, text, type = "error") { node.textContent = text; node.className = `inline-message show ${type}`; }
  function clearMessage(node) { node.textContent = ""; node.className = "inline-message"; }
  function unwrap(value) { return value && typeof value === "object" && value.data && value.code !== undefined ? value.data : value; }
  function errorText(error) {
    const value = String(error && error.message || error || "请求失败");
    const mappings = [[/401|credentials|token|登录/i, "登录状态已失效，请重新登录。"], [/insufficient|积分|balance/i, "可用积分不足，请先充值。"], [/Ray|timed out|timeout|503|unavailable/i, "云端服务暂时不可用，请稍后重试。"], [/quota|queue|并发|429/i, "当前任务额度已用完，请稍后再试。"], [/413|too large/i, "内容或文件超出允许大小。"], [/415|format|WAV|MP3|FLAC/i, "文件格式不受支持。"]];
    return (mappings.find(([pattern]) => pattern.test(value)) || [null, value])[1];
  }

  async function refreshToken() {
    if (!state.session || !state.session.refresh_token) return false;
    const response = await fetch(`${API_BASE}/auth/refresh`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ refresh_token: state.session.refresh_token }) });
    if (!response.ok) return false;
    saveSession(await response.json()); return true;
  }

  async function api(path, options = {}, retry = true) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (state.session && state.session.access_token) headers.Authorization = `Bearer ${state.session.access_token}`;
    if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
    if (response.status === 401 && retry && await refreshToken()) return api(path, options, false);
    if (response.status === 204) return null;
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("json") ? await response.json() : await response.text();
    if (!response.ok || (data && typeof data === "object" && data.code && data.code !== 0 && data.code !== "0")) {
      const detail = typeof data === "object" ? (data.message || data.detail || data.code) : data;
      throw new Error(typeof detail === "string" ? detail : `HTTP ${response.status}`);
    }
    return data;
  }

  async function authenticatedBlob(pathOrUrl) {
    const isApiPath = String(pathOrUrl).startsWith("/");
    const headers = isApiPath && state.session ? { Authorization: `Bearer ${state.session.access_token}` } : {};
    let response = await fetch(isApiPath ? `${API_BASE.replace(/\/api\/v1$/, "")}${pathOrUrl}` : pathOrUrl, { headers });
    if (response.status === 401 && isApiPath && await refreshToken()) response = await fetch(`${API_BASE.replace(/\/api\/v1$/, "")}${pathOrUrl}`, { headers: { Authorization: `Bearer ${state.session.access_token}` } });
    if (!response.ok) throw new Error(`下载失败（HTTP ${response.status}）`);
    return response.blob();
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

  function poolChat(system, user) {
    return api("/model-pool/v1/chat/completions", { method: "POST", body: JSON.stringify({ model: "auto", temperature: 0.45, messages: [{ role: "system", content: system }, { role: "user", content: user }] }) });
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
    element("#auth-wall").classList.toggle("hidden", loggedIn); element("#workspace-form").classList.toggle("is-locked", !loggedIn);
    element("#header-account").textContent = loggedIn ? (state.session.user && state.session.user.email || "账户中心") : "登录账户";
    element("#studio-email").textContent = loggedIn ? (state.session.user && state.session.user.email || "已登录") : "尚未登录";
    updateSubmit(); updateStoryboardButton();
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
      card.querySelector(".voice-select").addEventListener("click", () => { state.selectedVoice = voice; renderVoices(); scheduleQuote(); });
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
  async function uploadVoice() {
    const file = element("#voice-file").files[0]; const name = element("#voice-name").value.trim(); if (!file || !name) { message(element("#voice-message"), "请选择音频文件并填写音色名称。"); return; }
    const button = element("#upload-voice"); button.disabled = true; button.textContent = "正在上传…";
    try { const form = new FormData(); form.append("file", file); form.append("display_name", name); const result = await api("/cloud/voices", { method: "POST", headers: { "Idempotency-Key": `voice-web-${Date.now()}-${file.size}` }, body: form }); state.selectedVoice = result.voice; element("#voice-file").value = ""; element("#voice-name").value = ""; element("#upload-form").classList.remove("show"); await loadVoices(); message(element("#voice-message"), result.deduplicated ? "该音频已存在，已选中原音色。" : "个人音色上传成功。", "success"); }
    catch (error) { message(element("#voice-message"), errorText(error)); }
    finally { button.disabled = false; button.textContent = "上传音色"; }
  }

  function updateStoryboardButton() {
    const button = element("#generate-storyboard"); if (!button) return;
    button.disabled = !state.session || !state.modelReady || !element("#script-input").value.trim();
    if (!state.session) button.textContent = "登录后生成分镜"; else if (!state.modelReady) button.textContent = "模型号池暂不可用"; else if (!element("#script-input").value.trim()) button.textContent = "请先输入文案"; else button.textContent = state.storyboard.length ? "重新生成分镜" : "用 AI 生成分镜";
  }

  async function generateStoryboard() {
    const text = element("#script-input").value.trim(); const count = Math.max(2, Math.ceil(text.length / 850), Math.min(16, Number(element("#scene-count").value) || 6)); const style = element("#visual-style").value;
    const button = element("#generate-storyboard"); const progress = element("#agent-progress"); clearMessage(element("#storyboard-message")); button.disabled = true;
    try {
      progress.innerHTML = "<b>Agent 0</b><span>正在通读全文并提取人物、地点和叙事主线…</span>";
      const analysis = parseModelJson(await poolChat("你是视频策划 Agent 0。通读文案，返回严格 JSON：{\"theme\":\"\",\"tone\":\"\",\"characters\":[],\"locations\":[],\"visual_continuity\":\"\"}。不要 Markdown。", text));
      progress.innerHTML = "<b>Agent 1</b><span>正在划分语义镜头并匹配旁白…</span>";
      const plan = parseModelJson(await poolChat(`你是视频分镜 Agent 1。将文案规划为约 ${count} 个连续镜头。每个镜头必须保留原文旁白，所有 narration 拼接后应完整覆盖原文。返回严格 JSON：{\"scenes\":[{\"title\":\"\",\"narration\":\"\",\"description\":\"镜头主体、动作、环境和构图\"}]}。不要 Markdown。`, JSON.stringify({ analysis, script: text })));
      let scenes = Array.isArray(plan.scenes) ? plan.scenes : [];
      if (!scenes.length) throw new Error("Agent 1 没有生成有效镜头");
      progress.innerHTML = "<b>Agent 2</b><span>正在为每个镜头生成统一风格的生图提示词…</span>";
      const prompts = parseModelJson(await poolChat(`你是画面提示词 Agent 2。为每个镜头生成一条可直接生图的中文提示词，保证角色外观、时代、地点连续，统一采用“${style}”。不要在画面中生成文字、水印或标志。返回严格 JSON：{\"scenes\":[{\"index\":0,\"prompt\":\"\"}]}。不要 Markdown。`, JSON.stringify({ analysis, scenes })));
      const promptList = Array.isArray(prompts.scenes) ? prompts.scenes : [];
      state.storyboard = scenes.slice(0, 16).flatMap((scene, index) => {
        const title = String(scene.title || `镜头 ${index + 1}`); const narrationParts = splitText(String(scene.narration || "")); const description = String(scene.description || "").trim(); const prompt = String((promptList.find((item) => Number(item.index) === index) || promptList[index] || {}).prompt || description).trim();
        return narrationParts.map((narration, partIndex) => ({ title: narrationParts.length > 1 ? `${title}（${partIndex + 1}）` : title, narration, description, prompt, imageStatus: "pending" }));
      }).slice(0, 16).filter((scene) => scene.narration && scene.prompt).map((scene, index) => ({ ...scene, index }));
      if (!state.storyboard.length) throw new Error("AI 返回的镜头缺少旁白或画面提示词");
      progress.innerHTML = `<b>规划完成</b><span>${state.storyboard.length} 个镜头，可在提交前直接修改。</span>`; renderStoryboard(); scheduleQuote(); message(element("#storyboard-message"), "分镜已生成。文本模型费用已按云端实际用量结算。", "success");
    } catch (error) { progress.innerHTML = ""; message(element("#storyboard-message"), errorText(error)); }
    finally { updateStoryboardButton(); await loadAccount(); }
  }

  function renderStoryboard() {
    const grid = element("#storyboard-grid"); grid.innerHTML = state.storyboard.map((scene, index) => `<article class="story-card"><div class="story-number">${String(index + 1).padStart(2, "0")}</div><div><input data-scene-title="${index}" value="${escapeAttribute(scene.title)}" aria-label="镜头标题" /><label>旁白<textarea data-scene-narration="${index}">${escapeHtml(scene.narration)}</textarea></label><label>生图提示词<textarea data-scene-prompt="${index}">${escapeHtml(scene.prompt)}</textarea></label></div></article>`).join("");
    grid.querySelectorAll("[data-scene-title]").forEach((input) => input.addEventListener("input", () => { state.storyboard[Number(input.dataset.sceneTitle)].title = input.value; }));
    grid.querySelectorAll("[data-scene-narration]").forEach((input) => input.addEventListener("input", () => { state.storyboard[Number(input.dataset.sceneNarration)].narration = input.value; scheduleQuote(); }));
    grid.querySelectorAll("[data-scene-prompt]").forEach((input) => input.addEventListener("input", () => { state.storyboard[Number(input.dataset.scenePrompt)].prompt = input.value; }));
  }

  let quoteTimer;
  function scheduleQuote() { window.clearTimeout(quoteTimer); updateTextSummary(); updateStoryboardButton(); quoteTimer = window.setTimeout(loadQuote, 450); }
  async function loadQuote() {
    state.quote = null; const chunks = ttsChunks(); if (!state.session || !state.selectedVoice || !chunks.length) { element("#summary-cost").textContent = "—"; updateSubmit(); return; }
    try { state.quote = await api("/cloud/quotes", { method: "POST", body: JSON.stringify(ttsPayload()) }); element("#summary-cost").textContent = `${state.quote.estimated_credits} 积分`; } catch (_) { element("#summary-cost").textContent = "报价失败"; }
    updateSubmit();
  }
  function updateTextSummary() { const text = element("#script-input").value; element("#text-counter").textContent = `${text.length.toLocaleString("zh-CN")} / 5,000`; element("#summary-characters").textContent = text.length.toLocaleString("zh-CN"); element("#summary-chunks").textContent = `${ttsChunks().length} / ${state.storyboard.length}`; }
  function updateSubmit() {
    const button = element("#submit-job"); const ready = Boolean(state.session && state.selectedVoice && state.storyboard.length && state.quote && state.cloudReady && !state.composing);
    button.disabled = !ready;
    if (!state.session) button.textContent = "登录后开始创作"; else if (!state.storyboard.length) button.textContent = "请先生成 AI 分镜"; else if (!state.cloudReady) button.textContent = "配音集群维护中"; else if (!state.selectedVoice) button.textContent = "请选择音色"; else if (!state.quote) button.textContent = "正在计算配音报价…"; else button.textContent = `并行生成全部素材 · 配音 ${state.quote.estimated_credits} 积分`;
  }

  async function submitJob() {
    clearMessage(element("#job-message")); const estimated = Number(state.quote && state.quote.estimated_credits || 0); const available = Number(state.account && state.account.credits && state.account.credits.available || 0);
    if (available < estimated) { message(element("#job-message"), "当前积分不足以预扣配音费用，图片和模型还会按实际用量另行结算。"); return; }
    resetAssets(); const button = element("#submit-job"); button.disabled = true; button.textContent = "正在提交云端任务…";
    const clientJobId = `web_video_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    try {
      const ttsRequest = api("/cloud/jobs", { method: "POST", headers: { "Idempotency-Key": clientJobId }, body: JSON.stringify({ ...ttsPayload(), client_job_id: clientJobId }) });
      const imageRequest = generateAllImages(clientJobId); const result = await ttsRequest; state.currentJob = { ...result, total_chunks: ttsChunks().length, progress: 0 }; renderCurrentJob();
      const ttsDone = pollTtsJob(result.job_id); await Promise.allSettled([ttsDone, imageRequest]); await loadAccount(); updateAssetProgress(); maybeEnableCompose();
    } catch (error) { message(element("#job-message"), errorText(error)); }
    finally { updateSubmit(); }
  }

  function resetAssets() { state.audioBlobs.clear(); state.imageBlobs.clear(); state.imageJobs = []; stableUrls.forEach((url) => URL.revokeObjectURL(url)); stableUrls.clear(); element("#asset-results").innerHTML = ""; element("#compose-progress").textContent = ""; clearMessage(element("#compose-message")); updateAssetProgress(); maybeEnableCompose(); }
  async function generateAllImages(batchId) {
    const queue = state.storyboard.map((scene) => ({ scene, status: "submitting", taskId: null, error: null })); state.imageJobs = queue; renderAssets(); updateAssetProgress();
    let cursor = 0; const worker = async () => { while (cursor < queue.length) { const item = queue[cursor++]; await generateOneImage(item, batchId); } };
    await Promise.all(Array.from({ length: Math.min(3, queue.length) }, worker)); renderAssets(); updateAssetProgress(); maybeEnableCompose();
  }
  async function generateOneImage(item, batchId) {
    try {
      const clientJobId = `${batchId}-image-${item.scene.index}`;
      const created = unwrap(await api("/image-pool/generate", { method: "POST", headers: { "Idempotency-Key": clientJobId }, body: JSON.stringify({ clientJobId, prompt: item.scene.prompt, aspectRatio: element("#aspect-ratio").value, resolution: element("#image-resolution").value, imageUrls: [] }) }));
      item.taskId = created.taskId || created.task_id; if (!item.taskId) throw new Error("图片服务没有返回任务编号"); item.status = "QUEUED"; renderAssets();
      const deadline = Date.now() + 15 * 60 * 1000;
      while (Date.now() < deadline) {
        const result = unwrap(await api("/image-pool/query", { method: "POST", body: JSON.stringify({ taskId: item.taskId }) })); item.status = String(result.status || "RUNNING").toUpperCase(); item.imageUrl = result.imageUrl || result.image_url || result.download_url; renderAssets(); updateAssetProgress();
        if (IMAGE_TERMINAL.has(item.status)) {
          if (item.status !== "SUCCESS" || !item.imageUrl) throw new Error(result.message || "图片生成失败");
          try { state.imageBlobs.set(item.scene.index, await authenticatedBlob(item.imageUrl)); } catch (error) { throw new Error(`图片已生成但浏览器无法下载：${errorText(error)}`); }
          renderAssets(); updateAssetProgress(); maybeEnableCompose(); return;
        }
        await sleep(2200);
      }
      throw new Error("图片任务等待超时");
    } catch (error) { item.status = "FAILED"; item.error = errorText(error); renderAssets(); updateAssetProgress(); }
  }

  function statusLabel(status) { return ({ queued: "排队中", running: "生成中", finalizing: "整理结果", completed: "已完成", failed: "失败", cancelled: "已取消", cancel_requested: "正在取消" })[status] || status || "未知"; }
  function renderCurrentJob() {
    const node = element("#current-task"); const job = state.currentJob;
    if (!job) { node.className = "current-task empty"; node.innerHTML = '<div class="empty-illustration">◎</div><div><strong>还没有正在处理的任务</strong><p>完成分镜和配音设置后，可并行生成全部素材。</p></div>'; return; }
    const progress = Number(job.progress || 0); node.className = `current-task ${job.status || "queued"}`;
    node.innerHTML = `<div class="task-progress-ring" style="background:conic-gradient(var(--teal) ${Math.min(100, progress)}%,#deebe8 0)"><strong>${progress}%</strong></div><div class="task-progress-copy"><span>${statusLabel(job.status)}</span><strong>${escapeHtml(job.message || "云端配音处理中")}</strong><div class="task-bar"><i style="width:${Math.min(100, progress)}%"></i></div><small>任务编号 ${escapeHtml(job.job_id || "")}</small></div>${["queued", "running", "finalizing"].includes(job.status) ? '<button class="quiet-button" data-cancel type="button">取消任务</button>' : ""}`;
    const cancel = node.querySelector("[data-cancel]"); if (cancel) cancel.addEventListener("click", () => cancelJob(job.job_id));
  }

  async function fetchReadyAudio(job) {
    const chunks = job && job.result && Array.isArray(job.result.chunks) ? job.result.chunks : [];
    await Promise.all(chunks.map(async (chunk) => { const index = Number(chunk.index); if (state.audioBlobs.has(index)) return; const path = chunk.audio_url || `/api/v1/cloud/jobs/${encodeURIComponent(job.job_id)}/chunks/${index}/audio`; state.audioBlobs.set(index, await authenticatedBlob(path)); renderAssets(); updateAssetProgress(); maybeEnableCompose(); }));
  }
  async function pollTtsJob(jobId) {
    window.clearInterval(state.pollTimer);
    for (;;) {
      const job = await api(`/cloud/jobs/${encodeURIComponent(jobId)}`); state.currentJob = job; await fetchReadyAudio(job); renderCurrentJob(); updateAssetProgress();
      if (TERMINAL.has(job.status)) { if (job.status !== "completed") message(element("#job-message"), job.message || `配音任务${statusLabel(job.status)}`); await loadJobs(); return job; }
      await sleep(2400);
    }
  }
  async function cancelJob(jobId) { try { state.currentJob = await api(`/cloud/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }); renderCurrentJob(); await loadAccount(); await loadJobs(); } catch (error) { message(element("#job-message"), errorText(error)); } }
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

  function downloadProject() { const project = { version: 1, created_at: new Date().toISOString(), script: element("#script-input").value, aspect_ratio: element("#aspect-ratio").value, resolution: element("#image-resolution").value, visual_style: element("#visual-style").value, voice: state.selectedVoice ? { id: state.selectedVoice.id, display_name: state.selectedVoice.display_name, type: state.selectedVoice.type } : null, audio: { speed: Number(element("#speed").value), pitch: Number(element("#pitch").value), emotion: element("#emotion").value }, scenes: state.storyboard.map(({ index, title, narration, description, prompt }) => ({ index, title, narration, description, prompt })) }; downloadBlob(new Blob([JSON.stringify(project, null, 2)], { type: "application/json" }), "OneClickVidGen_project.json"); }
  function downloadBlob(blob, filename) { const url = rememberUrl(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove(); }
  function rememberUrl(blob) { const url = URL.createObjectURL(blob); state.objectUrls.push(url); return url; }
  const stableUrls = new Map(); function rememberStableUrl(blob, key) { if (!stableUrls.has(key)) stableUrls.set(key, rememberUrl(blob)); return stableUrls.get(key); }
  function escapeHtml(value) { const div = document.createElement("div"); div.textContent = String(value || ""); return div.innerHTML; }
  function escapeAttribute(value) { return escapeHtml(value).replace(/"/g, "&quot;"); }
  function openAuth() { element("#auth-message").className = "message"; if (!element("#auth-dialog").open) element("#auth-dialog").showModal(); }
  function protectedTargetFromHash() { const target = window.location.hash.slice(1); return protectedTargets.has(target) ? target : null; }
  function guardProtectedTarget() {
    const target = protectedTargetFromHash();
    if (!target || state.session) return;
    pendingProtectedTarget = target;
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    window.scrollTo({ top: 0, behavior: "auto" });
    openAuth();
  }
  function enterPendingProtectedTarget() {
    if (!pendingProtectedTarget) return;
    const target = pendingProtectedTarget;
    pendingProtectedTarget = null;
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${target}`);
    window.requestAnimationFrame(() => element(`#${target}`)?.scrollIntoView({ block: "start" }));
  }
  function logout(showNotice = true) { saveSession(null); state.account = null; state.voices = []; state.selectedVoice = null; state.modelReady = false; element("#studio-credits").textContent = "—"; element("#studio-daily").textContent = "—"; element("#studio-concurrency").textContent = "—"; element("#voice-grid").innerHTML = ""; element("#model-pool-state").textContent = "尚未检测"; renderAuth(); if (showNotice) openAuth(); }

  let authMode = "login";
  element("#studio-login").addEventListener("click", openAuth); element("#header-account").addEventListener("click", () => state.session ? (window.confirm("是否退出当前账户？") && logout()) : openAuth()); element("#close-dialog").addEventListener("click", () => element("#auth-dialog").close()); element("#auth-dialog").addEventListener("click", (event) => { if (event.target === element("#auth-dialog")) event.currentTarget.close(); });
  document.querySelectorAll("[data-auth-mode]").forEach((tab) => tab.addEventListener("click", () => { authMode = tab.dataset.authMode; document.querySelectorAll("[data-auth-mode]").forEach((item) => item.classList.toggle("active", item === tab)); element("#auth-title").textContent = authMode === "login" ? "登录云端账户" : "注册云端账户"; element("#auth-submit").textContent = authMode === "login" ? "登录" : "注册并继续"; element("#password").autocomplete = authMode === "login" ? "current-password" : "new-password"; element("#auth-message").className = "message"; }));
  element("#auth-form").addEventListener("submit", async (event) => { event.preventDefault(); const submit = element("#auth-submit"); submit.disabled = true; const credentials = { email: event.currentTarget.email.value.trim(), password: event.currentTarget.password.value }; try { if (authMode === "register") await api("/auth/register", { method: "POST", body: JSON.stringify(credentials) }); const login = await api("/auth/login", { method: "POST", body: JSON.stringify(credentials) }); saveSession(login); element("#auth-dialog").close(); renderAuth(); enterPendingProtectedTarget(); await Promise.all([loadAccount(), loadVoices(), loadJobs(), checkModelPool()]); scheduleQuote(); } catch (error) { const node = element("#auth-message"); node.textContent = errorText(error); node.className = "message show error"; } finally { submit.disabled = false; submit.textContent = authMode === "login" ? "登录" : "注册并继续"; } });

  element("#script-input").addEventListener("input", () => { state.storyboard = []; renderStoryboard(); scheduleQuote(); }); element("#clear-script").addEventListener("click", () => { element("#script-input").value = ""; state.storyboard = []; renderStoryboard(); scheduleQuote(); }); element("#generate-storyboard").addEventListener("click", generateStoryboard);
  element("#speed").addEventListener("input", (event) => { element("#speed-value").textContent = `${Number(event.target.value).toFixed(2)}×`; scheduleQuote(); }); element("#pitch").addEventListener("input", (event) => { const value = Number(event.target.value); element("#pitch-value").textContent = value > 0 ? `+${value}` : String(value); scheduleQuote(); }); element("#emotion-weight").addEventListener("input", (event) => { element("#emotion-weight-value").textContent = `${Math.round(Number(event.target.value) * 100)}%`; scheduleQuote(); }); element("#emotion").addEventListener("change", scheduleQuote);
  element("#reset-settings").addEventListener("click", () => { element("#emotion").value = ""; element("#speed").value = 1; element("#pitch").value = 0; element("#emotion-weight").value = .65; element("#aspect-ratio").value = "16:9"; element("#image-resolution").value = "1k"; element("#speed-value").textContent = "1.00×"; element("#pitch-value").textContent = "0"; element("#emotion-weight-value").textContent = "65%"; scheduleQuote(); });
  element("#refresh-voices").addEventListener("click", loadVoices); element("#voice-file").addEventListener("change", (event) => { element("#upload-form").classList.toggle("show", Boolean(event.target.files[0])); if (event.target.files[0] && !element("#voice-name").value) element("#voice-name").value = event.target.files[0].name.replace(/\.[^.]+$/, ""); }); element("#upload-voice").addEventListener("click", uploadVoice); element("#submit-job").addEventListener("click", submitJob); element("#refresh-jobs").addEventListener("click", loadJobs); element("#compose-video").addEventListener("click", composeVideo); element("#download-project").addEventListener("click", downloadProject);

  window.addEventListener("beforeunload", () => state.objectUrls.forEach((url) => URL.revokeObjectURL(url))); window.requestAnimationFrame(() => document.body.classList.add("studio-ready"));
  if ("IntersectionObserver" in window) { const stepObserver = new IntersectionObserver((entries) => { const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0]; if (!visible) return; document.querySelectorAll(".workspace-steps a").forEach((link) => link.classList.toggle("active", link.getAttribute("href") === `#${visible.target.id}`)); }, { threshold: [0.2, 0.55], rootMargin: "-90px 0px -50%" }); document.querySelectorAll(".workspace-card").forEach((section) => stepObserver.observe(section)); }
  window.addEventListener("hashchange", guardProtectedTarget);
  renderAuth(); updateTextSummary(); updateAssetProgress(); checkHealth(); window.setInterval(checkHealth, 30000); guardProtectedTarget(); if (state.session) Promise.all([loadAccount(), loadVoices(), loadJobs(), checkModelPool()]).then(scheduleQuote);
})();
