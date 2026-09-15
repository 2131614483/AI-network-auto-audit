import { describe, expect, it } from "vitest";
import type { ChatDraft } from "./canvasChat";
import { seedInputsOf } from "./canvasChat";
import { applyDraftEdit, draftToProjection } from "./canvasDraft";

function sampleDraft(): ChatDraft {
  return {
    plan_key: "plan-test-1",
    nodes: [
      {
        node_instance_id: "ledger-a",
        capability: "audit.ledger.validate",
        plugin_id: "audit.ledger-quality",
        input_ports: [{ port_id: "ledger", direction: "input", required: true }],
        output_ports: [{ port_id: "candidates", direction: "output" }],
      },
      {
        node_instance_id: "finding-b",
        capability: "audit.finding.draft",
        plugin_id: "audit.finding-draft",
        input_ports: [{ port_id: "candidates", direction: "input", required: true }],
        output_ports: [{ port_id: "finding", direction: "output" }],
      },
    ],
    edges: [
      {
        edge_id: "e1",
        source_instance: "ledger-a",
        source_port: "candidates",
        target_instance: "finding-b",
        target_port: "candidates",
        adapter: "direct",
      },
    ],
    seed_inputs: [["ledger-a", "ledger"]],
    selection_reasons: "test draft",
  };
}

describe("draftToProjection", () => {
  it("maps draft nodes and edges into a canvas projection", () => {
    const projection = draftToProjection(sampleDraft());
    expect(projection.plan_key).toBe("plan-test-1");
    expect(projection.status).toBe("pending");
    expect(projection.nodes).toHaveLength(2);
    expect(projection.edges).toHaveLength(1);
    const node = projection.nodes[0];
    expect(node.node_instance_id).toBe("ledger-a");
    expect(node.capability).toBe("audit.ledger.validate");
    expect(node.attempt_id).toContain("draft:");
    const edge = projection.edges[0];
    expect(edge.source_instance).toBe("ledger-a");
    expect(edge.source_port).toBe("candidates");
    expect(edge.key).toEqual(["ledger-a", "candidates", "finding-b", "candidates"]);
  });
});

describe("applyDraftEdit", () => {
  it("removes a node and its incident edges", () => {
    const next = applyDraftEdit(sampleDraft(), { kind: "remove_node", node_instance_id: "ledger-a" });
    expect(next.nodes.map((node) => node.node_instance_id)).toEqual(["finding-b"]);
    expect(next.edges).toHaveLength(0);
  });

  it("removes a matching edge by endpoint pair", () => {
    const next = applyDraftEdit(sampleDraft(), {
      kind: "remove_edge",
      source_instance: "ledger-a",
      source_port: "candidates",
      target_instance: "finding-b",
      target_port: "candidates",
    });
    expect(next.edges).toHaveLength(0);
    expect(next.nodes).toHaveLength(2);
  });

  it("keeps the draft unchanged for a presentation-only move", () => {
    const draft = sampleDraft();
    const next = applyDraftEdit(draft, { kind: "move", node_instance_id: "ledger-a", x: 100, y: 200 });
    expect(next).toEqual(draft);
  });
});

describe("seedInputsOf", () => {
  it("returns seed input pairs from a draft", () => {
    expect(seedInputsOf(sampleDraft())).toEqual([["ledger-a", "ledger"]]);
    expect(seedInputsOf(null)).toEqual([]);
    expect(seedInputsOf(undefined)).toEqual([]);
  });
});
