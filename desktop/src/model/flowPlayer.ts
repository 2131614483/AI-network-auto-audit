// ComfyUI-style flow player model.  Everything here is a pure function of the
// server canvas projection (attempt nodes + data edges), so the animation can
// never drift from run truth and can be unit-tested without React.
//
// Two "processes" are made visible:
//   1. build (draft)  — nodes/edges are revealed in topological order, so the
//      user watches the AI place nodes and pull wires one by one.
//   2. run playback   — nodes activate in order and a data packet travels along
//      each edge from an upstream output port to a downstream input port.
//
// Timing is a *virtual* clock in milliseconds (not wall time), driven by the
// player controls (play / step / speed).  It only animates presentation and
// never triggers execution.

import type { CanvasEdge, CanvasNode, CanvasProjection } from "./runCanvas";

export type NodePhase = "idle" | "active" | "done";
export type NodeKind = "seed" | "computed" | "sink";

// Virtual timing budget (ms). GAP >= FLOW so a packet reaches a node exactly as
// that node activates.
export const NODE_ACTIVE_MS = 480;
export const STEP_GAP_MS = 280;
export const EDGE_FLOW_MS = 280;
const STEP_MS = NODE_ACTIVE_MS + STEP_GAP_MS;

export type TimelineNode = {
  id: string;
  order: number;
  layer: number;
  enterAt: number;
  doneAt: number;
  kind: NodeKind;
};

export type TimelineEdge = {
  key: string;
  source: string;
  target: string;
  sourcePort: string;
  targetPort: string;
  flowStart: number;
  flowEnd: number;
};

export type FlowTimeline = {
  durationMs: number;
  nodeOrder: string[];
  nodes: Record<string, TimelineNode>;
  edges: TimelineEdge[];
  incoming: Record<string, TimelineEdge[]>;
  outgoing: Record<string, TimelineEdge[]>;
};

export type FlowFrame = {
  t: number;
  finished: boolean;
  progress: number;
  doneCount: number;
  total: number;
  nodePhase: Record<string, NodePhase>;
  // edge key -> packet position 0..1 while flowing
  edgePacket: Record<string, number>;
  // edge key -> data has reached the downstream node (wire is "live")
  edgeReached: Record<string, boolean>;
};

type GraphLike = {
  nodes: ReadonlyArray<{ node_instance_id: string }>;
  edges: ReadonlyArray<{
    key: [string, string, string, string];
    source_instance: string;
    target_instance: string;
    source_port: string;
    target_port: string;
  }>;
};

const edgeKeyOf = (e: GraphLike["edges"][number]): string => e.key.join("|");

/** Longest-path layering + Kahn ordering. Ties break by `rank` then id so a real
 *  run follows attempt_seq/created_at and a draft follows stable ids. */
export function topoOrder(
  graph: GraphLike,
  rank: Record<string, number> = {},
): { order: string[]; layer: Record<string, number> } {
  const ids = graph.nodes.map((n) => n.node_instance_id);
  const indeg = new Map<string, number>(ids.map((id) => [id, 0]));
  const adj = new Map<string, string[]>(ids.map((id) => [id, []]));
  for (const e of graph.edges) {
    if (!indeg.has(e.source_instance) || !indeg.has(e.target_instance)) continue;
    adj.get(e.source_instance)!.push(e.target_instance);
    indeg.set(e.target_instance, (indeg.get(e.target_instance) ?? 0) + 1);
  }
  const layer = new Map<string, number>(ids.map((id) => [id, 0]));
  const ready = ids.filter((id) => (indeg.get(id) ?? 0) === 0);
  const tie = (a: string, b: string): number =>
    (rank[a] ?? Number.MAX_SAFE_INTEGER) - (rank[b] ?? Number.MAX_SAFE_INTEGER)
    || (layer.get(a) ?? 0) - (layer.get(b) ?? 0)
    || a.localeCompare(b);
  ready.sort(tie);
  const order: string[] = [];
  while (ready.length) {
    const id = ready.shift()!;
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
  // Longest-path layering in a single pass over the topological order. Doing
  // it along Kahn order (not a fixpoint loop) is guaranteed to terminate even
  // when a cycle is present — cycle nodes never enter `order` and stay layer 0.
  for (const id of order) {
    for (const next of adj.get(id) ?? []) {
      if (!order.includes(next)) continue;
      layer.set(next, Math.max(layer.get(next) ?? 0, (layer.get(id) ?? 0) + 1));
    }
  }
  // Defensive: any node not reached by Kahn (cycle / orphan) keeps deterministic
  // order by rank/id so nothing disappears from the animation.
  for (const id of ids) {
    if (!order.includes(id)) order.push(id);
  }
  return { order, layer: Object.fromEntries(layer) };
}

function nodeKindOf(id: string, incoming: Record<string, unknown[]>, outgoing: Record<string, unknown[]>): NodeKind {
  const hasIn = (incoming[id]?.length ?? 0) > 0;
  const hasOut = (outgoing[id]?.length ?? 0) > 0;
  if (!hasIn) return "seed";
  if (!hasOut) return "sink";
  return "computed";
}

export function buildTimeline(projection: GraphLike, rank: Record<string, number> = {}): FlowTimeline {
  const { order, layer } = topoOrder(projection, rank);
  const orderIndex = new Map(order.map((id, index) => [id, index]));
  const incoming: Record<string, TimelineEdge[]> = {};
  const outgoing: Record<string, TimelineEdge[]> = {};
  const edges: TimelineEdge[] = [];
  for (const e of projection.edges) {
    const sourceOrder = orderIndex.get(e.source_instance);
    if (sourceOrder === undefined || orderIndex.get(e.target_instance) === undefined) continue;
    const edge: TimelineEdge = {
      key: edgeKeyOf(e),
      source: e.source_instance,
      target: e.target_instance,
      sourcePort: e.source_port,
      targetPort: e.target_port,
      flowStart: 0,
      flowEnd: 0,
    };
    edges.push(edge);
    (outgoing[e.source_instance] ??= []).push(edge);
    (incoming[e.target_instance] ??= []).push(edge);
  }
  const nodes: Record<string, TimelineNode> = {};
  order.forEach((id, index) => {
    const enterAt = index * STEP_MS;
    nodes[id] = {
      id,
      order: index,
      layer: layer[id] ?? 0,
      enterAt,
      doneAt: enterAt + NODE_ACTIVE_MS,
      kind: nodeKindOf(id, incoming, outgoing),
    };
  });
  for (const edge of edges) {
    const source = nodes[edge.source];
    edge.flowStart = source ? source.doneAt : 0;
    edge.flowEnd = edge.flowStart + EDGE_FLOW_MS;
  }
  const lastOrder = order.length - 1;
  const lastNode = order.length ? nodes[order[lastOrder]] : undefined;
  const maxEdgeEnd = edges.reduce((m, e) => Math.max(m, e.flowEnd), 0);
  const durationMs = Math.max(lastNode ? lastNode.doneAt : 0, maxEdgeEnd) + STEP_GAP_MS;
  return { durationMs, nodeOrder: order, nodes, edges, incoming, outgoing };
}

/** Frame of the animation at virtual time `t` (clamped to [0, duration]). */
export function buildFrame(timeline: FlowTimeline, t: number): FlowFrame {
  const time = Math.max(0, Math.min(t, timeline.durationMs));
  const nodePhase: Record<string, NodePhase> = {};
  let doneCount = 0;
  for (const id of timeline.nodeOrder) {
    const n = timeline.nodes[id];
    const phase: NodePhase = time < n.enterAt ? "idle" : time < n.doneAt ? "active" : "done";
    nodePhase[id] = phase;
    if (phase === "done") doneCount += 1;
  }
  const edgePacket: Record<string, number> = {};
  const edgeReached: Record<string, boolean> = {};
  for (const e of timeline.edges) {
    if (time >= e.flowEnd) edgeReached[e.key] = true;
    else if (time > e.flowStart) edgePacket[e.key] = (time - e.flowStart) / EDGE_FLOW_MS;
  }
  return {
    t: time,
    finished: time >= timeline.durationMs,
    progress: timeline.durationMs ? time / timeline.durationMs : 1,
    doneCount,
    total: timeline.nodeOrder.length,
    nodePhase,
    edgePacket,
    edgeReached,
  };
}

/** Rank nodes by real execution order for a finished run: attempt_seq first,
 *  then created_at. Drafts fall back to stable ids inside buildTimeline. */
export function runRank(nodes: ReadonlyArray<CanvasNode>): Record<string, number> {
  const sorted = [...nodes].sort((a, b) => {
    if (a.attempt_seq !== b.attempt_seq) return a.attempt_seq - b.attempt_seq;
    return String(a.created_at ?? "").localeCompare(String(b.created_at ?? ""));
  });
  return Object.fromEntries(sorted.map((n, i) => [n.node_instance_id, i]));
}

export type NormalizedBinding = {
  port: string;
  source_instance: string;
  source_port: string;
  sha256: string | null;
  uri: string | null;
  adapter: string | null;
};

/** input_bindings may be {port: [items]} or {port: item}; normalize both. */
export function inputBindingEntries(node: CanvasNode): NormalizedBinding[] {
  const raw = node.input_bindings;
  if (!raw || typeof raw !== "object") return [];
  const out: NormalizedBinding[] = [];
  for (const [port, value] of Object.entries(raw)) {
    const items = Array.isArray(value) ? value : [value];
    for (const item of items) {
      if (!item || typeof item !== "object") continue;
      const rec = item as Record<string, unknown>;
      out.push({
        port,
        source_instance: String(rec.source_instance ?? "seed"),
        source_port: String(rec.source_port ?? port),
        sha256: (rec.sha256 as string) ?? null,
        uri: (rec.uri as string) ?? null,
        adapter: (rec.adapter as string) ?? null,
      });
    }
  }
  return out;
}

export function isSeedBinding(binding: NormalizedBinding): boolean {
  return binding.source_instance === "seed" || binding.source_instance === "";
}

// -- ComfyUI-style deterministic layout -------------------------------------
// Nodes grow with their port count; wires attach to exact port anchors.

export const FLOW_NODE_W = 216;
const FLOW_HEADER = 46;
const FLOW_PORT_ROW = 19;
const FLOW_FOOT = 20;
const FLOW_LAYER_GAP = 118;
const FLOW_ROW_GAP = 34;
export const SEED_PORT = "数据入口";
export const SINK_PORT = "最终输出";

export type LayoutPort = { port: string; row: number };
export type LaidNode = {
  id: string; x: number; y: number; w: number; h: number; layer: number;
  inPorts: LayoutPort[];
  outPorts: LayoutPort[];
};
export type LaidEdge = {
  key: string; source: string; target: string;
  sourcePort: string; targetPort: string;
  from: { x: number; y: number };
  to: { x: number; y: number };
};
export type FlowLayout = {
  width: number; height: number;
  nodes: Record<string, LaidNode>;
  edges: LaidEdge[];
};

function uniquePorts(values: Iterable<string>): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const v of values) {
    if (!seen.has(v)) { seen.add(v); out.push(v); }
  }
  return out;
}

function nodeHeight(inCount: number, outCount: number): number {
  const rows = Math.max(inCount, outCount, 1);
  return FLOW_HEADER + rows * FLOW_PORT_ROW + FLOW_FOOT;
}

/** Layered (left→right) DAG layout sized to each node's ports. */
export function flowLayout(graph: GraphLike, rank: Record<string, number> = {}): FlowLayout {
  const { order, layer } = topoOrder(graph, rank);
  const inNames = new Map<string, string[]>();
  const outNames = new Map<string, string[]>();
  for (const id of order) { inNames.set(id, []); outNames.set(id, []); }
  for (const e of graph.edges) {
    if (!inNames.has(e.target_instance) || !outNames.has(e.source_instance)) continue;
    outNames.get(e.source_instance)!.push(e.source_port);
    inNames.get(e.target_instance)!.push(e.target_port);
  }
  const pre: Record<string, LaidNode> = {};
  const byLayer = new Map<number, string[]>();
  for (const id of order) {
    const inPorts0 = uniquePorts(inNames.get(id) ?? []);
    const outPorts0 = uniquePorts(outNames.get(id) ?? []);
    const isSeed = inPorts0.length === 0;
    const isSink = outPorts0.length === 0;
    const inPorts = (isSeed ? [SEED_PORT] : inPorts0).map((port, row) => ({ port, row }));
    const outPorts = (isSink ? [SINK_PORT] : outPorts0).map((port, row) => ({ port, row }));
    const h = nodeHeight(inPorts.length, outPorts.length);
    pre[id] = {
      id, x: 0, y: 0, w: FLOW_NODE_W, h, layer: layer[id] ?? 0, inPorts, outPorts,
    };
    const l = layer[id] ?? 0;
    if (!byLayer.has(l)) byLayer.set(l, []);
    byLayer.get(l)!.push(id);
  }
  const layerHeight = new Map<number, number>();
  for (const [l, ids] of byLayer) {
    const total = ids.reduce((sum, id) => sum + pre[id].h, 0) + (ids.length - 1) * FLOW_ROW_GAP;
    layerHeight.set(l, total);
  }
  const maxHeight = Math.max(0, ...layerHeight.values());
  const nodes: Record<string, LaidNode> = {};
  for (const [l, ids] of byLayer) {
    const x = l * (FLOW_NODE_W + FLOW_LAYER_GAP);
    let y = Math.max(0, (maxHeight - (layerHeight.get(l) ?? 0)) / 2);
    for (const id of ids) {
      const node = pre[id];
      node.x = x; node.y = y;
      nodes[id] = node;
      y += node.h + FLOW_ROW_GAP;
    }
  }
  const portY = (node: LaidNode, side: "in" | "out", port: string): number => {
    const ports = side === "in" ? node.inPorts : node.outPorts;
    const row = ports.find((p) => p.port === port)?.row ?? 0;
    return node.y + FLOW_HEADER + row * FLOW_PORT_ROW + FLOW_PORT_ROW / 2;
  };
  const edges: LaidEdge[] = [];
  for (const e of graph.edges) {
    const s = nodes[e.source_instance];
    const t = nodes[e.target_instance];
    if (!s || !t) continue;
    edges.push({
      key: e.key.join("|"),
      source: e.source_instance,
      target: e.target_instance,
      sourcePort: e.source_port,
      targetPort: e.target_port,
      from: { x: s.x + s.w, y: portY(s, "out", e.source_port) },
      to: { x: t.x, y: portY(t, "in", e.target_port) },
    });
  }
  const width = Math.max(...order.map((id) => nodes[id].x + nodes[id].w), FLOW_NODE_W);
  const height = Math.max(maxHeight, 220);
  return { width: width + FLOW_LAYER_GAP, height, nodes, edges };
}

