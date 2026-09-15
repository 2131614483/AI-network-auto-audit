import { describe, expect, it } from "vitest";
import { approvalStatusClass, dashboardMetrics } from "./dashboard";

describe("dashboard presentation contracts", () => {
  it("reports GUI slots, plugin count, and gateway state", () => {
    const metrics = dashboardMetrics(["dashboard.cards", "workspace.tools"], [{ plugin_id: "audit", plugin_version: "1", navigation_count: 1, view_count: 1, action_count: 2 }], true);
    expect(metrics.map((metric) => metric.value)).toEqual(["2", "1", "已启用"]);
  });

  it("only applies known approval status classes", () => {
    expect(approvalStatusClass("pending")).toBe("pending");
    expect(approvalStatusClass("unexpected")).toBe("unknown");
  });
});
