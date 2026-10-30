import { describe, expect, it } from "vitest";
import { initialGraphReadState, graphReadTransition, graphProposalsFromResponse } from "./graphReadState";

describe("graph proposal read state", () => {
  it("does not treat a policy failure as an empty successful result", () => {
    const pending = graphReadTransition(initialGraphReadState, { type: "start", request: 1, tenant: "a" });
    const denied = graphReadTransition(pending, { type: "failure", request: 1, message: "需要审批" });
    expect(denied.status).toBe("error");
    expect(denied.message).toContain("需要审批");
    expect(denied.proposals).toEqual([]);
  });

  it("only presents empty data after a successful request", () => {
    const pending = graphReadTransition(initialGraphReadState, { type: "start", request: 1, tenant: "a" });
    expect(pending.status).toBe("loading");
    expect(graphReadTransition(pending, { type: "success", request: 1, proposals: [] }).status).toBe("ready");
  });

  it("clears previous data and ignores responses from an earlier request", () => {
    const first = graphReadTransition(initialGraphReadState, { type: "start", request: 1, tenant: "a" });
    const loaded = graphReadTransition(first, { type: "success", request: 1, proposals: [{ id: "old" }] });
    const second = graphReadTransition(loaded, { type: "start", request: 2, tenant: "b" });
    expect(second.proposals).toEqual([]);
    expect(second.tenant).toBe("b");
    expect(graphReadTransition(second, { type: "success", request: 1, proposals: [{ id: "old" }] })).toEqual(second);
    expect(graphReadTransition(second, { type: "failure", request: 1, message: "old" })).toEqual(second);
  });

  it("rejects malformed successful responses instead of reporting an empty collection", () => {
    expect(() => graphProposalsFromResponse({})).toThrow();
    expect(() => graphProposalsFromResponse({ proposals: null })).toThrow();
    expect(graphProposalsFromResponse({ proposals: [] })).toEqual([]);
  });
});
