(function (root) {
  "use strict";
  const GENERIC_MESSAGE = "如果该邮箱已绑定账户，验证码将发送至邮箱，请检查收件箱或垃圾邮件。验证码 30 分钟内有效。";
  function loginPath(search) {
    const value = new URLSearchParams(search).get("return") || "/";
    const allowed = ["/", "/studio/", "/recharge/", "/projects/", "/contact/", "/services/", "/workspace/", "/voice-editor/", "/visual-editor/", "/video-editor/", "/subtitles/", "/admin/", "/admin/index.html"];
    return (allowed.includes(value) ? value : "/") + (value.startsWith("/admin/") && allowed.includes(value) ? "" : "#login");
  }
  function createClient(env) {
    let sending = false, confirming = false, retryAt = 0;
    const now = () => (env.now ? env.now() : Date.now());
    const seconds = () => Math.max(0, Math.ceil((retryAt - now()) / 1000));
    function cooldown(value) { const n = Number(value); retryAt = now() + (Number.isFinite(n) && n > 0 ? Math.min(86400, n) : 60) * 1000; }
    async function request(path, payload) {
      let response;
      try { response = await env.fetch("/api/v1/auth/password-reset/" + path, { method: "POST", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json", "Content-Type": "application/json" }, body: JSON.stringify(payload) }); }
      catch (_) { throw new Error("网络连接失败，请稍后重试。"); }
      const data = await response.json().catch(() => ({}));
      if (response.status === 429) { cooldown(response.headers?.get("Retry-After")); throw new Error("操作过于频繁，请等待后重试。"); }
      if (!response.ok) {
        const detail = typeof data.detail === "string" ? data.detail : typeof data.message === "string" ? data.message : "操作失败，请检查输入或稍后重试。";
        throw new Error(detail);
      }
      return data;
    }
    return {
      remaining: seconds,
      async send(email) {
        if (sending) throw new Error("正在发送，请勿重复点击。");
        if (seconds()) throw new Error(`请 ${seconds()} 秒后重新发送。`);
        sending = true;
        try { const data = await request("request", { email: email.trim() }); cooldown(data.retry_after); return GENERIC_MESSAGE; }
        finally { sending = false; }
      },
      async confirm(email, code, password, repeated) {
        if (confirming) throw new Error("正在重置，请勿重复提交。");
        if (!/^\d{6}$/.test(code.trim())) throw new Error("请输入邮件中的 6 位数字验证码。");
        if (password.length < 10 || password.length > 200) throw new Error("新密码需要 10 至 200 位字符。");
        if (password !== repeated) throw new Error("两次输入的新密码不一致。");
        confirming = true;
        try { return await request("confirm", { email: email.trim(), code: code.trim(), password }); }
        finally { confirming = false; }
      }
    };
  }
  root.OCVGPasswordReset = { createClient, loginPath };
  if (!root.document) return;
  const doc = root.document, byId = id => doc.getElementById(id), client = createClient(root);
  const form = byId("reset-form"), send = byId("reset-send"), submit = byId("reset-submit"), email = byId("reset-email"), message = byId("reset-message");
  let busy = false, sentTo = "";
  function show(text, kind = "error") { message.textContent = text; message.className = "register-message show " + kind; }
  function update() { const remaining = client.remaining(); send.disabled = busy || remaining > 0; send.textContent = remaining ? `${remaining} 秒后重新发送` : sentTo ? "重新发送验证码" : "发送验证码"; }
  byId("reset-back").href = loginPath(root.location.search);
  byId("reset-login").href = loginPath(root.location.search);
  send.addEventListener("click", async () => {
    if (!email.reportValidity()) return;
    busy = true; send.disabled = true; send.textContent = "正在发送…"; submit.disabled = true; email.readOnly = true;
    try { show(await client.send(email.value), "success"); sentTo = email.value.trim(); byId("reset-code").focus(); }
    catch (error) { show(error.message); }
    finally { busy = false; submit.disabled = false; email.readOnly = false; update(); }
  });
  email.addEventListener("input", () => { if (sentTo && email.value.trim() !== sentTo) { byId("reset-code").value = ""; show("邮箱已修改，请为当前邮箱重新发送验证码。", "info"); } });
  form.addEventListener("submit", async event => {
    event.preventDefault(); if (busy) return;
    busy = true; submit.disabled = true; send.disabled = true; email.readOnly = true; submit.textContent = "正在重置…";
    try {
      await client.confirm(email.value, byId("reset-code").value, byId("reset-password").value, byId("reset-repeat").value);
      try { root.sessionStorage.removeItem("ocvg-cloud-session"); } catch (_) {}
      form.reset(); form.hidden = true; byId("reset-success").hidden = false; byId("reset-login").focus();
    } catch (error) { show(error.message); }
    finally { busy = false; submit.disabled = false; submit.textContent = "重置密码"; email.readOnly = false; update(); }
  });
  root.setInterval(() => { if (!busy) update(); }, 1000);
})(globalThis);
