(function () {
  const healthNodes = document.querySelectorAll("[data-health-status]");

  async function refreshHealth() {
    if (!healthNodes.length) return;
    try {
      const response = await fetch("/api/v1/health", { cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const health = await response.json();
      const online = Boolean(health.ok && health.control_api);
      healthNodes.forEach((node) => {
        node.classList.remove("online", "offline");
        node.classList.add(online ? "online" : "offline");
        node.innerHTML = `<i></i>${online ? "云端服务运行正常" : "部分云端服务异常"}`;
      });
    } catch (error) {
      healthNodes.forEach((node) => {
        node.classList.remove("online");
        node.classList.add("offline");
        node.innerHTML = "<i></i>云端服务暂时无法连接";
      });
    }
  }

  function enableMotion() {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    document.documentElement.classList.add("motion-enabled");
    const selectors = [
      ".section-heading", ".feature-card", ".service-row", ".price-panel",
      ".steps article", ".cta-panel", ".proof-grid > div", ".site-footer .footer-grid",
    ];
    const nodes = document.querySelectorAll(selectors.join(","));
    nodes.forEach((node, index) => {
      node.classList.add("reveal");
      if (index % 3 === 1) node.classList.add("reveal-delay-1");
      if (index % 3 === 2) node.classList.add("reveal-delay-2");
    });
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("visible");
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -30px" });
    nodes.forEach((node) => observer.observe(node));
  }

  enableMotion();
  refreshHealth();
  window.setInterval(refreshHealth, 30000);
})();
