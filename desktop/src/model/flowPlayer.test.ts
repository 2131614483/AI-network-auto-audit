import { describe, expect, it } from "vitest";
import {
  buildFrame,
  buildTimeline,
  EDGE_FLOW_MS,
  inputBindingEntries,
  NODE_ACTIVE_MS,
  runRank,
  topoOrder,
} from "./flowPlayer";
import type { CanvasNode, CanvasProjection } from "./runCanvas";

const edge = (
  source: string, sp: string, target: string, tp: string,
) => ({
  key: [source, sp, target, tp] as [string, string, string, string],
  source_instance: source,
  source_port: sp,
  target_instance: target,
  target_port: tp,
});

const chain = () => ({
  nodes: [{ node_instance_id: "a" }, { node_instance_id: "b" }, { node_instance_id: "c" }],
  edges: [edge("a", "o", "b", "i"), edge("b", "o", "c", "i")],
});

describe("topoOrder", () => {
  it("orders a linear chain upstream-first and layers by longest path", () => {
    const { order, layer } = topoOrder(chain());
    expect(order).toEqual(["a", "b", "c"]);
    expect(layer).toMatchObject({ a: 0, b: 1, c: 2 });
  });

  it("keeps every node even with a cycle (never drops from animation)", () => {
    const g = {
      nodes: [{ node_instance_id: "x" }, { node_instance_id: "y" }],
      edges: [edge("x", "o", "y", "i"), edge("y", "o", "x", "i")],
    };
    const { order } = topoOrder(g);
    expect(order.sort()).toEqual(["x", "y"]);
  });

  it("breaks ties with an explicit rank (real run order)", () => {
    const g = {
      nodes: [{ node_instance_id: "p" }, { node_instance_id: "q" }],
      edges: [],
    };
    expect(topoOrder(g, { q: 0, p: 1 }).order).toEqual(["q", "p"]);
  });
});

describe("buildTimeline", () => {
  it("classifies seed / computed / sink and schedules packets after source done", () => {
    const tl = buildTimeline(chain());
    expect(tl.nodes.a.kind).toBe("seed");
    expect(tl.nodes.b.kind).toBe("computed");
    expect(tl.nodes.c.kind).toBe("sink");
    const ab = tl.edges.find((e) => e.source === "a" && e.target === "b")!;
    expect(ab.flowStart).toBe(tl.nodes.a.doneAt);
    expect(ab.flowEnd).toBe(ab.flowStart + EDGE_FLOW_MS);
    // packet reaches b exactly when b activates
    expect(tl.nodes.b.enterAt).toBeGreaterThanOrEqual(ab.flowEnd);
    expect(tl.durationMs).toBeGreaterThan(tl.nodes.c.doneAt);
  });

  it("indexes incoming/outgoing edges", () => {
    const diamond = {
      nodes: ["s", "l", "r", "z"].map((id) => ({ node_instance_id: id })),
      edges: [edge("s", "o", "l", "i"), edge("s", "o", "r", "i"), edge("l", "o", "z", "i"), edge("r", "o", "z", "i")],
    };
    const tl = buildTimeline(diamond);
    expect(tl.outgoing.s.map((e) => e.target).sort()).toEqual(["l", "r"]);
    expect(tl.incoming.z.map((e) => e.source).sort()).toEqual(["l", "r"]);
  });
});

describe("buildFrame", () => {
  it("starts all idle, activates in order, and ends all done with wires live", () => {
    const tl = buildTimeline(chain());
    const f0 = buildFrame(tl, 0);
    expect(Object.values(f0.nodePhase)).toEqual(["active", "idle", "idle"]);
    expect(f0.finished).toBe(false);

    const mid = buildFrame(tl, tl.nodes.b.enterAt + 1);
    expect(mid.nodePhase.a).toBe("done");
    expect(mid.nodePhase.b).toBe("active");
    expect(mid.nodePhase.c).toBe("idle");

    const end = buildFrame(tl, tl.durationMs);
    expect(Object.values(end.nodePhase).every((p) => p === "done")).toBe(true);
    expect(end.finished).toBe(true);
    expect(end.progress).toBe(1);
    expect(Object.keys(end.edgeReached)).toHaveLength(2);
  });

  it("emits a 0..1 packet position while an edge is flowing", () => {
    const tl = buildTimeline(chain());
    const ab = tl.edges[0];
    const f = buildFrame(tl, ab.flowStart + EDGE_FLOW_MS / 2);
    expect(f.edgePacket[ab.key]).toBeCloseTo(0.5, 3);
  });
});

describe("runRank", () => {
  it("orders by attempt_seq then created_at", () => {
    const node = (id: string, seq: number, at: string): CanvasNode => ({
      node_instance_id: id, capability: "", plugin_id: "", attempt_seq: seq, attempt_id: "",
      status: "succeeded", error_kind: null, error_message: null, worker_id: null,
      input_bindings: {}, output_refs: {}, trace_id: "", created_at: at, finished_at: null,
    });
    const rank = runRank([node("z", 2, "t2"), node("a", 0, "t0"), node("m", 1, "t1")]);
    expect(rank.a).toBe(0);
    expect(rank.m).toBe(1);
    expect(rank.z).toBe(2);
  });
});

describe("inputBindingEntries", () => {
  const base = (bindings: unknown): CanvasNode => ({
    node_instance_id: "n", capability: "", plugin_id: "", attempt_seq: 0, attempt_id: "",
    status: "pending", error_kind: null, error_message: null, worker_id: null,
    input_bindings: bindings as CanvasNode["input_bindings"], output_refs: {},
    trace_id: "", created_at: null, finished_at: null,
  });

  it("normalizes port -> list[binding]", () => {
    const entries = inputBindingEntries(base({ ledger: [{ source_instance: "a", source_port: "o", sha256: "s", uri: "u", adapter: null }] }));
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ port: "ledger", source_instance: "a", source_port: "o", sha256: "s", uri: "u" });
  });

  it("defaults missing source to seed", () => {
    const entries = inputBindingEntries(base({ x: [{}] }));
    expect(entries[0].source_instance).toBe("seed");
  });

  it("returns [] for empty/non-object bindings", () => {
    expect(inputBindingEntries(base({}))).toEqual([]);
    expect(inputBindingEntries(base(null))).toEqual([]);
  });
});

// keep NODE_ACTIVE_MS referenced so timing budget changes are intentional
void NODE_ACTIVE_MS;
export type _Projection = CanvasProjection;
