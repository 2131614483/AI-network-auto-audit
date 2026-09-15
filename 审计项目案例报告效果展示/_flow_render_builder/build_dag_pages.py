# -*- coding: utf-8 -*-
"""构建审计组网 DAG 渲染页（自包含 HTML，零后端零执行）。

为三份审计报告（基础版 / 实验组 / 高难度）各生成一个 _flow_render/index.html，
数据与渲染引擎完全内嵌，浏览器打开即可播放 / 单步 / 拖动时间轴。
支持 URL hash 寻址：index.html#t=700&s=P24 用于无头浏览器逐帧截屏。
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
CASE = os.path.abspath(os.path.join(BASE, ".."))

# --------------------------------------------------------------------------
# 通用引擎（HTML 模板，只读 __DAG 数据，确定性渲染）
# --------------------------------------------------------------------------
TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>@@TITLE@@</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: #05070e; color: #e5e7eb;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; overflow: hidden; }
  .topbar { display: flex; align-items: center; gap: 14px; padding: 10px 18px;
    border-bottom: 1px solid #1b2431; background: linear-gradient(180deg, #0a0f1a, #070b13); }
  .topbar h1 { font-size: 15px; margin: 0; font-weight: 600; letter-spacing: .5px; }
  .topbar .sub { font-size: 11.5px; color: #8b97a8; flex: 1 1 auto; min-width: 180px; }
  #hud { margin-left: auto; font-size: 12px; color: #9fb4cc; font-family: Consolas, monospace;
    white-space: nowrap; }
  .provebar { display: flex; gap: 14px; align-items: center; padding: 7px 18px;
    font-size: 11.5px; color: #9fb4cc; border-bottom: 1px solid #141c28; background: #070b13;
    flex-wrap: wrap; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 2px 10px;
    border: 1px solid #223149; border-radius: 999px; background: #0b1320; white-space: nowrap; }
  .chip b { color: #7fe0c0; font-weight: 600; }
  .chip .k { color: #6b7a90; }
  .legend { display: flex; gap: 16px; padding: 6px 18px; font-size: 11px; color: #9fb4cc;
    border-bottom: 1px solid #141c28; background: #070b13; flex-wrap: wrap; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .swatch { width: 22px; height: 0; border-top-width: 2.4px; border-top-style: solid; }
  .main { display: flex; height: calc(100vh - 300px); min-height: 300px; }
  .stage-wrap { flex: 1; overflow: auto; padding: 22px; display: flex;
    background:
      radial-gradient(circle at 30% 18%, rgba(46,129,247,.10), transparent 55%),
      radial-gradient(circle at 78% 82%, rgba(127,224,192,.07), transparent 55%), #05070e; }
  svg { display: block; margin: 0 auto; flex: none; }
  .panel { width: 330px; flex: none; border-left: 1px solid #151e2c; background: #070c15;
    padding: 14px 16px; overflow-y: auto; font-size: 12.5px; display: flex; flex-direction: column; gap: 10px; }
  .panel h3 { margin: 0; font-size: 13.5px; }
  .panel .cap { color: #7fe0c0; font-family: Consolas, monospace; font-size: 11px; }
  .panel .row { display: flex; justify-content: space-between; gap: 8px;
    border-bottom: 1px dashed #182333; padding: 3px 0; }
  .panel .row span:first-child { color: #8b97a8; }
  .panel .row b { color: #ffd98a; font-family: Consolas, monospace; font-size: 12px; text-align: right; }
  .panel .kpis { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .panel .kpi { border: 1px solid #1d2a3c; border-radius: 8px; padding: 8px 10px; background: #0b1320; }
  .panel .kpi .v { font-size: 15px; font-weight: 700; color: #7fe0c0; font-family: Consolas, monospace; }
  .panel .kpi .l { font-size: 10.5px; color: #6b7a90; margin-top: 2px; }
  .panel .trace { font-family: Consolas, monospace; font-size: 11px; color: #64748b;
    border-top: 1px solid #182333; padding-top: 8px; word-break: break-all; }
  .panel .note { color: #7d8ea5; font-size: 11.5px; line-height: 1.55; }
  .panel .find { border: 1px solid #3d3322; border-radius: 6px; padding: 6px 8px;
    background: #171207; color: #ffd98a; font-size: 11.5px; }
  .ctrl { display: flex; align-items: center; gap: 10px; padding: 8px 18px;
    border-top: 1px solid #151e2c; background: #0a0f1a; font-size: 12px; flex-wrap: wrap; }
  .ctrl button { background: #12233a; color: #cfe0f5; border: 1px solid #23476e;
    border-radius: 6px; padding: 5px 14px; cursor: pointer; font-size: 12px; }
  .ctrl button:hover { background: #1a3f66; }
  .ctrl button:disabled { opacity: .4; cursor: default; }
  .ctrl select { background: #0b1320; color: #cfe0f5; border: 1px solid #23476e; border-radius: 6px; padding: 4px 8px; }
  .ctrl input[type=range] { flex: 1; accent-color: #4f96e8; min-width: 160px; }
  .stats { display: flex; gap: 10px; padding: 8px 18px; border-top: 1px solid #141c28;
    background: #070b13; overflow-x: auto; }
  .stat { border: 1px solid #1d2a3c; border-radius: 8px; padding: 6px 12px; background: #0a1220;
    white-space: nowrap; min-width: 120px; }
  .stat .t { font-size: 10px; color: #6b7a90; }
  .stat .n { font-size: 15px; font-weight: 700; color: #e5e7eb; font-family: Consolas, monospace; }
  .layer-label { font-size: 10px; fill: #52617a; letter-spacing: 1px; }
  .edge-label { font-size: 9.5px; fill: #b9a14f; }
  .foot { padding: 5px 18px; font-size: 10.5px; color: #4b5a6e; border-top: 1px solid #121a26; }
  /* ---------- 双模式设置（AI 全自动 / 人修改+AI微调）---------- */
  .modebar { display: flex; align-items: center; gap: 18px; padding: 8px 18px;
    border-bottom: 1px solid #141c28; background: #080d17; font-size: 12px; color: #9fb4cc;
    flex-wrap: wrap; }
  .modebar .mlabel { color: #6b7a90; font-size: 11px; letter-spacing: 1px; }
  .modebar label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; padding: 3px 10px; border-radius: 999px; border: 1px solid #223149; background: #0b1320; }
  .modebar label:hover { border-color: #33507a; }
  .modebar label.on { border-color: #ffd98a; background: #171207; color: #ffd98a; }
  .modebar input[type=radio] { accent-color: #ffd98a; margin: 0; }
  .modebar .hint { font-size: 11px; color: #52617a; flex: 1 1 auto; min-width: 200px; }
  .editbar { display: flex; align-items: center; gap: 10px; padding: 8px 18px;
    border-top: 1px solid #151e2c; background: #0a0f1a; font-size: 12px; flex-wrap: wrap; }
  .editbar .ebtn { background: #12233a; color: #cfe0f5; border: 1px solid #23476e;
    border-radius: 6px; padding: 4px 12px; cursor: pointer; font-size: 11.5px; }
  .editbar .ebtn:hover { background: #1a3f66; }
  .editbar .ebtn.danger { border-color: #6e2b2b; color: #ffb4a8; background: #2a1113; }
  .pane-sep { margin: 4px 0 2px; color: #6b7a90; font-size: 10.5px; letter-spacing: 1px; }
  .panel .trow { display: flex; justify-content: space-between; align-items: center; gap: 8px;
    border: 1px solid #1d2a3c; border-radius: 6px; padding: 5px 8px; background: #0b1320; }
  .panel .trow .tid { font-family: Consolas, monospace; color: #ffd98a; font-size: 11px; }
  .panel .trow .tname { color: #e5e7eb; font-size: 11.5px; flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .panel .trow .tlayer { color: #6b7a90; font-size: 10px; }
  .panel .mini { background: #12233a; color: #cfe0f5; border: 1px solid #23476e;
    border-radius: 5px; padding: 2px 8px; cursor: pointer; font-size: 10.5px; }
  .panel .mini.danger { border-color: #6e2b2b; color: #ffb4a8; background: #2a1113; }
  .panel .mini.ok { border-color: #1e4a39; color: #7fe0c0; background: #0d1f18; }
  .panel input, .panel select, .panel textarea { background: #0b1320; color: #e5e7eb;
    border: 1px solid #223149; border-radius: 5px; padding: 4px 7px; font-size: 11.5px;
    font-family: Consolas, monospace; width: 100%; box-sizing: border-box; }
  .panel .fld { margin-bottom: 8px; }
  .panel .fld .fk { color: #6b7a90; font-size: 10px; margin-bottom: 3px; }
  .panel .fld textarea { min-height: 52px; resize: vertical; }
  .panel .kv-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 8px; }
  .panel .kv-grid .fld input[type=number] { text-align: right; }
  .panel .sug { border: 1px solid #223149; border-radius: 6px; padding: 7px 9px;
    font-size: 11px; background: #0b1320; line-height: 1.5; }
  .panel .sug .shead { display: flex; justify-content: space-between; gap: 8px; align-items: flex-start; }
  .panel .sug.err { border-left: 3px solid #e06060; } .panel .sug.warn { border-left: 3px solid #e2b13c; }
  .panel .sug.ok { border-left: 3px solid #52e08a; }
  .panel .tag { font-size: 9.5px; padding: 1px 7px; border-radius: 999px; }
  .panel .tag.err { background: #2a1113; color: #ffb4a8; } .panel .tag.warn { background: #1d180d; color: #ffd98a; }
  .panel .tag.ok { background: #0d1f18; color: #7fe0c0; }
  .panel .notemsg { color: #52617a; font-size: 11px; line-height: 1.6; }
  svg text { user-select: none; }
</style>
</head>
<body>
  <div class="topbar">
    <h1>@@TITLE@@</h1>
    <span class="sub" id="sub">@@SUB@@</span>
    <div id="hud">初始化…</div>
  </div>
  <div class="provebar" id="provebar"></div>
  <div class="modebar" id="modebar">
    <span class="mlabel">组网模式</span>
    <label id="modeAuto"><input type="radio" name="gmMode" value="auto" checked /> AI 全自动</label>
    <label id="modeManual"><input type="radio" name="gmMode" value="manual" /> 人修改 + AI 微调</label>
    <span class="hint" id="modeHint">默认全自动：组网由确定性引擎生成，图表只读。切到「人修改+AI 微调」后，中间人可改节点/连线/阈值/发现证据，AI 引擎给出一致性微调建议。</span>
  </div>
  <div class="editbar" id="editbar" style="display:none">
    <span class="mlabel" style="color:#ffd98a">✎ 编辑态</span>
    <button class="ebtn" id="btnEditWork">编辑工作台</button>
    <button class="ebtn" id="btnEditApply">保存草稿并刷新预览</button>
    <button class="ebtn" id="btnEditExport">导出组网 JSON</button>
    <button class="ebtn danger" id="btnEditReset">重置为原始组网</button>
    <span class="hint" id="editHint">改动先写回内存副本，保存后整页按草稿重渲染（本地 localStorage，零后端零网络）。</span>
  </div>
  <div class="legend">
    <span><i class="swatch" style="border-top-color:#2b3a4d"></i>未导通</span>
    <span><i class="swatch" style="border-top-color:#ffd98a"></i>数据正在端口间传递（数据包）</span>
    <span><i class="swatch" style="border-top-color:#52e08a;border-top-style:dashed"></i>已送达 / 已产出</span>
    <span><i class="swatch" style="border-top-color:#e2b13c;border-top-style:dotted"></i>◆ 虚线节点=批量取证/检出聚合（非独立插件）</span>
    <span>节点亮金银=处理中，绿勾=已产出；点击节点查看该阶段证据卡</span>
  </div>
  <div class="main">
    <div class="stage-wrap"><svg id="stage"></svg></div>
    <div class="panel" id="panel"></div>
  </div>
  <div class="ctrl">
    <button id="btnPlay">⏸ 暂停</button>
    <button id="btnStep">⏭ 单步</button>
    <button id="btnRestart">↺ 重播</button>
    <label>速度
      <select id="speed">
        <option value="0.5">0.5×</option>
        <option value="1" selected>1×</option>
        <option value="2">2×</option>
        <option value="4">4×</option>
      </select>
    </label>
    <input type="range" id="seek" min="0" max="1000" value="0" />
    <span id="timeLabel" style="font-family:Consolas,monospace;color:#8b97a8">0 / 0 ms</span>
  </div>
  <div class="stats" id="stats"></div>
  <div class="foot" id="foot"></div>
<script>
window.__DAG = @@DAG_JSON@@;
(function () {
  try {
    var k = "audit_dag_draft_" + (window.__DAG.plan || "unknown");
    var s = localStorage.getItem(k);
    if (s) { window.__DAG = JSON.parse(s); window.__DAG_DRAFT_KEY = k; window.__DAG_DRAFT = true; }
  } catch (e) {}
})();
</script>
<script>
(() => {
  "use strict";
  var D = window.__DAG;

  var STAGES = D.nodes.map(function (n) {
    return {
      id: n.id, layer: n.layer, name: n.name, cap: n.cap || "",
      finding: n.finding || "", virtual: !!n.virtual,
      kpis: n.kpis || [], rows: n.rows || [], note: n.note || "", live: n.live || null,
      kind: n.kind || "" };
  });
  var EDGES = D.edges.map(function (e) {
    return { from: e.from, to: e.to, label: e.label || "data", virtual: !!e.virtual };
  });
  var LAYER_TITLES = D.layers;
  var BY_ID = Object.fromEntries(STAGES.map(function (s) { return [s.id, s]; }));
  var DEFAULT_EDGE_PORT = "__default__";

  var inPorts = {}, outPorts = {};
  STAGES.forEach(function (s) { inPorts[s.id] = []; outPorts[s.id] = []; });
  EDGES.forEach(function (e) {
    if (!inPorts[e.to].includes(e.label)) inPorts[e.to].push(e.label);
    if (!outPorts[e.from].includes(e.label)) outPorts[e.from].push(e.label);
  });
  function portList(id, side) {
    var list = side === "in" ? inPorts[id] : outPorts[id];
    if (side === "in" && list.length === 0) return ["数据入口"];
    if (side === "out" && list.length === 0) return ["最终输出"];
    return list;
  }

  var ACTIVE_MS = 560, STEP_GAP_MS = 300, EDGE_MS = 320, STEP_MS = ACTIVE_MS + STEP_GAP_MS;
  var layerOf = function (s) { return s.layer; };
  var layerMax = Math.max.apply(null, STAGES.map(layerOf));
  var timeline = (function () {
    var nodes = {}, edges = [];
    STAGES.forEach(function (s) {
      var t0 = layerOf(s) * STEP_MS;
      nodes[s.id] = { id: s.id, enterAt: t0, doneAt: t0 + ACTIVE_MS };
    });
    EDGES.forEach(function (e) {
      var src = nodes[e.from], flowStart = src.doneAt;
      edges.push({ key: e.from + "|" + e.label + "|" + e.to, source: e.from, target: e.to,
        flowStart: flowStart, flowEnd: flowStart + EDGE_MS });
    });
    var lastDone = Math.max.apply(null, Object.values(nodes).map(function (n) { return n.doneAt; }));
    var maxEdge = edges.reduce(function (m, e) { return Math.max(m, e.flowEnd); }, 0);
    return { durationMs: Math.max(lastDone, maxEdge) + STEP_GAP_MS, nodes: nodes, edges: edges,
      nodeOrder: STAGES.map(function (s) { return s.id; }) };
  })();

  function buildFrame(t, finished) {
    var nodePhase = {}, doneCount = 0;
    timeline.nodeOrder.forEach(function (id) {
      var n = timeline.nodes[id];
      var ph = t < n.enterAt ? "idle" : t < n.doneAt ? "active" : "done";
      nodePhase[id] = ph; if (ph === "done") doneCount += 1;
    });
    var edgePacket = {}, edgeReached = {};
    timeline.edges.forEach(function (e) {
      if (t >= e.flowEnd) edgeReached[e.key] = true;
      else if (t > e.flowStart) edgePacket[e.key] = (t - e.flowStart) / EDGE_MS;
    });
    return { t: t, doneCount: doneCount, total: timeline.nodeOrder.length,
      progress: timeline.durationMs ? t / timeline.durationMs : 1,
      nodePhase: nodePhase, edgePacket: edgePacket, edgeReached: edgeReached };
  }

  /* ---------- 布局 ---------- */
  var NODE_W = 196, HEADER = 40, PORT_ROW = 15, FOOT = 14, LAYER_GAP = 150, ROW_GAP = 26;
  function layout() {
    var nodeH = function (io) { return HEADER + Math.max(io, 1) * PORT_ROW + FOOT; };
    var inLayers = {};
    LAYER_TITLES.forEach(function (_, l) { inLayers[l] = []; });
    STAGES.forEach(function (s) { inLayers[layerOf(s)].push(s.id); });
    var spec = {};
    STAGES.forEach(function (s) {
      var ins = portList(s.id, "in"), outs = portList(s.id, "out");
      var inR = ins.map(function (p, r) { return { port: p, row: r }; });
      var outR = outs.map(function (p, r) { return { port: p, row: r }; });
      spec[s.id] = { id: s.id, w: NODE_W, h: nodeH(Math.max(ins.length, outs.length)),
        inPorts: inR, outPorts: outR };
    });
    var layerH = {};
    Object.keys(inLayers).forEach(function (l) {
      layerH[l] = inLayers[l].reduce(function (s, id) { return s + spec[id].h; }, 0) +
        (inLayers[l].length - 1) * ROW_GAP;
    });
    var maxH = Math.max.apply(null, [0].concat(Object.values(layerH)));
    Object.keys(inLayers).forEach(function (l) {
      var x = (+l) * (NODE_W + LAYER_GAP),
          y = Math.max(0, (maxH - layerH[l]) / 2);
      inLayers[l].forEach(function (id) { spec[id].x = x; spec[id].y = y; y += spec[id].h + ROW_GAP; });
    });
    var portY = function (n, side, port) {
      var ps = side === "in" ? n.inPorts : n.outPorts;
      var row = 0; ps.forEach(function (p) { if (p.port === port) row = p.row; });
      return n.y + HEADER + row * PORT_ROW + PORT_ROW / 2;
    };
    var edges = EDGES.map(function (e) {
      var s = spec[e.from], t = spec[e.to];
      var sp = s.outPorts.find(function (p) { return p.port === e.label; }) || s.outPorts[0];
      var tp = t.inPorts.find(function (p) { return p.port === e.label; }) || t.inPorts[0];
      return { key: e.from + "|" + e.label + "|" + e.to, source: e.from, target: e.to, label: e.label,
        from: { x: s.x + s.w, y: s.y + HEADER + sp.row * PORT_ROW + PORT_ROW / 2 },
        to: { x: t.x, y: t.y + HEADER + tp.row * PORT_ROW + PORT_ROW / 2 } };
    });
    var width = Math.max.apply(null, STAGES.map(function (s) { return spec[s.id].x + spec[s.id].w; })) + LAYER_GAP;
    return { width: width, height: Math.max(maxH, 220), nodes: spec, edges: edges, maxH: maxH };
  }
  var laid = layout();

  /* ---------- SVG 构建 ---------- */
  var NS = "http://www.w3.org/2000/svg";
  var svg = document.getElementById("stage");
  var VBW = laid.width + 40, VBH = laid.height + 40;
  svg.setAttribute("viewBox", "0 0 " + VBW + " " + VBH);
  svg.style.maxWidth = VBW + "px"; svg.style.width = VBW + "px";
  svg.style.height = VBH + "px";
  var rootG = document.createElementNS(NS, "g");
  rootG.setAttribute("transform", "translate(20,20)");
  svg.appendChild(rootG);
  var edgeG = document.createElementNS(NS, "g"), nodeG = document.createElementNS(NS, "g");
  rootG.appendChild(edgeG); rootG.appendChild(nodeG);
  rootG.appendChild(document.createElementNS(NS, "g")); // 占位
  LAYER_TITLES.forEach(function (t, l) {
    var el = document.createElementNS(NS, "text");
    el.setAttribute("x", l * (NODE_W + LAYER_GAP) + NODE_W / 2);
    el.setAttribute("y", -4); el.setAttribute("text-anchor", "middle"); el.setAttribute("class", "layer-label");
    el.textContent = t;
    rootG.appendChild(el);
  });

  function wirePath(from, to) {
    var dx = Math.max(34, (to.x - from.x) / 4);
    return "M " + from.x + " " + from.y + " C " + (from.x + dx) + " " + from.y + ", " +
      (to.x - dx) + " " + to.y + ", " + to.x + " " + to.y;
  }
  function cubic(p0, a, b) {
    var dx = Math.max(34, (b.x - a.x) / 4);
    var p1 = { x: a.x + dx, y: a.y }, p2 = { x: b.x - dx, y: b.y }, u = 1 - p0;
    return { x: u * u * u * a.x + 3 * u * u * p0 * p1.x + 3 * u * p0 * p0 * p2.x + p0 * p0 * p0 * b.x,
      y: u * u * u * a.y + 3 * u * u * p0 * p1.y + 3 * u * p0 * p0 * p2.y + p0 * p0 * p0 * b.y };
  }
  var edgeEls = {}, packetEls = {}, edgeLblEls = {};
  laid.edges.forEach(function (e) {
    var sV = !!(BY_ID[e.source].virtual || BY_ID[e.target].virtual);
    var path = document.createElementNS(NS, "path");
    path.setAttribute("d", wirePath(e.from, e.to));
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", sV ? "#3a4760" : (e.label.startsWith("G") ? "#2f5c52" : "#2b3a4d"));
    path.setAttribute("stroke-width", "1.5");
    if (sV) path.setAttribute("stroke-dasharray", "5 4");
    edgeG.appendChild(path); edgeEls[e.key] = path;
    var pk = document.createElementNS(NS, "circle");
    pk.setAttribute("r", "4"); pk.setAttribute("fill", "#ffe3a8"); pk.setAttribute("stroke", "#ffd98a");
    pk.setAttribute("opacity", "0");
    edgeG.appendChild(pk); packetEls[e.key] = pk;
    var mid = cubic(0.5, e.from, e.to);
    var lb = document.createElementNS(NS, "text");
    lb.setAttribute("x", mid.x + 4); lb.setAttribute("y", mid.y - 6);
    lb.setAttribute("class", "edge-label"); lb.textContent = e.label;
    edgeG.appendChild(lb); edgeLblEls[e.key] = lb;
  });
  var nodeEls = {};
  STAGES.forEach(function (s) {
    var ln = laid.nodes[s.id];
    var g = document.createElementNS(NS, "g");
    g.setAttribute("transform", "translate(" + ln.x + "," + ln.y + ")");
    g.setAttribute("cursor", "pointer"); g.dataset.id = s.id;
    var rect = document.createElementNS(NS, "rect");
    rect.setAttribute("width", ln.w); rect.setAttribute("height", ln.h);
    rect.setAttribute("rx", "10"); rect.setAttribute("fill", "#0f1720");
    rect.setAttribute("stroke", s.virtual ? "#e2b13c" : "#33404f");
    rect.setAttribute("stroke-width", s.virtual ? "1.2" : "1.3");
    if (s.virtual) rect.setAttribute("stroke-dasharray", "6 4");
    g.appendChild(rect);
    var title = document.createElementNS(NS, "text");
    title.setAttribute("x", "12"); title.setAttribute("y", "18");
    title.setAttribute("font-size", "12"); title.setAttribute("font-weight", "600");
    title.setAttribute("fill", s.virtual ? "#e2b13c" : "#e5e7eb");
    title.textContent = (s.virtual ? "◆ " : "") + s.name;
    g.appendChild(title);
    var cap = document.createElementNS(NS, "text");
    cap.setAttribute("x", "12"); cap.setAttribute("y", "32");
    cap.setAttribute("font-size", "9"); cap.setAttribute("fill", "#9ca3af");
    cap.textContent = s.cap;
    g.appendChild(cap);
    var inList = portList(s.id, "in"), outList = portList(s.id, "out");
    inList.forEach(function (p, r) {
      var c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", "6"); c.setAttribute("cy", HEADER + r * PORT_ROW + PORT_ROW / 2);
      c.setAttribute("r", "3.5"); c.setAttribute("fill", "#0b0e14"); c.setAttribute("stroke", "#6b7a90");
      g.appendChild(c);
    });
    outList.forEach(function (p, r) {
      var c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", ln.w - 6); c.setAttribute("cy", HEADER + r * PORT_ROW + PORT_ROW / 2);
      c.setAttribute("r", "3.5"); c.setAttribute("fill", "#0b0e14"); c.setAttribute("stroke", "#6b7a90");
      g.appendChild(c);
    });
    var state = document.createElementNS(NS, "text");
    state.setAttribute("x", "12"); state.setAttribute("y", ln.h - 5);
    state.setAttribute("font-size", "9"); state.dataset.role = "state";
    g.appendChild(state);
    g.addEventListener("click", function () { selectNode(s.id); });
    nodeEls[s.id] = g;
    nodeG.appendChild(g);
  });

  /* ---------- 状态与动画 ---------- */
  var t = 0, playing = true, speed = 1, last = performance.now();
  var hud = document.getElementById("hud");
  var selected = null;
  var results = {};
  function runStage(id) {
    var s = BY_ID[id];
    results[id] = { kpis: s.kpis, rows: s.rows, note: s.note, finding: s.finding };
    return results[id];
  }
  function progressOf(id, p) {
    var s = BY_ID[id];
    return s.live && s.live.to != null ? Math.floor(s.live.to * Math.min(1, Math.max(0, p))) : null;
  }

  function render(now) {
    if (playing) {
      t += (now - last) * speed;
      if (t >= timeline.durationMs) t = timeline.durationMs;
    }
    last = now;
    var frame = buildFrame(t);
    timeline.nodeOrder.forEach(function (id) {
      var ph = frame.nodePhase[id];
      var g = nodeEls[id], rect = g.querySelector("rect"), st = g.querySelector('[data-role="state"]');
      var s = BY_ID[id];
      if (ph === "idle") {
        g.setAttribute("opacity", "0.35");
        rect.setAttribute("stroke", s.virtual ? "#5c4a1e" : "#232c39");
        st.setAttribute("fill", "#5a6675"); st.textContent = "待处理";
      } else {
        g.setAttribute("opacity", "1");
        var n = timeline.nodes[id], p = (t - n.enterAt) / ACTIVE_MS;
        if (ph === "active") {
          rect.setAttribute("stroke", "#ffd98a"); st.setAttribute("fill", "#ffd98a");
          var c = progressOf(id, p);
          st.textContent = "● 处理中" + (c != null ? " " + c + "/" + BY_ID[id].live.to : "…");
        } else {
          runStage(id);
          rect.setAttribute("stroke", s.virtual ? "#e2b13c" : "#52e08a");
          st.setAttribute("fill", "#52e08a");
          st.textContent = s.virtual ? "✓ 汇集/分发" : ("✓ " + (s.finding || "已产出"));
        }
      }
      if (selected === id) { rect.setAttribute("stroke", "#4f96e8"); rect.setAttribute("stroke-width", "2.6"); }
      else rect.setAttribute("stroke-width", s.virtual ? "1.2" : "1.3");
    });
    laid.edges.forEach(function (e) {
      var path = edgeEls[e.key], pk = packetEls[e.key], lb = edgeLblEls[e.key];
      var sV = !!(BY_ID[e.source].virtual || BY_ID[e.target].virtual);
      var base = sV ? "#3a4760" : (e.label.startsWith("G") ? "#2f5c52" : "#2b3a4d");
      var pkt = frame.edgePacket[e.key];
      if (pkt !== void 0) {
        path.setAttribute("stroke", "#ffd98a"); path.setAttribute("stroke-width", "2.4");
        var pt = cubic(pkt, e.from, e.to);
        pk.setAttribute("cx", pt.x); pk.setAttribute("cy", pt.y); pk.setAttribute("opacity", "1");
      } else if (frame.edgeReached[e.key]) {
        path.setAttribute("stroke", "#52e08a"); path.setAttribute("stroke-width", "1.6");
        path.setAttribute("opacity", "0.92"); pk.setAttribute("opacity", "0");
      } else {
        path.setAttribute("stroke", base); path.setAttribute("stroke-width", "1.4");
        if (sV) path.setAttribute("stroke-dasharray", "5 4");
        else path.removeAttribute("stroke-dasharray");
        pk.setAttribute("opacity", "0");
      }
      lb.setAttribute("opacity", frame.edgeReached[e.key] ? "0.95" : "0.45");
    });
    hud.textContent = "已激活 " + frame.doneCount + "/" + frame.total + " · " +
      Math.round(frame.progress * 100) + "% · 确定性渲染 · plan: " + D.plan;
    refreshSeekUI();
    if (playing && t < timeline.durationMs) requestAnimationFrame(render);
  }

  /* ---------- 底部统计条（静态最终值） ---------- */
  var statsBox = document.getElementById("stats");
  D.stats.forEach(function (s) {
    var el = document.createElement("div");
    el.className = "stat";
    el.innerHTML = '<div class="t">' + s[0] + '</div><div class="n">' + s[1] + "</div>";
    statsBox.appendChild(el);
  });

  /* ---------- 证据面板 ---------- */
  var panel = document.getElementById("panel");
  function proveCard() {
    return '<h3 style="color:#7fe0c0">📄 审计取证快照</h3>' +
      '<div class="trace">' + D.source + "<br/>文件 " + D.files + " · " + D.rows + "<br/>" +
      (D.sha ? "证据 sha256 " + D.sha.slice(0, 16) + "…<br/>" : "") +
      "内嵌于 " + new Date(D.generated).toISOString().slice(0, 19).replace("T", " ") + "</div>" +
      '<div class="note">点击任意节点查看该阶段的取证证据（KPI 与传样均为审计脚本实测值；证据口径见对应审计报告 §7/§8/§9）。</div>';
  }
  function txt(v, f) {
    if (typeof v === "number") {
      var d = f || 2;
      var s = v.toLocaleString("zh-CN", { maximumFractionDigits: d });
      if (d > 0 && s.includes(".")) s = s.replace(/0+$/, "").replace(/\.$/, "");
      return s;
    }
    return String(v);
  }
  function nodeCard(id) {
    var s = BY_ID[id], r = results[id] || runStage(id);
    var html = "<h3>" + s.name + "</h3><div class=\"cap\">" + s.cap + " · L" + s.layer +
      (s.virtual ? " · 虚拟汇聚节点" : "") + "</div>";
    if (r.finding) html += '<div class="find">发现：' + r.finding + "</div>";
    if (r.kpis.length) {
      html += '<div class="kpis">' + r.kpis.map(function (k) {
        return '<div class="kpi"><div class="v">' + txt(k[0], k[2] || 2) + '</div><div class="l">' + k[1] + "</div></div>";
      }).join("") + "</div>";
    }
    if (r.rows.length) html += "<div>" + r.rows.map(function (p) {
      return '<div class="row"><span>' + p[0] + "</span><b>" + p[1] + "</b></div>";
    }).join("") + "</div>";
    var ins = inPorts[id].map(function (p) { return "edge:" + p; });
    var outs = outPorts[id].map(function (p) { return "artf:" + id + "." + p; });
    html += '<div class="trace">输入端<br/>' + (ins.join("<br/>") || "（种子输入）") + "<br/>输出工件<br/>" +
      (outs.join("<br/>") || "（最终输出）") + "</div>";
    html += '<div class="note">' + s.note + "</div>";
    return html;
  }
  function selectNode(id) {
    selected = id;
    if (window.__DAG_EDIT_HOOK) { window.__DAG_EDIT_HOOK(id); return; }
    panel.innerHTML = (id == null ? proveCard() : nodeCard(id));
  }
  selectNode(null);

  /* ---------- 控制条 ---------- */
  var btnPlay = document.getElementById("btnPlay"), btnStep = document.getElementById("btnStep"),
      btnRestart = document.getElementById("btnRestart"), speedSel = document.getElementById("speed"),
      seek = document.getElementById("seek"), timeLabel = document.getElementById("timeLabel");
  btnPlay.addEventListener("click", function () {
    playing = !playing;
    if (playing && t >= timeline.durationMs) { t = 0; results = {}; selectNode(selected); }
    last = performance.now();
    btnPlay.textContent = playing ? "⏸ 暂停" : "▶ 播放";
    if (playing && t < timeline.durationMs) requestAnimationFrame(render);
  });
  btnStep.addEventListener("click", function () {
    playing = false; btnPlay.textContent = "▶ 播放";
    var nx = timeline.nodeOrder.map(function (id) { return timeline.nodes[id].enterAt; })
      .filter(function (et) { return et > t + 10; });
    var nextStart = nx.length ? Math.min.apply(null, nx) : timeline.durationMs;
    t = nextStart === timeline.durationMs ? timeline.durationMs : nextStart + Math.min(ACTIVE_MS / 2, 400);
    last = performance.now(); render(last);
  });
  btnRestart.addEventListener("click", function () { playing = false; t = 0; last = performance.now(); render(last); btnPlay.textContent = "▶ 播放"; });
  speedSel.addEventListener("change", function () { speed = +speedSel.value; });
  seek.addEventListener("input", function () {
    t = (+seek.value / 1000) * timeline.durationMs; playing = false;
    btnPlay.textContent = "▶ 播放"; last = performance.now(); render(last);
  });
  function refreshSeekUI() {
    seek.value = Math.round((t / timeline.durationMs) * 1000);
    timeLabel.textContent = Math.round(t) + " / " + timeline.durationMs + " ms";
  }

  /* ---------- 启动：hash 寻址（#t=ms&s=nodeId） ---------- */
  var hash = location.hash.replace(/^#/, "");
  var params = {};
  hash.split("&").forEach(function (kv) {
    var i = kv.indexOf("="); if (i < 0) return;
    params[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1));
  });
  if (params.t != null) {
    t = Math.min(Math.max(0, +params.t), timeline.durationMs);
    playing = false; btnPlay.textContent = "▶ 播放";
  }
  if (params.s != null && BY_ID[params.s]) {
    t = timeline.durationMs;
    playing = false; btnPlay.textContent = "▶ 播放";
    selectNode(params.s);
  }
  window.__auditDemo = {
    seek: function (ms) { t = Math.min(Math.max(0, ms), timeline.durationMs); selectNode(null); last = performance.now(); render(last); },
    play: function () { if (t >= timeline.durationMs) t = 0; playing = true; last = performance.now(); btnPlay.textContent = "⏸ 暂停"; requestAnimationFrame(render); },
    pause: function () { playing = false; btnPlay.textContent = "▶ 播放"; },
    info: function () { return { durationMs: timeline.durationMs, nodes: STAGES.length, edges: EDGES.length, layers: LAYER_TITLES.length }; },
    frame: function () { return buildFrame(t, false); }
  };
  document.getElementById("provebar").innerHTML =
    '<span class="chip"><span class="k">源数据</span><b>' + D.dataset + "</b></span>" +
    '<span class="chip"><span class="k">文件</span><b>' + D.files + "</b></span>" +
    '<span class="chip"><span class="k">记录</span><b>' + D.rows + "</b></span>" +
    '<span class="chip"><span class="k">管道</span><b>' + STAGES.length + " 节点 / " + EDGES.length + " 边 / " + LAYER_TITLES.length + " 层</b></span>" +
    '<span class="chip"><span class="k">边界</span><b>只读 · 零后端 · 零执行</b></span>';
  document.getElementById("foot").textContent = D.foot;
  last = performance.now();
  render(last);
})();
</script>
<script>
/* ============ 中间人编辑引擎：AI 全自动 / 人修改+AI 微调 ============
 * 纯前端、零后端、零网络、零执行副作用。
 * - AI 全自动：默认态，图只读（证据卡可看，不可改）。
 * - 人修改+AI微调：可增删改节点/连线/层级/参数阈值/发现证据；
 *   所有改动写回内存工作副本 → 「保存草稿并刷新预览」持久化到
 *   localStorage 并按草稿整页重渲染；「AI 微调」用本地确定性规则
 *   做一致性检查与一键修复。 */
(() => {
  "use strict";
  var BK = "audit_dag_draft_" + (window.__DAG.plan || "unknown");
  var D = window.__DAG;                          /* 当前视图（可能已是草稿） */
  var ED = JSON.parse(JSON.stringify(D));        /* 编辑用工作副本 */
  var PANEL = document.getElementById("panel");
  var MODEL = "auto";
  var AUTOPANEL = null;                          /* 进入编辑态前的只读快照 */

  var DISK = { nodes: [], edges: [], edges_bk: [] };
  function esc(s){ return String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
  function nodeById(id){ for (var i=0;i<ED.nodes.length;i++) if (ED.nodes[i].id===id) return ED.nodes[i]; return null; }
  function nodeIdx(id){ for (var i=0;i<ED.nodes.length;i++) if (ED.nodes[i].id===id) return i; return -1; }

  function setModel(m){
    MODEL = m;
    document.getElementById("modeAuto").classList.toggle("on", m === "auto");
    document.getElementById("modeManual").classList.toggle("on", m === "manual");
    document.getElementById("editbar").style.display = m === "manual" ? "flex" : "none";
    if (m === "manual") {
      AUTOPANEL = AUTOPANEL || PANEL.innerHTML;      /* 快照只读证据卡 */
      window.__DAG_EDIT_HOOK = function (id) { if (id) nodeEditor(id); else workbench(); };
      workbench();
    } else {
      window.__DAG_EDIT_HOOK = null;
      PANEL.innerHTML = AUTOPANEL || PANEL.innerHTML; /* 恢复只读证据卡 */
    }
  }
  function radiosOnChange(){
    document.querySelectorAll("input[name=gmMode]").forEach(function (r) {
      r.addEventListener("change", function () { setModel(r.value); });
    });
    document.getElementById("btnEditWork").onclick = workbench;
    document.getElementById("btnEditApply").onclick = function () {
      try { localStorage.setItem(BK, JSON.stringify(ED)); } catch (e) { alert("保存失败：" + e.message); return; }
      location.reload();
    };
    document.getElementById("btnEditExport").onclick = exportJson;
    document.getElementById("btnEditReset").onclick = function () {
      if (!confirm("重置为原始组网（丢弃当前草稿）？")) return;
      try { localStorage.removeItem(BK); } catch (e) {}
      location.reload();
    };
  }

  /* ---------------- 工作台 ---------------- */
  function workbench(){
    var h = [];
    h.push('<h3 style="color:#7fe0c0">✎ 编辑工作台</h3>');
    h.push('<div class="notemsg">共 ' + ED.nodes.length + ' 节点 / ' + ED.edges.length + ' 边 · ' +
      (window.__DAG_DRAFT ? "当前为草稿视图" : "原始组网") +
      '。改动写回内存副本，点「保存草稿并刷新预览」整页重渲染。</div>');

    h.push('<div class="pane-sep">＋ 新增节点</div>');
    h.push('<div class="fld"><div class="fk">节点 ID</div><input id="nw_id" value="PN' + (ED.nodes.length + 1) + '" /></div>');
    h.push('<div class="fld"><div class="fk">名称</div><input id="nw_name" value="新建实质性程序" /></div>');
    h.push('<div class="fld"><div class="fk">层级 (0..' + (ED.layers.length - 1) + ')</div><input id="nw_layer" type="number" value="2" /></div>');
    h.push('<div class="fld"><div class="fk">cap</div><input id="nw_cap" value="rule.manual" /></div>');
    h.push('<button class="mini ok" id="nw_add">添加节点</button>');

    h.push('<div class="pane-sep">节点清单（点击图上节点直接编辑）</div>');
    ED.nodes.forEach(function (n) {
      h.push('<div class="trow"><span class="tid">' + esc(n.id) + '</span><span class="tname">' + esc(n.name) + '</span>' +
        '<span class="tlayer">L' + n.layer + (n.virtual ? " ◆" : "") + '</span>' +
        '<button class="mini" data-act="nodedel" data-id="' + esc(n.id) + '">删</button></div>');
    });

    h.push('<div class="pane-sep">＋ 新增连线</div>');
    h.push('<div class="kv-grid"><div class="fld"><div class="fk">起点</div><select id="nw_efrom">' +
      ED.nodes.map(function (n) { return '<option>' + esc(n.id) + '</option>'; }).join("") + '</select></div>' +
      '<div class="fld"><div class="fk">终点</div><select id="nw_eto">' +
      ED.nodes.map(function (n) { return '<option>' + esc(n.id) + '</option>'; }).join("") + '</select></div></div>');
    h.push('<div class="fld"><div class="fk">边 label（如 G03 / E5）</div><input id="nw_elabel" value="data" /></div>');
    h.push('<button class="mini ok" id="nw_addedge">添加连线</button>');

    h.push('<div class="pane-sep">连线清单</div>');
    ED.edges.forEach(function (e, i) {
      h.push('<div class="trow"><span class="tid">' + esc(e.from) + ' → ' + esc(e.to) + '</span>' +
        '<span class="tname" style="color:#b9a14f">' + esc(e.label) + '</span>' +
        '<button class="mini" data-act="edgedel" data-idx="' + i + '">删</button></div>');
    });

    h.push('<div class="pane-sep">✨ AI 微调（本地确定性规则 · 零后端零网络）</div>');
    h.push('<button class="mini ok" id="ai_run">运行一致性检查</button> <button class="mini" id="ai_all">一键修复全部</button>');
    h.push('<div id="ai_out"></div>');

    PANEL.innerHTML = h.join("");

    document.getElementById("nw_add").onclick = addNode;
    document.getElementById("nw_addedge").onclick = addEdge;
    document.getElementById("ai_run").onclick = function () { renderAi(runChecks(false)); };
    document.getElementById("ai_all").onclick = function () { runChecks(true); alert("已自动修复全部规则问题，点「保存草稿并刷新预览」生效。"); renderAi(runChecks(false)); };
    PANEL.querySelectorAll("[data-act=nodedel]").forEach(function (b) {
      b.onclick = function () {
        var id = b.getAttribute("data-id");
        ED.nodes = ED.nodes.filter(function (n) { return n.id !== id; });
        ED.edges = ED.edges.filter(function (e) { return e.from !== id && e.to !== id; });
        workbench();
      };
    });
    PANEL.querySelectorAll("[data-act=edgedel]").forEach(function (b) {
      b.onclick = function () { ED.edges.splice(+b.getAttribute("data-idx"), 1); workbench(); };
    });
  }

  function addNode(){
    var id = document.getElementById("nw_id").value.trim();
    if (!id || nodeById(id)) { alert("节点 ID 缺失或重复"); return; }
    ED.nodes.push({ id: id, layer: Math.max(0, Math.min(ED.layers.length - 1, parseInt(document.getElementById("nw_layer").value) || 2)),
      name: document.getElementById("nw_name").value.trim() || id,
      cap: document.getElementById("nw_cap").value.trim() || "rule.manual",
      finding: "", kpis: [], rows: [], note: "中间人新增节点", virtual: false });
    workbench();
  }
  function addEdge(){
    var f = document.getElementById("nw_efrom").value, t = document.getElementById("nw_eto").value,
        l = document.getElementById("nw_elabel").value.trim() || "data";
    if (f === t) { alert("不允许自环：起点=终点"); return; }
    ED.edges.push({ from: f, to: t, label: l, virtual: false });
    workbench();
  }

  /* ---------------- 节点编辑器 ---------------- */
  function nodeEditor(id){
    var n = nodeById(id); if (!n) { workbench(); return; }
    var h = [];
    h.push('<h3 style="color:#7fe0c0">✎ 编辑节点 ' + esc(n.id) + '</h3>');
    h.push('<button class="mini" id="ed_back">← 返回工作台</button> <button class="mini danger" id="ed_del">删除节点</button>');
    h.push('<div class="fld"><div class="fk">ID（重命名会同步更新连线）</div><input id="ed_id" value="' + esc(n.id) + '" /></div>');
    h.push('<div class="fld"><div class="fk">名称</div><input id="ed_name" value="' + esc(n.name) + '" /></div>');
    h.push('<div class="fld"><div class="fk">cap</div><input id="ed_cap" value="' + esc(n.cap) + '" /></div>');
    h.push('<div class="fld"><div class="fk">层级 (0..' + (ED.layers.length - 1) + ')</div><input id="ed_layer" type="number" value="' + n.layer + '" /></div>');
    h.push('<div class="fld"><div class="fk">发现（finding，如 H01 6,800,000）</div><input id="ed_finding" value="' + esc(n.finding) + '" /></div>');
    h.push('<div class="fld"><div class="fk">note</div><textarea id="ed_note">' + esc(n.note) + '</textarea></div>');

    h.push('<div class="pane-sep">参数与阈值（kpi：值 / 标签 / 小数位）</div>');
    (n.kpis || []).forEach(function (k, i) {
      h.push('<div data-kvi="' + i + '" class="kv-grid" style="position:relative;padding-right:20px">' +
        '<div class="fld"><div class="fk">值</div><input class="k_v" type="number" step="any" value="' + k[0] + '" /></div>' +
        '<div class="fld"><div class="fk">标签</div><input class="k_l" value="' + esc(k[1]) + '" /></div>' +
        '<div class="fld"><div class="fk">小数位</div><input class="k_d" type="number" value="' + (k[2] != null ? k[2] : 2) + '" /></div>' +
        '<button class="mini danger kv_del" style="position:absolute;top:4px;right:2px">×</button></div>');
    });
    h.push('<button class="mini ok" id="k_add">＋ 参数/阈值</button>');

    h.push('<div class="pane-sep">发现与证据（rows：键 / 值）</div>');
    (n.rows || []).forEach(function (r, i) {
      h.push('<div data-rwi="' + i + '" class="kv-grid" style="position:relative;padding-right:20px">' +
        '<div class="fld"><div class="fk">键</div><input class="r_k" value="' + esc(r[0]) + '" /></div>' +
        '<div class="fld"><div class="fk">值</div><input class="r_v" value="' + esc(r[1]) + '" /></div>' +
        '<button class="mini danger kv_del" style="position:absolute;top:4px;right:2px">×</button></div>');
    });
    h.push('<button class="mini ok" id="r_add">＋ 证据行</button>');
    h.push('<br/><button class="mini ok" id="ed_save" style="margin-top:8px">保存节点修改</button>');

    PANEL.innerHTML = h.join("");

    document.getElementById("ed_back").onclick = workbench;
    document.getElementById("ed_del").onclick = function () {
      ED.nodes = ED.nodes.filter(function (x) { return x.id !== id; });
      ED.edges = ED.edges.filter(function (e) { return e.from !== id && e.to !== id; });
      workbench();
    };
    document.getElementById("ed_save").onclick = function () { saveNodeEditor(id); };
    document.getElementById("k_add").onclick = function () {
      document.querySelector("[data-kvi]") && 0; /* 无操作占位 */
      var box = document.createElement("div");
      box.innerHTML = '<div class="kv-grid" style="position:relative;padding-right:20px">' +
        '<div class="fld"><div class="fk">值</div><input class="k_v" type="number" step="any" value="0" /></div>' +
        '<div class="fld"><div class="fk">标签</div><input class="k_l" value="新参数" /></div>' +
        '<div class="fld"><div class="fk">小数位</div><input class="k_d" type="number" value="2" /></div>' +
        '<button class="mini danger kv_del" style="position:absolute;top:4px;right:2px">×</button></div>';
      var prev = document.getElementById("k_add").previousElementSibling;
      prev.parentNode.insertBefore(box.firstElementChild, document.getElementById("k_add"));
    };
    document.getElementById("r_add").onclick = function () {
      var box = document.createElement("div");
      box.innerHTML = '<div class="kv-grid" style="position:relative;padding-right:20px">' +
        '<div class="fld"><div class="fk">键</div><input class="r_k" value="证据" /></div>' +
        '<div class="fld"><div class="fk">值</div><input class="r_v" value="—" /></div>' +
        '<button class="mini danger kv_del" style="position:absolute;top:4px;right:2px">×</button></div>';
      var prev = document.getElementById("r_add").previousElementSibling;
      prev.parentNode.insertBefore(box.firstElementChild, document.getElementById("r_add"));
    };
    PANEL.addEventListener("click", function (ev) {
      var t = ev.target;
      if (t.classList && t.classList.contains("kv_del")) {
        var box = t.closest("[data-kvi],[data-rwi]");
        if (box) box.parentNode.removeChild(box);
      }
    });
  }

  function saveNodeEditor(oldId){
    var n = nodeById(oldId); if (!n) return;
    var newId = document.getElementById("ed_id").value.trim();
    if (!newId) { alert("节点 ID 不能为空"); return; }
    if (newId !== oldId && nodeById(newId)) { alert("节点 ID 重复：" + newId); return; }
    n.id = newId; n.name = document.getElementById("ed_name").value.trim() || newId;
    n.cap = document.getElementById("ed_cap").value.trim();
    n.finding = document.getElementById("ed_finding").value.trim();
    n.note = document.getElementById("ed_note").value;
    n.layer = Math.max(0, Math.min(ED.layers.length - 1, parseInt(document.getElementById("ed_layer").value) || 0));
    var kpis = []; PANEL.querySelectorAll("[data-kvi]").forEach(function (box) {
      var v = box.querySelector(".k_v"), l = box.querySelector(".k_l"), d = box.querySelector(".k_d");
      kpis.push([parseFloat(v.value) || 0, l.value, parseInt(d.value) || 0]);
    });
    n.kpis = kpis;
    var rows = []; PANEL.querySelectorAll("[data-rwi]").forEach(function (box) {
      var k = box.querySelector(".r_k"), v = box.querySelector(".r_v");
      if (k.value.trim() || v.value.trim()) rows.push([k.value, v.value]);
    });
    n.rows = rows;
    if (newId !== oldId) {   /* 同步更新连线端点 */
      ED.edges.forEach(function (e) { if (e.from === oldId) e.from = newId; if (e.to === oldId) e.to = newId; });
    }
    workbench();
  }

  /* ---------------- AI 微调：一致性规则引擎 ---------------- */
  function runChecks(autoFix){
    var out = [], fixed = 0;
    var ids = ED.nodes.map(function (n) { return n.id; });
    var seen = {};
    ED.nodes.forEach(function (n) {
      if (seen[n.id]) {
        out.push({ sev: "err", msg: "节点 ID 重复：" + n.id });
        if (autoFix && !n.virtual) { /* 不改 ID，仅提示 */ }
      }
      seen[n.id] = 1;
      if (n.layer < 0 || n.layer >= ED.layers.length) {
        out.push({ sev: "err", msg: "节点 " + n.id + " 层级越界（L" + n.layer + "），应收敛到 0.." + (ED.layers.length - 1) });
        if (autoFix) { n.layer = Math.max(0, Math.min(ED.layers.length - 1, n.layer)); fixed++; }
      }
    });
    ED.edges.forEach(function (e) {
      if (ids.indexOf(e.from) < 0) { out.push({ sev: "err", msg: "悬空连线：起点 " + e.from + " 不存在" }); if (autoFix) {} }
      if (ids.indexOf(e.to) < 0)   { out.push({ sev: "err", msg: "悬空连线：终点 " + e.to + " 不存在" }); if (autoFix) {} }
      if (e.from === e.to)         { out.push({ sev: "warn", msg: "自环连线：" + e.from + " → " + e.to }); }
      var a = nodeById(e.from), b = nodeById(e.to);
      if (autoFix && (!a || !b)) { ED.edges = ED.edges.filter(function (x) { return x !== e; }); fixed++; }
      if (autoFix && e.from === e.to && a) { ED.edges = ED.edges.filter(function (x) { return x !== e; }); fixed++; }
    });
    ED.nodes.forEach(function (n) {
      if (!n.virtual && !n.finding && n.layer > 1) out.push({ sev: "warn", msg: "节点 " + n.id + " 无 finding（实质性程序应产出发现）" });
      (n.kpis || []).forEach(function (k) { if (typeof k[0] !== "number" || isNaN(k[0])) { out.push({ sev: "err", msg: "节点 " + n.id + " 参数值为空/非法" }); if (autoFix) { k[0] = 0; fixed++; } } });
    });
    return { list: out, fixed: fixed };
  }
  function renderAi(r){
    var el = document.getElementById("ai_out"); if (!el) return;
    if (!r.list.length) { el.innerHTML = '<div class="sug ok"><span class="tag ok">✓ 通过</span> 一致性检查通过：无悬空连线、无层级越界、参数均为数值。</div>'; return; }
    el.innerHTML = r.list.map(function (s) {
      return '<div class="sug ' + s.sev + '"><span class="tag ' + s.sev + '">' + (s.sev === "err" ? "错误" : "提示") + '</span> ' + esc(s.msg) + '</div>';
    }).join("");
  }

  /* ---------------- 导出 / 初始化 ---------------- */
  function exportJson(){
    var payload = { title: ED.title, sub: ED.sub, plan: ED.plan, dataset: ED.dataset,
      layers: ED.layers, nodes: ED.nodes, edges: ED.edges, stats: ED.stats,
      generated: ED.generated, source: ED.source, sha: ED.sha,
      foot: ED.foot + "（该 JSON 由中间人于「人修改+AI微调」模式导出）" };
    try {
      var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "组网-人工微调-" + (ED.plan || "dag") + ".json";
      document.body.appendChild(a); a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); document.body.removeChild(a); }, 1500);
    } catch (e) { alert("导出失败：" + e.message); }
  }

  // 进入编辑态前置快照，避免覆盖只读证据卡
  AUTOPANEL = PANEL.innerHTML;
  radiosOnChange();
  var hsh = (location.hash || "").replace(/^#/, "");
  var hm = /m=manual/.test(hsh);
  if (window.__DAG_DRAFT || hm) {
    document.querySelector("input[name=gmMode][value=manual]").checked = true;
    setModel("manual");
  }
})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# 报告 1：基础版（黔岭酒业2025年度财务报表审计）
# --------------------------------------------------------------------------
def spec_base():
    L = ["数据接入 L0", "勾稽门户 L1", "实质性程序 L2", "发现收敛 L3", "报表重编 L4", "结论 L5"]
    nodes = [
        dict(id="P1", layer=0, name="P1 账务接入", cap="ingest.books",
             rows=[["科目余额表×3", "P/S1/S2 三主体"], ["记账凭证", "178 行"], ["未审合并报表", "17 行利润表"]],
             note="读取科目余额表×3、凭证、未审合并报表、子公司清单，金额统一 Decimal 标准化（去千分位/空值归零）。"),
        dict(id="P2", layer=0, name="P2 业务接入", cap="ingest.biz",
             rows=[["业务明细文件", "14 个"], ["销售/账龄/固资", "行式工件"]],
             note="读取 14 个业务明细文件为行式工件（销售/账龄/存货/固资/无长摊/薪酬/供应商/合同/期间费用等）。"),
        dict(id="P3", layer=0, name="P3 税务接入", cap="ingest.tax",
             rows=[["应交税费明细", "消费税 746,000,000"], ["企业所得税", "267,487,333.34"]],
             note="读取 04_税务资料 4 文件；合计 1,013,487,333.34 与总账 2221 勾稽一致 ✓。"),
        dict(id="P4", layer=0, name="P4 银行接入", cap="ingest.bank",
             rows=[["日记账", "31 行"], ["对账单", "30 行"], ["基本户起点余额", "1,812,800,000.00"]],
             note="读取日记账/对账单/账户清单，标注未达方向与在途标记。"),
        dict(id="P5", layer=0, name="P5 治理文件接入", cap="ingest.gov",
             rows=[["董事会决议", "垫付 25,000,000 / 奖金 18,000,000"], ["补助摘要", "黔工信投〔2025〕156 号 30,000,000"]],
             note="读取组织架构/董事会决议/期后文件/政府补助摘要（MD+CSV）。"),
        dict(id="P6", layer=1, name="P6 勾稽门户", cap="reconcile · 36 项双读数对撞",
             finding="勾稽 35✓ / 1✗（归 F5）",
             kpis=[[35, "勾稽通过 (36 项)", 0], [0.01, "容差 ±0.05", 2]],
             rows=[["试算平衡·黔岭", "0.00 / 0.00 ✓"], ["折旧可复算 FA-P-008", "0.00 / 3,325,000 ✗"], ["账龄合计", "1,182,000,000 = 应收 ✓"]],
             note="36 项双读数对撞复算；1 项折旧不符（FA-P-008）归入 F5。"),
        dict(id="w_evid", layer=1, virtual=True, name="证据工件池 E3", cap="WAS · L0→P7–P16 取证分发",
             rows=[["承载边", "E3（5 进 / 10 出）"]],
             note="虚拟汇聚节点：承载 E3 逐域证据工件向 10 个实质性程序的批量分发，非独立插件。"),
        dict(id="P7", layer=2, name="P7 T1 收入截止", cap="cutoff.sales", finding="F1 收入跨期",
             rows=[["销售明细:出库/签收日期", "vs 记账日期"]], note="出库/签收期后但收入已入账 → 收入截止错报 F1。"),
        dict(id="P8", layer=2, name="P8 T2 关联方识别", cap="related.party", finding="F2 关联方漏报",
             rows=[["供应商工商核查", "管理层漏报"]], note="供应商工商核查发现管理层漏报关联方 → F2。"),
        dict(id="P9", layer=2, name="P9 T3 坏账重算", cap="aging.recompute", finding="F3 坏账计提不足",
             rows=[["1-2 年账龄 ×30%", "重算"]], note="1-2 年账龄 ×30% 重算坏账 → F3。"),
        dict(id="P10", layer=2, name="P10 T4 存货跌价", cap="nrv.test", finding="F4 跌价不足",
             rows=[["成本 − 可变现净值", "孰低复核"]], note="成本−可变现净值复核 → F4。"),
        dict(id="P11", layer=2, name="P11 T5 折旧重算", cap="dep.recompute", finding="F5 折旧 3,325,000",
             rows=[["FA-P-008 原值", "70,000,000"], ["应提 3,325,000 / 账面 0.00", "✗"]],
             note="卡片参数直线法逐卡重算 → F5（FA-P-008 本期未提折旧）。"),
        dict(id="P12", layer=2, name="P12 T6 资产确认", cap="asset.cap", finding="F6 费用化不足",
             rows=[["无形及长摊", "无未来经济利益资本化"]], note="无未来经济利益资本化 → F6。"),
        dict(id="P13", layer=2, name="P13 T7 其他应收款", cap="other.receivable", finding="F7 垫付 25,000,000",
             rows=[["凭证 1221 黔岭物流", "25,000,000（无协议不计息）"]],
             note="无协议不计息垫付 → F7（仅披露）。"),
        dict(id="P14", layer=2, name="P14 T8 补助分类", cap="grant.class", finding="F8 补助 30,000,000",
             rows=[["黔工信投〔2025〕156 号", "30,000,000 与资产相关"]],
             note="与资产相关补助误入其他收益 → F8。"),
        dict(id="P15", layer=2, name="P15 T9 银行调节", cap="bank.recon", finding="F9 未达调节",
             rows=[["日记账 D = 对账单 G", "双向未达调节"]], note="双向未达调节 D=G → F9（仅披露）。"),
        dict(id="P16", layer=2, name="P16 T10 薪酬截止", cap="cutoff.salary", finding="F10 奖金 18,000,000",
             rows=[["董事会决议批准奖金", "18,000,000"], ["账面未计提", "✗"]],
             note="已批未提奖金 → F10。"),
        dict(id="P17", layer=3, name="P17 重大性判定", cap="materiality",
             kpis=[[5, "重大错报 (金额≥21,400,000)", 0], [10, "发现输入 F1–F10", 0]],
             rows=[["重大性阈值", "21,400,000"], ["判定项", "5 项重大"]],
             note="金额≥21,400,000 记重大错报（5 项），其余降为非重大。"),
        dict(id="P18", layer=3, name="P18 调整分录", cap="entries.adjust",
             kpis=[[7, "组调整分录 (income_effect)", 0]],
             rows=[["收入/费用/减值调整", "7 组"], ["仅披露", "F2 / F7 / F9"]],
             note="7 组收入/费用/减值调整分录，F2/F7/F9 仅披露不调整。"),
        dict(id="P19", layer=4, name="P19 报表重编", cap="restate.fs",
             kpis=[[17, "审定利润表行", 0]],
             rows=[["line_map 映射", "损益调整 → 审定"], ["输出", "审定结果 17 行"]],
             note="line_map 映射损益调整 → 审定利润表。"),
        dict(id="P20", layer=5, kind="sink", name="P20 结论输出", cap="sink.conclusion",
             finding="意见方向：保留",
             kpis=[[1, "输出工件 _audit_digest.json", 0]],
             rows=[["勾稽 35✓/1✗", "汇总"], ["意见方向", "保留"]],
             note="汇总 digest、意见方向（保留）。"),
    ]
    edges = []
    for f, lbl in [("P1", "E1"), ("P2", "E1"), ("P5", "E1"), ("P3", "E1"), ("P4", "E2")]:
        edges.append(dict(from_=f, to="P6", label=lbl))
    for f in ["P1", "P2", "P3", "P4", "P5"]:
        edges.append(dict(from_=f, to="w_evid", label="E3"))
    for i in range(7, 17):
        edges.append(dict(from_="w_evid", to="P%d" % i, label="E3", virtual=True))
    for i in range(7, 17):
        edges.append(dict(from_="P%d" % i, to="P17", label="E5"))
    edges.append(dict(from_="P17", to="P18", label="E6"))
    edges.append(dict(from_="P18", to="P19", label="E7"))
    edges.append(dict(from_="P19", to="P20", label="E8"))
    edges.append(dict(from_="P6", to="P20", label="E8"))
    return dict(
        title="黔岭酒业 2025 年度财务报表审计 · 组网复算 DAG（基础版）",
        sub="20 插件 / E1–E8 边 / F1–F10 发现 · 浏览器端确定性渲染",
        plan="basic-qianling-2025",
        dataset="01_被审计单位提供资料", files="账务/业务/税务/银行/治理 五域", rows="凭证 178 行", sha="",
        source="审计项目案例/黔岭酒业2025年度财务报表审计/01_被审计单位提供资料/",
        foot="数据编号与边定义见《审计报告-黔岭酒业2025年度财务报表审计.md》§7/§8；本页为纯只读演示，不写库、不进网络、零执行副作用。",
        layers=L, nodes=nodes, edges=edges,
        stats=[["插件", "20 (P1–P20)"], ["边", "E1–E8"], ["发现", "F1–F10"], ["勾稽", "35/36"], ["意见", "保留"]])


# --------------------------------------------------------------------------
# 报告 2：实验组（黔岭酒业2025年度财务报表审计_实验组）
# --------------------------------------------------------------------------
def spec_exp():
    L = ["数据接入 L0", "勾稽门户 L1", "实质性程序 L2 ×28", "发现收敛 L3", "报表影响 L4", "结论与评测 L5"]
    domains = [("P-books", "账务域 P-books", "06 文件"), ("P-biz", "业务域 P-biz", "28+2MD 文件"),
               ("P-gov", "治理域 P-gov", "04 文件"), ("P-tax", "税务域 P-tax", "3+1 文件"),
               ("P-bank", "银行域 P-bank", "3+1 文件"), ("P-oth", "其他域 P-oth", "1+4 文件")]
    nodes = [dict(id=d[0], layer=0, name=d[1], cap=d[2],
                  rows=[["行式工件", "全量转入 L1/L2"]],
                  note="L0 数据域接入：单文件原样读入 + 截断标注，工件化后进入门户与取证分发。") for d in domains]
    rules = [
        ("P01", "sales_cutoff 收入截止", "F1 · E09"), ("P02", "goods_flow 实物流转", "E01"),
        ("P03", "aging_recompute 账龄重算", "F3 · E01"), ("P04", "nrv_test 跌价测试", "F4"),
        ("P05", "inventory_count 存货监盘", "E06 · E15"), ("P06", "cogs_recompute 成本倒算", "E03"),
        ("P07", "dep_recompute 折旧重算", "F5"), ("P08", "impairment_test 减值测试", "E18"),
        ("P09", "fa_count 固资监盘", "E19"), ("P10", "cip_transfer 在建转固", "E17"),
        ("P11", "asset_condition 资产确认", "F6"), ("P12", "expense_cutoff 费用截止", "E05 · E24"),
        ("P13", "voucher_review 凭证审查", "E05 · E31"), ("P14", "ap_lookup 未入账负债", "E02 · E22"),
        ("P15", "payroll_recompute 社保交叉", "E23"), ("P16", "salary_cutoff 薪酬截止", "F10"),
        ("P17", "related_party 关联方穿透", "F2 · F7 · E04 · E27"), ("P18", "bank_recon 银行调节", "F9"),
        ("P19", "ic_reconcile 内部往来", "E30"), ("P20", "consolidation_scope 合并范围", "E29"),
        ("P21", "disclosure_lookup 披露检查", "E12·E14·E16·E20·E26·E28"), ("P22", "note_reclass 票据置换", "E07"),
        ("P23", "revenue_concentration 收入集中度", "E12"), ("P24", "contract_clause 合同条款", "E11·E13·E32"),
        ("P25", "gov_grant_class 补助分类", "F8 · E08"), ("P26", "aging_lookup 预付账龄", "E25"),
        ("P27", "subsequent_return 期后事项", "E10"), ("P28", "dev_cost_test 研发归集", "E21"),
    ]
    for pid, name, fnd in rules:
        nodes.append(dict(id=pid, layer=2, name=pid + " " + name, cap="rule." + pid.lower(),
                          finding="发现 " + fnd,
                          rows=[["检出编号", fnd]],
                          note="规则 " + pid + "（T2）逐条产出带文件+数字的证据引用；规则明细见报告 §7。"))
    nodes += [
        dict(id="P29", layer=1, name="P29 勾稽门户", cap="reconcile · 试算平衡(3主体)+六域建档",
             kpis=[[3, "平衡主体（差额 0.00）", 0]],
             rows=[["试算平衡", "3/3 ✓"], ["六域建档", "全量行式工件"]],
             note="试算平衡（3 主体）+ 六域建档为自洽锚点。"),
        dict(id="w_evid", layer=1, virtual=True, name="取证工件池 G02", cap="WAS · L0→P01–P28 取证分发",
             rows=[["程序级取证边", "28 条（并行）"]],
             note="虚拟汇聚节点：承载 G02 下 28 条 L0→程序取证边，非独立插件。"),
        dict(id="P30", layer=3, name="P30 发现收敛", cap="classify · 42 项",
             kpis=[[42, "发现 (F1–F10/E01–E32)", 0], [32, "需调整 / 1,083,925,000", 0]],
             rows=[["按性质", "错报 27 / 舞弊 4 / 披露 9 / 内控 2"], ["编号归口", "42/42"]],
             note="42 项按性质/严重/处理归类、编号归口。"),
        dict(id="P31", layer=4, name="P31 报表影响评估", cap="impact.eval",
             kpis=[[1083925000, "需调整金额合计", 0]],
             rows=[["需调整", "32 项"], ["影响程度判断", "待办重编(演示)"]],
             note="需调整金额汇总 → 影响程度判断（演示留待报表重编）。"),
        dict(id="P32", layer=5, kind="sink", name="P32 结论与评测", cap="eval · recall",
             finding="意见方向：保留",
             kpis=[[1, "召回率 100.0%", 2], [0, "误报", 0]],
             rows=[["对拍基准", "_ground_truth"], ["口径", "42/42、0 误报、保留意见"]],
             note="召回/误报比对标准答案 → 42/42、0 误报、意见方向保留。"),
    ]
    edges = []
    for d in domains:
        edges.append(dict(from_=d[0], to="P29", label="G01"))
        edges.append(dict(from_=d[0], to="w_evid", label="G02"))
    for pid, _, _ in rules:
        edges.append(dict(from_="w_evid", to=pid, label="G02", virtual=True))
    for pid, _, _ in rules:
        edges.append(dict(from_=pid, to="P30", label="G03"))
    edges.append(dict(from_="P30", to="P31", label="G04"))
    edges.append(dict(from_="P31", to="P32", label="G05"))
    return dict(
        title="黔岭酒业 2025 年度财务报表审计（实验组）· 组网复算 DAG",
        sub="32 插件 / G01–G05 / 42 项发现 (F1–F10/E01–E32) · 浏览器端确定性渲染",
        plan="exp-qianling-2025",
        dataset="01_被审计单位提供资料（8 域 53 文件）", files="8 域 53 文件", rows="凭证 200 行", sha="",
        source="审计项目案例/黔岭酒业2025年度财务报表审计_实验组/01_被审计单位提供资料/",
        foot="节点与边定义见《审计报告-黔岭酒业2025年度财务报表审计(实验组).md》§7/§8；本页为纯只读演示，不写库、不进网络、零执行副作用。",
        layers=L, nodes=nodes, edges=edges,
        stats=[["程序", "P01–P28"], ["发现", "42/42"], ["需调整", "1,083,925,000"], ["误报", "0"], ["意见", "保留"]])


# --------------------------------------------------------------------------
# 报告 3：高难度（实验组_高难度）
# --------------------------------------------------------------------------
def spec_hard():
    L = ["数据接入 L0 · 7 域", "勾稽门户 L1", "跨表勾稽 T1/T2", "分析性程序 T3", "准则与证据 T4/T5",
         "噪音排除旁路", "发现收敛 L3'", "影响汇总 L4'", "结论与评测 L5"]
    doms = [("S1", "S1 资料总览", "01 文件"), ("S2", "S2 账务资料", "07 文件"), ("S3", "S3 业务资料", "19 文件"),
            ("S4", "S4 治理资料", "06 文件"), ("S5", "S5 税务资料", "04 文件"), ("S6", "S6 银行资料", "05 文件"),
            ("S7", "S7 其他资料", "09 文件")]
    l2 = [
        ("P1", "暂估冲回核对", "H01 6,800,000", "发票×入库暂估 + 预付款账龄"),
        ("P2", "账龄重算", "H02 4,500,000", "重排档位×比例 − 已提"),
        ("P3", "折旧逐卡复算", "H03 451,250 净", "原值×(1−残值)÷年限÷12；次月起提/封顶"),
        ("P4", "税费跨主体归属", "H04 2,300,000", "计提主体×缴纳主体交叉"),
        ("P5", "暂估完整性", "H07 3,100,000", "入库单“对应发票”为空"),
        ("P6", "费用截止", "H14 490,000(12笔)", "单据日期≥2026 记 2025"),
        ("P7", "收入截止", "H15 1,240,000(3笔)", "出库≥2026-01 收入入 2025"),
        ("P8", "固资监盘", "H16 22,833,333.33", "实盘 < 账面 → 盘亏未处理"),
        ("P9", "银行双向核对", "H22 18,000,000(3笔)", "日记账借方逐笔匹配对账单"),
        ("P10", "单据连续性", "H23 缺 13 张", "出库单号区间扫描 87/100"),
    ]
    l3 = [
        ("P11", "成本倒推", "H05 38,000,000", "季度批发成本率环比 → 倒推应结转"),
        ("P12", "周转与减值迹象", "H06 9,600,000", "周转天数同比 ↑ + 减值迹象"),
        ("P13", "费用率联动", "H08 7,400,000", "费用率 7.50%→6.96% 反向变动"),
        ("P14", "复合计征复核", "H11 3,821,240", "从价×20% + 从量×0.5 元/500ml"),
    ]
    l4 = [
        ("P15", "回购条款", "H09 120,000,000", "实质重于形式：回购/固定回报"),
        ("P16", "总额净额", "H10 180,000,000", "代理业务 → 净额法"),
        ("P17", "年限横向比对", "H12 13,490,000", "卡片年限 vs 政策区间"),
        ("P18", "租赁入表", "H13 38,965,500", "ZL 合同识别 + 折现"),
        ("P19", "受限资产披露", "H17 200,000,000", "质押清单 × 附注“受限”"),
        ("P20", "担保披露", "H18 120,000,000", "决议通读“增信支持”条款"),
        ("P21", "或有负债", "H19 25,000,000", "一审败诉+上诉 → 应确认"),
        ("P22", "期后事项", "H20 620,000,000", "报出日前重大投资 → 披露"),
        ("P23", "客户集中度", "H21 披露缺口", "前五名占比 102.06% 矛盾"),
        ("P24", "两层穿透", "H24 26,000,000", "股权链 × 近亲属申报交叉"),
    ]
    noise = [("PN1", "关联交易排除", "N01 通过·不报"), ("PN2", "估计变更排除", "N02 通过·不报"),
             ("PN3", "季节波动排除", "N03 通过·不报"), ("PN4", "募投一致性排除", "N04 通过·不报"),
             ("PN5", "跌价转回排除", "N05 通过·不报")]
    nodes = [dict(id=d[0], layer=0, name=d[1], cap=d[2],
                  rows=[["行式工件", "全量录入"]],
                  note="L0 七域接入（51 文件 / 35 张 CSV 普查）：单文件原样读入、标注工件，供门户与取证。") for d in doms]
    for pid, nm, fh, brief in l2:
        nodes.append(dict(id=pid, layer=2, name=pid + " " + nm, cap="T2 跨表勾稽", finding="发现 " + fh,
                          rows=[["原理", brief]], note="P" + pid + " → " + fh + "（证据=文件+行/值+单据编号，详见报告 §7）。"))
    for pid, nm, fh, brief in l3:
        nodes.append(dict(id=pid, layer=3, name=pid + " " + nm, cap="T3 分析性程序", finding="发现 " + fh,
                          rows=[["原理", brief]], note="P" + pid + " → " + fh + "（率比/结构分析类发现）。"))
    for pid, nm, fh, brief in l4:
        nodes.append(dict(id=pid, layer=4, name=pid + " " + nm, cap="T4/T5 准则与证据", finding="发现 " + fh,
                          rows=[["原理", brief]], note="P" + pid + " → " + fh + "（准则套用/职业判断类发现）。"))
    for pid, nm, fh in noise:
        nodes.append(dict(id=pid, layer=5, name=pid + " " + nm, cap="噪音排除 · 平行旁路", finding=fh,
                          rows=[["结论", "通过（不报）"]],
                          note="对相似噪音项完整核对后放行，防止误报。"))
    nodes += [
        dict(id="P25", layer=1, name="P25 勾稽门户", cap="reconcile · 3 主体试算平衡",
             kpis=[[3, "平衡主体（差额 0.00）", 0]],
             rows=[["“账必平”", "把全部错报逼入单据层"]],
             note="3 主体科目余额表试算平衡 + 七域工件建档；3/3 差额 0.00。"),
        dict(id="w_evid", layer=1, virtual=True, name="取证工件池 G02", cap="WAS · L0→P1–P24 取证分发（40 条）",
             rows=[["程序级取证边", "40 条（并行）"]],
             note="虚拟汇聚节点：承载 G02 下 40 条 L0→P1–P24 取证边，非独立插件。"),
        dict(id="w_noise", layer=1, virtual=True, name="噪音核对工件池 G03", cap="WAS · L0→PN1–PN5 核对边（5 条）",
             rows=[["程序级核对边", "5 条（平行旁路）"]],
             note="虚拟汇聚节点：承载 G03 下 5 条 L0→噪音排除器核对边，非独立插件。"),
        dict(id="P26", layer=6, name="P26 发现收敛", cap="classify · 24 项 + 5 项排除",
             kpis=[[24, "发现 (H01–H24)", 0], [5, "排除 (N01–N05)", 0]],
             rows=[["按认定/严重度/性质", "归类编号"], ["需调整合计", "389,185,000.00（金额类 22 项）"]],
             note="24 项按认定/严重度/性质归类编号，与 5 项排除结论汇总。"),
        dict(id="P27", layer=7, name="P27 影响汇总", cap="impact.rollup",
             kpis=[[389185000, "需调整金额合计", 0]],
             rows=[["剔除非金额项", "22 项金额类"], ["口径", "389,185,000.00"]],
             note="需调整金额按科目汇总（剔除非金额项）。"),
        dict(id="P28", layer=8, kind="sink", name="P28 结论与评测", cap="eval · 对拍 _ground_truth",
             finding="24/24 命中 · 5/5 排除 · 0 误报",
             kpis=[[24, "命中 /24", 0], [0, "误报", 0]],
             rows=[["噪音排除", "5/5"], ["意见方向", "保留"]],
             note="逐项结果与 _ground_truth 对拍 → 24/24 命中、5/5 排除、0 误报 → 意见方向保留。"),
    ]
    edges = []
    for d in doms:
        edges.append(dict(from_=d[0], to="P25", label="G01"))
        edges.append(dict(from_=d[0], to="w_evid", label="G02"))
        edges.append(dict(from_=d[0], to="w_noise", label="G03"))
    for pid, _, _, _ in l2 + l3 + l4:
        edges.append(dict(from_="w_evid", to=pid, label="G02", virtual=True))
    for pid, _, _ in noise:
        edges.append(dict(from_="w_noise", to=pid, label="G03", virtual=True))
    for pid, _, _, _ in l2 + l3 + l4:
        edges.append(dict(from_=pid, to="P26", label="G04"))
    for pid, _, _ in noise:
        edges.append(dict(from_=pid, to="P26", label="G05"))
    edges.append(dict(from_="P26", to="P27", label="G06"))
    edges.append(dict(from_="P27", to="P28", label="G07"))
    return dict(
        title="黔岭酒业 2025 年度财务报表审计（实验组_高难度）· 组网复算 DAG",
        sub="P1–P28 + PN1–PN5 / G01–G07 / H01–H24 + N01–N05 · 浏览器端确定性渲染",
        plan="hard-qianling-2025",
        dataset="01_被审计单位提供资料（7 域 51 文件）", files="51 文件 · 35 CSV", rows="凭证 116 张 / 235 分录",
        sha="d7da7fdbe0f1ebee14477432c1c05c78cee7dd9ef7e479efedc7b97dd11f795d",
        source="审计项目案例/黔岭酒业2025年度财务报表审计_实验组_高难度/01_被审计单位提供资料/",
        foot="编号与边定义见《审计报告-黔岭酒业2025年度财务报表审计(实验组_高难度).md》§7/§8；证据 sha256 绑定 audit_evidence.json（d7da7ff…）。本页纯只读，不写库、不进网络、零执行副作用。",
        layers=L, nodes=nodes, edges=edges,
        stats=[["程序", "P1–P28 + PN1–PN5"], ["发现", "24/24"], ["噪音排除", "5/5"], ["误报", "0"], ["需调整", "389,185,000"]])


def run():
    import copy
    specs = [
        ("黔岭酒业2025年度财务报表审计", spec_base()),
        ("黔岭酒业2025年度财务报表审计_实验组", spec_exp()),
        ("黔岭酒业2025年度财务报表审计_实验组_高难度", spec_hard()),
    ]
    for folder, sp in specs:
        d = copy.deepcopy(sp)
        d["nodes"] = [dict(n, kpis=[list(k) for k in n.get("kpis", [])],
                           rows=[list(r) for r in n.get("rows", [])]) for n in d["nodes"]]
        d["edges"] = [{"from": e["from_"] if "from_" in e else e["from"],
                       "to": e["to"], "label": e["label"], "virtual": e.get("virtual", False)} for e in d["edges"]]
        d["stats"] = [list(s) for s in d["stats"]]
        d["generated"] = "2026-09-15T00:00:00"
        html = TEMPLATE.replace("@@TITLE@@", d["title"]).replace("@@SUB@@", d["sub"]).replace("@@DAG_JSON@@", json.dumps(d, ensure_ascii=False))
        target = os.path.join(CASE, folder, "_flow_render", "index.html")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(html)
        dur = len(d["layers"]) * 860 + 300
        print("[%s]" % folder)
        print("  -> %s" % target)
        print("  节点=%d 边=%d 层=%d durationMs=%d" % (len(d["nodes"]), len(d["edges"]), len(d["layers"]), dur))
        print("  帧：#t=0 | #t=%d(边传递L1) | #t=%d(L2并行) | #t=%d(全完)" % (700, 1720 + 260, dur - 200))
    print("DONE")


if __name__ == "__main__":
    run()