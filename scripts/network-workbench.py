"""Local workbench: compose a draft, look at it, revise it, validate it.

用法::

    .\\.venv\\Scripts\\python.exe scripts\\network-workbench.py --domain audit
    .\\.venv\\Scripts\\python.exe scripts\\network-workbench.py --domain aiops --port 8771 --no-open

打开 http://127.0.0.1:8770 就是那个回路：**第一稿 → 看 → 改（自己改或让 AI 改）→ 跑契约校验**。

为什么是一个独立的小应用而不是接进主 API：这个回路只需要契约目录 + 编译器，
不需要数据库、不需要启动整套服务。它绑在 127.0.0.1，只服务本机。

它自己**不判断对错**：校验一律调 ``compile_plan``（工作台的唯一权威），
渲染一律调 ``packages/ai_planner/render.py``（与静态渲染器同一份布局代码）。

写操作只有两个：内存里的当前草稿，以及 ``.data/drafts/<name>.json``。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from packages.ai_planner.domain_pack import available_domains, load_pack  # noqa: E402
from packages.ai_planner.render import build_view, svg_fragment  # noqa: E402
from packages.ai_planner.roles import resolve_all  # noqa: E402
from packages.ai_planner.semantics import load_semantics  # noqa: E402
from packages.ai_planner.workbench import (  # noqa: E402
    apply_edit,
    list_drafts,
    load_draft,
    new_draft,
    reusable_suggestions,
    save_draft,
    validate_draft_full,
)

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"domain": "audit", "goal": "全流程", "draft": None}


# --------------------------------------------------------------------------
# request models
# --------------------------------------------------------------------------

class ComposeRequest(BaseModel):
    domain: str = "audit"
    goal: str = "全流程"


class EditRequest(BaseModel):
    edit: dict[str, Any]


class ReviseRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []


class SaveRequest(BaseModel):
    name: str


class LoadRequest(BaseModel):
    name: str


# --------------------------------------------------------------------------
# the API
# --------------------------------------------------------------------------

def _current() -> dict[str, Any]:
    draft = _STATE.get("draft")
    if draft is None:
        _STATE["draft"] = new_draft(str(_STATE["domain"]), str(_STATE["goal"]))
    result: dict[str, Any] = _STATE["draft"]
    return result


def _payload() -> dict[str, Any]:
    draft = _current()
    report = validate_draft_full(draft)
    domain = str(draft.get("domain") or _STATE["domain"])
    view = build_view(draft, domain=domain)
    svg, width, height = svg_fragment(view, interactive=True)
    return {
        "domain": domain,
        "goal": str(draft.get("goal") or ""),
        "plan_key": str(draft.get("plan_key") or ""),
        "origin": draft.get("origin") or {},
        "validation": report.as_dict(),
        "view": view,
        "svg": svg,
        "width": width,
        "height": height,
        "suggestions": draft.get("suggestions", {}).get("reusable_plugins", []),
        "drafts": list_drafts(),
    }


def create_app() -> FastAPI:
    app = FastAPI(title="组网工作台", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _PAGE.replace("__BOOTSTRAP__", json.dumps(_payload(), ensure_ascii=False))

    @app.get("/api/draft")
    def get_draft() -> dict[str, Any]:
        return _payload()

    @app.post("/api/compose")
    def compose(req: ComposeRequest) -> dict[str, Any]:
        if req.domain not in available_domains():
            raise HTTPException(status_code=422, detail=f"unknown domain {req.domain!r}")
        with _LOCK:
            _STATE["domain"], _STATE["goal"] = req.domain, req.goal
            _STATE["draft"] = new_draft(req.domain, req.goal)
        return _payload()

    @app.post("/api/validate")
    def validate() -> dict[str, Any]:
        return {"validation": validate_draft_full(_current()).as_dict()}

    @app.post("/api/edit")
    def edit(req: EditRequest) -> dict[str, Any]:
        with _LOCK:
            result = apply_edit(_current(), req.edit).as_dict()
            if result["ok"] and result["draft"] is not None:
                _STATE["draft"] = result["draft"]
            return {**result, "state": _payload() if result["ok"] else None}

    @app.get("/api/suggestions")
    def suggestions() -> dict[str, Any]:
        draft = _current()
        domain = str(draft.get("domain") or "audit")
        specs = dict(draft.get("suggestions", {}))
        if not specs:
            return {"suggestions": reusable_suggestions(draft, domain)}
        return {"suggestions": specs.get("reusable_plugins", [])}

    @app.get("/api/plugins")
    def plugins() -> dict[str, Any]:
        """Every plugin in the domain, grouped by stage — for the add-node picker."""
        from packages.ai_planner.composer import discover_plugins

        draft = _current()
        domain = str(draft.get("domain") or "audit")
        pack = load_pack(domain)
        specs = discover_plugins(globs=pack.globs)
        roles = resolve_all(specs, catalog=load_semantics())
        groups: dict[str, list[dict[str, str]]] = {}
        for plugin_id in sorted(specs):
            stage = pack.stage_of(plugin_id) or pack.layer_of(plugin_id)
            groups.setdefault(stage, []).append({
                "plugin_id": plugin_id,
                "label": plugin_id.split(".")[-1],
                "role": roles[plugin_id].role if plugin_id in roles else "review",
            })
        order = list(pack.stage_order) + [x for x in ("base", "gov") if x in groups]
        names = {**pack.stage_name, "base": "数据支撑层", "biz": "核心业务循环层", "gov": "治理优化层"}
        return {
            "groups": [
                {"stage": s, "name": names.get(s, s), "plugins": groups[s]}
                for s in order if s in groups
            ]
        }

    @app.post("/api/revise")
    def revise(req: ReviseRequest) -> dict[str, Any]:
        from packages.ai_planner.workbench import domain_catalog, revise_with_ai

        with _LOCK:
            draft = _current()
            # The model can only reference ports it was shown, and the validator
            # only accepts ports in the catalog it was given — so the catalog
            # must be derived from *this draft's* domain, not left empty.
            result = revise_with_ai(
                draft, req.message, history=req.history,
                catalog=domain_catalog(str(draft.get("domain") or "audit")),
            )
            if result["ok"] and result["draft"] is not None:
                _STATE["draft"] = result["draft"]
                return {**result, "state": _payload()}
            if any(i["code"] == "model_unavailable" for i in result["issues"]):
                raise HTTPException(status_code=503, detail=result["issues"])
            return {**result, "state": None}

    @app.post("/api/save")
    def save(req: SaveRequest) -> dict[str, Any]:
        path = save_draft(req.name, _current())
        return {"saved": str(path), "drafts": list_drafts()}

    @app.post("/api/load")
    def load(req: LoadRequest) -> dict[str, Any]:
        with _LOCK:
            _STATE["draft"] = load_draft(req.name)
        return _payload()

    return app


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------

_PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>组网工作台</title>
<style>
  :root{
    color-scheme:light;
    --surface-1:#fcfcfb; --plane:#f4f3f0; --rule:#e6e5e1;
    --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#8a8a7f;
    --layer-base:#eb6834; --layer-biz:#2a78d6; --layer-gov:#1baf7a;
    --good:#1baf7a; --bad:#e34948;
  }
  @media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){
    color-scheme:dark;
    --surface-1:#1a1a19; --plane:#141413; --rule:#33322f;
    --text-primary:#fff; --text-secondary:#c3c2b7; --text-muted:#8a8a7f;
    --layer-base:#d95926; --layer-biz:#3987e5; --layer-gov:#199e70;
    --good:#199e70; --bad:#e66767;
  }}
  *{box-sizing:border-box}
  body{margin:0;background:var(--plane);color:var(--text-primary);
       font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  header{padding:16px 20px 12px;border-bottom:1px solid var(--rule);
         display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  h1{font-size:16px;margin:0;font-weight:600}
  .muted{color:var(--text-secondary);font-size:12.5px}
  .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  select,input[type=text],textarea{background:var(--surface-1);color:var(--text-primary);
       border:1px solid var(--rule);border-radius:8px;padding:6px 9px;font:inherit}
  textarea{width:100%;min-height:66px;resize:vertical}
  button{background:var(--surface-1);color:var(--text-primary);border:1px solid var(--rule);
       border-radius:8px;padding:6px 12px;font:inherit;cursor:pointer}
  button:hover{border-color:var(--text-muted)}
  button.primary{background:var(--layer-biz);border-color:var(--layer-biz);color:#fff}
  main{display:grid;grid-template-columns:1fr 380px;gap:0;align-items:start}
  .canvas{padding:16px 20px;overflow:auto}
  aside{border-left:1px solid var(--rule);padding:16px;max-height:calc(100vh - 66px);
        overflow:auto}
  .card{background:var(--surface-1);border:1px solid var(--rule);border-radius:12px;
        padding:12px 14px;margin-bottom:14px}
  .card h2{font-size:13px;margin:0 0 8px;font-weight:600;color:var(--text-secondary)}
  .tiles{display:flex;gap:10px;flex-wrap:wrap}
  .tile{background:var(--surface-1);border:1px solid var(--rule);border-radius:10px;
        padding:8px 12px;min-width:88px}
  .tile .k{color:var(--text-secondary);font-size:11.5px}
  .tile .v{font-size:19px;font-weight:600}
  .ok{color:var(--good);font-weight:600}
  .bad{color:var(--bad);font-weight:600}
  svg{display:block}
  .colhead{fill:var(--text-primary);font-size:13px;font-weight:600}
  .colcount{fill:var(--text-muted);font-weight:400}
  .colrule{stroke:var(--rule);stroke-width:1}
  .nlabel{fill:var(--text-primary);font-size:12px}
  .nrole{fill:var(--text-secondary);font-size:10px}
  .dot-base{fill:var(--layer-base)} .dot-biz{fill:var(--layer-biz)} .dot-gov{fill:var(--layer-gov)}
  .dot-island{fill:var(--surface-1);stroke:var(--text-muted);stroke-width:2;stroke-dasharray:2 2}
  .edge{fill:none;stroke-width:1.6;opacity:.75;stroke:var(--text-muted)}
  .edge-data{stroke:var(--text-muted)}
  .edge-call{stroke-dasharray:4 3;opacity:.42}
  .hit{fill:transparent;cursor:pointer}
  .hit:hover{fill:rgba(42,120,214,.10)}
  .legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--text-secondary);
          margin-bottom:8px}
  .key{display:inline-flex;align-items:center;gap:6px}
  .sw{width:10px;height:10px;border-radius:50%;display:inline-block}
  .l-base{background:var(--layer-base)} .l-biz{background:var(--layer-biz)}
  .l-gov{background:var(--layer-gov)}
  .issue{border-left:3px solid var(--bad);padding:6px 10px;margin-bottom:8px;
         background:color-mix(in srgb,var(--bad) 7%,transparent);border-radius:0 8px 8px 0}
  .issue .code{font-weight:600;font-size:12px}
  .issue .msg{font-size:12px;color:var(--text-secondary)}
  .sug{border:1px solid var(--rule);border-radius:8px;padding:8px 10px;margin-bottom:8px;font-size:12.5px}
  .sug .pid{font-weight:600}
  .coarse{border-color:var(--layer-base)}
  table{border-collapse:collapse;width:100%;font-size:12px}
  th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--rule)}
  th{color:var(--text-secondary)}
  details summary{cursor:pointer;color:var(--text-secondary);margin-bottom:8px}
  .pill{display:inline-block;border:1px solid var(--rule);border-radius:999px;
        padding:1px 8px;font-size:11.5px;color:var(--text-secondary)}
</style></head>
<body>
<header>
  <h1>组网工作台</h1>
  <div class="row">
    <label class="muted">领域
      <select id="domain"></select>
    </label>
    <label class="muted">目标 <input type="text" id="goal" size="20"></label>
    <button class="primary" onclick="compose()">出第一稿</button>
    <button onclick="validate()">跑契约校验</button>
    <button onclick="save()">存草稿</button>
  </div>
  <div class="muted" id="origin"></div>
</header>
<main>
  <div class="canvas">
    <div class="legend">
      <span class="key"><i class="sw l-biz"></i>业务循环层</span>
      <span class="key"><i class="sw l-base"></i>数据支撑层</span>
      <span class="key"><i class="sw l-gov"></i>治理优化层</span>
      <span class="key">实线=data 边</span>
      <span class="key">虚线=call 边</span>
      <span class="key">虚线圆=孤岛</span>
    </div>
    <div id="graph"></div>
  </div>
  <aside>
    <div class="card"><h2>契约校验</h2><div id="validation"></div></div>
    <div class="card"><h2>加节点</h2>
      <div class="row">
        <select id="addStage" onchange="fillAddPlugins()"></select>
        <select id="addPlugin"></select>
        <button onclick="addNode()">加入</button>
      </div>
      <div class="muted" id="addNote" style="margin-top:6px"></div>
    </div>
    <div class="card"><h2>连边</h2>
      <div class="row"><span class="muted">源</span>
        <select id="edgeSrc" onchange="fillSrcPorts()"></select>
        <select id="edgeSrcPort"></select></div>
      <div class="row" style="margin-top:6px"><span class="muted">目标</span>
        <select id="edgeDst" onchange="fillDstPorts()"></select>
        <select id="edgeDstPort"></select></div>
      <div class="row" style="margin-top:6px"><span class="muted">适配器（schema 不同才需要）</span>
        <input type="text" id="edgeAdapter" size="18" placeholder="已注册的 adapter 名"></div>
      <div class="row" style="margin-top:8px"><button onclick="addEdge()">连上</button>
        <span class="muted" id="edgeNote"></span></div>
    </div>
    <div class="card"><h2>让 AI 改</h2>
      <textarea id="feedback" placeholder="例如：把校验节点插到财务清洗之后"></textarea>
      <div class="row" style="margin-top:8px"><button onclick="revise()">提交给 AI</button>
        <span class="muted" id="reviseNote"></span></div>
    </div>
    <div class="card"><h2>复用建议（只建议，不自动连）</h2><div id="suggestions"></div></div>
    <div class="card"><h2>选中</h2><div id="selection" class="muted">点图上的节点或连线</div></div>
    <div class="card"><h2>表格视图</h2><details><summary>展开</summary><div id="table"></div></details></div>
  </aside>
</main>
<script>
const BOOT = __BOOTSTRAP__;
let STATE = BOOT;

function esc(s){return String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}

function tiles(v){return [
  ["插件",v.stats.nodes],["边",v.stats.edges],["data",v.stats.data_edges],
  ["call",v.stats.call_edges],["种子",v.stats.seeds],["孤岛",v.stats.islands],
].map(([k,x])=>`<div class="tile"><div class="k">${k}</div><div class="v">${x}</div></div>`).join("");}

function render(){
  const v = STATE.view, val = STATE.validation;
  document.getElementById("graph").innerHTML =
    `<div class="tiles" style="margin-bottom:12px">${tiles(v)}</div>`
    + `<div class="card"><svg viewBox="0 0 ${STATE.width} ${STATE.height}" width="${STATE.width}" height="${STATE.height}">${STATE.svg}</svg></div>`;
  document.getElementById("validation").innerHTML = val.ok
    ? `<div class="ok">✔ 通过</div><div class="muted">execution_hash <code>${esc(val.execution_hash.slice(0,32))}…</code><br>`
      + `节点 ${val.nodes} · 边 ${val.edges} · 种子 ${val.seeds}</div>`
    : val.issues.map(i=>`<div class="issue"><div class="code">${esc(i.code)}</div>`
      + `<div class="msg">${esc(i.message)}</div>`
      + `<div class="msg">→ ${esc(i.suggested_action)}</div></div>`).join("");
  document.getElementById("suggestions").innerHTML = (STATE.suggestions||[]).map(s=>
    `<div class="sug ${s.coarse_schema?"coarse":""}"><div class="pid">${esc(s.plugin_id)}</div>`
    + `<div class="muted">角色 ${esc(s.role)} · 输入 <code>${esc(s.input_contract)}</code>`
    + ` · 候选 ${s.candidate_count}${s.coarse_schema?" · <b>粗粒度 schema，需人工判断</b>":""}</div>`
    + s.candidates.slice(0,4).map((c,ci)=>
        `<div class="muted">候选 ${ci+1}：<code>${esc(c.node)}.${esc(c.port)}</code>`
        + ` <button onclick="insertHere(${esc(JSON.stringify(s.plugin_id))},${esc(JSON.stringify(c.node))},`
        + `${esc(JSON.stringify(c.port))},${esc(JSON.stringify(s.input_contract))})">在这里插一个</button></div>`)
      .join("") + `</div>`).join("")
    || `<div class="muted">这个领域没有校验/外壳/服务类复用件</div>`;
  document.getElementById("origin").innerHTML =
    `plan_key <code>${esc(STATE.plan_key)}</code> · 编辑者 ${esc((STATE.origin.edited_by||[]).join(" → "))}`;
  fetch("/api/plugins").then(r=>r.json()).then(d=>{
    const t=document.getElementById("table");
    t.innerHTML = `<table><thead><tr><th>插件</th><th>阶段</th><th>角色</th></tr></thead><tbody>`
      + v.nodes.map(n=>`<tr><td>${esc(n.plugin_id)}</td><td>${esc(n.stage_name)}</td><td>${esc(n.role_zh)}</td></tr>`).join("")
      + `</tbody></table>`;
  });
  document.querySelectorAll("[data-node-id]").forEach(el=>{
    el.addEventListener("click",()=>{
      const n = v.nodes.find(x=>x.node_instance_id===el.dataset.nodeId);
      if(!n) return;
      document.getElementById("selection").innerHTML =
        `<b>${esc(n.node_instance_id)}</b><br>插件 ${esc(n.plugin_id)}<br>角色 ${esc(n.role_zh)}`
        + `<br>阶段 ${esc(n.stage_name)}`
        + `<br>入端口 ${n.input_ports.map(p=>esc(p.port_id)).join(", ")||"—"}`
        + `<br>出端口 ${n.output_ports.map(p=>esc(p.port_id)).join(", ")||"—"}`
        + (n.island?`<br>孤岛依据 ${esc(n.island_reason)}`:"")
        + `<div class="row" style="margin-top:8px"><button onclick="removeNode('${esc(n.node_instance_id)}')">删除该节点</button></div>`;
    });
  });
}

async function api(path, body){
  const r = await fetch(path,{method: body?"POST":"GET",
    headers:{"Content-Type":"application/json"}, body: body?JSON.stringify(body):undefined});
  return r.json();
}
async function compose(){
  const domain=document.getElementById("domain").value, goal=document.getElementById("goal").value||"全流程";
  STATE = await api("/api/compose",{domain,goal}); render();
}
async function validate(){ const d=await api("/api/validate",{}); STATE.validation=d.validation; render(); }
async function removeNode(id){ await edit({kind:"remove_node",node_instance_id:id}); }
async function edit(e){
  const d = await api("/api/edit",{edit:e});
  if(d.ok && d.state){ STATE=d.state; render(); fillEdgeForms(); }
  else { showIssues(d.issues); }
}
async function revise(){
  const msg=document.getElementById("feedback").value.trim();
  if(!msg) return;
  document.getElementById("reviseNote").textContent="提交中…";
  const r = await fetch("/api/revise",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({message:msg,history:[]})});
  if(r.status===503){ document.getElementById("reviseNote").textContent="模型不可用，草稿未改动"; return; }
  const d=await r.json();
  document.getElementById("reviseNote").textContent = d.ok ? "已按建议修订" : "AI 未能产出可编译的草稿";
  if(d.ok && d.state){ STATE=d.state; render(); }
}
let PLUGINS = [];
async function loadPlugins(){
  const d = await api("/api/plugins");
  PLUGINS = d.groups || [];
  const st = document.getElementById("addStage");
  st.innerHTML = PLUGINS.map((g,i)=>`<option value="${i}">${esc(g.name)}（${g.plugins.length}）</option>`).join("");
  fillAddPlugins();
  fillEdgeForms();
}
function fillAddPlugins(){
  const g = PLUGINS[+document.getElementById("addStage").value] || {plugins:[]};
  document.getElementById("addPlugin").innerHTML =
    g.plugins.map(p=>`<option value="${esc(p.plugin_id)}">${esc(p.label)} · ${esc(p.role)}</option>`).join("");
}
async function addNode(){
  const pid = document.getElementById("addPlugin").value; if(!pid) return;
  await edit({kind:"add_node", plugin_id:pid});
  document.getElementById("addNote").textContent = "已加入 " + pid;
}
function fillEdgeForms(){
  const nodes = STATE.view.nodes, opts = nodes.map(n=>
    `<option value="${esc(n.node_instance_id)}">${esc(n.label)}（${esc(n.node_instance_id)}）</option>`).join("");
  document.getElementById("edgeSrc").innerHTML = opts;
  document.getElementById("edgeDst").innerHTML = opts;
  fillSrcPorts(); fillDstPorts();
}
function fillSrcPorts(){
  const id = document.getElementById("edgeSrc").value;
  const n = STATE.view.nodes.find(x=>x.node_instance_id===id) || {output_ports:[]};
  document.getElementById("edgeSrcPort").innerHTML =
    n.output_ports.map(p=>`<option value="${esc(p.port_id)}">${esc(p.port_id)}</option>`).join("");
}
function fillDstPorts(){
  const id = document.getElementById("edgeDst").value;
  const n = STATE.view.nodes.find(x=>x.node_instance_id===id) || {input_ports:[]};
  document.getElementById("edgeDstPort").innerHTML =
    n.input_ports.map(p=>`<option value="${esc(p.port_id)}">${esc(p.port_id)}</option>`).join("");
}
async function addEdge(){
  const e = {kind:"add_edge",
    source_instance: document.getElementById("edgeSrc").value,
    source_port: document.getElementById("edgeSrcPort").value,
    target_instance: document.getElementById("edgeDst").value,
    target_port: document.getElementById("edgeDstPort").value,
    adapter: document.getElementById("edgeAdapter").value || null};
  await edit(e);
  document.getElementById("edgeNote").textContent = "见左侧结果";
}
async function insertHere(pluginId, sourceNode, sourcePort, targetPort){
  const before = new Set(STATE.view.nodes.filter(n=>n.plugin_id===pluginId).map(n=>n.node_instance_id));
  const count = before.size + 1;
  let d = await api("/api/edit",{edit:{kind:"set_instances",plugin_id:pluginId,count}});
  if(!d.ok){ showIssues(d.issues); return; }
  STATE = d.state;
  const after = STATE.view.nodes.filter(n=>n.plugin_id===pluginId).map(n=>n.node_instance_id);
  const fresh = after.find(x=>!before.has(x));
  d = await api("/api/edit",{edit:{kind:"add_edge",source_instance:sourceNode,
        source_port:sourcePort,target_instance:fresh,target_port:targetPort}});
  if(!d.ok){ showIssues(d.issues); }
  else { STATE = d.state; }
  render(); renderEdgeFormsRefresh();
}
function renderEdgeFormsRefresh(){ fillEdgeForms(); }
function showIssues(issues){
  document.getElementById("selection").innerHTML =
    (issues||[]).map(i=>`<div class="issue"><div class="code">${esc(i.code)}</div>`
      + `<div class="msg">${esc(i.message)}</div></div>`).join("") || "编辑被拒";
}
async function save(){
  const name = prompt("草稿名（存到 .data/drafts/）","draft-1"); if(!name) return;
  const d = await api("/api/save",{name}); alert("已保存 " + d.saved);
}
(async function init(){
  const dom = document.getElementById("domain");
  for(const x of __DOMAINS_LIST__) dom.innerHTML += `<option value="${x}">${x}</option>`;
  dom.value = STATE.domain;
  document.getElementById("goal").value = STATE.goal;
  render();
  loadPlugins();
})();
</script>
</body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", choices=available_domains(), default="audit")
    parser.add_argument("--goal", default="全流程")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)

    global _PAGE
    import json as _json

    _PAGE = _PAGE.replace("__DOMAINS_LIST__", _json.dumps(list(available_domains())))
    _STATE["domain"], _STATE["goal"] = args.domain, args.goal

    import uvicorn

    url = f"http://127.0.0.1:{args.port}"
    print(f"组网工作台：{url}   (领域 {args.domain}，Ctrl+C 退出)")
    if not args.no_open:
        import webbrowser

        webbrowser.open(url)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
