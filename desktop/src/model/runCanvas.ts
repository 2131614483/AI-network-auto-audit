// CW4: run canvas model — server canvas projection types, deterministic 2-D
// layout, and the control-plane feed client used by the desktop panels.

export type RunSummary = {
  run_id: string;
  plan_key: string;
  execution_hash: string;
  status: "succeeded" | "failed" | "running";
  trace_id: string;
  created_at: string | null;
  attempt_total: number;
  attempt_succeeded: number;
  attempt_failed: number;
};

export type CanvasRef = { uri: string; sha256: string; size_bytes: number };
export type CanvasNode = {
  node_instance_id: string;
  capability: string;
  plugin_id: string;
  attempt_seq: number;
  attempt_id: string;
  status: string;
  error_kind: string | null;
  error_message: string | null;
  worker_id: string | null;
  input_bindings: Record<string, unknown>;
  output_refs: Record<string, CanvasRef>;
  trace_id: string;
  created_at: string | null;
  finished_at: string | null;
};
export type CanvasEdge = {
  key: [string, string, string, string];
  source_instance: string;
  source_port: string;
  target_instance: string;
  target_port: string;
  sha256: string | null;
  uri: string | null;
  adapter: string | null;
  attempt_id: string;
};
export type CanvasProjection = {
  run_id: string;
  plan_key: string | null;
  execution_hash: string | null;
  status: string;
  trace_id: string;
  created_at: string | null;
  nodes: CanvasNode[];
  edges: CanvasEdge[];
};

export type RunsFeed = {
  items: RunSummary[];
  trace_id: string;
};

export type LayoutPoint = { x: number; y: number };
export type LayoutNode = { node_instance_id: string; point: LayoutPoint; layer: number };
export type LayoutEdge = {
  key: [string, string, string, string];
  from: LayoutPoint;
  to: LayoutPoint;
};

const NODE_W = 220;
const NODE_H = 96;
const LAYER_GAP = 120;
const ROW_GAP = 40;

export function canvasLayout(
  nodes: ReadonlyArray<{ node_instance_id: string }>,
  edges: ReadonlyArray<{ source_instance: string; target_instance: string }>,
): { nodes: LayoutNode[]; edges: LayoutEdge[] } {
  const layers = new Map<string, number>();
  const indegree = new Map<string, number>();
  const adjacency = new Map<string, string[]>();
  for (const node of nodes) {
    layers.set(node.node_instance_id, 0);
    indegree.set(node.node_instance_id, 0);
    adjacency.set(node.node_instance_id, []);
  }
  for (const edge of edges) {
    if (!layers.has(edge.source_instance) || !layers.has(edge.target_instance)) continue;
    adjacency.get(edge.source_instance)!.push(edge.target_instance);
    indegree.set(edge.target_instance, (indegree.get(edge.target_instance) ?? 0) + 1);
  }
  // Kahn layering: node layer = max(producer layer) + 1 (longest path).
  let changed = true;
  while (changed) {
    changed = false;
    for (const edge of edges) {
      if (!layers.has(edge.source_instance) || !layers.has(edge.target_instance)) continue;
      const candidate = (layers.get(edge.source_instance) ?? 0) + 1;
      if (candidate > (layers.get(edge.target_instance) ?? 0)) {
        layers.set(edge.target_instance, candidate);
        changed = true;
      }
    }
  }
  const byLayer = new Map<number, string[]>();
  for (const [nodeId, layer] of layers) {
    if (!byLayer.has(layer)) byLayer.set(layer, []);
    byLayer.get(layer)!.push(nodeId);
  }
  const sortedLayers = [...byLayer.keys()].sort((a, b) => a - b);
  const maxLayer = Math.max(0, sortedLayers.length - 1);
  const maxRows = Math.max(1, ...[...byLayer.values()].map((rows) => rows.length));
  const points = new Map<string, LayoutPoint>();
  for (const layer of sortedLayers) {
    const rows = byLayer.get(layer)!;
    const y0 = (maxRows - rows.length) * (NODE_H + ROW_GAP) / 2;
    rows.forEach((nodeId, index) => {
      points.set(nodeId, {
        x: layer * (NODE_W + LAYER_GAP),
        y: y0 + index * (NODE_H + ROW_GAP),
      });
    });
  }
  const layoutEdges: LayoutEdge[] = [];
  for (const edge of edges) {
    const from = points.get(edge.source_instance);
    const to = points.get(edge.target_instance);
    if (!from || !to) continue;
    layoutEdges.push({
      key: [edge.source_instance, "", edge.target_instance, ""] as [string, string, string, string],
      from,
      to,
    });
  }
  return {
    nodes: nodes.map((node) => ({ node_instance_id: node.node_instance_id, point: points.get(node.node_instance_id)!, layer: layers.get(node.node_instance_id)! })),
    edges: layoutEdges,
  };
}

export function canvasSize(layout: { nodes: LayoutNode[] }): { width: number; height: number } {
  let width = 0;
  let height = 0;
  for (const node of layout.nodes) {
    width = Math.max(width, node.point.x + NODE_W);
    height = Math.max(height, node.point.y + NODE_H);
  }
  return { width: Math.max(width, 640), height: Math.max(height, 360) };
}

export function statusTone(status: string): "success" | "error" | "warning" | "default" {
  if (status === "succeeded") return "success";
  if (status === "failed") return "error";
  if (status === "running" || status === "pending" || status === "retry_wait") return "warning";
  return "default";
}

export function fileUriToPath(uri: string): string {
  try {
    const parsed = new URL(uri);
    if (parsed.protocol === "file:") {
      return decodeURIComponent(parsed.pathname.replace(/^\/([A-Za-z]:)/, "$1"));
    }
    return uri;
  } catch {
    return uri;
  }
}
