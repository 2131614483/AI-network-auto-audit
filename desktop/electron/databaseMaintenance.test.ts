import { describe, expect, it, vi } from "vitest";
import { prepareDatabase, type DatabaseOperations } from "./databaseMaintenance";

function fixture(overrides: Partial<DatabaseOperations> = {}): DatabaseOperations {
  return {
    inspect: vi.fn().mockResolvedValue({ state: "ready", current: "0069", target: "0069" }),
    requestAction: vi.fn().mockResolvedValue(true),
    initialize: vi.fn().mockResolvedValue(undefined),
    backup: vi.fn().mockResolvedValue("backup.dump"),
    verifyBackup: vi.fn().mockResolvedValue(true),
    migrate: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe("explicit database maintenance", () => {
  it("opens an up-to-date workspace without migrations or prompts", async () => {
    const ops = fixture();
    await prepareDatabase(ops);
    expect(ops.requestAction).not.toHaveBeenCalled();
    expect(ops.initialize).not.toHaveBeenCalled();
    expect(ops.backup).not.toHaveBeenCalled();
    expect(ops.migrate).not.toHaveBeenCalled();
  });

  it("only initializes after explicit consent", async () => {
    const ops = fixture({ inspect: vi.fn().mockResolvedValue({ state: "new", target: "0069" }) });
    await prepareDatabase(ops);
    expect(ops.requestAction).toHaveBeenCalledWith({ kind: "initialize", target: "0069" });
    expect(ops.initialize).toHaveBeenCalledOnce();
  });

  it("does not initialize or migrate when the action is cancelled", async () => {
    const ops = fixture({ inspect: vi.fn().mockResolvedValue({ state: "new", target: "0069" }), requestAction: vi.fn().mockResolvedValue(false) });
    await expect(prepareDatabase(ops)).rejects.toThrow("cancelled");
    expect(ops.initialize).not.toHaveBeenCalled();
    expect(ops.migrate).not.toHaveBeenCalled();
  });

  it("backs up and verifies before an authorized upgrade", async () => {
    const sequence: string[] = [];
    const ops = fixture({
      inspect: vi.fn().mockResolvedValue({ state: "upgrade", current: "0068", target: "0069" }),
      requestAction: vi.fn().mockImplementation(async () => { sequence.push("confirm"); return true; }),
      backup: vi.fn().mockImplementation(async () => { sequence.push("backup"); return "backup.dump"; }),
      verifyBackup: vi.fn().mockImplementation(async () => { sequence.push("verify"); return true; }),
      migrate: vi.fn().mockImplementation(async () => { sequence.push("migrate"); }),
    });
    await prepareDatabase(ops);
    expect(sequence).toEqual(["confirm", "backup", "verify", "migrate"]);
    expect(ops.migrate).toHaveBeenCalledWith("0069");
  });

  it("never migrates after a rejected or failed backup", async () => {
    const ops = fixture({ inspect: vi.fn().mockResolvedValue({ state: "upgrade", current: "0068", target: "0069" }), verifyBackup: vi.fn().mockResolvedValue(false) });
    await expect(prepareDatabase(ops)).rejects.toThrow("backup");
    expect(ops.migrate).not.toHaveBeenCalled();
  });

  it("does not back up or migrate when an upgrade is cancelled", async () => {
    const ops = fixture({ inspect: vi.fn().mockResolvedValue({ state: "upgrade", current: "0068", target: "0069" }), requestAction: vi.fn().mockResolvedValue(false) });
    await expect(prepareDatabase(ops)).rejects.toThrow("cancelled");
    expect(ops.backup).not.toHaveBeenCalled();
    expect(ops.migrate).not.toHaveBeenCalled();
  });

  it("does not offer to downgrade or repair an unrecognized workspace", async () => {
    const ops = fixture({ inspect: vi.fn().mockResolvedValue({ state: "blocked", reason: "unknown revision" }) });
    await expect(prepareDatabase(ops)).rejects.toThrow("unknown revision");
    expect(ops.requestAction).not.toHaveBeenCalled();
    expect(ops.migrate).not.toHaveBeenCalled();
  });
});
