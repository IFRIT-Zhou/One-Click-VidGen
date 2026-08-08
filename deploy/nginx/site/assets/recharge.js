(function () {
  const API_BASE = "/api/v1";
  const authDialog = document.querySelector("#auth-dialog");
  const authForm = document.querySelector("#auth-form");
  const authMessage = document.querySelector("#auth-message");
  const orderMessage = document.querySelector("#order-message");
  const payButton = document.querySelector("#pay-button");
  const guestPanel = document.querySelector("#account-guest");
  const accountPanel = document.querySelector("#account-summary");
  let authMode = "login";
  let session = readSession();

  function readSession() {
    try { return JSON.parse(sessionStorage.getItem("ocvg-cloud-session")) || null; } catch (error) { return null; }
  }

  function saveSession(value) {
    session = value;
    if (value) sessionStorage.setItem("ocvg-cloud-session", JSON.stringify(value));
    else sessionStorage.removeItem("ocvg-cloud-session");
  }

  function showMessage(node, text, type) {
    node.textContent = text;
    node.className = `message show ${type}`;
  }

  function clearMessage(node) {
    node.textContent = "";
    node.className = "message";
  }

  async function api(path, options) {
    const headers = { Accept: "application/json", ...(options && options.headers) };
    if (options && options.body) headers["Content-Type"] = "application/json";
    if (session && session.access_token) headers.Authorization = `Bearer ${session.access_token}`;
    const response = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...options, headers });
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const detail = typeof data === "object" ? (data.message || data.detail || data.code) : data;
      throw new Error(detail || `请求失败（HTTP ${response.status}）`);
    }
    return data;
  }

  function renderLoggedOut() {
    guestPanel.style.display = "flex";
    accountPanel.classList.remove("show");
    payButton.textContent = "登录后提交订单";
  }

  async function loadAccount() {
    if (!session || !session.access_token) { renderLoggedOut(); return; }
    try {
      const summary = await api("/account/summary");
      guestPanel.style.display = "none";
      accountPanel.classList.add("show");
      document.querySelector("#account-email").textContent = session.user && session.user.email ? session.user.email : "已登录账户";
      document.querySelector("#account-credits").textContent = Number(summary.credits && summary.credits.available || 0).toLocaleString("zh-CN");
      payButton.textContent = "支付宝支付";
      clearMessage(orderMessage);
    } catch (error) {
      saveSession(null);
      renderLoggedOut();
      showMessage(orderMessage, "登录状态已失效，请重新登录。", "error");
    }
  }

  function openAuth() {
    clearMessage(authMessage);
    if (typeof authDialog.showModal === "function") authDialog.showModal();
  }

  document.querySelector("#open-login").addEventListener("click", openAuth);
  document.querySelector("#close-dialog").addEventListener("click", () => authDialog.close());
  authDialog.addEventListener("click", (event) => { if (event.target === authDialog) authDialog.close(); });

  document.querySelectorAll("[data-auth-mode]").forEach((tab) => {
    tab.addEventListener("click", () => {
      authMode = tab.dataset.authMode;
      document.querySelectorAll("[data-auth-mode]").forEach((item) => item.classList.toggle("active", item === tab));
      document.querySelector("#auth-title").textContent = authMode === "login" ? "登录云端账户" : "注册云端账户";
      document.querySelector("#auth-submit").textContent = authMode === "login" ? "登录" : "注册并继续";
      document.querySelector("#password").autocomplete = authMode === "login" ? "current-password" : "new-password";
      clearMessage(authMessage);
    });
  });

  authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearMessage(authMessage);
    const submit = document.querySelector("#auth-submit");
    submit.disabled = true;
    submit.textContent = authMode === "login" ? "正在登录…" : "正在注册…";
    const payload = { email: authForm.email.value.trim(), password: authForm.password.value };
    try {
      if (authMode === "register") {
        await api("/auth/register", { method: "POST", body: JSON.stringify(payload) });
      }
      const login = await api("/auth/login", { method: "POST", body: JSON.stringify(payload) });
      saveSession(login);
      showMessage(authMessage, authMode === "register" ? "注册成功，正在进入充值页面…" : "登录成功，正在读取账户余额…", "success");
      await loadAccount();
      window.setTimeout(() => authDialog.close(), 450);
    } catch (error) {
      showMessage(authMessage, error.message, "error");
    } finally {
      submit.disabled = false;
      submit.textContent = authMode === "login" ? "登录" : "注册并继续";
    }
  });

  document.querySelector("#logout").addEventListener("click", () => {
    saveSession(null);
    renderLoggedOut();
    showMessage(orderMessage, "已退出云端账户。", "success");
  });

  async function loadRechargeProducts() {
    const testOption = document.querySelector("#test-payment-product");
    const grid = document.querySelector("#package-grid");
    try {
      const catalog = await api("/recharge/products");
      const products = new Map((catalog.items || []).map((item) => [item.product_id, item]));
      document.querySelectorAll(".package-option").forEach((option) => {
        const input = option.querySelector('input[name="package"]');
        const product = products.get(input.value);
        const isTestProduct = option === testOption;
        const enabled = Boolean(product && (!isTestProduct || catalog.test_product_enabled));
        option.hidden = !enabled;
        if (!enabled) return;
        const price = (Number(product.amount_fen) / 100).toFixed(2);
        const description = option.querySelector("small").textContent.split("·").slice(1).join("·").trim();
        input.dataset.credits = String(product.credits);
        input.dataset.price = price;
        option.querySelector("strong").textContent = `${Number(product.credits).toLocaleString("zh-CN")} 积分`;
        option.querySelector("small").textContent = `¥${price} · ${description}`;
      });
      const testEnabled = !testOption.hidden;
      grid.classList.toggle("test-product-enabled", testEnabled);
      if (!testEnabled && testOption.querySelector("input").checked) {
        const fallback = document.querySelector('input[name="package"][value="credits_1000"]');
        fallback.checked = true;
      }
      const selected = document.querySelector('input[name="package"]:checked');
      if (selected) selected.dispatchEvent(new Event("change"));
    } catch (error) {
      testOption.hidden = true;
      grid.classList.remove("test-product-enabled");
    }
  }

  document.querySelectorAll("input[name=package]").forEach((radio) => {
    radio.addEventListener("change", () => {
      document.querySelectorAll(".package-option").forEach((option) => option.classList.toggle("selected", option.contains(radio) && radio.checked));
      document.querySelector("#summary-credits").textContent = `${Number(radio.dataset.credits).toLocaleString("zh-CN")} 积分`;
      document.querySelector("#summary-price").textContent = `¥${radio.dataset.price}`;
      clearMessage(orderMessage);
    });
  });

  payButton.addEventListener("click", async () => {
    if (!session || !session.access_token) { openAuth(); return; }
    const selected = document.querySelector("input[name=package]:checked");
    payButton.disabled = true;
    payButton.textContent = "正在创建订单…";
    clearMessage(orderMessage);
    try {
      const order = await api("/recharge/orders", {
        method: "POST",
        headers: { "Idempotency-Key": `web-alipay-${Date.now()}-${Math.random().toString(16).slice(2)}` },
        body: JSON.stringify({ product_id: selected.value, payment_provider: "alipay" }),
      });
      const payment = order.payment || {};
      const target = payment.payment_url || payment.pay_url || payment.checkout_url || payment.url;
      sessionStorage.setItem("ocvg-last-recharge-order", order.order_id || "");
      if (target) {
        showMessage(orderMessage, "订单已创建，正在前往支付宝收银台…", "success");
        window.location.assign(target);
        return;
      }
      showMessage(orderMessage, `订单 ${order.order_id || ""} 已创建，请按服务端返回的支付指引继续。`, "success");
    } catch (error) {
      showMessage(orderMessage, `暂时无法发起支付宝支付：${error.message}`, "error");
    } finally {
      payButton.disabled = false;
      payButton.textContent = "支付宝支付";
    }
  });

  async function checkReturnedOrder() {
    const parameters = new URLSearchParams(window.location.search);
    const orderId = sessionStorage.getItem("ocvg-last-recharge-order");
    if (!parameters.has("alipay_return") || !orderId || !session) return;
    showMessage(orderMessage, "已返回商户页面，正在确认支付宝支付结果…", "success");
    for (let attempt = 0; attempt < 15; attempt += 1) {
      try {
        const order = await api(`/recharge/orders/${encodeURIComponent(orderId)}`);
        if (order.status === "paid") {
          showMessage(orderMessage, "支付成功，积分已经到账。", "success");
          sessionStorage.removeItem("ocvg-last-recharge-order");
          await loadAccount();
          return;
        }
        if (["cancelled", "expired", "refunded"].includes(order.status)) {
          showMessage(orderMessage, `订单状态：${order.status}，本次未增加积分。`, "error");
          return;
        }
      } catch (error) {
        showMessage(orderMessage, `查询支付结果失败：${error.message}`, "error");
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
    }
    showMessage(orderMessage, "支付结果仍在确认中，请稍后刷新页面查看积分余额。", "success");
  }

  loadRechargeProducts();
  loadAccount().then(checkReturnedOrder);
})();
