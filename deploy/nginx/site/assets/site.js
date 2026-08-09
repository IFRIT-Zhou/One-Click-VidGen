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
      ".section-heading", ".feature-card", ".service-row", ".service-jump", ".service-module", ".price-panel",
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


  const pricingLabels = {
    credits_1: ["轻量体验", "快速体验云端服务，适合确认音色和短文案效果。"],
    credits_5: ["入门包", "适合少量试用，完成短配音或单次图片任务。"],
    credits_10: ["基础包", "适合短内容创作，配音、图片与模型按实际用量结算。"],
    credits_20: ["标准包", "适合日常创作，配音、图片与模型均按实际用量结算。"],
    credits_30: ["进阶包", "适合连续任务和多镜头内容，余额更从容。"],
    credits_50: ["创作包", "适合多次创作与更完整的视频工作流。"],
    credits_100: ["专业包", "适合批量任务和高频内容生产。"],
  };

  function selectServicePrice(button) {
    const panel = document.querySelector(".billing-dynamic");
    if (!panel || !button) return;
    const id = button.dataset.productId;
    const credits = Number(button.dataset.credits || 0);
    const amountFen = Number(button.dataset.amountFen || 0);
    const [label, description] = pricingLabels[id] || ["积分套餐", "按实际用量结算。"];
    document.querySelectorAll("#services-package-list .mini-package").forEach((item) => item.classList.toggle("selected", item === button));
    panel.dataset.selectedProduct = id;
    panel.classList.remove("changing");
    window.requestAnimationFrame(() => panel.classList.add("changing"));
    window.setTimeout(() => panel.classList.remove("changing"), 220);
    document.querySelector("#billing-tier-name").textContent = label;
    document.querySelector("#billing-description").textContent = description;
    document.querySelector("#billing-credit-value").textContent = credits.toLocaleString("zh-CN");
    document.querySelector("#billing-orbit-value").textContent = credits.toLocaleString("zh-CN");
    document.querySelector("#billing-price-value").textContent = "¥" + (amountFen / 100).toFixed(2);
    document.querySelector("#billing-action").href = "/recharge/?product=" + encodeURIComponent(id);
  }

  async function setupServicesPricing() {
    const list = document.querySelector("#services-package-list");
    if (!list) return;
    const buttons = [...list.querySelectorAll(".mini-package")];
    buttons.forEach((button) => button.addEventListener("click", () => selectServicePrice(button)));
    selectServicePrice(buttons.find((button) => button.dataset.productId === "credits_20") || buttons[0]);
    try {
      const response = await fetch("/api/v1/recharge/products", { cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const catalog = await response.json();
      const products = new Map((catalog.items || []).map((product) => [product.product_id, product]));
      buttons.forEach((button) => {
        const product = products.get(button.dataset.productId);
        button.hidden = !product;
        if (!product) return;
        button.dataset.credits = String(product.credits);
        button.dataset.amountFen = String(product.amount_fen);
        button.querySelector("strong").textContent = Number(product.credits).toLocaleString("zh-CN") + " 积分";
        button.querySelector("small").textContent = "¥" + (Number(product.amount_fen) / 100).toFixed(2);
      });
      const selected = buttons.find((button) => button.classList.contains("selected") && !button.hidden)
        || buttons.find((button) => !button.hidden);
      selectServicePrice(selected);
      document.querySelector("#pricing-status").textContent = "已同步 " + products.size + " 个云端套餐";
    } catch (error) {
      document.querySelector("#pricing-status").textContent = "使用已发布套餐";
    }
  }

  function setupServiceNavigation() {
    const links = [...document.querySelectorAll("[data-service-link]")];
    if (!links.length) return;
    const targets = links.map((link) => document.querySelector("#" + link.dataset.serviceLink)).filter(Boolean);
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        links.forEach((link) => link.classList.toggle("active", link.dataset.serviceLink === entry.target.id));
      });
    }, { threshold: .45, rootMargin: "-70px 0px -30%" });
    targets.forEach((target) => observer.observe(target));
    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const nodes = [...document.querySelectorAll(".hero-service-flow .flow-node")];
      let index = 0;
      window.setInterval(() => {
        nodes.forEach((node, nodeIndex) => node.classList.toggle("active", nodeIndex === index));
        index = (index + 1) % nodes.length;
      }, 1300);
    }
  }

  function setupCreationFlowAuth() {
    const links = [...document.querySelectorAll("[data-creation-target]")];
    const authDialog = document.querySelector("#home-auth-dialog");
    if (!links.length || !authDialog) return;

    const authForm = document.querySelector("#home-auth-form");
    const authMessage = document.querySelector("#home-auth-message");
    const authSubmit = document.querySelector("#home-auth-submit");
    const statusDialog = document.querySelector("#home-status-dialog");
    let authMode = "login";
    let pendingCreation = null;

    function readSession() {
      try { return JSON.parse(sessionStorage.getItem("ocvg-cloud-session")) || null; }
      catch (_) { return null; }
    }

    function saveSession(session) {
      sessionStorage.setItem("ocvg-cloud-session", JSON.stringify(session));
    }

    function isLoggedIn() {
      const session = readSession();
      return Boolean(session && session.access_token);
    }

    async function authRequest(path, body) {
      const response = await fetch(`/api/v1${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      const text = await response.text();
      let payload = {};
      try { payload = text ? JSON.parse(text) : {}; } catch (_) { payload = {}; }
      if (!response.ok) throw new Error(payload.detail || payload.message || `请求失败（${response.status}）`);
      return payload;
    }

    function openAuth() {
      authMessage.className = "message";
      authMessage.textContent = "";
      if (!authDialog.open) authDialog.showModal();
    }

    function showLoggedIn() {
      if (!statusDialog.open) statusDialog.showModal();
    }

    function continueCreation(target, href) {
      if (target === "account") showLoggedIn();
      else window.location.assign(href);
    }

    links.forEach((link) => link.addEventListener("click", (event) => {
      const target = link.dataset.creationTarget;
      if (isLoggedIn()) {
        if (target !== "account") return;
        event.preventDefault();
        showLoggedIn();
        return;
      }
      event.preventDefault();
      pendingCreation = { target, href: link.href };
      openAuth();
    }));

    document.querySelectorAll("[data-home-auth-mode]").forEach((tab) => tab.addEventListener("click", () => {
      authMode = tab.dataset.homeAuthMode;
      document.querySelectorAll("[data-home-auth-mode]").forEach((item) => item.classList.toggle("active", item === tab));
      document.querySelector("#home-auth-title").textContent = authMode === "login" ? "登录云端账户" : "注册云端账户";
      authSubmit.textContent = authMode === "login" ? "登录" : "注册并继续";
      document.querySelector("#home-password").autocomplete = authMode === "login" ? "current-password" : "new-password";
      authMessage.className = "message";
    }));

    authForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const credentials = { email: event.currentTarget.email.value.trim(), password: event.currentTarget.password.value };
      authSubmit.disabled = true;
      authSubmit.textContent = authMode === "login" ? "正在登录…" : "正在注册…";
      try {
        if (authMode === "register") await authRequest("/auth/register", credentials);
        const session = await authRequest("/auth/login", credentials);
        saveSession(session);
        authDialog.close();
        const destination = pendingCreation;
        pendingCreation = null;
        if (destination) continueCreation(destination.target, destination.href);
      } catch (error) {
        authMessage.textContent = error.message || "登录失败，请稍后重试";
        authMessage.className = "message show error";
      } finally {
        authSubmit.disabled = false;
        authSubmit.textContent = authMode === "login" ? "登录" : "注册并继续";
      }
    });

    document.querySelector("#home-auth-close").addEventListener("click", () => authDialog.close());
    document.querySelector("#home-status-close").addEventListener("click", () => statusDialog.close());
    [authDialog, statusDialog].forEach((dialog) => dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    }));
  }

  setupServicesPricing();
  setupServiceNavigation();
  setupCreationFlowAuth();
  enableMotion();
  refreshHealth();
  window.setInterval(refreshHealth, 30000);
})();
