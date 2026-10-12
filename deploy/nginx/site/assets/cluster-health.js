(function (root) {
  "use strict";
  // Admission describes queue availability; model readiness alone cannot gate cold starts.
  function describe(payload) {
    const health = payload || {}, ray = health.ray || {}, dispatcher = ray.dispatcher || {};
    const admission = health.tts_admission;
    const queueHealthy = dispatcher.redis_ready !== false && dispatcher.consumer_alive !== false;
    const onDemand = admission?.on_demand === true;
    const accepting = health.control_api === true && queueHealthy && (
      onDemand
        ? ["accepting", "scheduler_ready", "queue_ready"].every(key => admission[key] === true)
        : health.ok === true && !health.ray_error && ray.ok !== false && dispatcher.ready !== false
    );
    const ready = accepting && health.ok === true && ray.ok !== false && dispatcher.ready !== false;
    return {
      accepting, ready,
      label: !accepting ? "配音服务暂不可用" : ready ? "配音服务已就绪" : "配音可接单 · 按需启动",
      detail: !accepting ? "暂时无法接收配音任务，请稍后重试。" : ready ? "配音模型已就绪。" : "任务可正常提交；集群将自动排队并启动配音模型，首次生成需等待加载。",
    };
  }
  root.OCVGClusterHealth = Object.freeze({ describe });
})(globalThis);
