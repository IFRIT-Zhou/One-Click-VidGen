(function () {
  "use strict";
  const API_BASE = "/api/v1";
  const form = document.getElementById("register-form");
  const button = document.getElementById("register-submit");
  const message = document.getElementById("register-message");
  function setMessage(text, type = "error") { message.textContent = text || ""; message.className = `register-message ${text ? `show ${type}` : ""}`.trim(); }
  async function request(path, options = {}) { const response = await fetch(`${API_BASE}${path}`, { ...options, headers: { Accept: "application/json", "Content-Type": "application/json", ...(options.headers || {}) } }); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.message || data.detail || "注册失败，请稍后重试"); return data; }
  form.addEventListener("submit", async (event) => { event.preventDefault(); button.disabled = true; button.textContent = "正在注册…"; setMessage(""); const credentials = { email: document.getElementById("register-email").value.trim(), password: document.getElementById("register-password").value }; try { await request("/auth/register", { method: "POST", body: JSON.stringify({ ...credentials, source: 2 }) }); const session = await request("/auth/login", { method: "POST", body: JSON.stringify(credentials) }); sessionStorage.setItem("ocvg-cloud-session", JSON.stringify(session)); setMessage("注册成功，正在进入云端工作台…", "success"); window.setTimeout(() => { window.location.assign("/studio/"); }, 700); } catch (error) { setMessage(error.message); } finally { button.disabled = false; button.textContent = "注册并开始创作"; } });
})();
