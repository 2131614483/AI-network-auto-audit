import { describe, expect, it } from "vitest";
import {
  canvasLayout,
  canvasSize,
  fileUriToPath,
  statusTone,
  type CanvasNode,
} from "./runCanvas";

const NODES: Array<{ node_instance_id: string }> = [
  { node_instance_id: "seed" },
  { node_instance_id: "ledger-a" },
  { node_instance_id: "ledger-b" },
  { node_instance_id: "consumer-x" },
  { node_instance_id: "consumer-y" },
];
const EDGES = [
  { source_instance: "seed", target_instance: "ledger-a" },
  { source_instance: "seed", target_instance: "ledger-b" },
  { source_instance: "ledger-a", target_instance: "consumer-x" },
  { source_instance: "ledger-a", target_instance: "consumer-y" },
];

describe("canvasLayout", () => {
  it("assigns longest-path layers and deterministic coordinates", () => {
    const layout = canvasLayout(NODES, EDGES);
    const byId = new Map(layout.nodes.map((n) => [n.node_instance_id, n]));
    expect(byId.get("seed")!.layer).toBe(0);
    expect(byId.get("ledger-a")!.layer).toBe(1);
    expect(byId.get("consumer-x")!.layer).toBe(2);
    // same layer rows do not overlap
    const yLedgerA = byId.get("ledger-a")!.point.y;
    const yLedgerB = byId.get("ledger-b")!.point.y;
    expect(Math.abs(yLedgerA - yLedgerB)).toBeGreaterThanOrEqual(40);
    // layout is deterministic for the same input
    const again = canvasLayout(NODES, EDGES);
    expect(again).toEqual(layout);
  });

  it("centers fan-out consumers in their own layer", () => {
    const layout = canvasLayout(NODES, EDGES);
    const layer2 = layout.nodes.filter((n) => n.layer === 2).map((n) => n.point.y);
    expect(layer2).toHaveLength(2);
    expect(layer2[0]).toBeCloseTo(layer2[1] - (96 + 40), 6);
  });
});

describe("canvasSize", () => {
  it("bounds the canvas to the furthest node", () => {
    const layout = canvasLayout(NODES, EDGES);
    const size = canvasSize(layout);
    expect(size.width).toBeGreaterThanOrEqual(640);
    expect(size.height).toBeGreaterThanOrEqual(360);
  });
});

describe("statusTone", () => {
  it("maps run statuses to deterministic tones", () => {
    expect(statusTone("succeeded")).toBe("success");
    expect(statusTone("failed")).toBe("error");
    expect(statusTone("running")).toBe("warning");
    expect(statusTone("cancelled")).toBe("default");
  });
});

describe("fileUriToPath", () => {
  it("converts windows file URIs to local paths", () => {
    expect(fileUriToPath("file:///C:/Users/he/x.json")).toBe("C:/Users/he/x.json");
    expect(fileUriToPath("https://example.com/x")).toBe("https://example.com/x");
  });
});

describe("canvas node projection contract", () => {
  it("keeps output refs with sha for the inspector", () => {
    const node: CanvasNode = {
      node_instance_id: "consumer-x",
      capability: "quant.experiment.evaluate",
      plugin_id: "quant.experiment-evaluator",
      attempt_seq: 1,
      attempt_id: "a1",
      status: "succeeded",
      error_kind: null,
      error_message: null,
      worker_id: "w1",
      input_bindings: {},
      output_refs: { evaluation: { uri: "file:///C:/x.json", sha256: "ab".repeat(32), size_bytes: 4 } },
      trace_id: "t1",
      created_at: "2026-09-09T00:00:00Z",
      finished_at: "2026-09-09T00:00:01Z",
    };
    expect(node.output_refs.evaluation.sha256).toHaveLength(64);
  });
});
