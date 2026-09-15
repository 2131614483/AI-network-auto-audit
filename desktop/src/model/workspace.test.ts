import { describe, expect, it } from "vitest";

import { controlPlaneState, initialWorkspaceState, workspaceMetricRows } from "./workspace";

describe("desktop workspace presentation contract", () => {
  it("starts disconnected without pretending that the control plane is available", () => {
    expect(initialWorkspaceState.connection).toBe("connecting");
    expect(controlPlaneState(false)).toEqual({ label: "控制平面不可用", tone: "error" });
  });

  it("shows only declared bootstrap facts in the high-density metric strip", () => {
    const rows = workspaceMetricRows({
      apiVersion: "v1",
      shellVersion: "0.1.0",
      slotCount: 2,
      contributionCount: 1,
      policyGated: true,
    });

    expect(rows.map((row) => row.value)).toEqual(["v1", "2", "1", "已启用"]);
  });
});
