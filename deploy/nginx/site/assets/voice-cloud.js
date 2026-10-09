(function (root) {
  "use strict";
  function payload(line, voice) {
    const text = String(line.speechText || "").trim();
    if (!text || text.length > 1000) throw new Error("每句朗读文本须为 1–1000 字，请先拆分长句。");
    if (!voice?.id) throw new Error("请先选择云端音色。");
    const names = new Set(["happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm"]);
    return { chunks: [{ index: 0, text }], voice: { type: voice.type === "preset" ? "preset" : "uploaded", id: voice.id }, emotion: { name: names.has(line.emotion) ? line.emotion : null, weight: .65 }, audio: { speed: line.speed, volume: 1, pitch: 0, sample_rate: 24000, channels: 1 }, gpu_acceleration: true };
  }
  function fingerprint(line, voice) { return JSON.stringify(payload(line, voice)); }
  function createClient(env = root) {
    let refreshing;
    const auth = env.sessionRefresh || root.OCVGSessionRefresh?.create(env);
    const session = () => { try { return JSON.parse(env.sessionStorage.getItem("ocvg-cloud-session")); } catch { return null; } };
    async function request(path, options = {}, binary = false, retry = true, initial = session()) {
      const target = new URL(/^https?:\/\//i.test(path) || path.startsWith("/api/") ? path : `/api/v1${path}`, env.location.origin);
      if (target.origin !== env.location.origin || !target.pathname.startsWith("/api/v1/")) throw new Error("无效的云端素材地址");
      const current = session();
      if (!current?.access_token) throw new Error("请先在云端工作台登录，再返回此页。");
      if (!initial || current.user?.id !== initial.user?.id) throw new Error("账号已切换，请刷新页面。");
      const headers = { Accept: binary ? "*/*" : "application/json", ...options.headers, Authorization: `Bearer ${current.access_token}` };
      if (options.body && !(typeof FormData !== "undefined" && options.body instanceof FormData)) headers["Content-Type"] = "application/json";
      const response = await env.fetch(target.href, { ...options, headers });
      if (session()?.user?.id !== initial.user?.id) throw new Error("账号已切换，请刷新页面。");
      if (response.status === 401 && retry && (current.refresh_token || session()?.access_token !== current.access_token)) {
        if (auth) { await auth.refresh(current); return request(path, options, binary, false, initial); }
        if (session()?.access_token !== current.access_token) return request(path, options, binary, false, initial);
        refreshing ||= env.fetch(`${env.location.origin}/api/v1/auth/refresh`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: current.refresh_token }) }).then(async (r) => {
          if (!r.ok) throw new Error("登录已过期，请重新登录。");
          const fresh = await r.json();
          if (session()?.refresh_token !== current.refresh_token) throw new Error("账号已切换，请刷新页面。");
          env.sessionStorage.setItem("ocvg-cloud-session", JSON.stringify(fresh));
        }).finally(() => { refreshing = null; });
        await refreshing;
        return request(path, options, binary, false, initial);
      }
      if (session()?.user?.id !== current.user?.id) throw new Error("账号已切换，请刷新页面。");
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        const error = new Error(data.message || (typeof data.detail === "string" ? data.detail : data.detail?.message) || `云端请求失败（${response.status}）`);
        error.status = response.status; throw error;
      }
      const result = await (binary ? response.blob() : response.json());
      if (session()?.user?.id !== current.user?.id) throw new Error("账号已切换，请刷新页面。");
      return result;
    }
    return { request, session };
  }
  root.VoiceCloud = { payload, fingerprint, createClient };
})(globalThis);
