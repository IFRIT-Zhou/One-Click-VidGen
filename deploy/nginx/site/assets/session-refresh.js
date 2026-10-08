(function (root) {
  "use strict";
  const key = "ocvg-cloud-session", clients = new WeakMap();
  const owner = value => String(value?.user?.id || value?.user?.user_id || value?.user?.email || "");
  function create(env = root) {
    if (clients.has(env.sessionStorage)) return clients.get(env.sessionStorage);
    const pending = new Map();
    function read() { try { return JSON.parse(env.sessionStorage.getItem(key)) || null; } catch (_) { return null; } }
    function current(initial) {
      const value = read();
      if (!initial?.access_token || !owner(initial) || !value?.access_token || owner(value) !== owner(initial)) throw new Error("账户或登录状态已改变，请重新操作。");
      return value;
    }
    async function refresh(initial) {
      const value = current(initial), account = owner(value);
      if (value.access_token !== initial.access_token || value.refresh_token !== initial.refresh_token) return value;
      if (pending.has(account)) return pending.get(account);
      if (!value.refresh_token) throw new Error("登录已过期，请重新登录。");
      const operation = (async () => {
        const response = await env.fetch("/api/v1/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: value.refresh_token }) });
        let latest = current(initial);
        if (latest.access_token !== value.access_token || latest.refresh_token !== value.refresh_token) return latest;
        if (!response.ok) throw new Error("登录已过期，请重新登录。");
        const data = await response.json(), fresh = data?.data && data.code !== undefined ? data.data : data;
        latest = current(initial);
        if (latest.access_token !== value.access_token || latest.refresh_token !== value.refresh_token) return latest;
        if (!fresh?.access_token || !fresh.refresh_token || owner(fresh) !== account) throw new Error("刷新登录返回的账户无效，请重新登录。");
        env.sessionStorage.setItem(key, JSON.stringify(fresh));
        return fresh;
      })();
      pending.set(account, operation);
      try { return await operation; } finally { if (pending.get(account) === operation) pending.delete(account); }
    }
    const client = { read, current, refresh };
    clients.set(env.sessionStorage, client);
    return client;
  }
  root.OCVGSessionRefresh = { create, owner };
  if (root.sessionStorage && root.fetch) root.OCVGSessionRefresh.session = create(root);
})(globalThis);
