import { describe, expect, it } from "vitest";
import { buildPlanningBody } from "./collaborativePlanning";

const artifact = "00000000-0000-4000-8000-000000000001";
const plugins = [{ inputs: [{ port_id: "sampling-input", schema_ref: "artifact-ref.schema.json" }], outputs: [{ port_id: "sampling-plan", schema_ref: "artifact-ref.schema.json" }] }];

describe("collaborative planning submission", () => {
  it("binds a registered artifact to its exact directory contract", () => {
    const body = buildPlanningBody("生成抽样方案", "audit", ["sampling-plan"], `sampling-input,${artifact}`, plugins);
    expect(body.mode).toBe("plan_only");
    expect(body.inputs).toEqual([{ port_id: "sampling-input", artifact_id: artifact, schema_ref: "artifact-ref.schema.json" }]);
    expect(body.requirements[0].output_ports).toEqual(["sampling-plan"]);
  });
  it.each(["unknown", ""]) ("rejects an unknown target %s", target => {
    expect(() => buildPlanningBody("生成抽样方案", "audit", [target], "", plugins)).toThrow();
  });
  it("does not guess a port when directory contracts conflict", () => {
    const conflicting = [...plugins, { inputs: [{ port_id: "sampling-input", schema_ref: "different.schema.json" }], outputs: [] }];
    expect(() => buildPlanningBody("生成抽样方案", "audit", ["sampling-plan"], `sampling-input,${artifact}`, conflicting)).toThrow();
  });
  it.each([`unknown,${artifact}`, "sampling-input,not-a-uuid", `sampling-input,${artifact},extra`, `sampling-input,${artifact}\nsampling-input,${artifact}`])("rejects malformed or ambiguous bindings", binding => {
    expect(() => buildPlanningBody("生成抽样方案", "audit", ["sampling-plan"], binding, plugins)).toThrow();
  });
});
