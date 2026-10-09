(function () {
  "use strict";
  const API = "/api/v1";
  const page = document.body.dataset.adminPage || "overview";
  const sessionKey = "ocvg-cloud-session";
  const listState = {page:1, size:20, q:"", source:"", status:"", kind:"tts", startDate:"", endDate:"", total:0};
  const analyticsState = {days:30, granularity:"day"};
  let refreshPromise = null;
  let loading = false;
  const paged = ["users", "jobs", "audit", "orders"].includes(page);
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>'"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
  const date = (v) => v ? new Date(v).toLocaleString("zh-CN", {dateStyle:"short", timeStyle:"short", timeZone:"Asia/Shanghai"}) : "—";
  const money = (v) => Number(v || 0).toLocaleString("zh-CN", {minimumFractionDigits:2, maximumFractionDigits:2});
  const connectivity = (x) => {
    if (x?.ray_connected === true) return '<span class="status-pill status-online">Ray 在线</span>';
    const value = x?.connectivity_status;
    if (value === "online") return '<span class="status-pill status-online">在线</span>';
    if (value === "stale") return '<span class="status-pill status-stale">心跳超时</span>';
    return '<span class="status-pill status-unknown">未上报</span>';
  };
  const heartbeat = (x) => x?.last_seen_at ? date(x.last_seen_at) : "暂无上报";
  function getSession() { try { return JSON.parse(sessionStorage.getItem(sessionKey)) || null; } catch (_) { return null; } }
  function save(v) { if (v) sessionStorage.setItem(sessionKey, JSON.stringify(v)); else sessionStorage.removeItem(sessionKey); }
  async function refresh(s) {
    if (!s?.refresh_token) return null;
    if (!refreshPromise) refreshPromise = (async () => {
      const r = await fetch(`${API}/auth/refresh`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({refresh_token:s.refresh_token})});
      if (!r.ok) return null;
      const n = await r.json(); save(n); return n;
    })().finally(() => { refreshPromise = null; });
    return refreshPromise;
  }
  async function api(path, options = {}, retry = true) {
    let s = getSession();
    const headers = {Accept:"application/json", ...(options.headers || {})};
    if (s?.access_token) headers.Authorization = `Bearer ${s.access_token}`;
    if (options.body) headers["Content-Type"] = "application/json";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    let r;
    try {
      r = await fetch(`${API}${path}`, {...options, headers, signal:controller.signal});
      if (r.status === 401 && retry) {
        s = await refresh(s);
        if (s) return api(path, options, false);
      }
      if (r.status === 401) {
        save(null); location.assign("/admin/"); throw new Error("登录已过期，请重新登录");
      }
      const type = r.headers.get("content-type") || "";
      const data = type.includes("json") ? await r.json() : null;
      if (!r.ok) {
        const detail = data?.message || data?.detail;
        throw new Error(typeof detail === "string" ? detail : `请求失败（HTTP ${r.status}）`);
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("请求超时，请稍后刷新重试");
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function message(text, ok = false) { const n = $("page-message"); if (!n) return; n.textContent = text || ""; n.className = `admin-message ${text ? `show ${ok ? "success" : "error"}` : ""}`.trim(); }
  const NAV_ITEMS = [["overview","概览","/admin/"],["analytics","数据分析","/admin/analytics.html"],["users","用户与权限","/admin/users.html"],["orders","充值订单","/admin/orders.html"],["jobs","任务监控","/admin/jobs.html"],["cluster","集群状态","/admin/cluster.html"],["devices","集群设备","/admin/devices.html"],["models","模型发布","/admin/models.html"],["enrollment","节点接入","/admin/enrollment.html"],["audit","审计日志","/admin/audit.html"]]; function normalizeNav(activePage) { const nav = document.querySelector(".admin-nav"); if (!nav) return; nav.innerHTML = NAV_ITEMS.map(([id,label,href]) => `<a data-page="${id}" href="${href}">${label}</a>`).join(""); nav.querySelector(`[data-page="${activePage}"]`)?.classList.add("active"); }
  function wireShell() { const s = getSession(); if (!s?.access_token || s.user?.role !== "admin") { location.href = "/admin/"; return false; } $("admin-identity").textContent = s.user.email || "管理员"; $("admin-logout").addEventListener("click", () => { save(null); location.href = "/admin/"; }); normalizeNav(page === "device" ? "devices" : page); return true; }
  async function users() { const d = await listData("/admin/users"); $("page-content").innerHTML = `<div class="admin-table-wrap"><table class="admin-table"><thead><tr><th>账户</th><th>来源</th><th>可用积分</th><th>冻结 / 已消耗</th><th>角色</th><th>状态</th><th>注册时间</th><th>操作</th></tr></thead><tbody>${(d.items||[]).map(u => `<tr><td><strong>${esc(u.email)}</strong><small>${esc(u.id)}</small></td><td>${Number(u.source)===2?"二维码":"B站"}</td><td><strong>${Number(u.credits?.available || 0).toLocaleString("zh-CN")}</strong></td><td>${Number(u.credits?.reserved || 0).toLocaleString("zh-CN")} / ${Number(u.credits?.consumed || 0).toLocaleString("zh-CN")}</td><td>${esc(u.role)}</td><td>${esc(u.status)}</td><td>${date(u.created_at)}</td><td><button class="table-button" data-credit-id="${esc(u.id)}" data-credit-email="${esc(u.email)}" data-credit-value="${Number(u.credits?.available || 0)}">调整积分</button> <button type="button" class="table-button" data-quota-id="${esc(u.id)}" data-quota-email="${esc(u.email)}">额度</button> <button type="button" class="table-button" data-reset-id="${esc(u.id)}" data-reset-email="${esc(u.email)}" ${u.id === getSession()?.user?.id ? "disabled" : ""}>重置密码</button> <button class="table-button" data-user-state="${u.status === "disabled" ? "enable" : "disable"}" data-user-id="${esc(u.id)}" data-user-email="${esc(u.email)}" ${u.id === getSession()?.user?.id || u.status === "pending" ? "disabled" : ""}>${u.status === "disabled" ? "恢复" : "停用"}</button></td></tr>`).join("") || '<tr><td colspan="8" class="admin-empty">暂无账户</td></tr>'}</tbody></table></div>`; $("page-summary").textContent = `共 ${d.total || 0} 个匹配账户`; document.querySelectorAll("[data-reset-id]").forEach(b => b.onclick = () => resetPasswordDialog(b.dataset.resetId, b.dataset.resetEmail)); document.querySelectorAll("[data-quota-id]").forEach(b => b.onclick = () => quotaDialog(b.dataset.quotaId, b.dataset.quotaEmail)); document.querySelectorAll("[data-user-state]").forEach(b => b.onclick = () => userState(b)); document.querySelectorAll("[data-credit-id]").forEach(b => b.addEventListener("click", () => creditDialog(b.dataset.creditId, b.dataset.creditEmail, b.dataset.creditValue))); }
  function resetPasswordDialog(userId, email) {
    let dialog = $("reset-password-dialog");
    if (!dialog) { dialog = document.createElement("dialog"); dialog.id = "reset-password-dialog"; dialog.className = "admin-dialog"; document.body.append(dialog); }
    dialog.innerHTML = `<form class="admin-dialog-inner"><div class="admin-dialog-heading"><div><span>ACCOUNT PASSWORD</span><h2>重置用户密码</h2><p>${esc(email)}</p></div><button type="button" class="dialog-close" aria-label="关闭">×</button></div><p>确认将此用户的密码重置为 <strong>123456789</strong>？</p><p>重置后原密码无法使用，已有登录会失效，用户需用新密码重新登录。</p><div class="admin-message" role="status" data-reset-message></div><div class="admin-dialog-actions"><button type="button" class="button secondary" data-reset-cancel>取消</button><button type="submit" class="button primary">确认重置</button></div></form>`;
    let submitting = false;
    const close = () => { if (!submitting) dialog.close(); };
    dialog.querySelectorAll("[data-reset-cancel],.dialog-close").forEach(b => b.onclick = close);
    dialog.oncancel = e => { if (submitting) e.preventDefault(); };
    dialog.querySelector("form").addEventListener("submit", async e => {
      e.preventDefault();
      if (submitting) return;
      submitting = true;
      const buttons = dialog.querySelectorAll("button"), alertNode = dialog.querySelector("[data-reset-message]");
      buttons.forEach(b => b.disabled = true);
      alertNode.textContent = "正在重置…"; alertNode.className = "admin-message show";
      try {
        await api(`/admin/users/${encodeURIComponent(userId)}/reset-password`, {method:"POST"});
        dialog.close();
        message(`${email} 的密码已重置为 123456789，旧登录已失效。`, true);
      } catch (error) { alertNode.textContent = error.message; alertNode.className = "admin-message show error"; }
      finally { submitting = false; buttons.forEach(b => b.disabled = false); }
    });
    dialog.showModal();
  }
  function creditDialog(userId, email, available) { let dialog = $("credit-dialog"); if (!dialog) { dialog = document.createElement("dialog"); dialog.id = "credit-dialog"; dialog.className = "admin-dialog"; document.body.append(dialog); } dialog.innerHTML = `<form method="dialog" class="admin-dialog-inner" id="credit-adjust-form"><div class="admin-dialog-heading"><div><span>WALLET CONTROL</span><h2>调整账户积分</h2><p>${esc(email)} · 当前可用 ${esc(available)}</p></div><button type="button" class="dialog-close" value="cancel">×</button></div><label class="admin-field"><span>调整数量（正数增加，负数扣除）</span><input id="credit-adjust-amount" type="number" step="0.000001" required placeholder="例如 500 或 -100"></label><label class="admin-field"><span>调整原因（写入审计）</span><input id="credit-adjust-reason" minlength="2" maxlength="500" value="管理员后台调整" required></label><div id="credit-adjust-message" class="admin-message"></div><div class="admin-dialog-actions"><button type="button" class="button secondary" data-dialog-cancel>取消</button><button class="button primary" type="submit">确认调整</button></div></form>`; dialog.querySelectorAll("[data-dialog-cancel],.dialog-close").forEach((b)=>b.onclick=()=>dialog.close()); dialog.querySelector("form").addEventListener("submit", async (e) => { e.preventDefault(); const submit=dialog.querySelector('button[type="submit"]'); if(submit.disabled)return; const amount=Number($("credit-adjust-amount").value), reason=$("credit-adjust-reason").value.trim(), alertNode=$("credit-adjust-message"); if (!amount) { alertNode.textContent="调整数量不能为 0"; alertNode.className="admin-message show error"; return; } submit.disabled=true; try { const key=dialog.dataset.operationKey; await api(`/admin/users/${encodeURIComponent(userId)}/credits/adjust`, {method:"POST", headers:{"Idempotency-Key":key}, body:JSON.stringify({amount, reason, idempotency_key:key})}); dialog.close(); message("积分已调整。", true); await users(); } catch (err) { alertNode.textContent=err.message; alertNode.className="admin-message show error"; } finally { submit.disabled=false; } }); dialog.dataset.operationKey=crypto.randomUUID(); dialog.showModal(); }
  const JOB_STATUS_LABELS = {created:"已创建", queued:"排队中", running:"运行中", finalizing:"收尾中", storyboarding:"分镜中", generating_assets:"生成素材", composing:"合成中", publishing:"发布中", completed:"已完成", failed:"失败", cancel_requested:"等待取消", cancelled:"已取消", expired:"已过期"};
  const jobStatus = (value) => JOB_STATUS_LABELS[value] || value || "未知";
  const jobStage = (value) => ({storyboard:"分镜", tts:"配音", images:"图片", compose:"合成", publish:"发布"}[value] || value || "—");
  const jobCredit = (value) => Number(value || 0).toLocaleString("zh-CN", {maximumFractionDigits:6});
  function jobError(error) { return error ? `<div class="job-error"><strong>${esc(error.code || "任务错误")}</strong><span>${esc(error.message || "未提供错误详情")}</span></div>` : '<span class="job-ok">无错误</span>'; }
  async function jobDetail(kind, id) {
    let dialog = $("job-detail-dialog");
    if (!dialog) { dialog = document.createElement("dialog"); dialog.id = "job-detail-dialog"; dialog.className = "admin-dialog job-detail-dialog"; document.body.append(dialog); }
    dialog.innerHTML = `<div class="admin-dialog-inner"><div class="admin-dialog-heading"><div><span>TASK DETAIL</span><h2>任务详情</h2><p>${esc(id)} · ${kind === "video" ? "视频" : "TTS"}</p></div><button type="button" class="dialog-close" data-job-close aria-label="关闭详情">×</button></div><div class="job-detail-loading" role="status">正在读取任务详情…</div></div>`;
    dialog.querySelector("[data-job-close]").onclick = () => dialog.close();
    const requestId = crypto.randomUUID();
    dialog.dataset.jobRequest = requestId;
    dialog.showModal();
    try {
      const d = await api(`/admin/jobs/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`);
      if (!dialog.open || dialog.dataset.jobRequest !== requestId) return;
      const steps = Array.isArray(d.steps) ? d.steps : [];
      dialog.querySelector(".admin-dialog-inner").innerHTML = `<div class="admin-dialog-heading"><div><span>TASK DETAIL</span><h2>${esc(d.client_job_id || d.job_id || id)}</h2><p>${esc(d.user_email || d.user_id || "—")} · ${kind === "video" ? "视频" : "TTS"}</p></div><button type="button" class="dialog-close" data-job-close aria-label="关闭详情">×</button></div><div class="job-detail-summary"><article><span>状态</span><strong>${esc(jobStatus(d.status))}</strong></article><article><span>阶段</span><strong>${esc(jobStage(d.stage))}</strong></article><article><span>进度</span><strong>${Number(d.progress || 0)}%</strong></article><article><span>积分</span><strong>${jobCredit(d.credits?.reserved)} / ${jobCredit(d.credits?.consumed)} / ${jobCredit(d.credits?.released)}</strong><small>预留 / 消耗 / 释放</small></article></div><dl class="job-detail-times"><div><dt>创建</dt><dd>${date(d.created_at)}</dd></div><div><dt>开始</dt><dd>${date(d.started_at)}</dd></div><div><dt>更新</dt><dd>${date(d.updated_at)}</dd></div><div><dt>完成</dt><dd>${date(d.finished_at)}</dd></div></dl><p class="job-detail-message">${esc(d.message || "暂无任务消息")}</p>${jobError(d.error)}${kind === "tts" ? `<div class="job-detail-counts"><span>字符数 <strong>${Number(d.total_characters || 0).toLocaleString("zh-CN")}</strong></span><span>分片 <strong>${Number(d.completed_chunks || 0)} / ${Number(d.total_chunks || 0)}</strong></span></div>` : ""}${kind === "video" ? `<section class="job-steps"><h3>处理步骤</h3>${steps.length ? `<ol>${steps.map(step => `<li><div><strong>${esc(jobStage(step.step_key))}</strong><span>${esc(jobStatus(step.status))} · ${Number(step.attempt_count || 0)} 次尝试</span></div><small>${date(step.started_at)} → ${date(step.finished_at)}</small>${jobError(step.error)}</li>`).join("")}</ol>` : '<p class="admin-empty">暂无步骤记录</p>'}</section>` : ""}`;
      dialog.querySelector("[data-job-close]").onclick = () => dialog.close();
    } catch (error) {
      if (!dialog.open || dialog.dataset.jobRequest !== requestId) return;
      dialog.querySelector(".admin-dialog-inner").innerHTML = `<div class="admin-dialog-heading"><div><span>TASK DETAIL</span><h2>任务详情</h2></div><button type="button" class="dialog-close" data-job-close aria-label="关闭详情">×</button></div><div class="admin-message show error" role="alert">${esc(error.message)}</div><div class="admin-dialog-actions"><button type="button" class="button secondary" data-job-close>关闭</button></div>`;
      dialog.querySelectorAll("[data-job-close]").forEach(button => button.onclick = () => dialog.close());
    }
  }
  async function jobs() { const d = await listData("/admin/jobs"); const kind = d.kind || listState.kind; const items = d.items || []; $("page-summary").textContent = `共 ${d.total || 0} 条${kind === "video" ? "视频" : "TTS"}任务 · 只读监控`; $("page-content").innerHTML = `<div class="admin-table-wrap"><table class="admin-table jobs-table"><thead><tr><th>任务</th><th>用户</th><th>状态 / 阶段</th><th>进度</th><th>积分（预留 / 消耗 / 释放）</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${items.map(j => { const progress=Math.min(100,Math.max(0,Number(j.progress || 0))); return `<tr><td><strong>${esc(j.job_id)}</strong><small>${esc(j.client_job_id || "—")}</small></td><td><strong>${esc(j.user_email || "—")}</strong><small>${esc(j.user_id)}</small></td><td><span class="status-pill job-status-${esc(j.status)}">${esc(jobStatus(j.status))}</span><small>${esc(jobStage(j.stage))}</small></td><td><div class="job-progress" role="progressbar" aria-label="任务进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><b style="width:${progress}%"></b><span>${progress}%</span></div></td><td>${jobCredit(j.credits?.reserved)} / ${jobCredit(j.credits?.consumed)} / ${jobCredit(j.credits?.released)}</td><td>${date(j.updated_at || j.created_at)}</td><td><button type="button" class="table-button" data-job-detail="${esc(j.job_id)}" data-job-kind="${esc(kind)}">查看详情</button></td></tr>`; }).join("") || '<tr><td colspan="7" class="admin-empty">暂无任务</td></tr>'}</tbody></table></div>`; document.querySelectorAll("[data-job-detail]").forEach(button => button.onclick = () => jobDetail(button.dataset.jobKind, button.dataset.jobDetail)); }
  async function cluster() { const d = await api("/admin/cluster/summary"); const ray=d.ray||{}, dis=ray.dispatcher||{}, dep=dis.dependencies||{}; $("page-content").innerHTML = `<div class="metric-grid"><article><span>控制 API</span><strong>${d.control_api?"正常":"异常"}</strong></article><article><span>调度器</span><strong>${dis.ready?"就绪":"异常"}</strong></article><article><span>GPU 副本</span><strong>${dep.model_worker?.registered_gpu || 0} / ${ray.gpu_replicas_configured || 0}</strong></article><article><span>队列深度</span><strong>${dis.queue_depth || 0}</strong></article></div><pre class="admin-json">${esc(JSON.stringify(d,null,2))}</pre>`; }
  async function audit() { const d = await listData("/admin/audit-logs"); $("page-summary").textContent=`共 ${d.total||0} 条匹配审计记录`; $("page-content").innerHTML = `<div class="admin-table-wrap"><table class="admin-table"><thead><tr><th>时间</th><th>操作</th><th>目标</th><th>详情</th></tr></thead><tbody>${(d.items||[]).map(a => `<tr><td>${date(a.created_at)}</td><td>${esc(a.action)}</td><td>${esc(a.target_id)}</td><td>${esc(JSON.stringify(a.details||{}))}</td></tr>`).join("") || '<tr><td colspan="4" class="admin-empty">暂无审计记录</td></tr>'}</tbody></table></div>`; }
  function chart(title, series, key, color, suffix="") {
    const max = Math.max(1, ...series.map(x => Number(x[key] || 0)));
    const width = 640, height = 190, step = width / Math.max(1, series.length);
    const bars = series.map((x, i) => {
      const value = Number(x[key] || 0), barHeight = value / max * height;
      return `<rect x="${(i * step).toFixed(2)}" y="${(height - barHeight).toFixed(2)}" width="${Math.max(0.5, step * .8).toFixed(2)}" height="${barHeight.toFixed(2)}" fill="${color}"><title>${esc(x.period)}：${value}${suffix}</title></rect>`;
    }).join("");
    return `<section class="chart-card"><div class="chart-heading"><h3>${title}</h3><span>峰值 ${max === 1 && !series.some(x => Number(x[key])) ? 0 : max}${suffix}</span></div><svg class="analytics-plot" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${title}，${esc(series[0]?.period || "")} 至 ${esc(series.at(-1)?.period || "")}"><line x1="0" y1="190" x2="640" y2="190" stroke="#b9c9c5"/>${bars}</svg><div class="chart-axis"><span>${esc(series[0]?.period || "")}</span><span>${esc(series.at(-1)?.period || "")}</span></div></section>`;
  }
  async function analytics() {
    const {days, granularity} = analyticsState;
    const d = await api(`/admin/analytics?days=${days}&granularity=${granularity}`), s = d.summary || {}, list = d.series || [];
    $("page-summary").textContent = `${d.start_date || ""} 至 ${d.end_date || ""} · ${granularity === "day" ? "按日" : "按月"} · 北京时间`;
    $("page-content").innerHTML = `<div class="metric-grid analytics-metrics"><article><span>账户总数</span><strong>${Number(s.total_users || 0).toLocaleString("zh-CN")}</strong></article><article><span>期间新增用户</span><strong>${Number(s.new_users || 0).toLocaleString("zh-CN")}</strong></article><article><span>已支付充值</span><strong>¥${money(s.recharge_cny)}</strong></article><article><span>全站可用积分</span><strong>${Number(s.available_credits || 0).toLocaleString("zh-CN")}</strong></article></div><div class="analytics-charts">${chart("新增用户", list, "new_users", "#5269c9", " 人")}${chart("实际充值金额", list, "recharge_cny", "#19a890", " 元")}${chart("已支付订单", list, "paid_orders", "#d47a42", " 单")}</div><details class="analytics-detail"><summary>统计明细</summary><div class="admin-table-wrap"><table class="admin-table"><thead><tr><th>日期</th><th>新增用户</th><th>已支付订单</th><th>充值金额</th><th>充值积分</th></tr></thead><tbody>${list.map(x => `<tr><td>${esc(x.period)}</td><td>${Number(x.new_users || 0)}</td><td>${Number(x.paid_orders || 0)}</td><td>¥${money(x.recharge_cny)}</td><td>${Number(x.recharge_credits || 0).toLocaleString("zh-CN")}</td></tr>`).join("") || '<tr><td colspan="5" class="admin-empty">暂无统计数据</td></tr>'}</tbody></table></div></details>`;
    document.querySelectorAll(".analytics-controls [data-days]").forEach((button) => button.classList.toggle("selected", Number(button.dataset.days) === days));
    document.querySelectorAll(".analytics-controls [data-granularity]").forEach((button) => button.classList.toggle("selected", button.dataset.granularity === granularity));
  }
  async function orders() {
    const d = await listData("/admin/orders"), s = d.summary || {};
    const statuses = {pending:"待支付", paid:"已支付", expired:"已过期", refunded:"已退款", cancelled:"已取消"};
    $("page-summary").textContent = `共 ${d.total || 0} 条匹配订单 · 北京时间`;
    $("page-content").innerHTML = `<div class="metric-grid order-metrics"><article><span>匹配订单</span><strong>${Number(s.orders ?? d.total ?? 0).toLocaleString("zh-CN")}</strong></article><article><span>已支付订单</span><strong>${Number(s.paid_orders || 0).toLocaleString("zh-CN")}</strong></article><article><span>已支付金额</span><strong>¥${money(s.paid_amount_cny ?? s.paid_cny)}</strong></article><article><span>已支付积分</span><strong>${Number(s.paid_credits || 0).toLocaleString("zh-CN")}</strong></article></div><div class="admin-table-wrap"><table class="admin-table order-table"><thead><tr><th>订单 / 支付流水</th><th>账户</th><th>金额 / 积分</th><th>状态</th><th>支付渠道 / 商品</th><th>创建时间</th><th>支付时间</th></tr></thead><tbody>${(d.items || []).map(o => `<tr><td><strong>${esc(o.id)}</strong><small>${esc(o.provider_order_id || "—")}</small></td><td><strong>${esc(o.email || o.user_email || "—")}</strong><small>${esc(o.user_id)}</small></td><td class="order-value"><strong>¥${money(o.amount_cny ?? Number(o.amount_fen || 0) / 100)}</strong><small>${Number(o.credits || 0).toLocaleString("zh-CN")} 积分</small></td><td><span class="status-pill ${o.status === "paid" ? "status-online" : ""}">${esc(statuses[o.status] || o.status)}</span></td><td>${esc(o.provider)}<small>${esc(o.product_id)}</small></td><td>${date(o.created_at)}</td><td>${date(o.paid_at)}</td></tr>`).join("") || '<tr><td colspan="7" class="admin-empty">暂无匹配订单</td></tr>'}</tbody></table></div>`;
  }
  async function enrollment() { $("page-content").innerHTML = '<form id="enrollment-form" class="enrollment-form"><div class="enrollment-grid"><label class="admin-field"><span>Profile（可编辑）</span><input id="enrollment-profile-id" list="enrollment-profile-options" value="tts-gpu-worker-v1" required pattern="[a-z0-9-]{3,64}" /><datalist id="enrollment-profile-options"><option value="gpu-worker-v1"></option><option value="tts-gpu-worker-v1"></option><option value="cpu-worker-v1"></option><option value="onboarding-probe-v1"></option></datalist><small>计划运行 TTS 的机器建议使用 tts-gpu-worker-v1；它仍保留普通 GPU 资源。</small></label><label class="admin-field"><span>有效期（秒）</span><input id="enrollment-expires" type="number" min="60" max="3600" value="900" required /></label></div><button class="button primary" type="submit">生成 enrollment token</button></form><pre id="enrollment-result" class="admin-json enrollment-result" hidden></pre>'; try { const catalog=await api("/admin/enrollment/profiles"); const profiles=catalog?.payload?.profiles || catalog?.profiles || {}; const options=$("enrollment-profile-options"); Object.keys(profiles).forEach((profile) => { if (![...options.options].some((o) => o.value===profile)) { const option=document.createElement("option"); option.value=profile; options.append(option); } }); } catch (_) {} $("enrollment-form").addEventListener("submit", async (e) => { e.preventDefault(); try { const p={profile_id:$("enrollment-profile-id").value.trim(),expires_in_seconds:Number($("enrollment-expires").value)}; const d=await api("/admin/enrollment/tokens",{method:"POST",body:JSON.stringify(p)}); $("enrollment-result").textContent=`Profile：${p.profile_id}\n有效期至：${d.expires_at||"—"}\nEnrollment API：https://oneclickvidgen.com/ray-worker/api/enrollment/v1\nToken：${d.token||""}`; $("enrollment-result").hidden=false; message("已生成一次性 token，请立即复制。",true); } catch(err) { message(err.message); } }); }
  async function devices() { const d=await api("/admin/cluster/devices?limit=200"), items=d.items||[]; const notReported=items.filter(x=>!x.last_seen_at).length, rayOnline=items.filter(x=>x.ray_connected===true).length; $("page-summary").textContent=`共 ${d.total||0} 台设备 · Ray 在线 ${rayOnline} 台 · Agent heartbeat 未上报 ${notReported} 台`; $("page-content").innerHTML=`<div class="admin-callout">${esc(d.inventory_note||"节点尚未上报镜像/模型库存")}。</div><div class="admin-table-wrap"><table class="admin-table device-table"><thead><tr><th>设备</th><th>生命周期</th><th>连接</th><th>IP / Profile</th><th>Generation</th><th>Peer revision</th><th>最后心跳</th><th>操作</th></tr></thead><tbody>${items.map(x=>`<tr><td class="device-identity"><strong title="${esc(x.device_id)}">${esc(x.device_id)}</strong><small>${esc(x.profile_name||x.profile_id)}</small></td><td><span class="status-pill status-${String(x.lifecycle_state||'').toLowerCase()}">${esc(x.lifecycle_state)}</span></td><td title="${esc(x.connectivity_reason||'')}">${connectivity(x)}</td><td class="device-network"><code>${esc(x.ray_node_ip||"—")}</code><small>${esc(x.profile_id)}</small></td><td>${esc(x.generation)}</td><td>${esc(x.applied_revision)} / ${esc(x.desired_revision)} <small>${esc(x.peer_status)}</small></td><td>${heartbeat(x)}</td><td><a class="table-button" href="/admin/device.html?id=${encodeURIComponent(x.device_id)}">管理</a></td></tr>`).join("")||'<tr><td colspan="8" class="admin-empty">暂无设备记录</td></tr>'}</tbody></table></div>`; }
  async function deviceDetail() { const id=new URLSearchParams(location.search).get("id"); if(!id){message("缺少设备 ID");return;} const x=await api(`/admin/cluster/devices/${encodeURIComponent(id)}`); $("page-summary").textContent=`${x.device_id} · 设备管理`; $("page-content").innerHTML=`<div class="detail-actions"><button class="button secondary" data-action="disable">停用并移出调度</button><button class="button secondary" data-action="rekey">生成下一代 rekey token</button><button class="button secondary" data-action="refresh">刷新状态</button></div><div class="detail-grid"><article><span>生命周期</span><strong>${esc(x.lifecycle_state)}</strong></article><article><span>连接状态</span><strong>${connectivity(x)}<small class="detail-hint">${esc(x.connectivity_reason||"")}</small></strong></article><article><span>Ray 节点 IP（预留修改）</span><strong><code>${esc(x.ray_node_ip||"—")}</code></strong></article><article><span>Profile（预留修改）</span><strong>${esc(x.profile_id)}</strong></article><article><span>设备 ID</span><strong>${esc(x.device_id)}</strong></article><article><span>Generation</span><strong>${esc(x.generation)}</strong></article><article><span>Peer revision</span><strong>${esc(x.applied_revision)} / ${esc(x.desired_revision)}</strong></article><article><span>最后 heartbeat</span><strong>${heartbeat(x)}</strong></article><article><span>凭据到期</span><strong>${date(x.credential_expires_at)}</strong></article></div><section class="inventory-section"><h2>镜像与模型</h2><p class="admin-callout">${esc(x.inventory_reason||"节点尚未上报镜像/模型库存")}。库存上报协议接通后，这里会提供查看、拉取和停用操作。</p><div class="inventory-empty"><span>镜像：未上报</span><span>模型：未上报</span></div></section><section class="readonly-note"><strong>IP / Profile / 设备 ID 修改入口已预留</strong><span>当前控制面没有安全迁移接口，按钮不会直接改数据库；待确定 WireGuard 重配、凭据轮换和审计流程后再开放。</span></section>`; const act=async(kind)=>{if(kind==="refresh"){await renderCurrent();return;}const reason=prompt("请输入操作原因（写入审计）");if(!reason||reason.trim().length<3)return;const key=`admin-device-${kind}-${id}-${Date.now()}`;try{const path=kind==="disable"?`/admin/cluster/devices/${encodeURIComponent(id)}/disable`:`/admin/cluster/devices/${encodeURIComponent(id)}/rekey-token`;const r=await api(path,{method:"POST",headers:{"Idempotency-Key":key},body:JSON.stringify({reason:reason.trim(),idempotency_key:key})});message(kind==="disable"?"设备已停用并移出调度。":"已生成新 token，请立即复制。",true);if(kind!=="disable")alert(JSON.stringify(r,null,2));await deviceDetail();}catch(e){message(e.message);}}; document.querySelector('[data-action="disable"]').onclick=()=>act("disable"); document.querySelector('[data-action="rekey"]').onclick=()=>act("rekey"); document.querySelector('[data-action="refresh"]').onclick=()=>act("refresh"); }
  async function models() { const d=await api("/admin/cluster/model-releases"),items=d.items||[]; $("page-summary").textContent=`${items.length} 个发布版本 · 启用状态控制模型下载授权`; $("page-content").innerHTML=`<div class="admin-callout">启用/停用只影响新模型下载授权，不会远程修改节点文件。</div><div class="admin-table-wrap"><table class="admin-table"><thead><tr><th>模型</th><th>版本</th><th>状态</th><th>发布时间</th><th>操作</th></tr></thead><tbody>${items.map(x=>`<tr><td><strong>${esc(x.model_id)}</strong></td><td><code>${esc(x.version)}</code></td><td><span class="status-pill ${x.enabled?"status-online":"status-disabled"}">${x.enabled?"已启用":"已停用"}</span></td><td>${date(x.published_at)}</td><td><button class="table-button" data-model-action="${x.enabled?"disable":"enable"}" data-model-id="${esc(x.model_id)}" data-model-version="${esc(x.version)}">${x.enabled?"停用":"启用"}</button></td></tr>`).join("")||'<tr><td colspan="5" class="admin-empty">暂无发布版本</td></tr>'}</tbody></table></div>`; document.querySelectorAll("[data-model-action]").forEach(b=>b.onclick=async()=>{const reason=prompt("请输入操作原因（写入审计）");if(!reason||reason.trim().length<3)return;const key=`admin-model-${b.dataset.modelAction}-${Date.now()}`;try{await api(`/admin/cluster/model-releases/${encodeURIComponent(b.dataset.modelId)}/${encodeURIComponent(b.dataset.modelVersion)}/${b.dataset.modelAction}`,{method:"POST",headers:{"Idempotency-Key":key},body:JSON.stringify({reason:reason.trim(),idempotency_key:key})});message("模型发布状态已更新。",true);await models();}catch(e){message(e.message);}}); }
  function installControls() {
    const toolbar = document.createElement("div");
    toolbar.className = "list-toolbar";
    toolbar.innerHTML = page === "analytics" ? '<div class="analytics-controls" role="group" aria-label="统计范围"><span>范围</span><button type="button" class="table-button" data-days="7">7 天</button><button type="button" class="table-button" data-days="30">30 天</button><button type="button" class="table-button" data-days="90">90 天</button><button type="button" class="table-button" data-days="365">365 天</button><span class="analytics-separator"></span><button type="button" class="table-button" data-granularity="day">按日</button><button type="button" class="table-button" data-granularity="month">按月</button></div><button id="page-reload" class="table-button" type="button">刷新</button><span id="page-updated" aria-live="polite"></span>' : `<form id="list-filter" class="list-filter">${paged ? '<input id="list-query" type="search" aria-label="搜索" placeholder="搜索账户、ID 或操作" maxlength="160"><button class="table-button" type="submit">搜索</button><button class="table-button" type="reset">重置</button>' : ''}${page === "users" ? '<select id="list-source" aria-label="用户来源"><option value="">全部来源</option><option value="1">B站</option><option value="2">二维码</option></select><select id="list-status" aria-label="账户状态"><option value="">全部状态</option><option value="active">正常</option><option value="disabled">已停用</option><option value="pending">待激活</option></select>' : ''}${page === "jobs" ? '<div class="job-kind-controls" role="group" aria-label="任务类型"><button type="button" class="table-button" data-job-kind="tts">TTS</button><button type="button" class="table-button" data-job-kind="video">视频</button></div><select id="list-status" aria-label="任务状态"><option value="">全部状态</option><option value="created">已创建</option><option value="queued">排队中</option><option value="running">运行中</option><option value="finalizing">收尾中</option><option value="storyboarding">分镜中</option><option value="generating_assets">生成素材</option><option value="composing">合成中</option><option value="publishing">发布中</option><option value="completed">已完成</option><option value="failed">失败</option><option value="cancel_requested">等待取消</option><option value="cancelled">已取消</option><option value="expired">已过期</option></select>' : ''}${page === "orders" ? '<select id="list-status" aria-label="订单状态"><option value="">全部状态</option><option value="pending">待支付</option><option value="paid">已支付</option><option value="expired">已过期</option><option value="refunded">已退款</option><option value="cancelled">已取消</option></select><input id="list-start-date" type="date" aria-label="开始日期"><input id="list-end-date" type="date" aria-label="结束日期">' : ''}</form><button id="page-reload" class="table-button" type="button">刷新</button><span id="page-updated" aria-live="polite"></span>`;
    $("page-content").before(toolbar);
    const filter = $("list-filter");
    if (filter) filter.onsubmit = (event) => {
      event.preventDefault();
      listState.q = $("list-query")?.value.trim() || "";
      listState.source = $("list-source")?.value || "";
      listState.status = $("list-status")?.value || "";
      listState.kind = toolbar.querySelector("[data-job-kind].selected")?.dataset.jobKind || listState.kind;
      listState.startDate = $("list-start-date")?.value || "";
      listState.endDate = $("list-end-date")?.value || "";
      listState.page = 1;
      renderCurrent();
    };
    if (filter) filter.onreset = () => {
      Object.assign(listState, {page:1, q:"", source:"", status:"", startDate:"", endDate:"", kind:"tts"});
      renderCurrent();
    };
    toolbar.querySelectorAll("select,input[type=date]").forEach(el => el.onchange = () => $("list-filter").requestSubmit());
    $("page-reload").onclick = renderCurrent;
    if (page === "jobs") {
      toolbar.querySelectorAll("[data-job-kind]").forEach((button) => { button.classList.toggle("selected", button.dataset.jobKind === listState.kind); button.onclick = () => { listState.kind = button.dataset.jobKind; listState.page = 1; toolbar.querySelectorAll("[data-job-kind]").forEach((x) => x.classList.toggle("selected", x === button)); renderCurrent(); }; });
    }
    if (page === "analytics") {
      toolbar.querySelectorAll("[data-days]").forEach((button) => button.onclick = () => { analyticsState.days = Number(button.dataset.days); renderCurrent(); });
      toolbar.querySelectorAll("[data-granularity]").forEach((button) => button.onclick = () => { analyticsState.granularity = button.dataset.granularity; renderCurrent(); });
    }
    if (paged) {
      const nav = document.createElement("nav"); nav.className = "list-pagination"; nav.setAttribute("aria-label", "列表分页");
      nav.innerHTML = '<button id="list-prev" class="table-button" type="button">上一页</button><span id="list-count" aria-live="polite"></span><button id="list-next" class="table-button" type="button">下一页</button><select id="list-size" aria-label="每页条数"><option value="20">20 条 / 页</option><option value="50">50 条 / 页</option><option value="100">100 条 / 页</option></select>';
      $("page-content").after(nav);
      $("list-prev").onclick = () => { listState.page--; renderCurrent(); };
      $("list-next").onclick = () => { listState.page++; renderCurrent(); };
      $("list-size").onchange = () => { listState.size = Number($("list-size").value); listState.page = 1; renderCurrent(); };
    }
  }
  async function userState(button) {
    const label = button.dataset.userState === "enable" ? "恢复" : "停用";
    if (!confirm(`确认${label}账户 ${button.dataset.userEmail}？`)) return;
    button.disabled = true;
    try {
      await api(`/admin/users/${encodeURIComponent(button.dataset.userId)}/${button.dataset.userState}`, {method:"POST"});
      await renderCurrent();
      message(`账户已${label}`, true);
    } catch (error) { message(error.message); button.disabled = false; }
  }
  async function quotaDialog(id, email) {
    try {
      const quota = await api(`/admin/users/${encodeURIComponent(id)}/quota`);
      let dialog = $("quota-dialog");
      if (!dialog) { dialog = document.createElement("dialog"); dialog.id="quota-dialog"; dialog.className="admin-dialog"; document.body.append(dialog); }
      dialog.innerHTML = `<form class="admin-dialog-inner"><h2>任务额度</h2><p>${esc(email)}</p>${[["max_concurrent_jobs","最大并发任务",100],["max_queue_jobs","最大排队任务",1000],["daily_characters_limit","每日字符额度",10000000]].map(([key,label,max]) => `<label class="admin-field"><span>${label}</span><input name="${key}" type="number" min="0" max="${max}" step="1" value="${quota[key]}" required></label>`).join("")}<p class="form-error" role="alert"></p><div class="admin-dialog-actions"><button type="button" class="button secondary">取消</button><button type="submit" class="button primary">保存额度</button></div></form>`;
      dialog.querySelector('button[type="button"]').onclick = () => dialog.close();
      dialog.querySelector("form").onsubmit = async (event) => {
        event.preventDefault();
        const submit = dialog.querySelector('button[type="submit"]');
        if (submit.disabled) return;
        submit.disabled = true;
        try {
          const payload = Object.fromEntries([...new FormData(event.currentTarget)].map(([key,value]) => [key,Number(value)]));
          await api(`/admin/users/${encodeURIComponent(id)}/quota`, {method:"POST",body:JSON.stringify(payload)});
          dialog.close(); message("任务额度已更新", true);
        } catch (error) { dialog.querySelector(".form-error").textContent = error.message; }
        finally { submit.disabled = false; }
      };
      dialog.showModal();
    } catch (error) { message(error.message); }
  }
  async function listData(path) {
    const params = new URLSearchParams({page:listState.page, page_size:listState.size, q:listState.q});
    if (listState.source) params.set("source", listState.source);
    if (listState.status) params.set(page === "users" ? "user_status" : page === "jobs" ? "job_status" : "order_status", listState.status);
    if (page === "orders") {
      if (listState.startDate) params.set("date_from", listState.startDate);
      if (listState.endDate) params.set("date_to", listState.endDate);
    }
    if (page === "jobs") params.set("kind", listState.kind);
    const d = await api(`${path}?${params}`);
    listState.total = d.total || 0;
    if ($("list-count")) {
      const pages = Math.max(1, Math.ceil(listState.total / listState.size));
      $("list-count").textContent = `第 ${listState.page} / ${pages} 页 · 共 ${listState.total} 条`;
      $("list-prev").disabled = listState.page <= 1;
      $("list-next").disabled = listState.page >= pages;
    }
    return d;
  }
  async function renderCurrent() {
    if (loading) return;
    loading = true;
    document.querySelectorAll(".list-toolbar button,.list-toolbar input,.list-toolbar select,.list-pagination button,.list-pagination select").forEach(el => el.disabled = true);
    $("page-content").setAttribute("aria-busy", "true");
    message("");
    try {
      await ({users, jobs, orders, cluster, audit, analytics, enrollment, devices, device:deviceDetail, models}[page])();
      $("page-updated").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN")}`;
    } catch (error) { message(error.message); }
    finally {
      loading = false;
      $("page-content").removeAttribute("aria-busy");
      document.querySelectorAll(".list-toolbar button,.list-toolbar input,.list-toolbar select,.list-pagination select").forEach(el => el.disabled = false);
      if (paged) {
        $("list-prev").disabled = listState.page <= 1;
        $("list-next").disabled = listState.page * listState.size >= listState.total;
      }
    }
  }
  if (wireShell()) { installControls(); renderCurrent(); }
}());
