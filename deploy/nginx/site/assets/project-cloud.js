(function (root) {
  "use strict";
  const PROJECTS = "ocvg.projects.v1";
  const SESSION = "ocvg-cloud-session";
  const BINDINGS = "ocvg.cloud-project-bindings.v1:";
  const parse = (value, fallback) => { try { return JSON.parse(value) ?? fallback; } catch (_) { return fallback; } };
  const owner = (session) => String(session?.user?.id || session?.user?.user_id || session?.user?.email || "");
  const unwrap = (value) => value?.data && value.code !== undefined ? value.data : value;
  function cleanDocument(value) {
    // Browser object URLs never identify a durable, remotely available asset.
    if (typeof value === "string" && /^(blob:|file:)/i.test(value)) return "";
    if (Array.isArray(value)) return value.map(cleanDocument);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).filter(([key]) => !["cloudId", "cloudRevision", "cloudAccount", "cloudBinding"].includes(key)).map(([key, item]) => [key, cleanDocument(item)]));
    return value;
  }
  function signature(project) {
    const document = cleanDocument(project);
    delete document.updatedAt;
    const value = JSON.stringify(document);
    let a = 2166136261, b = 5381;
    for (let i = 0; i < value.length; i++) { a = Math.imul(a ^ value.charCodeAt(i), 16777619); b = Math.imul(b, 33) ^ value.charCodeAt(i); }
    return `${value.length}:${a >>> 0}:${b >>> 0}`;
  }
  function createClient(env) {
    const busy = new Set();
    const auth = env.sessionRefresh || root.OCVGSessionRefresh?.create(env);
    function session() { return parse(env.sessionStorage.getItem(SESSION), null); }
    function requireSession() {
      const value = session();
      if (!value?.access_token || !owner(value)) throw new Error("请先在云端工作台登录账户，再返回保存项目。");
      return value;
    }
    function unchanged(initial) {
      const current = session();
      if (!current?.access_token || owner(current) !== owner(initial)) throw new Error("账户或登录状态已改变，请重新操作。");
      return current;
    }
    function bindings(account) { return parse(env.localStorage.getItem(BINDINGS + encodeURIComponent(account)), {}); }
    function bind(account, id, detail, savedSignature) {
      const map = bindings(account);
      map[id] = { id: String(detail.id), revision: detail.revision, signature: savedSignature };
      env.localStorage.setItem(BINDINGS + encodeURIComponent(account), JSON.stringify(map));
    }
    async function request(path, options, initial, retry = true) {
      const sent = unchanged(initial);
      const response = await env.fetch("/api/v1" + path, { ...options, headers: { Accept: "application/json", "Content-Type": "application/json", Authorization: `Bearer ${sent.access_token}` } });
      unchanged(initial);
      if (response.status === 401 && retry && (sent.refresh_token || unchanged(initial).access_token !== sent.access_token)) {
        if (auth) { await auth.refresh(sent); return request(path, options, initial, false); }
        if (unchanged(initial).access_token !== sent.access_token) return request(path, options, initial, false);
        const refreshed = await env.fetch("/api/v1/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: sent.refresh_token }) });
        unchanged(initial);
        if (refreshed.ok) {
          const next = unwrap(await refreshed.json());
          unchanged(initial);
          if (next.access_token && owner(next) === owner(initial)) {
            env.sessionStorage.setItem(SESSION, JSON.stringify(next));
            Object.assign(initial, next);
            return request(path, options, initial, false);
          }
        }
      }
      const data = response.status === 204 ? null : await response.json().catch(() => null);
      unchanged(initial);
      if (!response.ok) {
        const message = response.status === 409 ? "云端已有更新，本地修改仍保留。请打开云端版本作为新副本进行比较，或另存为新的云端项目。" : response.status === 401 ? "登录已过期，请到云端工作台重新登录。" : response.status === 404 ? "云端项目已不存在，可另存为新的云端项目。" : response.status === 413 ? "项目超过云端大小限制，请减少内嵌素材后重试。" : `云端操作失败（${response.status}），本地项目仍保留。`;
        const error = new Error(message); error.status = response.status; throw error;
      }
      return unwrap(data);
    }
    function localProjects() {
      const value = parse(env.localStorage.getItem(PROJECTS), []);
      return Array.isArray(value) ? value : Array.isArray(value.projects) ? value.projects : Object.values(value);
    }
    async function save(localId, asNew = false) {
      if (busy.has(localId)) throw new Error("该项目正在保存，请稍候。");
      const initial = requireSession();
      const project = localProjects().find((item) => item.id === localId);
      if (!project) throw new Error("未找到本地项目，请先保存本地修改。");
      busy.add(localId);
      try {
        const binding = asNew ? null : bindings(owner(initial))[localId];
        const body = { title: String(project.title || "未命名项目"), document_json: cleanDocument(project) };
        if (binding) body.expected_revision = binding.revision;
        const detail = await request(binding ? `/projects/${encodeURIComponent(binding.id)}` : "/projects", { method: binding ? "PUT" : "POST", body: JSON.stringify(body) }, initial);
        if (!detail?.id || !Number.isInteger(detail.revision)) throw new Error("云端返回了无效的项目版本，请刷新云端列表核对。");
        bind(owner(initial), localId, detail, signature(project));
        return detail;
      } finally { busy.delete(localId); }
    }
    async function list(page = 1) {
      const initial = requireSession();
      return request(`/projects?page=${page}&page_size=20`, { method: "GET" }, initial);
    }
    async function openCopy(cloudId) {
      const initial = requireSession();
      const detail = await request(`/projects/${encodeURIComponent(cloudId)}`, { method: "GET" }, initial);
      if (!detail?.document_json || typeof detail.document_json !== "object" || Array.isArray(detail.document_json) || !Number.isInteger(detail.revision)) throw new Error("云端项目格式无效。");
      const project = { ...cleanDocument(detail.document_json), id: env.makeId(), title: detail.title, updatedAt: new Date().toISOString() };
      const projects = localProjects(); projects.unshift(project);
      env.localStorage.setItem(PROJECTS, JSON.stringify(projects));
      bind(owner(initial), project.id, detail, signature(project));
      return project;
    }
    async function remove(cloudId) {
      const initial = requireSession();
      await request(`/projects/${encodeURIComponent(cloudId)}`, { method: "DELETE" }, initial);
      const map = bindings(owner(initial));
      for (const id of Object.keys(map)) if (map[id].id === cloudId) delete map[id];
      env.localStorage.setItem(BINDINGS + encodeURIComponent(owner(initial)), JSON.stringify(map));
    }
    function syncState(id) {
      const account = owner(session()), project = localProjects().find(item => item.id === id);
      const binding = account && bindings(account)[id];
      return { account, exists: Boolean(project), eligible: Boolean(account && project && (binding || project.source?.account === account)), changed: Boolean(project && signature(project) !== binding?.signature) };
    }
    return { save, list, openCopy, remove, localProjects, session, syncState };
  }
  function createAutoSync(client, id, notify) {
    let busy = false, paused = false, account = client.syncState(id).account;
    async function sync(force = false, asNew = false) {
      if (busy) return false;
      const state = client.syncState(id);
      if (!state.exists) { notify("作品已移除，请返回我的作品。"); return false; }
      if (!force && state.account !== account) { paused = true; notify("账号已变化；当前修改保存在本机，点击同步可保存到当前账号。"); return false; }
      if (force) { paused = false; account = state.account; }
      if (paused) return false;
      if (!force && !state.eligible) { notify(state.account ? "已保存到本机 · 点击同步后自动保存到当前账号" : "已保存到本机 · 登录后可同步"); return false; }
      if (!force && !state.changed) { notify("已同步到账号"); return true; }
      busy = true; notify("正在同步…");
      try {
        await client.save(id, asNew);
        const latest = client.syncState(id);
        if (latest.changed && latest.account === account && latest.eligible) {
          busy = false;
          return await sync();
        }
        notify("已同步到账号"); return true;
      }
      catch (error) { paused = true; notify(error.status === 409 ? "云端有其他修改，已暂停同步；请在“更多”中查看云端副本。" : `${error.message} 修改仍保留在本机。`); return false; }
      finally { busy = false; }
    }
    return { sync };
  }
  root.OCVGCloudProjects = { createClient, cleanDocument, signature, createAutoSync };
  if (!root.document) return;
  const doc = root.document;
  for (const link of doc.querySelectorAll('a[href="/studio/"]')) {
    if (/登录|账号|账户/.test(link.textContent) && root.location.pathname !== "/studio/") link.href = `/studio/?return=${encodeURIComponent(root.location.pathname + root.location.search)}`;
  }
  const client = createClient({ localStorage: root.localStorage, sessionStorage: root.sessionStorage, fetch: root.fetch.bind(root), makeId: () => `project-${root.crypto.randomUUID()}` });
  root.OCVGCloudProjects.client = client;
  function message(text) { const node = doc.querySelector("#cloud-project-message"); if (node) node.textContent = text; }
  const pendingSaves = new Set();
  async function saveProject(id, asNew = false) {
    if (pendingSaves.has(id)) return;
    pendingSaves.add(id);
    const account = owner(client.session());
    const buttons = [...doc.querySelectorAll("[data-cloud-save], [data-cloud-new], [data-action='cloud-save'], [data-action='cloud-new']")];
    buttons.forEach((button) => { button.disabled = true; });
    message("正在保存到云端…");
    try {
      if (!doc.dispatchEvent(new CustomEvent("ocvg:flush-project", { cancelable: true }))) throw new Error("本地修改未能保存，已停止云端上传。请先处理编辑器中的保存提示。");
      // Let existing debounced local writes finish before taking the snapshot.
      await new Promise((resolve) => root.setTimeout(resolve, 520));
      if (owner(client.session()) !== account) throw new Error("账户已改变，请重新操作。");
      const detail = await client.save(id, asNew);
      message(`已保存到云端 · 版本 ${detail.revision}。本地副本保留；媒体文件未随项目上传。`);
    }
    catch (error) { message(error.message); }
    finally { pendingSaves.delete(id); buttons.forEach((button) => { button.disabled = false; }); }
  }
  root.OCVGCloudProjects.saveProject = saveProject;
  const page = doc.body.dataset.page;
  if (page === "projects") {
    let pageNumber = 1;
    const list = doc.querySelector("#cloud-project-list");
    const previous = doc.querySelector("#cloud-project-previous");
    const next = doc.querySelector("#cloud-project-next");
    let generation = 0;
    async function refresh() {
      const run = ++generation; list.replaceChildren(); previous.disabled = true; next.disabled = true;
      message("正在读取当前账户的云端项目…");
      try {
        const result = await client.list(pageNumber);
        if (run !== generation) return;
        for (const item of result.items || []) {
          const card = doc.createElement("article"); card.className = "cloud-project-card";
          const title = doc.createElement("strong"); title.textContent = item.title;
          const meta = doc.createElement("span"); meta.textContent = `版本 ${item.revision} · ${new Date(item.updated_at).toLocaleString("zh-CN")}`;
          const open = doc.createElement("button"); open.type = "button"; open.textContent = "打开编辑副本";
          open.addEventListener("click", async () => {
            open.disabled = true;
            try { const project = await client.openCopy(item.id); root.location.assign(`/workspace/?project=${encodeURIComponent(project.id)}`); }
            catch (error) { message(error.message); open.disabled = false; }
          });
          const remove = doc.createElement("button"); remove.type = "button"; remove.textContent = "删除云端版本";
          remove.addEventListener("click", async () => {
            if (!root.confirm(`删除云端项目“${item.title}”？此操作无法撤销，已有本地副本会保留。`)) return;
            remove.disabled = true;
            try { await client.remove(item.id); await refresh(); }
            catch (error) { message(error.message); remove.disabled = false; }
          });
          card.append(title, meta, open, remove); list.append(card);
        }
        previous.disabled = pageNumber <= 1;
        next.disabled = typeof result.total === "number" ? pageNumber * 20 >= result.total : (result.items || []).length < 20;
        message(`${client.session()?.user?.email || "当前账户"} · 第 ${pageNumber} 页 · ${(result.items || []).length} 个已同步作品`);
      } catch (error) { if (run === generation) message(error.message); }
    }
    doc.querySelector("#cloud-project-refresh").addEventListener("click", () => { pageNumber = 1; refresh(); });
    previous.addEventListener("click", () => { pageNumber--; refresh(); });
    next.addEventListener("click", () => { pageNumber++; refresh(); });
    if (client.session()?.access_token) refresh();
    else message("登录后在这里查看其他设备保存的作品。本机草稿仍可继续编辑。");
    root.addEventListener("focus", () => { if (client.session()?.access_token) refresh(); });
    doc.addEventListener("ocvg:account-changed", () => { generation++; list.replaceChildren(); pageNumber = 1; if (client.session()?.access_token) refresh(); else message("登录后查看已同步作品。本机草稿仍可继续编辑。"); });
  } else if (page === "studio") {
    const syncs = new Map(), timers = new Map();
    let currentId;
    const retry = doc.createElement("button"); retry.type = "button"; retry.className = "quiet-button"; retry.textContent = "重新同步"; retry.hidden = true;
    const cloudState = doc.querySelector("#studio-cloud-state"); cloudState?.after(retry);
    retry.addEventListener("click", () => { if (currentId && doc.dispatchEvent(new CustomEvent("ocvg:flush-project", { cancelable: true }))) syncs.get(currentId)?.sync(true); });
    function scheduleStudio(id) {
      if (!id) return;
      currentId = id;
      if (!syncs.has(id)) syncs.set(id, createAutoSync(client, id, text => {
        if (currentId !== id) return;
        const node = doc.querySelector("#studio-cloud-state"); if (node) node.textContent = text.replace("请在“更多”中查看云端副本。", "请到“我的作品”中查看云端副本。");
        retry.hidden = text === "已同步到账号" || text === "正在同步…" || !client.session()?.access_token;
      }));
      clearTimeout(timers.get(id)); timers.set(id, root.setTimeout(() => syncs.get(id).sync(), 1000));
    }
    doc.addEventListener("ocvg:studio-claimed", event => {
      const { id, account } = event.detail || {};
      const state = id && client.syncState(id);
      if (!state?.eligible || !account || state.account !== account) return;
      clearTimeout(timers.get(id)); syncs.delete(id); scheduleStudio(id);
    });
    doc.addEventListener("ocvg:project-saved", event => scheduleStudio(event.detail?.id));
    function resumeStudio() {
      const account = owner(client.session());
      const binding = parse(root.sessionStorage.getItem(`ocvg.studio-current.v1:${encodeURIComponent(account || "local")}`), null);
      if (binding && (binding.account || "") === account) scheduleStudio(binding.snapshot?.id);
    }
    resumeStudio(); root.setInterval(() => { if (!doc.hidden) resumeStudio(); }, 8000);
  } else if (new URLSearchParams(root.location.search).get("project")) {
    const id = new URLSearchParams(root.location.search).get("project");
    const bar = doc.createElement("aside"); bar.className = "cloud-project-bar";
    bar.innerHTML = '<div class="project-save-row"><span id="cloud-project-message" role="status" aria-live="polite">正在读取保存状态…</span><button type="button" data-cloud-save>同步到账号</button><details class="project-more"><summary>更多</summary><div><button type="button" data-cloud-new>另存副本到账号</button><a href="/projects/#cloud-projects">查看云端副本</a><a href="/studio/">登录 / 切换账号</a><p>已登录且绑定账号的作品会自动同步。媒体文件请另行备份。</p></div></details></div><nav class="project-step-nav" aria-label="作品编辑步骤"></nav>';
    (doc.querySelector("main") || doc.body).prepend(bar);
    for (const link of doc.querySelectorAll('a[href="/studio/"]')) {
      if (/登录|账号|账户/.test(link.textContent)) link.href = `/studio/?return=${encodeURIComponent(root.location.pathname + root.location.search)}`;
    }
    const navigation = bar.querySelector("nav");
    for (const [path, label] of [["workspace", "脚本与分镜"], ["voice-editor", "配音"], ["visual-editor", "画面"], ["subtitles", "字幕"], ["video-editor", "预览与导出"]]) {
      const link = doc.createElement("a"); link.href = `/${path}/?project=${encodeURIComponent(id)}`; link.textContent = label;
      if (root.location.pathname === `/${path}/`) link.setAttribute("aria-current", "page");
      navigation.append(link);
    }
    function flush() { return doc.dispatchEvent(new CustomEvent("ocvg:flush-project", { cancelable: true })); }
    doc.addEventListener("click", event => { if (event.target.closest("a[href]") && !flush()) { event.preventDefault(); message("当前修改未保存，请先处理页面上的提示再切换。"); } }, true);
    const auto = createAutoSync(client, id, text => {
      message(text);
      const button = bar.querySelector("[data-cloud-save]");
      button.textContent = !client.session()?.access_token ? "登录后同步" : text === "已同步到账号" ? "立即同步" : text === "正在同步…" ? "同步中…" : "同步到账号";
      button.disabled = text === "正在同步…";
    });
    let timer;
    function schedule() { clearTimeout(timer); timer = root.setTimeout(() => auto.sync(), 1000); }
    bar.querySelector("[data-cloud-save]").addEventListener("click", () => {
      if (!flush()) return;
      if (!client.session()?.access_token) { root.location.assign(`/studio/?return=${encodeURIComponent(root.location.pathname + root.location.search)}`); return; }
      auto.sync(true);
    });
    bar.querySelector("[data-cloud-new]").addEventListener("click", () => { if (flush()) auto.sync(true, true); });
    doc.addEventListener("ocvg:project-saved", event => { if (event.detail?.id === id) schedule(); });
    root.addEventListener("online", schedule);
    // Detect saves from older editor paths that do not emit an event yet.
    root.setInterval(() => { if (!doc.hidden) auto.sync(); }, 8000);
    auto.sync();
  }
})(typeof window !== "undefined" ? window : globalThis);
