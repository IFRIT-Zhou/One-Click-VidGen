(function (root) {
  "use strict";
  const key = "ocvg-cloud-session";
  const unwrap = value => value?.data && value.code !== undefined ? value.data : value;
  function createClient(env, auth) {
    async function summary() {
      const initial = auth.read(); if (!initial?.access_token) return null;
      async function request(retry) {
        const sent = auth.current(initial);
        const response = await env.fetch("/api/v1/account/summary", { cache: "no-store", headers: { Accept: "application/json", Authorization: `Bearer ${sent.access_token}` } });
        auth.current(initial);
        if (response.status === 401 && retry) { await auth.refresh(sent); return request(false); }
        if (!response.ok) throw new Error(response.status === 401 ? "登录已过期，请重新登录" : "积分暂时无法读取");
        const data = unwrap(await response.json()); auth.current(initial); return data;
      }
      return request(true);
    }
    async function login(email, password) {
      const before = env.sessionStorage.getItem(key);
      const response = await env.fetch("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ email, password }) });
      const value = unwrap(await response.json().catch(() => null));
      if (!response.ok) throw new Error(response.status === 401 ? "邮箱或密码不正确" : "登录失败，请稍后重试");
      if (!value?.access_token || !value.user) throw new Error("登录返回异常，请重试");
      if (env.sessionStorage.getItem(key) !== before) throw new Error("账户状态已变化，请重新操作");
      env.sessionStorage.setItem(key, JSON.stringify(value)); return value;
    }
    function logout() {
      const session = auth.read(); env.sessionStorage.removeItem(key);
      if (session?.refresh_token) env.fetch("/api/v1/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: session.refresh_token }) }).catch(() => {});
    }
    return { summary, login, logout };
  }
  root.OCVGAccountHeader = { createClient };
  if (!root.document) return;
  const doc = root.document, trigger = doc.querySelector("[data-account-trigger]");
  if (!trigger) return;
  const auth = root.OCVGSessionRefresh.session, client = createClient(root, auth);
  const wrapper = doc.createElement("div"); wrapper.className = "public-account"; trigger.before(wrapper); wrapper.append(trigger);
  const name = doc.createElement("span"), credits = doc.createElement("small"); trigger.replaceChildren(name, credits);
  trigger.setAttribute("aria-haspopup", "dialog"); trigger.setAttribute("aria-expanded", "false");
  const panel = doc.createElement("div"); panel.className = "account-popover"; panel.hidden = true; panel.id = "public-account-panel";
  trigger.setAttribute("aria-controls", panel.id);
  panel.innerHTML = '<strong data-account-email></strong><p data-account-balance></p><button type="button" data-account-refresh>刷新积分</button><a href="/recharge/">账户与积分</a><button type="button" data-account-login hidden>重新登录</button><button type="button" data-account-logout>退出登录</button>';
  wrapper.append(panel);
  const dialog = doc.createElement("dialog"); dialog.className = "auth-dialog public-login-dialog"; dialog.setAttribute("aria-labelledby", "public-login-title");
  dialog.innerHTML = '<div class="dialog-inner"><div class="dialog-head"><div><h2 id="public-login-title">登录账户</h2><p>登录后留在当前页面，查看账户与剩余积分。</p></div><button type="button" class="dialog-close" aria-label="关闭登录">×</button></div><form><div class="field"><label for="public-login-email">邮箱</label><input id="public-login-email" name="email" type="email" autocomplete="email" required /></div><div class="field"><label for="public-login-password">密码</label><input id="public-login-password" name="password" type="password" autocomplete="current-password" required /></div><p class="forgot-password-link"><a data-forgot-password href="/forgot-password/">忘记密码？</a></p><button class="button primary full" type="submit">登录</button><div class="message" role="status"></div></form><p class="public-register">还没有账户？<a href="/register/">注册账户</a></p></div>';
  dialog.querySelector("[data-forgot-password]").href = "/forgot-password/?return=" + encodeURIComponent(root.location.pathname);
  doc.body.append(dialog);
  let generation = 0;
  function closePanel() { panel.hidden = true; trigger.setAttribute("aria-expanded", "false"); }
  function openLogin() { closePanel(); if (root.OCVGPageAccount?.login) return root.OCVGPageAccount.login(); dialog.querySelector(".message").className = "message"; dialog.showModal(); }
  async function refresh() {
    const run = ++generation, session = auth.read(), loggedIn = Boolean(session?.access_token);
    name.textContent = loggedIn ? session.user?.email || "我的账户" : "登录账户";
    name.title = name.textContent; credits.hidden = !loggedIn; credits.textContent = "剩余积分 · 读取中";
    trigger.setAttribute("aria-label", loggedIn ? `${name.textContent}，查看账户信息` : "登录账户");
    panel.querySelector("[data-account-email]").textContent = name.textContent;
    panel.querySelector("[data-account-login]").hidden = true;
    if (!loggedIn) { closePanel(); return; }
    try {
      const summary = await client.summary(); if (run !== generation) return;
      const available = Number(summary?.credits?.available);
      const text = Number.isFinite(available) ? `剩余积分 · ${available.toLocaleString("zh-CN")}` : "积分暂时无法读取";
      credits.textContent = text; panel.querySelector("[data-account-balance]").textContent = text;
    } catch (error) {
      if (run !== generation) return;
      credits.textContent = "积分暂不可用"; panel.querySelector("[data-account-balance]").textContent = error.message;
      panel.querySelector("[data-account-login]").hidden = !/登录|账户/.test(error.message);
    }
  }
  trigger.addEventListener("click", () => { if (!auth.read()?.access_token) return openLogin(); panel.hidden = !panel.hidden; trigger.setAttribute("aria-expanded", String(!panel.hidden)); if (!panel.hidden) refresh(); });
  panel.querySelector("[data-account-refresh]").addEventListener("click", refresh);
  panel.querySelector("[data-account-login]").addEventListener("click", openLogin);
  panel.querySelector("[data-account-logout]").addEventListener("click", () => { if (root.OCVGPageAccount?.logout) root.OCVGPageAccount.logout(); else { client.logout(); doc.dispatchEvent(new CustomEvent("ocvg:account-changed")); } closePanel(); refresh(); });
  doc.addEventListener("click", event => { if (!wrapper.contains(event.target)) closePanel(); });
  doc.addEventListener("keydown", event => { if (event.key === "Escape" && !panel.hidden) { closePanel(); trigger.focus(); } });
  dialog.querySelector(".dialog-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
  dialog.querySelector("form").addEventListener("submit", async event => {
    event.preventDefault(); const form = event.currentTarget, button = form.querySelector('[type="submit"]'), message = form.querySelector(".message");
    button.disabled = true; button.textContent = "正在登录…"; message.className = "message";
    try { await client.login(form.elements.email.value.trim(), form.elements.password.value); form.elements.password.value = ""; dialog.close(); doc.dispatchEvent(new CustomEvent("ocvg:account-changed")); }
    catch (error) { message.textContent = error.message; message.className = "message show error"; }
    finally { button.disabled = false; button.textContent = "登录"; }
  });
  doc.addEventListener("ocvg:account-changed", refresh);
  root.addEventListener("focus", refresh);
  root.setInterval(() => { if (!doc.hidden) refresh(); }, 60000);
  refresh();
  if (root.location.hash === "#login" && !auth.read()?.access_token) openLogin();
})(globalThis);
