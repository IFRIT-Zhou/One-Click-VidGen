(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const auth = globalThis.OCVGSessionRefresh?.session;
  const initial = auth?.read();
  const labels = {draft: "草稿", published: "已发布", archived: "已归档"};
  let items = [], total = 0, selected = null, busy = false, loading = false, dirty = false;
  let clientId = crypto.randomUUID();
  const unwrap = value => value?.data && value.code !== undefined ? value.data : value;
  const date = value => value ? new Date(value).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai"}) : "";
  function message(text, error = false) {
    $("announcement-admin-message").textContent = text;
    $("announcement-admin-message").className = `announcement-message${error ? " error" : ""}`;
  }
  if (!initial?.access_token || initial.user?.role !== "admin") { $("announcement-admin-login").hidden = false; return; }
  $("announcement-admin-content").hidden = false;
  async function api(path, options = {}) {
    let session = auth.current(initial);
    for (let attempt = 0; attempt < 2; attempt++) {
      const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 20000);
      let response;
      try {
        response = await fetch(`/api/v1/admin/announcements${path}`, {...options, headers: {Accept: "application/json", "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}`}, signal: controller.signal, cache: "no-store"});
      } catch (error) { throw new Error(error.name === "AbortError" ? "请求超时。若正在保存，请保留当前内容并重试，避免重复新建。" : "网络连接失败，请保留当前内容后重试。"); }
      finally { clearTimeout(timer); }
      auth.current(initial);
      if (response.status === 401 && attempt === 0) { session = await auth.refresh(session); continue; }
      let data = null;
      try { data = await response.json(); } catch (_) { /* Handle invalid gateway response below. */ }
      auth.current(initial);
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) $("announcement-admin-login").hidden = false;
        const detail = data?.detail?.message || data?.detail || data?.message;
        const error = new Error(response.status === 409 ? "公告已被其他管理员更新，或同一请求内容发生变化。当前输入已保留；请先复制需要保留的内容，再刷新列表并选中对应公告，以最新版本编辑。" : typeof detail === "string" ? detail : `请求失败（HTTP ${response.status}）`);
        error.status = response.status; throw error;
      }
      return unwrap(data);
    }
    throw new Error("登录已过期，请返回后台重新登录。");
  }
  function controls() {
    document.querySelectorAll("#announcement-admin-content button, #announcement-admin-content input, #announcement-admin-content textarea, #announcement-filter").forEach(button => { button.disabled = busy || loading; });
    $("announcement-archive").disabled = busy || loading || !selected || selected.status === "archived";
    $("announcement-admin-more").hidden = items.length >= total;
  }
  function preview() {
    $("announcement-preview-title").textContent = $("announcement-title").value || "公告标题";
    $("announcement-preview-body").textContent = $("announcement-body").value || "正文预览将在这里显示。";
  }
  function select(item) {
    if (busy || loading || (dirty && !confirm("当前编辑尚未保存，是否放弃这些修改？"))) return;
    selected = item ? {...item} : null; dirty = false; clientId = crypto.randomUUID();
    $("announcement-title").value = item?.title || "";
    $("announcement-body").value = item?.body || "";
    $("announcement-pinned").checked = !!item?.pinned;
    $("announcement-editor-heading").textContent = item ? "编辑公告" : "新建公告";
    $("announcement-editor-status").textContent = item ? `${labels[item.status] || item.status} · 版本 ${item.revision} · ${date(item.updated_at)}` : "草稿尚未保存";
    $("announcement-save").textContent = item?.status === "published" ? "转为草稿（撤下）" : "保存草稿";
    $("announcement-publish").textContent = item?.status === "published" ? "更新已发布公告" : "发布公告";
    message(""); preview(); render(); controls();
  }
  function render() {
    const list = $("announcement-admin-list"); list.replaceChildren();
    for (const item of items) {
      const button = document.createElement("button");
      button.type = "button"; button.className = "announcement-edit-item";
      button.setAttribute("aria-pressed", String(selected?.id === item.id));
      const title = document.createElement("strong"), meta = document.createElement("small");
      title.textContent = item.title;
      meta.textContent = `${labels[item.status] || item.status}${item.pinned ? " · 置顶" : ""} · ${date(item.updated_at)}`;
      button.append(title, meta); button.addEventListener("click", () => select(item)); list.append(button);
    }
    if (!items.length) list.textContent = "暂无公告。";
    $("announcement-count").textContent = `已显示 ${items.length} / ${total} 条`;
  }
  async function load(append = false) {
    if (loading) return false;
    loading = true; controls();
    try {
      const status = $("announcement-filter").value;
      const data = await api(`?limit=100&offset=${append ? items.length : 0}${status ? `&status=${encodeURIComponent(status)}` : ""}`);
      if (!Array.isArray(data?.items)) throw new Error("公告列表返回异常，请重试。");
      items = append ? [...items, ...data.items] : data.items; total = Number(data.total || 0);
      render(); return true;
    } catch (error) { message(error.message, true); return false; }
    finally { loading = false; controls(); }
  }
  async function save(status) {
    if (busy || loading || !$("announcement-form").reportValidity()) return;
    const title = $("announcement-title").value.trim(), body = $("announcement-body").value.trim();
    if (!title || !body) { message("标题和正文不能为空。", true); return; }
    if (status === "published" && !confirm("发布后所有用户都能看到此公告，确认发布当前内容？")) return;
    if (selected?.status === "published" && status !== "published" && !confirm("此操作将撤下公开公告，确认继续？")) return;
    const payload = {title, body, pinned: $("announcement-pinned").checked, status};
    if (selected) payload.revision = selected.revision; else payload.client_id = clientId;
    busy = true; controls(); message("正在保存…");
    try {
      const data = await api(selected ? `/${encodeURIComponent(selected.id)}` : "", {method: selected ? "PUT" : "POST", body: JSON.stringify(payload)});
      const saved = data?.item || data;
      if (!saved?.id || !Number.isFinite(Number(saved.revision))) throw new Error("保存返回异常，请保留当前内容并刷新列表核对结果。");
      selected = {...saved}; dirty = false;
      const loaded = await load();
      busy = false;
      select(saved);
      message(loaded ? `公告已${status === "published" ? "发布" : status === "archived" ? "归档" : "保存为草稿"}。` : "公告已保存，但列表刷新失败，请重新刷新列表。", !loaded);
    } catch (error) { message(error.message, true); }
    finally { busy = false; controls(); }
  }
  $("announcement-form").addEventListener("submit", event => event.preventDefault());
  $("announcement-form").addEventListener("input", () => { dirty = true; preview(); });
  $("announcement-new").addEventListener("click", () => select(null));
  $("announcement-refresh").addEventListener("click", () => load());
  $("announcement-filter").addEventListener("change", () => load());
  $("announcement-admin-more").addEventListener("click", () => load(true));
  $("announcement-save").addEventListener("click", () => save("draft"));
  $("announcement-publish").addEventListener("click", () => save("published"));
  $("announcement-archive").addEventListener("click", () => save("archived"));
  window.addEventListener("beforeunload", event => { if (dirty || busy) { event.preventDefault(); event.returnValue = ""; } });
  load();
})();
