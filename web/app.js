const state = { tenantId: null, bootstrap: null, contributions: [] };
const $ = (selector) => document.querySelector(selector);

function setNotice(message, error = false) {
  const node = $("#notice"); node.textContent = message; node.classList.toggle("error", error);
}
function setConnection(message, mode = "") {
  const node = $(".connection"); node.className = `connection ${mode}`; $("#connection-status").textContent = message;
}
async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.tenantId) headers.set("X-Tenant-Id", state.tenantId);
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail?.message || payload.detail || `请求失败 (${response.status})`);
  return payload;
}
function renderOverview() {
  const bootstrap = state.bootstrap;
  if (!bootstrap) return;
  const cards = [
    ["控制平面", bootstrap.api_version, `Shell ${bootstrap.shell_version}`],
    ["安全 GUI 插槽", String(bootstrap.slots.length), "声明式扩展点"],
    ["已启用插件贡献", String(state.contributions.length), "受模式验证保护"],
    ["策略网关", bootstrap.features.policy_gated_actions ? "已启用" : "不可用", "工具调用强制经过策略"],
  ];
  $("#overview-cards").innerHTML = cards.map(([label, value, note]) => `<article class="card"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");
}
function renderPlugins() {
  const target = $("#plugin-list");
  if (!state.contributions.length) { target.textContent = "暂无已启用插件贡献。插件注册后会在这里显示受控导航与视图数量。"; return; }
  target.innerHTML = `<table><thead><tr><th>插件</th><th>版本</th><th>导航</th><th>视图</th><th>动作</th></tr></thead><tbody>${state.contributions.map((item) => `<tr><td>${item.plugin_id}</td><td>${item.plugin_version}</td><td>${item.navigation_count}</td><td>${item.view_count}</td><td>${item.action_count}</td></tr>`).join("")}</tbody></table>`;
}
function renderApprovals(items) {
  const target = $("#approval-list");
  if (!items.length) { target.innerHTML = '<tr><td colspan="4">当前没有审批记录。</td></tr>'; return; }
  target.innerHTML = items.map((item) => `<tr><td>${item.capability}</td><td>${item.risk_class}</td><td><span class="status ${item.status}">${item.status}</span></td><td>${new Date(item.created_at).toLocaleString()}</td></tr>`).join("");
}
async function loadApprovals() {
  if (!state.tenantId) return;
  const payload = await request("/api/v1/approvals"); renderApprovals(payload.items);
}
async function loadWorkspace() {
  const slug = $("#tenant-slug").value.trim() || "local-dev";
  $("#connect-tenant").disabled = true; setNotice("正在加载租户上下文与 GUI 契约…");
  try {
    const tenant = await request("/api/v1/ui/tenant-context", { headers: { "X-Tenant-Slug": slug } });
    state.tenantId = tenant.tenant_id;
    $("#tenant-context").textContent = `${tenant.tenant_slug} · ${tenant.tenant_id}`;
    const [bootstrap, contributions] = await Promise.all([request("/api/v1/ui/bootstrap"), request("/api/v1/ui/contributions")]);
    state.bootstrap = bootstrap; state.contributions = contributions.items; renderOverview(); renderPlugins(); await loadApprovals();
    setConnection("控制平面已连接", "online"); setNotice("工作区已加载。所有可执行操作仍会经过策略网关。");
  } catch (error) { setConnection("控制平面不可用", "error"); setNotice(error instanceof Error ? error.message : "加载失败", true); }
  finally { $("#connect-tenant").disabled = false; }
}
document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => { document.querySelectorAll(".tab,.view").forEach((node) => node.classList.remove("active")); tab.classList.add("active"); $(`#${tab.dataset.view}`).classList.add("active"); }));
$("#connect-tenant").addEventListener("click", loadWorkspace);
$("#refresh-approvals").addEventListener("click", () => loadApprovals().catch((error) => setNotice(error.message, true)));
$("#knowledge-upload").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.tenantId) { setNotice("请先加载工作区。", true); return; }
  const files = Array.from($("#knowledge-files").files || []);
  const output = $("#knowledge-result");
  if (!files.length) { output.textContent = "请选择至少一个文件。"; return; }
  const body = new FormData();
  files.forEach((file) => { body.append("files", file, file.name); body.append("relative_paths", file.webkitRelativePath || file.name); });
  output.textContent = `正在登记 ${files.length} 个文件…`;
  try {
    const result = await request("/api/v1/knowledge/upload", { method: "POST", body });
    output.textContent = JSON.stringify(result, null, 2);
    setNotice(`知识任务已创建：${result.accepted} 个已解析，${result.deferred} 个等待本地提取器。`);
  } catch (error) { output.textContent = `导入失败：${error instanceof Error ? error.message : "未知错误"}`; }
});
$("#policy-form").addEventListener("submit", async (event) => { event.preventDefault(); if (!state.tenantId) { setNotice("请先加载工作区。", true); return; } const form = new FormData(event.currentTarget); const output = $("#simulation-result"); try { const argumentsValue = JSON.parse(String(form.get("arguments"))); const result = await request("/api/v1/policy/simulate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ capability: form.get("capability"), risk_class: form.get("risk_class"), side_effects: form.get("side_effects"), arguments: argumentsValue }) }); output.textContent = JSON.stringify(result, null, 2); } catch (error) { output.textContent = `模拟失败：${error instanceof Error ? error.message : "未知错误"}`; } });
loadWorkspace();
