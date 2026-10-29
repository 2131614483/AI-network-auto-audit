import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { join } from "node:path";
import { prepareDatabase, type DatabaseState, type MaintenanceAction } from "./databaseMaintenance";

/**
 * Packaged-install supervisor.
 *
 * In development the API, worker and database are started by the PowerShell
 * scripts and the desktop shell only talks to an already running control plane.
 * A packaged install cannot assume any of that, so this module owns the whole
 * lifecycle: portable PostgreSQL, one-time workspace initialisation, the API
 * and the worker.
 *
 * Layout produced by the build (`process.resourcesPath`):
 *
 *   runtime\pgsql\bin\pg_ctl.exe
 *   runtime\python\python.exe
 *   app\apps\api\main.py ...
 *
 * Per-user writable state stays in `app.getPath("userData")`:
 *
 *   data\pgdata          database cluster
 *   data\runtime.json    generated credentials and ports
 *   data\logs            api / worker logs
 *
 * Ports are chosen at runtime: a machine may already run another PostgreSQL on
 * 54329 or another service on 8010, and a portable install must not require the
 * user to free a port by hand. The chosen pair is persisted in runtime.json so
 * a restart reuses the same one whenever it is still available.
 */

const DEFAULT_PG_PORT = 54329;
const DEFAULT_API_PORT = 8010;
/** 端口被占时向后探测的范围；超过则交给系统分配。 */
const PORT_SPAN = 30;

type Credentials = {
  bootstrapPassword: string;
  appPassword: string;
  migratorPassword: string;
  /** 上次成功的端口；旧版本 runtime.json 没有这两个字段。 */
  pgPort?: number;
  apiPort?: number;
};

type RuntimePaths = {
  resources: string;
  dataDir: string;
  pgData: string;
  pgBin: string;
  python: string;
  appDir: string;
  credentials: Credentials;
  pgPort: number;
  apiPort: number;
};

export type BackendOptions = {
  resourcesPath: string;
  userDataPath: string;
  onLog: (message: string) => void;
  requestMaintenance: (action: MaintenanceAction) => Promise<boolean>;
};

let apiProcess: ChildProcess | null = null;
let workerProcess: ChildProcess | null = null;
let pgStarted = false;
let stopped = false;
let runtimePaths: RuntimePaths | null = null;
let runtimeOptions: BackendOptions | null = null;

function log(options: BackendOptions, message: string): void {
  options.onLog(message);
}

function credentialsPath(dataDir: string): string {
  return join(dataDir, "runtime.json");
}

function loadCredentials(dataDir: string): Credentials {
  const path = credentialsPath(dataDir);
  if (existsSync(path)) {
    return JSON.parse(readFileSync(path, "utf8")) as Credentials;
  }
  const generated: Credentials = {
    bootstrapPassword: randomBytes(24).toString("base64url"),
    appPassword: randomBytes(24).toString("base64url"),
    migratorPassword: randomBytes(24).toString("base64url"),
  };
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(path, JSON.stringify(generated, null, 2), { mode: 0o600 });
  return generated;
}

function savePorts(paths: RuntimePaths): void {
  const state = {
    ...paths.credentials,
    pgPort: paths.pgPort,
    apiPort: paths.apiPort,
  };
  try {
    writeFileSync(credentialsPath(paths.dataDir), JSON.stringify(state, null, 2), { mode: 0o600 });
  } catch {
    // 端口持久化失败不影响本次启动
  }
}

function resolvePaths(options: BackendOptions): RuntimePaths {
  const resources = options.resourcesPath;
  const dataDir = join(options.userDataPath, "data");
  mkdirSync(join(dataDir, "logs"), { recursive: true });
  const python = join(resources, "runtime", "python", "python.exe");
  const pgBin = join(resources, "runtime", "pgsql", "bin");
  if (!existsSync(python)) {
    throw new Error(`便携 Python 运行时缺失：${python}`);
  }
  if (!existsSync(join(pgBin, "pg_ctl.exe"))) {
    throw new Error(`便携数据库运行时缺失：${pgBin}`);
  }
  const credentials = loadCredentials(dataDir);
  return {
    resources,
    dataDir,
    pgData: join(dataDir, "pgdata"),
    pgBin,
    python,
    appDir: join(resources, "app"),
    credentials,
    pgPort: credentials.pgPort ?? DEFAULT_PG_PORT,
    apiPort: credentials.apiPort ?? DEFAULT_API_PORT,
  };
}

function run(
  command: string,
  args: string[],
  options: { cwd?: string; env?: NodeJS.ProcessEnv; quiet?: boolean; detachStdio?: boolean; input?: string } = {},
): { status: number; stdout: string; stderr: string } {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: { ...process.env, ...options.env },
    encoding: "utf8",
    input: options.input,
    windowsHide: true,
    maxBuffer: 32 * 1024 * 1024,
    // pg_ctl 会拉起 postmaster，而后者继承 stdout/stderr 句柄；
    // 若这里用 pipe，spawnSync 会一直等不到 EOF 而永久阻塞。
    stdio: options.detachStdio ? "ignore" : "pipe",
  });
  const stdout = result.stdout ?? "";
  const stderr = result.stderr ?? "";
  if (!options.quiet && stderr.trim()) {
    // 只记录尾部，避免把整个 psql 输出灌进日志面板
    console.warn(stderr.slice(-2000));
  }
  return { status: result.status ?? -1, stdout, stderr };
}

function isPortFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const server = createServer();
    const done = (free: boolean) => {
      server.removeAllListeners();
      resolve(free);
    };
    server.once("error", () => done(false));
    server.once("listening", () => server.close(() => done(true)));
    server.listen(port, "127.0.0.1");
  });
}

function randomFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : DEFAULT_API_PORT;
      server.close(() => resolve(port));
    });
  });
}

async function pickPort(preferred: number): Promise<number> {
  for (let offset = 0; offset < PORT_SPAN; offset += 1) {
    const candidate = preferred + offset;
    if (candidate > 65535) break;
    if (await isPortFree(candidate)) return candidate;
  }
  return randomFreePort();
}

function psqlBase(paths: RuntimePaths): { command: string; args: string[] } {
  return {
    command: join(paths.pgBin, "psql.exe"),
    args: ["-h", "127.0.0.1", "-p", String(paths.pgPort), "-v", "ON_ERROR_STOP=1", "-tA"],
  };
}

function psql(
  paths: RuntimePaths,
  user: string,
  password: string,
  database: string,
  sql: string,
): { status: number; stdout: string; stderr: string } {
  const base = psqlBase(paths);
  return run(base.command, [...base.args, "-U", user, "-d", database, "-c", sql], {
    env: { PGPASSWORD: password },
    quiet: true,
  });
}

function isServerRunning(paths: RuntimePaths): boolean {
  // Confirm this exact data directory owns the server; an unrelated server
  // answering on the same port must never be adopted as this workspace.
  const result = run(join(paths.pgBin, "pg_ctl.exe"), ["-D", paths.pgData, "status"], { quiet: true });
  return result.status === 0;
}

/**
 * 选定数据库端口。
 *
 * 已有簇且 `postmaster.pid` 存在 → 端口上的实例很可能就是它自己（上次异常退出
 * 留下的），沿用该端口交给 `pg_isready` 判定；否则端口被别人占着，必须换一个，
 * 不然 initdb / pg_ctl 会失败。
 */
async function resolvePgPort(paths: RuntimePaths): Promise<number> {
  const clusterExists = existsSync(join(paths.pgData, "PG_VERSION"));
  const pidFile = join(paths.pgData, "postmaster.pid");
  const preferred = paths.pgPort;
  if (clusterExists && existsSync(pidFile)) return preferred;
  if (await isPortFree(preferred)) return preferred;
  return pickPort(preferred + 1);
}

function initCluster(options: BackendOptions, paths: RuntimePaths): void {
  if (existsSync(join(paths.pgData, "PG_VERSION"))) return;
  log(options, "正在创建本地数据库实例（首次启动约需 10 秒）…");
  mkdirSync(paths.pgData, { recursive: true });
  const pwFile = join(paths.dataDir, ".initdb-pw");
  writeFileSync(pwFile, paths.credentials.bootstrapPassword, { mode: 0o600 });
  try {
    const result = run(join(paths.pgBin, "initdb.exe"), [
      "-D", paths.pgData,
      "-U", "audit_bootstrap",
      `--pwfile=${pwFile}`,
      "-E", "UTF8",
      "--locale=C",
      "-A", "scram-sha-256",
    ], { quiet: true });
    if (result.status !== 0) {
      throw new Error(`initdb 失败：${result.stderr.slice(-1200)}`);
    }
  } finally {
    rmSync(pwFile, { force: true });
  }
}

function startDatabase(options: BackendOptions, paths: RuntimePaths): void {
  initCluster(options, paths);
  if (isServerRunning(paths)) {
    log(options, "本地数据库已在运行");
    return;
  }
  const logFile = join(paths.dataDir, "logs", "postgres.log");
  const result = run(join(paths.pgBin, "pg_ctl.exe"), [
    "-D", paths.pgData,
    "-l", logFile,
    "-o", `-p ${paths.pgPort} -c listen_addresses=127.0.0.1`,
    "start", "-w", "-t", "90",
  ], { quiet: true, detachStdio: true });
  if (result.status !== 0) {
    const tail = existsSync(logFile) ? readFileSync(logFile, "utf8").slice(-1200) : "";
    throw new Error(`本地数据库启动失败：${tail}`);
  }
  pgStarted = true;
  log(options, `本地数据库已启动（端口 ${paths.pgPort}）`);
}

function ensureRolesAndDatabase(options: BackendOptions, paths: RuntimePaths): void {
  const password = paths.credentials.bootstrapPassword;
  const existing = psql(
    paths, "audit_bootstrap", password, "postgres",
    "SELECT 1 FROM pg_database WHERE datname='audit_network'",
  );
  if (existing.status !== 0) {
    throw new Error(`连接本地数据库失败：${existing.stderr.slice(-800)}`);
  }
  if (existing.stdout.trim() === "1") return;

  log(options, "正在初始化工作区角色与扩展…");
  const statements = [
    `CREATE ROLE audit_app NOINHERIT LOGIN PASSWORD '${paths.credentials.appPassword}'`,
    `CREATE ROLE audit_migrator NOINHERIT LOGIN PASSWORD '${paths.credentials.migratorPassword}'`,
    "CREATE DATABASE audit_network OWNER audit_migrator",
  ];
  for (const statement of statements) {
    const result = psql(paths, "audit_bootstrap", password, "postgres", statement);
    if (result.status !== 0) {
      throw new Error(`初始化 SQL 失败：${statement.slice(0, 40)} / ${result.stderr.slice(-600)}`);
    }
  }
  const extensions = [
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS ltree",
    "CREATE EXTENSION IF NOT EXISTS vector",
    "GRANT USAGE ON SCHEMA public TO audit_app",
  ];
  for (const statement of extensions) {
    const result = psql(paths, "audit_bootstrap", password, "audit_network", statement);
    if (result.status !== 0) {
      throw new Error(`创建扩展失败：${statement} / ${result.stderr.slice(-600)}`);
    }
  }
}

function pythonEnv(paths: RuntimePaths): NodeJS.ProcessEnv {
  const inherited: NodeJS.ProcessEnv = {};
  for (const key of ["SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE",
    "COMSPEC", "AUDIT_NETWORK_COLLABORATIVE_PLANNING", "AUDIT_COLLABORATIVE_DEBUG_SYNTHETIC",
    "AUDIT_NETWORK_TENANT_SLUG", "AUDIT_NETWORK_SKIP_DOTENV"]) {
    if (process.env[key] !== undefined) inherited[key] = process.env[key];
  }
  return {
    ...inherited,
    ...(existsSync(join(paths.dataDir, "planning-enabled.json")) ? { AUDIT_NETWORK_COLLABORATIVE_PLANNING: "1" } : {}),
    DATABASE_URL: `postgresql://audit_app:${encodeURIComponent(paths.credentials.appPassword)}@127.0.0.1:${paths.pgPort}/audit_network`,
    AUDIT_NETWORK_DATA_DIR: paths.dataDir,
    PYTHONNOUSERSITE: "1",
    PYTHONIOENCODING: "utf-8",
    PYTHONDONTWRITEBYTECODE: "1",
  };
}

function pythonStep(
  options: BackendOptions,
  paths: RuntimePaths,
  label: string,
  args: string[],
  env: NodeJS.ProcessEnv,
): void {
  log(options, label);
  const result = run(paths.python, args, {
    cwd: paths.appDir,
    env,
    quiet: true,
  });
  if (result.status !== 0) {
    throw new Error(`${label}失败：${(result.stderr || result.stdout).slice(-1500)}`);
  }
}

function migrate(options: BackendOptions, paths: RuntimePaths, target: string): void {
  pythonStep(
    options, paths, "正在升级数据库结构…",
    ["-m", "alembic", "upgrade", target],
    {
      ...pythonEnv(paths),
      DATABASE_URL: `postgresql://audit_migrator:${encodeURIComponent(paths.credentials.migratorPassword)}@127.0.0.1:${paths.pgPort}/audit_network`,
    },
  );
}

function schemaHistory(paths: RuntimePaths): { target: string; ancestors: string[] } {
  const code = "import json; from alembic.config import Config; from alembic.script import ScriptDirectory; " +
    "s=ScriptDirectory.from_config(Config('alembic.ini')); " +
    "print(json.dumps({'target':s.get_current_head(),'ancestors':[r.revision for r in s.walk_revisions()]}))";
  const result = run(paths.python, ["-c", code], { cwd: paths.appDir, env: pythonEnv(paths), quiet: true });
  if (result.status !== 0) throw new Error("无法检查安装包数据库版本");
  const value: unknown = JSON.parse(result.stdout);
  if (!value || typeof value !== "object" || !("target" in value) || !("ancestors" in value) ||
      typeof value.target !== "string" || !Array.isArray(value.ancestors) ||
      !value.ancestors.every((item: unknown) => typeof item === "string")) {
    throw new Error("安装包数据库版本信息无效");
  }
  return { target: value.target, ancestors: value.ancestors as string[] };
}

function inspectDatabase(options: BackendOptions, paths: RuntimePaths): DatabaseState {
  const history = schemaHistory(paths);
  if (!existsSync(join(paths.pgData, "PG_VERSION"))) {
    if (existsSync(paths.pgData) && readdirSync(paths.pgData).length > 0) {
      return { state: "blocked", reason: "数据库目录已有内容但不是完整工作区，请先检查并恢复。" };
    }
    return { state: "new", target: history.target };
  }
  startDatabase(options, paths);
  const query = (database: string, sql: string) => psql(paths, "audit_bootstrap", paths.credentials.bootstrapPassword, database, sql);
  const exists = query("postgres", "SELECT 1 FROM pg_database WHERE datname='audit_network'");
  if (exists.status !== 0 || exists.stdout.trim() !== "1") {
    return { state: "blocked", reason: "已有数据库实例无法确认工作区，请先检查并恢复。" };
  }
  const table = query("audit_network", "SELECT to_regclass('public.alembic_version') IS NOT NULL");
  if (table.status !== 0 || table.stdout.trim() !== "t" || !existsSync(join(paths.dataDir, "initialized.json"))) {
    return { state: "blocked", reason: "工作区初始化不完整，请先检查并恢复。" };
  }
  const revision = query("audit_network", "SELECT version_num FROM public.alembic_version");
  const current = revision.stdout.trim();
  if (revision.status !== 0 || !history.ancestors.includes(current)) {
    return { state: "blocked", reason: "数据库版本未知或比软件更新，不能自动升级。" };
  }
  return current === history.target
    ? { state: "ready", current, target: history.target }
    : { state: "upgrade", current, target: history.target };
}

function backupDatabase(options: BackendOptions, paths: RuntimePaths): string {
  const directory = join(paths.dataDir, "backups");
  mkdirSync(directory, { recursive: true });
  const filename = join(directory, `pre-upgrade-${randomUUID()}.dump`);
  if (existsSync(filename)) throw new Error("备份文件已存在");
  log(options, `正在备份数据库：${filename}`);
  const result = run(join(paths.pgBin, "pg_dump.exe"), [
    "-h", "127.0.0.1", "-p", String(paths.pgPort), "-U", "audit_bootstrap",
    "-Fc", "--file", filename, "audit_network",
  ], { env: { PGPASSWORD: paths.credentials.bootstrapPassword }, quiet: true });
  if (result.status !== 0) throw new Error(`数据库备份失败；保留备份文件：${filename}`);
  return filename;
}

function verifyDatabaseBackup(paths: RuntimePaths, filename: string): boolean {
  if (!existsSync(filename)) return false;
  // Render the entire archive to the Windows null device, so data corruption
  // outside the table of contents also blocks an upgrade.
  return run(join(paths.pgBin, "pg_restore.exe"), ["--file", "NUL", filename], { quiet: true }).status === 0;
}

function provisionDemoData(options: BackendOptions, paths: RuntimePaths): void {
  const marker = join(paths.dataDir, "initialized.json");
  if (existsSync(marker)) return;
  const env = pythonEnv(paths);
  pythonStep(options, paths, "正在写入示例数据…", [
    "-c",
    "import os; from packages.demo.seed import seed_local_demo; print(seed_local_demo(os.environ['DATABASE_URL']))",
  ], env);
  pythonStep(options, paths, "正在注册插件目录…", [
    "-m", "packages.plugin_runtime.registration",
    "--database-url", env.DATABASE_URL as string,
    "--tenant-slug", "local-dev",
  ], env);
  pythonStep(options, paths, "正在建立能力图谱索引…", [
    join("scripts", "index-plugin-graph.py"),
    "--database-url", env.DATABASE_URL as string,
    "--domain", "audit", "--domain", "aiops", "--domain", "quant", "--domain", "knowledge",
  ], env);
}

function maintenanceGateway(paths: RuntimePaths, operation: "begin" | "finish", payload: Record<string, unknown>): string {
  const result = run(paths.python, ["-m", "packages.policy.maintenance", operation, "--workspace", paths.dataDir], {
    cwd: paths.appDir, env: pythonEnv(paths), quiet: true, input: JSON.stringify(payload),
  });
  if (result.status !== 0) throw new Error(`维护策略未允许操作或审计记录未完成。请检查 ${join(paths.dataDir, "maintenance", "history.jsonl")}`);
  const response = JSON.parse(result.stdout) as { record?: { operation_id?: string }; replayed?: boolean };
  if (!response.record?.operation_id || (operation === "begin" && response.replayed)) {
    throw new Error("维护操作已有记录或响应无效，请先检查恢复状态。");
  }
  return response.record.operation_id;
}

function spawnService(
  options: BackendOptions,
  paths: RuntimePaths,
  name: string,
  args: string[],
  logFile: string,
): ChildProcess {
  const child = spawn(paths.python, args, {
    cwd: paths.appDir,
    env: pythonEnv(paths),
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const append = (chunk: Buffer) => {
    const text = chunk.toString("utf8").trimEnd();
    if (text) {
      const sanitized = text.replace(/\b(password|api[_-]?key|token|secret)(\s*[:=]\s*)\S+/gi, "$1$2***")
        .replace(/(postgres(?:ql)?:\/\/[^:\s/]+:)[^@\s]+@/gi, "$1***@");
      try { appendFileSync(logFile, sanitized + "\n", "utf8"); }
      catch { options.onLog(`[${name}] 日志写入失败：${logFile}`); }
      options.onLog(`[${name}] ${sanitized}`);
    }
  };
  child.stdout?.on("data", append);
  child.stderr?.on("data", append);
  child.on("error", (error) => {
    append(Buffer.from(`进程启动失败：${error.message}`));
  });
  child.on("exit", (code) => {
    if (!stopped) options.onLog(`[${name}] 进程退出 code=${code ?? "null"}（${logFile}）`);
  });
  return child;
}

async function waitForHealth(paths: RuntimePaths, timeoutMs = 90_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${paths.apiPort}/health`);
      if (response.ok) return;
    } catch {
      // 尚未监听，继续等待
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("控制平面在 90 秒内没有就绪，请查看日志。");
}

export type BackendHandle = {
  /** 形如 http://127.0.0.1:8010，主进程转发 IPC 请求时使用。 */
  apiBaseUrl: string;
};

export async function startPackagedBackend(options: BackendOptions): Promise<BackendHandle> {
  stopped = false;
  const paths = resolvePaths(options);
  runtimePaths = paths;
  runtimeOptions = options;

  paths.pgPort = await resolvePgPort(paths);
  paths.apiPort = await pickPort(paths.apiPort);
  savePorts(paths);

  let maintenanceOperation: string | undefined;
  let initializing = false;
  try { await prepareDatabase({
    inspect: async () => inspectDatabase(options, paths),
    requestAction: async (action) => {
      if (!await options.requestMaintenance(action)) return false;
      maintenanceOperation = maintenanceGateway(paths, "begin", {
        action: action.kind, target: action.target, confirmed: true, idempotency_key: randomUUID(),
        ...(action.kind === "upgrade" ? { current: action.current } : {}),
      });
      initializing = action.kind === "initialize";
      return true;
    },
    initialize: async (target) => {
      startDatabase(options, paths);
      ensureRolesAndDatabase(options, paths);
      migrate(options, paths, target);
      provisionDemoData(options, paths);
    },
    backup: async () => backupDatabase(options, paths),
    verifyBackup: async (filename) => verifyDatabaseBackup(paths, filename),
    migrate: async (target) => migrate(options, paths, target),
  }); } catch (error) {
    if (maintenanceOperation) {
      try { maintenanceGateway(paths, "finish", { operation_id: maintenanceOperation, status: "failed" }); }
      catch { log(options, "维护失败记录需要复核，请保留维护日志和备份。"); }
    }
    await stopPackagedBackend();
    throw error;
  }
  if (maintenanceOperation) {
    try {
      maintenanceGateway(paths, "finish", { operation_id: maintenanceOperation, status: "succeeded" });
      if (initializing) writeFileSync(join(paths.dataDir, "initialized.json"), JSON.stringify({ initializedAt: new Date().toISOString() }), { flag: "wx" });
    } catch (error) {
      await stopPackagedBackend();
      throw error;
    }
  }

  log(options, "正在启动控制平面…");
  apiProcess = spawnService(
    options, paths, "API",
    ["-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", String(paths.apiPort)],
    join(paths.dataDir, "logs", "api.log"),
  );
  await waitForHealth(paths);

  workerProcess = spawnService(
    options, paths, "worker",
    ["-m", "apps.worker.local"],
    join(paths.dataDir, "logs", "worker.log"),
  );
  log(options, "控制平面已就绪");
  return { apiBaseUrl: `http://127.0.0.1:${paths.apiPort}` };
}

function stopChild(child: ChildProcess | null): void {
  if (!child || child.killed) return;
  try {
    child.kill();
  } catch {
    // 进程可能已自行退出
  }
}

type PlanningPreview = { model: string; endpoint: string; config_hash: string };

export async function enablePackagedPlanning(confirm: (preview: PlanningPreview) => Promise<boolean>): Promise<{ enabled: boolean; detail: string }> {
  const paths = runtimePaths;
  const options = runtimeOptions;
  if (!paths || !options) throw new Error("本入口只用于安装版自己管理的本机工作区。");
  const previewResult = run(paths.python, ["-m", "packages.policy.maintenance", "preview-planning", "--workspace", paths.dataDir], {
    cwd: paths.appDir, env: pythonEnv(paths), quiet: true,
  });
  if (previewResult.status !== 0) throw new Error("请先在 AI 设置中保存外接 API 的模型、地址和凭据；Debug 用于独立合成测试库。");
  const preview = (JSON.parse(previewResult.stdout) as { model_access: PlanningPreview }).model_access;
  if (!preview || typeof preview.model !== "string" || typeof preview.endpoint !== "string" || !/^[a-f0-9]{64}$/.test(preview.config_hash)) {
    throw new Error("模型配置预览无效。");
  }
  if (!await confirm(preview)) return { enabled: false, detail: "已取消，本机规划权限未改变。" };
  const result = run(paths.python, ["-m", "packages.policy.maintenance", "enable-planning", "--workspace", paths.dataDir], {
    cwd: paths.appDir, env: pythonEnv(paths), quiet: true, input: JSON.stringify({
      action: "configure_planning", target: schemaHistory(paths).target, confirmed: true,
      idempotency_key: randomUUID(), expected_model_hash: preview.config_hash,
    }),
  });
  if (result.status !== 0) throw new Error("本机策略未允许启用规划；请查看维护审计记录。未自动切换模型。");
  writeFileSync(join(paths.dataDir, "planning-enabled.json"), JSON.stringify({ model_config_hash: preview.config_hash, enabledAt: new Date().toISOString() }), { mode: 0o600 });
  stopChild(workerProcess);
  workerProcess = spawnService(options, paths, "worker", ["-m", "apps.worker.local"], join(paths.dataDir, "logs", "worker.log"));
  return { enabled: true, detail: "已授权当前配置用于静态规划并启用本机规划后台。每次检索和模型调用仍经过策略裁决。" };
}

export async function stopPackagedBackend(): Promise<void> {
  stopped = true;
  stopChild(workerProcess);
  stopChild(apiProcess);
  workerProcess = null;
  apiProcess = null;
  if (pgStarted && runtimePaths) {
    // pg_ctl 负责优雅关闭 postmaster；Node 侧只能发信号，因此走 pg_ctl。
    const pgCtl = join(runtimePaths.pgBin, "pg_ctl.exe");
    try {
      spawnSync(pgCtl, ["-D", runtimePaths.pgData, "stop", "-m", "fast", "-w", "-t", "30"], {
        windowsHide: true,
        encoding: "utf8",
        stdio: "ignore",
      });
    } catch {
      // 关库失败不阻塞退出
    }
    pgStarted = false;
  }
  runtimePaths = null;
  runtimeOptions = null;
}
