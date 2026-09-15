import { describe, expect, it } from "vitest";
import {
  CANVAS_STARTER_SOURCES,
  CANVAS_TASK_EXAMPLES,
  selectedSourcesFor,
  sourcePairsFor,
} from "./canvasStarter";

describe("first-run canvas planning sources", () => {
  it("maps only registered source choices to planner authorization pairs", () => {
    expect(sourcePairsFor(["ledger-a:ledger", "unknown:port"])).toEqual([["ledger-a", "ledger"]]);
    expect(sourcePairsFor(["ledger-b:ledger", "ledger-a:ledger"])).toEqual([
      ["ledger-a", "ledger"],
      ["ledger-b", "ledger"],
    ]);
  });

  it("does not turn source choices into file paths or artifact references", () => {
    for (const source of CANVAS_STARTER_SOURCES) {
      expect(source.nodeInstanceId).not.toContain("/");
      expect(source.portId).not.toContain("/");
      expect(source.detail).toContain("工件");
    }
  });

  it("provides a safe plan-only audit example", () => {
    expect(selectedSourcesFor(["ledger-a:ledger"])).toHaveLength(1);
    expect(CANVAS_TASK_EXAMPLES[0].message).toContain("不要修改原始数据");
    expect(CANVAS_TASK_EXAMPLES[0].message).toContain("数据流");
  });
});
