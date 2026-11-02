(function () {
  "use strict";
  const API_BASE = "/api/v1";
  const form = document.getElementById("register-form");
  const button = document.getElementById("register-submit");
  const message = document.getElementById("register-message");
  const email = document.getElementById("register-email");
  const password = document.getElementById("register-password");
  const codeField = document.getElementById("verification-field");
  const code = document.getElementById("register-code");
  const inviteCode = new URLSearchParams(window.location.search).get("invite");
  let awaitingVerification = false;
  if (inviteCode) document.getElementById("invite-status").textContent = `邀请码 ${inviteCode.toUpperCase()} · 验证后邀请人获赠 10 积分`;
  function setMessage(text, type = "error") { message.textContent = text || ""; message.className = `register-message ${text ? `show ${type}` : ""}`.trim(); }
  async function request(path, options = {}) { const response = await fetch(`${API_BASE}${path}`, { ...options, headers: { Accept: "application/json", "Content-Type": "application/json", ...(options.headers || {}) } }); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.message || data.detail || "注册失败，请稍后重试"); return data; }
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); button.disabled = true; setMessage("");
    try {
      if (!awaitingVerification) {
        button.textContent = "正在发送验证码…";
        const payload = { email: email.value.trim(), password: password.value, source: 2 };
        if (inviteCode) payload.inviteCode = inviteCode;
        const result = await request("/auth/register", { method: "POST", body: JSON.stringify(payload) });
        awaitingVerification = true; codeField.hidden = false; code.required = true;
        email.readOnly = true; password.hidden = true; password.required = false;
        button.textContent = "验证邮箱"; code.focus();
        setMessage(result.message || "验证码已发送，请查收邮箱。", "success");
      } else {
        button.textContent = "正在验证…";
        await request("/auth/register/verify", { method: "POST", body: JSON.stringify({ email: email.value.trim(), code: code.value.trim() }) });
        setMessage("邮箱验证完成，请登录进入云端工作台。", "success");
        window.setTimeout(() => window.location.assign("/studio/#login"), 900);
      }
    } catch (error) { setMessage(error.message); }
    finally { button.disabled = false; if (awaitingVerification) button.textContent = "验证邮箱"; else button.textContent = "注册并开始创作"; }
  });
  document.getElementById("resend-code").addEventListener("click", async (event) => {
    const resend = event.currentTarget; resend.disabled = true;
    try { const result = await request("/auth/register/resend", { method: "POST", body: JSON.stringify({ email: email.value.trim() }) }); setMessage(result.message || "验证码已重新发送。", "success"); }
    catch (error) { setMessage(error.message); }
    finally { window.setTimeout(() => { resend.disabled = false; }, 60000); }
  });
})();
