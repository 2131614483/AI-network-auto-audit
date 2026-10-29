import { EventEmitter } from "node:events";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { PassThrough } from "node:stream";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({ calls: [] as string[], revision: "0069", restoreOk: true, dumpOk: true, gateOk: true,
  children: [] as Array<{ child: EventEmitter & { stdout: PassThrough; stderr: PassThrough }; env: NodeJS.ProcessEnv }> }));
vi.mock("node:child_process", async () => {
  const fs = await import("node:fs");
  return {
    spawnSync: (command: string, args: string[]) => {
      const executable = command.split(/[\\/]/).pop() ?? command;
      fake.calls.push(`${executable} ${args.join(" ")}`);
      if (args.includes("preview-planning")) return { status: 0, stdout: JSON.stringify({ model_access: {
        model: "fixture-model", endpoint: "http://127.0.0.1:12345/v1", config_hash: "a".repeat(64) } }), stderr: "" };
      if (args.includes("packages.policy.maintenance")) return { status: fake.gateOk ? 0 : 1,
        stdout: JSON.stringify({ record: { operation_id: "fixture-operation" }, replayed: false }), stderr: "" };
      if (executable === "python.exe" && args[0] === "-c") return { status: 0, stdout: JSON.stringify({ target: "0069", ancestors: ["0069", "0068"] }), stderr: "" };
      if (executable === "pg_ctl.exe") return { status: 0, stdout: "running", stderr: "" };
      if (executable === "psql.exe") {
        const sql = args[args.length - 1];
        return { status: 0, stdout: sql.includes("pg_database") ? "1\n" : sql.includes("to_regclass") ? "t\n" : `${fake.revision}\n`, stderr: "" };
      }
      if (executable === "pg_dump.exe") {
        fs.writeFileSync(args[args.indexOf("--file") + 1], "synthetic archive");
        if (!fake.dumpOk) return { status: 1, stdout: "", stderr: "fixture failure" };
      }
      if (executable === "pg_restore.exe") return { status: fake.restoreOk ? 0 : 1, stdout: "", stderr: "" };
      return { status: 0, stdout: "", stderr: "" };
    },
    spawn: (_command: string, _args: string[], options: { env: NodeJS.ProcessEnv }) => {
      const child = Object.assign(new EventEmitter(), { stdout: new PassThrough(), stderr: new PassThrough(), killed: false, kill: vi.fn() });
      fake.children.push({ child, env: options.env });
      return child;
    },
  };
});

import { enablePackagedPlanning, startPackagedBackend, stopPackagedBackend } from "./backend";

function setup(existing: boolean) {
  const root = mkdtempSync(join(tmpdir(), "audit-network-maintenance-test-"));
  const resourcesPath = join(root, "resources");
  const userDataPath = join(root, "user");
  for (const path of [join(resourcesPath, "runtime", "python", "python.exe"), join(resourcesPath, "runtime", "pgsql", "bin", "pg_ctl.exe")]) {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, "fixture");
  }
  mkdirSync(join(resourcesPath, "app"), { recursive: true });
  if (existing) {
    const data = join(userDataPath, "data");
    mkdirSync(join(data, "pgdata"), { recursive: true });
    writeFileSync(join(data, "pgdata", "PG_VERSION"), "16");
    writeFileSync(join(data, "initialized.json"), "{}");
    writeFileSync(join(data, "runtime.json"), JSON.stringify({ bootstrapPassword: "fixture", appPassword: "fixture", migratorPassword: "fixture" }));
  }
  return { resourcesPath, userDataPath, onLog: vi.fn(), requestMaintenance: vi.fn().mockResolvedValue(true) };
}

beforeEach(() => { fake.calls = []; fake.children = []; fake.revision = "0069"; fake.restoreOk = true; fake.dumpOk = true; fake.gateOk = true; vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true })); });
afterEach(async () => { await stopPackagedBackend(); vi.unstubAllGlobals(); });

describe("packaged startup maintenance boundary", () => {
  it("does not migrate an existing current workspace", async () => {
    const options = setup(true);
    await startPackagedBackend(options);
    expect(options.requestMaintenance).not.toHaveBeenCalled();
    expect(fake.calls.some(call => call.includes("alembic upgrade"))).toBe(false);
    expect(fake.calls.some(call => call.startsWith("pg_dump.exe"))).toBe(false);
  });

  it("does not create a database when initialization is cancelled", async () => {
    const options = setup(false);
    options.requestMaintenance.mockResolvedValue(false);
    await expect(startPackagedBackend(options)).rejects.toThrow("cancelled");
    expect(fake.calls.some(call => call.startsWith("initdb.exe"))).toBe(false);
    expect(fake.calls.some(call => call.includes("alembic upgrade"))).toBe(false);
  });

  it("only initializes a new workspace after its explicit action", async () => {
    const options = setup(false);
    options.requestMaintenance.mockImplementation(async () => { fake.calls.push("user-confirmed"); return true; });
    await startPackagedBackend(options);
    const confirmation = fake.calls.indexOf("user-confirmed");
    const initdb = fake.calls.findIndex(call => call.startsWith("initdb.exe"));
    const migration = fake.calls.findIndex(call => call.includes("alembic upgrade 0069"));
    expect(confirmation).toBeGreaterThanOrEqual(0);
    expect(initdb).toBeGreaterThan(confirmation);
    expect(migration).toBeGreaterThan(initdb);
  });

  it("does not migrate when the database dump fails", async () => {
    const options = setup(true);
    fake.revision = "0068";
    fake.dumpOk = false;
    await expect(startPackagedBackend(options)).rejects.toThrow("数据库备份失败");
    expect(fake.calls.some(call => call.startsWith("pg_restore.exe"))).toBe(false);
    expect(fake.calls.some(call => call.includes("alembic upgrade"))).toBe(false);
  });

  it("does not migrate after an unreadable backup", async () => {
    const options = setup(true);
    fake.revision = "0068";
    fake.restoreOk = false;
    await expect(startPackagedBackend(options)).rejects.toThrow("backup verification failed");
    expect(options.requestMaintenance).toHaveBeenCalledOnce();
    expect(fake.calls.some(call => call.startsWith("pg_dump.exe"))).toBe(true);
    expect(fake.calls.some(call => call.includes("alembic upgrade"))).toBe(false);
  });

  it("runs an accepted upgrade only after dumping and reading the backup", async () => {
    const options = setup(true);
    fake.revision = "0068";
    await startPackagedBackend(options);
    const dump = fake.calls.findIndex(call => call.startsWith("pg_dump.exe"));
    const restore = fake.calls.findIndex(call => call.startsWith("pg_restore.exe"));
    const migration = fake.calls.findIndex(call => call.includes("alembic upgrade 0069"));
    expect(dump).toBeGreaterThanOrEqual(0);
    expect(restore).toBeGreaterThan(dump);
    expect(migration).toBeGreaterThan(restore);
  });

  it("rejects an unrecognized revision without offering an upgrade", async () => {
    const options = setup(true);
    fake.revision = "unknown";
    await expect(startPackagedBackend(options)).rejects.toThrow("数据库版本未知");
    expect(options.requestMaintenance).not.toHaveBeenCalled();
    expect(fake.calls.some(call => call.includes("alembic upgrade"))).toBe(false);
  });

  it("blocks initialization when the maintenance gateway denies it", async () => {
    fake.gateOk = false;
    await expect(startPackagedBackend(setup(false))).rejects.toThrow("维护策略");
    expect(fake.calls.some(call => call.startsWith("initdb.exe"))).toBe(false);
  });

  it("obtains a gateway decision before initialization", async () => {
    await startPackagedBackend(setup(false));
    expect(fake.calls.findIndex(call => call.includes("packages.policy.maintenance begin")))
      .toBeLessThan(fake.calls.findIndex(call => call.startsWith("initdb.exe")));
    expect(fake.calls.some(call => call.includes("packages.policy.maintenance finish"))).toBe(true);
  });

  it("handles child startup errors and writes service logs", async () => {
    const options = setup(true);
    await startPackagedBackend(options);
    expect(() => fake.children[1].child.emit("error", new Error("fixture spawn failure"))).not.toThrow();
    fake.children[0].child.stdout.write("synthetic service output\n");
    const { readFileSync } = await import("node:fs");
    expect(readFileSync(join(options.userDataPath, "data", "logs", "api.log"), "utf8")).toContain("synthetic service output");
    expect(options.onLog).toHaveBeenCalledWith(expect.stringContaining("fixture spawn failure"));
  });

  it("passes explicit collaborative switches to its own worker", async () => {
    vi.stubEnv("AUDIT_NETWORK_COLLABORATIVE_PLANNING", "1");
    try {
      await startPackagedBackend(setup(true));
      expect(fake.children[1].env.AUDIT_NETWORK_COLLABORATIVE_PLANNING).toBe("1");
    } finally { vi.unstubAllEnvs(); }
  });

  it("keeps planning disabled when native confirmation is cancelled", async () => {
    await startPackagedBackend(setup(true));
    expect((await enablePackagedPlanning(async () => false)).enabled).toBe(false);
    expect(fake.calls.some(call => call.includes("enable-planning"))).toBe(false);
  });

  it("enables only the previewed model through the gateway and restarts its worker", async () => {
    await startPackagedBackend(setup(true));
    const confirm = vi.fn().mockResolvedValue(true);
    expect((await enablePackagedPlanning(confirm)).enabled).toBe(true);
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ model: "fixture-model" }));
    expect(fake.calls.some(call => call.includes("enable-planning"))).toBe(true);
    expect(fake.children.length).toBe(3);
    expect(fake.children[2].env.AUDIT_NETWORK_COLLABORATIVE_PLANNING).toBe("1");
  });
});
