// CW5 canvas draft model: converts an AI chat draft into a canvas projection
// (reusing the run-canvas layout) and applies in-canvas edits back to the
// draft.  Layout positions are presentation-only and never change the
// execution hash — dragging a node edits only the view.

import type { CanvasProjection, CanvasNode, CanvasEdge } from "./runCanvas";
import type { ChatDraft, ChatDraftEdge, ChatDraftNode } from "./canvasChat";

export type DraftEdit =
  | { kind: "move"; node_instance_id: string; x: number; y: number }
  | { kind: "remove_node"; node_instance_id: string }
  | { kind: "remove_edge"; source_instance: string; source_port: string; target_instance: string; target_port: string };

export function draftToProjection(draft: ChatDraft): CanvasProjection {
  const nodes: CanvasNode[] = draft.nodes.map((node) => ({
    node_instance_id: node.node_instance_id,
    capability: node.capability,
    plugin_id: node.plugin_id,
    attempt_seq: 0,
    attempt_id: `draft:${draft.plan_key}`,
    status: "pending",
    error_kind: null,
    error_message: null,
    worker_id: null,
    input_bindings: {},
    output_refs: {},
    trace_id: "",
    created_at: null,
    finished_at: null,
  }));
  const edges: CanvasEdge[] = draft.edges.map((edge) => ({
    key: [edge.source_instance, edge.source_port, edge.target_instance, edge.target_port] as [string, string, string, string],
    source_instance: edge.source_instance,
    source_port: edge.source_port,
    target_instance: edge.target_instance,
    target_port: edge.target_port,
    sha256: null,
    uri: null,
    adapter: edge.adapter ?? null,
    attempt_id: `draft:${draft.plan_key}`,
  }));
  return {
    run_id: `draft:${draft.plan_key}`,
    plan_key: draft.plan_key,
    execution_hash: null,
    status: "pending",
    trace_id: "",
    created_at: null,
    nodes,
    edges,
  };
}

export function applyDraftEdit(draft: ChatDraft, edit: DraftEdit): ChatDraft {
  if (edit.kind === "remove_node") {
    const nodeIds = new Set(draft.nodes.map((node) => node.node_instance_id));
    nodeIds.delete(edit.node_instance_id);
    return {
      ...draft,
      nodes: draft.nodes.filter((node) => node.node_instance_id !== edit.node_instance_id),
      edges: draft.edges.filter(
        (edge) => edge.source_instance !== edit.node_instance_id && edge.target_instance !== edit.node_instance_id,
      ),
    };
  }
  if (edit.kind === "remove_edge") {
    return {
      ...draft,
      edges: draft.edges.filter(
        (edge) =>
          !(
            edge.source_instance === edit.source_instance &&
            edge.source_port === edit.source_port &&
            edge.target_instance === edit.target_instance &&
            edge.target_port === edit.target_port
          ),
      ),
    };
  }
  // move is presentation-only: the draft itself is unchanged
  return draft;
}

export function draftNodeLabel(node: ChatDraftNode): string {
  return node.node_instance_id;
}

export function draftEdgeKey(edge: ChatDraftEdge): string {
  return [edge.source_instance, edge.source_port, edge.target_instance, edge.target_port].join("|");
}
