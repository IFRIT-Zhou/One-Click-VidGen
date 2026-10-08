(() => {
  "use strict";

  const STORAGE_KEY = "ocvg.projects.v1";
  const params = new URLSearchParams(window.location.search);
  const requestedId = params.get("project");
  const titleInput = document.querySelector("#workspace-title");
  const scriptInput = document.querySelector("#workspace-script");
  const sceneList = document.querySelector("#scene-list");
  const emptyScenes = document.querySelector("#empty-scenes");
  const sceneDialog = document.querySelector("#scene-dialog");
  const sceneForm = document.querySelector("#scene-form");
  const saveState = document.querySelector("#save-state");
  const toast = document.querySelector("#workspace-toast");
  let projects = loadProjects();
  let project = findOrCreateProject();
  let baseline = JSON.parse(JSON.stringify(window.OCVGProjectStore.unpack(JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]")).find(item => item.id === project.id) || project));
  let saveTimer = 0;
  let toastTimer = 0;
  const ownedFields = ["title", "script", "scenes", "voiceLines", "audio", "subtitles", "exports", "scriptScenesOutOfSync"];

  function makeId(prefix = "project") {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return `${prefix}-${window.crypto.randomUUID()}`;
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
  }

  function asArray(value) { return Array.isArray(value) ? value : []; }

  function normalizeScene(scene, index) {
    const source = scene && typeof scene === "object" ? scene : {};
    return { ...source, id: String(source.id || makeId("scene")), title: String(source.title || `镜头 ${String(index + 1).padStart(2, "0")}`), narration: String(source.narration || source.text || ""), visualPrompt: String(source.visualPrompt || source.prompt || ""), duration: Number(source.duration) || 0, order: index };
  }

  function normalizeProject(value, index = 0) {
    const source = value && typeof value === "object" ? value : {};
    const now = new Date().toISOString();
    return {
      ...source,
      id: String(source.id || makeId("project")), title: String(source.title || `未命名项目 ${index + 1}`), script: String(source.script || ""),
      createdAt: String(source.createdAt || now), updatedAt: String(source.updatedAt || source.createdAt || now),
      scenes: asArray(source.scenes).map(normalizeScene), characters: asArray(source.characters), subtitles: asArray(source.subtitles), audio: asArray(source.audio), voiceLines: asArray(source.voiceLines), imageVersions: source.imageVersions && typeof source.imageVersions === "object" ? source.imageVersions : [], videoClips: source.videoClips && typeof source.videoClips === "object" ? source.videoClips : [], bgm: asArray(source.bgm), exports: asArray(source.exports),
    };
  }

  function loadProjects() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      const list = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.projects) ? parsed.projects : Object.values(parsed || {});
      return list.filter((item) => item && typeof item === "object").map(normalizeProject);
    } catch (error) {
      console.warn("无法读取项目数据", error);
      return [];
    }
  }

  function findOrCreateProject() {
    const found = requestedId ? projects.find((item) => item.id === requestedId) : projects.slice().sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt))[0];
    if (found) {
      if (!requestedId) history.replaceState(null, "", `/workspace/?project=${encodeURIComponent(found.id)}`);
      return found;
    }
    const now = new Date().toISOString();
    const created = normalizeProject({ id: requestedId || makeId("project"), title: "未命名项目", createdAt: now, updatedAt: now });
    projects.unshift(created);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(projects));
    history.replaceState(null, "", `/workspace/?project=${encodeURIComponent(created.id)}`);
    return created;
  }

  function persistNow() {
    window.clearTimeout(saveTimer);
    saveTimer = 0;
    try {
      const changed = ownedFields.some(field => JSON.stringify(project[field]) !== JSON.stringify(baseline[field]));
      if (!changed) {
        saveState.classList.remove("saving");
        saveState.querySelector("b").textContent = "已保存到本机";
        return true;
      }
      const latest = window.OCVGProjectStore.unpack(JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]")).find(item => item.id === project.id);
      if (document.activeElement?.closest?.("#scene-list") && JSON.stringify(latest?.scenes) !== JSON.stringify(baseline.scenes)) throw new Error("镜头已在其他页面修改，请保留当前编辑并刷新核对；未覆盖任何内容。");
      for (const [input, field] of [[titleInput, "title"], [scriptInput, "script"]]) {
        if (document.activeElement === input && latest?.[field] !== baseline[field]) throw new Error("正在编辑的内容已在其他页面修改，请保留当前输入并刷新核对；未覆盖任何内容。");
      }
      const priorScenes = JSON.stringify(project.scenes);
      project = normalizeProject(window.OCVGProjectStore.save(localStorage, STORAGE_KEY, baseline, project, ownedFields));
      if (JSON.stringify(project.scenes) !== priorScenes) renderScenes();
      if (document.activeElement !== titleInput) titleInput.value = project.title;
      if (document.activeElement !== scriptInput) scriptInput.value = project.script;
      document.title = `${project.title}｜脚本与分镜`;
      updateScriptStats();
      baseline = JSON.parse(JSON.stringify(window.OCVGProjectStore.unpack(JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]")).find(item => item.id === project.id) || project));
      projects = loadProjects();
      saveState.classList.remove("saving");
      saveState.querySelector("b").textContent = "已保存到本机";
      updateOverview();
      updateScriptSync();
      if (changed) document.dispatchEvent(new CustomEvent("ocvg:project-saved", { detail: { id: project.id } }));
      return true;
    } catch (error) {
      saveState.querySelector("b").textContent = "保存失败，编辑仍保留";
      showToast(error.message || "本机存储空间不足，请导出项目备份");
      return false;
    }
  }

  function scheduleSave() {
    saveState.classList.add("saving");
    saveState.querySelector("b").textContent = "保存中";
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(persistNow, 450);
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
  }

  function renderScenes() {
    emptyScenes.hidden = project.scenes.length > 0;
    sceneList.hidden = project.scenes.length === 0;
    sceneList.innerHTML = project.scenes.map((scene, index) => `<article class="scene-item" data-scene-id="${escapeHtml(scene.id)}">
      <div class="scene-number"><small>SCENE</small><strong>${String(index + 1).padStart(2, "0")}</strong></div>
      <div class="scene-content"><input data-field="title" maxlength="80" value="${escapeHtml(scene.title)}" aria-label="镜头 ${index + 1} 标题" /><textarea data-field="narration" maxlength="3000" aria-label="镜头 ${index + 1} 旁白" placeholder="添加这个镜头的旁白…">${escapeHtml(scene.narration)}</textarea><div class="scene-visual-line">画面 · ${escapeHtml(scene.visualPrompt || "尚未添加画面描述")}</div><small class="scene-stale-note" ${scene.materialsStale ? "" : "hidden"}>旁白或脚本已变更，请重新配音并确认画面；相关旧字幕已隐藏，需重新校对。</small></div>
      <div class="scene-actions"><button type="button" data-action="up" aria-label="上移镜头" ${index === 0 ? "disabled" : ""}>↑</button><button type="button" data-action="down" aria-label="下移镜头" ${index === project.scenes.length - 1 ? "disabled" : ""}>↓</button><button class="remove-scene" type="button" data-action="remove">删除镜头</button></div>
    </article>`).join("");
  }

  function updateScriptStats() {
    const value = project.script.trim();
    const paragraphs = value ? value.split(/\n\s*\n|\n/).filter((part) => part.trim()).length : 0;
    document.querySelector("#script-char-count").textContent = String(value.length);
    document.querySelector("#script-paragraph-count").textContent = String(paragraphs);
  }

  function mediaRecords(value) { return Array.isArray(value) ? value : Object.values(value || {}).flat(); }

  function scriptsDiffer(value) {
    const compact = text => String(text || "").replace(/\s+/g, "");
    return Boolean(value.scenes?.length) && compact(value.script) !== compact(value.scenes.map(scene => scene.narration || "").join(""));
  }
  function invalidateSceneMedia(value, sceneIds, replaceNarration = false) {
    const affected = new Set(sceneIds);
    for (const scene of value.scenes || []) if (affected.has(scene.id)) {
      scene.currentImageVersionId = null; scene.currentVideoClipId = null; scene.videoStale = true; scene.materialsStale = true;
    }
    for (const field of ["voiceLines", "audio"]) {
      const old = value[field] || [];
      value[field] = old.filter(line => !affected.has(line.sceneId));
      for (const scene of value.scenes || []) if (affected.has(scene.id)) {
        const prior = old.filter(line => line.sceneId === scene.id);
        if (replaceNarration || !prior.length) {
          value[field].push({ id: `voice-${scene.id}`, sceneId: scene.id, text: scene.narration, speechText: scene.narration, subtitleText: scene.narration, status: "draft", pause: 0 });
        } else value[field].push(...prior.map(line => {
          const clean = { ...line, status: "draft" };
          for (const key of ["cloudAudio", "url", "audioUrl", "blobUrl", "duration", "durationSeconds"]) delete clean[key];
          return clean;
        }));
      }
    }
    value.subtitles = (value.subtitles || []).map(cue => affected.has(cue.sceneId) ? { ...cue, hidden: true, stale: true } : cue);
    value.exports = (value.exports || []).map(record => ({ ...record, stale: true }));
  }
  function updateScriptSync() {
    const mismatch = scriptsDiffer(project);
    const node = document.querySelector("#script-sync-message");
    node.hidden = !mismatch;
    document.querySelector("#scenes-to-script").hidden = !mismatch;
    node.textContent = "脚本与镜头旁白不同步。素材已标为待更新；选择按脚本重建镜头，或保留手工镜头并以其旁白更新脚本。";
    document.querySelector("#script-to-scenes").textContent = project.scenes.length ? "按脚本更新镜头" : "按段落生成镜头";
  }
  function noteNarrationChange(scene) {
    invalidateSceneMedia(project, [scene.id], true);
    project.scriptScenesOutOfSync = scriptsDiffer(project);
    updateScriptSync(); updateOverview();
  }

  function readiness(project, account = "") {
    const scenes = project.scenes || [];
    const images = Array.isArray(project.imageVersions) ? project.imageVersions : Object.entries(project.imageVersions || {}).flatMap(([sceneId, list]) => (Array.isArray(list) ? list : list.versions || []).map(item => ({ ...item, sceneId })));
    const clips = Array.isArray(project.videoClips) ? project.videoClips : Object.entries(project.videoClips || {}).flatMap(([sceneId, list]) => (Array.isArray(list) ? list : list.versions || []).map(item => ({ ...item, sceneId })));
    const available = item => Boolean(item?.url || item?.download_url || item?.videoUrl) && !/^(blob:|file:)/i.test(item.url || item.videoUrl || "") && (!item.account ? /^(data:|https?:)/i.test(item.url || item.videoUrl || "") : item.account === account);
    const hasVideo = scene => !scene.videoStale && clips.some(clip => clip.id === scene.currentVideoClipId && clip.sceneId === scene.id && ["ready", "complete", "completed"].includes(clip.status) && available(clip));
    const lines = project.voiceLines?.length ? project.voiceLines : project.audio || [];
    const hasScript = Boolean(project.script?.trim()), hasScenes = scenes.length > 0, hasSubtitles = (project.subtitles || []).some(cue => !cue.hidden && cue.text?.trim() && cue.end > cue.start);
    const missingImages = scenes.filter(scene => !hasVideo(scene) && !images.some(image => image.id === scene.currentImageVersionId && image.sceneId === scene.id && image.status === "ready" && available(image))).length;
    const missingVoice = scenes.filter(scene => { if (hasVideo(scene)) return false; const needed = lines.filter(line => line.sceneId === scene.id); return (scene.narration?.trim() || needed.length) && (!needed.length || needed.some(line => line.status !== "generated" || !line.cloudAudio?.url || line.cloudAudio?.account !== account || !account)); }).length;
    const hasImages = hasScenes && missingImages === 0, hasVoice = hasScenes && missingVoice === 0;
    const foreignVisual = scenes.some(scene => [...images, ...clips].some(item => item.sceneId === scene.id && [scene.currentImageVersionId, scene.currentVideoClipId].includes(item.id) && item.account && item.account !== account));
    const hasExport = (project.exports || []).some(record => !record.stale && ["complete", "completed"].includes(record?.status) && available(record));
    const query = `?project=${encodeURIComponent(project.id)}`;
    const next = project.scriptScenesOutOfSync ? { label: "核对脚本与镜头", href: "#script", copy: "脚本与镜头旁白不同步，请先确认采用哪份内容，再更新素材。" }
      : hasExport ? { label: "查看与下载成片", href: `/video-editor/${query}`, copy: "已有完成的导出结果，可下载成片，或继续调整作品。" }
      : !hasScript ? { label: "完善文案", href: "#script", copy: "先写下作品文案，再编排镜头。" }
      : !hasScenes ? { label: "编排镜头", href: "#scenes", copy: "将文案拆为镜头，为每个画面安排旁白。" }
      : !hasImages ? { label: "完善画面", href: `/visual-editor/${query}`, copy: `还有 ${missingImages} 个镜头缺少可用画面。${foreignVisual ? "部分素材属于其他账号，请登录原账号后使用。" : ""}` }
      : !hasVoice ? { label: "生成配音", href: `/voice-editor/${query}`, copy: `还有 ${missingVoice} 个镜头的旁白未完成配音。` }
      : { label: "合成与导出", href: `/video-editor/${query}`, copy: "画面和配音已就绪，可检查字幕与配乐并合成成片。" };
    return { hasScript, hasScenes, hasSubtitles, hasImages, hasVoice, hasExport, next };
  }
  function updateOverview() {
    let account = ""; try { account = JSON.parse(sessionStorage.getItem("ocvg-cloud-session") || "null")?.user?.id || ""; } catch (_) {}
    const { hasScript, hasScenes, hasSubtitles, hasImages, hasVoice, hasExport, next } = readiness(project, account);
    const completed = [hasScript, hasScenes, hasSubtitles, hasImages, hasVoice, hasExport].filter(Boolean).length;
    const percent = Math.round(completed / 6 * 100);
    document.querySelector("#progress-percent").textContent = `${percent}%`;
    document.querySelector("#progress-circle").style.strokeDashoffset = String(320.44 * (1 - percent / 100));
    const stage = hasExport ? "已有成片" : hasImages && hasVoice ? "可以合成" : "继续完善";
    document.querySelector("#project-stage").textContent = stage;
    document.querySelector("#progress-hint").textContent = next.copy;
    document.querySelector("#next-step-copy").textContent = next.copy;
    document.querySelector("#next-step-link").textContent = `${next.label} →`;
    document.querySelector("#next-step-link").href = next.href;
    document.querySelector("#snapshot-title").textContent = project.title;
    document.querySelector("#snapshot-initial").textContent = project.title.trim().slice(0, 1) || "项";
    document.querySelector("#snapshot-scenes").textContent = String(project.scenes.length);
    document.querySelector("#snapshot-subtitles").textContent = String(project.subtitles.length);
    document.querySelector("#snapshot-assets").textContent = String(mediaRecords(project.imageVersions).length + mediaRecords(project.videoClips).length + project.audio.length + project.voiceLines.length);
    document.querySelector("#snapshot-updated").textContent = formatUpdated(project.updatedAt);
    [["check-script",hasScript],["check-scenes",hasScenes],["check-subtitles",hasSubtitles],["check-images",hasImages],["check-voice",hasVoice],["check-export",hasExport]].forEach(([id,done]) => {
      const item = document.querySelector(`#${id}`); item.classList.toggle("done", done);
      item.querySelector("i").textContent = done ? "✓" : "○";
      item.querySelector("small").textContent = done ? "已完成" : "待完成";
    });
  }

  function formatUpdated(value) {
    const date = new Date(value);
    const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
    if (!Number.isFinite(minutes) || minutes < 1) return "刚刚更新";
    if (minutes < 60) return `${minutes} 分钟前更新`;
    if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前更新`;
    return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(date) + " 更新";
  }

  function updateLinks() {
    const query = `?project=${encodeURIComponent(project.id)}`;
    document.querySelector("#subtitles-link").href = `/subtitles/${query}`;
    document.querySelector("#voice-link").href = `/voice-editor/${query}`;
    document.querySelector("#visual-link").href = `/visual-editor/${query}`;
    document.querySelector("#video-link").href = `/video-editor/${query}`;
  }

  function showToast(message) {
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.add("show");
    toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2300);
  }

  function openSceneDialog() {
    sceneForm.reset();
    sceneDialog.showModal();
    window.setTimeout(() => document.querySelector("#scene-title").focus(), 30);
  }

  function createScenesFromScript() {
    const paragraphs = project.script.split(/\n\s*\n|\n/).map((part) => part.trim()).filter(Boolean);
    if (!paragraphs.length) {
      showToast("请先写一段脚本，再生成镜头");
      scriptInput.focus();
      return;
    }
    if (project.scenes.length && !window.confirm("这会按当前脚本重建全部镜头，并重置逐句配音和字幕。手工镜头修改不会保留，原成片仍作为历史记录。确认更新？")) return;
    if (paragraphs.length > 16) { showToast("当前脚本超过 16 段，请先合并段落再更新镜头。"); return; }
    project.scenes = paragraphs.map((paragraph, index) => normalizeScene({ id: makeId("scene"), title: `镜头 ${String(index + 1).padStart(2, "0")}`, narration: paragraph }, index));
    project.voiceLines = []; project.audio = []; project.subtitles = [];
    invalidateSceneMedia(project, project.scenes.map(scene => scene.id), true);
    project.scriptScenesOutOfSync = false;
    renderScenes();
    updateScriptSync();
    if (!persistNow()) return;
    showToast(`已从脚本生成 ${project.scenes.length} 个镜头`);
    document.querySelector("#scenes").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function downloadProject() {
    persistNow();
    const safeName = project.title.replace(/[\\/:*?"<>|]/g, "-").slice(0, 60) || "oneclick-project";
    const blob = new Blob([JSON.stringify(project, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${safeName}.ocvg.json`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    showToast("项目备份已导出");
  }

  titleInput.value = project.title;
  scriptInput.value = project.script;
  document.title = `${project.title}｜脚本与分镜`;
  updateLinks();
  updateScriptStats();
  updateScriptSync();
  renderScenes();
  updateOverview();

  titleInput.addEventListener("input", () => {
    project.title = titleInput.value.trimStart() || "未命名项目";
    document.title = `${project.title}｜脚本与分镜`;
    scheduleSave();
  });
  titleInput.addEventListener("blur", () => { titleInput.value = project.title.trim() || "未命名项目"; project.title = titleInput.value; persistNow(); });
  scriptInput.addEventListener("input", () => { project.script = scriptInput.value; invalidateSceneMedia(project, project.scenes.map(scene => scene.id)); project.scriptScenesOutOfSync = scriptsDiffer(project); updateScriptStats(); updateScriptSync(); renderScenes(); updateOverview(); scheduleSave(); });

  sceneList.addEventListener("input", (event) => {
    const field = event.target.dataset.field;
    const card = event.target.closest("[data-scene-id]");
    if (!field || !card) return;
    const scene = project.scenes.find((item) => item.id === card.dataset.sceneId);
    if (!scene) return;
    if (scene[field] === event.target.value) return;
    scene[field] = event.target.value;
    if (field === "narration") { noteNarrationChange(scene); const note = card.querySelector(".scene-stale-note"); if (note) note.hidden = false; }
    scheduleSave();
  });

  sceneList.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    const card = button?.closest("[data-scene-id]");
    if (!button || !card) return;
    const index = project.scenes.findIndex((item) => item.id === card.dataset.sceneId);
    if (index < 0) return;
    if (button.dataset.action === "remove") {
      if (!window.confirm("删除这个镜头及关联的配音、字幕？原成片仍会保留。")) return;
      const removed = project.scenes.splice(index, 1)[0];
      for (const field of ["voiceLines", "audio", "subtitles"]) project[field] = project[field].filter(item => item.sceneId !== removed.id);
    }
    if (button.dataset.action === "up" && index > 0) [project.scenes[index - 1], project.scenes[index]] = [project.scenes[index], project.scenes[index - 1]];
    if (button.dataset.action === "down" && index < project.scenes.length - 1) [project.scenes[index + 1], project.scenes[index]] = [project.scenes[index], project.scenes[index + 1]];
    project.scenes.forEach((scene, sceneIndex) => { scene.order = sceneIndex; });
    project.scriptScenesOutOfSync = scriptsDiffer(project);
    project.exports = project.exports.map(record => ({ ...record, stale: true }));
    renderScenes();
    persistNow();
  });

  sceneForm.addEventListener("submit", (event) => {
    event.preventDefault();
    project.scenes.push(normalizeScene({ id: makeId("scene"), title: document.querySelector("#scene-title").value.trim(), narration: document.querySelector("#scene-narration").value.trim(), visualPrompt: document.querySelector("#scene-visual").value.trim() }, project.scenes.length));
    invalidateSceneMedia(project, [project.scenes[project.scenes.length - 1].id], true);
    project.scriptScenesOutOfSync = scriptsDiffer(project);
    sceneDialog.close();
    renderScenes();
    persistNow();
    showToast("镜头已添加");
  });

  document.querySelector("#add-scene").addEventListener("click", openSceneDialog);
  document.querySelector("#empty-add-scene").addEventListener("click", openSceneDialog);
  document.querySelector("#scene-dialog-close").addEventListener("click", () => sceneDialog.close());
  document.querySelector("#cancel-scene").addEventListener("click", () => sceneDialog.close());
  document.querySelector("#script-to-scenes").addEventListener("click", createScenesFromScript);
  document.querySelector("#scenes-to-script").addEventListener("click", () => {
    if (!window.confirm("以现有镜头旁白替换上方脚本？镜头与手工编排会保留。")) return;
    project.script = project.scenes.map(scene => scene.narration).join("\n\n");
    scriptInput.value = project.script; project.scriptScenesOutOfSync = false;
    updateScriptStats(); updateScriptSync(); persistNow();
  });
  document.querySelector("#empty-generate-scenes").addEventListener("click", createScenesFromScript);
  document.querySelector("#export-current").addEventListener("click", downloadProject);
  sceneDialog.addEventListener("click", (event) => { if (event.target === sceneDialog) sceneDialog.close(); });

  const observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    document.querySelectorAll(".project-nav a").forEach((link) => link.classList.toggle("active", link.getAttribute("href") === `#${visible.target.id}`));
  }, { rootMargin: "-20% 0px -65%", threshold: [0, .15, .5] });
  document.querySelectorAll("#overview,#script,#scenes,#production").forEach((section) => observer.observe(section));
  document.addEventListener("ocvg:flush-project", (event) => { if (!persistNow()) event.preventDefault(); });
  document.addEventListener("click", event => { if (event.target.closest("a[href]") && !persistNow()) event.preventDefault(); }, true);
  window.addEventListener("beforeunload", event => { if (!persistNow()) { event.preventDefault(); event.returnValue = ""; } });
})();
