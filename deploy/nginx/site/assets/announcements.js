(function () {
  "use strict";
  if (globalThis.OCVGAnnouncementsStarted) return;
  globalThis.OCVGAnnouncementsStarted = true;
  const list = document.getElementById("announcement-list");
  const nav = document.querySelector(".site-header .nav");
  if (nav && !nav.querySelector('a[href="/announcements/"]')) {
    const link = document.createElement("a");
    link.href = "/announcements/";
    link.textContent = "公告";
    if (list) { link.className = "active"; link.setAttribute("aria-current", "page"); }
    nav.append(link);
  }
  const unwrap = value => value?.data && value.code !== undefined ? value.data : value;
  const date = value => value ? new Date(value).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai"}) : "";
  function node(tag, className, text) {
    const value = document.createElement(tag);
    value.className = className;
    if (text !== undefined) value.textContent = text;
    return value;
  }
  async function fetchPage(offset, limit) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`/api/v1/announcements?limit=${limit}&offset=${offset}`, {headers: {Accept: "application/json"}, signal: controller.signal, cache: "no-store"});
      if (!response.ok) throw new Error("公告暂时无法加载，请稍后重试。");
      const data = unwrap(await response.json());
      if (!Array.isArray(data?.items)) throw new Error("公告暂时无法加载，请稍后重试。");
      return data;
    } finally { clearTimeout(timer); }
  }
  if (list) {
    let offset = 0, busy = false, hashLocated = false;
    const next = document.getElementById("announcement-more"), status = document.getElementById("announcement-status");
    async function load() {
      if (busy) return;
      busy = true; next.disabled = true; status.textContent = "正在读取公告…";
      try {
        const data = await fetchPage(offset, 20);
        for (const item of data.items) {
          const article = node("article", "announcement-card");
          article.id = `announcement-${item.id}`;
          article.append(node("div", "announcement-meta", `${item.pinned ? "置顶 · " : ""}${date(item.published_at || item.updated_at)}`), node("h2", "", item.title), node("div", "announcement-body", item.body));
          list.append(article);
        }
        if (!hashLocated && location.hash?.startsWith("#announcement-")) {
          let target;
          try { target = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch (_) { /* Ignore malformed fragments. */ }
          if (target) { target.scrollIntoView({block: "start"}); hashLocated = true; }
        }
        offset += data.items.length;
        status.textContent = offset ? `已显示 ${offset} 条公告` : "暂无公告。平台调整将在这里发布。";
        next.hidden = offset >= Number(data.total || 0) || data.items.length === 0;
        next.textContent = "加载更多";
      } catch (_) { status.textContent = "公告暂时无法加载，请稍后重试。"; next.hidden = false; next.textContent = "重新加载"; }
      finally { busy = false; next.disabled = false; }
    }
    next.addEventListener("click", load);
    load();
    return;
  }
  if (location.pathname.startsWith("/admin/")) return;
  fetchPage(0, 1).then(data => {
    const item = data.items[0];
    if (!item) return;
    const key = `ocvg-announcement-dismissed:${item.id}:${item.revision}`;
    try { if (localStorage.getItem(key) === "1") return; } catch (_) { /* Storage may be unavailable. */ }
    const banner = node("aside", "announcement-banner shell");
    banner.setAttribute("aria-label", "平台公告");
    const label = node("span", "announcement-badge", "公告");
    const link = node("a", "announcement-banner-link", item.title);
    link.href = `/announcements/#announcement-${encodeURIComponent(item.id)}`;
    const close = node("button", "announcement-dismiss", "×");
    close.type = "button"; close.setAttribute("aria-label", "关闭本条公告提示");
    close.addEventListener("click", () => { try { localStorage.setItem(key, "1"); } catch (_) { /* Dismiss for this page. */ } banner.remove(); });
    banner.append(label, link, close);
    document.querySelector(".site-header")?.insertAdjacentElement("afterend", banner);
  }).catch(() => { /* Announcements must not interrupt the creation workflow. */ });
})();
