(function (root) {
  "use strict";
  function text(value) {
    const p = value?.pricing, examples = value?.examples;
    if (!p || p.currency !== "CNY" || p.duration_basis !== "requested"
        || p.rounding !== "ceil_cent" || !p.version
        || ![p.threshold_seconds, p.first_10_seconds_rate, p.additional_seconds_rate, p.credits_per_yuan]
          .every(v => Number.isFinite(Number(v)) && Number(v) > 0)
        || !Array.isArray(examples) || !examples.length
        || !examples.every(e => Number.isFinite(Number(e.seconds)) && Number(e.seconds) > 0
          && Number.isFinite(Number(e.credits)) && Number(e.credits) > 0)) throw new Error("Invalid H3 pricing");
    return {
      rate: `每镜前 ${p.threshold_seconds} 秒 ${p.first_10_seconds_rate} 元/秒，超出部分 ${p.additional_seconds_rate} 元/秒。`,
      examples: examples.map(e => `${e.seconds} 秒 ${Number(e.credits).toFixed(2)} 积分`).join("；"),
      terms: `1 元 = ${p.credits_per_yuan} 积分。按请求时长计费，补帧不加收；每镜向上取整到分，整分不加价。提交时预留，成功扣费，失败或取消后释放；排队中的已确认报价保留。`
    };
  }
  root.OCVGH3Pricing = { text };
  if (!root.document) return;
  const panels = [...root.document.querySelectorAll("[data-h3-pricing]")];
  if (!panels.length) return;
  let loading = false;
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const response = await root.fetch("/api/v1/cloud/video/pricing", { cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Pricing unavailable");
      const value = text(await response.json());
      for (const panel of panels) {
        panel.querySelector("[data-h3-rate]").textContent = value.rate;
        panel.querySelector("[data-h3-examples]").textContent = value.examples;
        panel.querySelector("[data-h3-terms]").textContent = value.terms;
      }
    } catch (_) {
      for (const panel of panels) {
        panel.querySelector("[data-h3-rate]").textContent = "暂时无法读取 H3 价格，请稍后刷新；生成前须确认云端报价。";
        panel.querySelector("[data-h3-examples]").textContent = "";
        panel.querySelector("[data-h3-terms]").textContent = "";
      }
    } finally { loading = false; }
  }
  refresh();
  root.addEventListener("focus", refresh);
  root.document.addEventListener("visibilitychange", () => { if (!root.document.hidden) refresh(); });
})(typeof window === "undefined" ? globalThis : window);
