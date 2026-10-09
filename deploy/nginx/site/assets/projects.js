(() => {
  "use strict";

  const STORAGE_KEY = "ocvg.projects.v1";
  const grid = document.querySelector("#project-grid");
  const empty = document.querySelector("#empty-projects");
  const summary = document.querySelector("#project-summary");
  const search = document.querySelector("#project-search");
  const sort = document.querySelector("#project-sort");
  const projectDialog = document.querySelector("#project-dialog");
  const projectForm = document.querySelector("#project-form");
  const deleteDialog = document.querySelector("#delete-dialog");
  const fileInput = document.querySelector("#project-file");
  const toast = document.querySelector("#project-toast");
  let projects = loadProjects();
  let knownIds = new Set(projects.map((project) => project.id));
  let pendingDeleteId = "";
  let toastTimer = 0;

  function makeId(prefix = "project") {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return `${prefix}-${window.crypto.randomUUID()}`;
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
  }

  function asArray(value) { return Array.isArray(value) ? value : []; }

  function normalizeScene(scene, index) {
    const source = scene && typeof scene === "object" ? scene : {};
    return {
      ...source,
      id: String(source.id || makeId("scene")),
      title: String(source.title || `镜头 ${String(index + 1).padStart(2, "0")}`),
      narration: String(source.narration || source.text || ""),
      visualPrompt: String(source.visualPrompt || source.prompt || ""),
      duration: Number(source.duration) || 0,
      order: index,
    };
  }

  function normalizeProject(project, index = 0) {
    const source = project && typeof project === "object" ? project : {};
    const now = new Date().toISOString();
    return {
      ...source,
      id: String(source.id || makeId("project")),
      title: String(source.title || `未命名项目 ${index + 1}`),
      script: String(source.script || ""),
      createdAt: String(source.createdAt || now),
      updatedAt: String(source.updatedAt || source.createdAt || now),
      scenes: asArray(source.scenes).map(normalizeScene),
      characters: asArray(source.characters),
      subtitles: asArray(source.subtitles),
      audio: asArray(source.audio),
      voiceLines: asArray(source.voiceLines),
      imageVersions: source.imageVersions && typeof source.imageVersions === "object" ? source.imageVersions : [],
      videoClips: source.videoClips && typeof source.videoClips === "object" ? source.videoClips : [],
      bgm: asArray(source.bgm),
      exports: asArray(source.exports),
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

  function saveProjects() {
    const currentIds = new Set(projects.map((project) => project.id));
    const deletedIds = new Set([...knownIds].filter((id) => !currentIds.has(id)));
    const additions = projects.filter((project) => !knownIds.has(project.id));
    projects = [...additions, ...loadProjects().filter((project) => !deletedIds.has(project.id) && !additions.some((added) => added.id === project.id))];
    localStorage.setItem(STORAGE_KEY, JSON.stringify(projects));
    knownIds = new Set(projects.map((project) => project.id));
  }

  function createProject(title, script = "") {
    const now = new Date().toISOString();
    return normalizeProject({ id: makeId("project"), title: title.trim() || "未命名项目", script: script.trim(), createdAt: now, updatedAt: now });
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
  }

  function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "刚刚更新";
    const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
    if (seconds < 60) return "刚刚更新";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)} 天前`;
    return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(date);
  }

  function scriptPreview(project) {
    return project.script.replace(/\s+/g, " ").trim() || "还没有脚本，打开工作台开始记录你的创意。";
  }

  function render() {
    const query = search.value.trim().toLocaleLowerCase("zh-CN");
    const order = sort.value;
    const visible = projects.filter((project) => `${project.title} ${project.script}`.toLocaleLowerCase("zh-CN").includes(query));
    visible.sort((a, b) => {
      if (order === "title") return a.title.localeCompare(b.title, "zh-CN");
      const field = order === "created" ? "createdAt" : "updatedAt";
      return new Date(b[field]).getTime() - new Date(a[field]).getTime();
    });

    summary.textContent = projects.length ? `共 ${projects.length} 个本机作品 · 绑定账号后自动同步` : "你的项目只保存在当前浏览器中。";
    empty.hidden = projects.length > 0 || Boolean(query);
    grid.hidden = visible.length === 0;
    if (!visible.length) {
      grid.innerHTML = "";
      if (query && projects.length) grid.innerHTML = `<div class="empty-projects" style="display:block;grid-column:1/-1"><h2>没有找到匹配项目</h2><p>换个关键词试试，项目仍安全保存在当前浏览器。</p></div>`;
      return;
    }

    grid.innerHTML = visible.map((project) => {
      const initial = escapeHtml(project.title.trim().slice(0, 1) || "项");
      return `<article class="project-item" data-project-id="${escapeHtml(project.id)}">
        <div class="project-cover"><div class="project-cover-top"><span>PROJECT / ${escapeHtml(project.id.slice(-6).toUpperCase())}</span><b>${project.exports.some(item => ["complete", "completed"].includes(item.status) && (item.url || item.download_url)) ? "已有成片" : "创作中"}</b></div><div class="project-cover-mark">${initial}</div></div>
        <div class="project-card-body"><div class="project-card-head"><div><h3 title="${escapeHtml(project.title)}">${escapeHtml(project.title)}</h3><p>${escapeHtml(scriptPreview(project))}</p></div><div class="project-menu"><button type="button" data-action="duplicate" title="复制项目" aria-label="复制项目">⧉</button><button type="button" data-action="export" title="下载项目备份 JSON" aria-label="下载项目备份 JSON">↓</button><button class="delete" type="button" data-action="delete" title="删除项目" aria-label="删除项目">×</button></div></div><div class="project-meta"><span>${formatDate(project.updatedAt)}</span><span>${project.scenes.length} 镜头</span><a class="open-project" href="/workspace/?project=${encodeURIComponent(project.id)}">继续编辑 <b>→</b></a></div><button class="project-cloud-save" type="button" data-action="cloud-save">同步到账号</button><details class="project-more"><summary>更多操作</summary><button class="project-cloud-save" type="button" data-action="cloud-new">另存云端副本</button></details></div>
      </article>`;
    }).join("");
  }

  function openCreateDialog() {
    projectForm.reset();
    projectDialog.showModal();
    window.setTimeout(() => document.querySelector("#project-title").focus(), 30);
  }

  function closeCreateDialog() { projectDialog.close(); }

  function showToast(message) {
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.add("show");
    toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2600);
  }

  function downloadJson(project) {
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
    showToast("项目备份 JSON 已下载");
  }

  function duplicateProject(project) {
    const copy = normalizeProject(structuredCloneSafe(project));
    const now = new Date().toISOString();
    copy.id = makeId("project");
    copy.title = `${project.title}（副本）`;
    copy.createdAt = now;
    copy.updatedAt = now;
    // Scene IDs are project scoped; retain them so subtitle and media references stay valid.
    copy.scenes = copy.scenes.map((scene, index) => ({ ...scene, order: index }));
    projects.unshift(copy);
    saveProjects();
    render();
    showToast("已创建项目副本");
  }

  function structuredCloneSafe(value) {
    return typeof structuredClone === "function" ? structuredClone(value) : JSON.parse(JSON.stringify(value));
  }

  function importProjects(file) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result || ""));
        const incoming = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.projects) ? parsed.projects : [parsed];
        if (!incoming.length || incoming.some((item) => !item || typeof item !== "object")) throw new Error("invalid project");
        const currentIds = new Set(projects.map((project) => project.id));
        const normalized = incoming.map(normalizeProject).map((project) => {
          // Imported documents are always independent local copies with no cloud binding.
          project.id = makeId("project");
          currentIds.add(project.id);
          project.updatedAt = new Date().toISOString();
          return project;
        });
        projects = [...normalized, ...projects];
        saveProjects();
        render();
        showToast(`已导入 ${normalized.length} 个项目`);
      } catch (error) {
        console.warn("项目导入失败", error);
        showToast("无法导入：请选择有效的项目 JSON 文件");
      } finally {
        fileInput.value = "";
      }
    };
    reader.readAsText(file, "utf-8");
  }



  document.querySelector("#project-dialog-close").addEventListener("click", closeCreateDialog);
  document.querySelector("#cancel-project").addEventListener("click", closeCreateDialog);
  document.querySelector("#import-project").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => { if (fileInput.files?.[0]) importProjects(fileInput.files[0]); });
  search.addEventListener("input", render);
  sort.addEventListener("change", render);

  projectForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const title = document.querySelector("#project-title").value;
    const script = document.querySelector("#project-script").value;
    const project = createProject(title, script);
    projects.unshift(project);
    saveProjects();
    closeCreateDialog();
    window.location.assign(`/workspace/?project=${encodeURIComponent(project.id)}`);
  });

  grid.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const card = button.closest("[data-project-id]");
    const project = projects.find((item) => item.id === card?.dataset.projectId);
    if (!project) return;
    const action = button.dataset.action;
    if (action === "cloud-save" || action === "cloud-new") {
      window.OCVGCloudProjects.saveProject(project.id, action === "cloud-new");
      document.querySelector("#cloud-project-message").scrollIntoView({ block: "nearest" });
    }
    if (action === "duplicate") duplicateProject(project);
    if (action === "export") downloadJson(project);
    if (action === "delete") {
      pendingDeleteId = project.id;
      document.querySelector("#delete-copy").textContent = `“${project.title}”及其镜头、字幕和素材记录将从当前浏览器移除。`;
      deleteDialog.showModal();
    }
  });

  document.querySelector("#cancel-delete").addEventListener("click", () => { pendingDeleteId = ""; deleteDialog.close(); });
  document.querySelector("#confirm-delete").addEventListener("click", () => {
    if (!pendingDeleteId) return;
    projects = projects.filter((project) => project.id !== pendingDeleteId);
    pendingDeleteId = "";
    saveProjects();
    deleteDialog.close();
    render();
    showToast("项目已删除");
  });

  [projectDialog, deleteDialog].forEach((dialog) => dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  }));

  render();
})();
