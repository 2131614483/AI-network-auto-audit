// Offline, self-contained ComfyUI-style data-flow demo. It reuses the SAME pure
// flowPlayer model the React RunCanvas uses, fed by a synthetic audit pipeline
// (no DB / no backend / no execution). Used for visual acceptance and as a
// standalone animation preview. Bundled by esbuild into src/public.

import {
  buildFrame,
  buildTimeline,
  flowLayout,
  runRank,
} from "../src/model/flowPlayer";

const N = (
  node_instance_id: string, capability: string, plugin_id: string,
  outputRefs: Record<string, { uri: string; sha256: string; size_bytes: number }>,
  inputBindings: Record<string, unknown>,
) => ({
  node_instance_id, capability, plugin_id, attempt_seq: 0, attempt_id: `a:${node_instance_id}`,
  status: "succeeded", error_kind: null, error_message: null, worker_id: null,
  input_bindings: inputBindings, output_refs: outputRefs, trace_id: "demo",
  created_at: null, finished_at: null,
});
const E = (s: string, sp: string, t: string, tp: string) => ({
  key: [s, sp, t, tp] as [string, string, string, string],
  source_instance: s, source_port: sp, target_instance: t, target_port: tp,
  sha256: null, uri: null, adapter: null, attempt_id: "a",
});

const ref = (name: string) => ({
  uri: `file:///staging/demo/${name}.json`, sha256: `${name}8f3a91c2b7d4e5f60718293a4b5c6d7e`.slice(0, 64), size_bytes: 2048 + name.length * 137,
});

const projection = {
  run_id: "demo-flow", plan_key: "凭证穿透→底稿→证据→问题定性", execution_hash: null,
  status: "succeeded", trace_id: "demo", created_at: null,
  nodes: [
    N("凭证数据入口", "multi-source.collect", "audit.foundation.multi-source-collect", { "voucher-set": ref("voucher-set") }, {}),
    N("凭证穿透查询", "field.voucher-drilldown", "audit.field.voucher-drilldown", { "voucher-detail": ref("voucher-detail") },
      { "voucher-set": [{ source_instance: "凭证数据入口", source_port: "voucher-set", sha256: "x", uri: "file:///v", adapter: null }] }),
    N("财务数据清洗", "finance.clean", "audit.foundation.finance-clean", { "clean-ledger": ref("clean-ledger") },
      { "voucher-set": [{ source_instance: "凭证数据入口", source_port: "voucher-set", sha256: "x", uri: "file:///v", adapter: "normalize" }] }),
    N("审计底稿编制", "field.workpaper-build", "audit.field.workpaper-build", { "workpaper-draft": ref("workpaper-draft") },
      { "voucher-detail": [{ source_instance: "凭证穿透查询", source_port: "voucher-detail", sha256: "x", uri: "", adapter: null }],
        "clean-ledger": [{ source_instance: "财务数据清洗", source_port: "clean-ledger", sha256: "x", uri: "", adapter: null }] }),
    N("证据索引关联", "evidence.index-link", "audit.evidence.evidence-index-link", { "evidence-index": ref("evidence-index") },
      { "workpaper-draft": [{ source_instance: "审计底稿编制", source_port: "workpaper-draft", sha256: "x", uri: "", adapter: null }] }),
    N("问题金额核算", "finding.issue-amount", "audit.finding.issue-amount-compute", { finding: ref("finding") },
      { "evidence-index": [{ source_instance: "证据索引关联", source_port: "evidence-index", sha256: "x", uri: "", adapter: null }] }),
  ],
  edges: [
    E("凭证数据入口", "voucher-set", "凭证穿透查询", "voucher-set"),
    E("凭证数据入口", "voucher-set", "财务数据清洗", "voucher-set"),
    E("凭证穿透查询", "voucher-detail", "审计底稿编制", "voucher-detail"),
    E("财务数据清洗", "clean-ledger", "审计底稿编制", "clean-ledger"),
    E("审计底稿编制", "workpaper-draft", "证据索引关联", "workpaper-draft"),
    E("证据索引关联", "evidence-index", "问题金额核算", "evidence-index"),
  ],
};
const proj = projection as never;

const NS = "http://www.w3.org/2000/svg";
const timeline = buildTimeline(proj, runRank(projection.nodes as never));
const laid = flowLayout(proj, runRank(projection.nodes as never));

function wirePath(from: { x: number; y: number }, to: { x: number; y: number }): string {
  const dx = Math.max(40, (to.x - from.x) / 2);
  return `M ${from.x} ${from.y} C ${from.x + dx} ${from.y}, ${to.x - dx} ${to.y}, ${to.x} ${to.y}`;
}
function cubic(p: number, a: { x: number; y: number }, b: { x: number; y: number }) {
  const dx = Math.max(40, (b.x - a.x) / 2);
  const p1 = { x: a.x + dx, y: a.y }; const p2 = { x: b.x - dx, y: b.y };
  const u = 1 - p;
  return { x: u ** 3 * a.x + 3 * u ** 2 * p * p1.x + 3 * u * p ** 2 * p2.x + p ** 3 * b.x,
    y: u ** 3 * a.y + 3 * u ** 2 * p * p1.y + 3 * u * p ** 2 * p2.y + p ** 3 * b.y };
}

const svg = document.getElementById("stage") as unknown as SVGSVGElement;
const VB_W = laid.width + 40;
const VB_H = laid.height + 40;
svg.setAttribute("viewBox", `0 0 ${VB_W} ${VB_H}`);
svg.setAttribute("width", "100%");
svg.style.maxWidth = `${VB_W}px`;
svg.style.aspectRatio = `${VB_W} / ${VB_H}`;
svg.style.height = "auto";
const rootG = document.createElementNS(NS, "g");
rootG.setAttribute("transform", "translate(20,20)");
svg.appendChild(rootG);

const edgeG = document.createElementNS(NS, "g");
const nodeG = document.createElementNS(NS, "g");
rootG.appendChild(edgeG); rootG.appendChild(nodeG);

// static edge base paths
const edgeEls: Record<string, SVGPathElement> = {};
for (const e of laid.edges) {
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", wirePath(e.from, e.to));
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#2b3a4d");
  path.setAttribute("stroke-width", "1.5");
  edgeG.appendChild(path);
  edgeEls[e.key] = path;
  const packet = document.createElementNS(NS, "circle");
  packet.setAttribute("r", "4.5");
  packet.setAttribute("fill", "#ffe3a8");
  packet.setAttribute("stroke", "#ffd98a");
  packet.setAttribute("opacity", "0");
  packet.dataset.role = "packet";
  packet.dataset.key = e.key;
  edgeG.appendChild(packet);
}
const nodeEls: Record<string, SVGGElement> = {};
for (const id of timeline.nodeOrder) {
  const ln = laid.nodes[id];
  const data = projection.nodes.find((n) => n.node_instance_id === id)!;
  const g = document.createElementNS(NS, "g");
  g.setAttribute("transform", `translate(${ln.x},${ln.y})`);
  g.setAttribute("opacity", "0");
  const rect = document.createElementNS(NS, "rect");
  rect.setAttribute("width", String(ln.w)); rect.setAttribute("height", String(ln.h));
  rect.setAttribute("rx", "10"); rect.setAttribute("fill", "#0f1720"); rect.setAttribute("stroke", "#33404f");
  rect.setAttribute("stroke-width", "1.3");
  g.appendChild(rect);
  const title = document.createElementNS(NS, "text");
  title.setAttribute("x", "12"); title.setAttribute("y", "20"); title.setAttribute("font-size", "12.5");
  title.setAttribute("font-weight", "600"); title.setAttribute("fill", "#e5e7eb"); title.textContent = id;
  g.appendChild(title);
  const cap = document.createElementNS(NS, "text");
  cap.setAttribute("x", "12"); cap.setAttribute("y", "36"); cap.setAttribute("font-size", "9.5"); cap.setAttribute("fill", "#9ca3af");
  cap.textContent = data.capability;
  g.appendChild(cap);
  ln.inPorts.forEach((p) => {
    const c = document.createElementNS(NS, "circle");
    c.setAttribute("cx", "6"); c.setAttribute("cy", String(46 + p.row * 19 + 9.5)); c.setAttribute("r", "4");
    c.setAttribute("fill", "#0b0e14"); c.setAttribute("stroke", "#6b7a90");
    g.appendChild(c);
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", "15"); t.setAttribute("y", String(46 + p.row * 19 + 13)); t.setAttribute("font-size", "9");
    t.setAttribute("fill", p.port === "数据入口" ? "#7fe0c0" : "#aab6c6"); t.textContent = p.port;
    g.appendChild(t);
  });
  ln.outPorts.forEach((p) => {
    const c = document.createElementNS(NS, "circle");
    c.setAttribute("cx", String(ln.w - 6)); c.setAttribute("cy", String(46 + p.row * 19 + 9.5)); c.setAttribute("r", "4");
    c.setAttribute("fill", "#0b0e14"); c.setAttribute("stroke", "#6b7a90");
    g.appendChild(c);
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", String(ln.w - 15)); t.setAttribute("y", String(46 + p.row * 19 + 13)); t.setAttribute("font-size", "9");
    t.setAttribute("text-anchor", "end"); t.setAttribute("fill", p.port === "最终输出" ? "#ffd98a" : "#aab6c6");
    t.textContent = p.port;
    g.appendChild(t);
  });
  const state = document.createElementNS(NS, "text");
  state.setAttribute("x", "12"); state.setAttribute("y", String(ln.h - 6)); state.setAttribute("font-size", "9");
  state.dataset.role = "state";
  g.appendChild(state);
  nodeEls[id] = g; nodeG.appendChild(g);
}

let t = 0;
let playing = true;
let last = performance.now();
const hud = document.getElementById("hud") as HTMLElement;

function render(now: number) {
  if (playing) { t += now - last; if (t >= timeline.durationMs) t = timeline.durationMs; }
  last = now;
  const frame = buildFrame(timeline, t);
  for (const id of timeline.nodeOrder) {
    const phase = frame.nodePhase[id];
    const g = nodeEls[id];
    const rect = g.querySelector("rect") as SVGRectElement;
    const state = g.querySelector('[data-role="state"]') as SVGTextElement;
    if (phase === "idle") {
      g.setAttribute("opacity", "0.34");
      rect.setAttribute("stroke", "#232c39");
      state.setAttribute("fill", "#5a6675");
      state.textContent = "待处理";
      continue;
    }
    g.setAttribute("opacity", "1");
    if (phase === "active") { rect.setAttribute("stroke", "#ffd98a"); state.setAttribute("fill", "#ffd98a"); state.textContent = "● 处理中…"; }
    else { rect.setAttribute("stroke", "#52e08a"); state.setAttribute("fill", "#52e08a"); state.textContent = "✓ 已产出"; }
  }
  for (const e of laid.edges) {
    const path = edgeEls[e.key];
    const packet = edgeG.querySelector(`[data-role="packet"][data-key="${CSS.escape(e.key)}"]`) as SVGCircleElement;
    const pk = frame.edgePacket[e.key];
    if (pk !== undefined) {
      path.setAttribute("stroke", "#ffd98a"); path.setAttribute("stroke-width", "2.4");
      const pt = cubic(pk, e.from, e.to);
      packet.setAttribute("cx", String(pt.x)); packet.setAttribute("cy", String(pt.y)); packet.setAttribute("opacity", "1");
    } else if (frame.edgeReached[e.key]) {
      path.setAttribute("stroke", "#52e08a"); path.setAttribute("stroke-width", "1.6");
      path.setAttribute("stroke-dasharray", "7 5");
      packet.setAttribute("opacity", "0");
    } else {
      path.setAttribute("stroke", "#2b3a4d"); packet.setAttribute("opacity", "0");
    }
  }
  hud.textContent = `已激活 ${frame.doneCount}/${frame.total} · ${Math.round(frame.progress * 100)}% · 数据沿端口连线流动，金色=正在传递，绿色虚线=已送达`;
  if (playing && t < timeline.durationMs) requestAnimationFrame(render);
}
requestAnimationFrame(render);

(window as unknown as Record<string, unknown>).__flowDemo = {
  seek: (ms: number) => { playing = false; t = Math.max(0, Math.min(ms, timeline.durationMs)); requestAnimationFrame((n) => render(n)); },
  play: () => { if (t >= timeline.durationMs) t = 0; playing = true; last = performance.now(); requestAnimationFrame(render); },
  pause: () => { playing = false; },
  info: () => ({ durationMs: timeline.durationMs, nodes: timeline.nodeOrder.length, edges: laid.edges.length }),
};
