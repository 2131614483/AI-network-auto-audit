(() => {
  // src/model/flowPlayer.ts
  var NODE_ACTIVE_MS = 480;
  var STEP_GAP_MS = 280;
  var EDGE_FLOW_MS = 280;
  var STEP_MS = NODE_ACTIVE_MS + STEP_GAP_MS;
  var edgeKeyOf = (e) => e.key.join("|");
  function topoOrder(graph, rank = {}) {
    const ids = graph.nodes.map((n) => n.node_instance_id);
    const indeg = new Map(ids.map((id) => [id, 0]));
    const adj = new Map(ids.map((id) => [id, []]));
    for (const e of graph.edges) {
      if (!indeg.has(e.source_instance) || !indeg.has(e.target_instance)) continue;
      adj.get(e.source_instance).push(e.target_instance);
      indeg.set(e.target_instance, (indeg.get(e.target_instance) ?? 0) + 1);
    }
    const layer = new Map(ids.map((id) => [id, 0]));
    const ready = ids.filter((id) => (indeg.get(id) ?? 0) === 0);
    const tie = (a, b) => (rank[a] ?? Number.MAX_SAFE_INTEGER) - (rank[b] ?? Number.MAX_SAFE_INTEGER) || (layer.get(a) ?? 0) - (layer.get(b) ?? 0) || a.localeCompare(b);
    ready.sort(tie);
    const order = [];
    while (ready.length) {
      const id = ready.shift();
      order.push(id);
      for (const next of adj.get(id) ?? []) {
        const d = (indeg.get(next) ?? 0) - 1;
        indeg.set(next, d);
        if (d === 0) {
          ready.push(next);
          ready.sort(tie);
        }
      }
    }
    for (const id of order) {
      for (const next of adj.get(id) ?? []) {
        if (!order.includes(next)) continue;
        layer.set(next, Math.max(layer.get(next) ?? 0, (layer.get(id) ?? 0) + 1));
      }
    }
    for (const id of ids) {
      if (!order.includes(id)) order.push(id);
    }
    return { order, layer: Object.fromEntries(layer) };
  }
  function nodeKindOf(id, incoming, outgoing) {
    const hasIn = (incoming[id]?.length ?? 0) > 0;
    const hasOut = (outgoing[id]?.length ?? 0) > 0;
    if (!hasIn) return "seed";
    if (!hasOut) return "sink";
    return "computed";
  }
  function buildTimeline(projection2, rank = {}) {
    const { order, layer } = topoOrder(projection2, rank);
    const orderIndex = new Map(order.map((id, index) => [id, index]));
    const incoming = {};
    const outgoing = {};
    const edges = [];
    for (const e of projection2.edges) {
      const sourceOrder = orderIndex.get(e.source_instance);
      if (sourceOrder === void 0 || orderIndex.get(e.target_instance) === void 0) continue;
      const edge = {
        key: edgeKeyOf(e),
        source: e.source_instance,
        target: e.target_instance,
        sourcePort: e.source_port,
        targetPort: e.target_port,
        flowStart: 0,
        flowEnd: 0
      };
      edges.push(edge);
      (outgoing[e.source_instance] ??= []).push(edge);
      (incoming[e.target_instance] ??= []).push(edge);
    }
    const nodes = {};
    order.forEach((id, index) => {
      const enterAt = index * STEP_MS;
      nodes[id] = {
        id,
        order: index,
        layer: layer[id] ?? 0,
        enterAt,
        doneAt: enterAt + NODE_ACTIVE_MS,
        kind: nodeKindOf(id, incoming, outgoing)
      };
    });
    for (const edge of edges) {
      const source = nodes[edge.source];
      edge.flowStart = source ? source.doneAt : 0;
      edge.flowEnd = edge.flowStart + EDGE_FLOW_MS;
    }
    const lastOrder = order.length - 1;
    const lastNode = order.length ? nodes[order[lastOrder]] : void 0;
    const maxEdgeEnd = edges.reduce((m, e) => Math.max(m, e.flowEnd), 0);
    const durationMs = Math.max(lastNode ? lastNode.doneAt : 0, maxEdgeEnd) + STEP_GAP_MS;
    return { durationMs, nodeOrder: order, nodes, edges, incoming, outgoing };
  }
  function buildFrame(timeline2, t2) {
    const time = Math.max(0, Math.min(t2, timeline2.durationMs));
    const nodePhase = {};
    let doneCount = 0;
    for (const id of timeline2.nodeOrder) {
      const n = timeline2.nodes[id];
      const phase = time < n.enterAt ? "idle" : time < n.doneAt ? "active" : "done";
      nodePhase[id] = phase;
      if (phase === "done") doneCount += 1;
    }
    const edgePacket = {};
    const edgeReached = {};
    for (const e of timeline2.edges) {
      if (time >= e.flowEnd) edgeReached[e.key] = true;
      else if (time > e.flowStart) edgePacket[e.key] = (time - e.flowStart) / EDGE_FLOW_MS;
    }
    return {
      t: time,
      finished: time >= timeline2.durationMs,
      progress: timeline2.durationMs ? time / timeline2.durationMs : 1,
      doneCount,
      total: timeline2.nodeOrder.length,
      nodePhase,
      edgePacket,
      edgeReached
    };
  }
  function runRank(nodes) {
    const sorted = [...nodes].sort((a, b) => {
      if (a.attempt_seq !== b.attempt_seq) return a.attempt_seq - b.attempt_seq;
      return String(a.created_at ?? "").localeCompare(String(b.created_at ?? ""));
    });
    return Object.fromEntries(sorted.map((n, i) => [n.node_instance_id, i]));
  }
  var FLOW_NODE_W = 216;
  var FLOW_HEADER = 46;
  var FLOW_PORT_ROW = 19;
  var FLOW_FOOT = 20;
  var FLOW_LAYER_GAP = 118;
  var FLOW_ROW_GAP = 34;
  var SEED_PORT = "\u6570\u636E\u5165\u53E3";
  var SINK_PORT = "\u6700\u7EC8\u8F93\u51FA";
  function uniquePorts(values) {
    const seen = /* @__PURE__ */ new Set();
    const out = [];
    for (const v of values) {
      if (!seen.has(v)) {
        seen.add(v);
        out.push(v);
      }
    }
    return out;
  }
  function nodeHeight(inCount, outCount) {
    const rows = Math.max(inCount, outCount, 1);
    return FLOW_HEADER + rows * FLOW_PORT_ROW + FLOW_FOOT;
  }
  function flowLayout(graph, rank = {}) {
    const { order, layer } = topoOrder(graph, rank);
    const inNames = /* @__PURE__ */ new Map();
    const outNames = /* @__PURE__ */ new Map();
    for (const id of order) {
      inNames.set(id, []);
      outNames.set(id, []);
    }
    for (const e of graph.edges) {
      if (!inNames.has(e.target_instance) || !outNames.has(e.source_instance)) continue;
      outNames.get(e.source_instance).push(e.source_port);
      inNames.get(e.target_instance).push(e.target_port);
    }
    const pre = {};
    const byLayer = /* @__PURE__ */ new Map();
    for (const id of order) {
      const inPorts0 = uniquePorts(inNames.get(id) ?? []);
      const outPorts0 = uniquePorts(outNames.get(id) ?? []);
      const isSeed = inPorts0.length === 0;
      const isSink = outPorts0.length === 0;
      const inPorts = (isSeed ? [SEED_PORT] : inPorts0).map((port, row) => ({ port, row }));
      const outPorts = (isSink ? [SINK_PORT] : outPorts0).map((port, row) => ({ port, row }));
      const h = nodeHeight(inPorts.length, outPorts.length);
      pre[id] = {
        id,
        x: 0,
        y: 0,
        w: FLOW_NODE_W,
        h,
        layer: layer[id] ?? 0,
        inPorts,
        outPorts
      };
      const l = layer[id] ?? 0;
      if (!byLayer.has(l)) byLayer.set(l, []);
      byLayer.get(l).push(id);
    }
    const layerHeight = /* @__PURE__ */ new Map();
    for (const [l, ids] of byLayer) {
      const total = ids.reduce((sum, id) => sum + pre[id].h, 0) + (ids.length - 1) * FLOW_ROW_GAP;
      layerHeight.set(l, total);
    }
    const maxHeight = Math.max(0, ...layerHeight.values());
    const nodes = {};
    for (const [l, ids] of byLayer) {
      const x = l * (FLOW_NODE_W + FLOW_LAYER_GAP);
      let y = Math.max(0, (maxHeight - (layerHeight.get(l) ?? 0)) / 2);
      for (const id of ids) {
        const node = pre[id];
        node.x = x;
        node.y = y;
        nodes[id] = node;
        y += node.h + FLOW_ROW_GAP;
      }
    }
    const portY = (node, side, port) => {
      const ports = side === "in" ? node.inPorts : node.outPorts;
      const row = ports.find((p) => p.port === port)?.row ?? 0;
      return node.y + FLOW_HEADER + row * FLOW_PORT_ROW + FLOW_PORT_ROW / 2;
    };
    const edges = [];
    for (const e of graph.edges) {
      const s = nodes[e.source_instance];
      const t2 = nodes[e.target_instance];
      if (!s || !t2) continue;
      edges.push({
        key: e.key.join("|"),
        source: e.source_instance,
        target: e.target_instance,
        sourcePort: e.source_port,
        targetPort: e.target_port,
        from: { x: s.x + s.w, y: portY(s, "out", e.source_port) },
        to: { x: t2.x, y: portY(t2, "in", e.target_port) }
      });
    }
    const width = Math.max(...order.map((id) => nodes[id].x + nodes[id].w), FLOW_NODE_W);
    const height = Math.max(maxHeight, 220);
    return { width: width + FLOW_LAYER_GAP, height, nodes, edges };
  }

  // flow-demo/entry.ts
  var N = (node_instance_id, capability, plugin_id, outputRefs, inputBindings) => ({
    node_instance_id,
    capability,
    plugin_id,
    attempt_seq: 0,
    attempt_id: `a:${node_instance_id}`,
    status: "succeeded",
    error_kind: null,
    error_message: null,
    worker_id: null,
    input_bindings: inputBindings,
    output_refs: outputRefs,
    trace_id: "demo",
    created_at: null,
    finished_at: null
  });
  var E = (s, sp, t2, tp) => ({
    key: [s, sp, t2, tp],
    source_instance: s,
    source_port: sp,
    target_instance: t2,
    target_port: tp,
    sha256: null,
    uri: null,
    adapter: null,
    attempt_id: "a"
  });
  var ref = (name) => ({
    uri: `file:///staging/demo/${name}.json`,
    sha256: `${name}8f3a91c2b7d4e5f60718293a4b5c6d7e`.slice(0, 64),
    size_bytes: 2048 + name.length * 137
  });
  var projection = {
    run_id: "demo-flow",
    plan_key: "\u51ED\u8BC1\u7A7F\u900F\u2192\u5E95\u7A3F\u2192\u8BC1\u636E\u2192\u95EE\u9898\u5B9A\u6027",
    execution_hash: null,
    status: "succeeded",
    trace_id: "demo",
    created_at: null,
    nodes: [
      N("\u51ED\u8BC1\u6570\u636E\u5165\u53E3", "multi-source.collect", "audit.foundation.multi-source-collect", { "voucher-set": ref("voucher-set") }, {}),
      N(
        "\u51ED\u8BC1\u7A7F\u900F\u67E5\u8BE2",
        "field.voucher-drilldown",
        "audit.field.voucher-drilldown",
        { "voucher-detail": ref("voucher-detail") },
        { "voucher-set": [{ source_instance: "\u51ED\u8BC1\u6570\u636E\u5165\u53E3", source_port: "voucher-set", sha256: "x", uri: "file:///v", adapter: null }] }
      ),
      N(
        "\u8D22\u52A1\u6570\u636E\u6E05\u6D17",
        "finance.clean",
        "audit.foundation.finance-clean",
        { "clean-ledger": ref("clean-ledger") },
        { "voucher-set": [{ source_instance: "\u51ED\u8BC1\u6570\u636E\u5165\u53E3", source_port: "voucher-set", sha256: "x", uri: "file:///v", adapter: "normalize" }] }
      ),
      N(
        "\u5BA1\u8BA1\u5E95\u7A3F\u7F16\u5236",
        "field.workpaper-build",
        "audit.field.workpaper-build",
        { "workpaper-draft": ref("workpaper-draft") },
        {
          "voucher-detail": [{ source_instance: "\u51ED\u8BC1\u7A7F\u900F\u67E5\u8BE2", source_port: "voucher-detail", sha256: "x", uri: "", adapter: null }],
          "clean-ledger": [{ source_instance: "\u8D22\u52A1\u6570\u636E\u6E05\u6D17", source_port: "clean-ledger", sha256: "x", uri: "", adapter: null }]
        }
      ),
      N(
        "\u8BC1\u636E\u7D22\u5F15\u5173\u8054",
        "evidence.index-link",
        "audit.evidence.evidence-index-link",
        { "evidence-index": ref("evidence-index") },
        { "workpaper-draft": [{ source_instance: "\u5BA1\u8BA1\u5E95\u7A3F\u7F16\u5236", source_port: "workpaper-draft", sha256: "x", uri: "", adapter: null }] }
      ),
      N(
        "\u95EE\u9898\u91D1\u989D\u6838\u7B97",
        "finding.issue-amount",
        "audit.finding.issue-amount-compute",
        { finding: ref("finding") },
        { "evidence-index": [{ source_instance: "\u8BC1\u636E\u7D22\u5F15\u5173\u8054", source_port: "evidence-index", sha256: "x", uri: "", adapter: null }] }
      )
    ],
    edges: [
      E("\u51ED\u8BC1\u6570\u636E\u5165\u53E3", "voucher-set", "\u51ED\u8BC1\u7A7F\u900F\u67E5\u8BE2", "voucher-set"),
      E("\u51ED\u8BC1\u6570\u636E\u5165\u53E3", "voucher-set", "\u8D22\u52A1\u6570\u636E\u6E05\u6D17", "voucher-set"),
      E("\u51ED\u8BC1\u7A7F\u900F\u67E5\u8BE2", "voucher-detail", "\u5BA1\u8BA1\u5E95\u7A3F\u7F16\u5236", "voucher-detail"),
      E("\u8D22\u52A1\u6570\u636E\u6E05\u6D17", "clean-ledger", "\u5BA1\u8BA1\u5E95\u7A3F\u7F16\u5236", "clean-ledger"),
      E("\u5BA1\u8BA1\u5E95\u7A3F\u7F16\u5236", "workpaper-draft", "\u8BC1\u636E\u7D22\u5F15\u5173\u8054", "workpaper-draft"),
      E("\u8BC1\u636E\u7D22\u5F15\u5173\u8054", "evidence-index", "\u95EE\u9898\u91D1\u989D\u6838\u7B97", "evidence-index")
    ]
  };
  var proj = projection;
  var NS = "http://www.w3.org/2000/svg";
  var timeline = buildTimeline(proj, runRank(projection.nodes));
  var laid = flowLayout(proj, runRank(projection.nodes));
  function wirePath(from, to) {
    const dx = Math.max(40, (to.x - from.x) / 2);
    return `M ${from.x} ${from.y} C ${from.x + dx} ${from.y}, ${to.x - dx} ${to.y}, ${to.x} ${to.y}`;
  }
  function cubic(p, a, b) {
    const dx = Math.max(40, (b.x - a.x) / 2);
    const p1 = { x: a.x + dx, y: a.y };
    const p2 = { x: b.x - dx, y: b.y };
    const u = 1 - p;
    return {
      x: u ** 3 * a.x + 3 * u ** 2 * p * p1.x + 3 * u * p ** 2 * p2.x + p ** 3 * b.x,
      y: u ** 3 * a.y + 3 * u ** 2 * p * p1.y + 3 * u * p ** 2 * p2.y + p ** 3 * b.y
    };
  }
  var svg = document.getElementById("stage");
  var VB_W = laid.width + 40;
  var VB_H = laid.height + 40;
  svg.setAttribute("viewBox", `0 0 ${VB_W} ${VB_H}`);
  svg.setAttribute("width", "100%");
  svg.style.maxWidth = `${VB_W}px`;
  svg.style.aspectRatio = `${VB_W} / ${VB_H}`;
  svg.style.height = "auto";
  var rootG = document.createElementNS(NS, "g");
  rootG.setAttribute("transform", "translate(20,20)");
  svg.appendChild(rootG);
  var edgeG = document.createElementNS(NS, "g");
  var nodeG = document.createElementNS(NS, "g");
  rootG.appendChild(edgeG);
  rootG.appendChild(nodeG);
  var edgeEls = {};
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
  var nodeEls = {};
  for (const id of timeline.nodeOrder) {
    const ln = laid.nodes[id];
    const data = projection.nodes.find((n) => n.node_instance_id === id);
    const g = document.createElementNS(NS, "g");
    g.setAttribute("transform", `translate(${ln.x},${ln.y})`);
    g.setAttribute("opacity", "0");
    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("width", String(ln.w));
    rect.setAttribute("height", String(ln.h));
    rect.setAttribute("rx", "10");
    rect.setAttribute("fill", "#0f1720");
    rect.setAttribute("stroke", "#33404f");
    rect.setAttribute("stroke-width", "1.3");
    g.appendChild(rect);
    const title = document.createElementNS(NS, "text");
    title.setAttribute("x", "12");
    title.setAttribute("y", "20");
    title.setAttribute("font-size", "12.5");
    title.setAttribute("font-weight", "600");
    title.setAttribute("fill", "#e5e7eb");
    title.textContent = id;
    g.appendChild(title);
    const cap = document.createElementNS(NS, "text");
    cap.setAttribute("x", "12");
    cap.setAttribute("y", "36");
    cap.setAttribute("font-size", "9.5");
    cap.setAttribute("fill", "#9ca3af");
    cap.textContent = data.capability;
    g.appendChild(cap);
    ln.inPorts.forEach((p) => {
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", "6");
      c.setAttribute("cy", String(46 + p.row * 19 + 9.5));
      c.setAttribute("r", "4");
      c.setAttribute("fill", "#0b0e14");
      c.setAttribute("stroke", "#6b7a90");
      g.appendChild(c);
      const t2 = document.createElementNS(NS, "text");
      t2.setAttribute("x", "15");
      t2.setAttribute("y", String(46 + p.row * 19 + 13));
      t2.setAttribute("font-size", "9");
      t2.setAttribute("fill", p.port === "\u6570\u636E\u5165\u53E3" ? "#7fe0c0" : "#aab6c6");
      t2.textContent = p.port;
      g.appendChild(t2);
    });
    ln.outPorts.forEach((p) => {
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", String(ln.w - 6));
      c.setAttribute("cy", String(46 + p.row * 19 + 9.5));
      c.setAttribute("r", "4");
      c.setAttribute("fill", "#0b0e14");
      c.setAttribute("stroke", "#6b7a90");
      g.appendChild(c);
      const t2 = document.createElementNS(NS, "text");
      t2.setAttribute("x", String(ln.w - 15));
      t2.setAttribute("y", String(46 + p.row * 19 + 13));
      t2.setAttribute("font-size", "9");
      t2.setAttribute("text-anchor", "end");
      t2.setAttribute("fill", p.port === "\u6700\u7EC8\u8F93\u51FA" ? "#ffd98a" : "#aab6c6");
      t2.textContent = p.port;
      g.appendChild(t2);
    });
    const state = document.createElementNS(NS, "text");
    state.setAttribute("x", "12");
    state.setAttribute("y", String(ln.h - 6));
    state.setAttribute("font-size", "9");
    state.dataset.role = "state";
    g.appendChild(state);
    nodeEls[id] = g;
    nodeG.appendChild(g);
  }
  var t = 0;
  var playing = true;
  var last = performance.now();
  var hud = document.getElementById("hud");
  function render(now) {
    if (playing) {
      t += now - last;
      if (t >= timeline.durationMs) t = timeline.durationMs;
    }
    last = now;
    const frame = buildFrame(timeline, t);
    for (const id of timeline.nodeOrder) {
      const phase = frame.nodePhase[id];
      const g = nodeEls[id];
      const rect = g.querySelector("rect");
      const state = g.querySelector('[data-role="state"]');
      if (phase === "idle") {
        g.setAttribute("opacity", "0.34");
        rect.setAttribute("stroke", "#232c39");
        state.setAttribute("fill", "#5a6675");
        state.textContent = "\u5F85\u5904\u7406";
        continue;
      }
      g.setAttribute("opacity", "1");
      if (phase === "active") {
        rect.setAttribute("stroke", "#ffd98a");
        state.setAttribute("fill", "#ffd98a");
        state.textContent = "\u25CF \u5904\u7406\u4E2D\u2026";
      } else {
        rect.setAttribute("stroke", "#52e08a");
        state.setAttribute("fill", "#52e08a");
        state.textContent = "\u2713 \u5DF2\u4EA7\u51FA";
      }
    }
    for (const e of laid.edges) {
      const path = edgeEls[e.key];
      const packet = edgeG.querySelector(`[data-role="packet"][data-key="${CSS.escape(e.key)}"]`);
      const pk = frame.edgePacket[e.key];
      if (pk !== void 0) {
        path.setAttribute("stroke", "#ffd98a");
        path.setAttribute("stroke-width", "2.4");
        const pt = cubic(pk, e.from, e.to);
        packet.setAttribute("cx", String(pt.x));
        packet.setAttribute("cy", String(pt.y));
        packet.setAttribute("opacity", "1");
      } else if (frame.edgeReached[e.key]) {
        path.setAttribute("stroke", "#52e08a");
        path.setAttribute("stroke-width", "1.6");
        path.setAttribute("stroke-dasharray", "7 5");
        packet.setAttribute("opacity", "0");
      } else {
        path.setAttribute("stroke", "#2b3a4d");
        packet.setAttribute("opacity", "0");
      }
    }
    hud.textContent = `\u5DF2\u6FC0\u6D3B ${frame.doneCount}/${frame.total} \xB7 ${Math.round(frame.progress * 100)}% \xB7 \u6570\u636E\u6CBF\u7AEF\u53E3\u8FDE\u7EBF\u6D41\u52A8\uFF0C\u91D1\u8272=\u6B63\u5728\u4F20\u9012\uFF0C\u7EFF\u8272\u865A\u7EBF=\u5DF2\u9001\u8FBE`;
    if (playing && t < timeline.durationMs) requestAnimationFrame(render);
  }
  requestAnimationFrame(render);
  window.__flowDemo = {
    seek: (ms) => {
      playing = false;
      t = Math.max(0, Math.min(ms, timeline.durationMs));
      requestAnimationFrame((n) => render(n));
    },
    play: () => {
      if (t >= timeline.durationMs) t = 0;
      playing = true;
      last = performance.now();
      requestAnimationFrame(render);
    },
    pause: () => {
      playing = false;
    },
    info: () => ({ durationMs: timeline.durationMs, nodes: timeline.nodeOrder.length, edges: laid.edges.length })
  };
})();
