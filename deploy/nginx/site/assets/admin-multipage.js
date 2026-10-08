(function () {
  "use strict";
  const items = [["overview", "概览", "/admin/"], ["analytics", "数据分析", "/admin/analytics.html"], ["users", "用户与权限", "/admin/users.html"], ["orders", "充值订单", "/admin/orders.html"], ["jobs", "任务监控", "/admin/jobs.html"], ["cluster", "集群状态", "/admin/cluster.html"], ["devices", "集群设备", "/admin/devices.html"], ["models", "模型发布", "/admin/models.html"], ["enrollment", "节点接入", "/admin/enrollment.html"], ["audit", "审计日志", "/admin/audit.html"]];
  const nav = document.querySelector(".admin-nav");
  if (nav) { nav.innerHTML = items.map(([id, label, href]) => `<a data-page="${id}" href="${href}">${label}</a>`).join(""); nav.querySelector("[data-page=\"overview\"]")?.classList.add("active"); }
}());
