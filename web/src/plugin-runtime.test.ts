import { describe, expect, it } from "vitest";
import { actionRequest, normalizeContribution, isSupportedSlot } from "./plugin-runtime";

const contribution = {
  schema_version: "1.0.0",
  navigation: [
    { slot: "workspace.tools", title_key: "demo.title", route: "/plugins/demo" },
    { slot: "system.override", title_key: "bad", route: "/bad" },
  ],
  views: [],
  actions: [
    { id: "inspect", capability: "audit.demo.inspect", label_key: "inspect", argument_schema: { type: "object" }, placement: ["detail.header"] },
  ],
  i18n: ["zh-CN"],
};

describe("plugin runtime", () => {
  it("filters unknown slots and preserves typed capability actions", () => {
    const normalized = normalizeContribution(contribution);
    expect(normalized.navigation).toHaveLength(1);
    expect(isSupportedSlot("workspace.tools")).toBe(true);
    expect(actionRequest(normalized.actions[0], { id: "x" }).capability).toBe("audit.demo.inspect");
  });

  it("rejects incompatible major versions", () => {
    expect(() => normalizeContribution({ ...contribution, schema_version: "2.0.0" })).toThrow();
  });
});
