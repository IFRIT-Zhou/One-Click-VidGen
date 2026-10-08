(() => {
  "use strict";

  const STORAGE_KEY = "ocvg.projects.v1";
  const MAX_CHARACTERS = 3;
  const MAX_IMAGE_BYTES = 12 * 1024 * 1024;
  const $ = (selector, root = document) => root.querySelector(selector);
  const uid = (prefix) => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const state = { store: null, project: null, projectId: "", scenes: [], characters: [], imageVersions: {}, selectedSceneId: null, histories: new Map(), historyTimers: new Map(), editingCharacterId: null, draftPortrait: "" };
  const activeTasks = new Set();
  const previewUrls = new Map();
  const invalidatedScenes = new Set();
  const savedSceneVisuals = new Map();
  const visualFields = ["title", "visualPrompt", "prompt", "characterIds", "currentImageVersionId"];
  function rememberSceneVisuals() { state.scenes.forEach((scene) => { savedSceneVisuals.set(scene.id, Object.fromEntries(visualFields.map((key) => [key, JSON.stringify(scene.raw[key])]))); }); }
  let tokenRefresh = null;
  let savedVisualSignature = null;
  const visualSignature = (project) => JSON.stringify([project?.characters || [], project?.imageVersions || []]);
  const busyVersion = (scene) => (state.imageVersions[scene.id] || []).find((version) => ["submitting", "queued", "running", "interrupted"].includes(version.status));
  const session = () => { try { return JSON.parse(sessionStorage.getItem("ocvg-cloud-session") || "null"); } catch (_) { return null; } };
  async function request(path, options = {}, retry = true) {
    const current = session();
    if (!current?.access_token) throw new Error("请先在云端工作室登录，再返回本页生成图片。");
    const target = new URL(path, location.origin);
    if (target.origin !== location.origin || !target.pathname.startsWith("/api/v1/")) throw new Error("图片服务地址无效。");
    const response = await fetch(target.href, { ...options, headers: { Authorization: `Bearer ${current.access_token}`, ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}), ...options.headers } });
    if (response.status === 401 && retry && current.refresh_token) {
      if (!tokenRefresh) tokenRefresh = fetch("/api/v1/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: current.refresh_token }) }).then(async (result) => { if (!result.ok) throw new Error("登录状态已失效，请重新登录。"); const fresh = await result.json(); if (session()?.refresh_token !== current.refresh_token) throw new Error("账号已切换，请刷新页面。"); sessionStorage.setItem("ocvg-cloud-session", JSON.stringify(fresh)); }).finally(() => { tokenRefresh = null; });
      await tokenRefresh;
      return request(path, options, false);
    }
    if (session()?.user?.id !== current.user?.id) throw new Error("账号已切换，请刷新页面。");
    if (!response.ok) { const data = await response.json().catch(() => ({})); const error = new Error(typeof data.detail === "string" ? data.detail : data.message || `图片服务请求失败（${response.status}）`); error.status = response.status; throw error; }
    return response;
  }
  async function api(path, body, headers) {
    const response = await request(`/api/v1${path}`, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body), headers });
    const data = await response.json();
    if (data.code && data.code !== "0") throw new Error(data.message || "图片服务请求失败。");
    return data.data || data;
  }
  function displayUrl(url) { return previewUrls.get(url) || (url.startsWith("/api/") || url.includes("/api/v1/image-pool/") ? "" : url); }
  async function hydrateImage(url) {
    if (!url || previewUrls.has(url) || !url.includes("/api/v1/image-pool/")) return;
    previewUrls.set(url, "");
    try { const response = await request(url); previewUrls.set(url, URL.createObjectURL(await response.blob())); renderAll(); }
    catch (error) { previewUrls.delete(url); showMessage(error.message, "error"); }
  }
  function invalidateVideo(scene) { invalidatedScenes.add(scene.id); scene.raw.currentVideoClipId = null; scene.raw.current_video_clip_id = null; scene.raw.videoStale = true; state.project.videoNeedsRender = true; }

  function parseStore() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); }
    catch (_) { return []; }
  }

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
  function scenePrompt(scene) { return String(scene?.visualPrompt || scene?.prompt || scene?.imagePrompt || scene?.image_prompt || scene?.description || ""); }
  function sceneImage(scene) { return String(scene?.imageUrl || scene?.image_url || scene?.image || scene?.thumbnail || ""); }

  function normalizeVersions(project, scenes) {
    const result = {};
    const source = project.imageVersions && typeof project.imageVersions === "object" ? project.imageVersions : {};
    scenes.forEach((entry) => {
      const raw = Array.isArray(source) ? source.filter((item) => String(item.sceneId ?? item.scene_id) === entry.id) : source[entry.id];
      const nested = Array.isArray(entry.raw.imageVersions) ? entry.raw.imageVersions : [];
      const versions = Array.isArray(raw) ? raw : nested;
      result[entry.id] = versions.map((version, index) => ({
        ...version,
        id: String(version.id || uid(`image-${index}`)),
        url: String(version.url || version.dataUrl || version.imageUrl || version.image_url || ""),
        name: String(version.name || version.label || `版本 ${index + 1}`),
        status: String(version.status || (version.url || version.dataUrl ? "ready" : "pending_generation")),
        createdAt: version.createdAt || version.created_at || new Date().toISOString(),
      }));
      const initialImage = sceneImage(entry.raw);
      if (!result[entry.id].length && initialImage) result[entry.id].push({ id: uid("original"), url: initialImage, name: "项目原图", status: "ready", createdAt: new Date().toISOString(), source: "project" });
      const requestedCurrent = String(entry.raw.currentImageVersionId || entry.raw.current_image_version_id || "");
      entry.currentVersionId = result[entry.id].some((item) => item.id === requestedCurrent) ? requestedCurrent : result[entry.id][0]?.id || null;
    });
    return result;
  }

  function loadProject() {
    state.store = parseStore();
    state.projectId = new URLSearchParams(location.search).get("project") || "";
    state.project = locateProject(state.store, state.projectId);
    if (!state.project) {
      showPageNotice(state.projectId ? `找不到项目“${state.projectId}”。请从项目工作台重新进入。` : "链接中缺少 project 参数，请从项目工作台选择项目。", true);
      state.project = { id: state.projectId || "unbound", title: "未绑定项目", scenes: [] };
    }
    state.projectId = projectIdOf(state.project) || state.projectId;
    const rawScenes = Array.isArray(state.project.scenes) ? state.project.scenes : Array.isArray(state.project.shots) ? state.project.shots : [];
    state.scenes = rawScenes.map((raw, index) => ({ id: sceneId(raw, index), raw, index, title: sceneTitle(raw, index), prompt: scenePrompt(raw), characterIds: Array.isArray(raw.characterIds) ? [...raw.characterIds].map(String) : Array.isArray(raw.character_ids) ? [...raw.character_ids].map(String) : [], currentVersionId: null }));
    state.characters = Array.isArray(state.project.characters) ? state.project.characters.slice(0, MAX_CHARACTERS).map((character) => ({ ...character, id: String(character.id || uid("character")), name: String(character.name || "未命名角色"), role: String(character.role || ""), description: String(character.description || character.appearance || ""), image: String(character.image || character.imageDataUrl || character.referenceImage || "") })) : [];
    state.imageVersions = normalizeVersions(state.project, state.scenes);
    const requestedScene = new URLSearchParams(location.search).get("scene");
    state.selectedSceneId = state.scenes.find(scene => scene.id === requestedScene)?.id || state.scenes[0]?.id || null;
    state.scenes.forEach((scene) => state.histories.set(scene.id, { entries: [snapshot(scene)], cursor: 0 }));
  }

  function snapshot(scene) { return JSON.stringify({ title: scene.title, prompt: scene.prompt, characterIds: scene.characterIds, currentVersionId: scene.currentVersionId, versions: state.imageVersions[scene.id] || [] }); }
  function restoreSnapshot(scene, serialized) { const data = JSON.parse(serialized); scene.title = data.title; scene.prompt = data.prompt; scene.characterIds = data.characterIds; scene.currentVersionId = data.currentVersionId; state.imageVersions[scene.id] = data.versions; syncSceneRaw(scene); }

  function commitHistory(scene) {
    if (!scene) return;
    clearTimeout(state.historyTimers.get(scene.id));
    const history = state.histories.get(scene.id) || { entries: [], cursor: -1 };
    const serialized = snapshot(scene);
    if (history.entries[history.cursor] === serialized) return;
    history.entries = history.entries.slice(0, history.cursor + 1);
    history.entries.push(serialized);
    if (history.entries.length > 30) history.entries.shift();
    history.cursor = history.entries.length - 1;
    state.histories.set(scene.id, history);
    persist();
    renderHistoryButtons();
  }

  function scheduleHistory(scene) { clearTimeout(state.historyTimers.get(scene.id)); state.historyTimers.set(scene.id, setTimeout(() => commitHistory(scene), 450)); }
  function currentScene() { return state.scenes.find((scene) => scene.id === state.selectedSceneId) || null; }
  function currentVersion(scene = currentScene()) { return scene ? (state.imageVersions[scene.id] || []).find((version) => version.id === scene.currentVersionId) || null : null; }
  function syncSceneRaw(scene) { scene.raw.title = scene.title; scene.raw.visualPrompt = scene.prompt; scene.raw.prompt = scene.prompt; scene.raw.characterIds = [...scene.characterIds]; scene.raw.currentImageVersionId = scene.currentVersionId; }

  function persist() {
    if (!locateProject(state.store, state.projectId)) return;
    state.project.characters = state.characters.map((character) => ({ ...character }));
    state.project.imageVersions = Object.entries(state.imageVersions).flatMap(([sceneIdValue, versions]) => versions.map((version) => ({ ...version, sceneId: sceneIdValue })));
    state.scenes.forEach(syncSceneRaw);
    state.project.updatedAt = new Date().toISOString();
    const indicator = $("#save-state"); indicator.className = "save-state saving"; indicator.innerHTML = "<i></i>保存中";
    try {
      const latestStore = parseStore(); const latest = locateProject(latestStore, state.projectId);
      if (!latest) throw new Error("项目已在其他页面删除，未覆盖当前存储。");
      if (savedVisualSignature !== null && visualSignature(latest) !== savedVisualSignature) throw new Error("角色或图片版本已在其他页面修改，请刷新后继续，当前操作未覆盖其内容。");
      latest.characters = state.project.characters; latest.imageVersions = state.project.imageVersions;
      (latest.scenes || latest.shots || []).forEach((raw) => {
        const edited = state.scenes.find((scene) => scene.id === String(raw.id ?? raw.sceneId ?? raw.scene_id));
        if (!edited) return;
        const previous = savedSceneVisuals.get(edited.id) || {};
        visualFields.forEach((key) => { if (key in edited.raw && JSON.stringify(edited.raw[key]) !== previous[key]) raw[key] = edited.raw[key]; });
        if (invalidatedScenes.has(edited.id)) { raw.currentVideoClipId = null; raw.current_video_clip_id = null; raw.videoStale = true; }
      });
      if (invalidatedScenes.size) latest.videoNeedsRender = true;
      latest.updatedAt = state.project.updatedAt;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(latestStore)); savedVisualSignature = visualSignature(latest); rememberSceneVisuals(); invalidatedScenes.clear();
      indicator.className = "save-state"; indicator.innerHTML = "<i></i>已保存到本机"; return true;
    }
    catch (error) { indicator.className = "save-state error"; indicator.innerHTML = "<i></i>保存失败"; showMessage(error.name === "QuotaExceededError" ? "本机存储空间不足，请清理较大的图片版本。" : error.message, "error"); return false; }
  }

  function escapeHtml(value) { const div = document.createElement("div"); div.textContent = String(value ?? ""); return div.innerHTML; }
  function escapeAttr(value) { return escapeHtml(value).replace(/"/g, "&quot;"); }
  function shortDate(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "刚刚" : new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date); }
  function showPageNotice(text, visible = true) { const node = $("#page-notice"); node.textContent = text; node.classList.toggle("hidden", !visible); }
  function showMessage(text, type = "") { const node = $("#action-message"); node.textContent = text; node.className = `inline-notice ${type}`.trim(); }

  function renderCharacters() {
    $("#character-count").textContent = `${state.characters.length} / ${MAX_CHARACTERS}`;
    $("#add-character").disabled = state.characters.length >= MAX_CHARACTERS;
    $("#character-grid").innerHTML = state.characters.map((character) => `<button class="character-card" type="button" data-character-id="${escapeAttr(character.id)}">${character.image ? `<img src="${escapeAttr(character.image)}" alt="${escapeAttr(character.name)}">` : `<span class="character-avatar">${escapeHtml(character.name.slice(0, 1))}</span>`}<span><strong>${escapeHtml(character.name)}</strong><small>${escapeHtml(character.role || "未设置角色定位")}</small><em>${escapeHtml(character.description || "暂无外观描述")}</em></span><span class="character-edit">✎</span></button>`).join("");
    $("#character-grid").querySelectorAll("[data-character-id]").forEach((button) => button.addEventListener("click", () => openCharacterDialog(button.dataset.characterId)));
    renderCharacterOptions();
  }

  function renderCharacterOptions() {
    const scene = currentScene();
    const node = $("#character-options");
    if (!state.characters.length) { node.innerHTML = '<span class="empty-options">先在上方创建角色档案</span>'; return; }
    node.innerHTML = state.characters.map((character) => `<label class="character-option"><input type="checkbox" value="${escapeAttr(character.id)}" ${scene?.characterIds.includes(character.id) ? "checked" : ""}><span>${character.image ? "●" : "○"} ${escapeHtml(character.name)}</span></label>`).join("");
    node.querySelectorAll("input").forEach((input) => input.addEventListener("change", () => { const selected = currentScene(); if (!selected) return; selected.characterIds = [...node.querySelectorAll("input:checked")].map((item) => item.value); syncSceneRaw(selected); commitHistory(selected); }));
  }

  function renderScenes() {
    $("#scene-count").textContent = `${state.scenes.length} 镜`;
    const node = $("#scene-list");
    if (!state.scenes.length) { node.innerHTML = '<div class="empty-list">项目还没有分镜。请先在项目工作台生成或添加镜头。</div>'; return; }
    node.innerHTML = state.scenes.map((scene) => { const version = currentVersion(scene); return `<button class="scene-item ${scene.id === state.selectedSceneId ? "active" : ""}" type="button" data-scene-id="${escapeAttr(scene.id)}"><span class="scene-thumb">${version?.url ? `<img src="${escapeAttr(displayUrl(version.url))}" alt="">` : `<span>${String(scene.index + 1).padStart(2, "0")}</span>`}</span><span><strong>${escapeHtml(scene.title)}</strong><small>${version?.status === "pending_generation" ? "待生成" : version ? "已有画面" : "暂无画面"}</small></span></button>`; }).join("");
    node.querySelectorAll("[data-scene-id]").forEach((button) => button.addEventListener("click", () => { state.selectedSceneId = button.dataset.sceneId; renderAll(); }));
  }

  function renderCanvas() {
    const scene = currentScene(); const image = $("#current-image"); const empty = $("#empty-art"); const generation = $("#generation-state");
    [$("#shot-title-input"), $("#prompt-input"), $("#redraw-button"), $("#local-image-input")].forEach((element) => { element.disabled = !scene; });
    if (!scene) { $("#scene-kicker").textContent = "镜头 —"; $("#scene-title").textContent = "选择一个镜头"; $("#shot-badge").textContent = "SHOT —"; image.removeAttribute("src"); empty.classList.remove("hidden"); generation.classList.add("hidden"); $("#image-status").textContent = "尚无可用画面"; $("#image-dimensions").textContent = "—"; $("#shot-title-input").value = ""; $("#prompt-input").value = ""; return; }
    const version = currentVersion(scene); $("#scene-kicker").textContent = `镜头 ${String(scene.index + 1).padStart(2, "0")}`; $("#scene-title").textContent = scene.title; $("#shot-badge").textContent = `SHOT ${String(scene.index + 1).padStart(2, "0")}`; $("#shot-title-input").value = scene.title; $("#prompt-input").value = scene.prompt; $("#prompt-count").textContent = scene.prompt.length;
    if (version?.url) { const url = displayUrl(version.url); if (url) image.src = url; else image.removeAttribute("src"); empty.classList.add("hidden"); generation.classList.add("hidden"); $("#image-status").textContent = `${version.name} · 当前采用`; image.onload = () => { $("#image-dimensions").textContent = `${image.naturalWidth} × ${image.naturalHeight}`; }; }
    else { image.removeAttribute("src"); empty.classList.toggle("hidden", version?.status === "pending_generation"); generation.classList.toggle("hidden", version?.status !== "pending_generation"); $("#image-status").textContent = version?.status === "pending_generation" ? "待生成任务已记录" : "尚无可用画面"; $("#image-dimensions").textContent = "—"; }
  }

  function renderVersions() {
    const scene = currentScene(); const versions = scene ? state.imageVersions[scene.id] || [] : [];
    $("#version-count").textContent = `${versions.length} 个版本`;
    const node = $("#version-list");
    if (!versions.length) { node.innerHTML = '<div class="empty-versions">当前镜头还没有图片版本。上传本地图片或提交整图重绘后会显示在这里。</div>'; return; }
    node.innerHTML = versions.slice().reverse().map((version) => `<article class="version-card ${version.id === scene.currentVersionId ? "current" : ""}"><div class="version-preview">${version.url ? `<img src="${escapeAttr(displayUrl(version.url))}" alt="${escapeAttr(version.name)}">` : `<span>${escapeHtml(version.error || ({ submitting: "正在提交", queued: "云端排队中", running: "云端生成中", interrupted: "查询已中断", failed: "生成失败", pending_generation: "历史待生成记录" }[version.status] || "无预览"))}</span>`}${version.id === scene.currentVersionId ? '<b class="current-mark">当前采用</b>' : ""}</div><div class="version-body"><strong>${escapeHtml(version.name)}</strong><small>${shortDate(version.createdAt)} · ${version.source === "upload" ? "本地替换" : version.status === "pending_generation" ? "重绘请求" : "项目素材"}</small><div class="version-actions"><button type="button" data-set-version="${escapeAttr(version.id)}" ${version.id === scene.currentVersionId ? "disabled" : ""}>设为当前</button><button class="danger" type="button" data-delete-version="${escapeAttr(version.id)}" aria-label="删除版本">×</button></div></div></article>`).join("");
    node.querySelectorAll("[data-set-version]").forEach((button) => { const version = versions.find((item) => item.id === button.dataset.setVersion); if (!version?.url) button.disabled = true; button.addEventListener("click", () => { if (!version?.url || busyVersion(scene)) return; scene.currentVersionId = button.dataset.setVersion; invalidateVideo(scene); syncSceneRaw(scene); commitHistory(scene); renderAll(); }); });
    node.querySelectorAll("[data-delete-version]").forEach((button) => button.addEventListener("click", () => deleteVersion(scene, button.dataset.deleteVersion)));
  }

  function renderHistoryButtons() { const scene = currentScene(); const history = scene && state.histories.get(scene.id); const busy = scene && busyVersion(scene); $("#undo-button").disabled = Boolean(busy) || !history || history.cursor <= 0; $("#redo-button").disabled = Boolean(busy) || !history || history.cursor >= history.entries.length - 1; }
  function renderAll() {
    renderCharacters(); renderScenes(); renderCanvas(); renderCharacterOptions(); renderVersions(); renderHistoryButtons();
    const scene = currentScene(); const pending = scene && busyVersion(scene);
    $("#redraw-button").disabled = !scene || Boolean(pending && activeTasks.has(pending.id));
    $("#redraw-button").querySelector("b").textContent = pending ? activeTasks.has(pending.id) ? "云端生成中…" : "继续查询 / 恢复提交" : "参考图驱动整图重绘";
    if (pending) $("#image-status").textContent += ` · ${pending.status === "interrupted" ? "查询中断，可继续" : "云端生成中，保留原图"}`;
    document.querySelectorAll("img").forEach((image) => { const url = image.getAttribute("src") || ""; if (url.includes("/api/v1/image-pool/")) { const resolved = displayUrl(url); if (resolved) image.src = resolved; else image.removeAttribute("src"); } });
    if (pending) document.querySelectorAll("[data-delete-version], [data-set-version]").forEach((button) => { button.disabled = true; });
  }

  function moveHistory(direction) { const scene = currentScene(); const history = scene && state.histories.get(scene.id); if (!history) return; const next = history.cursor + direction; if (next < 0 || next >= history.entries.length) return; history.cursor = next; restoreSnapshot(scene, history.entries[next]); persist(); renderAll(); }

  function deleteVersion(scene, versionId) {
    if (busyVersion(scene)) return;
    const versions = state.imageVersions[scene.id] || []; const index = versions.findIndex((item) => item.id === versionId); if (index < 0) return;
    versions.splice(index, 1); if (scene.currentVersionId === versionId) scene.currentVersionId = versions[Math.max(0, index - 1)]?.id || versions[0]?.id || null;
    invalidateVideo(scene); syncSceneRaw(scene); commitHistory(scene); renderAll(); showMessage("图片版本已删除。", "success");
  }

  async function compressImage(file, maxEdge = 1600, quality = 0.84) {
    if (!file.type.startsWith("image/")) throw new Error("请选择 JPG、PNG 或 WebP 图片。");
    if (file.size > MAX_IMAGE_BYTES) throw new Error("单张图片不能超过 12 MB。");
    const source = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error("无法读取图片。")); reader.readAsDataURL(file); });
    const image = await new Promise((resolve, reject) => { const item = new Image(); item.onload = () => resolve(item); item.onerror = () => reject(new Error("图片格式无法解析。")); item.src = source; });
    const scale = Math.min(1, maxEdge / Math.max(image.naturalWidth, image.naturalHeight)); const canvas = document.createElement("canvas"); canvas.width = Math.max(1, Math.round(image.naturalWidth * scale)); canvas.height = Math.max(1, Math.round(image.naturalHeight * scale)); canvas.getContext("2d", { alpha: false }).drawImage(image, 0, 0, canvas.width, canvas.height); return canvas.toDataURL("image/jpeg", quality);
  }

  async function handleLocalImage(file) {
    const scene = currentScene(); if (!scene || !file) return;
    showMessage("正在压缩并保存图片…");
    try { const dataUrl = await compressImage(file); const version = { id: uid("image"), url: dataUrl, name: file.name.replace(/\.[^.]+$/, "").slice(0, 60) || "本地图片", status: "ready", source: "upload", originalName: file.name, createdAt: new Date().toISOString() }; (state.imageVersions[scene.id] ||= []).push(version); scene.currentVersionId = version.id; invalidateVideo(scene); syncSceneRaw(scene); commitHistory(scene); renderAll(); showMessage("本地图片已压缩并设为当前版本。", "success"); }
    catch (error) { showMessage(error.message || "图片处理失败。", "error"); }
  }

  async function referenceUrl(character) {
    if (!character.image) return null;
    if (!character.image.startsWith("data:")) return character.image;
    const response = await fetch(character.image);
    const form = new FormData(); form.append("file", await response.blob(), `${character.id}.jpg`);
    const uploaded = await api("/image-pool/media/upload", form);
    if (!uploaded.download_url) throw new Error("参考图上传未返回地址。");
    return uploaded.download_url;
  }
  async function runGeneration(scene, version) {
    if (activeTasks.has(version.id)) return;
    activeTasks.add(version.id); renderAll();
    try {
      if (version.account && version.account !== session()?.user?.id) throw new Error("该图片任务属于另一账号，请切回原账号后继续。");
      if (!version.taskId) {
        version.status = "submitting";
        if (!version.requestPayload) {
          const references = [];
          for (const character of version.referenceCharacters || []) { const url = await referenceUrl(character); if (url) references.push(url); }
          version.requestPayload = { clientJobId: version.clientJobId, prompt: version.prompt, aspectRatio: state.project.aspectRatio || "16:9", resolution: state.project.resolution || "1k", imageUrls: references };
          delete version.referenceCharacters;
          if (!persist()) throw new Error("无法保存任务恢复信息，未提交生成。请释放浏览器存储空间。");
        }
        const result = await api("/image-pool/generate", version.requestPayload, { "Idempotency-Key": version.clientJobId });
        version.taskId = result.taskId || result.task_id;
        if (!version.taskId) throw new Error("图片服务未返回任务编号；可点击继续查询安全重试。");
        version.status = "queued"; persist(); renderAll();
      }
      for (let attempt = 0; attempt < 120; attempt++) {
        if (version.account && version.account !== session()?.user?.id) throw new Error("账号已切换，已停止查询。");
        const result = await api("/image-pool/query", { taskId: version.taskId });
        const status = String(result.status).toUpperCase();
        if (["FAILED", "ERROR", "CANCELLED"].includes(status)) { version.status = "failed"; throw new Error(result.message || "云端图片生成失败。请检查提示词后重新生成。"); }
        if (status === "SUCCESS") {
          const url = result.imageUrl || result.image_url;
          if (!url) throw new Error("任务已完成，但尚未返回图片地址。请继续查询。");
          const response = await request(url);
          const blob = await response.blob();
          if (!blob.size || !blob.type.startsWith("image/")) throw new Error("生成结果不是可用图片。请继续查询。");
          previewUrls.set(url, URL.createObjectURL(blob));
          version.url = url; version.status = "ready"; version.chargedCredits = result.charged_credits; delete version.error;
          scene.currentVersionId = version.id; invalidateVideo(scene); syncSceneRaw(scene); commitHistory(scene); renderAll();
          showMessage(`“${scene.title}”云端图片已生成并采用。${result.charged_credits != null ? `实际消耗 ${result.charged_credits} 积分。` : ""}旧视频需要重新合成。`, "success");
          return;
        }
        version.status = status === "QUEUED" ? "queued" : "running"; persist(); renderAll();
        await new Promise((resolve) => setTimeout(resolve, 2500));
      }
      throw new Error("任务仍在云端处理，可点击继续查询；不会重复创建任务。");
    } catch (error) {
      if (!version.taskId && [400, 402, 403, 404, 409, 413, 415, 422].includes(error.status)) version.status = "failed";
      if (version.status !== "failed") version.status = "interrupted";
      version.error = error.message || "图片请求中断。"; persist(); showMessage(version.error, "error");
    } finally { activeTasks.delete(version.id); renderAll(); }
  }
  async function requestRedraw() {
    const scene = currentScene(); if (!scene) return;
    const existing = busyVersion(scene);
    if (existing) { await runGeneration(scene, existing); return; }
    if (!scene.prompt.trim()) { showMessage("请先填写生图提示词。", "error"); return; }
    if (!session()?.access_token) { showMessage("请先在云端工作室登录，再返回本页生成图片。", "error"); return; }
    const characters = state.characters.filter((character) => scene.characterIds.includes(character.id)).slice(0, MAX_CHARACTERS);
    const descriptions = characters.map((character) => `${character.name}：${character.description || character.role || "保持参考图外观"}`).join("；");
    const version = { id: uid("redraw"), clientJobId: uid("image-request"), account: session()?.user?.id, url: "", name: `云端重绘 ${state.imageVersions[scene.id].length + 1}`, status: "submitting", source: "redraw", prompt: `${scene.prompt}${descriptions ? `\n参考角色：${descriptions}` : ""}`, characterIds: [...scene.characterIds], referenceCharacters: characters.map((character) => ({ ...character })), createdAt: new Date().toISOString() };
    state.imageVersions[scene.id].push(version);
    if (!persist()) { state.imageVersions[scene.id].pop(); return; }
    await runGeneration(scene, version);
  }

  function openCharacterDialog(id = null) {
    const character = state.characters.find((item) => item.id === id); state.editingCharacterId = character?.id || null; state.draftPortrait = character?.image || "";
    $("#character-dialog-title").textContent = character ? "编辑角色档案" : "新建角色档案"; $("#character-name").value = character?.name || ""; $("#character-role").value = character?.role || ""; $("#character-description").value = character?.description || ""; $("#delete-character").classList.toggle("hidden", !character); updatePortraitPreview(); $("#character-dialog").showModal();
  }
  function updatePortraitPreview() { const preview = $("#character-preview"); if (state.draftPortrait) preview.src = state.draftPortrait; else preview.removeAttribute("src"); $("#character-placeholder").classList.toggle("hidden", Boolean(state.draftPortrait)); }
  async function handleCharacterImage(file) { if (!file) return; try { state.draftPortrait = await compressImage(file, 1200, 0.82); updatePortraitPreview(); } catch (error) { showMessage(error.message, "error"); } }
  function saveCharacter() {
    const name = $("#character-name").value.trim(); if (!name) { $("#character-name").focus(); return; }
    const data = { name, role: $("#character-role").value.trim(), description: $("#character-description").value.trim(), image: state.draftPortrait };
    const index = state.characters.findIndex((item) => item.id === state.editingCharacterId);
    if (index >= 0) state.characters[index] = { ...state.characters[index], ...data }; else if (state.characters.length < MAX_CHARACTERS) state.characters.push({ id: uid("character"), ...data });
    persist(); $("#character-dialog").close(); renderAll(); showMessage("角色档案已保存。", "success");
  }
  function deleteCharacter() {
    const id = state.editingCharacterId; if (!id) return; state.characters = state.characters.filter((item) => item.id !== id); state.scenes.forEach((scene) => { scene.characterIds = scene.characterIds.filter((item) => item !== id); syncSceneRaw(scene); }); persist(); $("#character-dialog").close(); renderAll(); showMessage("角色档案已删除，镜头绑定也已更新。", "success");
  }

  function bindEvents() {
    $("#add-character").addEventListener("click", () => openCharacterDialog());
    $("#character-image").addEventListener("change", (event) => handleCharacterImage(event.target.files[0]).finally(() => { event.target.value = ""; }));
    $("#save-character").addEventListener("click", saveCharacter); $("#delete-character").addEventListener("click", deleteCharacter);
    $("#shot-title-input").addEventListener("input", (event) => { const scene = currentScene(); if (!scene) return; scene.title = event.target.value; syncSceneRaw(scene); $("#scene-title").textContent = scene.title || "未命名镜头"; scheduleHistory(scene); });
    $("#prompt-input").addEventListener("input", (event) => { const scene = currentScene(); if (!scene) return; scene.prompt = event.target.value; syncSceneRaw(scene); $("#prompt-count").textContent = scene.prompt.length; scheduleHistory(scene); });
    $("#shot-title-input").addEventListener("change", () => commitHistory(currentScene())); $("#prompt-input").addEventListener("change", () => commitHistory(currentScene()));
    $("#local-image-input").addEventListener("change", (event) => handleLocalImage(event.target.files[0]).finally(() => { event.target.value = ""; }));
    $("#redraw-button").addEventListener("click", requestRedraw); $("#undo-button").addEventListener("click", () => moveHistory(-1)); $("#redo-button").addEventListener("click", () => moveHistory(1));
  }

  loadProject();
  savedVisualSignature = visualSignature(state.project);
  rememberSceneVisuals();
  const title = state.project.title || state.project.name || "未命名项目"; $("#project-title").textContent = title; document.title = `${title} · 视觉编辑器｜One-Click VidGen`;
  const query = state.projectId ? `?project=${encodeURIComponent(state.projectId)}` : ""; $("#back-project").href = `/workspace/${query}`; $("#open-video").href = `/video-editor/${query}`;
  bindEvents(); renderAll();
  document.addEventListener("ocvg:flush-project", (event) => { if (!persist()) event.preventDefault(); });
  Object.values(state.imageVersions).flat().forEach((version) => { if (version.url) hydrateImage(version.url); });
  state.scenes.forEach((scene) => { const version = busyVersion(scene); if (version?.taskId) runGeneration(scene, version); else if (version) { version.status = "interrupted"; renderAll(); } });
  addEventListener("beforeunload", () => { previewUrls.forEach((url) => { if (url) URL.revokeObjectURL(url); }); });
})();
