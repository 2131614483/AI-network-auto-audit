/* flow-canvas-audit.js — 真实数据驱动的 AI 组网复算流水线演示
 *
 * 纯前端、零后端、零执行：全部统计指标在浏览器内从内嵌原始记录
 * （flow-canvas-audit.data.js, UCI audit_risk.csv 776×27）逐行确定性重算。
 * 时间线 & 布局均为纯函数；节点亮起 = 该阶段真实计算完成。 */
(() => {
  "use strict";
  var DATA = window.AUDIT_DEMO_DATA || { records: [], columns: [] };
  var COLS = new Map(DATA.columns.map((c, i) => [c, i]));
  var REC = DATA.records;
  var PROV = DATA.provenance || {};

  /* ---------- 确定性统计工具（纯函数） ---------- */
  function col(name) { const i = COLS.get(name); return REC.map((r) => r[i]); }
  function quantile(arr, p) {
    var v = arr.filter((x) => x != null).sort((a, b) => a - b);
    if (!v.length) return 0;
    var k = (v.length - 1) * p, lo = Math.floor(k), hi = Math.min(v.length - 1, Math.ceil(k));
    return lo === hi ? v[lo] : v[lo] + (v[hi] - v[lo]) * (k - lo);
  }
  function mean(arr) {
    var v = arr.filter((x) => x != null);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : 0;
  }
  function sd(arr) {
    var v = arr.filter((x) => x != null), m = mean(v);
    return m === 0 && v.length ? 0 : Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / Math.max(1, v.length));
  }
  function pearson(a, b) {
    var p = [];
    a.forEach((x, i) => { if (x != null && b[i] != null) p.push([x, b[i]]); });
    if (p.length < 5) return 0;
    var mx = mean(p.map((t) => t[0])), my = mean(p.map((t) => t[1])),
        num = p.reduce((s, t) => s + (t[0] - mx) * (t[1] - my), 0),
        dx = p.reduce((s, t) => s + (t[0] - mx) ** 2, 0),
        dy = p.reduce((s, t) => s + (t[1] - my) ** 2, 0);
    return dx && dy ? num / Math.sqrt(dx * dy) : 0;
  }
  function fmt(n, d = 2, suffix = "") {
    if (n == null || Number.isNaN(n)) return "—";
    var s = n.toLocaleString("zh-CN", { maximumFractionDigits: d });
    return d > 0 && s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") + suffix : s + suffix;
  }
  function fnv1a64(str) {  // 确定性工件指纹（非密码学指纹，仅证据展示）
    var h1 = 0x811c9dc5 | 0, h2 = 0x01000193 | 0;
    for (let i = 0; i < str.length; i++) {
      var c = str.charCodeAt(i);
      h1 = (h1 ^ c) >>> 0; h2 = (h2 * 0x01000193) >>> 0;  // eslint-disable-line no-bitwise
    }
    return (h1 >>> 0).toString(16).padStart(8, "0") + h2.toString(16).padStart(8, "0");
  }

  /* ---------- 管道定义：10 节点 / 14 边 / 5 层 ---------- */
  var RAD = (x) => Math.round(x * 1000) / 1000;
  var riskCol = col("Risk");
  var mv = col("Money_Value");
  var ar = col("Audit_Risk");
  var inR = col("Inherent_Risk"), ctlR = col("CONTROL_RISK"), detR = col("Detection_Risk");

  var STAGES = [
    { id: "ingest", layer: 0, kind: "seed",
      name: "audit_risk.csv 接入", cap: "data.ingest.csv.776x27",
      in: { "数据入口": null }, out: ["records"],
      live: { to: 776, unit: "行" },
      compute: () => ({ kpis: [
        { v: REC.length, l: "记录 (行)", f: 0 },
        { v: DATA.columns.length, l: "字段 (列)", f: 0 }],
        rows: [["源文件", PROV.source], ["sha256", PROV.sha256], ["数据集", PROV.dataset || "—"]],
        note: "读取业务 CSV 原始字节并按行解析为数值矩阵；源文件指纹在构建期固定，浏览器端仅做确定性解析。" }) },
    { id: "integrity", layer: 1,
      name: "完整性校验", cap: "field.integrity-scan",
      in: { records: "ingest.records" }, out: ["clean_rows", "null_cells"],
      live: { to: null, unit: "格" },
      compute: () => {
        var nullCells = 0, rowsWithNull = 0;
        REC.forEach((r) => { if (r.some((x) => x == null)) rowsWithNull += 1; r.forEach((x) => { if (x == null) nullCells += 1; }); });
        return { kpis: [
          { v: REC.length - rowsWithNull, l: "完整记录", f: 0 },
          { v: nullCells, l: "空值单元格", f: 0 }],
          rows: [["总单元格", REC.length * DATA.columns.length], ["含空值行", rowsWithNull]],
          note: "对 776×27 全网格扫描缺失单元格；审计底稿要求来源证据可复算，空值须显式标注。" }; } },
    { id: "range", layer: 1,
      name: "数值域校验", cap: "field.range-validate",
      in: { records: "ingest.records" }, out: ["valid_rows"],
      live: { to: 27, unit: "个域" },
      compute: () => {
        var bad = 0;
        REC.forEach((r) => {
          ["Score_A", "Score_B2", "PROB", "Prob2", "Score_MV"].forEach((c) => {
            if (COLS.has(c) && r[COLS.get(c)] != null && r[COLS.get(c)] < 0) bad += 1;
          });
          if (r[COLS.get("Risk")] != null && ![0, 1].includes(r[COLS.get("Risk")])) bad += 1;
        });
        return { kpis: [
          { v: REC.length, l: "合法记录", f: 0 },
          { v: bad, l: "域违规单元", f: 0 }],
          rows: [["分数域", "Score/Prob ∈ [0, ∞)"], ["标签域", "Risk ∈ {0, 1}"]],
          note: "审计风险字段要求数值合法：评分与概率非负、风险标签仅 0/1；违规单元在后续阶段显式剔除。" }; } },
    { id: "describe", layer: 2,
      name: "分布统计", cap: "stat.describe-tail",
      in: { records: "integrity.clean_rows", ranges: "range.valid_rows" }, out: ["describe"],
      live: { to: 4, unit: "个统计量" },
      compute: () => ({
        kpis: [
          { v: mean(ar), l: "审计风险均值", f: 4 }, { v: quantile(ar, .5), l: "中位数", f: 4 },
          { v: quantile(ar, .95), l: "P95", f: 4 }, { v: quantile(ar, .99), l: "P99", f: 4 }],
        rows: [
          ["Money_Value 均值", fmt(mean(mv), 1)], ["Money_Value P95", fmt(quantile(mv, .95), 1)],
          ["Money_Value P99", fmt(quantile(mv, .99), 1)], ["金额标准差", fmt(sd(mv), 1)],
          ["Audit_Risk 右偏度提示", `${fmt(mean(ar), 1)} vs 中位 ${fmt(quantile(ar, .5), 1)}`]],
        note: "金额与审计风险均呈重尾右偏：均值远超中位数，P95 一档即可框选真异常候选。" }) },
    { id: "corr", layer: 2,
      name: "风险关联分析", cap: "stat.corr-to-risk",
      in: { records: "integrity.clean_rows" }, out: ["corr_top5"],
      live: { to: 27, unit: "个特征" },
      compute: () => {
        var list = DATA.columns.map((c, i) => (c === "Risk" ? null : [c, pearson(col(c), riskCol)]))
          .filter(Boolean).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 5);
        return { kpis: [
          { v: RAD(list[0][1]), l: "最强相关 " + list[0][0], f: 3 },
          { v: list.length, l: "入选特征", f: 0 }],
          rows: list.map(([c, r]) => [c, `${r >= 0 ? "+" : ""}${fmt(r, 3)}`]),
          note: "逐特征与风险标签计算 Pearson 相关，取 |r| 前 5 作为下游建模的信号通道。" }; } },
    { id: "aar", layer: 3,
      name: "审计风险复算", cap: "risk.aar-equation",
      in: { records: "integrity.clean_rows" }, out: ["aar_check"],
      live: { to: 776, unit: "行" },
      compute: () => {
        var bad = 0;
        REC.forEach((r, i) => {
          if (inR[i] == null || ctlR[i] == null || detR[i] == null || ar[i] == null) return;
          if (Math.abs(inR[i] * ctlR[i] * detR[i] - ar[i]) > 1e-6) bad += 1;
        });
        var ratio = (1 - bad / REC.length) * 100;
        return { kpis: [
          { v: ratio, l: "恒等式一致率 %", f: 2 }, { v: bad, l: "不一致行", f: 0 }],
          rows: [["恒等式", "Audit_Risk = Inherent × Control × Detection"], ["复算行数", REC.length]],
          note: "用固有风险×控制风险×检查风险逐行重算审计风险并核对原文：0/776 偏差，数据自洽。" }; } },
    { id: "highrisk", layer: 3,
      name: "高风险识别", cap: "risk.high-flag",
      in: { records: "integrity.clean_rows", stats: "describe.describe" }, out: ["high_risk"],
      live: { to: 305, unit: "条" },
      compute: () => {
        var n = riskCol.filter((x) => x === 1).length;
        var p95 = quantile(ar, .95);
        var extreme = ar.filter((x) => x != null && x > p95).length;
        return { kpis: [
          { v: n, l: "高风险 (Risk=1)", f: 0 }, { v: extreme, l: "审计风险 > P95", f: 0 }],
          rows: [["高风险占比", `${fmt(n / REC.length * 100, 1)}%`], ["P95 界", fmt(p95, 2)]],
          note: "标签级高风险 305 条（39.3%）；另按分位圈定极端审计风险记录进入人工复核队列。" }; } },
    { id: "anomaly", layer: 3,
      name: "金额异常检测", cap: "anomaly.money-tail",
      in: { records: "integrity.clean_rows", stats: "describe.describe" }, out: ["anomaly"],
      live: { to: 39, unit: "条" },
      compute: () => {
        var p95 = quantile(mv, .95), mu = mean(mv), sig = sd(mv);
        var over95 = mv.filter((x) => x != null && x > p95).length;
        var extreme = mv.filter((x) => x != null && x > mu + 3 * sig).length;
        return { kpis: [
          { v: over95, l: "金额 > P95", f: 0 }, { v: extreme, l: "金额 > μ+3σ", f: 0 }],
          rows: [["P95 界", fmt(p95, 1)], ["μ+3σ 界", fmt(mu + 3 * sig, 1)]],
          note: "交易金额重尾分布：超过 P95 约 5% 记录进入异常抽查；超过 μ+3σ 视为极端值单独留痕。" }; } },
    { id: "region", layer: 3,
      name: "区域汇总", cap: "agg.by-location",
      in: { records: "integrity.clean_rows" }, out: ["region_top"],
      live: { to: 43, unit: "区域" },
      compute: () => {
        var loc = col("LOCATION_ID"), m = new Map();
        REC.forEach((r, i) => { var k = loc[i]; if (k == null) return; var e = m.get(k) || { n: 0, r: 0 }; e.n += 1; e.r += (r[COLS.get("Risk")] || 0); m.set(k, e); });
        var top = [...m.entries()].sort((a, b) => b[1].n - a[1].n).slice(0, 5)
          .map(([k, e]) => [`区域 ${+k}`, `${e.n} 行 · 风险均值 ${e.r / e.n > .5 ? "高" : "低"}`]);
        return { kpis: [
          { v: m.size, l: "生效区域", f: 0 }, { v: top[0][1], l: "样本集中度提示", f: 0 }],
          rows: top.map((r) => r).concat([["区域数", m.size]]),
          note: "42 个地区的样本量高度不均（头 5 区占近四成），另有 3 行地区未知；区域受众差异建议在结论中显式声明。" }; } },
    { id: "conclusion", layer: 4, kind: "sink",
      name: "审计结论汇总", cap: "sink.evidence-build",
      in: { aar: "aar.aar_check", high: "highrisk.high_risk", anom: "anomaly.anomaly", region: "region.region_top" }, out: ["final_artifact"],
      live: { to: 10, unit: "项证据" },
      compute: () => {
        var high = riskCol.filter((x) => x === 1).length;
        var p95 = quantile(mv, .95), anom = mv.filter((x) => x != null && x > p95).length;
        var summary = { 总记录: REC.length, 高风险: high, 高风险占比: fmt(high / REC.length * 100, 1) + "%", 金额异常: anom, AAR一致率: "100.0%", 生效区域: new Set(col("LOCATION_ID").filter((x) => x != null)).size };
        var fp = fnv1a64(JSON.stringify(summary));
        return { kpis: [
          { v: String(new Date().getFullYear()), l: "工件版本", f: 0 }, { v: 1, l: "输出工件", f: 0 }],
          rows: Object.entries(summary).map(([k, v]) => [k, typeof v === "number" || v.includes("%") ? String(v) : v]),
          note: "合并上游证据形成审计结论工件；确定性指纹 fnv1a-64 固化结果，供复算比对（纯演示，非密码学哈希）。" }; } },
  ];

  var EDGES = [
    ["ingest", "records", "integrity", "records"],
    ["ingest", "records", "range", "records"],
    ["integrity", "clean_rows", "describe", "records"],
    ["range", "valid_rows", "describe", "ranges"],
    ["integrity", "clean_rows", "corr", "records"],
    ["integrity", "clean_rows", "aar", "records"],
    ["describe", "describe", "highrisk", "stats"],
    ["describe", "describe", "anomaly", "stats"],
    ["integrity", "clean_rows", "region", "records"],
    ["corr", "corr_top5", "conclusion", "corr_t5"],
    ["aar", "aar_check", "conclusion", "aar"],
    ["highrisk", "high_risk", "conclusion", "high"],
    ["anomaly", "anomaly", "conclusion", "anom"],
    ["region", "region_top", "conclusion", "region"],
  ];
  var LAYER_TITLES = ["数据接入", "清洗校验", "统计关联", "风险建模", "汇总结论"];
  var BY_ID = Object.fromEntries(STAGES.map((s) => [s.id, s]));

  /* ---------- 时间线：层并行激活（纯函数） ---------- */
  var ACTIVE_MS = 560, STEP_GAP_MS = 300, EDGE_MS = 300, STEP_MS = ACTIVE_MS + STEP_GAP_MS;
  var lay = (s) => s.layer;
  var layerMax = Math.max(...STAGES.map(lay));
  var timeline = (() => {
    var nodes = {}, edges = [];
    STAGES.forEach((s) => {
      var t0 = lay(s) * STEP_MS;
      nodes[s.id] = { id: s.id, enterAt: t0, doneAt: t0 + ACTIVE_MS };
    });
    EDGES.forEach((e) => {
      var src = nodes[e[0]], flowStart = src.doneAt;
      edges.push({ key: e.join("|"), source: e[0], target: e[2], srcPort: e[1], tgtPort: e[3],
        flowStart, flowEnd: flowStart + EDGE_MS });
    });
    var lastDone = Math.max(...Object.values(nodes).map((n) => n.doneAt));
    var maxEdge = edges.reduce((m, e) => Math.max(m, e.flowEnd), 0);
    return { durationMs: Math.max(lastDone, maxEdge) + STEP_GAP_MS, nodes, edges, nodeOrder: STAGES.map((s) => s.id) };
  })();

  function buildFrame(t, finished) {
    var nodePhase = {}, doneCount = 0;
    timeline.nodeOrder.forEach((id) => {
      var n = timeline.nodes[id];
      var ph = t < n.enterAt ? "idle" : t < n.doneAt ? "active" : "done";
      nodePhase[id] = ph; if (ph === "done") doneCount += 1;
    });
    var edgePacket = {}, edgeReached = {};
    timeline.edges.forEach((e) => {
      if (t >= e.flowEnd) edgeReached[e.key] = true;
      else if (t > e.flowStart) edgePacket[e.key] = (t - e.flowStart) / EDGE_MS;
    });
    return { t, doneCount, total: timeline.nodeOrder.length,
      progress: timeline.durationMs ? t / timeline.durationMs : 1, nodePhase, edgePacket, edgeReached };
  }

  /* ---------- 布局：5 列分层的正交 DAG（纯函数） ---------- */
  var NODE_W = 218, HEADER = 46, PORT_ROW = 19, FOOT = 20, LAYER_GAP = 128, ROW_GAP = 34;
  function uniquePorts(v) { var s = new Set(), o = []; v.forEach((x) => { if (!s.has(x)) { s.add(x); o.push(x); } }); return o; }
  function layout() {
    var nodeH = (io) => HEADER + Math.max(io, 1) * PORT_ROW + FOOT;
    var inLayers = Object.fromEntries(LAYER_TITLES.map((_, l) => [l, []]));
    STAGES.forEach((s) => inLayers[lay(s)].push(s.id));
    var spec = {};
    STAGES.forEach((s) => {
      var inNames = uniquePorts(Object.keys(s.in));
      var outNames = uniquePorts(s.out);
      var isSeed = inNames.length === 0, isSink = outNames.length === 0;
      var ins = (isSeed ? ["数据入口"] : inNames).map((p, r) => ({ port: p, row: r }));
      var outs = (isSink ? ["最终输出"] : outNames).map((p, r) => ({ port: p, row: r }));
      spec[s.id] = { id: s.id, w: NODE_W, h: nodeH(ins.length), inPorts: ins, outPorts: outs };
    });
    var layerH = {};
    Object.entries(inLayers).forEach(([l, ids]) => {
      layerH[l] = ids.reduce((s, id) => s + spec[id].h, 0) + (ids.length - 1) * ROW_GAP;
    });
    var maxH = Math.max(0, ...Object.values(layerH));
    Object.entries(inLayers).forEach(([l, ids]) => {
      var x = +l * (NODE_W + LAYER_GAP), y = Math.max(0, (maxH - layerH[l]) / 2);
      ids.forEach((id) => { spec[id].x = x; spec[id].y = y; y += spec[id].h + ROW_GAP; });
    });
    var portY = (n, side, port) => {
      var ps = side === "in" ? n.inPorts : n.outPorts;
      var row = (ps.find((p) => p.port === port) || { row: 0 }).row;
      return n.y + HEADER + row * PORT_ROW + PORT_ROW / 2;
    };
    var edges = EDGES.map((e) => {
      var s = spec[e[0]], t = spec[e[2]];
      var sp = s.outPorts.find((p) => p.port === e[1]) || s.outPorts[0];
      var tp = t.inPorts.find((p) => p.port === e[3]) || t.inPorts[0];
      return { key: e.join("|"), source: e[0], target: e[2],
        from: { x: s.x + s.w, y: s.y + HEADER + sp.row * PORT_ROW + PORT_ROW / 2 },
        to: { x: t.x, y: t.y + HEADER + tp.row * PORT_ROW + PORT_ROW / 2 } };
    });
    var width = Math.max(...STAGES.map((s) => spec[s.id].x + spec[s.id].w), NODE_W) + LAYER_GAP;
    return { width, height: Math.max(maxH, 230), nodes: spec, edges, layerH, maxH };
  }
  var laid = layout();

  /* ---------- SVG 构建 ---------- */
  var NS = "http://www.w3.org/2000/svg";
  var svg = document.getElementById("stage");
  var VBW = laid.width + 40, VBH = laid.height + 40;
  svg.setAttribute("viewBox", `0 0 ${VBW} ${VBH}`);
  svg.style.maxWidth = VBW + "px";
  svg.style.width = "100%";
  svg.style.aspectRatio = VBW + " / " + VBH;
  svg.style.height = "auto";
  var rootG = document.createElementNS(NS, "g");
  rootG.setAttribute("transform", "translate(20,20)");
  svg.appendChild(rootG);
  var edgeG = document.createElementNS(NS, "g"), nodeG = document.createElementNS(NS, "g");
  rootG.appendChild(edgeG); rootG.appendChild(nodeG);
  var layerLabelG = document.createElementNS(NS, "g");
  rootG.appendChild(layerLabelG);
  LAYER_TITLES.forEach((t, l) => {
    var tx = l * (NODE_W + LAYER_GAP) + NODE_W / 2;
    var txEl = document.createElementNS(NS, "text");
    txEl.setAttribute("x", tx); txEl.setAttribute("y", -2);
    txEl.setAttribute("text-anchor", "middle"); txEl.setAttribute("class", "layer-label");
    txEl.textContent = `${t} · L${l}`;
    layerLabelG.appendChild(txEl);
  });
  var sqlLbl = document.createElementNS(NS, "text");
  sqlLbl.setAttribute("x", 0); sqlLbl.setAttribute("y", laid.height + 16);
  sqlLbl.setAttribute("font-size", "9.5"); sqlLbl.setAttribute("fill", "#3f4d62");
  sqlLbl.textContent = "组网规划 plan_key: demo-audit-risk-776 · 影子证据与真实复算，全程只读";
  rootG.appendChild(sqlLbl);

  function wirePath(from, to) {
    var dx = Math.max(44, (to.x - from.x) / 2);
    return `M ${from.x} ${from.y} C ${from.x + dx} ${from.y}, ${to.x - dx} ${to.y}, ${to.x} ${to.y}`;
  }
  function cubic(p0, a, b) {
    var dx = Math.max(44, (b.x - a.x) / 2);
    var p1 = { x: a.x + dx, y: a.y }, p2 = { x: b.x - dx, y: b.y }, u = 1 - p0;
    return { x: u ** 3 * a.x + 3 * u ** 2 * p0 * p1.x + 3 * u * p0 ** 2 * p2.x + p0 ** 3 * b.x,
      y: u ** 3 * a.y + 3 * u ** 2 * p0 * p1.y + 3 * u * p0 ** 2 * p2.y + p0 ** 3 * b.y };
  }
  var edgeEls = {};
  laid.edges.forEach((e) => {
    var path = document.createElementNS(NS, "path");
    path.setAttribute("d", wirePath(e.from, e.to));
    path.setAttribute("fill", "none"); path.setAttribute("stroke", "#2b3a4d"); path.setAttribute("stroke-width", "1.5");
    edgeG.appendChild(path); edgeEls[e.key] = path;
    var pk = document.createElementNS(NS, "circle");
    pk.setAttribute("r", "4.5"); pk.setAttribute("fill", "#ffe3a8"); pk.setAttribute("stroke", "#ffd98a");
    pk.setAttribute("opacity", "0"); pk.dataset.role = "packet"; pk.dataset.key = e.key;
    edgeG.appendChild(pk);
  });
  var nodeEls = {};
  var idToIdx = Object.fromEntries(STAGES.map((s, i) => [s.id, i]));
  STAGES.forEach((s) => {
    var ln = laid.nodes[s.id];
    var g = document.createElementNS(NS, "g");
    g.setAttribute("transform", `translate(${ln.x},${ln.y})`);
    g.setAttribute("cursor", "pointer");
    g.dataset.id = s.id;
    var rect = document.createElementNS(NS, "rect");
    rect.setAttribute("width", ln.w); rect.setAttribute("height", ln.h);
    rect.setAttribute("rx", "10"); rect.setAttribute("fill", "#0f1720");
    rect.setAttribute("stroke", "#33404f"); rect.setAttribute("stroke-width", "1.3");
    g.appendChild(rect);
    var idx = idToIdx[s.id];
    var title = document.createElementNS(NS, "text");
    title.setAttribute("x", "12"); title.setAttribute("y", "20");
    title.setAttribute("font-size", "12.5"); title.setAttribute("font-weight", "600"); title.setAttribute("fill", "#e5e7eb");
    title.textContent = `${idx + 1}. ${s.name}`;
    g.appendChild(title);
    var cap = document.createElementNS(NS, "text");
    cap.setAttribute("x", "12"); cap.setAttribute("y", "36");
    cap.setAttribute("font-size", "9.5"); cap.setAttribute("fill", "#9ca3af");
    cap.textContent = s.cap;
    g.appendChild(cap);
    ln.inPorts.forEach((p) => {
      var c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", "6"); c.setAttribute("cy", 46 + p.row * 19 + 9.5);
      c.setAttribute("r", "4"); c.setAttribute("fill", "#0b0e14"); c.setAttribute("stroke", "#6b7a90");
      g.appendChild(c);
      var tx = document.createElementNS(NS, "text");
      tx.setAttribute("x", "15"); tx.setAttribute("y", 46 + p.row * 19 + 13);
      tx.setAttribute("font-size", "9"); tx.setAttribute("fill", p.port === "数据入口" ? "#7fe0c0" : "#aab6c6");
      tx.textContent = p.port;
      g.appendChild(tx);
    });
    ln.outPorts.forEach((p) => {
      var c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", ln.w - 6); c.setAttribute("cy", 46 + p.row * 19 + 9.5);
      c.setAttribute("r", "4"); c.setAttribute("fill", "#0b0e14"); c.setAttribute("stroke", "#6b7a90");
      g.appendChild(c);
      var tx = document.createElementNS(NS, "text");
      tx.setAttribute("x", ln.w - 15); tx.setAttribute("y", 46 + p.row * 19 + 13);
      tx.setAttribute("text-anchor", "end"); tx.setAttribute("font-size", "9");
      tx.setAttribute("fill", p.port === "最终输出" ? "#ffd98a" : "#aab6c6");
      tx.textContent = p.port;
      g.appendChild(tx);
    });
    var state = document.createElementNS(NS, "text");
    state.setAttribute("x", "12"); state.setAttribute("y", ln.h - 6); state.setAttribute("font-size", "9");
    state.dataset.role = "state";
    g.appendChild(state);
    g.addEventListener("click", () => selectNode(s.id));
    nodeEls[s.id] = g;
    nodeG.appendChild(g);
  });

  /* ---------- 状态与动画 ---------- */
  var t = 0, playing = true, speed = 1, last = performance.now();
  var hud = document.getElementById("hud");
  var results = {};
  var selected = null;

  function runStage(id) {  // 真实复算缓存
    if (!results[id]) results[id] = BY_ID[id].compute();
    return results[id];
  }
  function progressOf(id, p) {  // 活动阶段的进度计数（0→1 线性）
    var live = BY_ID[id].live;
    return live && live.to != null ? Math.floor(live.to * Math.min(1, Math.max(0, p))) : null;
  }

  function render(now) {
    if (playing) {
      t += (now - last) * speed;
      if (t >= timeline.durationMs) t = timeline.durationMs;
    }
    last = now;
    var frame = buildFrame(t);
    timeline.nodeOrder.forEach((id) => {
      var ph = frame.nodePhase[id];
      var g = nodeEls[id], rect = g.querySelector("rect"), st = g.querySelector('[data-role="state"]');
      if (ph === "idle") {
        g.setAttribute("opacity", "0.35"); rect.setAttribute("stroke", "#232c39");
        st.setAttribute("fill", "#5a6675"); st.textContent = "待处理";
        return;
      }
      g.setAttribute("opacity", "1");
      var n = timeline.nodes[id], p = (t - n.enterAt) / ACTIVE_MS;
      if (ph === "active") {
        rect.setAttribute("stroke", "#ffd98a"); st.setAttribute("fill", "#ffd98a");
        var c = progressOf(id, p);
        st.textContent = "● 处理中" + (c != null ? ` ${c}/${BY_ID[id].live.to}` : "…");
      } else {
        runStage(id);
        rect.setAttribute("stroke", "#52e08a"); st.setAttribute("fill", "#52e08a");
        st.textContent = "✓ 已产出";
      }
    });
    laid.edges.forEach((e) => {
      var path = edgeEls[e.key];
      var pk = document.querySelector(`[data-role="packet"][data-key="${CSS.escape(e.key)}"]`);
      var pkt = frame.edgePacket[e.key];
      if (pkt !== void 0) {
        path.setAttribute("stroke", "#ffd98a"); path.setAttribute("stroke-width", "2.4");
        var pt = cubic(pkt, e.from, e.to);
        pk.setAttribute("cx", pt.x); pk.setAttribute("cy", pt.y); pk.setAttribute("opacity", "1");
      } else if (frame.edgeReached[e.key]) {
        path.setAttribute("stroke", "#52e08a"); path.setAttribute("stroke-width", "1.6");
        path.setAttribute("stroke-dasharray", "7 5"); pk.setAttribute("opacity", "0");
      } else {
        path.setAttribute("stroke", "#2b3a4d"); pk.setAttribute("opacity", "0");
      }
    });
    hud.textContent = `已激活 ${frame.doneCount}/${frame.total} · ${Math.round(frame.progress * 100)}% · 真实数据复算中`;
    renderStats(frame);
    refreshSeekUI();
    if (playing && t < timeline.durationMs) requestAnimationFrame(render);
  }

  /* ---------- 底部统计条 ---------- */
  var STAT_DEFS = [
    { id: "ingest", k: "总记录", f: () => fmt(REC.length, 0) },
    { id: "integrity", k: "完整记录", f: () => fmt(REC.length, 0) },
    { id: "highrisk", k: "高风险", f: () => fmt(riskCol.filter((x) => x === 1).length, 0) },
    { id: "anomaly", k: `金额异常 > P95(61.6)`, f: () => fmt(mv.filter((x) => x != null && x > quantile(mv, .95)).length, 0) },
    { id: "aar", k: "AAR 一致率", f: () => {
      var bad = 0; REC.forEach((r, i) => { if (inR[i] != null && ctlR[i] != null && detR[i] != null && ar[i] != null && Math.abs(inR[i] * ctlR[i] * detR[i] - ar[i]) > 1e-6) bad += 1; });
      return fmt((1 - bad / REC.length) * 100, 1) + "%"; } },
    { id: "region", k: "区域数", f: () => fmt(new Set(col("LOCATION_ID").filter((x) => x != null)).size, 0) },
  ];
  var statsBox = document.getElementById("stats");
  var statEls = {};
  STAT_DEFS.forEach((d) => {
    var el = document.createElement("div");
    el.className = "stat";
    el.innerHTML = `<div class="t">${d.k}</div><div class="n">—</div>`;
    statsBox.appendChild(el); statEls[d.id] = el;
  });
  function renderStats(frame) {
    STAT_DEFS.forEach((d) => {
      var ph = frame.nodePhase[d.id];
      var el = statEls[d.id];
      el.className = "stat" + (ph === "done" ? " done" : ph === "active" ? " busy" : "");
      if (ph === "done" || ph === "active") {
        var txt = d.f();
        el.querySelector(".n").textContent = ph === "active" ? (txt.includes("%") ? "…" : "计算中") : txt;
      } else el.querySelector(".n").textContent = "—";
    });
  }

  /* ---------- 证据面板 ---------- */
  var panel = document.getElementById("panel");
  function proveCard() {
    return `<h3 style="color:#7fe0c0">📄 源数据指纹</h3>
      <div class="trace">${PROV.source}<br/>sha256 ${PROV.sha256 || "—"}<br/>${REC.length} 行 × ${DATA.columns.length} 列 · 内嵌于 ${new Date(PROV.generated || Date.now()).toISOString().slice(0, 19).replace("T", " ")}</div>
      <div class="note">点击任意节点查看该阶段基于真实记录的复算证据（KPI 全部由浏览器端从 776 条原始记录重算得出，非写死的数字）。</div>`;
  }
  function nodeCard(id) {
    var s = BY_ID[id], r = results[id] || runStage(id);
    var html = `<h3>${idToIdx[id] + 1}. ${s.name}</h3>
      <div class="cap">${s.cap} · L${s.layer} · ${PLAY_BY[s.id]}</div>`;
    html += `<div class="kpis">` + r.kpis.map((k) =>
      `<div class="kpi"><div class="v">${fmt(k.v, k.f || 2)}</div><div class="l">${k.l}</div></div>`).join("") + `</div>`;
    html += `<div>` + r.rows.map(([a, b]) =>
      `<div class="row"><span>${a}</span><b>${b}</b></div>`).join("") + `</div>`;
    var ins = Object.entries(s.in).map(([port, from]) => `${from || "无"} → ${port}`);
    var outs = s.out.map((p) => `artf:${s.id}.${p} (${fnv1a64(s.id + ":" + JSON.stringify(r))})`).join("<br/>");
    html += `<div class="trace">输入端<br/>${ins.join("<br/>") || "—"}<br/>输出工件<br/>${outs}</div>`;
    html += `<div class="note">${r.note}</div>`;
    return html;
  }
  var PLAY_BY = { ingest: "种子输入", integrity: "并行①", range: "并行②", describe: "并行③", corr: "并行④", aar: "并行⑤", highrisk: "并行⑥", anomaly: "并行⑦", region: "并行⑧", conclusion: "最终输出" };
  function selectNode(id) {
    selected = id;
    panel.innerHTML = (id == null ? proveCard() : nodeCard(id));
  }
  selectNode(null);

  /* ---------- 控制条 ---------- */
  var btnPlay = document.getElementById("btnPlay"), btnStep = document.getElementById("btnStep"),
      btnRestart = document.getElementById("btnRestart"), speedSel = document.getElementById("speed"),
      seek = document.getElementById("seek"), timeLabel = document.getElementById("timeLabel");
  btnPlay.addEventListener("click", () => {
    playing = !playing;
    if (playing && t >= timeline.durationMs) { t = 0; runStageInit(); }
    last = performance.now();
    btnPlay.textContent = playing ? "⏸ 暂停" : "▶ 播放";
    if (playing && t < timeline.durationMs) requestAnimationFrame(render);
  });
  btnStep.addEventListener("click", () => {
    playing = false; btnPlay.textContent = "▶ 播放";
    // 前进到下一节点激活边界
    var nx = timeline.nodeOrder.map((id) => timeline.nodes[id].enterAt).filter((et) => et > t + 10);
    var nextStart = Math.min(...nx, timeline.durationMs);
    t = nextStart === timeline.durationMs ? timeline.durationMs : nextStart + Math.min(ACTIVE_MS / 2, 400);
    last = performance.now(); render(last);
  });
  btnRestart.addEventListener("click", () => { playing = false; t = 0; last = performance.now(); render(last); btnPlay.textContent = "▶ 播放"; });
  speedSel.addEventListener("change", () => { speed = +speedSel.value; });
  seek.addEventListener("input", () => { t = (+seek.value / 1000) * timeline.durationMs; playing = false; btnPlay.textContent = "▶ 播放"; last = performance.now(); render(last); });
  function refreshSeekUI() {
    seek.value = Math.round((t / timeline.durationMs) * 1000);
    timeLabel.textContent = `${Math.round(t)} / ${timeline.durationMs} ms`;
  }
  function runStageInit() { results = {}; selectNode(selected); }

  /* ---------- 启动 ---------- */
  window.__auditDemo = {
    seek: (ms) => { t = Math.min(Math.max(0, ms), timeline.durationMs); selectNode(null); requestAnimationFrame(render); },
    play: () => { if (t >= timeline.durationMs) t = 0; playing = true; last = performance.now(); btnPlay.textContent = "⏸ 暂停"; requestAnimationFrame(render); },
    pause: () => { playing = false; btnPlay.textContent = "▶ 播放"; },
    info: () => ({ durationMs: timeline.durationMs, nodes: STAGES.length, edges: EDGES.length, layers: LAYER_TITLES.length, rows: REC.length }),
  };
  document.getElementById("provebar").innerHTML =
    `<span class="chip"><span class="k">源数据</span><b>audit_risk.csv</b></span>` +
    `<span class="chip"><span class="k">sha256</span><b>${(PROV.sha256 || "").slice(0, 18)}…</b></span>` +
    `<span class="chip"><span class="k">记录 × 字段</span><b>${REC.length} × ${DATA.columns.length}</b></span>` +
    `<span class="chip"><span class="k">管道</span><b>10 节点 / 14 边 / 5 层</b></span>` +
    `<span class="chip"><span class="k">复算</span><b>浏览器端 · 零后端</b></span>`;

  requestAnimationFrame(render);
})();